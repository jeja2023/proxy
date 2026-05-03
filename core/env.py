"""项目级 .env 加载：将指定目录下 `.env` 写入 os.environ（不覆盖已有环境变量）。"""

from __future__ import annotations

import os
from pathlib import Path


def load_repo_dotenv(root: Path) -> None:
    """将 root 目录下的 `.env` 写入 os.environ（不覆盖已有环境变量，与多数 dotenv 行为一致）。"""
    path = root / ".env"
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, rest = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        val = rest.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        os.environ[key] = val
