import pytest
from pathlib import Path

from safe_monitor.storage.db import Database
from safe_monitor.core.scheduler import x_recovery_probe


@pytest.mark.asyncio
async def test_recovery_probe_clears_only_polling_when_credits_present(tmp_path: Path, monkeypatch):
    """Probe verifies REST credits — that proves x_polling can resume but
    says nothing about WS reachability, so x_websocket must NOT be cleared."""
    db = Database(tmp_path / "x.db")
    await db.init()
    await db.set_degraded("x_websocket", True, reason="test")
    await db.set_degraded("x_polling", True, reason="test")

    async def fake_check_credits(api_key: str, base_url: str) -> bool:
        return True

    monkeypatch.setattr("safe_monitor.core.scheduler._check_credits", fake_check_credits)

    await x_recovery_probe(db, api_key="k", base_url="https://api.twitterapi.io")
    assert await db.is_degraded("x_websocket") is True
    assert await db.is_degraded("x_polling") is False
    await db.close()


@pytest.mark.asyncio
async def test_recovery_probe_no_op_when_credits_absent(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "x.db")
    await db.init()
    await db.set_degraded("x_polling", True, reason="test")

    async def fake_check_credits(api_key: str, base_url: str) -> bool:
        return False

    monkeypatch.setattr("safe_monitor.core.scheduler._check_credits", fake_check_credits)

    await x_recovery_probe(db, api_key="k", base_url="https://api.twitterapi.io")
    assert await db.is_degraded("x_polling") is True
    await db.close()


@pytest.mark.asyncio
async def test_recovery_probe_skips_when_only_ws_degraded(tmp_path: Path, monkeypatch):
    """If only x_websocket is degraded, probe shouldn't even spend a REST
    call — WS recovery is out of scope for this probe."""
    db = Database(tmp_path / "x.db")
    await db.init()
    await db.set_degraded("x_websocket", True, reason="test")

    called = []
    async def fake_check_credits(api_key: str, base_url: str) -> bool:
        called.append(True)
        return True

    monkeypatch.setattr("safe_monitor.core.scheduler._check_credits", fake_check_credits)

    await x_recovery_probe(db, api_key="k", base_url="https://api.twitterapi.io")
    assert called == []
    assert await db.is_degraded("x_websocket") is True
    await db.close()


@pytest.mark.asyncio
async def test_recovery_probe_skips_when_not_degraded(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "x.db")
    await db.init()

    called = []
    async def fake_check_credits(api_key: str, base_url: str) -> bool:
        called.append(True)
        return True

    monkeypatch.setattr("safe_monitor.core.scheduler._check_credits", fake_check_credits)

    await x_recovery_probe(db, api_key="k", base_url="https://api.twitterapi.io")
    assert called == []
    await db.close()
