from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from telethon import TelegramClient, events

from safe_monitor.core.models import RawEvent
from safe_monitor.sources.base import Source
from safe_monitor.storage.db import Database

log = structlog.get_logger(__name__)


class TelegramIngestor(Source):
    name = "telegram_ingestor"

    def __init__(
        self,
        *,
        api_id: int,
        api_hash: str,
        session_path: str,
        phone: str,
        channels: list[tuple[str, str]],  # (source_name, username)
        db: Database,
    ):
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_path = session_path
        self._phone = phone
        self._channels = channels
        self._db = db
        self._client: TelegramClient | None = None

    async def run(self, sink: asyncio.Queue[RawEvent]) -> None:
        self._client = TelegramClient(self._session_path, self._api_id, self._api_hash)
        await self._client.start(phone=self._phone)

        chat_id_to_source: dict[int, str] = {}
        for source_name, username in self._channels:
            try:
                ent = await self._client.get_entity(username)
                chat_id_to_source[int(ent.id)] = source_name
                log.info("tg.channel_bound", source=source_name, username=username, id=ent.id)
            except Exception as e:
                log.warning("tg.channel_resolve_failed", username=username, error=str(e))

        if not chat_id_to_source:
            log.warning("tg.no_channels_resolved", count=len(self._channels))
        else:

            @self._client.on(events.NewMessage(chats=list(chat_id_to_source.keys())))
            async def handler(event):
                msg = event.message
                chat_id = int(event.chat_id) if hasattr(event, "chat_id") else None
                source_name = chat_id_to_source.get(chat_id or 0, "unknown_tg")
                text = (msg.message or "").strip()
                if not text:
                    return
                raw = RawEvent(
                    source=source_name,
                    source_kind="tg",
                    external_id=str(msg.id),
                    received_at=datetime.now(UTC),
                    raw={"text": text, "msg_id": msg.id, "chat_id": chat_id},
                    text=text,
                    occurred_at=msg.date if hasattr(msg, "date") else None,
                )
                try:
                    await sink.put(raw)
                except asyncio.CancelledError:
                    raise

        try:
            # Keep running until cancelled
            await asyncio.Event().wait()
        finally:
            await self._client.disconnect()
