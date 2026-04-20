from __future__ import annotations

from datetime import datetime
from enum import IntEnum, Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


class Severity(IntEnum):
    low = 10
    medium = 20
    high = 30
    critical = 40


class EventCategory(str, Enum):
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
    source_kind: Literal["tg", "api"]
    external_id: str
    received_at: datetime
    raw: dict[str, Any]
    text: Optional[str] = None
    url: Optional[str] = None
    occurred_at: Optional[datetime] = None


class Event(BaseModel):
    model_config = ConfigDict(frozen=False)

    fingerprint: str
    source: str
    title: str
    body: Optional[str] = None
    url: Optional[str] = None
    severity: Severity
    category: list[EventCategory] = []
    chain: Optional[str] = None
    tx_hash: Optional[str] = None
    attacker_addr: Optional[str] = None
    loss_usd: Optional[float] = None
    occurred_at: Optional[datetime] = None
    received_at: datetime
    raw: dict[str, Any]
