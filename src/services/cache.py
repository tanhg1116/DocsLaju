from __future__ import annotations

from typing import Dict, Tuple
from threading import RLock

_MEMO_FALLBACK: Dict[str, Dict[Tuple[str, str, int], str]] = {"ocr": {}}
_LOCK = RLock()

# Cache pure data results. Key by composite key to isolate per session/file/page.

def get_cached_markdown(key: Tuple[str, str, int]) -> str | None:
    return read_memo_markdown(key)

# Store markdown in st.session_state["_memo_cache"] dict
def set_cached_markdown(key: Tuple[str, str, int], value: str) -> None:
    with _LOCK:
        bucket = _MEMO_FALLBACK.setdefault("ocr", {})
        bucket[key] = value


# Retrieve cached markdown: Check session_state first, then fallback dict
def read_memo_markdown(key: Tuple[str, str, int]) -> str | None:
    with _LOCK:
        return _MEMO_FALLBACK.get("ocr", {}).get(key)


# Delete cache entry (used when Re-OCR clicked)
def invalidate_markdown(key: Tuple[str, str, int]) -> None:
    with _LOCK:
        bucket = _MEMO_FALLBACK.setdefault("ocr", {})
        bucket.pop(key, None)
