"""把历史消息里的 <@openid> / <faceType=...> 清洗成可读文本。

只改 messages.content（raw_events 保留原始 payload，随时可回溯）。
用法：python tools/clean_messages.py [--apply]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qqbot import config as C  # noqa: E402
from qqbot.store import Store  # noqa: E402
from qqbot.textutil import clean  # noqa: E402


def rebuild_from_raw(store: Store, apply: bool) -> int:
    """从 raw_events 重新推导正文，可修复被清洗过头的历史（标签已丢的情况）。"""
    import json
    import sqlite3

    names = json.loads(store.get_kv("bot_names", "{}") or "{}")
    conn = sqlite3.connect(store.path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT payload FROM raw_events WHERE event_type LIKE '%MESSAGE%' "
        "ORDER BY received_at DESC LIMIT 5000").fetchall()
    changed, seen = 0, set()
    for r in rows:
        d = json.loads(r["payload"]).get("d") or {}
        mid, g, raw = d.get("id"), d.get("group_openid"), d.get("content") or ""
        if not mid or not g or mid in seen or ("<@" not in raw and "<faceType" not in raw):
            continue
        seen.add(mid)
        text = clean(raw, lambda oid: store.member_nickname(g, oid),
                     bot_ids=names, bot_name="机器人")
        cur = conn.execute("SELECT content FROM messages WHERE message_id=?", (mid,)).fetchone()
        if cur and cur["content"] != text:
            if apply:
                conn.execute("UPDATE messages SET content=? WHERE message_id=?", (text, mid))
            changed += 1
            if changed <= 5:
                print(f" {cur['content'][:40]}\n → {text[:40]}")
    if apply:
        conn.commit()
    print(f"可修复 {changed} 条" + ("" if apply else "（预览，加 --apply 写库）"))
    return 0


def main() -> int:
    apply = "--apply" in sys.argv
    cfg = C.load()
    store = Store(cfg.db_path)
    bot_name = store.get_kv("bot_name", "") or "机器人"
    if "--from-raw" in sys.argv:
        return rebuild_from_raw(store, apply)
    try:
        bot_ids = __import__("json").loads(store.get_kv("bot_names", "{}") or "{}")
    except Exception:
        bot_ids = {}
    with store._conn() as conn:  # noqa: SLF001 内部脚本，直接用连接
        rows = conn.execute(
            "SELECT message_id, group_openid, content FROM messages "
            "WHERE content LIKE '%<@%' OR content LIKE '%<faceType%'").fetchall()
        print(f"待清洗 {len(rows)} 条")
        if not apply:
            for r in rows[:5]:
                print(" 前:", r["content"][:50])
                print(" 后:", clean(r["content"], lambda o: store.member_nickname(r["group_openid"], o),
                                   bot_ids=bot_ids, bot_name=bot_name)[:50])
            print("（预览模式，加 --apply 才会写库）")
            return 0
        for r in rows:
            fixed = clean(r["content"], lambda o: store.member_nickname(r["group_openid"], o),
                          bot_ids=bot_ids, bot_name=bot_name)
            if fixed != r["content"]:
                conn.execute("UPDATE messages SET content=? WHERE message_id=?",
                             (fixed, r["message_id"]))
    print("已写库")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
