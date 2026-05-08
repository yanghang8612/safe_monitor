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


def test_normalize_ofac_sdn():
    raw = RawEvent(
        source="ofac_sdn",
        source_kind="api",
        external_id="123:0xabc",
        received_at=datetime.now(UTC),
        raw={
            "uid": "123",
            "name": "Evil Person",
            "id_type": "Digital Currency Address - XBT",
            "address": "0xabc123",
        },
        text="OFAC sanctioned: Evil Person (Digital Currency Address - XBT) 0xabc123",
    )
    ev = Normalizer().normalize(raw)
    assert ev is not None
    assert EventCategory.h in ev.category
    assert ev.severity >= Severity.high
    assert "OFAC" in ev.title


def test_normalize_forta_passes_severity_hint():
    raw = RawEvent(
        source="forta",
        source_kind="api",
        external_id="0xhash1",
        received_at=datetime.now(UTC),
        raw={
            "hash": "0xhash1",
            "name": "Anomalous Approval",
            "description": "non-keyword text that the scorer would otherwise rate low",
            "severity": "CRITICAL",
            "chainId": 1,
            "createdAt": "2026-05-08T10:00:00Z",
            "source": {"transactionHash": "0xtx1"},
        },
    )
    ev = Normalizer().normalize(raw)
    assert ev is not None
    assert ev.severity == Severity.critical
    assert ev.url and "etherscan.io" in ev.url


def test_normalize_rekt_always_high_regardless_of_text():
    raw = RawEvent(
        source="rekt_news",
        source_kind="api",
        external_id="https://rekt.news/x",
        received_at=datetime.now(UTC),
        raw={
            "title": "Some Project - Rekt",
            "link": "https://rekt.news/some-project",
            "description": "<p>boring excerpt</p>",
        },
    )
    ev = Normalizer().normalize(raw)
    assert ev is not None
    assert ev.severity == Severity.high
    assert ev.url == "https://rekt.news/some-project"


def test_fingerprint_stable_same_day():
    import re

    r = _raw_tg("peckshield_tg", "Protocol X exploited loss $5M")
    a = Normalizer().normalize(r)
    b = Normalizer().normalize(r)
    assert a.fingerprint == b.fingerprint
    assert re.fullmatch(r"[0-9a-f]{64}", a.fingerprint)
