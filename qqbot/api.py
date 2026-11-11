"""QQ 开放平台 OpenAPI 客户端：只封装本项目需要的接口，标准库实现。"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .auth import TokenManager
from .config import Config

PROD_BASE = "https://api.sgroup.qq.com"
SANDBOX_BASE = "https://sandbox.api.sgroup.qq.com"
CST = timezone(timedelta(hours=8))

MAX_MUTE_MINUTES = 30 * 24 * 60
MAX_KICK_BATCH = 20


class ApiError(RuntimeError):
    def __init__(self, status: int, code, message: str, body: str = ""):
        self.status = status
        self.code = code
        self.message = message
        self.body = body
        super().__init__(f"HTTP {status} code={code} msg={message}")


def rfc3339(dt: datetime) -> str:
    return dt.astimezone(CST).isoformat(timespec="seconds")


class QQOpenAPI:
    def __init__(self, cfg: Config, tokens: TokenManager):
        self.cfg = cfg
        self.tokens = tokens
        self.base = SANDBOX_BASE if cfg.sandbox else PROD_BASE

    def _send(self, method: str, path: str, body: dict | None, params: dict | None, token: str):
        url = self.base + path
        if params:
            clean = {k: v for k, v in params.items() if v not in (None, "")}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Authorization": f"QQBot {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, (json.loads(raw) if raw.strip() else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                parsed = {}
            raise ApiError(exc.code, parsed.get("code"), parsed.get("message", exc.reason), raw) from None
        except urllib.error.URLError as exc:
            raise ApiError(0, None, f"网络错误: {exc.reason}") from None

    def request(self, method: str, path: str, body: dict | None = None,
                params: dict | None = None, _retry: bool = True):
        token = self.tokens.get()
        try:
            return self._send(method, path, body, params, token)
        except ApiError as exc:
            if exc.status in (401, 403) and _retry:
                self.tokens.get(force=True)
                return self.request(method, path, body, params, _retry=False)
            raise

    # ---------------- 群管理 ----------------

    def recall_message(self, group_openid: str, message_id: str):
        return self.request("DELETE", f"/v2/groups/{group_openid}/messages/{message_id}")

    def mute_member(self, group_openid: str, member_openid: str, minutes: int):
        minutes = max(1, min(int(minutes), MAX_MUTE_MINUTES))
        expire = datetime.now(CST) + timedelta(minutes=minutes)
        body = {"members": [{"op": "add", "member_openid": member_openid,
                             "mute_expire_at": rfc3339(expire)}]}
        return self.request("POST", f"/v2/groups/{group_openid}/restrict_chat_setting", body)

    def unmute_member(self, group_openid: str, member_openid: str):
        body = {"members": [{"op": "del", "member_openid": member_openid, "mute_expire_at": ""}]}
        return self.request("POST", f"/v2/groups/{group_openid}/restrict_chat_setting", body)

    def mute_all(self, group_openid: str, enable: bool):
        body = {"global_rule": {"mode": "always" if enable else "none"}}
        return self.request("POST", f"/v2/groups/{group_openid}/restrict_chat_setting", body)

    def kick_members(self, group_openid: str, member_openids: list[str], blacklist: bool = False):
        if not member_openids:
            raise ValueError("member_openids 不能为空")
        if len(member_openids) > MAX_KICK_BATCH:
            raise ValueError(f"单次最多 {MAX_KICK_BATCH} 个成员")
        body = {"member_openids": member_openids, "add_to_member_blacklist": bool(blacklist)}
        return self.request("POST", f"/v2/groups/{group_openid}/batch_remove_members", body)

    def join_requests(self, group_openid: str, cursor: str = "", limit: int = 20):
        return self.request("GET", f"/v2/groups/{group_openid}/join_request_list",
                            params={"cursor": cursor, "limit": limit})

    def approval_join(self, group_openid: str, member_openid: str, action: str = "approve"):
        if action not in ("approve", "decline"):
            raise ValueError("action 只能是 approve 或 decline")
        return self.request(
            "POST", f"/v2/groups/{group_openid}/approval_join_request/{member_openid}",
            {"op": action})

    def send_message(self, group_openid: str, content: str,
                     msg_id: str | None = None, msg_seq: int = 1):
        body: dict = {"content": content, "msg_type": 0, "msg_seq": msg_seq}
        if msg_id:
            body["msg_id"] = msg_id
        return self.request("POST", f"/v2/groups/{group_openid}/messages", body)

    def send_message_at(self, group_openid: str, content: str, member_openid: str,
                        msg_id: str | None = None, msg_seq: int = 1):
        """markdown 消息 + @某人。群聊里 @ 用户只支持 markdown。"""
        md = f'<qqbot-at-user id="{member_openid}" /> {content}'
        body: dict = {"msg_type": 2, "markdown": {"content": md}, "msg_seq": msg_seq}
        if msg_id:
            body["msg_id"] = msg_id
        return self.request("POST", f"/v2/groups/{group_openid}/messages", body)

    def send_image(self, group_openid: str, image_url: str,
                   msg_id: str | None = None, msg_seq: int = 1):
        """先按 URL 让平台转存，再发富媒体消息。失败时退回二进制上传。"""
        st, data = self.request("POST", f"/v2/groups/{group_openid}/files", {
            "file_type": 1, "url": image_url, "srv_send_msg": False})
        file_info = (data or {}).get("file_info")
        if not file_info:
            raise ApiError(st, None, "上传未返回 file_info", str(data)[:200])

        body: dict = {"msg_type": 7, "media": {"file_info": file_info}, "msg_seq": msg_seq}
        if msg_id:
            body["msg_id"] = msg_id
        return self.request("POST", f"/v2/groups/{group_openid}/messages", body)

    def upload_image_bytes(self, group_openid: str, blob: bytes, file_name: str = "image.png"):
        """二进制直传（≤5MB），用于 URL 转存失败的情况。"""
        import base64
        return self.request("POST", f"/v2/groups/{group_openid}/files", {
            "file_type": 1, "file_name": file_name,
            "file_data": base64.b64encode(blob).decode(), "srv_send_msg": False})

    def send_c2c_image(self, user_openid: str, image_url: str,
                       msg_id: str | None = None, msg_seq: int = 1):
        """私聊发图：先上传富媒体，再发 msg_type=7。"""
        st, data = self.request("POST", f"/v2/users/{user_openid}/files", {
            "file_type": 1, "url": image_url, "srv_send_msg": False})
        info = (data or {}).get("file_info")
        if not info:
            raise ApiError(st, None, "私聊上传未返回 file_info", str(data)[:200])
        return self.send_c2c_media(user_openid, info, msg_id, msg_seq)

    def send_c2c_media(self, user_openid: str, file_info: str,
                       msg_id: str | None = None, msg_seq: int = 1):
        body: dict = {"msg_type": 7, "media": {"file_info": file_info}, "msg_seq": msg_seq}
        if msg_id:
            body["msg_id"] = msg_id
        return self.request("POST", f"/v2/users/{user_openid}/messages", body)

    def upload_c2c_image(self, user_openid: str, blob: bytes, file_name: str = "image.png"):
        import base64
        return self.request("POST", f"/v2/users/{user_openid}/files", {
            "file_type": 1, "file_name": file_name,
            "file_data": base64.b64encode(blob).decode(), "srv_send_msg": False})

    def send_c2c(self, user_openid: str, content: str,
                 msg_id: str | None = None, msg_seq: int = 1):
        """私聊（C2C）文本回复，接口与群聊不同：/v2/users/{openid}/messages。"""
        body: dict = {"content": content, "msg_type": 0, "msg_seq": msg_seq}
        if msg_id:
            body["msg_id"] = msg_id
        return self.request("POST", f"/v2/users/{user_openid}/messages", body)

    def bot_self(self):
        return self.request("GET", "/users/@me")
