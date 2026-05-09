#!/usr/bin/env -S uv run python
"""Seed last_seen_id for every row in x_users so the first polling cycle
doesn't backfill ~20 historical tweets per user (≈ N×20 TG messages).

Run once after `resolve_x_handles.py` on a fresh deployment. Idempotent —
re-running just refreshes cursors to the current latest tweet.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

import structlog

from safe_monitor.config import load_settings
from safe_monitor.logging_setup import setup as setup_logging
from safe_monitor.sources.x_client import TwitterApiIoClient
from safe_monitor.storage.db import Database


async def _amain(retry: int) -> int:
    settings = load_settings()
    setup_logging(settings.log_level)
    log = structlog.get_logger("seed_x_cursors")

    if not settings.x_api_key:
        log.error("missing X_API_KEY env var; aborting")
        return 2
    xcfg = settings.config.sources.x if settings.config else None
    if not xcfg:
        log.error("config.yaml has no sources.x; aborting")
        return 2

    db = Database(settings.db_path)
    await db.init()
    users = await db.list_x_users()
    if not users:
        log.error("x_users table is empty; run resolve_x_handles.py first")
        await db.close()
        return 2

    client = TwitterApiIoClient(api_key=settings.x_api_key, base_url=xcfg.rest_base_url)

    seeded = empty = errors = 0
    for u in users:
        uid, handle = u["user_id"], u["handle"]
        for attempt in range(1, retry + 1):
            try:
                tweets = await client.get_last_tweets(user_id=uid, limit=1)
                if not tweets:
                    if attempt == retry:
                        empty += 1
                        log.warning("no_tweets", handle=handle)
                    else:
                        await asyncio.sleep(1.0)
                        continue
                    break
                tid = str(tweets[0].get("id_str") or tweets[0].get("id"))
                await db.set_x_user_last_seen(uid, tid)
                seeded += 1
                log.info("seeded", handle=handle, last_seen_id=tid)
                break
            except TwitterApiIoClient.CreditsExhausted:
                log.error("credits exhausted; stopping", seeded=seeded)
                await db.close()
                return 3
            except Exception as e:
                if attempt == retry:
                    errors += 1
                    log.warning("error", handle=handle, error=str(e)[:80])
                else:
                    await asyncio.sleep(1.0)
        await asyncio.sleep(0.2)

    log.info("done", seeded=seeded, empty=empty, errors=errors)
    await db.close()
    return 0 if errors == 0 else 1


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--retry", type=int, default=3, help="per-user retry attempts (default 3)")
    args = p.parse_args()
    sys.exit(asyncio.run(_amain(args.retry)))


if __name__ == "__main__":
    main()
