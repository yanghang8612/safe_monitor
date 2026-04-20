from datetime import UTC, datetime

from safe_monitor.config import FilterCfg
from safe_monitor.core.filter import Filter
from safe_monitor.core.models import Event, EventCategory, Severity


def _event(title: str, sev: Severity) -> Event:
    return Event(
        fingerprint="f",
        source="s",
        title=title,
        severity=sev,
        category=[EventCategory.a],
        received_at=datetime.now(UTC),
        raw={},
    )


def test_drops_below_min_severity():
    f = Filter(FilterCfg(min_severity="high", deny_keywords=[]))
    assert f.allow(_event("x", Severity.medium)) is False
    assert f.allow(_event("x", Severity.high)) is True


def test_drops_deny_keyword():
    f = Filter(FilterCfg(min_severity="low", deny_keywords=["airdrop"]))
    assert f.allow(_event("Free airdrop soon", Severity.high)) is False
    assert f.allow(_event("Real exploit", Severity.high)) is True
