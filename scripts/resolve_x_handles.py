#!/usr/bin/env -S uv run python
"""One-shot CLI: resolve every handle in config.yaml's `sources.x.handles`
to a user_id via TwitterAPI.io and write to the x_users table.

Idempotent — already-resolved handles are skipped unless --force.
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


async def _amain(force: bool) -> int:
    settings = load_settings()
    setup_logging(settings.log_level)
    log = structlog.get_logger("resolve_x_handles")

    if not settings.x_api_key:
        log.error("missing X_API_KEY env var; aborting")
        return 2
    xcfg = settings.config.sources.x if settings.config else None
    if not xcfg or not xcfg.handles:
        log.error("config.yaml has no sources.x.handles; aborting")
        return 2

    db = Database(settings.db_path)
    await db.init()

    existing = {u["handle"]: u for u in await db.list_x_users()}
    client = TwitterApiIoClient(api_key=settings.x_api_key, base_url=xcfg.rest_base_url)

    ok = miss = skip = err = 0
    for h in xcfg.handles:
        if not force and h.handle in existing:
            skip += 1
            continue
        try:
            uid = await client.resolve_handle(h.handle)
        except TwitterApiIoClient.CreditsExhausted:
            log.error("credits exhausted; stopping", resolved=ok)
            await db.close()
            return 3
        except Exception as e:
            log.warning("resolve_failed", handle=h.handle, error=str(e))
            err += 1
            continue
        if not uid:
            log.warning("handle_not_found", handle=h.handle)
            miss += 1
            continue
        await db.upsert_x_user(handle=h.handle, user_id=uid, tier=h.tier)
        ok += 1
        log.info("resolved", handle=h.handle, user_id=uid, tier=h.tier)

    log.info("done", resolved=ok, skipped=skip, missing=miss, errors=err)
    await db.close()
    return 0 if (miss + err) == 0 else 1


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true", help="re-resolve already-stored handles")
    args = p.parse_args()
    sys.exit(asyncio.run(_amain(args.force)))


if __name__ == "__main__":
    main()
