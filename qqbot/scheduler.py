"""定时任务：5 段 cron 解析 + 执行线程。

cron 语法（常用子集）：分 时 日 月 周
    *        任意
    */n      每 n 个单位
    a-b      区间
    a-b/n    区间内每 n 个
    a,b,c    枚举
周：0-6，0 为周日（也接受 7）

例：
    0 9 * * *        每天 09:00
    */30 * * * *     每 30 分钟
    0 9 * * 1-5      工作日 09:00
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta

from .api import ApiError, QQOpenAPI
from .config import Config
from .store import Store

RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]


class CronError(ValueError):
    pass


def _parse_field(text: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise CronError("空的字段片段")
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) <= 0:
                raise CronError(f"步长无效: {step_s}")
            step = int(step_s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a_s, b_s = part.split("-", 1)
            if not (a_s.isdigit() and b_s.isdigit()):
                raise CronError(f"区间无效: {part}")
            a, b = int(a_s), int(b_s)
        else:
            if not part.isdigit():
                raise CronError(f"数值无效: {part}")
            a = b = int(part)
        if a < lo or b > hi or a > b:
            raise CronError(f"越界: {part}（允许 {lo}-{hi}）")
        out.update(range(a, b + 1, step))
    return out


def parse(expr: str) -> list[set[int]]:
    fields = expr.split()
    if len(fields) != 5:
        raise CronError(f"cron 需要 5 段，收到 {len(fields)} 段")
    sets = []
    for text, (lo, hi) in zip(fields, RANGES):
        s = _parse_field(text, lo, hi)
        if hi == 6:
            # 周日既写 0 也写 7
            if 7 in s:
                s.discard(7)
                s.add(0)
        sets.append(s)
    return sets


def match(expr: str, dt: datetime) -> bool:
    try:
        m, h, dom, mon, dow = parse(expr)
    except CronError:
        return False
    return (dt.minute in m and dt.hour in h and dt.day in dom
            and dt.month in mon and (dt.weekday() + 1) % 7 in dow)


def next_after(expr: str, base: datetime | None = None, limit_days: int = 400) -> int | None:
    """返回下一次执行的时间戳；解析失败或无解返回 None。"""
    try:
        m, h, dom, mon, dow = parse(expr)
    except CronError:
        return None
    cur = (base or datetime.now()).replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = cur + timedelta(days=limit_days)
    while cur < end:
        if (cur.minute in m and cur.hour in h and cur.day in dom
                and cur.month in mon and (cur.weekday() + 1) % 7 in dow):
            return int(cur.timestamp())
        cur += timedelta(minutes=1)
    return None


class Scheduler:
    def __init__(self, cfg: Config, store: Store, api: QQOpenAPI):
        self.cfg = cfg
        self.store = store
        self.api = api
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="scheduler", daemon=True)
        self._last_agg = 0.0

    def start(self):
        self._bootstrap()
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _bootstrap(self):
        """启动时给没有 next_run 的任务补算。"""
        for row in self.store.list_schedules():
            if row["enabled"] and not row["next_run"]:
                nxt = next_after(row["cron"])
                self.store.finish_schedule(row["id"], nxt, row["last_result"] or "已排程")
                print(f"[sched] {row['name']} 下次执行 {datetime.fromtimestamp(nxt) if nxt else '无法解析'}", flush=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:
                print(f"[sched] tick 失败: {type(exc).__name__}: {exc}", flush=True)
            self._stop.wait(20)

    def _tick(self):
        now = time.time()
        # 每 5 分钟聚合一次小时指标
        if now - self._last_agg > 300:
            self._last_agg = now
            self.store.aggregate_hours(48)

        for row in self.store.due_schedules(int(now)):
            self._execute(dict(row))

    def _execute(self, row: dict):
        groups = []
        try:
            groups = json.loads(row["groups"] or "[]")
        except json.JSONDecodeError:
            groups = []
        if not groups:
            groups = [r["group_openid"] for r in self.store.list_groups()]

        nxt = next_after(row["cron"])
        if self.cfg.dry_run:
            self.store.finish_schedule(row["id"], nxt, "dry_run 锁定，未发送")
            print(f"[sched] 「{row['name']}」到点但 dry_run 锁定", flush=True)
            return

        ok, fail = 0, 0
        for g in groups:
            try:
                self.api.send_message(g, row["text"])
                ok += 1
                self.store.log_action("schedule", g, None,
                                      json.dumps({"name": row["name"]}, ensure_ascii=False),
                                      True, "HTTP 200")
            except ApiError as exc:
                fail += 1
                self.store.log_action("schedule", g, None,
                                      json.dumps({"name": row["name"]}, ensure_ascii=False),
                                      False, f"{exc.code} {exc.message}")
        self.store.finish_schedule(row["id"], nxt, f"成功 {ok} / 失败 {fail}")
        print(f"[sched] 「{row['name']}」执行完成 成功{ok} 失败{fail}", flush=True)
