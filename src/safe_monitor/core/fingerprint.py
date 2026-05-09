from __future__ import annotations

import hashlib
import re
from datetime import datetime

_ws = re.compile(r"\s+")


def compute(
    source: str,
    title: str,
    received_at: datetime,
    *,
    key: str | None = None,
) -> str:
    # `key` is a stable unique identifier (e.g. canonical URL with tweet id).
    # When provided it replaces the title-derived discriminator, so sources
    # whose title is intentionally non-unique (X tweets use `@handle` as title)
    # still produce per-item fingerprints instead of one-per-handle-per-day.
    head = key if key else _ws.sub(" ", title.strip().lower())[:100]
    day = received_at.strftime("%Y%m%d")
    return hashlib.sha256(f"{source}|{head}|{day}".encode()).hexdigest()
