"""Webhook 接收端：验签 → 快速 200 → 异步落库。

设计要点：
1. 回调地址校验（op=13）：用 AppSecret 派生的 Ed25519 私钥签 event_ts + plain_token。
2. 事件推送：验 X-Signature-Ed25519 / X-Signature-Timestamp，签名原文是 timestamp + 原始 body。
3. 先入队并立即返回 200，落库在后台线程做，避免平台 3 秒超时重推。
4. 幂等靠 raw_events 主键，重推不会重复计算成员发言数以外的数据。
"""

from __future__ import annotations

import json
import queue
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import Config
from .ingest import ingest
from .sign import Signer
from .store import Store


class _Pipeline:
    """接收线程 → 队列 → 单写线程，SQLite 只有一个写者，避免锁竞争。"""

    def __init__(self, store: Store, verbose: bool = True, on_message=None):
        self.store = store
        self.verbose = verbose
        self.on_message = on_message
        self.q: queue.Queue = queue.Queue(maxsize=20000)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="writer", daemon=True)
        self._thread.start()

    def put(self, item):
        try:
            self.q.put_nowait(item)
        except queue.Full:
            # 队列满说明写入严重阻塞，丢掉最旧的，保证接收端不被拖死
            try:
                self.q.get_nowait()
                self.q.put_nowait(item)
            except queue.Empty:
                pass

    def _run(self):
        while not self._stop.is_set():
            try:
                event, data, raw, rid = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                line = ingest(self.store, event, data, raw)
            except Exception as exc:  # 单个事件失败不能拖垮写线程
                print(f"[error] ingest {event} 失败: {type(exc).__name__}: {exc}", flush=True)
                continue
            if self.verbose and line:
                print(line, flush=True)
            if self.on_message and event in ("GROUP_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE"):
                try:
                    self.on_message(event, data)
                except Exception as exc:
                    print(f"[rules] 提交失败: {type(exc).__name__}: {exc}", flush=True)

    def stop(self):
        self._stop.set()


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, cfg: Config, signer: "Signer | None", pipeline: _Pipeline):
        super().__init__(addr, handler)
        self.cfg = cfg
        self.signer = signer
        self.pipeline = pipeline


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "qqbot-webhook/0.2"

    def log_message(self, fmt, *args):  # 关掉默认逐字节日志
        return

    # ---------- 工具 ----------

    def _client_ip(self) -> str:
        return (self.headers.get("X-Real-IP")
                or self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or self.client_address[0])

    def _reply(self, code: int, payload: bytes = b"{}", ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        if length > self.server.cfg.max_body:
            raise ValueError(f"body 过大: {length}")
        return self.rfile.read(length)

    # ---------- 路由 ----------

    def do_GET(self):
        if self.path.split("?")[0] == self.server.cfg.webhook_path:
            self._reply(200, b'{"ok":true,"service":"qqbot-webhook"}')
        else:
            self._reply(404, b'{"ok":false}')

    def do_POST(self):
        if self.path.split("?")[0] != self.server.cfg.webhook_path:
            self._reply(404, b'{"ok":false}')
            return

        try:
            body = self._read_body()
        except ValueError as exc:
            print(f"[warn] {self._client_ip()} body 异常: {exc}", flush=True)
            self._reply(413, b'{"ok":false}')
            return

        # op=13：平台校验回调地址，不需要验签，必须回签名
        try:
            payload = json.loads(body.decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError:
            self._reply(400, b'{"ok":false}')
            return

        if self.server.signer is None:
            self._reply(503, b'{"ok":false,"error":"setup required"}')
            return

        if payload.get("op") == 13:
            d = payload.get("d") or {}
            token_field = d.get("plain_token", "")
            event_ts = d.get("event_ts", "")
            signature = self.server.signer.sign_validation(token_field, event_ts)
            resp = json.dumps({"plain_token": token_field, "signature": signature}).encode()
            print(f"[校验] 回调地址校验通过 ip={self._client_ip()} ts={event_ts}", flush=True)
            self._reply(200, resp)
            return

        # 事件推送：验签失败直接 401，不落库
        sig = self.headers.get("X-Signature-Ed25519", "")
        ts = self.headers.get("X-Signature-Timestamp", "")
        if not self.server.signer.verify_event(sig, ts, body):
            print(f"[拒绝] 验签失败 ip={self._client_ip()} event={payload.get('t')}", flush=True)
            self._reply(401, b'{"ok":false}')
            return

        event = payload.get("t") or ""
        data = payload.get("d") or {}
        self.server.pipeline.put((event, data, body.decode("utf-8", "replace"), payload.get("id")))
        # 先回 200，落库异步
        self._reply(200, b"{}")


def serve(cfg: Config, store: Store, signer: "Signer | None"):
    from .api import QQOpenAPI
    from .auth import TokenManager
    from .panel import build_server as build_panel

    from .rules import Engine
    from .scheduler import Scheduler

    tokens = TokenManager(cfg.app_id, cfg.app_secret, cfg.timeout)
    api = QQOpenAPI(cfg, tokens)

    engine = Engine(cfg, store, api)

    def _on_message(event: str, data: dict):
        engine.submit(data.get("group_openid") or "",
                      ((data.get("author") or {}).get("member_openid")
                       or (data.get("author") or {}).get("id") or ""),
                      data.get("id") or "",
                      data.get("content") or "")

    pipeline = _Pipeline(store, verbose=cfg.log_events,
                        on_message=_on_message if cfg.has_credentials() else None)
    httpd = _Server((cfg.listen_host, cfg.listen_port), _Handler, cfg, signer, pipeline)
    scheduler = None
    if cfg.has_credentials():
        engine.start()
        scheduler = Scheduler(cfg, store, api)
        scheduler.start()

    panel_httpd = None
    if cfg.panel_enabled:
        panel_httpd = build_panel(cfg, store, tokens, api)
        threading.Thread(target=panel_httpd.serve_forever, kwargs={"poll_interval": 0.5},
                         daemon=True).start()
        print(f"[panel] 管理面板 http://{cfg.panel_host}:{cfg.panel_port}/panel/", flush=True)

    def _shutdown(signum, frame):
        print(f"\n[serve] 收到信号 {signum}，停止监听", flush=True)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    print(f"[serve] 监听 http://{cfg.listen_host}:{cfg.listen_port}{cfg.webhook_path}", flush=True)
    print(f"[serve] 环境={'沙箱' if cfg.sandbox else '正式'} db={cfg.db_path}", flush=True)
    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        if engine:
            engine.stop()
        if scheduler:
            scheduler.stop()
        pipeline.stop()
        httpd.server_close()
        if panel_httpd:
            panel_httpd.shutdown()
            panel_httpd.server_close()
        print("[serve] 已退出", flush=True)


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def selftest(cfg: Config, store: Store, signer: Signer) -> list[tuple[str, bool, str]]:
    """本机自测：起临时端口，模拟平台校验、合法事件、篡改签名三种请求。"""
    import threading
    import urllib.error
    import urllib.request
    from datetime import datetime, timezone, timedelta

    probe = {"group_openid": "SELFTEST_GROUP", "id": f"selftest-{int(time.time())}",
             "content": "自测消息",
             "timestamp": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
             "author": {"member_openid": "SELFTEST_MEMBER", "username": "selftest"}}

    pipeline = _Pipeline(store, verbose=False)
    httpd = _Server((cfg.listen_host, 0), _Handler, cfg, signer, pipeline)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()
    url = f"http://{cfg.listen_host}:{port}{cfg.webhook_path}"
    results: list[tuple[str, bool, str]] = []

    def post(body: bytes, headers: dict):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    try:
        token_field, ts = "PLAINTOKEN123", str(int(time.time()))

        body = json.dumps({"op": 13, "d": {"plain_token": token_field, "event_ts": ts}}).encode()
        status, text = post(body, {"Content-Type": "application/json"})
        expect = signer.sign_validation(token_field, ts)
        ok = status == 200 and json.loads(text).get("signature") == expect
        results.append(("回调地址校验", ok, f"{status} sig={text[:48]}"))

        raw = json.dumps({"op": 0, "id": "evt1", "t": "GROUP_AT_MESSAGE_CREATE", "d": probe},
                         ensure_ascii=False).encode()
        status, text = post(raw, {"Content-Type": "application/json",
                                  "X-Signature-Ed25519": signer.sign_event(ts, raw),
                                  "X-Signature-Timestamp": ts})
        results.append(("合法签名事件", status == 200, f"{status} {text[:32]}"))

        status, text = post(raw, {"Content-Type": "application/json",
                                  "X-Signature-Ed25519": "00" * 64,
                                  "X-Signature-Timestamp": ts})
        results.append(("篡改签名拦截", status == 401, f"{status} {text[:32]}"))

        deadline = time.time() + 3
        row = None
        while time.time() < deadline:
            row = store.last_message("SELFTEST_GROUP")
            if row:
                break
            time.sleep(0.1)
        results.append(("事件落库", bool(row and row["content"] == "自测消息"),
                        str(row["message_id"]) if row else "无记录"))
    finally:
        httpd.shutdown()
        httpd.server_close()
        pipeline.stop()
    return results
