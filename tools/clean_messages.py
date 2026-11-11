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


def main() -> int:
    apply = "--apply" in sys.argv
    cfg = C.load()
    store = Store(cfg.db_path)
    with store._conn() as conn:  # noqa: SLF001 内部脚本，直接用连接
        rows = conn.execute(
            "SELECT message_id, group_openid, content FROM messages "
            "WHERE content LIKE '%<@%' OR content LIKE '%<faceType%'").fetchall()
        print(f"待清洗 {len(rows)} 条")
        if not apply:
            for r in rows[:5]:
                print(" 前:", r["content"][:50])
                print(" 后:", clean(r["content"], lambda o: store.member_nickname(r["group_openid"], o))[:50])
            print("（预览模式，加 --apply 才会写库）")
            return 0
        for r in rows:
            fixed = clean(r["content"], lambda o: store.member_nickname(r["group_openid"], o))
            if fixed != r["content"]:
                conn.execute("UPDATE messages SET content=? WHERE message_id=?",
                             (fixed, r["message_id"]))
    print("已写库")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
