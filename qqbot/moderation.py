"""图片审核引擎：下载 → 视觉判断 → 违规处置（撤回 / 禁言 / 提示）。

设计要点：
1. 独立线程 + 队列，图片下载与模型调用都不阻塞 Webhook 的 3 秒响应。
2. 同一 message_id 只审一次，平台重推不重复处置。
3. 处置沿用 audit / disposals 两本账，rule_id 为空表示来自视觉审核。
4. 模型输出要求严格 JSON，解析失败按「不违规」处理，避免误伤。
"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request

from .api import ApiError, QQOpenAPI
from .config import Config
from .store import Store
from .imagesink import from_config as sink_from_config
from .vision import VisionClient, VisionError, fetch_image, parse_verdict

DEFAULT_PROMPT = (
    "你是群聊内容审核员。判断这张图片是否违反群规，重点关注：色情低俗、暴力血腥、赌博、"
    "诈骗引流、二维码或联系方式广告、违法违规内容。\n"
    "只输出一行 JSON，不要 markdown，不要解释：\n"
    '{"violation":true,"category":"类别","reason":"判断依据","summary":"一句话描述图片内容"}'
)


class VisionEngine:
    def __init__(self, cfg: Config, store: Store, api: QQOpenAPI):
        self.cfg = cfg
        self.store = store
        self.api = api
        self.q: queue.Queue = queue.Queue(maxsize=200)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="vision", daemon=True)
        self._seen: dict[str, float] = {}
        self.sink = sink_from_config(cfg)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    # ---------------- 入队 ----------------

    def submit(self, group: str, member: str, message_id: str, attachments: list):
        if not self.cfg.vision_enabled or not attachments:
            return
        images = [a for a in attachments
                  if str(a.get("content_type", "")).startswith("image/") and a.get("url")]
        if not images:
            return
        try:
            self.q.put_nowait((group, member, message_id, images))
        except queue.Full:
            print("[vision] 队列已满，跳过一张", flush=True)

    # ---------------- 主循环 ----------------

    def _run(self):
        while not self._stop.is_set():
            try:
                job = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._handle(*job)
            except Exception as exc:
                print(f"[vision] 处理失败: {type(exc).__name__}: {exc}", flush=True)

    def _download(self, url: str) -> tuple[bytes, str]:
        return fetch_image(url, self.cfg.vision_max_bytes)

    def _handle(self, group: str, member: str, message_id: str, images: list):
        now = time.time()
        if len(self._seen) > 500:
            self._seen = {k: v for k, v in self._seen.items() if now - v < 1800}
        if message_id in self._seen:
            return
        self._seen[message_id] = now

        att = images[0]
        try:
            image, mime = self._download(att["url"])
        except (urllib.error.URLError, ValueError, TimeoutError) as exc:
            print(f"[vision] 下载失败: {exc}", flush=True)
            return

        # 归档：审核过的图也进图床，便于事后追溯
        if self.sink.ok:
            self.sink.archive(self.store, image, source="moderation",
                              group=group, member=member, mime=mime,
                              filename=att.get("filename") or "")

        client = VisionClient(
            self.cfg.vision_base_url or self.cfg.llm_base_url,
            self.cfg.vision_api_key or self.cfg.llm_api_key,
            self.cfg.vision_model or self.cfg.llm_model,
            max_tokens=self.cfg.vision_max_tokens)
        try:
            text, tokens = client.ask(image, mime, self.cfg.vision_prompt or DEFAULT_PROMPT)
        except VisionError as exc:
            print(f"[vision] 模型调用失败: {exc}", flush=True)
            self.store.log_action("vision", group, member, att.get("filename", "")[:80],
                                  False, str(exc))
            return

        verdict = parse_verdict(text)
        summary = verdict.get("summary") or ""
        detail = json.dumps({k: verdict.get(k) for k in ("violation", "category", "reason", "summary")},
                            ensure_ascii=False)
        print(f"[vision] {'违规' if verdict['violation'] else '正常'} {summary[:40]} tokens={tokens}", flush=True)

        if not verdict.get("violation"):
            self.store.log_action("vision-ok", group, member, summary[:90], True, detail[:200])
            return

        action = self.cfg.vision_action
        results = []
        ok_all = True
        if action in ("recall", "recall_mute") and message_id:
            try:
                st, _ = self.api.recall_message(group, message_id)
                self.store.mark_recalled(message_id)
                results.append(f"recall HTTP {st}")
            except ApiError as exc:
                ok_all = False
                results.append(f"recall 失败: {exc.code} {exc.message}")
        if action in ("mute", "recall_mute") and member:
            try:
                st, _ = self.api.mute_member(group, member, self.cfg.vision_minutes)
                results.append(f"mute {self.cfg.vision_minutes}min HTTP {st}")
            except ApiError as exc:
                ok_all = False
                results.append(f"mute 失败: {exc.code} {exc.message}")
        if self.cfg.vision_notice:
            try:
                self.api.send_message(group, self.cfg.vision_notice, msg_id=message_id)
                results.append("notice")
            except ApiError as exc:
                results.append(f"notice 失败: {exc.code}")

        result = f"{verdict.get('category') or '违规'}: " + "; ".join(results)
        self.store.log_disposal(None, group, member, message_id, f"vision-{action}", ok_all, result)
        self.store.log_action("vision", group, member, summary[:90], ok_all, result)
