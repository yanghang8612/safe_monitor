from datetime import UTC, datetime

from safe_monitor.core.models import Event, EventCategory, Severity
from safe_monitor.publishers.formatter import format_event


def _ev() -> Event:
    return Event(
        fingerprint="fp",
        source="peckshield_tg",
        title="Resolv protocol exploited for $80M",
        body="Details: part of funds swapped into ETH and USDC.",
        severity=Severity.critical,
        category=[EventCategory.a],
        chain="Ethereum",
        tx_hash="0x" + "a" * 64,
        attacker_addr="0x" + "b" * 40,
        loss_usd=80_000_000,
        url="https://t.me/peckshield/12345",
        received_at=datetime(2026, 4, 20, 13, 45, 2, tzinfo=UTC),
        raw={},
    )


def test_formatter_contains_sections():
    txt = format_event(_ev())
    assert "SUMMARY" in txt
    assert "DETAILS" in txt
    assert "$80,000,000" in txt or "$80M" in txt or "80000000" in txt
    assert "🔴" in txt or "CRITICAL" in txt


def test_formatter_omits_missing_fields():
    e = _ev().model_copy(update={"tx_hash": None, "attacker_addr": None, "chain": None})
    txt = format_event(e)
    assert "Tx hash" not in txt
    assert "Attacker" not in txt
    assert "Chain" not in txt


def test_summary_and_details_split_cleanly():
    from safe_monitor.publishers.formatter import format_details, format_summary

    e = _ev()
    summary = format_summary(e)
    details = format_details(e)
    # Summary holds title/body; details holds the labelled rows.
    assert "SUMMARY" in summary and "DETAILS" not in summary
    assert "DETAILS" in details and "SUMMARY" not in details
    assert e.title in summary
    assert "Chain" in details and "Tx hash" in details


def test_formatter_truncates_long_body():
    long = "a" * 8000
    e = _ev().model_copy(update={"body": long})
    txt = format_event(e)
    assert len(txt) <= 4000
