#!/usr/bin/env -S uv run python
"""One-shot CLI: register `from:<handle>` filter rules on TwitterAPI.io so
the WebSocket endpoint streams tweets from our 47-handle whitelist.

TwitterAPI.io rule.value is capped at 255 chars, so we greedy-pack handles
into multiple rules tagged `safe_monitor_v1_NN`. Default behaviour is
`reset`: remove any existing rules with that tag prefix and recreate from
the current x_users table — keeps the live ruleset in sync with config.

Run AFTER `resolve_x_handles.py` (the script reads from `x_users`, not
config.yaml, so renames apply once you've re-resolved).

Costs: each active rule charges per delivered tweet. Inactive rules are
free. `--dry-run` prints the planned rules without touching the API.
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

TAG_PREFIX = "safe_monitor_v1_"
VALUE_BUDGET = 240  # leave 15-byte margin under the 255 hard cap
INTERVAL_SECONDS = 60


def _pack_rules(handles: list[str]) -> list[str]:
    """Greedy-pack `from:<h> OR from:<h2> ...` strings under VALUE_BUDGET."""
    out: list[str] = []
    cur = ""
    for h in handles:
        token = f"from:{h}"
        candidate = token if not cur else f"{cur} OR {token}"
        if len(candidate) > VALUE_BUDGET:
            if cur:
                out.append(cur)
            cur = token
        else:
            cur = candidate
    if cur:
        out.append(cur)
    return out


async def _amain(reset: bool, dry_run: bool) -> int:
    settings = load_settings()
    setup_logging(settings.log_level)
    log = structlog.get_logger("setup_x_ws_rules")

    xcfg = settings.config.sources.x if settings.config else None
    if not xcfg or not xcfg.handles:
        log.error("config.yaml has no sources.x.handles; aborting")
        return 2
    if not dry_run and not settings.x_api_key:
        log.error("missing X_API_KEY env var; aborting (use --dry-run to plan offline)")
        return 2

    db = Database(settings.db_path)
    await db.init()
    rows = await db.list_x_users()
    await db.close()
    if not rows:
        log.error("x_users table is empty; run scripts/resolve_x_handles.py first")
        return 2

    handles = [r["handle"] for r in rows]
    rule_values = _pack_rules(handles)
    log.info("planned", rules=len(rule_values), handles=len(handles))
    for i, v in enumerate(rule_values):
        log.info("rule_plan", idx=i, length=len(v), value=v)

    if dry_run:
        log.info("dry_run; no API calls made")
        return 0

    client = TwitterApiIoClient(
        api_key=settings.x_api_key, base_url=xcfg.rest_base_url
    )

    try:
        existing = await client.list_filter_rules()
    except Exception as e:
        log.error("list_filter_rules failed", error=str(e))
        return 3
    ours = [r for r in existing if str(r.get("tag", "")).startswith(TAG_PREFIX)]
    log.info(
        "existing_rules",
        total=len(existing),
        owned_by_safe_monitor=len(ours),
    )

    if reset:
        for r in ours:
            rid = str(r.get("rule_id") or "")
            if not rid:
                continue
            try:
                await client.delete_filter_rule(rid)
                log.info("rule_deleted", rule_id=rid, tag=r.get("tag"))
            except Exception as e:
                log.warning("rule_delete_failed", rule_id=rid, error=str(e))
        ours = []

    # Map remaining ours-by-tag for in-place update on non-reset path
    by_tag = {str(r.get("tag")): r for r in ours}

    added = updated = activated = 0
    for i, value in enumerate(rule_values):
        tag = f"{TAG_PREFIX}{i:02d}"
        match = by_tag.get(tag)
        if match:
            rule_id = str(match["rule_id"])
            try:
                await client.update_filter_rule(
                    rule_id=rule_id,
                    tag=tag,
                    value=value,
                    interval_seconds=INTERVAL_SECONDS,
                    is_effect=1,
                )
                updated += 1
                log.info("rule_updated", rule_id=rule_id, tag=tag, length=len(value))
            except Exception as e:
                log.error("rule_update_failed", tag=tag, error=str(e))
                return 3
            continue

        try:
            rule_id = await client.add_filter_rule(
                tag=tag, value=value, interval_seconds=INTERVAL_SECONDS
            )
            added += 1
            log.info("rule_added", rule_id=rule_id, tag=tag, length=len(value))
        except Exception as e:
            log.error("rule_add_failed", tag=tag, error=str(e))
            return 3
        try:
            await client.update_filter_rule(
                rule_id=rule_id,
                tag=tag,
                value=value,
                interval_seconds=INTERVAL_SECONDS,
                is_effect=1,
            )
            activated += 1
            log.info("rule_activated", rule_id=rule_id, tag=tag)
        except Exception as e:
            log.error("rule_activate_failed", rule_id=rule_id, tag=tag, error=str(e))
            return 3

    log.info("done", added=added, updated=updated, activated=activated)
    log.info(
        "next_step",
        msg=(
            "Now reset the x_websocket degraded flag: "
            "sqlite3 data/safe_monitor.db "
            "\"UPDATE source_status SET degraded=0, reason=NULL, "
            "changed_at=datetime('now') WHERE source='x_websocket';\""
        ),
    )
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--no-reset",
        action="store_true",
        help="reuse existing rules with our tag prefix instead of deleting them first",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned rules without making any API calls",
    )
    args = p.parse_args()
    sys.exit(asyncio.run(_amain(reset=not args.no_reset, dry_run=args.dry_run)))


if __name__ == "__main__":
    main()
