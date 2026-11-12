"""图片归档：把图片上传到图床，返回可长期使用的直链。

图床按 Lsky Pro 兼容协议实现：
    POST {base}/upload   header: X-API-TOKEN    multipart 字段 file
    返回 data.url / data.id / data.reused

本地按 sha256 去重，重复图片直接复用已记录的直链，不重复上传。
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import urllib.error
import urllib.request
import uuid


class SinkError(RuntimeError):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"HTTP {status} {message}")


class ImageSink:
    def __init__(self, base_url: str, token: str, enabled: bool = True, timeout: int = 30):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.enabled = enabled
        self.timeout = timeout

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

        req = urllib.request.Request(
            self.base_url + "/upload", data=body, method="POST",
            headers={"X-API-TOKEN": self.token,
                     "Content-Type": f"multipart/form-data; boundary={boundary}",
                     "User-Agent": "qqbot/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                msg = json.loads(detail).get("message") or detail[:160]
            except Exception:
                msg = detail[:160]
            raise SinkError(exc.code, msg) from None
        except urllib.error.URLError as exc:
            raise SinkError(0, f"网络错误: {exc.reason}") from None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise SinkError(0, f"返回非 JSON: {raw[:160]}") from None
        if not data.get("status") or not (data.get("data") or {}).get("url"):
            raise SinkError(int(data.get("code") or 0), str(data.get("message") or "上传失败"))
        return data["data"]

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
            print(f"[sink] 上传失败: {exc}", flush=True)
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
                     enabled=cfg.storage_enabled, timeout=cfg.storage_timeout)
