"""config.toml 定点修改：整段批量替换 + 原子落盘 + 串行化。

为什么必须原子写：
    面板保存是「读文件 → 改内存 → 写回」。两个请求同时保存时，一个请求可能
    读到另一个请求刚被 open('w') 截断的半截文件，再把残缺内容写回去，
    配置文件就永久损坏（本次线上故障的真实原因）。
    因此这里统一：临时文件 + os.replace 原子替换，并用进程内锁串行化。
"""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

_LOCK = threading.Lock()


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _atomic_write(path: Path, text: str):
    fd, tmp = tempfile.mkstemp(prefix=".cfg-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def patch_many(path: str | Path, section: str, items: dict) -> bool:
    """一次性把 [section] 下的多个 key 写入，只读一次、只写一次。"""
    p = Path(path)
    with _LOCK:
        lines = p.read_text(encoding="utf-8").splitlines()
        pending = dict(items)
        out: list[str] = []
        in_section = False
        section_seen = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                if in_section:
                    for k, v in pending.items():
                        out.append(f"{k} = {_toml_value(v)}")
                    pending.clear()
                in_section = stripped[1:-1].strip() == section
                section_seen = section_seen or in_section
                out.append(line)
                continue
            if in_section and "=" in line:
                key = line.split("=", 1)[0].strip()
                if key in pending:
                    out.append(f"{key} = {_toml_value(pending.pop(key))}")
                    continue
            out.append(line)

        # 文件末尾仍在该段内：补在段尾
        if in_section:
            for k, v in pending.items():
                out.append(f"{k} = {_toml_value(v)}")
            pending.clear()
        # 整段不存在：新建
        if not section_seen:
            out += ["", f"[{section}]"]
        for k, v in pending.items():
            out.append(f"{k} = {_toml_value(v)}")

        _atomic_write(p, "\n".join(out) + "\n")
        return True


def patch(path: str | Path, section: str, key: str, value) -> bool:
    return patch_many(path, section, {key: value})
