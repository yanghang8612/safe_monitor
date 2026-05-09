from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class Severity(IntEnum):
    low = 10
    medium = 20
    high = 30
    critical = 40


class EventCategory(StrEnum):
    a = "a"  # protocol/contract exploit
    b = "b"  # bridge attack
    c = "c"  # CEX / custodian
    d = "d"  # wallet / key / supply chain
    e = "e"  # phishing / social engineering
    f = "f"  # stablecoin / depeg / cascade
    g = "g"  # rug pull
    h = "h"  # compliance / regulatory
    i = "i"  # infrastructure
    j = "j"  # governance attack


class RawEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    source_kind: Literal["tg", "api", "x"]
    external_id: str
    received_at: datetime
    raw: dict[str, Any]
    text: str | None = None
    url: str | None = None
    occurred_at: datetime | None = None


class Event(BaseModel):
    model_config = ConfigDict(frozen=False)

    fingerprint: str
    source: str
    title: str
    body: str | None = None
    url: str | None = None
    severity: Severity
    category: list[EventCategory] = []
    chain: str | None = None
    tx_hash: str | None = None
    attacker_addr: str | None = None
    loss_usd: float | None = None
    occurred_at: datetime | None = None
    received_at: datetime
    raw: dict[str, Any]
