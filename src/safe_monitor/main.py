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
from safe_monitor.sources.forta import FortaPoller
from safe_monitor.sources.ofac import OfacSdnPoller
from safe_monitor.sources.rss_feed import RssFeedPoller
from safe_monitor.sources.rsshub_tg import RSSHubTGPoller
from safe_monitor.sources.telegram import TelegramIngestor
from safe_monitor.storage.db import Database


async def _build_sources(
    cfg,
    db: Database,
    *,
    settings_api_id: int,
    settings_api_hash: str,
    settings_session: str,
    settings_phone: str,
    settings_tg_mode: str,
    settings_rsshub_base: str,
) -> list[Source]:
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
        elif api.name == "ofac_sdn":
            sources.append(
                OfacSdnPoller(
                    name="ofac_sdn",
                    endpoint=api.endpoint,
                    poll_interval_seconds=api.poll_interval_seconds,
                    db=db,
                )
            )
        elif api.name == "forta":
            sources.append(
                FortaPoller(
                    name="forta",
                    endpoint=api.endpoint,
                    poll_interval_seconds=api.poll_interval_seconds,
                    db=db,
                )
            )
        elif api.name == "rekt_news":
            sources.append(
                RssFeedPoller(
                    name="rekt_news",
                    endpoint=api.endpoint,
                    poll_interval_seconds=api.poll_interval_seconds,
                    db=db,
                )
            )
    if cfg.sources.telegram:
        if settings_tg_mode == "rsshub":
            # One poller per channel; they all hit the shared RSSHub instance
            for ch in cfg.sources.telegram:
                sources.append(
                    RSSHubTGPoller(
                        name=ch.name,
                        username=ch.username,
                        rsshub_base_url=settings_rsshub_base,
                        poll_interval_seconds=ch.poll_interval_seconds,
                        db=db,
                    )
                )
        else:
            # telethon userbot: single ingestor multiplexes all channels
            channels = [(c.name, c.username) for c in cfg.sources.telegram]
            sources.append(
                TelegramIngestor(
                    api_id=settings_api_id,
                    api_hash=settings_api_hash,
                    session_path=settings_session,
                    phone=settings_phone,
                    channels=channels,
                    db=db,
                )
            )
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

    from safe_monitor.core.scheduler import build_scheduler

    scheduler = build_scheduler(db, ttl_days=settings.config.dedup.ttl_days, publisher=publisher)
    scheduler.start()

    sources = await _build_sources(
        settings.config,
        db,
        settings_api_id=settings.tg_api_id,
        settings_api_hash=settings.tg_api_hash,
        settings_session=settings.tg_userbot_session,
        settings_phone=settings.tg_userbot_phone,
        settings_tg_mode=settings.tg_ingest_mode,
        settings_rsshub_base=settings.rsshub_base_url,
    )
    filter_ = Filter(settings.config.filter)

    orch = Orchestrator(sources=sources, publisher=publisher, db=db, filter_=filter_)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, orch.request_shutdown)

    log.info("safe_monitor.start", sources=[s.name for s in sources])
    await orch.run()
    scheduler.shutdown(wait=False)
    await db.close()
    log.info("safe_monitor.stopped")


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
