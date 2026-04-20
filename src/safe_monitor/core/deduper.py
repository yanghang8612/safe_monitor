from __future__ import annotations

from safe_monitor.core.models import Event
from safe_monitor.storage.db import Database


class Deduper:
    def __init__(self, db: Database):
        self._db = db

    async def is_duplicate(self, event: Event) -> bool:
        existed = await self._db.check_and_insert_fingerprint(
            fingerprint=event.fingerprint,
            source=event.source,
        )
        return existed
