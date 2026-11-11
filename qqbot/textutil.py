"""消息文本清洗：把平台标签换成可读文本。

群消息里 @ 是 `<@!openid>`，表情是 `<faceType=6,faceld="0",ext="...">`，
原样存进库、喂给模型都很难看，统一在入库和对话时清洗。
"""

from __future__ import annotations

import re

MENTION = re.compile(r"<@!?([0-9A-Za-z_\-]{4,64})>")
FACE = re.compile(r"<faceType=[^>]*>")


def clean(text: str, resolve=None, bot_ids=None, bot_name: str = "机器人") -> str:
    """resolve(openid) -> 昵称；bot_ids 命中时用其映射名，其余未知显示 openid 尾号。

    bot_ids 支持 {openid: 名字} 或 [openid, ...]（后者统一用 bot_name）。
    """
    bots = dict(bot_ids or {})
    if bot_ids and not isinstance(bot_ids, dict):
        bots = {i: bot_name for i in bot_ids}
    if not text:
        return ""

    def _sub(m):
        oid = m.group(1)
        if oid in bots:
            return f"@{bots.get(oid) or bot_name}"
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
