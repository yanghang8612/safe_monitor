from __future__ import annotations

import asyncio
import signal

import structlog

from safe_monitor.config import load_settings
from safe_monitor.core.filter import Filter
from safe_monitor.core.orchestrator import Orchestrator
from safe_monitor.logging_setup import setup as setup_logging
from safe_monitor.publishers.telegram import TelegramPublisher
from safe_monitor.sources.base import Source
from safe_monitor.sources.defillama import DefiLlamaHacksPoller
from safe_monitor.storage.db import Database


async def _build_sources(cfg, db: Database) -> list[Source]:
    sources: list[Source] = []
    for api in cfg.sources.api:
        if api.name == "defillama_hacks":
            sources.append(
                DefiLlamaHacksPoller(
                    name="defillama_api",
                    endpoint=api.endpoint,
                    poll_interval_seconds=api.poll_interval_seconds,
                    db=db,
                )
            )
        # OFAC added in Task 11; Telegram ingestor added in Task 12
    return sources


async def _amain() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)
    log = structlog.get_logger("main")

    db = Database(settings.db_path)
    await db.init()

    publisher = TelegramPublisher(
        bot_token=settings.tg_bot_token,
        chat_id=settings.tg_target_chat_id,
    )

    sources = await _build_sources(settings.config, db)
    filter_ = Filter(settings.config.filter)

    orch = Orchestrator(sources=sources, publisher=publisher, db=db, filter_=filter_)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, orch.request_shutdown)

    log.info("safe_monitor.start", sources=[s.name for s in sources])
    await orch.run()
    await db.close()
    log.info("safe_monitor.stopped")


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
