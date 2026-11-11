"""消息文本清洗：把平台标签换成可读文本。

群消息里 @ 是 `<@!openid>`，表情是 `<faceType=6,faceld="0",ext="...">`，
原样存进库、喂给模型都很难看，统一在入库和对话时清洗。
"""

from __future__ import annotations

import re

MENTION = re.compile(r"<@!?([0-9A-Za-z_\-]{4,64})>")
FACE = re.compile(r"<faceType=[^>]*>")


def clean(text: str, resolve=None) -> str:
    """resolve(openid) -> 昵称；拿不到就显示「成员」。"""
    if not text:
        return ""

    def _sub(m):
        oid = m.group(1)
        name = ""
        if resolve:
            try:
                name = resolve(oid) or ""
            except Exception:
                name = ""
        return f"@{name or ('成员' + oid[-4:])}"

    out = MENTION.sub(_sub, text)
    out = FACE.sub("[表情]", out)
    return re.sub(r"[ \t]{2,}", " ", out).strip()
