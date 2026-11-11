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
    note         TEXT,
    title        TEXT
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

CREATE TABLE IF NOT EXISTS rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    match_type  TEXT NOT NULL,              -- contains | regex
    pattern     TEXT NOT NULL,
    action      TEXT NOT NULL,              -- recall | mute | recall_mute
    params      TEXT NOT NULL DEFAULT '{}', -- {"minutes": 10}
    groups      TEXT NOT NULL DEFAULT '[]', -- 空数组 = 全部群
    enabled     INTEGER NOT NULL DEFAULT 1,
    hits        INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL,
    last_hit_at INTEGER
);
CREATE TABLE IF NOT EXISTS disposals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id       INTEGER,
    group_openid  TEXT,
    member_openid TEXT,
    message_id    TEXT,
    action        TEXT,
    ok            INTEGER NOT NULL,
    result        TEXT,
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_disposals_time ON disposals(created_at DESC);
CREATE TABLE IF NOT EXISTS schedules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    cron        TEXT NOT NULL,              -- 5 段 cron：分 时 日 月 周
    text        TEXT NOT NULL,
    groups      TEXT NOT NULL DEFAULT '[]',
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_run    INTEGER,
    next_run    INTEGER,
    last_result TEXT,
    created_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS metrics_hourly (
    bucket       INTEGER NOT NULL,
    group_openid TEXT NOT NULL,
    messages     INTEGER NOT NULL,
    members      INTEGER NOT NULL,
    PRIMARY KEY (bucket, group_openid)
);
CREATE TABLE IF NOT EXISTS chat_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    group_openid  TEXT NOT NULL,
    member_openid TEXT,
    role          TEXT NOT NULL,
    content       TEXT NOT NULL,
    model         TEXT,
    kind          TEXT,
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_group ON chat_history(group_openid, id DESC);
CREATE TABLE IF NOT EXISTS chat_usage (
    day          TEXT NOT NULL,
    group_openid TEXT NOT NULL,
    calls        INTEGER NOT NULL DEFAULT 0,
    tokens       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, group_openid)
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS group_settings (
    group_openid TEXT PRIMARY KEY,
    auto_rules   INTEGER NOT NULL DEFAULT 1,
    note         TEXT,
    updated_at   INTEGER NOT NULL
);
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
            # 老库补列：群名不从事件推送，需要调用 info 接口后缓存
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(groups)").fetchall()]
            if "title" not in cols:
                conn.execute("ALTER TABLE groups ADD COLUMN title TEXT")
            ccols = [r["name"] for r in conn.execute("PRAGMA table_info(chat_history)").fetchall()]
            if "model" not in ccols:
                conn.execute("ALTER TABLE chat_history ADD COLUMN model TEXT")
            if "kind" not in ccols:
                conn.execute("ALTER TABLE chat_history ADD COLUMN kind TEXT")

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

    def set_group_title(self, group_openid: str, title: str):
        if not title:
            return
        with self._conn() as conn:
            conn.execute("UPDATE groups SET title=? WHERE group_openid=? AND "
                         "(title IS NULL OR title<>?)", (title, group_openid, title))

    def groups_missing_title(self, limit: int = 8) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT group_openid FROM groups WHERE title IS NULL OR title='' LIMIT ?",
                (limit,)).fetchall()
        return [r["group_openid"] for r in rows]

    def list_groups(self) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT g.group_openid, g.title, g.last_seen, "
                "(SELECT COUNT(*) FROM members m WHERE m.group_openid=g.group_openid) AS members, "
                "(SELECT COUNT(*) FROM messages x WHERE x.group_openid=g.group_openid) AS messages "
                "FROM groups g ORDER BY g.last_seen DESC").fetchall()

    def list_members(self, group_openid: str, limit: int = 50) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT member_openid, nickname, union_openid, first_seen, last_seen, msg_count "
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

    # ---------------- 规则 ----------------

    def list_rules(self, enabled_only: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT * FROM rules"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY id DESC"
        with self._conn() as conn:
            return conn.execute(sql).fetchall()

    def add_rule(self, name: str, match_type: str, pattern: str, action: str,
                 params: str = "{}", groups: str = "[]", enabled: bool = True) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO rules(name, match_type, pattern, action, params, groups, enabled, created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (name, match_type, pattern, action, params, groups, int(enabled), int(time.time())))
            return int(cur.lastrowid)

    def update_rule(self, rule_id: int, name: str, match_type: str, pattern: str,
                    action: str, params: str, groups: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE rules SET name=?, match_type=?, pattern=?, action=?, params=?, groups=? "
                "WHERE id=?",
                (name, match_type, pattern, action, params, groups, rule_id))

    def set_rule_enabled(self, rule_id: int, enabled: bool):
        with self._conn() as conn:
            conn.execute("UPDATE rules SET enabled=? WHERE id=?", (int(enabled), rule_id))

    def delete_rule(self, rule_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))

    def bump_rule(self, rule_id: int):
        with self._conn() as conn:
            conn.execute("UPDATE rules SET hits=hits+1, last_hit_at=? WHERE id=?",
                         (int(time.time()), rule_id))

    def log_disposal(self, rule_id: int | None, group_openid: str, member_openid: str | None,
                     message_id: str | None, action: str, ok: bool, result: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO disposals(rule_id, group_openid, member_openid, message_id, action, ok, result, created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (rule_id, group_openid, member_openid, message_id, action, int(ok), result, int(time.time())))

    def recent_disposals(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT d.*, mb.nickname FROM disposals d "
                "LEFT JOIN members mb ON mb.group_openid = d.group_openid "
                "AND mb.member_openid = d.member_openid "
                "ORDER BY d.id DESC LIMIT ?", (limit,)).fetchall()

    # ---------------- 定时任务 ----------------

    def list_schedules(self) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute("SELECT * FROM schedules ORDER BY id DESC").fetchall()

    def add_schedule(self, name: str, cron: str, text: str, groups: str = "[]",
                     enabled: bool = True) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO schedules(name, cron, text, groups, enabled, created_at) VALUES(?,?,?,?,?,?)",
                (name, cron, text, groups, int(enabled), int(time.time())))
            return int(cur.lastrowid)

    def set_schedule_enabled(self, sid: int, enabled: bool):
        with self._conn() as conn:
            conn.execute("UPDATE schedules SET enabled=? WHERE id=?", (int(enabled), sid))

    def delete_schedule(self, sid: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM schedules WHERE id=?", (sid,))

    def finish_schedule(self, sid: int, next_run: int | None, result: str):
        with self._conn() as conn:
            conn.execute("UPDATE schedules SET last_run=?, next_run=?, last_result=? WHERE id=?",
                         (int(time.time()), next_run, result, sid))

    def due_schedules(self, now: int) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM schedules WHERE enabled=1 AND next_run IS NOT NULL AND next_run<=?",
                (now,)).fetchall()

    # ---------------- 群设置 ----------------

    def get_group_setting(self, group_openid: str) -> dict:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM group_settings WHERE group_openid=?",
                               (group_openid,)).fetchone()
        return dict(row) if row else {"group_openid": group_openid, "auto_rules": 1, "note": None}

    def set_group_auto_rules(self, group_openid: str, enabled: bool):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO group_settings(group_openid, auto_rules, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(group_openid) DO UPDATE SET auto_rules=excluded.auto_rules, "
                "updated_at=excluded.updated_at",
                (group_openid, int(enabled), int(time.time())))

    # ---------------- 对话 ----------------

    def add_chat(self, group_openid: str, member_openid: str | None, role: str, content: str,
                 model: str | None = None, kind: str | None = None):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO chat_history(group_openid, member_openid, role, content, model, kind, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (group_openid, member_openid, role, content, model, kind, int(time.time())))

    def chat_context(self, group_openid: str, turns: int = 6) -> list[sqlite3.Row]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT role, content FROM chat_history WHERE group_openid=? ORDER BY id DESC LIMIT ?",
                (group_openid, turns * 2)).fetchall()
        return list(reversed(rows))

    def recent_chats(self, limit: int = 40) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT h.*, g.title AS group_title, mb.nickname AS member_nickname "
                "FROM chat_history h "
                "LEFT JOIN groups g ON g.group_openid = h.group_openid "
                "LEFT JOIN members mb ON mb.group_openid = h.group_openid "
                "AND mb.member_openid = h.member_openid "
                "ORDER BY h.id DESC LIMIT ?", (limit,)).fetchall()

    def bump_chat_usage(self, group_openid: str, tokens: int = 0) -> int:
        day = time.strftime("%Y-%m-%d")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO chat_usage(day, group_openid, calls, tokens) VALUES(?,?,1,?) "
                "ON CONFLICT(day, group_openid) DO UPDATE SET calls=calls+1, tokens=tokens+excluded.tokens",
                (day, group_openid, tokens))
            row = conn.execute("SELECT calls FROM chat_usage WHERE day=? AND group_openid=?",
                               (day, group_openid)).fetchone()
        return int(row["calls"]) if row else 0

    def chat_calls_today(self, group_openid: str) -> int:
        day = time.strftime("%Y-%m-%d")
        with self._conn() as conn:
            row = conn.execute("SELECT calls FROM chat_usage WHERE day=? AND group_openid=?",
                               (day, group_openid)).fetchone()
        return int(row["calls"]) if row else 0

    def chat_usage_today(self) -> dict:
        day = time.strftime("%Y-%m-%d")
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(calls),0) AS c, COALESCE(SUM(tokens),0) AS t "
                "FROM chat_usage WHERE day=?", (day,)).fetchone()
        return {"calls": int(row["c"]), "tokens": int(row["t"])}

    # ---------------- KV ----------------

    def get_kv(self, key: str, default: str = "") -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_kv(self, key: str, value: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO kv(key, value, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, int(time.time())))

    # ---------------- 统计聚合 ----------------

    def upsert_metrics(self, bucket: int, group_openid: str, messages: int, members: int):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO metrics_hourly(bucket, group_openid, messages, members) VALUES(?,?,?,?) "
                "ON CONFLICT(bucket, group_openid) DO UPDATE SET messages=excluded.messages, "
                "members=excluded.members",
                (bucket, group_openid, messages, members))

    def aggregate_hours(self, hours: int = 48):
        """把最近 N 小时的消息量写进 metrics_hourly，供长期统计。"""
        now_bucket = int(time.time() // 3600) * 3600
        start = now_bucket - hours * 3600
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT (created_at/3600)*3600 AS b, group_openid, COUNT(*) AS n, "
                "COUNT(DISTINCT member_openid) AS m FROM messages WHERE created_at>=? "
                "GROUP BY b, group_openid", (start,)).fetchall()
        for r in rows:
            self.upsert_metrics(int(r["b"]), r["group_openid"], int(r["n"]), int(r["m"]))

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
        sql = ("SELECT m.message_id, m.member_openid, m.content, m.created_at, m.recalled, "
               "mb.nickname FROM messages m "
               "LEFT JOIN members mb ON mb.group_openid=m.group_openid "
               "AND mb.member_openid=m.member_openid WHERE m.group_openid=?")
        args: list = [group_openid]
        if q:
            sql += " AND m.content LIKE ?"
            args.append(f"%{q}%")
        sql += " ORDER BY m.created_at DESC LIMIT ?"
        args.append(limit)
        with self._conn() as conn:
            return conn.execute(sql, tuple(args)).fetchall()

    def messages(self, group_openid: str, limit: int = 50) -> list[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT m.message_id, m.member_openid, m.content, m.created_at, m.recalled, "
                "mb.nickname FROM messages m "
                "LEFT JOIN members mb ON mb.group_openid=m.group_openid "
                "AND mb.member_openid=m.member_openid "
                "WHERE m.group_openid=? ORDER BY m.created_at DESC LIMIT ?",
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
