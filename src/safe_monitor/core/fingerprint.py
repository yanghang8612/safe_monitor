from __future__ import annotations

import hashlib
import re
from datetime import datetime

_ws = re.compile(r"\s+")


def compute(source: str, title: str, received_at: datetime) -> str:
    head = _ws.sub(" ", title.strip().lower())[:100]
    day = received_at.strftime("%Y%m%d")
    return hashlib.sha256(f"{source}|{head}|{day}".encode()).hexdigest()
