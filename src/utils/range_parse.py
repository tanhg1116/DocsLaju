from __future__ import annotations

from typing import Iterable, List
import re


def parse_page_range(text: str, total_pages: int | None = None) -> List[int]:
    """Parse a range string like "1-3,5,10-12" into a sorted unique list of ints.

    Accepts "all" to mean full range if total_pages provided.
    """
    text = (text or "").strip().lower()
    if text in ("all", "*"):
        if not total_pages:
            raise ValueError("Total pages required for 'all'")
        return list(range(1, total_pages + 1))

    pages: set[int] = set()
    range_re = re.compile(r"^(\d+)\s*-\s*(\d+)$")
    for part in text.split(','):
        part = part.strip()
        if not part:
            continue
        m = range_re.match(part)
        if m:
            start = int(m.group(1))
            end = int(m.group(2))
            if start > end:
                start, end = end, start
            pages.update(range(start, end + 1))
            continue
        # Fallback: single positive integer
        try:
            val = int(part)
            if val > 0:
                pages.add(val)
        except ValueError:
            # ignore malformed entries like '-1'
            continue
    result = sorted(p for p in pages if p > 0 and (not total_pages or p <= total_pages))
    if not result:
        raise ValueError("No valid pages selected")
    return result
