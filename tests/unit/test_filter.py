from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from safe_monitor.config import FilterCfg
from safe_monitor.core.filter import Filter
from safe_monitor.core.models import Event, EventCategory, Severity


def _event(title: str, sev: Severity, source: str = "s") -> Event:
    return Event(
        fingerprint="f",
        source=source,
        title=title,
        severity=sev,
        category=[EventCategory.a],
        received_at=datetime.now(UTC),
        raw={},
    )


@pytest.mark.asyncio
async def test_drops_below_min_severity():
    f = Filter(FilterCfg(min_severity="high", deny_keywords=[]))
    assert await f.allow(_event("x", Severity.medium)) is False
    assert await f.allow(_event("x", Severity.high)) is True


@pytest.mark.asyncio
async def test_drops_deny_keyword():
    f = Filter(FilterCfg(min_severity="low", deny_keywords=["airdrop"]))
    assert await f.allow(_event("Free airdrop soon", Severity.high)) is False
    assert await f.allow(_event("Real exploit", Severity.high)) is True


@pytest.mark.asyncio
async def test_classifier_runs_only_for_x_sources():
    classifier = AsyncMock()
    classifier.is_security = AsyncMock(return_value=True)
    f = Filter(FilterCfg(min_severity="low", deny_keywords=[]), classifier=classifier)

    # Non-x source bypasses classifier
    assert await f.allow(_event("anything", Severity.high, source="rekt_news")) is True
    classifier.is_security.assert_not_called()

    # x_websocket / x_polling source triggers it
    assert await f.allow(_event("anything", Severity.high, source="x_websocket")) is True
    classifier.is_security.assert_awaited_once()


@pytest.mark.asyncio
async def test_classifier_drops_non_security_x_event():
    classifier = AsyncMock()
    classifier.is_security = AsyncMock(return_value=False)
    f = Filter(FilterCfg(min_severity="low", deny_keywords=[]), classifier=classifier)

    assert await f.allow(_event("playing some LoL", Severity.high, source="x_polling")) is False


@pytest.mark.asyncio
async def test_classifier_skipped_when_severity_already_below_min():
    classifier = AsyncMock()
    classifier.is_security = AsyncMock(return_value=True)
    f = Filter(FilterCfg(min_severity="high", deny_keywords=[]), classifier=classifier)

    assert await f.allow(_event("anything", Severity.medium, source="x_websocket")) is False
    classifier.is_security.assert_not_called()
