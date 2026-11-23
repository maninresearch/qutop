"""Where qutop keeps its files. Follows the XDG base directory spec on every OS."""

from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "qutop"


def default_db_path() -> Path:
    return data_dir() / "history.sqlite3"
