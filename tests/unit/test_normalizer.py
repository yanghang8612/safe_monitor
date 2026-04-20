import json
from datetime import UTC, datetime
from pathlib import Path

from safe_monitor.core.models import EventCategory, RawEvent, Severity
from safe_monitor.core.normalizer import Normalizer


def _raw_api(payload: dict) -> RawEvent:
    return RawEvent(
        source="defillama_api",
        source_kind="api",
        external_id=payload.get("name", "x"),
        received_at=datetime.now(UTC),
        raw=payload,
    )


def _raw_tg(source: str, text: str) -> RawEvent:
    return RawEvent(
        source=source,
        source_kind="tg",
        external_id="42",
        received_at=datetime.now(UTC),
        raw={"text": text},
        text=text,
    )


def test_normalize_defillama_exploit():
    data = json.loads(Path("tests/fixtures/defillama_hack_item.json").read_text())
    ev = Normalizer().normalize(_raw_api(data))
    assert ev is not None
    assert "Example Bridge" in ev.title
    assert ev.loss_usd == 12_000_000
    assert ev.chain == "Ethereum"
    assert ev.severity >= Severity.high
    assert EventCategory.a in ev.category


def test_normalize_generic_tg_hack_keyword():
    ev = Normalizer().normalize(
        _raw_tg(
            "peckshield_tg",
            "@ProtocolX has been exploited. Loss ~$5,000,000. Tx: 0x" + "a" * 64,
        )
    )
    assert ev is not None
    assert ev.severity >= Severity.high
    assert ev.tx_hash and ev.tx_hash.startswith("0x")


def test_normalize_generic_tg_drops_noise():
    ev = Normalizer().normalize(_raw_tg("whale_alert_tg", "nothing interesting here"))
    # Still returns an Event (low severity); Filter will drop it
    assert ev is not None
    assert ev.severity == Severity.low


def test_fingerprint_stable_same_day():
    import re

    r = _raw_tg("peckshield_tg", "Protocol X exploited loss $5M")
    a = Normalizer().normalize(r)
    b = Normalizer().normalize(r)
    assert a.fingerprint == b.fingerprint
    assert re.fullmatch(r"[0-9a-f]{64}", a.fingerprint)
