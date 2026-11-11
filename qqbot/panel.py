"""Web 管理面板：标准库实现，无第三方框架。

与 Webhook 接收端共用同一个进程、同一个 SQLite：
    /qqbot/webhook  → 18900  收事件
    /panel/         → 18901  管后台

设计取舍：
1. 不引 Flask/FastAPI，前端不引框架和 CDN，服务器断网也能用。
2. 所有写操作走同一套 api.py，dry_run 硬锁在面板上同样生效。
3. 会话用 HMAC 签名 Cookie，密钥由 AppSecret 派生，不落明文密码。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import cfgedit
from .api import CST, MAX_MUTE_MINUTES, ApiError, QQOpenAPI, rfc3339
from .auth import AuthError, TokenManager
from .config import Config
from .llm import LLMClient, LLMError
from .scheduler import CronError, next_after, parse as cron_parse
from .store import Store

STATIC = Path(__file__).resolve().parent.parent / "web" / "panel"
SESSION_TTL = 12 * 3600


class Panel:
    def __init__(self, cfg: Config, store: Store, tokens: TokenManager, api: QQOpenAPI):
        self.cfg = cfg
        self.store = store
        self.tokens = tokens
        self.api = api
        self._key = hashlib.sha256((cfg.app_secret or "no-secret").encode()).digest()
        self._title_fail: dict[str, float] = {}

    # ---------------- 会话 ----------------

    def issue(self) -> str:
        exp = str(int(time.time()) + SESSION_TTL).encode()
        sig = hmac.new(self._key, exp, hashlib.sha256).hexdigest()[:32].encode()
        return base64.urlsafe_b64encode(exp + b"." + sig).decode()

    def valid(self, token: str) -> bool:
        try:
            raw = base64.urlsafe_b64decode(token.encode())
            exp, sig = raw.split(b".", 1)
            if int(exp) < time.time():
                return False
            return hmac.compare_digest(sig, hmac.new(self._key, exp, hashlib.sha256).hexdigest()[:32].encode())
        except Exception:
            return False

    # ---------------- 数据 ----------------

    def overview(self) -> dict:
        info = {}
        try:
            _, me = self.api.bot_self()
            info = {"name": me.get("username"), "id": me.get("id"), "avatar": me.get("avatar")}
        except (ApiError, AuthError) as exc:
            info = {"name": "未连接", "error": str(exc)}
        return {
            "bot": info,
            "stats": self.store.stats(),
            "dry_run": self.cfg.dry_run,
            "sandbox": self.cfg.sandbox,
            "groups": [dict(r) for r in self.store.list_groups()[:10]],
            "events": [dict(r) for r in self.store.event_stream(12)],
            "types": [dict(r) for r in self.store.event_types()[:10]],
            "hourly": self.store.hourly_messages(None, 24),
        }

    def sync_group_titles(self, limit: int = 8):
        """群名只能查 info 接口拿到，拉过就缓存；失败记 10 分钟，避免每次加载都重试。"""
        now = time.time()
        for g in self.store.groups_missing_title(limit):
            if now - self._title_fail.get(g, 0) < 600:
                continue
            try:
                _, info = self.api.request("GET", f"/v2/groups/{g}/info")
                title = info.get("group_name") or ""
                if title:
                    self.store.set_group_title(g, title)
                else:
                    self._title_fail[g] = now
            except (ApiError, AuthError) as exc:
                self._title_fail[g] = now
                print(f"[panel] 取群名失败 {g[:8]}…: {exc}", flush=True)

    def groups(self) -> list[dict]:
        self.sync_group_titles()
        return [dict(r) for r in self.store.list_groups()]

    def group_detail(self, g: str) -> dict:
        out: dict = {"group_openid": g, "info": None, "state": None, "error": None}
        try:
            _, info = self.api.request("GET", f"/v2/groups/{g}/info")
            out["info"] = info
            self.store.set_group_title(g, info.get("group_name") or "")
        except ApiError as exc:
            out["error"] = str(exc)
        try:
            _, state = self.api.request("GET", f"/v2/groups/{g}/bot_state")
            out["state"] = state
        except ApiError as exc:
            out["error"] = out["error"] or str(exc)
        out["hourly"] = self.store.hourly_messages(g, 24)
        out["top"] = [dict(r) for r in self.store.top_members(g, 5)]
        out["stats"] = self.store.stats()
        return out

    # ---------------- 对话（LLM） ----------------

    def llm_settings(self) -> dict:
        c = self.cfg
        masked = ("*" * 10 + c.llm_api_key[-4:]) if c.llm_api_key else ""
        return {
            "enabled": c.llm_enabled, "base_url": c.llm_base_url, "api_key": masked,
            "model": c.llm_model, "system": c.llm_system,
            "max_tokens": c.llm_max_tokens, "temperature": c.llm_temperature,
            "context_turns": c.llm_context_turns, "cooldown": c.llm_cooldown,
            "daily_limit": c.llm_daily_limit, "trigger": c.llm_trigger,
            "keywords": c.llm_keywords, "usage": self.store.chat_usage_today(),
        }

    def save_llm(self, body: dict) -> dict:
        """整段一次性写盘：多次 patch 会产生「读-改-写」竞态，已出过事故。"""
        c = self.cfg
        items: dict = {}

        if "base_url" in body:
            c.llm_base_url = str(body["base_url"]).strip() or c.llm_base_url
            items["base_url"] = c.llm_base_url
        key = (body.get("api_key") or "").strip()
        if key and not key.startswith("****"):
            c.llm_api_key = key
            items["api_key"] = key
        if "model" in body:
            c.llm_model = str(body["model"]).strip() or c.llm_model
            items["model"] = c.llm_model
        if "system" in body:
            c.llm_system = str(body["system"]).strip() or c.llm_system
            items["system"] = c.llm_system
        if "trigger" in body:
            trig = str(body["trigger"])
            if trig not in ("at", "all"):
                raise ValueError("trigger 只能是 at 或 all")
            c.llm_trigger = trig
            items["trigger"] = trig
        for field_name, attr, cast in (
                ("max_tokens", "llm_max_tokens", int), ("temperature", "llm_temperature", float),
                ("context_turns", "llm_context_turns", int), ("cooldown", "llm_cooldown", int),
                ("daily_limit", "llm_daily_limit", int)):
            if field_name in body:
                val = cast(body[field_name])
                setattr(c, attr, val)
                items[field_name] = val
        if "keywords" in body:
            kws = [k.strip() for k in str(body["keywords"]).replace("，", ",").split(",") if k.strip()]
            c.llm_keywords = kws
            items["keywords"] = kws
        if "enabled" in body:
            c.llm_enabled = bool(body["enabled"])
            items["enabled"] = c.llm_enabled

        if items:
            cfgedit.patch_many(c.source_path, "llm", items)
        self.store.log_action("llm-save", None, None,
                              json.dumps({k: v for k, v in body.items() if k != "api_key"},
                                         ensure_ascii=False), True, "saved")
        return self.llm_settings()

    def llm_test(self, prompt: str) -> dict:
        if not self.cfg.llm_api_key:
            raise ValueError("未配置 api_key")
        client = LLMClient(self.cfg.llm_base_url, self.cfg.llm_api_key, self.cfg.llm_model,
                           max_tokens=self.cfg.llm_max_tokens,
                           temperature=self.cfg.llm_temperature, timeout=30)
        answer, tokens = client.chat([
            {"role": "system", "content": self.cfg.llm_system},
            {"role": "user", "content": prompt},
        ])
        return {"answer": answer, "tokens": tokens, "model": self.cfg.llm_model}

    # ---------------- 图片审核 ----------------

    def vision_settings(self) -> dict:
        c = self.cfg
        return {
            "enabled": c.vision_enabled,
            "model": c.vision_model or c.llm_model,
            "model_custom": bool(c.vision_model),
            "base_url": c.vision_base_url or c.llm_base_url,
            "action": c.vision_action,
            "minutes": c.vision_minutes,
            "notice": c.vision_notice,
            "prompt": c.vision_prompt,
            "usage": {
                "checked": len([r for r in self.store.recent_audit(200)
                                if r["action"] in ("vision", "vision-ok")]),
                "blocked": len([r for r in self.store.recent_disposals(200)
                                if (r["action"] or "").startswith("vision")]),
            },
        }

    def save_vision(self, body: dict) -> dict:
        c = self.cfg
        items = {}
        if "enabled" in body:
            c.vision_enabled = bool(body["enabled"]); items["enabled"] = c.vision_enabled
        if "model" in body:
            c.vision_model = str(body["model"]).strip(); items["model"] = c.vision_model
        if "base_url" in body:
            c.vision_base_url = str(body["base_url"]).strip(); items["base_url"] = c.vision_base_url
        if "action" in body:
            act = str(body["action"])
            if act not in ("recall", "mute", "recall_mute", "none"):
                raise ValueError("action 只能是 recall / mute / recall_mute / none")
            c.vision_action = act; items["action"] = act
        if "minutes" in body:
            m = int(body["minutes"])
            if not 1 <= m <= 43200:
                raise ValueError("minutes 1~43200")
            c.vision_minutes = m; items["minutes"] = m
        if "notice" in body:
            c.vision_notice = str(body["notice"]).strip(); items["notice"] = c.vision_notice
        if "prompt" in body:
            c.vision_prompt = str(body["prompt"]).strip(); items["prompt"] = c.vision_prompt
        if items:
            cfgedit.patch_many(c.source_path, "vision", items)
        self.store.log_action("vision-save", None, None,
                              json.dumps({k: v for k, v in body.items() if k != "api_key"},
                                         ensure_ascii=False), True, "saved")
        return self.vision_settings()

    # ---------------- 首次配置 ----------------

    def setup_status(self) -> dict:
        missing = []
        if not self.cfg.app_id:
            missing.append("app_id")
        if not self.cfg.app_secret:
            missing.append("app_secret")
        if not self.cfg.panel_password:
            missing.append("panel_password")
        return {"needed": bool(missing), "missing": missing,
                "sandbox": self.cfg.sandbox, "config": str(self.cfg.source_path)}

    def do_setup(self, body: dict) -> dict:
        if not self.setup_status()["needed"]:
            raise PermissionError("已完成初始化，如需修改请直接编辑 config.toml")
        app_id = (body.get("app_id") or "").strip()
        secret = (body.get("app_secret") or "").strip()
        password = body.get("panel_password") or ""
        if not app_id or not secret:
            raise ValueError("AppID 与 AppSecret 必填")
        if len(password) < 6:
            raise ValueError("面板密码至少 6 位")

        cfgedit.patch_many(self.cfg.source_path, "bot",
                           {"app_id": app_id, "app_secret": secret})
        cfgedit.patch_many(self.cfg.source_path, "panel", {"password": password})

        # 热生效：cfg 与 token 缓存都是同一批对象，改完不用重启
        self.cfg.app_id = app_id
        self.cfg.app_secret = secret
        self.cfg.panel_password = password
        self.tokens.app_id = app_id
        self.tokens.app_secret = secret
        self.tokens._token = None
        self.store.log_action("setup", None, None, json.dumps({"app_id": app_id}), True, "initialized")
        return {"ok": True}

    # ---------------- P1：规则 / 定时 / 群设置 ----------------

    def create_rule(self, body: dict) -> dict:
        name = (body.get("name") or "").strip()
        match_type = body.get("match_type") or "contains"
        pattern = (body.get("pattern") or "").strip()
        action = body.get("action") or "recall"
        minutes = int(body.get("minutes") or 10)
        reply = (body.get("reply") or "").strip()
        groups = [g for g in (body.get("groups") or []) if g]
        if not name or not pattern:
            raise ValueError("名称与匹配内容必填")
        if match_type not in ("contains", "regex"):
            raise ValueError("match_type 只能是 contains 或 regex")
        if action not in ("recall", "mute", "recall_mute", "reply", "recall_reply"):
            raise ValueError("action 只能是 recall / mute / recall_mute / reply / recall_reply")
        if action in ("reply", "recall_reply") and not reply:
            raise ValueError("回复类规则必须填写回复内容")
        if len(reply) > 400:
            raise ValueError("回复内容不超过 400 字")
        if match_type == "regex":
            import re as _re
            try:
                _re.compile(pattern)
            except _re.error as exc:
                raise ValueError(f"正则有误: {exc}")
        if action in ("mute", "recall_mute") and not 1 <= minutes <= 43200:
            raise ValueError("禁言时长 1~43200 分钟")

        rid = self.store.add_rule(name, match_type, pattern, action,
                                  json.dumps({"minutes": minutes, "reply": reply}, ensure_ascii=False),
                                  json.dumps(groups, ensure_ascii=False))
        self.store.log_action("rule-add", None, str(rid), json.dumps(body, ensure_ascii=False), True, "created")
        return {"id": rid}

    def create_schedule(self, body: dict) -> dict:
        name = (body.get("name") or "").strip()
        cron = (body.get("cron") or "").strip()
        text = (body.get("text") or "").strip()
        groups = [g for g in (body.get("groups") or []) if g]
        if not name or not cron or not text:
            raise ValueError("名称、cron、内容都必填")
        try:
            cron_parse(cron)
        except CronError as exc:
            raise ValueError(f"cron 无效: {exc}")
        sid = self.store.add_schedule(name, cron, text, json.dumps(groups, ensure_ascii=False))
        nxt = next_after(cron)
        self.store.finish_schedule(sid, nxt, "已排程")
        self.store.log_action("schedule-add", None, str(sid), json.dumps(body, ensure_ascii=False), True, "created")
        return {"id": sid, "next_run": nxt}

    def settings(self) -> dict:
        return {
            "app_id": self.cfg.app_id,
            "app_secret": ("*" * 12 + self.cfg.app_secret[-4:]) if self.cfg.app_secret else "",
            "sandbox": self.cfg.sandbox,
            "dry_run": self.cfg.dry_run,
            "allowed_groups": self.cfg.allowed_groups,
            "at_all_template": self.cfg.at_all_template,
            "reply_at_user": self.cfg.reply_at_user,
            "listen": f"{self.cfg.listen_host}:{self.cfg.listen_port}{self.cfg.webhook_path}",
            "panel": f"{self.cfg.panel_host}:{self.cfg.panel_port}",
            "db": str(self.cfg.db_path),
            "config": str(self.cfg.source_path),
        }

    def action(self, name: str, p: dict) -> dict:
        if self.cfg.dry_run:
            raise PermissionError("dry_run 硬锁开启，写操作被拒绝。到「设置 → 安全」点「解除锁定」即可，无需改文件")
        g = p.get("group") or ""
        if not g:
            raise ValueError("缺少 group")
        if self.cfg.allowed_groups and g not in self.cfg.allowed_groups:
            raise PermissionError(f"群 {g} 不在 allowed_groups 白名单")

        if name == "recall":
            mid = p.get("message_id") or ""
            if not mid:
                row = self.store.last_message(g)
                if not row:
                    raise ValueError("该群没有可撤回消息")
                mid = row["message_id"]
            st, data = self.api.recall_message(g, mid)
            self.store.mark_recalled(mid)
            self.store.log_action("recall", g, mid, "{}", True, f"HTTP {st}")
            return {"status": st, "message_id": mid}

        if name in ("mute", "unmute"):
            m = p.get("member") or ""
            if not m:
                raise ValueError("缺少 member")
            if name == "mute":
                minutes = int(p.get("minutes") or 60)
                if not 1 <= minutes <= MAX_MUTE_MINUTES:
                    raise ValueError(f"minutes 取值 1~{MAX_MUTE_MINUTES}")
                st, data = self.api.mute_member(g, m, minutes)
                body = {"members": [{"op": "add", "member_openid": m,
                                     "mute_expire_at": rfc3339(datetime.now(CST) + timedelta(minutes=minutes))}]}
            else:
                st, data = self.api.unmute_member(g, m)
                body = {"members": [{"op": "del", "member_openid": m, "mute_expire_at": ""}]}
            self.store.log_action(name, g, m, json.dumps(body, ensure_ascii=False), True, f"HTTP {st}")
            return {"status": st}

        if name == "mute-all":
            enable = bool(p.get("enable"))
            st, data = self.api.mute_all(g, enable)
            self.store.log_action("mute-all", g, None,
                                  json.dumps({"global_rule": {"mode": "always" if enable else "none"}}),
                                  True, f"HTTP {st}")
            return {"status": st, "enabled": enable}

        if name == "send":
            text = (p.get("text") or "").strip()
            if not text:
                raise ValueError("内容为空")
            try:
                st, data = self.api.send_message(g, text, p.get("message_id"))
            except ApiError as exc:
                if exc.code == 40034105:
                    raise ValueError("该群未开启「允许机器人主动在群聊内发言」，"
                                     "请到手机 QQ 群设置里打开后再试") from None
                raise
            self.store.log_action("send", g, None, json.dumps({"content": text}, ensure_ascii=False),
                                  True, f"HTTP {st}")
            return {"status": st, "id": data.get("id")}

        if name == "at-all":
            text = (p.get("text") or "").strip()
            content = f"{self.cfg.at_all_template} {text}".strip()
            try:
                st, data = self.api.send_message(g, content)
            except ApiError as exc:
                if exc.code == 40034105:
                    raise ValueError("该群未开启「允许机器人主动在群聊内发言」，请先到群设置打开") from None
                if exc.code == 40034142:
                    raise ValueError("该群今日 @全体成员 配额已用完（每群每天 1 次），明天再试") from None
                raise
            self.store.log_action("at-all", g, None, json.dumps({"content": content}, ensure_ascii=False),
                                  True, f"HTTP {st}")
            return {"status": st, "id": data.get("id")}

        if name == "approve":
            m = p.get("member") or ""
            act = p.get("op") or "approve"
            st, data = self.api.approval_join(g, m, act)
            self.store.log_action("approve", g, m, json.dumps({"op": act}), True, f"HTTP {st}")
            return {"status": st}

        if name == "kick":
            members = p.get("members") or ([p["member"]] if p.get("member") else [])
            st, data = self.api.kick_members(g, members, bool(p.get("blacklist")))
            self.store.log_action("kick", g, ",".join(members), "{}", True, f"HTTP {st}")
            return {"status": st, "data": data}

        raise ValueError(f"未知操作 {name}")

    def joins(self, g: str) -> dict:
        st, data = self.api.join_requests(g, "", 20)
        return {"status": st, "data": data}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "qqbot-panel/0.3"

    def log_message(self, fmt, *args):
        return

    @property
    def panel(self) -> Panel:
        return self.server.panel

    # ---------- 工具 ----------

    def _send(self, code: int, body: bytes, ctype="application/json; charset=utf-8", cookie=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode())

    def _cookie_token(self) -> str:
        for part in (self.headers.get("Cookie") or "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                if k == "qqbot_sid":
                    return v
        return ""

    def _authed(self) -> bool:
        return self.panel.valid(self._cookie_token())

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError:
            return {}

    # ---------- 路由 ----------

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        if path in ("/panel", "/panel/", "/panel/index.html"):
            html = (STATIC / "index.html").read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
            return
        if path.startswith("/panel/") and not path.startswith("/panel/api/"):
            name = path[len("/panel/"):] or "index.html"
            f = (STATIC / name).resolve()
            if f.is_file() and str(f).startswith(str(STATIC.resolve())):
                ctype = {"css": "text/css; charset=utf-8",
                         "js": "application/javascript; charset=utf-8",
                         "html": "text/html; charset=utf-8"}.get(f.suffix.lstrip("."), "application/octet-stream")
                self._send(200, f.read_bytes(), ctype)
            else:
                self._send(404, b"not found", "text/plain")
            return
        if path == "/panel/api/health":
            self._json({"ok": True, "panel": True})
            return
        if path == "/panel/api/setup/status":
            self._json({"ok": True, "data": self.panel.setup_status()})
            return

        if not path.startswith("/panel/api/"):
            self._send(404, b"not found", "text/plain")
            return
        if not self._authed():
            self._json({"ok": False, "error": "unauthorized"}, 401)
            return

        p = self.panel
        try:
            if path == "/panel/api/overview":
                self._json({"ok": True, "data": p.overview()})
            elif path == "/panel/api/groups":
                self._json({"ok": True, "data": p.groups()})
            elif path.startswith("/panel/api/groups/") and path.endswith("/members"):
                g = path.split("/")[4]
                self._json({"ok": True, "data": [dict(r) for r in p.store.list_members(g, 200)]})
            elif path.startswith("/panel/api/groups/") and path.endswith("/messages"):
                g = path.split("/")[4]
                q = qs.get("q", [""])[0]
                n = int(qs.get("limit", ["50"])[0])
                rows = (p.store.search_messages(g, q, n) if q else p.store.messages(g, n))
                self._json({"ok": True, "data": [dict(r) for r in rows]})
            elif path == "/panel/api/events":
                t = qs.get("type", [None])[0]
                n = int(qs.get("limit", ["50"])[0])
                self._json({"ok": True, "data": [dict(r) for r in p.store.event_stream(n, t)]})
            elif path == "/panel/api/audit":
                self._json({"ok": True, "data": [dict(r) for r in p.store.recent_audit(int(qs.get("limit", ["50"])[0]))]})
            elif path.startswith("/panel/api/groups/") and path.endswith("/stats"):
                g = path.split("/")[4]
                self._json({"ok": True, "data": p.group_detail(g)})
            elif path == "/panel/api/vision":
                self._json({"ok": True, "data": p.vision_settings()})
            elif path == "/panel/api/vision/results":
                self._json({"ok": True, "data": [dict(r) for r in
                                                 p.store.recent_disposals(int(qs.get("limit", ["30"])[0]))]})
            elif path == "/panel/api/llm":
                self._json({"ok": True, "data": p.llm_settings()})
            elif path == "/panel/api/chat":
                self._json({"ok": True, "data": [dict(r) for r in
                                                 p.store.recent_chats(int(qs.get("limit", ["40"])[0]))]})
            elif path == "/panel/api/rules":
                self._json({"ok": True, "data": [dict(r) for r in p.store.list_rules()]})
            elif path == "/panel/api/disposals":
                self._json({"ok": True, "data": [dict(r) for r in
                                                 p.store.recent_disposals(int(qs.get("limit", ["60"])[0]))]})
            elif path == "/panel/api/schedules":
                self._json({"ok": True, "data": [dict(r) for r in p.store.list_schedules()]})
            elif path.startswith("/panel/api/groups/") and path.endswith("/setting"):
                g = path.split("/")[4]
                self._json({"ok": True, "data": p.store.get_group_setting(g)})
            elif path == "/panel/api/settings":
                self._json({"ok": True, "data": p.settings()})
            elif path == "/panel/api/joins":
                self._json({"ok": True, "data": p.joins(qs.get("group", [""])[0])})
            elif path == "/panel/api/me":
                self._json({"ok": True, "data": {"dry_run": p.cfg.dry_run,
                                                 "allowed_groups": p.cfg.allowed_groups}})
            else:
                self._json({"ok": False, "error": "not found"}, 404)
        except (ApiError, AuthError) as exc:
            self._json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        p = self.panel

        if path == "/panel/api/setup":
            try:
                self._json(p.do_setup(self._body()))
            except PermissionError as exc:
                self._json({"ok": False, "error": str(exc)}, 403)
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            return

        if path == "/panel/api/login":
            body = self._body()
            if not p.cfg.panel_password:
                self._json({"ok": False, "error": "未设置 panel.password，拒绝登录"}, 503)
                return
            if not hmac.compare_digest(str(body.get("password", "")), p.cfg.panel_password):
                self._json({"ok": False, "error": "密码错误"}, 403)
                return
            cookie = (f"qqbot_sid={p.issue()}; Path=/panel; HttpOnly; "
                      f"SameSite=Lax; Max-Age={SESSION_TTL}")
            self._send(200, json.dumps({"ok": True}).encode(), cookie=cookie)
            return

        if not self._authed():
            self._json({"ok": False, "error": "unauthorized"}, 401)
            return

        if path == "/panel/api/vision":
            try:
                self._json({"ok": True, "data": p.save_vision(self._body())})
            except (ValueError, TypeError) as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            return

        if path == "/panel/api/llm":
            try:
                self._json({"ok": True, "data": p.save_llm(self._body())})
            except (ValueError, TypeError) as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            return

        if path == "/panel/api/llm/test":
            body = self._body()
            prompt = (body.get("prompt") or "你好，用一句话介绍你自己").strip()
            try:
                self._json({"ok": True, "data": p.llm_test(prompt)})
            except LLMError as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            return

        if path == "/panel/api/rules":
            try:
                self._json({"ok": True, "data": p.create_rule(self._body())})
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            return

        if path == "/panel/api/rules/toggle":
            body = self._body()
            p.store.set_rule_enabled(int(body.get("id") or 0), bool(body.get("enabled")))
            self._json({"ok": True})
            return

        if path == "/panel/api/rules/delete":
            body = self._body()
            p.store.delete_rule(int(body.get("id") or 0))
            p.store.log_action("rule-del", None, str(body.get("id")), "{}", True, "deleted")
            self._json({"ok": True})
            return

        if path == "/panel/api/rules/test":
            body = self._body()
            from .rules import Engine as _E
            rule = {"match_type": body.get("match_type") or "contains",
                    "pattern": body.get("pattern") or ""}
            text = body.get("text") or ""
            try:
                hit = _E.matches(rule, text)
            except Exception as exc:
                self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 400)
                return
            self._json({"ok": True, "data": {"hit": hit}})
            return

        if path == "/panel/api/schedules":
            try:
                self._json({"ok": True, "data": p.create_schedule(self._body())})
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            return

        if path == "/panel/api/schedules/toggle":
            body = self._body()
            sid = int(body.get("id") or 0)
            enabled = bool(body.get("enabled"))
            p.store.set_schedule_enabled(sid, enabled)
            if enabled:
                for r in p.store.list_schedules():
                    if r["id"] == sid:
                        p.store.finish_schedule(sid, next_after(r["cron"]), r["last_result"] or "已排程")
            self._json({"ok": True})
            return

        if path == "/panel/api/schedules/delete":
            body = self._body()
            p.store.delete_schedule(int(body.get("id") or 0))
            p.store.log_action("schedule-del", None, str(body.get("id")), "{}", True, "deleted")
            self._json({"ok": True})
            return

        if path == "/panel/api/groups/auto-rules":
            body = self._body()
            g = body.get("group") or ""
            if not g:
                self._json({"ok": False, "error": "缺少 group"}, 400)
                return
            p.store.set_group_auto_rules(g, bool(body.get("enabled")))
            p.store.log_action("group-auto-rules", g, None,
                               json.dumps({"enabled": body.get("enabled")}), True, "changed")
            self._json({"ok": True})
            return

        if path == "/panel/api/settings/reply-at":
            body = self._body()
            enable = bool(body.get("enable"))
            cfgedit.patch_many(p.cfg.source_path, "reply", {"at_user": enable})
            p.cfg.reply_at_user = enable
            p.store.log_action("reply-at", None, None, json.dumps({"enable": enable}), True, "changed")
            self._json({"ok": True, "data": {"at_user": enable}})
            return

        if path == "/panel/api/settings/password":
            body = self._body()
            old, new = str(body.get("old") or ""), str(body.get("new") or "")
            if not hmac.compare_digest(old, p.cfg.panel_password):
                self._json({"ok": False, "error": "原密码不正确"}, 403)
                return
            if len(new) < 6:
                self._json({"ok": False, "error": "新密码至少 6 位"}, 400)
                return
            cfgedit.patch(p.cfg.source_path, "panel", "password", new)
            p.cfg.panel_password = new
            p.store.log_action("panel-password", None, None, "{}", True, "changed")
            self._json({"ok": True})
            return

        if path == "/panel/api/settings/dry-run":
            body = self._body()
            enable = bool(body.get("enable"))
            cfgedit.patch(p.cfg.source_path, "safety", "dry_run", enable)
            p.cfg.dry_run = enable
            p.store.log_action("dry-run", None, None, json.dumps({"enable": enable}), True, "changed")
            self._json({"ok": True, "data": {"dry_run": enable}})
            return

        if path == "/panel/api/action":
            body = self._body()
            try:
                data = p.action(body.get("action", ""), body)
                self._json({"ok": True, "data": data})
            except PermissionError as exc:
                self._json({"ok": False, "error": str(exc)}, 403)
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            except (ApiError, AuthError) as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            except Exception as exc:
                self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)
            return

        self._json({"ok": False, "error": "not found"}, 404)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, panel: Panel):
        super().__init__(addr, handler)
        self.panel = panel


def build_server(cfg: Config, store: Store, tokens: TokenManager, api: QQOpenAPI) -> ThreadingHTTPServer:
    """构造面板服务（不启动），由主进程负责线程与关停。"""
    panel = Panel(cfg, store, tokens, api)
    httpd = _Server((cfg.panel_host, cfg.panel_port), _Handler, panel)
    if not cfg.panel_password:
        print("[panel] 警告：未设置 panel.password，登录会被拒绝", flush=True)
    return httpd
