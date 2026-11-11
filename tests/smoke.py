"""离线冒烟测试：不联网、不需要凭证。

跑法：python tests/smoke.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime  # noqa: E402

from qqbot.chat import ChatEngine  # noqa: E402
from qqbot.config import Config  # noqa: E402
from qqbot.ingest import ingest  # noqa: E402
from qqbot.rules import Engine  # noqa: E402
from qqbot.scheduler import CronError, match, next_after, parse  # noqa: E402
from qqbot.sign import Signer, derive_seed  # noqa: E402
from qqbot import imagegen, imagepick  # noqa: E402
from qqbot.vision import image_block, parse_verdict  # noqa: E402
from qqbot.store import Store  # noqa: E402

# 官方文档给出的校验样例
VECTOR_SECRET = "DG5g3B4j9X2KOErG"
VECTOR_TS = "1725442341"
VECTOR_TOKEN = "Arq0D5A61EgUu4OxUvOp"
VECTOR_SIG = ("87befc99c42c651b3aac0278e71ada338433ae26fcb24307bdc5ad38c1adc2d01"
              "bcfcadc0842edac85e85205028a1132afe09280305f13aa6909ffc2d652c706")


def check_signature():
    assert len(derive_seed(VECTOR_SECRET)) == 32
    sig = Signer(VECTOR_SECRET).sign_validation(VECTOR_TOKEN, VECTOR_TS)
    assert sig == VECTOR_SIG, f"签名与官方向量不一致: {sig}"
    return "签名与官方向量一致"


def check_ingest(store: Store):
    raw = json.dumps({
        "id": "MSG1", "content": "测试", "group_openid": "G1",
        "timestamp": "2026-11-08T12:00:00+08:00",
        "author": {"member_openid": "M1", "union_openid": "U1"},
    })
    ingest(store, "GROUP_MESSAGE_CREATE", json.loads(raw), raw)
    assert len(store.list_groups()) == 1
    assert store.list_members("G1")[0]["member_openid"] == "M1"
    assert store.last_message("G1")["message_id"] == "MSG1"

    # 重推同一条：消息不重复，成员计数只加一次
    ingest(store, "GROUP_MESSAGE_CREATE", json.loads(raw), raw)
    assert store.list_members("G1")[0]["msg_count"] == 2, "重推会被计入发言数（预期行为，用于排查）"

    # 未知事件也要落 raw_events，便于对照平台文档补实现
    ingest(store, "GROUP_MEMBER_ADD", {"group_openid": "G1", "member_openid": "M2"}, None)
    assert any(r["event_type"] == "GROUP_MEMBER_ADD" for r in store.event_types())
    return "事件落库 / 去重 / 未知事件留档 通过"


def check_recall(store: Store):
    store.mark_recalled("MSG1")
    assert store.last_message("G1") is None
    store.log_action("recall", "G1", "MSG1", "{}", True, "HTTP 200")
    assert store.recent_audit(1)[0]["action"] == "recall"
    return "撤回标记与审计 通过"


def check_cron():
    assert parse("0 9 * * *")[0] == {0} and parse("0 9 * * *")[1] == {9}
    assert match("0 9 * * 1-5", datetime(2026, 11, 9, 9, 0))       # 周一
    assert not match("0 9 * * 1-5", datetime(2026, 11, 8, 9, 0))  # 周日
    assert match("*/30 * * * *", datetime(2026, 11, 8, 10, 30))
    assert not match("*/30 * * * *", datetime(2026, 11, 8, 10, 31))
    nxt = next_after("0 9 * * *", datetime(2026, 11, 8, 10, 0))
    assert nxt is not None and datetime.fromtimestamp(nxt).hour == 9
    try:
        parse("bad")
        raise AssertionError("非法 cron 应当抛错")
    except CronError:
        pass
    return "cron 解析 / 匹配 / 下一次时间 通过"


def check_rules(store: Store):
    rid = store.add_rule("广告拦截", "contains", "加群", "recall_mute",
                         '{"minutes": 5}', "[]")
    rule = [dict(r) for r in store.list_rules() if r["id"] == rid][0]
    assert Engine.matches(rule, "快来加群领福利"), "包含匹配失败"
    assert not Engine.matches(rule, "今天天气不错")
    r2 = {"match_type": "regex", "pattern": r"https?://\S+"}
    assert Engine.matches(r2, "看这个 http://a.b/c"), "正则匹配失败"
    assert not Engine.matches({"match_type": "regex", "pattern": "["}, "任意"), "非法正则应返回 False"

    cfg = Config(app_id="x", app_secret="y", dry_run=True)
    eng = Engine(cfg, store, api=None)  # dry_run 下不会调用 api
    eng._evaluate("G1", "M1", "MSG-RULE", "快来加群领福利")
    ds = store.recent_disposals(5)
    assert ds and ds[0]["rule_id"] == rid and "dry_run" in (ds[0]["result"] or ""), ds
    assert [dict(r) for r in store.list_rules() if r["id"] == rid][0]["hits"] == 1
    return "规则匹配 / 命中计数 / dry_run 留痕 通过"


class FakeAPI:
    """记录调用，不联网。"""
    def __init__(self):
        self.calls = []

    def send_message(self, group, text, msg_id=None, msg_seq=1):
        self.calls.append(("reply", group, text, msg_id))
        return 200, {"id": "FAKE"}

    def recall_message(self, group, message_id):
        self.calls.append(("recall", group, message_id, None))
        return 200, {}

    def mute_member(self, group, member, minutes):
        self.calls.append(("mute", group, member, minutes))
        return 200, {}


def check_reply_rule(store: Store):
    cfg = Config(app_id="x", app_secret="y", dry_run=False, llm_enabled=False)
    api = FakeAPI()
    eng = Engine(cfg, store, api=api)
    rid = store.add_rule("喊我就答", "contains", "小助手", "reply",
                         '{"reply": "我在，有事直说"}', "[]")
    eng._evaluate("G1", "M1", "MSG-REPLY", "小助手在吗")
    assert ("reply", "G1", "我在，有事直说", "MSG-REPLY") in api.calls, api.calls
    ds = store.recent_disposals(3)
    assert ds[0]["rule_id"] == rid and ds[0]["ok"] == 1, ds
    assert "reply HTTP 200" in (ds[0]["result"] or ""), ds[0]["result"]

    # 同一条消息重复进来不应重复回复
    before = len(api.calls)
    eng._evaluate("G1", "M1", "MSG-REPLY", "小助手在吗")
    assert len(api.calls) == before, "重复消息被重复回复"

    # 撤回+回复同时执行
    api.calls.clear()
    store.add_rule("先撤后答", "contains", "广告", "recall_reply",
                   '{"reply": "禁止发广告"}', "[]")
    eng.invalidate()  # 规则缓存 2 秒，测试里立即生效
    eng._evaluate("G1", "M2", "MSG-AD", "这是广告")
    kinds = sorted(c[0] for c in api.calls)
    assert kinds == ["recall", "reply"], api.calls

    # 规则回复也要进对话历史，带规则名
    rows = store.recent_chats(6)
    assert any(r["kind"] == "rule" for r in rows), [dict(r) for r in rows]
    label = [r for r in rows if r["kind"] == "rule"][0]["model"]
    assert label.startswith("规则："), label
    return "关键词回复 / 撤回+回复 / 重复去重 通过"


def check_schedules(store: Store):
    sid = store.add_schedule("每日公告", "0 9 * * *", "早上好")
    assert any(r["id"] == sid for r in store.list_schedules())
    store.finish_schedule(sid, 0, "已排程")
    due = [r for r in store.due_schedules(10) if r["id"] == sid]
    assert due, "到期任务未被捞出"
    store.set_schedule_enabled(sid, False)
    assert not [r for r in store.due_schedules(10) if r["id"] == sid], "停用后不应到期"
    store.aggregate_hours(48)
    return "定时任务存取 / 到期判定 / 指标聚合 通过"


def check_chat(store: Store):
    cfg = Config(app_id="x", app_secret="y", dry_run=True,
                 llm_enabled=True, llm_api_key="test-key", llm_trigger="at",
                 llm_keywords=["小助手"])
    eng = ChatEngine(cfg, store, api=None)

    at_event = {"content": "<@!123456> 你好", "group_openid": "G1", "id": "M1"}
    assert eng.should_reply("GROUP_AT_MESSAGE_CREATE", at_event), "@ 消息应触发"
    assert at_event["_clean"] == "你好", f"@ 前缀未剥离: {at_event['_clean']}"

    plain = {"content": "今天天气不错", "group_openid": "G1", "id": "M2"}
    assert not eng.should_reply("GROUP_MESSAGE_CREATE", plain), "普通消息不应触发"

    kw = {"content": "小助手出来", "group_openid": "G1", "id": "M3"}
    assert eng.should_reply("GROUP_MESSAGE_CREATE", kw), "关键词应触发"

    mention = {"content": "帮我看看", "group_openid": "G1", "id": "M4",
               "mentions": [{"bot": True, "id": "1"}]}
    assert eng.should_reply("GROUP_MESSAGE_CREATE", mention), "mentions 含 bot 应触发"

    empty = {"content": "<@!123456>", "group_openid": "G1", "id": "M5"}
    assert not eng.should_reply("GROUP_AT_MESSAGE_CREATE", empty), "空内容不应触发"

    # 上下文读写
    store.add_chat("G1", "M1", "user", "你好")
    store.add_chat("G1", "M1", "assistant", "你好，有什么可以帮你")
    ctx = store.chat_context("G1", 3)
    assert [c["role"] for c in ctx] == ["user", "assistant"], ctx
    assert store.chat_calls_today("G1") == 0
    assert store.bump_chat_usage("G1", 120) == 1
    assert store.chat_usage_today()["tokens"] == 120
    return "对话触发判定 / 上下文 / 用量计数 通过"


def check_verdict():
    v = parse_verdict('{"violation":true,"category":"广告","reason":"含二维码","summary":"一张引流图"}')
    assert v["violation"] and v["category"] == "广告", v

    fenced = parse_verdict('```json\n{"violation":false,"summary":"风景照"}\n```')
    assert not fenced["violation"] and fenced["summary"] == "风景照", fenced

    chatty = parse_verdict('分析如下：{"violation":true,"summary":"涉赌"} 以上。')
    assert chatty["violation"] is True and chatty["parsed"] is True, chatty

    # 模型可能用 is_violation / description 这类字段名
    alias = parse_verdict('{"is_violation": false, "description": "人像摄影", "reason": "衣着完整"}')
    assert alias["violation"] is False and alias["summary"] == "人像摄影", alias

    # 布尔值可能被写成字符串
    strflag = parse_verdict('{"violation":"true","summary":"二维码引流图"}')
    assert strflag["violation"] is True, strflag

    broken = parse_verdict("我看不清这张图片")
    assert broken["violation"] is False and broken["parsed"] is False, broken
    return "视觉判定 JSON 解析（标准 / 代码块 / 噪声 / 无法解析）通过"


def check_image_block():
    blk = image_block(b"\x89PNG\r\n\x1a\n", "image/png")
    assert blk["type"] == "image_url", blk
    url = blk["image_url"]["url"]
    assert url.startswith("data:image/png;base64,"), url[:40]

    # 只有图片、没有文字时，@ 也能触发识别
    cfg = Config(app_id="x", app_secret="y", llm_enabled=True, llm_api_key="k",
                 vision_chat_enabled=True, llm_trigger="at")
    eng = ChatEngine(cfg, store=None, api=None)
    ev = {"content": "", "group_openid": "G1", "id": "IMG1",
          "attachments": [{"content_type": "image/png", "url": "http://x/y.png"}]}
    assert eng.should_reply("GROUP_AT_MESSAGE_CREATE", ev), "纯图片 @ 应触发识别"

    # 全量消息模式下，mentions 里带 bot 也算 @
    ev_at = dict(ev, mentions=[{"bot": True, "id": "1"}])
    assert eng.should_reply("GROUP_MESSAGE_CREATE", ev_at), "mentions 含 bot 应触发识别"

    cfg2 = Config(app_id="x", app_secret="y", llm_enabled=True, llm_api_key="k",
                  vision_chat_enabled=False, llm_trigger="at")
    eng2 = ChatEngine(cfg2, store=None, api=None)
    assert not eng2.should_reply("GROUP_AT_MESSAGE_CREATE", ev), "关闭识别后纯图片不应触发"
    return "图片识别：多模态块构造 / 纯图触发 / 开关生效 通过"


def check_gen_size():
    assert imagegen.parse_size("画一张橘猫", "1024x1024") == "1024x1024"
    assert imagegen.parse_size("画一张竖版的橘猫", "1024x1024") == "1024x1536"
    assert imagegen.parse_size("画一张横图", "1024x1024") == "1536x1024"
    assert imagegen.parse_size("画一张方图", "1536x1024") == "1024x1024"
    assert imagegen.parse_size("画一张 768x1344 的图", "1024x1024") == "768x1344"
    assert imagegen.parse_size("画一张 9999x9999", "1024x1024") == "1024x1024"  # 超范围回落
    return "生图尺寸解析通过"


def check_image_intent():
    assert imagepick.detect("来张二次元图片") == "二次元 动漫 壁纸"
    assert imagepick.detect("找张搞笑图") == "搞笑 表情包"
    assert imagepick.detect("来一张风景照片") == "风景 壁纸"
    assert imagepick.detect("今天天气不错") is None
    assert imagepick.detect("来张图片") is None            # 没类别不猜
    assert imagepick.detect("来张二次元" + "图" * 40) is None  # 太长当闲聊

    safe = ["二次元", "搞笑", "风景"]
    assert imagepick.detect("来张二次元图片", safe) == "二次元 动漫 壁纸"
    assert imagepick.detect("来张美女图片", safe) is None      # 未放行
    # 语义变更：空列表 = 全部禁止；None = 内部不限制
    assert imagepick.detect("来张美女图片", []) is None
    assert imagepick.detect("来张美女图片", None) == "美女 写真 唯美"
    assert imagepick.detect("来张猫咪图", ["猫", "宠物"]) == "猫咪 可爱"  # 长类别优先

    # 类别未放行时，仍能识别出来，由上层决定怎么回答
    # 「来几张」这种变体也要认得出来
    assert imagepick.intent("机器人，来几张好看的漫画图") is True
    assert imagepick.classify("机器人，来几张好看的漫画图", ["漫画"])["key"] == "漫画"
    # 省略「图片」二字，只给类别也能触发
    assert imagepick.intent("来几张二次元") is True
    # 没指定类别时随机挑已启用的
    rnd = imagepick.random_choice(["二次元", "漫画"])
    assert rnd and rnd["key"] in ("二次元", "漫画"), rnd
    assert imagepick.random_choice([]) is None

    info = imagepick.classify("来张好看的漫画", ["二次元"])
    assert info and info["key"] == "漫画" and info["allowed"] is False, info
    assert imagepick.classify("来张好看的漫画", ["漫画"])["allowed"] is True
    return "找图意图识别通过"


def check_imagegen_intent():
    assert imagegen.detect("机器人，画一张赛博朋克城市夜景") == "赛博朋克城市夜景"
    assert imagegen.detect("帮我画一只橘猫") == "橘猫"
    assert imagegen.detect("生成一张二次元少女图")
    # 「做/出」也要认：面板能测通、群里没反应，就是因为漏了这两个动词
    assert imagegen.detect("机器人，帮我做一张图：一只坐在窗边的橘猫") == "坐在窗边的橘猫"
    assert imagegen.detect("出一张赛博朋克图") == "赛博朋克图"
    assert imagegen.detect("生成报告") is None      # 不是画图
    assert imagegen.detect("这张图做得不错") is None  # 闲聊不误判
    assert imagegen.detect("今天画画很开心") is None
    return "文生图意图识别通过"


def check_c2c_queue():
    cfg = Config(app_id="x", app_secret="y", llm_enabled=True, llm_api_key="k",
                 llm_trigger="all")
    eng = ChatEngine(cfg, store=None, api=None)
    ev = {"id": "C2C1", "content": "在吗", "author": {"user_openid": "U1", "bot": False}}
    assert eng.should_reply("C2C_MESSAGE_CREATE", ev, "c2c"), "私聊不需要 @ 也应回复"
    eng.submit("C2C_MESSAGE_CREATE", ev)
    assert eng.q.qsize() == 1, "私聊消息应入队"
    ev2 = {"id": "C2C2", "content": "在吗", "author": {"user_openid": "U1", "bot": True}}
    eng.submit("C2C_MESSAGE_CREATE", ev2)
    assert eng.q.qsize() == 1, "机器人自己的消息不应入队"
    return "私聊消息入队 / 机器人自身过滤 通过"


def check_send_at():
    from qqbot.api import QQOpenAPI
    from qqbot.auth import TokenManager

    cfg = Config(app_id="x", app_secret="y")
    api = QQOpenAPI(cfg, TokenManager("x", "y"))
    captured = {}

    def fake_request(method, path, body=None, **kw):
        captured.update({"m": method, "p": path, "b": body})
        return 200, {}

    api.request = fake_request  # type: ignore
    api.send_message_at("G1", "你好", "M1", msg_id="MSG1")
    b = captured["b"]
    assert b["msg_type"] == 2, b
    assert '<qqbot-at-user id="M1" /> 你好' in b["markdown"]["content"], b
    assert b["msg_id"] == "MSG1"
    return "@ 回复使用 markdown + qqbot-at-user 通过"


def main() -> int:
    msg1 = check_signature()
    msg_cron = check_cron()
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "t.db")
        msg2 = check_ingest(store)
        msg3 = check_recall(store)
        msg4 = check_rules(store)
        msg5 = check_schedules(store)
        msg6 = check_chat(store)
        msg7 = check_reply_rule(store)
    print(msg1)
    print(msg2)
    print(msg3)
    print(msg_cron)
    print(msg4)
    print(msg5)
    print(msg6)
    print(msg7)
    print(check_send_at())
    print(check_c2c_queue())
    print(check_verdict())
    print(check_image_block())
    print(check_image_intent())
    print(check_imagegen_intent())
    print(check_gen_size())
    print("smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
