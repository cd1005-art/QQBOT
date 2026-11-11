"""OpenAI 兼容的 Chat Completions 客户端（标准库实现）。

只依赖 /chat/completions，因此 OpenAI、DeepSeek、Moonshot、通义、Ollama、
one-api 等兼容网关都能直接用，只要填对 base_url 与 model。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class LLMError(RuntimeError):
    def __init__(self, status: int, message: str, body: str = ""):
        self.status = status
        self.message = message
        self.body = body
        super().__init__(f"HTTP {status} {message}")


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str,
                 max_tokens: int = 300, temperature: float = 0.7, timeout: int = 40):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout

    @property
    def endpoint(self) -> str:
        base = self.base_url
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def chat(self, messages: list[dict]) -> tuple[str, int]:
        """返回 (回复文本, 消耗 token 数)。token 数拿不到时返回 0。"""
        if not self.api_key:
            raise LLMError(0, "未配置 api_key")
        if not self.base_url:
            raise LLMError(0, "未配置 base_url")

        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": False,
        }, ensure_ascii=False).encode()

        req = urllib.request.Request(self.endpoint, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                msg = json.loads(detail).get("error", {}).get("message") or detail[:200]
            except Exception:
                msg = detail[:200]
            raise LLMError(exc.code, msg, detail) from None
        except urllib.error.URLError as exc:
            raise LLMError(0, f"网络错误: {exc.reason}") from None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise LLMError(0, f"返回非 JSON: {raw[:200]}") from None

        choices = data.get("choices") or []
        if not choices:
            raise LLMError(0, f"返回无 choices: {raw[:200]}")
        content = (choices[0].get("message") or {}).get("content") or ""
        usage = data.get("usage") or {}
        tokens = int(usage.get("total_tokens") or 0)
        return content.strip(), tokens
