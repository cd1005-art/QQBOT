"""命令行入口：Webhook 接收端 + 群管理接口调用。

安全模型（双层）：
1. config.toml 的 safety.dry_run = true 是硬锁，--apply 也不生效；
2. 解硬锁后，写操作仍需显式 --apply，否则只打印将要发出的请求。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta

from . import config as config_mod
from .api import CST, MAX_KICK_BATCH, MAX_MUTE_MINUTES, ApiError, QQOpenAPI, rfc3339
from .auth import AuthError, TokenManager
from .sign import Signer
from .store import Store

WRITE_CMDS = {"recall", "mute", "unmute", "mute-all", "kick", "approve", "send", "at-all"}


def build(argv: list[str] | None = None) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="qqbot", description="QQ 群管理（官方 OpenAPI + Webhook）")
    p.add_argument("--config", default=None)
    p.add_argument("--apply", action="store_true", help="真正执行写操作")
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<命令>")

    # 子命令也接受 --apply/--json：SUPPRESS 保证不覆盖顶层已解析的值
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--apply", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    def add(name, help_text):
        return sub.add_parser(name, help=help_text, parents=[common])

    add("serve", "启动 Webhook 接收端（常驻）")
    add("token", "验证凭证并输出 access_token 状态")
    add("self", "查询机器人自身信息")
    add("groups", "列出已知群")
    add("audit", "查看操作审计").add_argument("--limit", type=int, default=20)
    add("events", "查看最近收到的原始事件").add_argument("--limit", type=int, default=10)
    add("types", "按事件类型统计")
    add("check", "本机自测：模拟平台校验 + 模拟签名事件")
    add("reindex", "重放 raw_events，回填群/成员/消息索引")

    m = add("members", "列出群成员")
    m.add_argument("--group", required=True)
    m.add_argument("--limit", type=int, default=50)

    r = add("recall", "撤回消息（2 分钟内有效）")
    r.add_argument("--group", required=True)
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--msg")
    g.add_argument("--last", action="store_true")

    mu = add("mute", "禁言成员（上限 30 天）")
    mu.add_argument("--group", required=True)
    mu.add_argument("--member", required=True)
    mu.add_argument("--minutes", type=int, default=60)

    um = add("unmute", "解除禁言")
    um.add_argument("--group", required=True)
    um.add_argument("--member", required=True)

    ma = add("mute-all", "全员禁言开关")
    ma.add_argument("--group", required=True)
    ma.add_argument("--on", action="store_true")
    ma.add_argument("--off", action="store_true")

    k = add("kick", "移出群成员（单次最多 20）")
    k.add_argument("--group", required=True)
    k.add_argument("--member", required=True, action="append")
    k.add_argument("--blacklist", action="store_true")

    j = add("joins", "入群申请列表")
    j.add_argument("--group", required=True)
    j.add_argument("--cursor", default="")
    j.add_argument("--limit", type=int, default=20)

    ap = add("approve", "审批入群申请")
    ap.add_argument("--group", required=True)
    ap.add_argument("--member", required=True)
    ap.add_argument("--action", choices=["approve", "decline"], default="approve")

    s = add("send", "发送群消息")
    s.add_argument("--group", required=True)
    s.add_argument("--text", required=True)
    s.add_argument("--msg-id", default=None)

    a = add("at-all", "发 @全体成员（官方标签，每群每天 1 次）")
    a.add_argument("--group", required=True)
    a.add_argument("--text", default="")
    return p


# ---------------- 守卫与输出 ----------------

def emit(args, obj, text):
    print(json.dumps(obj, ensure_ascii=False, indent=2) if args.json else text)


def guard_group(cfg, group):
    if cfg.allowed_groups and group not in cfg.allowed_groups:
        raise SystemExit(f"拒绝执行：群 {group} 不在 allowed_groups 白名单\n"
                         f"当前白名单：{', '.join(cfg.allowed_groups)}")


def execute_allowed(cfg, args):
    if not args.apply:
        return False, "未加 --apply，仅预览"
    if cfg.dry_run:
        return False, "config.toml 的 safety.dry_run = true 为硬锁，已阻止执行"
    return True, ""


def preview(method, path, body):
    out = [f"{method} {path}"]
    if body is not None:
        out.append(json.dumps(body, ensure_ascii=False, indent=2))
    return "\n".join(out)


def run_write(cfg, args, store, action, method, path, call, body, group, target=None):
    ok, reason = execute_allowed(cfg, args)
    if not ok:
        print(f"[预览] {reason}")
        print(preview(method, path, body))
        return 0
    try:
        status, data = call()
    except ApiError as exc:
        store.log_action(action, group, target, json.dumps(body, ensure_ascii=False), False, str(exc))
        print(f"[失败] {exc}")
        if exc.code == 11253:
            print("提示：该接口仅白名单机器人可用，需在开放平台申请权限")
        return 1
    store.log_action(action, group, target, json.dumps(body, ensure_ascii=False), True, f"HTTP {status}")
    emit(args, {"ok": True, "status": status, "data": data},
         f"[成功] {action} HTTP {status} {json.dumps(data, ensure_ascii=False)}")
    return 0


def cmd_check(cfg: Config, store: Store, signer: Signer) -> int:
    """本机自测：起临时端口，模拟平台校验、合法事件、篡改签名。

    用临时库，避免自测数据污染正式库。
    """
    import tempfile
    from pathlib import Path

    from .webhook import selftest

    with tempfile.TemporaryDirectory() as tmp:
        tmp_store = Store(Path(tmp) / "selftest.db")
        results = selftest(cfg, tmp_store, signer)
    width = max(len(n) for n, _, _ in results)
    for name, ok, detail in results:
        print(f"{name.ljust(width)}  {'PASS' if ok else 'FAIL'}  {detail}")
    return 0 if all(ok for _, ok, _ in results) else 1


def main(argv: list[str] | None = None) -> int:
    args = build(argv).parse_args(argv)
    try:
        cfg = config_mod.load(args.config)
    except config_mod.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    store = Store(cfg.db_path)
    tokens = TokenManager(cfg.app_id, cfg.app_secret, cfg.timeout)
    api = QQOpenAPI(cfg, tokens)
    try:
        signer = Signer(cfg.app_secret)
    except ValueError:
        # 未配置 AppSecret：check 需要签名，serve 仍要能起来（走初始化引导）
        if args.cmd == "check":
            print("[配置错误] 缺少 AppSecret，先完成初始化", file=sys.stderr)
            return 2
        signer = None  # type: ignore

    cmd = args.cmd
    try:
        if cmd == "serve":
            from . import webhook as webhook_mod
            webhook_mod.serve(cfg, store, signer)
            return 0

        if cmd == "check":
            return cmd_check(cfg, store, signer)

        if cmd == "token":
            tokens.get(force=True)
            info = tokens.info()
            emit(args, {"ok": True, **info, "sandbox": cfg.sandbox},
                 f"凭证可用 | 环境={'沙箱' if cfg.sandbox else '正式'} | 前缀={info['token_prefix']} | 有效期 {info['remain_seconds']}s")
            return 0

        if cmd == "self":
            status, data = api.bot_self()
            emit(args, data, f"{data.get('username')}  id={data.get('id')}")
            return 0

        if cmd == "groups":
            rows = store.list_groups()
            if args.json:
                print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
            elif not rows:
                print("库里还没有群记录。先跑 serve，再在群里发一条消息。")
            for r in rows:
                name = r["title"] or "(未取到群名)"
                print(f"{name:<18} {r['group_openid']}  成员{r['members']:<4} 消息{r['messages']:<5}")
            return 0

        if cmd == "members":
            rows = store.list_members(args.group, args.limit)
            if args.json:
                print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
            elif not rows:
                print("该群暂无成员记录")
            for r in rows:
                print(f"{r['member_openid']}  发言{r['msg_count']:<4} 最近={r['last_seen']} 昵称={r['nickname'] or '-'}")
            return 0

        if cmd == "audit":
            rows = store.recent_audit(args.limit)
            if args.json:
                print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
            for r in rows:
                print(f"{r['ts']} {'OK ' if r['ok'] else 'ERR'} {r['action']:<9} 群={r['group_openid']} 对象={r['target_openid'] or '-'} {r['result']}")
            return 0

        if cmd == "reindex":
            from .ingest import ingest as _ingest
            rows = store.all_raw_events()
            done = 0
            for r in rows:
                try:
                    obj = json.loads(r["payload"])
                    # raw_events 存的是完整信封，ingest 需要里层的 d
                    data = obj.get("d") if isinstance(obj.get("d"), dict) else obj
                    _ingest(store, r["event_type"], data, r["payload"])
                    done += 1
                except Exception as exc:
                    print(f"[跳过] {r['event_type']}: {type(exc).__name__}: {exc}")
            print(f"重放 {done} 条原始事件完成")
            return 0

        if cmd == "types":
            rows = store.event_types()
            if not rows:
                print("还没收到任何事件")
            for r in rows:
                print(f"{r['event_type']:<30} {r['n']:<6} 最近={r['last_at']}")
            return 0

        if cmd == "events":
            rows = store.recent_events(args.limit)
            if args.json:
                print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
            for r in rows:
                print(f"{r['received_at']} {r['event_type']} 群={r['group_openid']} {r['payload'][:120]}")
            return 0

        # ---- 写操作 ----
        guard_group(cfg, args.group)

        if cmd == "recall":
            msg_id = args.msg
            if args.last:
                row = store.last_message(args.group)
                if not row:
                    print("库里没有该群的可撤回消息", file=sys.stderr)
                    return 1
                msg_id = row["message_id"]
            code = run_write(cfg, args, store, "recall", "DELETE",
                             f"/v2/groups/{args.group}/messages/{msg_id}",
                             lambda: api.recall_message(args.group, msg_id), None, args.group, msg_id)
            if code == 0 and execute_allowed(cfg, args)[0]:
                store.mark_recalled(msg_id)
            return code

        if cmd == "mute":
            if not 1 <= args.minutes <= MAX_MUTE_MINUTES:
                print(f"--minutes 取值范围 1~{MAX_MUTE_MINUTES}", file=sys.stderr)
                return 2
            expire = rfc3339(datetime.now(CST) + timedelta(minutes=args.minutes))
            body = {"members": [{"op": "add", "member_openid": args.member, "mute_expire_at": expire}]}
            return run_write(cfg, args, store, "mute", "POST",
                             f"/v2/groups/{args.group}/restrict_chat_setting",
                             lambda: api.mute_member(args.group, args.member, args.minutes),
                             body, args.group, args.member)

        if cmd == "unmute":
            body = {"members": [{"op": "del", "member_openid": args.member, "mute_expire_at": ""}]}
            return run_write(cfg, args, store, "unmute", "POST",
                             f"/v2/groups/{args.group}/restrict_chat_setting",
                             lambda: api.unmute_member(args.group, args.member),
                             body, args.group, args.member)

        if cmd == "mute-all":
            if args.on == args.off:
                print("必须且只能指定 --on 或 --off", file=sys.stderr)
                return 2
            enable = bool(args.on)
            body = {"global_rule": {"mode": "always" if enable else "none"}}
            return run_write(cfg, args, store, "mute-all", "POST",
                             f"/v2/groups/{args.group}/restrict_chat_setting",
                             lambda: api.mute_all(args.group, enable), body, args.group)

        if cmd == "kick":
            if len(args.member) > MAX_KICK_BATCH:
                print(f"单次最多 {MAX_KICK_BATCH} 个成员", file=sys.stderr)
                return 2
            body = {"member_openids": args.member, "add_to_member_blacklist": bool(args.blacklist)}
            return run_write(cfg, args, store, "kick", "POST",
                             f"/v2/groups/{args.group}/batch_remove_members",
                             lambda: api.kick_members(args.group, args.member, args.blacklist),
                             body, args.group, ",".join(args.member))

        if cmd == "joins":
            ok, reason = execute_allowed(cfg, args)
            if not ok:
                print(f"[预览] {reason}")
                print(preview("GET", f"/v2/groups/{args.group}/join_request_list?cursor={args.cursor}&limit={args.limit}", None))
                return 0
            status, data = api.join_requests(args.group, args.cursor, args.limit)
            emit(args, {"ok": True, "status": status, "data": data},
                 json.dumps(data, ensure_ascii=False, indent=2))
            return 0

        if cmd == "approve":
            body = {"op": args.action}
            return run_write(cfg, args, store, "approve", "POST",
                             f"/v2/groups/{args.group}/approval_join_request/{args.member}",
                             lambda: api.approval_join(args.group, args.member, args.action),
                             body, args.group, args.member)

        if cmd == "send":
            body = {"content": args.text, "msg_type": 0, "msg_seq": 1}
            if args.msg_id:
                body["msg_id"] = args.msg_id
            return run_write(cfg, args, store, "send", "POST",
                             f"/v2/groups/{args.group}/messages",
                             lambda: api.send_message(args.group, args.text, args.msg_id),
                             body, args.group)

        if cmd == "at-all":
            # 官方标签 <qqbot-at-everyone />，纯文本 "@全体成员" 不会触发真实提醒
            content = f"{cfg.at_all_template} {args.text}".strip()
            body = {"content": content, "msg_type": 0, "msg_seq": 1}
            return run_write(cfg, args, store, "at-all", "POST",
                             f"/v2/groups/{args.group}/messages",
                             lambda: api.send_message(args.group, content), body, args.group)

        print(f"未知命令：{cmd}", file=sys.stderr)
        return 2

    except AuthError as exc:
        print(f"[凭证错误] {exc}", file=sys.stderr)
        return 2
    except ApiError as exc:
        print(f"[接口错误] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
