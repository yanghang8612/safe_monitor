from pathlib import Path
import pytest
from safe_monitor.storage.db import Database


@pytest.mark.asyncio
async def test_upsert_and_list_x_users(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    await db.init()

    await db.upsert_x_user(handle="samczsun", user_id="12345", tier="S")
    await db.upsert_x_user(handle="evilcos",  user_id="67890", tier="S")
    # idempotent re-insert with same user_id MUST also update other columns
    await db.upsert_x_user(handle="samczsun", user_id="12345", tier="A")

    rows = await db.list_x_users()
    assert {r["handle"] for r in rows} == {"samczsun", "evilcos"}  # still 2 rows
    assert next(r for r in rows if r["user_id"] == "12345")["tier"] == "A"  # tier was updated
    assert all(r["last_seen_id"] is None for r in rows)

    await db.set_x_user_last_seen("12345", "999")
    rows = await db.list_x_users()
    assert next(r for r in rows if r["user_id"] == "12345")["last_seen_id"] == "999"

    await db.close()


@pytest.mark.asyncio
async def test_degrade_flag_roundtrip(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    await db.init()
    assert await db.is_degraded("x_websocket") is False
    await db.set_degraded("x_websocket", True, reason="five reconnects failed")
    assert await db.is_degraded("x_websocket") is True
    await db.set_degraded("x_websocket", False)
    assert await db.is_degraded("x_websocket") is False
    await db.close()
