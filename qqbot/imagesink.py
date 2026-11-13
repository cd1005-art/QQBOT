"""图片归档：把图片上传到图床，返回可长期使用的直链。

图床按 Lsky Pro 兼容协议实现：
    POST {base}/upload   header: X-API-TOKEN    multipart 字段 file
    返回 data.url / data.id / data.reused

本地按 sha256 去重，重复图片直接复用已记录的直链，不重复上传。
"""

from __future__ import annotations

import hashlib
import http.client
import json
import mimetypes
import socket
import ssl
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit


def _clean_error(text: str) -> str:
    """图床出错时可能返回整页 HTML，压成一行可读信息。"""
    t = (text or "").strip()
    if t.startswith("<"):
        import re as _re
        m = _re.search(r"<h1[^>]*>(.*?)</h1>", t, _re.S) or _re.search(r"<title>(.*?)</title>", t, _re.S)
        return m.group(1).strip()[:120] if m else "服务端返回 HTML 错误页"
    try:
        return str(json.loads(t).get("message") or t[:120])
    except Exception:
        return t[:120]


class SinkError(RuntimeError):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"HTTP {status} {message}")


class ImageSink:
    def __init__(self, base_url: str, token: str, enabled: bool = True, timeout: int = 30,
                 resolve_ip: str = ""):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.enabled = enabled
        self.timeout = timeout
        # 直连源站 IP，但证书仍按域名校验（CDN 下发链不完整时使用）
        self.resolve_ip = resolve_ip or ""

    @property
    def ok(self) -> bool:
        return bool(self.enabled and self.base_url and self.token)

    def upload(self, blob: bytes, filename: str = "", mime: str = "") -> dict:
        if not self.ok:
            raise SinkError(0, "未启用或未配置图床")
        if not blob:
            raise SinkError(0, "空文件")
        if not filename:
            ext = (mimetypes.guess_extension(mime) or ".jpg").lstrip(".")
            filename = f"qqbot_{uuid.uuid4().hex[:12]}.{ext}"
        mime = mime or mimetypes.guess_type(filename)[0] or "image/jpeg"

        boundary = "----qqbot" + uuid.uuid4().hex
        head = (f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f"Content-Type: {mime}\r\n\r\n").encode()
        body = head + blob + f"\r\n--{boundary}--\r\n".encode()

        url = self.base_url + "/upload"
        headers = {"X-API-TOKEN": self.token,
                   "Content-Type": f"multipart/form-data; boundary={boundary}",
                   "User-Agent": "qqbot/1.0"}

        if self.resolve_ip:
            raw = self._post_pinned(url, headers, body)
        else:
            raw = self._post(url, headers, body)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise SinkError(0, f"返回非 JSON: {raw[:160]}") from None
        if not data.get("status") or not (data.get("data") or {}).get("url"):
            raise SinkError(int(data.get("code") or 0), str(data.get("message") or "上传失败"))
        return data["data"]

    def _post(self, url: str, headers: dict, body: bytes) -> str:
        req = urllib.request.Request(url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise SinkError(exc.code, _clean_error(exc.read().decode("utf-8", "replace"))) from None
        except urllib.error.URLError as exc:
            raise SinkError(0, f"网络错误: {exc.reason}") from None

    def _post_pinned(self, url: str, headers: dict, body: bytes) -> str:
        """连指定 IP，SNI 与被校验证书仍用原域名。"""
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query

        ctx = ssl.create_default_context()
        sock = socket.create_connection((self.resolve_ip, port), timeout=self.timeout)
        try:
            tls = ctx.wrap_socket(sock, server_hostname=host)
            conn = http.client.HTTPSConnection(host, port, timeout=self.timeout)
            conn.sock = tls  # 复用已握手的连接
            hdrs = dict(headers, Host=host, Connection="close")
            conn.request("POST", path, body=body, headers=hdrs)
            resp = conn.getresponse()
            text = resp.read().decode("utf-8", "replace")
            if resp.status >= 400:
                raise SinkError(resp.status, _clean_error(text))
            return text
        except SinkError:
            raise
        except ssl.SSLError as exc:
            raise SinkError(0, f"TLS 校验失败: {exc}") from None
        except OSError as exc:
            raise SinkError(0, f"网络错误: {exc}") from None
        finally:
            try:
                sock.close()
            except Exception:
                pass

    # ---------------- 带本地去重的归档 ----------------

    def archive(self, store, blob: bytes, *, source: str, group: str = "",
                member: str = "", mime: str = "", filename: str = "") -> str | None:
        """上传并入库，返回直链。重复图片直接复用旧链接；失败返回 None。"""
        if not self.ok or not blob:
            return None
        digest = hashlib.sha256(blob).hexdigest()
        try:
            hit = store.find_image_by_sha(digest)
        except Exception:
            hit = None
        if hit:
            return hit["url"]

        try:
            data = self.upload(blob, filename=filename, mime=mime)
        except SinkError as exc:
            hint = ""
            msg = str(exc)
            if any(k in msg for k in ("CERTIFICATE", "certificate", "TLS", "SSL")):
                hint = "（证书链异常：可临时在 [storage] 配 resolve_ip 直连源站，或修图床证书链）"
            print(f"[sink] 上传失败: {exc}{hint}", flush=True)
            return None

        url = data.get("url")
        try:
            store.add_image(digest, url, int(data.get("size") or len(blob)),
                            int(data.get("width") or 0), int(data.get("height") or 0),
                            source, group, member, str(data.get("id") or ""),
                            1 if data.get("reused") else 0)
        except Exception as exc:
            print(f"[sink] 入库失败: {exc}", flush=True)
        return url


def from_config(cfg) -> ImageSink:
    return ImageSink(cfg.storage_base_url, cfg.storage_token,
                     enabled=cfg.storage_enabled, timeout=cfg.storage_timeout,
                     resolve_ip=getattr(cfg, "storage_resolve_ip", "") or "")
