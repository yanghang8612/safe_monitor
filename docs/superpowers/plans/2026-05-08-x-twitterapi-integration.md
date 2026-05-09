# X (Twitter) Real-Time Ingestion via TwitterAPI.io — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a sub-second-latency X (Twitter) ingestion path to `safe_monitor` that streams tweets from a curated 47-account whitelist into the existing event pipeline, via TwitterAPI.io's WebSocket plus REST polling backfill, with auto-degrade on provider failure.

**Architecture:**
- **Primary:** Persistent outbound WebSocket to `wss://ws.twitterapi.io/twitter/tweet/websocket` subscribed to a `follow` rule of all 47 user IDs. Pushes `fast_tweet` / `tweet` events into the existing `RawEvent` queue.
- **Backfill:** Per-user polling of `/twitter/user/last_tweets` every 600 s. Existing fingerprint dedup catches WS/poll overlaps; backfill only matters during the mandatory 90 s reconnect window or when WS silently misses a message.
- **Degrade:** After 5 consecutive WS reconnect failures or HTTP 402/429 from REST, set a degrade flag in the `checkpoints` table; the X sources self-disable while every other source (TG mirrors, RSS, Forta, OFAC) keeps running. The orchestrator probes for recovery every 30 min.
- **Severity:** Tier-aware floor in normalizer — Tier S/A handles force `severity≥high`; Tier B/C/D fall back to the existing keyword scorer.
- **No public IP needed:** WS is an outbound connection; AWS default outbound 443 works.

**Tech stack:** Python 3.12, `websockets` (new dep, `pyproject.toml`), `httpx` (already locked), `aiosqlite` (already locked), `pytest-asyncio` + `respx` (already locked) for tests.

---

## File Structure

**Create:**
- `src/safe_monitor/sources/x_websocket.py` — WS client `Source`
- `src/safe_monitor/sources/x_polling.py` — REST polling `Source` (backfill)
- `src/safe_monitor/sources/x_client.py` — shared TwitterAPI.io HTTP + WS auth helper
- `src/safe_monitor/core/parsers/x_tweet.py` — tweet payload → normalized dict
- `src/safe_monitor/storage/migrations/002_x_users.sql` — new tables
- `scripts/resolve_x_handles.py` — one-shot bootstrap CLI
- `tests/unit/test_parsers_x_tweet.py`
- `tests/unit/test_x_client.py`
- `tests/integration/test_x_websocket_source.py`
- `tests/integration/test_x_polling_source.py`
- `tests/integration/test_db_x_users.py`
- `tests/fixtures/x_tweet_ws_fast_tweet.json`
- `tests/fixtures/x_tweet_rest_response.json`

**Modify:**
- `src/safe_monitor/config.py` — add `XSourceCfg`, `Settings.x_api_key`, etc.
- `src/safe_monitor/core/models.py` — extend `RawEvent.source_kind` literal with `"x"`
- `src/safe_monitor/core/normalizer.py` — route `source_kind=="x"`, tier floor
- `src/safe_monitor/storage/db.py` — `x_users` CRUD + degrade get/set
- `src/safe_monitor/main.py` — wire X sources
- `config.yaml` — add `x:` block + 47-handle list
- `pyproject.toml` — add `websockets>=12` to deps
- `DEPLOY.md` — bootstrap step + env vars + degrade behaviour

---

## Pre-flight (one-time, manual)

- [ ] **Step P1:** Register at https://twitterapi.io/, create an API key in Dashboard, top up at least \$5 of credits.
- [ ] **Step P2:** Add to `.env` (do **not** commit):
  ```
  X_API_KEY=<your-twitterapi.io-key>
  ```
  Verify it loads: `grep -c X_API_KEY .env` should print `1`.

---

## Task 1 — DB schema: `x_users` table + degrade flag

**Files:**
- Create: `src/safe_monitor/storage/migrations/002_x_users.sql`
- Modify: `src/safe_monitor/storage/db.py`
- Test: `tests/integration/test_db_x_users.py`

- [ ] **Step 1.1: Write the failing test**

```python
# tests/integration/test_db_x_users.py
from pathlib import Path
import pytest
from safe_monitor.storage.db import Database


@pytest.mark.asyncio
async def test_upsert_and_list_x_users(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    await db.init()

    await db.upsert_x_user(handle="samczsun", user_id="12345", tier="S")
    await db.upsert_x_user(handle="evilcos",  user_id="67890", tier="S")
    # idempotent re-insert with same user_id should not duplicate
    await db.upsert_x_user(handle="samczsun", user_id="12345", tier="S")

    rows = await db.list_x_users()
    assert {r["handle"] for r in rows} == {"samczsun", "evilcos"}
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
```

- [ ] **Step 1.2: Run, expect failure**

```
uv run pytest tests/integration/test_db_x_users.py -v
```
Expected: `ModuleNotFoundError` or `AttributeError: ... 'upsert_x_user'`.

- [ ] **Step 1.3: Write the migration**

```sql
-- src/safe_monitor/storage/migrations/002_x_users.sql
CREATE TABLE IF NOT EXISTS x_users (
  user_id        TEXT PRIMARY KEY,
  handle         TEXT NOT NULL UNIQUE,
  tier           TEXT NOT NULL,                   -- 'S' | 'A' | 'B' | 'C' | 'D' | 'E'
  last_seen_id   TEXT,
  added_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_x_users_handle ON x_users(handle);

CREATE TABLE IF NOT EXISTS source_status (
  source     TEXT PRIMARY KEY,
  degraded   INTEGER NOT NULL DEFAULT 0,
  reason     TEXT,
  changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

- [ ] **Step 1.4: Add DB methods**

Append to `src/safe_monitor/storage/db.py` (inside `class Database`):

```python
    async def upsert_x_user(self, *, handle: str, user_id: str, tier: str) -> None:
        assert self._conn is not None
        await self._conn.execute(
            """INSERT INTO x_users(user_id, handle, tier, updated_at)
               VALUES(?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 handle=excluded.handle, tier=excluded.tier,
                 updated_at=excluded.updated_at""",
            (user_id, handle, tier, datetime.now(UTC).isoformat()),
        )
        await self._conn.commit()

    async def list_x_users(self) -> list[dict[str, Any]]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT user_id, handle, tier, last_seen_id FROM x_users ORDER BY handle"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def set_x_user_last_seen(self, user_id: str, last_seen_id: str) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE x_users SET last_seen_id=?, updated_at=? WHERE user_id=?",
            (last_seen_id, datetime.now(UTC).isoformat(), user_id),
        )
        await self._conn.commit()

    async def set_degraded(self, source: str, degraded: bool, reason: str | None = None) -> None:
        assert self._conn is not None
        await self._conn.execute(
            """INSERT INTO source_status(source, degraded, reason, changed_at)
               VALUES(?,?,?,?)
               ON CONFLICT(source) DO UPDATE SET
                 degraded=excluded.degraded, reason=excluded.reason,
                 changed_at=excluded.changed_at""",
            (source, 1 if degraded else 0, reason, datetime.now(UTC).isoformat()),
        )
        await self._conn.commit()

    async def is_degraded(self, source: str) -> bool:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT degraded FROM source_status WHERE source=?", (source,)
        ) as cur:
            row = await cur.fetchone()
        return bool(row and row[0])
```

- [ ] **Step 1.5: Run, expect pass**

```
uv run pytest tests/integration/test_db_x_users.py -v
```
Expected: 2 passed.

- [ ] **Step 1.6: Commit**

```bash
git add src/safe_monitor/storage/migrations/002_x_users.sql \
        src/safe_monitor/storage/db.py \
        tests/integration/test_db_x_users.py
git commit -S -m "feat(x): add x_users + source_status tables and DB helpers"
```

---

## Task 2 — Config schema, Settings, and 47-handle whitelist

**Files:**
- Modify: `src/safe_monitor/config.py`
- Modify: `config.yaml`
- Test: `tests/unit/test_config.py` (extend)

- [ ] **Step 2.1: Write the failing test**

Append to `tests/unit/test_config.py`:

```python
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
```

- [ ] **Step 2.2: Run, expect failure**

```
uv run pytest tests/unit/test_config.py -k test_x_config_loads_handles_with_tiers -v
```
Expected: FAIL (`x` not in `SourcesCfg`).

- [ ] **Step 2.3: Extend config.py**

In `src/safe_monitor/config.py`:

```python
# add near other models
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
    x: XSourceCfg | None = None  # <— add
```

In `class Settings`:

```python
    x_api_key: str = Field("", alias="X_API_KEY")
```

- [ ] **Step 2.4: Run, expect pass**

```
uv run pytest tests/unit/test_config.py -k test_x_config_loads_handles_with_tiers -v
```
Expected: 1 passed.

- [ ] **Step 2.5: Add the 47-handle whitelist to `config.yaml`**

Append (with comment block describing tiers):

```yaml
  x:
    enabled: true
    websocket_url: "wss://ws.twitterapi.io/twitter/tweet/websocket"
    rest_base_url: "https://api.twitterapi.io"
    poll_interval_seconds: 600
    ws_reconnect_min_seconds: 90
    ws_max_consecutive_failures: 5
    degrade_recheck_seconds: 1800
    # Tier S: lone researchers (force severity>=high regardless of text)
    # Tier A: institutional alert handles (force severity>=high)
    # Tier B: on-chain intel feeds (default medium, keyword can promote)
    # Tier C: English media
    # Tier D: Chinese-circle media (apply CN keyword scorer)
    # Tier E: optional / experimental — review before re-enabling
    handles:
      # --- Tier S: 15 individual researchers ---
      - {handle: samczsun,        tier: S}
      - {handle: zachxbt,         tier: S}
      - {handle: pcaversaccio,    tier: S}
      - {handle: tayvano_,        tier: S}
      - {handle: officer_cia,     tier: S}
      - {handle: evilcos,         tier: S}
      - {handle: Mudit__Gupta,    tier: S}
      - {handle: frangio_,        tier: S}
      - {handle: PatrickAlphaC,   tier: S}
      - {handle: pashovkrum,      tier: S}
      - {handle: FrankResearcher, tier: S}
      - {handle: 0xfoobar,        tier: S}
      - {handle: 0xQuit,          tier: S}
      - {handle: DanielVF,        tier: S}
      - {handle: patrickd_,       tier: S}
      # --- Tier A: 15 institutional alert handles ---
      - {handle: PeckShieldAlert, tier: A}
      - {handle: SlowMist_Team,   tier: A}
      - {handle: CertiKAlert,     tier: A}
      - {handle: Cyvers_,         tier: A}
      - {handle: BlockSecTeam,    tier: A}
      - {handle: Hypernative,     tier: A}
      - {handle: BeosinAlert,     tier: A}
      - {handle: realScamSniffer, tier: A}
      - {handle: GoPlusSecurity,  tier: A}
      - {handle: dedaub,          tier: A}
      - {handle: FortaNetwork,    tier: A}
      - {handle: _SEAL_Org,       tier: A}
      - {handle: hexensio,        tier: A}
      - {handle: OtterSec,        tier: A}
      - {handle: NumenCyber,      tier: A}
      # --- Tier B: 5 on-chain intel ---
      - {handle: whale_alert,     tier: B}
      - {handle: ArkhamIntel,     tier: B}
      - {handle: lookonchain,     tier: B}
      - {handle: spotonchain,     tier: B}
      - {handle: nansen_ai,       tier: B}
      # --- Tier C: 5 English media ---
      - {handle: TheBlock__,      tier: C}
      - {handle: DLNewsInfo,      tier: C}
      - {handle: CoinDesk,        tier: C}
      - {handle: decryptmedia,    tier: C}
      - {handle: BanklessHQ,      tier: C}
      # --- Tier D: 7 Chinese-circle ---
      - {handle: WuBlockchain,    tier: D}
      - {handle: ForesightNews,   tier: D}
      - {handle: PANewsCN,        tier: D}
      - {handle: ChainCatcher_,   tier: D}
      - {handle: OdailyChina,     tier: D}
      - {handle: theblockbeats,   tier: D}
      - {handle: hackenclub,      tier: D}
```

> If TwitterAPI.io rejects a handle at bootstrap (renamed/banned), the script logs the failure and continues; you delete or fix the entry and re-run.

- [ ] **Step 2.6: Add `websockets` to deps**

In `pyproject.toml` under `[project.dependencies]`:

```
"websockets>=12,<14",
```

Then:

```
uv sync
```

- [ ] **Step 2.7: Run all unit tests**

```
uv run pytest tests/unit/ -v
```
Expected: existing tests still pass, new test passes.

- [ ] **Step 2.8: Commit**

```bash
git add src/safe_monitor/config.py config.yaml pyproject.toml uv.lock \
        tests/unit/test_config.py
git commit -S -m "feat(x): config schema + 47-handle whitelist + websockets dep"
```

---

## Task 3 — Tweet parser

**Files:**
- Create: `src/safe_monitor/core/parsers/x_tweet.py`
- Test: `tests/unit/test_parsers_x_tweet.py`
- Fixtures: `tests/fixtures/x_tweet_ws_fast_tweet.json`, `tests/fixtures/x_tweet_rest_response.json`

> The TwitterAPI.io tweet object schema is not 100 % stable across endpoints. The parser **must** treat all fields as optional except `id`/`id_str` and tolerate missing keys. The fixtures encode the minimum we rely on; expand them after capturing real payloads in Task 8.

- [ ] **Step 3.1: Create fixtures with the minimum we rely on**

`tests/fixtures/x_tweet_ws_fast_tweet.json`:
```json
{
  "event_type": "fast_tweet",
  "timestamp": 1746694117000,
  "snow_delay_ms": 420,
  "tweet": {
    "id_str": "1789012345678901234",
    "text": "EXPLOIT in progress on protocol X — drained ~$12M, attacker addr 0xabc...",
    "created_at": "Thu May 08 13:28:37 +0000 2026",
    "user": {"id_str": "12345", "screen_name": "samczsun", "name": "samczsun"},
    "entities": {"urls": [{"expanded_url": "https://x.com/samczsun/status/1789012345678901234"}]}
  }
}
```

`tests/fixtures/x_tweet_rest_response.json`:
```json
{
  "tweets": [
    {
      "id_str": "1789012345678901111",
      "text": "@PeckShieldAlert reports flashloan attack on Y, ~$3M loss.",
      "created_at": "Thu May 08 12:10:00 +0000 2026",
      "user": {"id_str": "67890", "screen_name": "PeckShieldAlert", "name": "PeckShield Alert"}
    }
  ]
}
```

- [ ] **Step 3.2: Write the failing tests**

```python
# tests/unit/test_parsers_x_tweet.py
import json
from pathlib import Path
from safe_monitor.core.parsers.x_tweet import parse_x_tweet
from safe_monitor.core.models import Severity


def _load(name: str) -> dict:
    return json.loads(Path(f"tests/fixtures/{name}").read_text())


def test_parse_ws_fast_tweet_extracts_url_and_categorizes():
    raw = _load("x_tweet_ws_fast_tweet.json")["tweet"]
    p = parse_x_tweet(raw, tier="S")
    assert p["title"].startswith("@samczsun")
    assert "EXPLOIT" in p["body"]
    assert p["url"] == "https://x.com/samczsun/status/1789012345678901234"
    assert "a" in p["category"]                  # exploit -> protocol/contract
    # Tier S forces high
    assert p["severity"] == Severity.high


def test_parse_rest_tweet_no_entities_falls_back_to_canonical_url():
    raw = _load("x_tweet_rest_response.json")["tweets"][0]
    p = parse_x_tweet(raw, tier="A")
    assert p["url"] == "https://x.com/PeckShieldAlert/status/1789012345678901111"
    assert p["severity"] == Severity.high        # tier A also forces high


def test_parse_tier_b_uses_keyword_scorer():
    raw = {
        "id_str": "999",
        "text": "Big transfer detected: 100,000 ETH",
        "user": {"id_str": "1", "screen_name": "whale_alert", "name": "Whale Alert"},
    }
    p = parse_x_tweet(raw, tier="B")
    # No exploit/sanction keywords -> low; severity hint is None
    # so normalizer will fall through to the keyword scorer.
    assert p["severity"] is None


def test_parse_chinese_tier_d_emits_zh_keyword_match():
    raw = {
        "id_str": "1000",
        "text": "突发：某协议被黑，资金被盗约 500 万美元",
        "user": {"id_str": "2", "screen_name": "WuBlockchain", "name": "吴说"},
    }
    p = parse_x_tweet(raw, tier="D")
    # Tier D is medium-floor; severity hint is None so scorer runs and
    # the CN keyword "被黑" promotes to high.
    assert p["severity"] is None
    assert p["title"].startswith("@WuBlockchain")


def test_parse_missing_id_raises():
    import pytest as _pt
    with _pt.raises(ValueError):
        parse_x_tweet({"text": "hello"}, tier="S")
```

- [ ] **Step 3.3: Run, expect failure**

```
uv run pytest tests/unit/test_parsers_x_tweet.py -v
```
Expected: ImportError.

- [ ] **Step 3.4: Implement parser**

```python
# src/safe_monitor/core/parsers/x_tweet.py
from __future__ import annotations

from typing import Any

from safe_monitor.core.models import Severity

_HIGH_TIERS = frozenset({"S", "A"})


def _categorize(text: str) -> list[str]:
    blob = text.lower()
    out: list[str] = []
    if any(w in blob for w in ("exploit", "hack", "drain", "stolen", "被盗", "被黑")):
        out.append("a")
    if "bridge" in blob or "cross-chain" in blob:
        out.append("b")
    if any(w in blob for w in ("phishing", "drainer", "approval", "钓鱼", "盗号")):
        out.append("e")
    if "rug" in blob or "rugpull" in blob or "跑路" in blob:
        out.append("g")
    if "stablecoin" in blob or "depeg" in blob or "脱锚" in blob:
        out.append("f")
    if "sanction" in blob or "ofac" in blob or "制裁" in blob:
        out.append("h")
    return out


def _canonical_url(user_screen: str, tweet_id: str) -> str:
    return f"https://x.com/{user_screen}/status/{tweet_id}"


def parse_x_tweet(tweet: dict[str, Any], *, tier: str) -> dict[str, Any]:
    """Parse a TwitterAPI.io tweet object (WS or REST shape).

    `tier` controls the severity hint:
      S/A  -> Severity.high  (skip keyword scorer)
      else -> None           (normalizer falls back to keyword scorer)
    """
    tid = tweet.get("id_str") or tweet.get("id")
    if not tid:
        raise ValueError("tweet missing id_str / id")
    tid = str(tid)

    user = tweet.get("user") or {}
    screen = user.get("screen_name") or user.get("username") or "unknown"
    text = (tweet.get("text") or tweet.get("full_text") or "").strip()

    # Prefer the canonical x.com URL — entities[urls] is for embedded links,
    # not the tweet's own permalink.
    url = _canonical_url(screen, tid)

    title = f"@{screen}: " + (text[:80] + ("…" if len(text) > 80 else ""))

    severity: Severity | None = Severity.high if tier in _HIGH_TIERS else None

    return {
        "title": title,
        "body": text,
        "url": url,
        "category": _categorize(text),
        "severity": severity,
        "user_screen_name": screen,
    }
```

- [ ] **Step 3.5: Run, expect pass**

```
uv run pytest tests/unit/test_parsers_x_tweet.py -v
```
Expected: 5 passed.

- [ ] **Step 3.6: Commit**

```bash
git add src/safe_monitor/core/parsers/x_tweet.py tests/unit/test_parsers_x_tweet.py \
        tests/fixtures/x_tweet_ws_fast_tweet.json tests/fixtures/x_tweet_rest_response.json
git commit -S -m "feat(x): tweet parser with tier-aware severity hint"
```

---

## Task 4 — Wire parser into normalizer (`source_kind="x"`)

**Files:**
- Modify: `src/safe_monitor/core/models.py`
- Modify: `src/safe_monitor/core/normalizer.py`
- Test: `tests/unit/test_normalizer.py` (extend)

- [ ] **Step 4.1: Write the failing test**

Append to `tests/unit/test_normalizer.py`:

```python
def test_normalizer_routes_x_source_kind_to_x_parser():
    from datetime import datetime, UTC
    from safe_monitor.core.models import RawEvent, Severity
    from safe_monitor.core.normalizer import Normalizer

    raw = RawEvent(
        source="x_websocket",
        source_kind="x",
        external_id="1789012345678901234",
        received_at=datetime.now(UTC),
        raw={
            "id_str": "1789012345678901234",
            "text": "EXPLOIT on Foo: $5M drained",
            "user": {"id_str": "1", "screen_name": "samczsun"},
            "_tier": "S",  # injected by source so normalizer can read tier
        },
        text="EXPLOIT on Foo: $5M drained",
    )
    ev = Normalizer().normalize(raw)
    assert ev is not None
    assert ev.severity == Severity.high
    assert ev.url == "https://x.com/samczsun/status/1789012345678901234"
    assert "a" in [c.value if hasattr(c, 'value') else c for c in ev.category]
```

- [ ] **Step 4.2: Run, expect failure**

```
uv run pytest tests/unit/test_normalizer.py -k test_normalizer_routes_x -v
```
Expected: FAIL (`source_kind` literal doesn't include `"x"`).

- [ ] **Step 4.3: Extend `RawEvent.source_kind` literal**

In `src/safe_monitor/core/models.py`:

```python
    source_kind: Literal["tg", "api", "x"]
```

- [ ] **Step 4.4: Route in normalizer**

In `src/safe_monitor/core/normalizer.py`, before the `else: return None`:

```python
        elif raw.source_kind == "x":
            from safe_monitor.core.parsers.x_tweet import parse_x_tweet
            tier = (raw.raw.get("_tier") or "E")
            parsed = parse_x_tweet(raw.raw, tier=tier)
```

- [ ] **Step 4.5: Run, expect pass**

```
uv run pytest tests/unit/test_normalizer.py -v
```
Expected: all green.

- [ ] **Step 4.6: Commit**

```bash
git add src/safe_monitor/core/models.py src/safe_monitor/core/normalizer.py \
        tests/unit/test_normalizer.py
git commit -S -m "feat(x): normalizer routes source_kind=x via tier-aware parser"
```

---

## Task 5 — TwitterAPI.io REST client

**Files:**
- Create: `src/safe_monitor/sources/x_client.py`
- Test: `tests/unit/test_x_client.py`

> Two endpoints we need (verified at https://docs.twitterapi.io). If the path differs at runtime, the client raises with the actual response body so the engineer can fix the URL in one place.

- [ ] **Step 5.1: Write the failing test**

```python
# tests/unit/test_x_client.py
import pytest
import respx
from httpx import Response
from safe_monitor.sources.x_client import TwitterApiIoClient


@pytest.mark.asyncio
async def test_resolve_handle_returns_user_id():
    client = TwitterApiIoClient(api_key="k", base_url="https://api.twitterapi.io")
    with respx.mock(assert_all_called=True) as mock:
        mock.get(
            "https://api.twitterapi.io/twitter/user/info",
            params={"userName": "samczsun"},
        ).mock(return_value=Response(200, json={"data": {"id": "12345", "userName": "samczsun"}}))
        uid = await client.resolve_handle("samczsun")
    assert uid == "12345"


@pytest.mark.asyncio
async def test_resolve_handle_unknown_returns_none():
    client = TwitterApiIoClient(api_key="k", base_url="https://api.twitterapi.io")
    with respx.mock(assert_all_called=True) as mock:
        mock.get(
            "https://api.twitterapi.io/twitter/user/info",
            params={"userName": "nope"},
        ).mock(return_value=Response(404, json={"error": "not found"}))
        uid = await client.resolve_handle("nope")
    assert uid is None


@pytest.mark.asyncio
async def test_get_last_tweets_returns_list_and_filters_since_id():
    client = TwitterApiIoClient(api_key="k", base_url="https://api.twitterapi.io")
    payload = {"tweets": [
        {"id_str": "100", "text": "old"},
        {"id_str": "200", "text": "new"},
    ]}
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.twitterapi.io/twitter/user/last_tweets").mock(
            return_value=Response(200, json=payload)
        )
        out = await client.get_last_tweets(user_id="42", since_id="150")
    assert [t["id_str"] for t in out] == ["200"]


@pytest.mark.asyncio
async def test_402_raises_credits_exhausted():
    client = TwitterApiIoClient(api_key="k", base_url="https://api.twitterapi.io")
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.twitterapi.io/twitter/user/info").mock(
            return_value=Response(402, text="payment required")
        )
        with pytest.raises(TwitterApiIoClient.CreditsExhausted):
            await client.resolve_handle("anyone")
```

- [ ] **Step 5.2: Run, expect failure**

```
uv run pytest tests/unit/test_x_client.py -v
```
Expected: ImportError.

- [ ] **Step 5.3: Implement the client**

```python
# src/safe_monitor/sources/x_client.py
from __future__ import annotations

from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)


class TwitterApiIoClient:
    """Thin async client over TwitterAPI.io REST endpoints.

    Header name is lowercase 'x-api-key' (also accepted as X-API-Key).
    All methods are idempotent and safe to retry.
    """

    class CreditsExhausted(Exception):
        pass

    class TransientError(Exception):
        pass

    def __init__(self, *, api_key: str, base_url: str, timeout: float = 20.0):
        self._api_key = api_key
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._api_key, "accept": "application/json"}

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(url, headers=self._headers(), params=params)
        if r.status_code == 402:
            raise self.CreditsExhausted(r.text[:300])
        if r.status_code == 429:
            raise self.TransientError(f"429 rate limited: {r.text[:200]}")
        if r.status_code == 404:
            return {}
        if r.status_code >= 500:
            raise self.TransientError(f"{r.status_code}: {r.text[:200]}")
        r.raise_for_status()
        return r.json()

    async def resolve_handle(self, handle: str) -> str | None:
        """Return user_id for a screen_name, or None if not found."""
        data = await self._get("/twitter/user/info", {"userName": handle})
        if not data:
            return None
        node = data.get("data") or data
        uid = node.get("id") or node.get("id_str") or node.get("user_id")
        return str(uid) if uid else None

    async def get_last_tweets(
        self, *, user_id: str, since_id: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Fetch recent tweets by a user. Filters client-side by since_id
        because TwitterAPI.io's filter param name has shifted historically;
        a local int compare is robust."""
        data = await self._get(
            "/twitter/user/last_tweets",
            {"userId": user_id, "limit": limit},
        )
        tweets = data.get("tweets") or data.get("data") or []
        if since_id is None:
            return list(tweets)
        try:
            cutoff = int(since_id)
        except ValueError:
            return list(tweets)
        return [t for t in tweets if int(t.get("id_str") or t.get("id") or 0) > cutoff]
```

- [ ] **Step 5.4: Run, expect pass**

```
uv run pytest tests/unit/test_x_client.py -v
```
Expected: 4 passed.

- [ ] **Step 5.5: Commit**

```bash
git add src/safe_monitor/sources/x_client.py tests/unit/test_x_client.py
git commit -S -m "feat(x): TwitterAPI.io REST client with credit/rate-limit handling"
```

---

## Task 6 — Bootstrap script: resolve handles → user_ids

**Files:**
- Create: `scripts/resolve_x_handles.py`
- Test: smoke run

- [ ] **Step 6.1: Write the script**

```python
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
```

- [ ] **Step 6.2: Make executable**

```bash
chmod +x scripts/resolve_x_handles.py
```

- [ ] **Step 6.3: Smoke run (requires X_API_KEY in .env)**

```bash
uv run python scripts/resolve_x_handles.py
```
Expected: structured logs `resolved=...`. Spot-check the DB:

```bash
sqlite3 data/safe_monitor.db 'SELECT handle, user_id, tier FROM x_users ORDER BY tier, handle;'
```
Expected: most of 47 rows present. If a handle is missing/banned, fix the spelling in `config.yaml` and re-run — it's idempotent.

- [ ] **Step 6.4: Commit**

```bash
git add scripts/resolve_x_handles.py
git commit -S -m "feat(x): bootstrap script — resolve handles to user_ids"
```

---

## Task 7 — Polling source (REST backfill)

**Files:**
- Create: `src/safe_monitor/sources/x_polling.py`
- Test: `tests/integration/test_x_polling_source.py`

- [ ] **Step 7.1: Write the failing test**

```python
# tests/integration/test_x_polling_source.py
import asyncio
from pathlib import Path

import pytest
import respx
from httpx import Response

from safe_monitor.sources.x_polling import XPollingSource
from safe_monitor.storage.db import Database


@pytest.mark.asyncio
async def test_polls_each_user_emits_only_new_tweets(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    await db.init()
    await db.upsert_x_user(handle="samczsun", user_id="111", tier="S")
    await db.upsert_x_user(handle="WuBlockchain", user_id="222", tier="D")

    src = XPollingSource(
        api_key="k",
        rest_base_url="https://api.twitterapi.io",
        poll_interval_seconds=1,
        db=db,
    )
    q: asyncio.Queue = asyncio.Queue()

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.twitterapi.io/twitter/user/last_tweets",
                 params={"userId": "111", "limit": 20}).mock(
            return_value=Response(200, json={"tweets": [
                {"id_str": "1001", "text": "samczsun first", "user": {"id_str": "111", "screen_name": "samczsun"}},
            ]})
        )
        mock.get("https://api.twitterapi.io/twitter/user/last_tweets",
                 params={"userId": "222", "limit": 20}).mock(
            return_value=Response(200, json={"tweets": [
                {"id_str": "2001", "text": "wu first", "user": {"id_str": "222", "screen_name": "WuBlockchain"}},
            ]})
        )
        await src.poll_once(q)

    got = []
    while not q.empty():
        got.append(await q.get())
    assert {e.external_id for e in got} == {"1001", "2001"}
    # Tier S tweet carries _tier=S; Tier D carries _tier=D
    assert {e.raw["_tier"] for e in got} == {"S", "D"}

    # last_seen advanced
    rows = {u["user_id"]: u for u in await db.list_x_users()}
    assert rows["111"]["last_seen_id"] == "1001"
    assert rows["222"]["last_seen_id"] == "2001"

    # Re-poll with same response → no new emissions
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.twitterapi.io/twitter/user/last_tweets",
                 params={"userId": "111", "limit": 20}).mock(
            return_value=Response(200, json={"tweets": [
                {"id_str": "1001", "text": "same", "user": {"id_str": "111", "screen_name": "samczsun"}},
            ]})
        )
        mock.get("https://api.twitterapi.io/twitter/user/last_tweets",
                 params={"userId": "222", "limit": 20}).mock(
            return_value=Response(200, json={"tweets": [
                {"id_str": "2001", "text": "same", "user": {"id_str": "222", "screen_name": "WuBlockchain"}},
            ]})
        )
        await src.poll_once(q)
    assert q.empty()
    await db.close()


@pytest.mark.asyncio
async def test_skips_polling_when_degraded(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    await db.init()
    await db.upsert_x_user(handle="samczsun", user_id="111", tier="S")
    await db.set_degraded("x_polling", True, reason="manual")

    src = XPollingSource(
        api_key="k",
        rest_base_url="https://api.twitterapi.io",
        poll_interval_seconds=1,
        db=db,
    )
    q: asyncio.Queue = asyncio.Queue()
    # No respx mock — if it tried to hit the network the test would fail.
    await src.poll_once(q)
    assert q.empty()
    await db.close()


@pytest.mark.asyncio
async def test_402_marks_degraded(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    await db.init()
    await db.upsert_x_user(handle="samczsun", user_id="111", tier="S")

    src = XPollingSource(
        api_key="k",
        rest_base_url="https://api.twitterapi.io",
        poll_interval_seconds=1,
        db=db,
    )
    q: asyncio.Queue = asyncio.Queue()
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.twitterapi.io/twitter/user/last_tweets",
                 params={"userId": "111", "limit": 20}).mock(
            return_value=Response(402, text="no credits")
        )
        await src.poll_once(q)
    assert await db.is_degraded("x_polling") is True
    await db.close()
```

- [ ] **Step 7.2: Run, expect failure**

```
uv run pytest tests/integration/test_x_polling_source.py -v
```
Expected: ImportError.

- [ ] **Step 7.3: Implement the polling source**

```python
# src/safe_monitor/sources/x_polling.py
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog

from safe_monitor.core.models import RawEvent
from safe_monitor.sources.base import Source
from safe_monitor.sources.x_client import TwitterApiIoClient
from safe_monitor.storage.db import Database

log = structlog.get_logger(__name__)


class XPollingSource(Source):
    """Per-user backfill poller. Fills the gap during WS reconnects."""

    name = "x_polling"

    def __init__(
        self,
        *,
        api_key: str,
        rest_base_url: str,
        poll_interval_seconds: int,
        db: Database,
    ):
        self._api_key = api_key
        self._base = rest_base_url
        self._interval = poll_interval_seconds
        self._db = db
        self._client = TwitterApiIoClient(api_key=api_key, base_url=rest_base_url)

    async def poll_once(self, sink: asyncio.Queue[RawEvent]) -> None:
        if await self._db.is_degraded(self.name):
            log.info("x_polling.skip_degraded")
            return
        users = await self._db.list_x_users()
        for u in users:
            try:
                tweets = await self._client.get_last_tweets(
                    user_id=u["user_id"], since_id=u["last_seen_id"]
                )
            except TwitterApiIoClient.CreditsExhausted as e:
                await self._db.set_degraded(self.name, True, reason=f"402: {e}")
                log.warning("x_polling.degraded_credits", user=u["handle"])
                return
            except TwitterApiIoClient.TransientError as e:
                log.warning("x_polling.transient", user=u["handle"], error=str(e))
                continue
            except Exception as e:
                log.warning("x_polling.error", user=u["handle"], error=str(e))
                continue
            if not tweets:
                continue
            # tweets are newest-first; emit oldest-first so cursor moves monotonically
            tweets_sorted = sorted(tweets, key=lambda t: int(t.get("id_str") or t.get("id") or 0))
            for t in tweets_sorted:
                tid = str(t.get("id_str") or t.get("id"))
                # Tag tier so the normalizer can read it without another DB lookup
                payload = dict(t)
                payload["_tier"] = u["tier"]
                ev = RawEvent(
                    source=self.name,
                    source_kind="x",
                    external_id=tid,
                    received_at=datetime.now(UTC),
                    raw=payload,
                    text=t.get("text") or t.get("full_text") or "",
                )
                await sink.put(ev)
            newest_id = str(tweets_sorted[-1].get("id_str") or tweets_sorted[-1].get("id"))
            await self._db.set_x_user_last_seen(u["user_id"], newest_id)

    async def run(self, sink: asyncio.Queue[RawEvent]) -> None:
        while True:
            try:
                await self.poll_once(sink)
            except Exception as e:
                log.warning("x_polling.run_error", error=str(e))
            await asyncio.sleep(self._interval)
```

- [ ] **Step 7.4: Run, expect pass**

```
uv run pytest tests/integration/test_x_polling_source.py -v
```
Expected: 3 passed.

- [ ] **Step 7.5: Commit**

```bash
git add src/safe_monitor/sources/x_polling.py tests/integration/test_x_polling_source.py
git commit -S -m "feat(x): REST polling source (backfill) with degrade on 402"
```

---

## Task 8 — WebSocket source (happy path + reconnect + degrade)

**Files:**
- Create: `src/safe_monitor/sources/x_websocket.py`
- Test: `tests/integration/test_x_websocket_source.py`

> The WS spec: `wss://ws.twitterapi.io/twitter/tweet/websocket`, header `x-api-key`, one connection per key, ≥90 s wait before reconnect after disconnect, no replay.
>
> The subscription wire format (how to send the `follow` rule list) is **not** detailed in the public docs we read. The implementation **must** capture the actual handshake from a real first run: connect once, log every frame in/out, paste the verified frames into the test fixture, and finalize the implementation. Treat Step 8.4 as "verify against live server first, then re-test".

- [ ] **Step 8.1: Write the failing tests against a local mock WS server**

```python
# tests/integration/test_x_websocket_source.py
import asyncio
import json
from pathlib import Path

import pytest
import websockets

from safe_monitor.sources.x_websocket import XWebSocketSource
from safe_monitor.storage.db import Database


async def _start_mock_ws(handler):
    return await websockets.serve(handler, "127.0.0.1", 0, process_request=None)


@pytest.mark.asyncio
async def test_emits_fast_tweet_event(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    await db.init()
    await db.upsert_x_user(handle="samczsun", user_id="111", tier="S")

    received_subs: list[dict] = []
    fast_tweet = json.loads(Path("tests/fixtures/x_tweet_ws_fast_tweet.json").read_text())
    fast_tweet["tweet"]["user"]["id_str"] = "111"  # match our DB user

    async def handler(ws):
        # Capture the subscription frame
        sub_msg = await ws.recv()
        received_subs.append(json.loads(sub_msg))
        # Push a single fast_tweet event then close
        await ws.send(json.dumps(fast_tweet))
        await asyncio.sleep(0.05)
        await ws.close()

    server = await _start_mock_ws(handler)
    host, port = server.sockets[0].getsockname()[:2]
    url = f"ws://{host}:{port}"

    src = XWebSocketSource(
        api_key="k", websocket_url=url,
        ws_reconnect_min_seconds=1, ws_max_consecutive_failures=3,
        db=db,
    )
    q: asyncio.Queue = asyncio.Queue()

    task = asyncio.create_task(src.run(q))
    # let it connect, receive, and the server close it
    await asyncio.sleep(0.5)
    src.request_stop()
    await asyncio.wait_for(task, timeout=3)
    server.close()
    await server.wait_closed()

    # Verify subscription contained our user_id
    assert received_subs, "client did not send subscription"
    sub_str = json.dumps(received_subs[0])
    assert "111" in sub_str

    # Verify event emitted
    got = []
    while not q.empty():
        got.append(await q.get())
    assert len(got) == 1
    assert got[0].source_kind == "x"
    assert got[0].raw["_tier"] == "S"
    assert got[0].raw["id_str"] == "1789012345678901234"
    await db.close()


@pytest.mark.asyncio
async def test_marks_degraded_after_n_consecutive_failures(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    await db.init()

    src = XWebSocketSource(
        api_key="k",
        websocket_url="ws://127.0.0.1:1",  # nothing listens
        ws_reconnect_min_seconds=0,
        ws_max_consecutive_failures=3,
        db=db,
    )
    q: asyncio.Queue = asyncio.Queue()

    task = asyncio.create_task(src.run(q))
    # Enough time for 3 reconnects to fail
    await asyncio.sleep(0.5)
    src.request_stop()
    await asyncio.wait_for(task, timeout=3)

    assert await db.is_degraded(src.name) is True
    await db.close()
```

- [ ] **Step 8.2: Run, expect failure**

```
uv run pytest tests/integration/test_x_websocket_source.py -v
```
Expected: ImportError.

- [ ] **Step 8.3: Implement the WS source**

```python
# src/safe_monitor/sources/x_websocket.py
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import structlog
import websockets
from websockets.exceptions import ConnectionClosed

from safe_monitor.core.models import RawEvent
from safe_monitor.sources.base import Source
from safe_monitor.storage.db import Database

log = structlog.get_logger(__name__)


class XWebSocketSource(Source):
    name = "x_websocket"

    def __init__(
        self,
        *,
        api_key: str,
        websocket_url: str,
        ws_reconnect_min_seconds: int,
        ws_max_consecutive_failures: int,
        db: Database,
    ):
        self._api_key = api_key
        self._url = websocket_url
        self._reconnect_min = ws_reconnect_min_seconds
        self._max_fails = ws_max_consecutive_failures
        self._db = db
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    def _build_subscription(self, user_ids: list[str]) -> dict[str, Any]:
        # NOTE: TwitterAPI.io documents `follow: [user_id, ...]` as the rule
        # vocabulary. Wire format below is best-effort; verify against a real
        # connection and update if the server rejects it.
        return {"action": "subscribe", "rules": [{"follow": user_ids}]}

    async def _user_id_to_tier(self) -> dict[str, str]:
        rows = await self._db.list_x_users()
        return {r["user_id"]: r["tier"] for r in rows}

    async def _handle_payload(
        self, payload: dict[str, Any], tier_map: dict[str, str], sink: asyncio.Queue[RawEvent]
    ) -> None:
        et = payload.get("event_type")
        tweets: list[dict[str, Any]] = []
        if et == "fast_tweet":
            t = payload.get("tweet")
            if t:
                tweets.append(t)
        elif et == "tweet":
            tweets.extend(payload.get("tweets") or [])
        else:
            log.debug("x_ws.unknown_event_type", payload=payload)
            return

        for t in tweets:
            user = (t.get("user") or {})
            uid = str(user.get("id_str") or user.get("id") or "")
            tier = tier_map.get(uid, "E")
            tid = str(t.get("id_str") or t.get("id") or "")
            if not tid:
                continue
            tagged = dict(t)
            tagged["_tier"] = tier
            ev = RawEvent(
                source=self.name,
                source_kind="x",
                external_id=tid,
                received_at=datetime.now(UTC),
                raw=tagged,
                text=t.get("text") or t.get("full_text") or "",
            )
            await sink.put(ev)

    async def _one_connection(self, sink: asyncio.Queue[RawEvent]) -> None:
        tier_map = await self._user_id_to_tier()
        user_ids = list(tier_map.keys())
        if not user_ids:
            log.warning("x_ws.no_users_resolved")
            await asyncio.sleep(5)
            return

        async with websockets.connect(
            self._url, additional_headers={"x-api-key": self._api_key}
        ) as ws:
            await ws.send(json.dumps(self._build_subscription(user_ids)))
            log.info("x_ws.connected", users=len(user_ids))

            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=60)
                except asyncio.TimeoutError:
                    # No traffic within 60s: send ping to keep the conn alive.
                    try:
                        pong_waiter = await ws.ping()
                        await asyncio.wait_for(pong_waiter, timeout=10)
                    except (asyncio.TimeoutError, ConnectionClosed):
                        return
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("x_ws.bad_json", raw=raw[:200])
                    continue
                await self._handle_payload(payload, tier_map, sink)

    async def run(self, sink: asyncio.Queue[RawEvent]) -> None:
        consecutive = 0
        while not self._stop.is_set():
            if await self._db.is_degraded(self.name):
                log.info("x_ws.skip_degraded")
                await asyncio.sleep(min(60, max(self._reconnect_min, 5)))
                continue
            try:
                await self._one_connection(sink)
                consecutive = 0
                # Server closed cleanly: still wait the mandatory cooldown.
                if not self._stop.is_set():
                    await asyncio.sleep(self._reconnect_min)
            except Exception as e:
                consecutive += 1
                log.warning("x_ws.failure", attempt=consecutive, error=str(e))
                if consecutive >= self._max_fails:
                    await self._db.set_degraded(
                        self.name, True, reason=f"{consecutive} consecutive failures: {e}"
                    )
                    log.error("x_ws.degraded", consecutive=consecutive)
                    consecutive = 0
                # Backoff anyway
                await asyncio.sleep(self._reconnect_min)
```

- [ ] **Step 8.4: Run, expect pass**

```
uv run pytest tests/integration/test_x_websocket_source.py -v
```
Expected: 2 passed.

- [ ] **Step 8.5: Live verification (manual, after Pre-flight + Task 6 done)**

Bring up only the WS source and watch its first frames:

```bash
LOG_LEVEL=DEBUG uv run python -c "
import asyncio
from safe_monitor.config import load_settings
from safe_monitor.sources.x_websocket import XWebSocketSource
from safe_monitor.storage.db import Database

async def go():
    s = load_settings()
    db = Database(s.db_path); await db.init()
    src = XWebSocketSource(
        api_key=s.x_api_key,
        websocket_url=s.config.sources.x.websocket_url,
        ws_reconnect_min_seconds=90,
        ws_max_consecutive_failures=5,
        db=db,
    )
    q = asyncio.Queue()
    t = asyncio.create_task(src.run(q))
    for _ in range(3):
        ev = await asyncio.wait_for(q.get(), timeout=300)
        print('GOT', ev.source_kind, ev.raw.get('user',{}).get('screen_name'), ev.text[:80])
    src.request_stop(); await t

asyncio.run(go())
"
```

If the server rejects the subscription frame, capture its error, update `_build_subscription`, save the verified payload to `tests/fixtures/x_ws_subscribe_request.json`, and re-run Task 8.4 to make the unit test reflect reality.

- [ ] **Step 8.6: Commit**

```bash
git add src/safe_monitor/sources/x_websocket.py tests/integration/test_x_websocket_source.py
git commit -S -m "feat(x): WebSocket streaming source with reconnect + degrade FSM"
```

---

## Task 9 — Wire X sources into `main.py`

**Files:**
- Modify: `src/safe_monitor/main.py`
- Test: smoke run

- [ ] **Step 9.1: Extend `_build_sources`**

Append inside `_build_sources` (after the telegram block, before `return sources`):

```python
    xcfg = cfg.sources.x
    if xcfg and xcfg.enabled:
        from safe_monitor.sources.x_polling import XPollingSource
        from safe_monitor.sources.x_websocket import XWebSocketSource

        if not settings_x_api_key:
            import structlog as _sl
            _sl.get_logger("main").warning("x.skipped_no_api_key")
        else:
            sources.append(
                XWebSocketSource(
                    api_key=settings_x_api_key,
                    websocket_url=xcfg.websocket_url,
                    ws_reconnect_min_seconds=xcfg.ws_reconnect_min_seconds,
                    ws_max_consecutive_failures=xcfg.ws_max_consecutive_failures,
                    db=db,
                )
            )
            sources.append(
                XPollingSource(
                    api_key=settings_x_api_key,
                    rest_base_url=xcfg.rest_base_url,
                    poll_interval_seconds=xcfg.poll_interval_seconds,
                    db=db,
                )
            )
```

Adjust the `_build_sources` signature to accept `settings_x_api_key: str` and pass it from `_amain`:

```python
    sources = await _build_sources(
        settings.config,
        db,
        settings_api_id=settings.tg_api_id,
        settings_api_hash=settings.tg_api_hash,
        settings_session=settings.tg_userbot_session,
        settings_phone=settings.tg_userbot_phone,
        settings_tg_mode=settings.tg_ingest_mode,
        settings_rsshub_base=settings.rsshub_base_url,
        settings_x_api_key=settings.x_api_key,
    )
```

- [ ] **Step 9.2: Smoke run**

Make sure Task 6's bootstrap has populated `x_users`, then run the full app for 60 s:

```bash
timeout 60 uv run python -m safe_monitor.main || true
```
Expected log lines:
- `safe_monitor.start sources=[..., 'x_websocket', 'x_polling']`
- `x_ws.connected users=NN`

If you see `x.skipped_no_api_key`, the `.env` isn't loading — fix and rerun.

- [ ] **Step 9.3: Commit**

```bash
git add src/safe_monitor/main.py
git commit -S -m "feat(x): wire WebSocket + polling sources into main pipeline"
```

---

## Task 10 — Recovery probe (auto un-degrade)

**Files:**
- Modify: `src/safe_monitor/core/scheduler.py` (add periodic job)
- Test: `tests/integration/test_x_recovery.py`

- [ ] **Step 10.1: Write the failing test**

```python
# tests/integration/test_x_recovery.py
import pytest
from pathlib import Path
from safe_monitor.storage.db import Database
from safe_monitor.core.scheduler import x_recovery_probe


@pytest.mark.asyncio
async def test_recovery_probe_clears_degrade_when_credits_present(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "x.db"); await db.init()
    await db.set_degraded("x_websocket", True, reason="test")
    await db.set_degraded("x_polling",   True, reason="test")

    async def fake_check_credits(api_key: str, base_url: str) -> bool:
        return True
    monkeypatch.setattr("safe_monitor.core.scheduler._check_credits", fake_check_credits)

    await x_recovery_probe(db, api_key="k", base_url="https://api.twitterapi.io")
    assert await db.is_degraded("x_websocket") is False
    assert await db.is_degraded("x_polling") is False
    await db.close()


@pytest.mark.asyncio
async def test_recovery_probe_no_op_when_credits_absent(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "x.db"); await db.init()
    await db.set_degraded("x_websocket", True, reason="test")

    async def fake_check_credits(api_key: str, base_url: str) -> bool:
        return False
    monkeypatch.setattr("safe_monitor.core.scheduler._check_credits", fake_check_credits)

    await x_recovery_probe(db, api_key="k", base_url="https://api.twitterapi.io")
    assert await db.is_degraded("x_websocket") is True
    await db.close()
```

- [ ] **Step 10.2: Run, expect failure**

```
uv run pytest tests/integration/test_x_recovery.py -v
```
Expected: ImportError.

- [ ] **Step 10.3: Add probe + scheduler hook**

Append to `src/safe_monitor/core/scheduler.py` (and register a periodic job in `build_scheduler`):

```python
from safe_monitor.sources.x_client import TwitterApiIoClient


async def _check_credits(api_key: str, base_url: str) -> bool:
    """Best-effort: a cheap GET that costs ~$0.00015 — succeeds = creds OK."""
    client = TwitterApiIoClient(api_key=api_key, base_url=base_url)
    try:
        await client.resolve_handle("twitter")  # ubiquitous handle
        return True
    except TwitterApiIoClient.CreditsExhausted:
        return False
    except Exception:
        return False


async def x_recovery_probe(db, api_key: str, base_url: str) -> None:
    if not (await db.is_degraded("x_websocket") or await db.is_degraded("x_polling")):
        return
    if await _check_credits(api_key, base_url):
        await db.set_degraded("x_websocket", False)
        await db.set_degraded("x_polling", False)
```

In `build_scheduler`, register a job that runs every `degrade_recheck_seconds` (read from config) calling `x_recovery_probe(db, api_key, base_url)`. Wire `api_key` + `base_url` through `build_scheduler`'s signature; pass them from `main._amain`.

- [ ] **Step 10.4: Run, expect pass**

```
uv run pytest tests/integration/test_x_recovery.py -v
```
Expected: 2 passed.

- [ ] **Step 10.5: Commit**

```bash
git add src/safe_monitor/core/scheduler.py src/safe_monitor/main.py \
        tests/integration/test_x_recovery.py
git commit -S -m "feat(x): recovery probe — auto-clear degrade flag when creds OK"
```

---

## Task 11 — Documentation: DEPLOY.md

**Files:**
- Modify: `DEPLOY.md`

- [ ] **Step 11.1: Append a section**

```markdown
## X (Twitter) ingestion (TwitterAPI.io)

1. Sign up at https://twitterapi.io/ and top up at least $5 of credits.
2. Add to `.env`:
   ```
   X_API_KEY=...
   ```
3. Bootstrap the user list (idempotent, safe to re-run):
   ```
   uv run python scripts/resolve_x_handles.py
   ```
4. Verify:
   ```
   sqlite3 data/safe_monitor.db 'SELECT COUNT(*) FROM x_users;'   # expect ~47
   ```
5. Re-run `safe_monitor` — you should see `x_websocket` and `x_polling` in
   the source list.

**Degrade behaviour.** If TwitterAPI.io returns `402` (no credits) or the
WebSocket fails to reconnect 5× in a row, the X sources self-disable; the
rest of the pipeline (TG mirrors, RSS, Forta, OFAC) keeps running. The
scheduler probes for recovery every 30 min and re-enables automatically.

**Editing the whitelist.** Add/remove rows under `sources.x.handles` in
`config.yaml`, then re-run `scripts/resolve_x_handles.py`. To force-refresh
an already-resolved handle (e.g. account renamed), pass `--force`.
```

- [ ] **Step 11.2: Commit**

```bash
git add DEPLOY.md
git commit -S -m "docs(x): runbook for TwitterAPI.io ingestion + degrade behaviour"
```

---

## Self-Review Checklist (run before declaring done)

- [ ] **Spec coverage:**
  - [x] WebSocket primary — Task 8
  - [x] REST polling backfill — Task 7
  - [x] Tier-aware severity — Task 3 + Task 4
  - [x] Degrade FSM — Task 7 (polling), Task 8 (WS), Task 10 (recovery)
  - [x] Bootstrap handle→user_id — Task 6
  - [x] No public IP / outbound only — WS `websockets` lib + REST `httpx`
  - [x] 47-handle whitelist — Task 2
  - [x] Wired into existing pipeline — Task 9
  - [x] Documented — Task 11

- [ ] **Placeholder scan:** No "TBD/TODO/fill in", except one explicit live-verify in Task 8.5 which is necessary because TwitterAPI.io's WS subscription wire format is not in public docs.

- [ ] **Type consistency:** `XSourceCfg.handles[].tier` ⇄ `x_users.tier` ⇄ `parse_x_tweet(tier=)` ⇄ `_HIGH_TIERS` literal — all use the same `S/A/B/C/D/E` vocabulary.

- [ ] **Whole-suite run before merge:**
  ```
  uv run pytest -q
  ```
  Expected: every existing test still passes plus 14 new tests.

- [ ] **Cost sanity check:** After 24 h of operation, query event_log:
  ```
  sqlite3 data/safe_monitor.db "SELECT COUNT(*) FROM event_log WHERE source LIKE 'x_%' AND received_at > datetime('now','-1 day');"
  ```
  Multiply by `$0.00015` (= 15 credits) for a reality check on burn rate.
