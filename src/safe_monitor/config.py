from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSourceCfg(BaseModel):
    name: str
    endpoint: str
    poll_interval_seconds: int = 300


class TgSourceCfg(BaseModel):
    name: str
    username: str
    poll_interval_seconds: int = 120  # used in rsshub mode; ignored by telethon userbot


class XHandleCfg(BaseModel):
    handle: str
    tier: Literal["S", "A", "B", "C", "D", "E"]


class XSourceCfg(BaseModel):
    enabled: bool = False
    websocket_url: str = "wss://ws.twitterapi.io/twitter/tweet/websocket"
    rest_base_url: str = "https://api.twitterapi.io"
    poll_interval_seconds: int = 600
    ws_reconnect_min_seconds: int = 90
    ws_max_consecutive_failures: int = 5
    degrade_recheck_seconds: int = 1800
    handles: list[XHandleCfg] = []


class SourcesCfg(BaseModel):
    api: list[ApiSourceCfg] = []
    telegram: list[TgSourceCfg] = []
    x: XSourceCfg | None = None


class FilterCfg(BaseModel):
    min_severity: Literal["low", "medium", "high", "critical"] = "medium"
    deny_keywords: list[str] = []


class DedupCfg(BaseModel):
    ttl_days: int = 7


class ConfigYaml(BaseModel):
    sources: SourcesCfg
    filter: FilterCfg = Field(default_factory=FilterCfg)
    dedup: DedupCfg = Field(default_factory=DedupCfg)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Telegram ingestion mode: "userbot" (telethon, needs API credentials) or "rsshub" (no creds)
    tg_ingest_mode: Literal["userbot", "rsshub"] = Field("rsshub", alias="TG_INGEST_MODE")
    rsshub_base_url: str = Field("http://rsshub:1200", alias="RSSHUB_BASE_URL")

    # Required only when TG_INGEST_MODE=userbot
    tg_api_id: int = Field(0, alias="TG_API_ID")
    tg_api_hash: str = Field("", alias="TG_API_HASH")
    tg_userbot_phone: str = Field("", alias="TG_USERBOT_PHONE")
    tg_userbot_session: str = Field("./data/userbot.session", alias="TG_USERBOT_SESSION")

    # Required always (push side)
    tg_bot_token: str = Field(..., alias="TG_BOT_TOKEN")
    tg_target_chat_id: int = Field(..., alias="TG_TARGET_CHAT_ID")

    x_api_key: str = Field("", alias="X_API_KEY")

    # Optional translation: when OPENAI_API_KEY is set, non-Chinese alerts get
    # translated to Simplified Chinese before being formatted into TG messages.
    openai_api_key: str = Field("", alias="OPENAI_API_KEY")
    openai_model: str = Field("gpt-4o-mini", alias="OPENAI_MODEL")

    db_path: str = Field("./data/safe_monitor.db", alias="SAFE_MONITOR_DB_PATH")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    config_path: str = Field("./config.yaml", alias="SAFE_MONITOR_CONFIG")

    config: ConfigYaml | None = None  # filled in load_settings


def load_settings() -> Settings:
    s = Settings()  # pydantic-settings reads env
    data = yaml.safe_load(Path(s.config_path).read_text())
    s.config = ConfigYaml(**data)
    return s
