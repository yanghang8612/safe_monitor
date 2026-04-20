from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from safe_monitor.core.models import Event, Severity
from safe_monitor.publishers.base import Publisher
from safe_monitor.storage.db import Database

log = structlog.get_logger(__name__)

_MAX_BACKOFF = timedelta(hours=24)
_BASE_BACKOFF_MINUTES = 10
_MAX_RETRIES = 5


async def prune_fingerprints_job(db: Database, ttl_days: int) -> int:
    n = await db.prune_old_fingerprints(ttl_days)
    log.info("prune_fingerprints", deleted=n, ttl_days=ttl_days)
    return n


def _severity_from_name(name: str) -> Severity:
    try:
        return Severity[name]
    except KeyError:
        return Severity.low


def _next_retry(retry_count: int) -> str:
    """After the N-th failure the next wait is 10 * 2^N minutes, capped at 24h."""
    minutes = _BASE_BACKOFF_MINUTES * (2**retry_count)
    delta = timedelta(minutes=minutes)
    if delta > _MAX_BACKOFF:
        delta = _MAX_BACKOFF
    return (datetime.now(UTC) + delta).isoformat()


async def retry_failed_events_job(db: Database, publisher: Publisher) -> int:
    """Process pending retries: re-publish, then delete on success or reschedule on failure."""
    now_iso = datetime.now(UTC).isoformat()
    rows = await db.get_pending_retries(now_iso)
    retried = 0
    for row in rows:
        event_id = row["event_id"]
        failed_id = row["id"]
        retry_count = row["retry_count"]
        ev_row = await db.get_event_by_id(event_id)
        if ev_row is None:
            # event gone — give up on this failed row
            log.warning("retry.event_missing", failed_id=failed_id, event_id=event_id)
            await db.delete_failed(failed_id)
            continue
        received_at_raw = ev_row.get("received_at")
        if isinstance(received_at_raw, str):
            try:
                received_at = datetime.fromisoformat(received_at_raw)
            except ValueError:
                received_at = datetime.now(UTC)
        else:
            received_at = datetime.now(UTC)
        event = Event(
            fingerprint=f"retry:{event_id}",
            source=ev_row["source"],
            title=ev_row["title"],
            body=None,
            url=ev_row.get("url"),
            severity=_severity_from_name(ev_row["severity"]),
            category=[],
            chain=None,
            tx_hash=None,
            attacker_addr=None,
            loss_usd=None,
            occurred_at=None,
            received_at=received_at,
            raw={},
        )
        try:
            ok = await publisher.publish(event)
        except Exception as e:
            log.warning("retry.publish_error", failed_id=failed_id, error=str(e))
            ok = False
        if ok:
            await db.delete_failed(failed_id)
            log.info("retry.published", failed_id=failed_id, event_id=event_id)
        else:
            new_count = retry_count + 1
            if new_count >= _MAX_RETRIES:
                log.warning(
                    "retry.giving_up", failed_id=failed_id, event_id=event_id, tries=new_count
                )
                # Bump the count to max so the row no longer matches get_pending_retries.
                await db.update_retry(failed_id, next_retry_at=now_iso, increment=True)
            else:
                await db.update_retry(failed_id, next_retry_at=_next_retry(new_count))
        retried += 1
    if retried:
        log.info("retry.batch_done", processed=retried)
    return retried


def build_scheduler(db: Database, ttl_days: int, publisher: Publisher) -> AsyncIOScheduler:
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
    scheduler.add_job(
        retry_failed_events_job,
        trigger="interval",
        minutes=10,
        kwargs={"db": db, "publisher": publisher},
        id="retry_failed_events",
        replace_existing=True,
    )
    return scheduler
