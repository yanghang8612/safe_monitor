"""End-to-end: WS and polling both deliver the same tweet — verify
fingerprint dedup catches the cross-source overlap (regression test for
the issue where source-aware fingerprints let duplicates through)."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from safe_monitor.core.deduper import Deduper
from safe_monitor.core.models import RawEvent
from safe_monitor.core.normalizer import Normalizer
from safe_monitor.storage.db import Database


@pytest.mark.asyncio
async def test_ws_and_polling_emit_same_tweet_only_published_once(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    await db.init()

    tweet_payload = {
        "id_str": "1789012345678901234",
        "text": "EXPLOIT on Foo: $5M drained",
        "user": {"id_str": "111", "screen_name": "samczsun"},
        "_tier": "S",
    }
    received_at = datetime.now(UTC)

    ws_event = RawEvent(
        source="x_websocket",
        source_kind="x",
        external_id="1789012345678901234",
        received_at=received_at,
        raw=tweet_payload,
        text=tweet_payload["text"],
    )
    poll_event = RawEvent(
        source="x_polling",
        source_kind="x",
        external_id="1789012345678901234",
        received_at=received_at,
        raw=tweet_payload,
        text=tweet_payload["text"],
    )

    normalizer = Normalizer()
    deduper = Deduper(db)

    norm_ws = normalizer.normalize(ws_event)
    norm_poll = normalizer.normalize(poll_event)
    assert norm_ws is not None and norm_poll is not None
    # Same fingerprint → dedup catches the overlap
    assert norm_ws.fingerprint == norm_poll.fingerprint

    is_dup_first = await deduper.is_duplicate(norm_ws)
    is_dup_second = await deduper.is_duplicate(norm_poll)
    assert is_dup_first is False
    assert is_dup_second is True

    await db.close()
