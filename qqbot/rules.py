"""规则引擎：关键词 / 正则命中 → 自动撤回、禁言。

设计要点：
1. 事件已经落库，处置在独立线程做，不阻塞 Webhook 的 3 秒响应。
2. 规则表 5 秒缓存，改规则不必重启。
3. 同一 (message_id, rule_id) 只处置一次，平台重推不会重复禁言。
4. dry_run 生效：命中只记 skipped，不调接口。
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time

from .api import ApiError, QQOpenAPI
from .config import Config
from .store import Store

CACHE_TTL = 2.0


class Engine:
    def __init__(self, cfg: Config, store: Store, api: QQOpenAPI):
        self.cfg = cfg
        self.store = store
        self.api = api
        self.q: queue.Queue = queue.Queue(maxsize=5000)
        self._rules: list[dict] = []
        self._rules_at = 0.0
        self._seen: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="rules", daemon=True)

    # ---------------- 生命周期 ----------------

    def start(self):
        self._thread.start()

    def invalidate(self):
        """规则变更后立即失效缓存（面板新建规则时调用）。"""
        self._rules_at = 0.0

    def stop(self):
        self._stop.set()

    def submit(self, group_openid: str, member_openid: str, message_id: str, content: str):
        if not group_openid or not content:
            return
        try:
            self.q.put_nowait((group_openid, member_openid, message_id, content))
        except queue.Full:
            print("[rules] 队列已满，丢弃一条", flush=True)

    # ---------------- 内部 ----------------

    def _load_rules(self) -> list[dict]:
        now = time.time()
        if now - self._rules_at < CACHE_TTL:
            return self._rules
        self._rules_at = now
        out = []
        for r in self.store.list_rules(enabled_only=True):
            d = dict(r)
            try:
                d["params_obj"] = json.loads(d.get("params") or "{}")
            except json.JSONDecodeError:
                d["params_obj"] = {}
            try:
                d["groups_obj"] = json.loads(d.get("groups") or "[]")
            except json.JSONDecodeError:
                d["groups_obj"] = []
            out.append(d)
        self._rules = out
        return out

    @staticmethod
    def matches(rule: dict, text: str) -> bool:
        pattern = rule.get("pattern") or ""
        if not pattern:
            return False
        if rule.get("match_type") == "regex":
            try:
                return re.search(pattern, text) is not None
            except re.error:
                return False
        return pattern.lower() in text.lower()

    def _dedup(self, key: str) -> bool:
        now = time.time()
        # 顺手清理 10 分钟前的键，避免无限增长
        if len(self._seen) > 4000:
            self._seen = {k: v for k, v in self._seen.items() if now - v < 600}
        if key in self._seen:
            return False
        self._seen[key] = now
        return True

    def _run(self):
        while not self._stop.is_set():
            try:
                group, member, message_id, content = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._evaluate(group, member, message_id, content)
            except Exception as exc:  # 单条失败不能拖垮线程
                print(f"[rules] 处理失败: {type(exc).__name__}: {exc}", flush=True)

    def _evaluate(self, group: str, member: str, message_id: str, content: str):
        if self.store.get_group_setting(group).get("auto_rules", 1) == 0:
            return
        for rule in self._load_rules():
            scope = rule.get("groups_obj") or []
            if scope and group not in scope:
                continue
            if not self.matches(rule, content):
                continue
            if not self._dedup(f"{rule['id']}:{message_id}"):
                continue
            self._apply(rule, group, member, message_id, content)

    def _apply(self, rule: dict, group: str, member: str, message_id: str, content: str):
        action = rule.get("action") or "recall"
        params = rule.get("params_obj") or {}
        minutes = int(params.get("minutes") or 10)
        reply_text = (params.get("reply") or "").strip()
        self.store.bump_rule(rule["id"])

        if self.cfg.dry_run:
            self.store.log_disposal(rule["id"], group, member, message_id, action, False,
                                    "dry_run 锁定，未执行")
            print(f"[rules] 命中「{rule['name']}」但 dry_run 锁定", flush=True)
            return

        results = []
        ok_all = True
        if action in ("recall", "recall_mute", "recall_reply") and message_id:
            try:
                st, _ = self.api.recall_message(group, message_id)
                self.store.mark_recalled(message_id)
                results.append(f"recall HTTP {st}")
            except ApiError as exc:
                ok_all = False
                results.append(f"recall 失败: {exc.code} {exc.message}")
        if action in ("mute", "recall_mute") and member:
            try:
                st, _ = self.api.mute_member(group, member, minutes)
                results.append(f"mute {minutes}min HTTP {st}")
            except ApiError as exc:
                ok_all = False
                results.append(f"mute 失败: {exc.code} {exc.message}")
        if action in ("reply", "recall_reply") and reply_text and message_id:
            sent = False
            if self.cfg.reply_at_user and member:
                try:
                    st, _ = self.api.send_message_at(group, reply_text, member, msg_id=message_id)
                    results.append(f"reply HTTP {st}（@ 提问者）")
                    sent = True
                except ApiError as exc:
                    results.append(f"at 失败 {exc.code}，改用纯文本")
            if not sent:
                try:
                    st, _ = self.api.send_message(group, reply_text, msg_id=message_id)
                    results.append(f"reply HTTP {st}")
                except ApiError as exc:
                    ok_all = False
                    results.append(f"reply 失败: {exc.code} {exc.message}")

        result = "; ".join(results) or "无动作"
        self.store.log_disposal(rule["id"], group, member, message_id, action, ok_all, result)
        print(f"[rules] 「{rule['name']}」命中 群={group[:8]}… {result}", flush=True)
