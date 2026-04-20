import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from safe_monitor.sources.telegram import TelegramIngestor
from safe_monitor.storage.db import Database


@pytest.mark.asyncio
async def test_ingestor_forwards_new_messages(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    await db.init()

    fake_msg = SimpleNamespace(
        id=1001,
        message="Resolv exploited for $80M",
        date=datetime.now(UTC),
        peer_id=SimpleNamespace(channel_id=555),
    )

    fake_client = MagicMock()
    fake_client.start = AsyncMock()
    fake_client.disconnect = AsyncMock()
    fake_client.get_entity = AsyncMock(return_value=SimpleNamespace(id=555, username="peckshield"))

    handlers: list = []

    def fake_on(event):
        def deco(fn):
            handlers.append(fn)
            return fn

        return deco

    fake_client.on = fake_on

    class FakeTGClient:
        def __init__(self, *a, **kw):
            pass

        def __getattr__(self, n):
            return getattr(fake_client, n)

    monkeypatch.setattr("safe_monitor.sources.telegram.TelegramClient", FakeTGClient)

    q: asyncio.Queue = asyncio.Queue()
    ingestor = TelegramIngestor(
        api_id=1,
        api_hash="x",
        session_path=str(tmp_path / "s"),
        phone="+100",
        channels=[("peckshield_tg", "peckshield")],
        db=db,
    )

    run_task = asyncio.create_task(ingestor.run(q))
    # give it a tick to register handlers
    for _ in range(20):
        await asyncio.sleep(0.01)
        if handlers:
            break
    # simulate an incoming event
    event_obj = SimpleNamespace(message=fake_msg, chat_id=555)
    await handlers[0](event_obj)

    raw = await asyncio.wait_for(q.get(), timeout=1)
    assert raw.source == "peckshield_tg"
    assert "Resolv" in raw.text

    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task
    await db.close()
