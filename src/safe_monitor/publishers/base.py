from __future__ import annotations

import abc

from safe_monitor.core.models import Event


class Publisher(abc.ABC):
    @abc.abstractmethod
    async def publish(self, event: Event) -> bool:
        """Return True if delivery succeeded."""
