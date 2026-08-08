from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "logs")
LOG_DIR = os.path.abspath(LOG_DIR)
LOG_FILE = os.path.join(LOG_DIR, "api.log")


def _ensure_log_dir() -> None:
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception:
        pass


def log_api(event: str, detail: Optional[str] = None, extra: Optional[dict[str, Any]] = None) -> None:
    _ensure_log_dir()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {event}"
    if detail:
        line += f" | {detail}"
    if extra:
        try:
            # simple key=value join to avoid JSON overhead
            kv = " ".join(f"{k}={v}" for k, v in extra.items())
            line += f" | {kv}"
        except Exception:
            pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        # avoid raising from logging
        pass
