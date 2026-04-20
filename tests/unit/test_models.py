from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from safe_monitor.core.models import (
    Event,
    EventCategory,
    RawEvent,
    Severity,
)


def test_severity_ordering():
    assert Severity.critical > Severity.high
    assert Severity.high > Severity.medium
    assert Severity.medium > Severity.low


def test_raw_event_minimal():
    e = RawEvent(
        source="peckshield_tg",
        source_kind="tg",
        external_id="12345",
        received_at=datetime.now(UTC),
        raw={"text": "hi"},
    )
    assert e.source == "peckshield_tg"
    assert e.raw["text"] == "hi"


def test_event_minimal():
    now = datetime.now(UTC)
    e = Event(
        fingerprint="abc",
        source="defillama_api",
        title="Test hack",
        severity=Severity.high,
        category=[EventCategory.a],
        received_at=now,
        raw={},
    )
    assert e.fingerprint == "abc"
    assert EventCategory.a in e.category


def test_event_rejects_missing_required():
    with pytest.raises(ValidationError):
        Event(source="x", title="y")  # missing fingerprint etc.
