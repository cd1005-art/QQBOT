"""视觉理解客户端：把图片喂给 OpenAI 兼容的多模态接口。

只依赖 /chat/completions 的 image_url 内容块，因此 OpenAI、DeepSeek 兼容网关、
本地 one-api、vLLM 等都能用。
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.error
import urllib.request

# 同一张图可能同时被「对话识别」和「合规审核」使用，缓存 5 分钟避免重复下载
_CACHE: dict[str, tuple[float, bytes, str]] = {}
_CACHE_TTL = 300


def fetch_image(url: str, max_bytes: int = 8 * 1024 * 1024, timeout: int = 20) -> tuple[bytes, str]:
    key = hashlib.sha1(url.encode()).hexdigest()
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1], hit[2]
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        "Referer": "https://image.so.com/",   # 图床防盗链，缺这个会 403
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(max_bytes + 1)
        mime = (resp.headers.get("Content-Type") or "image/jpeg").split(";")[0]
    if len(data) > max_bytes:
        raise ValueError(f"图片超过 {max_bytes} 字节上限")
    _CACHE[key] = (time.time(), data, mime)
    if len(_CACHE) > 60:
        for k in sorted(_CACHE, key=lambda x: _CACHE[x][0])[:20]:
            _CACHE.pop(k, None)
    return data, mime


def image_block(image: bytes, mime: str) -> dict:
    """构造 /chat/completions 的多模态图片内容块。"""
    return {"type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{base64.b64encode(image).decode()}"}}


class VisionError(RuntimeError):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"HTTP {status} {message}")


class VisionClient:
    def __init__(self, base_url: str, api_key: str, model: str,
                 max_tokens: int = 800, temperature: float = 0.2, timeout: int = 45):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def ask(self, image: bytes, mime: str, prompt: str) -> tuple[str, int]:
        if not self.api_key:
            raise VisionError(0, "未配置 api_key")
        if not self.base_url:
            raise VisionError(0, "未配置 base_url")

        b64 = base64.b64encode(image).decode()
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": False,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
        }
        req = urllib.request.Request(
            self.endpoint, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                msg = json.loads(detail).get("error", {}).get("message") or detail[:200]
            except Exception:
                msg = detail[:200]
            raise VisionError(exc.code, msg) from None
        except urllib.error.URLError as exc:
            raise VisionError(0, f"网络错误: {exc.reason}") from None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise VisionError(0, f"返回非 JSON: {raw[:200]}") from None
        choices = data.get("choices") or []
        if not choices:
            raise VisionError(0, f"返回无 choices: {raw[:200]}")
        text = (choices[0].get("message") or {}).get("content") or ""
        tokens = int((data.get("usage") or {}).get("total_tokens") or 0)
        return text.strip(), tokens


def parse_verdict(text: str) -> dict:
    """从模型输出里抠出 JSON。允许 ```json 包裹，允许前后有废话。"""
    t = (text or "").strip()
    if "```" in t:
        parts = t.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("{") or p.startswith("json"):
                t = p[4:].strip() if p.startswith("json") else p
                break
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        return {"violation": False, "category": "", "reason": "", "summary": t[:120],
                "raw": text, "parsed": False}
    try:
        obj = json.loads(t[start:end + 1])
    except json.JSONDecodeError:
        return {"violation": False, "category": "", "reason": "", "summary": t[:120],
                "raw": text, "parsed": False}
    raw_flag = obj.get("violation")
    if raw_flag is None:
        raw_flag = obj.get("is_violation")
    if raw_flag is None:
        raw_flag = obj.get("block")
    if isinstance(raw_flag, str):
        raw_flag = raw_flag.strip().lower() in ("true", "1", "yes", "是")

    summary = obj.get("summary") or obj.get("description") or obj.get("caption") or ""
    category = obj.get("category") or obj.get("categories") or obj.get("type") or ""
    if isinstance(category, list):
        category = ",".join(str(x) for x in category)
    reason = obj.get("reason") or obj.get("explanation") or obj.get("detail") or ""

    return {
        "violation": bool(raw_flag),
        "category": str(category),
        "reason": str(reason),
        "summary": str(summary),
        "raw": text,
        "parsed": True,
    }
