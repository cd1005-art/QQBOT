"""网络找图：按关键词取图，返回可下载的图片地址。

来源用 360 图片的 JSON 接口（无需 key，响应快）。百度已对爬虫返回 antiFlag，
搜狗需要 cookie，因此只保留一个来源，失败即返回空，不做多源重试。
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://image.so.com/",
}

# 关键词 → 搜索词。刻意避开容易踩平台审核的表述。
CATEGORIES: dict[str, str] = {
    "二次元": "二次元 动漫 壁纸",
    "动漫": "动漫 壁纸",
    "搞笑": "搞笑 表情包",
    "沙雕": "沙雕 搞笑 图",
    "恐怖": "恐怖 氛围 图",
    "风景": "风景 壁纸",
    "宠物": "萌宠 可爱",
    "猫": "猫咪 可爱",
    "狗": "狗狗 可爱",
    "游戏": "游戏 壁纸",
    "汽车": "汽车 壁纸",
    "美食": "美食 摄影",
    "美女": "美女 写真 唯美",
}

VERBS = ("来张", "来一张", "找张", "找一张", "发张", "发一张", "整张", "给我一张", "来点", "找点")


def detect(text: str) -> str | None:
    """从群消息里识别「找图」意图，返回查询词；不是找图请求返回 None。"""
    t = (text or "").strip()
    if not t or len(t) > 40:
        return None
    has_verb = any(v in t for v in VERBS) or ("图片" in t) or ("照片" in t)
    if not has_verb:
        return None
    for key, query in CATEGORIES.items():
        if key in t:
            return query
    return None


def search(query: str, count: int = 8) -> list[dict]:
    url = ("https://image.so.com/j?" + urllib.parse.urlencode({
        "q": query, "pn": 0, "rn": count,
    }))
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return []

    out = []
    for item in (data.get("list") or []):
        # 注意：360 返回的 https 字段只是主机名，不是完整地址，必须用 img / thumb
        link = item.get("img") or item.get("thumb") or ""
        if not link:
            continue
        if link.startswith("//"):
            link = "https:" + link
        elif link.startswith("http://"):
            link = "https://" + link[len("http://"):]
        if not link.startswith("https://"):
            continue
        w, h = int(item.get("width") or 0), int(item.get("height") or 0)
        if w and h and (w < 300 or h < 300):
            continue
        out.append({"url": link, "title": (item.get("title") or "")[:60],
                    "width": w, "height": h})
    return out


def pick(query: str, max_bytes: int = 5 * 1024 * 1024) -> dict | None:
    """按顺序试，取第一张能下载且体积合规的图。"""
    for cand in search(query):
        try:
            req = urllib.request.Request(cand["url"], headers=UA)
            with urllib.request.urlopen(req, timeout=10) as resp:
                ctype = (resp.headers.get("Content-Type") or "").split(";")[0]
                if not ctype.startswith("image/"):
                    continue
                blob = resp.read(max_bytes + 1)
            if len(blob) > max_bytes or len(blob) < 1024:
                continue
            cand["bytes"] = len(blob)
            cand["mime"] = ctype
            return cand
        except Exception:
            continue
    return None
