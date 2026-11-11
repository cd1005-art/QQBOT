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


import re

# 生成类动词。「做一张图」「出一张图」都要算，只认「画」会漏掉一半说法。
VERB_RE = re.compile(r"(生成|绘制|作图|画|做|出)(一|个|张|幅|只|副)?(张|幅|只|副)?(图|图片|照片)?")
LEAD_JUNK = ("机器人", "@", "帮我", "帮忙", "请", "麻烦", "给我", "能不能", "可以", "能否")


def detect(text: str) -> str | None:
    """识别「画/做/生成 一张图…」这类指令，返回提示词。

    必须同时满足：动词在句首附近、紧跟量词或「图/图片/照片」、全文含图相关字。
    这样「今天画画很开心」「这张图做得不错」不会被误判。
    """
    t = (text or "").strip()
    if not t or len(t) > 160:
        return None
    if not any(k in t for k in ("图", "画", "照片")):
        return None

    body = t
    for junk in LEAD_JUNK:
        if body.startswith(junk):
            body = body[len(junk):].lstrip("，, 。:：")

    m = VERB_RE.search(body)
    if not m or m.start() > 10:
        return None
    if not (m.group(2) or m.group(3) or m.group(4)):
        return None          # 只有动词没有量词/名词，多半是闲聊

    prompt = body[m.end():].strip(" ：:，,。！？!?、\n")
    prompt = prompt.strip("一张个幅只副的")
    return prompt or None
