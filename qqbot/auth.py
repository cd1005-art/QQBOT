"""AccessToken 获取与缓存：有效期 7200 秒，到期前 120 秒主动刷新。"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
REFRESH_MARGIN = 120


class AuthError(RuntimeError):
    pass


class TokenManager:
    def __init__(self, app_id: str, app_secret: str, timeout: int = 15):
        self.app_id = app_id
        self.app_secret = app_secret
        self.timeout = timeout
        self._token: str | None = None
        self._expire_at: float = 0.0

    def get(self, force: bool = False) -> str:
        if not self.app_id or not self.app_secret:
            raise AuthError("缺少 app_id / app_secret：填 config.toml 或设 QQBOT_APP_SECRET")
        if not force and self._token and time.monotonic() < self._expire_at:
            return self._token
        return self._fetch()

    def _fetch(self) -> str:
        body = json.dumps({"appId": self.app_id, "clientSecret": self.app_secret}).encode()
        req = urllib.request.Request(
            TOKEN_URL, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise AuthError(f"换取 token 失败 HTTP {exc.code}: {exc.read()[:200]!r}") from None
        except urllib.error.URLError as exc:
            raise AuthError(f"换取 token 网络错误: {exc.reason}") from None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise AuthError(f"token 返回非 JSON: {raw[:200]}") from None

        token = data.get("access_token")
        if not token:
            # 100007 appid 无效 / 100016 appid 或 secret 不正确 / 11298 IP 不在白名单
            raise AuthError(f"token 被拒: code={data.get('code')} msg={data.get('message')}")

        try:
            ttl = int(data.get("expires_in", 7200))
        except (TypeError, ValueError):
            ttl = 7200
        self._token = token
        self._expire_at = time.monotonic() + max(60, ttl - REFRESH_MARGIN)
        return token

    def info(self) -> dict:
        remain = max(0, int(self._expire_at - time.monotonic())) if self._token else 0
        return {"cached": bool(self._token), "remain_seconds": remain,
                "token_prefix": (self._token[:8] + "…") if self._token else ""}
