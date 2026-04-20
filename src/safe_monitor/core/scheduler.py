from __future__ import annotations

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from safe_monitor.storage.db import Database

log = structlog.get_logger(__name__)


async def prune_fingerprints_job(db: Database, ttl_days: int) -> int:
    n = await db.prune_old_fingerprints(ttl_days)
    log.info("prune_fingerprints", deleted=n, ttl_days=ttl_days)
    return n


def build_scheduler(db: Database, ttl_days: int) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        prune_fingerprints_job,
        trigger="cron",
        hour=3,
        minute=0,
        kwargs={"db": db, "ttl_days": ttl_days},
        id="prune_fingerprints",
        replace_existing=True,
    )
    return scheduler
