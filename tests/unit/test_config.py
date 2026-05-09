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
    # Defaults
    assert s.tg_ingest_mode == "rsshub"
    assert s.rsshub_base_url == "http://rsshub:1200"
    assert s.config.sources.telegram[0].poll_interval_seconds == 120


def test_load_settings_without_userbot_creds(tmp_path: Path, monkeypatch):
    """In rsshub mode, telethon env vars are not required."""
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        """
sources:
  api: []
  telegram:
    - name: foo_tg
      username: foo
      poll_interval_seconds: 90
"""
    )
    monkeypatch.setenv("SAFE_MONITOR_CONFIG", str(cfg))
    monkeypatch.setenv("TG_BOT_TOKEN", "t")
    monkeypatch.setenv("TG_TARGET_CHAT_ID", "42")
    # Deliberately do NOT set TG_API_ID / TG_API_HASH / TG_USERBOT_PHONE
    for k in ("TG_API_ID", "TG_API_HASH", "TG_USERBOT_PHONE"):
        monkeypatch.delenv(k, raising=False)

    s: Settings = load_settings()
    assert s.tg_api_id == 0
    assert s.tg_api_hash == ""
    assert s.tg_userbot_phone == ""
    assert s.config.sources.telegram[0].poll_interval_seconds == 90


def test_x_config_loads_handles_with_tiers(tmp_path):
    from safe_monitor.config import ConfigYaml
    import yaml
    raw = yaml.safe_load("""
sources:
  api: []
  telegram: []
  x:
    enabled: true
    websocket_url: "wss://ws.twitterapi.io/twitter/tweet/websocket"
    rest_base_url: "https://api.twitterapi.io"
    poll_interval_seconds: 600
    handles:
      - {handle: samczsun, tier: S}
      - {handle: PeckShieldAlert, tier: A}
      - {handle: WuBlockchain, tier: D}
""")
    cfg = ConfigYaml(**raw)
    assert cfg.sources.x is not None
    assert cfg.sources.x.enabled is True
    assert len(cfg.sources.x.handles) == 3
    assert cfg.sources.x.handles[0].tier == "S"
