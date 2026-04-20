from __future__ import annotations

import abc
import asyncio

from safe_monitor.core.models import RawEvent


class Source(abc.ABC):
    name: str

    @abc.abstractmethod
    async def run(self, sink: asyncio.Queue[RawEvent]) -> None:
        """Run until cancelled; push RawEvents onto the queue."""
