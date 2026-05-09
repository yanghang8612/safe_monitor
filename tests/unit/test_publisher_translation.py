"""Verify TelegramPublisher's translation gating: Chinese alerts pass through
untouched, English alerts get title+body translated."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from safe_monitor.core.models import Event, EventCategory, Severity
from safe_monitor.publishers.telegram import TelegramPublisher


def _ev(title: str, body: str | None = None) -> Event:
    return Event(
        fingerprint="fp",
        source="x_websocket",
        title=title,
        body=body,
        severity=Severity.high,
        category=[EventCategory.a],
        url="https://x.com/x/status/1",
        received_at=datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC),
        raw={},
    )


@pytest.mark.asyncio
async def test_publisher_skips_translation_for_chinese_event():
    translator = AsyncMock()
    translator.translate = AsyncMock(return_value="should-not-be-called")
    pub = TelegramPublisher(bot_token="fake", chat_id=1, translator=translator)
    pub._send = AsyncMock()

    await pub.publish(_ev(title="PeckShield 警报：Resolv 协议被攻击 $80M", body="跨链桥被利用"))

    translator.translate.assert_not_called()
    assert pub._send.call_count == 1  # one combined message per alert


@pytest.mark.asyncio
async def test_publisher_translates_english_event():
    translator = AsyncMock()
    # Mock translation: prepend ZH- to make assertion easy
    translator.translate = AsyncMock(side_effect=lambda t: f"ZH-{t}")
    pub = TelegramPublisher(bot_token="fake", chat_id=1, translator=translator)
    pub._send = AsyncMock()

    await pub.publish(_ev(title="Resolv protocol exploited for $80M", body="Bridge drained"))

    assert translator.translate.await_count == 2  # title + body
    sent = pub._send.call_args_list[0].args[0]
    assert "ZH-Resolv protocol exploited for $80M" in sent
    assert "ZH-Bridge drained" in sent


@pytest.mark.asyncio
async def test_publisher_works_without_translator():
    pub = TelegramPublisher(bot_token="fake", chat_id=1, translator=None)
    pub._send = AsyncMock()

    await pub.publish(_ev(title="anything", body="anything"))
    assert pub._send.call_count == 1
