import os
from pathlib import Path

from safe_monitor.config import Settings, load_settings


def test_load_settings(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        """
sources:
  api:
    - name: defillama_hacks
      endpoint: https://x
      poll_interval_seconds: 60
  telegram:
    - name: foo_tg
      username: foo
filter:
  min_severity: medium
  deny_keywords: ["x"]
dedup:
  ttl_days: 5
"""
    )
    monkeypatch.setenv("SAFE_MONITOR_CONFIG", str(cfg))
    monkeypatch.setenv("TG_API_ID", "1")
    monkeypatch.setenv("TG_API_HASH", "x")
    monkeypatch.setenv("TG_USERBOT_PHONE", "+100")
    monkeypatch.setenv("TG_USERBOT_SESSION", "/tmp/s")
    monkeypatch.setenv("TG_BOT_TOKEN", "t")
    monkeypatch.setenv("TG_TARGET_CHAT_ID", "42")
    monkeypatch.setenv("SAFE_MONITOR_DB_PATH", "/tmp/db")

    s: Settings = load_settings()
    assert s.tg_api_id == 1
    assert s.tg_target_chat_id == 42
    assert s.config.dedup.ttl_days == 5
    assert len(s.config.sources.api) == 1
    assert s.config.sources.telegram[0].username == "foo"
