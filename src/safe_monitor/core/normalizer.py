from __future__ import annotations

import json

from safe_monitor.core.fingerprint import compute as compute_fp
from safe_monitor.core.models import Event, RawEvent
from safe_monitor.core.parsers.defillama import parse_defillama
from safe_monitor.core.parsers.forta import parse_forta
from safe_monitor.core.parsers.generic_tg import parse_generic_tg
from safe_monitor.core.parsers.ofac import parse_ofac
from safe_monitor.core.parsers.rekt import parse_rekt
from safe_monitor.core.severity import score as score_severity


class Normalizer:
    def normalize(self, raw: RawEvent) -> Event | None:
        parsed: dict
        if raw.source == "defillama_api":
            parsed = parse_defillama(raw.raw)
        elif raw.source == "ofac_sdn":
            parsed = parse_ofac(raw.raw)
        elif raw.source == "forta":
            parsed = parse_forta(raw.raw)
        elif raw.source == "rekt_news":
            parsed = parse_rekt(raw.raw)
        elif raw.source_kind == "tg":
            text = raw.text or json.dumps(raw.raw)
            if raw.source == "peckshield_tg":
                from safe_monitor.core.parsers.peckshield import parse_peckshield

                parsed = parse_peckshield(text)
            elif raw.source == "slowmist_tg":
                from safe_monitor.core.parsers.slowmist import parse_slowmist

                parsed = parse_slowmist(text)
            elif raw.source == "whale_alert_tg":
                from safe_monitor.core.parsers.whale_alert import parse_whale_alert

                parsed = parse_whale_alert(text)
            else:
                parsed = parse_generic_tg(text)
        else:
            return None

        title: str = parsed.get("title") or "(no title)"
        body = parsed.get("body")
        url = parsed.get("url") or raw.url
        loss_usd = parsed.get("loss_usd")
        category = parsed.get("category") or []

        # Structured sources (Forta, Rekt) ship a severity hint so we don't
        # try to keyword-match their bot-generated descriptions.
        sev = parsed.get("severity") or score_severity(f"{title} {body or ''}", loss_usd)

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
