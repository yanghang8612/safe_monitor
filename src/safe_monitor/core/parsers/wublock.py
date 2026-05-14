from __future__ import annotations

import html
import re
from typing import Any

from safe_monitor.core.parsers.generic_tg import parse_generic_tg

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return html.unescape(_TAG_RE.sub("", s or "")).strip()


def parse_wublock(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse a wublock123 (吴说) Atom feed entry.

    Unlike parse_rekt — which forces severity=high because every rekt.news
    post is a confirmed incident — wublock123 carries all-category crypto
    news. We deliberately omit a `severity` hint so the normalizer's
    keyword scorer runs: funding/market items score 'low' and get dropped
    by min_severity=medium, leaving only security-relevant entries.

    tx/address/loss/chain/category extraction is delegated to
    parse_generic_tg, which already handles Chinese + English vocabulary.
    """
    title = (raw.get("title") or "").strip() or "(吴说)"
    link = (raw.get("link") or "").strip() or None
    body = _strip_html(raw.get("description") or "") or title

    extracted = parse_generic_tg(body)

    return {
        "title": title,
        "body": body,
        "url": link,
        "tx_hash": extracted.get("tx_hash"),
        "attacker_addr": extracted.get("attacker_addr"),
        "loss_usd": extracted.get("loss_usd"),
        "chain": extracted.get("chain"),
        "category": extracted.get("category") or [],
    }
