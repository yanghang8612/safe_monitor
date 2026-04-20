from __future__ import annotations

import json
from typing import Optional

from safe_monitor.core.fingerprint import compute as compute_fp
from safe_monitor.core.models import Event, RawEvent, Severity
from safe_monitor.core.parsers.defillama import parse_defillama
from safe_monitor.core.parsers.generic_tg import parse_generic_tg
from safe_monitor.core.severity import score as score_severity


class Normalizer:
    def normalize(self, raw: RawEvent) -> Optional[Event]:
        parsed: dict
        if raw.source == "defillama_api":
            parsed = parse_defillama(raw.raw)
        elif raw.source_kind == "tg":
            parsed = parse_generic_tg(raw.text or json.dumps(raw.raw))
        else:
            return None

        title: str = parsed.get("title") or "(no title)"
        body = parsed.get("body")
        url = parsed.get("url") or raw.url
        loss_usd = parsed.get("loss_usd")
        category = parsed.get("category") or []

        sev = score_severity(f"{title} {body or ''}", loss_usd)

        fp = compute_fp(raw.source, title, raw.received_at)

        return Event(
            fingerprint=fp,
            source=raw.source,
            title=title,
            body=body,
            url=url,
            severity=sev,
            category=category,
            chain=parsed.get("chain"),
            tx_hash=parsed.get("tx_hash"),
            attacker_addr=parsed.get("attacker_addr"),
            loss_usd=loss_usd,
            occurred_at=parsed.get("occurred_at") or raw.occurred_at,
            received_at=raw.received_at,
            raw=raw.raw,
        )
