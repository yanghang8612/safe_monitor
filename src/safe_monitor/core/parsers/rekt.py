from __future__ import annotations

import html
import re
from typing import Any

from safe_monitor.core.models import Severity

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return html.unescape(_TAG_RE.sub("", s or "")).strip()


def _categorize(text: str) -> list[str]:
    # Rekt.news is exclusively post-mortems of exploits, so 'a' is the
    # default base category. Sub-flavors are additive tags.
    blob = text.lower()
    out: list[str] = ["a"]
    if "bridge" in blob or "cross-chain" in blob:
        out.append("b")
    if any(w in blob for w in ("phishing", "drainer", "approval")):
        out.append("e")
    if any(w in blob for w in ("rugpull", "rug pull", "honeypot")):
        out.append("g")
    if "stablecoin" in blob or "depeg" in blob:
        out.append("f")
    return out


def parse_rekt(raw: dict[str, Any]) -> dict[str, Any]:
    title = (raw.get("title") or "").strip() or "(rekt.news)"
    link = (raw.get("link") or "").strip() or None
    body_html = raw.get("description") or ""
    body = _strip_html(body_html) or title

    return {
        "title": title,
        "body": body,
        "url": link,
        "category": _categorize(f"{title} {body}"),
        # Rekt.news only ships post-mortems of confirmed incidents — every
        # entry is by definition severity=high. Bypass the keyword scorer.
        "severity": Severity.high,
    }
