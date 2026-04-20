from __future__ import annotations

import structlog
from telegram import Bot
from telegram.error import TelegramError
from tenacity import retry, stop_after_attempt, wait_exponential

from safe_monitor.core.models import Event
from safe_monitor.publishers.base import Publisher
from safe_monitor.publishers.formatter import format_event

log = structlog.get_logger(__name__)


class TelegramPublisher(Publisher):
    def __init__(self, *, bot_token: str, chat_id: int):
        self._bot = Bot(token=bot_token)
        self._chat_id = chat_id

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=30),
    )
    async def _send(self, text: str) -> None:
        # v0 intentionally ships plain text (no parse_mode). MarkdownV2 is
        # deferred to v0.1 because it requires escaping every dynamic field
        # (titles, addresses, loss strings) against a dozen special chars --
        # any miss produces a Telegram 400. See the design spec §6.5.
        await self._bot.send_message(
            chat_id=self._chat_id,
            text=text,
            disable_web_page_preview=True,
        )

    async def publish(self, event: Event) -> bool:
        text = format_event(event)
        try:
            await self._send(text)
            return True
        except TelegramError as e:
            log.error("telegram.publish_failed", error=str(e), fp=event.fingerprint)
            return False
