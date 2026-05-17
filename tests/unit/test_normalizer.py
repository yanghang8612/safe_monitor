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


def test_x_tweets_same_handle_same_day_get_distinct_fingerprints():
    """Regression: x_tweet parser puts only `@handle` in title, so the old
    `(source, title, day)` fingerprint collapsed every same-day tweet from one
    account into a single fingerprint — silently dedup'ing all but the first.
    With the canonical URL (tweet id) as fingerprint key, distinct tweets from
    one handle on the same day must produce distinct fingerprints."""
    received = datetime.now(UTC)
    handle = "whale_alert"
    tier = "B"

    def _raw(tid: str) -> RawEvent:
        return RawEvent(
            source="x_polling",
            source_kind="x",
            external_id=tid,
            received_at=received,
            raw={
                "id_str": tid,
                "text": f"transfer #{tid}",
                "user": {"id_str": "1", "screen_name": handle},
                "_tier": tier,
            },
            text=f"transfer #{tid}",
        )

    a = Normalizer().normalize(_raw("1789000000000000001"))
    b = Normalizer().normalize(_raw("1789000000000000002"))
    assert a is not None and b is not None
    assert a.title == b.title == f"@{handle}"
    assert a.fingerprint != b.fingerprint


def test_normalizer_routes_x_source_kind_to_x_parser():
    raw = RawEvent(
        source="x_websocket",
        source_kind="x",
        external_id="1789012345678901234",
        received_at=datetime.now(UTC),
        raw={
            "id_str": "1789012345678901234",
            "text": "EXPLOIT on Foo: $5M drained",
            "user": {"id_str": "1", "screen_name": "samczsun"},
            "_tier": "S",
        },
        text="EXPLOIT on Foo: $5M drained",
    )
    ev = Normalizer().normalize(raw)
    assert ev is not None
    assert ev.severity == Severity.high
    assert ev.url == "https://x.com/samczsun/status/1789012345678901234"
    assert "a" in [c.value if hasattr(c, "value") else c for c in ev.category]


def test_normalizer_routes_wublock_security_item_to_high():
    from datetime import UTC, datetime

    from safe_monitor.core.models import RawEvent, Severity
    from safe_monitor.core.normalizer import Normalizer

    raw = RawEvent(
        source="wublock_news",
        source_kind="api",
        external_id="https://www.wublock123.com/news/defi-exploit-61067",
        received_at=datetime.now(UTC),
        raw={
            "title": "某 DeFi 协议遭攻击被盗约 1200 万美元",
            "description": "<p>吴说获悉，某 DeFi 协议遭攻击，攻击者通过协议漏洞被盗约 1200 万美元。</p>",
            "link": "https://www.wublock123.com/news/defi-exploit-61067",
            "guid": "https://www.wublock123.com/news/defi-exploit-61067",
        },
        text="吴说获悉，某 DeFi 协议遭攻击，攻击者通过协议漏洞被盗约 1200 万美元。",
        url="https://www.wublock123.com/news/defi-exploit-61067",
    )
    ev = Normalizer().normalize(raw)
    assert ev is not None
    assert ev.title == "某 DeFi 协议遭攻击被盗约 1200 万美元"
    assert ev.severity == Severity.high
    assert ev.url == "https://www.wublock123.com/news/defi-exploit-61067"


def test_normalizer_routes_wublock_funding_item_to_low():
    from datetime import UTC, datetime

    from safe_monitor.core.models import RawEvent, Severity
    from safe_monitor.core.normalizer import Normalizer

    raw = RawEvent(
        source="wublock_news",
        source_kind="api",
        external_id="https://www.wublock123.com/news/stitch-61068",
        received_at=datetime.now(UTC),
        raw={
            "title": "金融基础设施公司 Stitch 宣布完成 2500 万美元 A 轮融资",
            "description": "<p>吴说获悉，金融基础设施公司 Stitch 宣布完成 2500 万美元 A 轮融资，由 a16z 领投。</p>",
            "link": "https://www.wublock123.com/news/stitch-61068",
            "guid": "https://www.wublock123.com/news/stitch-61068",
        },
        text="吴说获悉，金融基础设施公司 Stitch 宣布完成 2500 万美元 A 轮融资，由 a16z 领投。",
        url="https://www.wublock123.com/news/stitch-61068",
    )
    ev = Normalizer().normalize(raw)
    assert ev is not None
    assert ev.severity == Severity.low


def test_normalizer_routes_other_zh_newsflash_through_wublock_parser():
    # jinse / techflow / foresight / panews share wublock's parse path —
    # title + description + link from RSSHub-fed XML. Sanity-check that
    # the dispatch table includes them, otherwise items show up as
    # "unknown source" and get silently dropped.
    from datetime import UTC, datetime

    from safe_monitor.core.models import RawEvent, Severity
    from safe_monitor.core.normalizer import Normalizer

    for src in ("jinse_news", "techflow_news", "foresight_news", "panews_news"):
        raw = RawEvent(
            source=src,
            source_kind="api",
            external_id=f"id-{src}",
            received_at=datetime.now(UTC),
            raw={
                "title": "某协议遭攻击被盗 500 万美元",
                "description": "<p>某协议遭遇跨链桥攻击，损失约 500 万美元。</p>",
                "link": f"https://example.com/{src}",
                "guid": f"id-{src}",
            },
            text="某协议遭遇跨链桥攻击，损失约 500 万美元。",
            url=f"https://example.com/{src}",
        )
        ev = Normalizer().normalize(raw)
        assert ev is not None, f"{src} dropped by normalizer"
        assert ev.severity == Severity.high, f"{src} severity wrong"
        assert ev.source == src
