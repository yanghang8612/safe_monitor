from __future__ import annotations

import structlog
from telegram import Bot
from telegram.error import TelegramError
from tenacity import retry, stop_after_attempt, wait_exponential

from safe_monitor.core.models import Event
from safe_monitor.publishers.base import Publisher
from safe_monitor.publishers.formatter import format_event
from safe_monitor.publishers.translator import Translator, is_mostly_chinese

log = structlog.get_logger(__name__)


class TelegramPublisher(Publisher):
    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: int,
        translator: Translator | None = None,
    ):
        self._bot = Bot(token=bot_token)
        self._chat_id = chat_id
        self._translator = translator

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

    async def _maybe_translate(self, event: Event) -> Event:
        if self._translator is None:
            return event
        # Cheap pre-check: if the original is already mostly Chinese, skip the API call.
        joined = f"{event.title}\n{event.body or ''}"
        if is_mostly_chinese(joined):
            return event
        title = await self._translator.translate(event.title)
        body = await self._translator.translate(event.body) if event.body else event.body
        return event.model_copy(update={"title": title, "body": body})

    async def publish(self, event: Event) -> bool:
        # One alert = one message. In channel mode this becomes one card per
        # alert (with channel header); in private chat mode TG groups them
        # without per-message headers regardless of how we split.
        try:
            ev = await self._maybe_translate(event)
            await self._send(format_event(ev))
            return True
        except TelegramError as e:
            log.error("telegram.publish_failed", error=str(e), fp=event.fingerprint)
            return False
