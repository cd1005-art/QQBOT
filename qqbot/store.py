"""SQLite 落库：原始事件、群/成员 openid 映射、消息 id 索引、操作审计。"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    group_openid TEXT PRIMARY KEY,
    first_seen   INTEGER NOT NULL,
    last_seen    INTEGER NOT NULL,
    note         TEXT
);
CREATE TABLE IF NOT EXISTS members (
    group_openid  TEXT NOT NULL,
    member_openid TEXT NOT NULL,
    union_openid  TEXT,
    nickname      TEXT,
    first_seen    INTEGER NOT NULL,
    last_seen     INTEGER NOT NULL,
    msg_count     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (group_openid, member_openid)
);
CREATE TABLE IF NOT EXISTS messages (
    message_id    TEXT PRIMARY KEY,
    group_openid  TEXT NOT NULL,
    member_openid TEXT,
    content       TEXT,
    created_at    INTEGER NOT NULL,
    recalled      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_group_time ON messages(group_openid, created_at DESC);
CREATE TABLE IF NOT EXISTS audit (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            INTEGER NOT NULL,
    action        TEXT NOT NULL,
    group_openid  TEXT,
    target_openid TEXT,
    payload       TEXT,
    ok            INTEGER NOT NULL,
    result        TEXT
);
CREATE TABLE IF NOT EXISTS raw_events (
    event_key     TEXT PRIMARY KEY,
    event_type    TEXT NOT NULL,
    group_openid  TEXT,
    payload       TEXT,
    received_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_raw_events_type ON raw_events(event_type, received_at DESC);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            # WAL：读写并发不互相阻塞，Webhook 写入与 CLI 读取可同时进行
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------------- 写入 ----------------

    def touch_group(self, group_openid: str):
        now = int(time.time())
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO groups(group_openid, first_seen, last_seen) VALUES(?,?,?) "
                "ON CONFLICT(group_openid) DO UPDATE SET last_seen=excluded.last_seen",
                (group_openid, now, now))

    def touch_member(self, group_openid: str, member_openid: str,
                     union_openid: str | None = None, nickname: str | None = None):
        now = int(time.time())
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO members(group_openid, member_openid, union_openid, nickname, first_seen, last_seen, msg_count) "
                "VALUES(?,?,?,?,?,?,1) "
                "ON CONFLICT(group_openid, member_openid) DO UPDATE SET "
                "last_seen=excluded.last_seen, msg_count=msg_count+1, "
                "union_openid=COALESCE(excluded.union_openid, union_openid), "
                "nickname=COALESCE(excluded.nickname, nickname)",
                (group_openid, member_openid, union_openid, nickname, now, now))

    def add_message(self, message_id: str, group_openid: str, member_openid: str | None,
                    content: str | None, created_at: int | None = None):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO messages(message_id, group_openid, member_openid, content, created_at) "
                "VALUES(?,?,?,?,?)",
                (message_id, group_openid, member_openid, content, created_at or int(time.time())))

    def mark_recalled(self, message_id: str):
        with self._conn() as conn:
            conn.execute("UPDATE messages SET recalled=1 WHERE message_id=?", (message_id,))

    def log_action(self, action: str, group_openid: str | None, target_openid: str | None,
                   payload: str, ok: bool, result: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO audit(ts, action, group_openid, target_openid, payload, ok, result) "
                "VALUES(?,?,?,?,?,?,?)",
                (int(time.time()), action, group_openid, target_openid, payload, int(ok), result))

    def record_event(self, event_key: str, event_type: str, group_openid: str | None,
                     payload: str) -> bool:
        """写原始事件，主键去重。True 表示首次收到（重推的返回 False）。"""
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO raw_events(event_key, event_type, group_openid, payload, received_at) "
                "VALUES(?,?,?,?,?)",
                (event_key, event_type, group_openid, payload, int(time.time())))
            return cur.rowcount > 0

    # ---------------- 查询 ----------------

    def list_groups(self) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT g.group_openid, g.last_seen, "
                "(SELECT COUNT(*) FROM members m WHERE m.group_openid=g.group_openid) AS members, "
                "(SELECT COUNT(*) FROM messages x WHERE x.group_openid=g.group_openid) AS messages "
                "FROM groups g ORDER BY g.last_seen DESC").fetchall()

    def list_members(self, group_openid: str, limit: int = 50) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT member_openid, nickname, union_openid, last_seen, msg_count "
                "FROM members WHERE group_openid=? ORDER BY last_seen DESC LIMIT ?",
                (group_openid, limit)).fetchall()

    def last_message(self, group_openid: str, only_active: bool = True) -> sqlite3.Row | None:
        sql = "SELECT * FROM messages WHERE group_openid=?"
        if only_active:
            sql += " AND recalled=0"
        sql += " ORDER BY created_at DESC LIMIT 1"
        with self._conn() as conn:
            return conn.execute(sql, (group_openid,)).fetchone()

    def recent_audit(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def recent_events(self, limit: int = 20, event_type: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT event_type, group_openid, received_at, payload FROM raw_events"
        args: tuple = ()
        if event_type:
            sql += " WHERE event_type=?"
            args = (event_type,)
        sql += " ORDER BY received_at DESC LIMIT ?"
        with self._conn() as conn:
            return conn.execute(sql, args + (limit,)).fetchall()

    def event_types(self) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT event_type, COUNT(*) AS n, MAX(received_at) AS last_at "
                "FROM raw_events GROUP BY event_type ORDER BY n DESC").fetchall()

    def all_raw_events(self) -> list[sqlite3.Row]:
        """全量原始事件，供 reindex 重放（代码补了处理逻辑后回填索引）。"""
        with self._conn() as conn:
            return conn.execute(
                "SELECT event_type, payload FROM raw_events ORDER BY received_at").fetchall()

    def stats(self) -> dict:
        with self._conn() as conn:
            one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
            return {
                "groups": one("SELECT COUNT(*) FROM groups"),
                "members": one("SELECT COUNT(*) FROM members"),
                "messages": one("SELECT COUNT(*) FROM messages"),
                "events": one("SELECT COUNT(*) FROM raw_events"),
                "audit": one("SELECT COUNT(*) FROM audit"),
                "today_events": one(
                    "SELECT COUNT(*) FROM raw_events WHERE received_at >= strftime('%s','now','start of day')"),
            }

    def hourly_messages(self, group_openid: str | None = None, hours: int = 24) -> list[dict]:
        """按小时聚合消息量，供趋势图。返回 [{bucket, n}]，bucket 为整点时间戳。"""
        since = int(time.time()) - hours * 3600
        sql = ("SELECT (created_at/3600)*3600 AS bucket, COUNT(*) AS n FROM messages "
               "WHERE created_at >= ?")
        args: tuple = (since,)
        if group_openid:
            sql += " AND group_openid=?"
            args = (since, group_openid)
        sql += " GROUP BY bucket ORDER BY bucket"
        now_bucket = int(time.time() // 3600) * 3600
        with self._conn() as conn:
            rows = {int(r["bucket"]): int(r["n"]) for r in conn.execute(sql, args).fetchall()}
        # 补齐空桶，前端不用处理断点
        return [{"bucket": b, "n": rows.get(b, 0)}
                for b in range(now_bucket - (hours - 1) * 3600, now_bucket + 1, 3600)]

    def top_members(self, group_openid: str, limit: int = 5) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT member_openid, nickname, msg_count, last_seen FROM members "
                "WHERE group_openid=? ORDER BY msg_count DESC LIMIT ?",
                (group_openid, limit)).fetchall()

    def search_messages(self, group_openid: str, q: str = "", limit: int = 50) -> list[sqlite3.Row]:
        sql = ("SELECT message_id, member_openid, content, created_at, recalled "
               "FROM messages WHERE group_openid=?")
        args: list = [group_openid]
        if q:
            sql += " AND content LIKE ?"
            args.append(f"%{q}%")
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._conn() as conn:
            return conn.execute(sql, tuple(args)).fetchall()

    def messages(self, group_openid: str, limit: int = 50) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT message_id, member_openid, content, created_at, recalled "
                "FROM messages WHERE group_openid=? ORDER BY created_at DESC LIMIT ?",
                (group_openid, limit)).fetchall()

    def event_stream(self, limit: int = 50, event_type: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT event_type, group_openid, payload, received_at FROM raw_events"
        args: tuple = ()
        if event_type:
            sql += " WHERE event_type=?"
            args = (event_type,)
        sql += " ORDER BY received_at DESC LIMIT ?"
        with self._conn() as conn:
            return conn.execute(sql, args + (limit,)).fetchall()
