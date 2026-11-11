"""群聊对话引擎：@ 触发，被动回复，带上限与冷却。

触发策略（config [llm] trigger）：
    at       仅 @机器人 才回（默认，最安全）
    all      群里每条消息都回（配合冷却，容易刷屏，慎用）
另外 keywords 非空时，命中关键词也会触发。

约束：
1. 回复走被动消息（带原 msg_id），落在平台 5 分钟 / 5 次窗口内。
2. 每群冷却 llm_cooldown 秒，每群每天上限 llm_daily_limit 次。
3. 每群只带最近 llm_context_turns 轮上下文，控制 token。
4. 回复超过 400 字截断，避免刷屏。
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time

from .api import ApiError, QQOpenAPI
from .config import Config
from .llm import LLMClient, LLMError
from .vision import fetch_image, image_block
from . import imagegen, imagepick
from .store import Store

AT_TAG = re.compile(r"<@!?[0-9A-Za-z_\-]+>")
LEADING_AT = re.compile(r"^\s*@\S+\s*")
MAX_REPLY_CHARS = 400


class ChatEngine:
    def __init__(self, cfg: Config, store: Store, api: QQOpenAPI):
        self.cfg = cfg
        self.store = store
        self.api = api
        self.q: queue.Queue = queue.Queue(maxsize=2000)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="chat", daemon=True)
        self._last_reply: dict[str, float] = {}
        self._seen: dict[str, float] = {}

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    # ---------------- 入队 ----------------

    def submit(self, event: str, data: dict):
        if not self.cfg.llm_enabled or not self.cfg.llm_api_key:
            return
        group = data.get("group_openid") or ""
        message_id = data.get("id") or ""
        content = (data.get("content") or "").strip()
        author = data.get("author") or {}
        if not group or not message_id:
            return
        if author.get("bot"):
            return
        images = [a for a in (data.get("attachments") or [])
                  if str(a.get("content_type", "")).startswith("image/") and a.get("url")]
        # 没文字也没图就不处理；只有图时允许走识别
        if not content and not (images and self.cfg.vision_chat_enabled):
            return
        try:
            self.q.put_nowait((event, data))
        except queue.Full:
            pass

    # ---------------- 触发判定 ----------------

    def _is_at_me(self, event: str, data: dict) -> bool:
        if event == "GROUP_AT_MESSAGE_CREATE":
            return True
        mentions = data.get("mentions") or []
        return any(m.get("bot") for m in mentions if isinstance(m, dict))

    def _handled_by_reply_rule(self, group: str, content: str) -> bool:
        """回复类规则优先：避免同一条消息既被规则回又被模型回。"""
        from .rules import Engine
        if not self.store:
            return False
        for row in self.store.list_rules(enabled_only=True):
            rule = dict(row)
            if rule.get("action") not in ("reply", "recall_reply"):
                continue
            try:
                scope = json.loads(rule.get("groups") or "[]")
            except Exception:
                scope = []
            if scope and group not in scope:
                continue
            params = {}
            try:
                params = json.loads(rule.get("params") or "{}")
            except Exception:
                params = {}
            if not (params.get("reply") or "").strip():
                continue
            if Engine.matches(rule, content):
                return True
        return False

    def should_reply(self, event: str, data: dict) -> bool:
        content = AT_TAG.sub("", data.get("content") or "")
        content = LEADING_AT.sub("", content).strip()
        data["_clean"] = content
        if not content and not (data.get("attachments") and self.cfg.vision_chat_enabled):
            return False
        if self._handled_by_reply_rule(data.get("group_openid") or "", content):
            return False
        if any(k and k in content for k in (self.cfg.llm_keywords or [])):
            return True
        if self.cfg.llm_trigger == "all":
            return True
        return self._is_at_me(event, data)

    # ---------------- 主循环 ----------------

    def _generate_image(self, group: str, member: str, message_id: str, prompt: str):
        from .imagegen import ImageGenClient, GenError

        cfg = self.cfg
        text = (cfg.imagegen_prefix + prompt).strip()
        client = ImageGenClient(cfg.imagegen_base_url, cfg.imagegen_api_key,
                                cfg.imagegen_model, cfg.imagegen_size)
        try:
            url, blob, kind = client.generate(text)
        except GenError as exc:
            print(f"[gen] 生成失败: {exc}", flush=True)
            self.store.log_action("imagegen", group, member, prompt[:60], False, str(exc)[:150])
            try:
                self.api.send_message(group, f"画图失败：{exc.message[:60]}", msg_id=message_id)
            except ApiError:
                pass
            return

        try:
            if url:
                st, _ = self.api.send_image(group, url, msg_id=message_id)
            else:
                up_st, up = self.api.upload_image_bytes(group, blob or b"", "gen.png")
                info = (up or {}).get("file_info")
                if not info:
                    raise ApiError(up_st, None, "上传无 file_info")
                st, _ = self.api.request("POST", f"/v2/groups/{group}/messages", {
                    "msg_type": 7, "media": {"file_info": info},
                    "msg_id": message_id, "msg_seq": 1})
        except ApiError as exc:
            print(f"[gen] 发送失败: {exc.code} {exc.message}", flush=True)
            self.store.log_action("imagegen", group, member, prompt[:60], False, f"send {exc.code}")
            return

        self._last_reply[group] = time.time()
        self.store.log_action("imagegen", group, member, prompt[:60], True,
                              f"HTTP {st} {kind} {len(blob or b'')}B")
        print(f"[gen] 已生成并发送 群={group[:8]}… {prompt[:30]}", flush=True)

    def _send_found_image(self, group: str, member: str, message_id: str, query: str):
        cand = imagepick.pick(query, self.cfg.image_max_bytes)
        if not cand:
            try:
                self.api.send_message(group, "没找到合适的图，换个说法试试。", msg_id=message_id)
            except ApiError:
                pass
            self.store.log_action("image", group, member, query, False, "未找到可用图片")
            return

        st = None
        first_err = ""
        try:
            st, _ = self.api.send_image(group, cand["url"], msg_id=message_id)
        except ApiError as exc:
            first_err = f"{exc.code} {exc.message}"
            # URL 转存失败（850026 等）时，退回二进制上传
            try:
                blob, mime = fetch_image(cand["url"], self.cfg.image_max_bytes)
                ext = "png" if "png" in mime else "jpg"
                up_st, up = self.api.upload_image_bytes(group, blob, f"image.{ext}")
                info = (up or {}).get("file_info")
                if not info:
                    raise ApiError(up_st, None, "二进制上传无 file_info")
                st, _ = self.api.request("POST", f"/v2/groups/{group}/messages", {
                    "msg_type": 7, "media": {"file_info": info},
                    "msg_id": message_id, "msg_seq": 1})
                print(f"[chat] 转存失败({exc.code})，已改用二进制上传", flush=True)
            except Exception as exc2:
                print(f"[chat] 发图失败: 转存={first_err} / 回退={exc2}", flush=True)
                self.store.log_action("image", group, member, query, False,
                                      f"{first_err} | {exc2}"[:150])
                try:
                    self.api.send_message(group, "这张图没能发出去，换一句再试试。",
                                          msg_id=message_id)
                except ApiError:
                    pass
                return

        self._last_reply[group] = time.time()
        self.store.log_action("image", group, member, query, True,
                              f"HTTP {st} {cand.get('width')}x{cand.get('height')} {cand.get('bytes')}B")
        print(f"[chat] 已发图 群={group[:8]}… {query} {cand.get('bytes')}B", flush=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                event, data = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._handle(event, data)
            except Exception as exc:
                print(f"[chat] 处理失败: {type(exc).__name__}: {exc}", flush=True)

    def _handle(self, event: str, data: dict):
        if not self.should_reply(event, data):
            return
        group = data["group_openid"]
        message_id = data["id"]
        member = (data.get("author") or {}).get("member_openid") or ""
        question = data["_clean"]

        # 去重：平台重推不重复回复
        now = time.time()
        if len(self._seen) > 2000:
            self._seen = {k: v for k, v in self._seen.items() if now - v < 600}
        if message_id in self._seen:
            return
        self._seen[message_id] = now

        # 冷却
        if now - self._last_reply.get(group, 0) < self.cfg.llm_cooldown:
            return

        # 1) 文生图：说了「画一张…」就走生成，不走对话模型
        if self.cfg.imagegen_enabled:
            prompt = imagegen.detect(question)
            if prompt:
                self._generate_image(group, member, message_id, prompt)
                return

        # 2) 找图意图，直接发图，不走模型
        if self.cfg.image_search_enabled:
            info = imagepick.classify(question, self.cfg.image_categories)
            if info and not info["allowed"]:
                enabled = "、".join(self.cfg.image_categories) or "（未启用任何类别）"
                try:
                    self.api.send_message(
                        group, f"「{info['key']}」这类图没开。当前能发：{enabled}。", msg_id=message_id)
                except ApiError:
                    pass
                self.store.log_action("image", group, member, question[:60], False,
                                      f"类别未放行: {info['key']}")
                return
            if info:
                self._send_found_image(group, member, message_id, info["query"])
                return
            # 说了要图但没指定类别 → 从已启用类别里随机
            if imagepick.intent(question):
                if not self.cfg.image_categories:
                    try:
                        self.api.send_message(group, "图片功能还没启用任何类别，请先在面板勾选。",
                                              msg_id=message_id)
                    except ApiError:
                        pass
                    return
                pick = imagepick.random_choice(self.cfg.image_categories)
                if pick:
                    self._send_found_image(group, member, message_id, pick["query"])
                    return
        # 日限额
        if self.store.chat_calls_today(group) >= self.cfg.llm_daily_limit:
            print(f"[chat] 群 {group[:8]}… 达每日上限", flush=True)
            return

        client = LLMClient(self.cfg.llm_base_url, self.cfg.llm_api_key, self.cfg.llm_model,
                           max_tokens=self.cfg.llm_max_tokens,
                           temperature=self.cfg.llm_temperature)
        system = self.cfg.llm_system
        if self.cfg.imagegen_enabled:
            system += ("\n你能画图：用户说「画一张…」「做一张图…」「生成一张…」时系统会自动出图。"
                       "如果用户只是描述想要什么图，提醒他用「画一张 + 描述」的句式。不要说自己做不了图。")
        if self.cfg.image_search_enabled:
            cats = "、".join(self.cfg.image_categories) or "无"
            system += (f"\n你可以发图片：当用户要图片时，系统会自动找图发送。"
                       f"当前支持的图片类别：{cats}。用户要的类别不在其中时，如实说明支持范围，不要说自己发不了图片。")
        messages = [{"role": "system", "content": system}]
        for row in self.store.chat_context(group, self.cfg.llm_context_turns):
            messages.append({"role": row["role"], "content": row["content"]})
        images = [a for a in (data.get("attachments") or [])
                  if str(a.get("content_type", "")).startswith("image/") and a.get("url")]
        if images and self.cfg.vision_chat_enabled:
            try:
                blob, mime = fetch_image(images[0]["url"], self.cfg.vision_max_bytes)
                messages.append({"role": "user", "content": [
                    {"type": "text", "text": question or "看看这张图，说说图上是什么"},
                    image_block(blob, mime),
                ]})
            except Exception as exc:
                print(f"[chat] 图片读取失败，改为纯文本: {exc}", flush=True)
                messages.append({"role": "user", "content": question or "（对方发了一张图片，但你未能读取）"})
        else:
            messages.append({"role": "user", "content": question})

        try:
            answer, tokens = client.chat(messages)
        except LLMError as exc:
            print(f"[chat] 模型调用失败: {exc}", flush=True)
            self.store.log_action("chat", group, member, question[:120], False, str(exc))
            return
        if not answer:
            return
        answer = answer[:MAX_REPLY_CHARS]

        st = None
        try:
            if self.cfg.reply_at_user and member:
                st, _ = self.api.send_message_at(group, answer, member, msg_id=message_id)
            else:
                st, _ = self.api.send_message(group, answer, msg_id=message_id)
        except ApiError as exc:
            # markdown 失败时退回纯文本，至少把话说完
            if self.cfg.reply_at_user and member:
                try:
                    st, _ = self.api.send_message(group, answer, msg_id=message_id)
                    print(f"[chat] @ 失败({exc.code})，已退回纯文本", flush=True)
                except ApiError as exc2:
                    print(f"[chat] 回复失败: {exc2.code} {exc2.message}", flush=True)
                    self.store.log_action("chat", group, member, question[:120], False,
                                          f"reply {exc2.code}")
                    return
            else:
                # 常见：40034005 msg_id 过期 / 40034128 被动回复超限
                print(f"[chat] 回复失败: {exc.code} {exc.message}", flush=True)
                self.store.log_action("chat", group, member, question[:120], False,
                                      f"reply {exc.code}")
                return

        self._last_reply[group] = time.time()
        calls = self.store.bump_chat_usage(group, tokens)
        self.store.add_chat(group, member, "user", question)
        self.store.add_chat(group, member, "assistant", answer)
        self.store.log_action("chat", group, member, question[:120], True,
                              f"HTTP {st} tokens={tokens} today={calls}")
        print(f"[chat] 已回复 群={group[:8]}… tokens={tokens} 第 {calls} 次", flush=True)
