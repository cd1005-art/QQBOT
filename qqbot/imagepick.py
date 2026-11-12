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
    "漫画": "漫画 插画",
    "插画": "插画 唯美",
    "壁纸": "高清 壁纸",
    "头像": "动漫 头像",
    "表情包": "表情包 搞笑",
    "萌图": "萌系 插画",
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

# 只要「动作字 + 图相关字」同时出现就认，避免「来几张」这类变体漏判
VERBS = ("来", "找", "发", "要", "给", "整", "换", "搜", "随机", "随便")
NOUNS = ("图", "照片", "壁纸", "表情包", "壁纸图")


def intent(text: str) -> bool:
    """动作字 + （图片类名词 或 已支持的类别名）即认为在要图。

    这样「来几张二次元」这种省略了「图片」二字的说法也能命中。
    """
    t = (text or "").strip()
    if not t or len(t) > 40:
        return False
    if not any(v in t for v in VERBS):
        return False
    return any(n in t for n in NOUNS) or any(k in t for k in CATEGORIES)


def classify(text: str, allowed: list[str] | None = None) -> dict | None:
    """识别找图意图，返回 {key, query, allowed}；非找图请求返回 None。

    与 detect 的区别：类别没放行时也返回结果（allowed=False），
    让调用方能明确告诉用户「这类没开」，而不是把话丢给模型瞎编。
    """
    t = (text or "").strip()
    if not intent(t):
        return None
    for key in sorted(CATEGORIES, key=len, reverse=True):
        if key in t:
            # allowed 为 None 表示内部不限制；空列表表示「一个都不允许」
            ok = allowed is None or key in allowed
            return {"key": key, "query": CATEGORIES[key], "allowed": ok}
    return None


def random_choice(allowed: list[str]) -> dict | None:
    """没指定类别时，从已启用类别里随机挑一个。"""
    pool = [k for k in CATEGORIES if k in (allowed or [])]
    if not pool:
        return None
    import random
    key = random.choice(pool)
    return {"key": key, "query": CATEGORIES[key], "allowed": True}


def detect(text: str, allowed: list[str] | None = None) -> str | None:
    """识别「找图」意图，返回查询词；不是找图请求或类别未放行则返回 None。

    allowed 为空表示不限制（不推荐），否则只认白名单里的类别。
    """
    info = classify(text, allowed)
    return info["query"] if info and info["allowed"] else None


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
                ctype = (resp.headers.get("Content-Type") or "").split(";")[0].lower()
                # 平台富媒体只收 png/jpg，gif/webp 会被拒，直接跳过
                if ctype not in ("image/jpeg", "image/jpg", "image/png"):
                    continue
                blob = resp.read(max_bytes + 1)
            if len(blob) > max_bytes or len(blob) < 1024:
                continue
            cand["bytes"] = len(blob)
            cand["mime"] = ctype
            cand["blob"] = blob
            return cand
        except Exception:
            continue
    return None
