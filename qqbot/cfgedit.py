"""config.toml 定点修改：只替换目标行，保留注释与其他内容。"""

from __future__ import annotations

import re
from pathlib import Path


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def patch(path: str | Path, section: str, key: str, value) -> bool:
    """把 [section] 下的 key 改成 value。成功返回 True。

    逐行处理，不重排、不丢注释；section 不存在时追加到文件末尾。
    """
    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    in_section = False
    section_seen = False
    done = False
    line_re = re.compile(rf"^\s*{re.escape(key)}\s*=")

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            # 离开上一个 section 时，若还没写就补在它末尾
            if in_section and not done:
                out.append(f"{key} = {_toml_value(value)}")
                done = True
            in_section = stripped[1:-1].strip() == section
            section_seen = section_seen or in_section
            out.append(line)
            continue
        if in_section and line_re.match(line):
            out.append(f"{key} = {_toml_value(value)}")
            done = True
            continue
        out.append(line)

    if in_section and not done:
        out.append(f"{key} = {_toml_value(value)}")
        done = True
    if not section_seen:
        out += ["", f"[{section}]", f"{key} = {_toml_value(value)}"]

    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    return True
