"""文生图：OpenAI 兼容的 /images/generations 客户端。

和对话模型是两类接口，不能互相替代：
    对话 → /chat/completions
    生图 → /images/generations

多数厂商（OpenAI、即梦、通义万相、SD WebUI 网关）都提供兼容实现，
返回 data[0].url 或 data[0].b64_json，这里两种都吃。
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request


class GenError(RuntimeError):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"HTTP {status} {message}")


class ImageGenClient:
    def __init__(self, base_url: str, api_key: str, model: str,
                 size: str = "1024x1024", timeout: int = 90):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.size = size
        self.timeout = timeout

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/images/generations"):
            return self.base_url
        return self.base_url + "/images/generations"

    def generate(self, prompt: str) -> tuple[str | None, bytes | None, str]:
        """返回 (图片URL, 二进制, 说明)。URL 与二进制二选一有值。"""
        if not self.api_key:
            raise GenError(0, "未配置 api_key")
        if not self.base_url:
            raise GenError(0, "未配置 base_url")
        if not self.model:
            raise GenError(0, "未配置 model")

        body = json.dumps({"model": self.model, "prompt": prompt,
                           "n": 1, "size": self.size}).encode()
        req = urllib.request.Request(self.endpoint, data=body, method="POST", headers={
            "Content-Type": "application/json",
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
            raise GenError(exc.code, msg) from None
        except urllib.error.URLError as exc:
            raise GenError(0, f"网络错误: {exc.reason}") from None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise GenError(0, f"返回非 JSON: {raw[:200]}") from None

        items = data.get("data") or []
        if not items:
            raise GenError(0, f"返回无 data: {raw[:200]}")
        first = items[0]
        if first.get("url"):
            return first["url"], None, "url"
        if first.get("b64_json"):
            try:
                blob = base64.b64decode(first["b64_json"])
            except Exception:
                raise GenError(0, "b64_json 解码失败") from None
            return None, blob, "b64"
        raise GenError(0, "返回里既没有 url 也没有 b64_json")


VERBS = ("画一", "画个", "画张", "画幅", "画只", "生成", "绘制", "作图", "AI画", "ai画", "画一下")


def detect(text: str) -> str | None:
    """识别「画一张 XXX」这类指令，返回用于模型的提示词。

    命中条件：出现作画动词 + 图片相关名词，避免把「生成报告」误判。
    """
    t = (text or "").strip()
    if not t or len(t) > 120:
        return None
    if not any(v in t for v in VERBS):
        return None
    if not (("图" in t) or ("画" in t)):
        return None

    prompt = t
    for junk in ("机器人", "请", "帮我", "帮忙", "给我", "能不能", "可以"):
        prompt = prompt.replace(junk, "")
    prompt = prompt.strip("，。！？,.!? ")

    # 去掉最前面的动作词，只把描述留给模型
    for v in sorted(VERBS, key=len, reverse=True):
        if prompt.startswith(v):
            prompt = prompt[len(v):]
            break
    prompt = prompt.strip("，。！？,.!? 一张个幅只")
    return prompt or None
