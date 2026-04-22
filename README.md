# safe_monitor

Real-time Web3 security event monitor. Subscribes to 2 HTTP APIs and 8 public Telegram channels, dedupes/filters/scores events, and pushes alerts to your Telegram chat.

Scope and design: `docs/superpowers/specs/2026-04-20-monitor-service-design.md`
Channel inventory: `docs/research/2026-04-20-web3-security-channels.md`

## Telegram ingestion modes

Two paths for pulling messages out of Telegram public channels. **RSSHub is the default and the recommended one** — it does not require any Telegram API credentials.

| Mode | `TG_INGEST_MODE` | Creds required | Latency | When to use |
|---|---|---|---|---|
| **RSSHub** (default) | `rsshub` | **None** (uses self-hosted RSSHub container) | ~2 min per channel | Can't register on `my.telegram.org`, or want zero Telegram-API risk |
| telethon userbot | `userbot` | `TG_API_ID` + `TG_API_HASH` from my.telegram.org + phone login | Seconds-level | Have working API creds and want minimum latency |

Both modes produce the same `RawEvent` shape; the rest of the pipeline (Normalizer / Deduper / Filter / Publisher) is identical. You can switch modes later without rebuilding anything.

---

## Quick start (RSSHub mode, default)

### 1. Prerequisites

- Docker + Docker Compose
- A Telegram bot for **push** — created via `@BotFather` (independent from `my.telegram.org`)

### 2. Create the bot and get your chat id

- Message `@BotFather` → `/newbot` → choose a name → save the bot token
- Start a DM with your new bot (or add it to a group) and send any message
- Message `@userinfobot` → it replies with your numeric chat id

### 3. Configure

```bash
cp .env.example .env
# Edit .env:
#   TG_BOT_TOKEN=<from BotFather>
#   TG_TARGET_CHAT_ID=<numeric id from userinfobot>
# The TG_INGEST_MODE, RSSHUB_BASE_URL defaults are already correct.
# Leave TG_API_ID / TG_API_HASH / TG_USERBOT_PHONE blank — RSSHub doesn't use them.
```

`config.yaml` is prepopulated with the 8 TG channels + DeFiLlama + OFAC. Tweak if needed.

### 4. Run

```bash
docker compose up -d
docker compose logs -f safe_monitor
```

The first minute you should see:

```
INFO  safe_monitor.start  sources=["defillama_api","ofac_sdn","peckshield_tg","slowmist_tg","certik_alert_tg","whale_alert_tg","scam_sniffer_tg","wublockchain_tg","binance_ann_tg","bybit_ann_tg"]
INFO  defillama.first_run_seeded  last_ts=...
INFO  rsshub.poll_done  source=peckshield_tg  emitted=<N>
```

Bot alerts will start arriving at your chat as events come in.

### 5. Verify RSSHub is healthy

```bash
docker compose ps
# All three services should be Up: safe_monitor, rsshub, redis

# Quick sanity check against the RSSHub instance (from your host):
curl -s http://localhost:1200/telegram/channel/peckshield | head -5
```

If you prefer to use the public RSSHub (not recommended; rate-limited and sometimes down):

```bash
# In .env
RSSHUB_BASE_URL=https://rsshub.app
# Then remove the rsshub + redis services from docker-compose.yml (or just
# ignore them — they'll run but won't be used).
```

---

## Advanced: telethon userbot mode (lower latency)

Only use this if you want sub-second ingestion AND you successfully registered at `my.telegram.org`.

### 1. Extra prerequisites

- `api_id` + `api_hash` from https://my.telegram.org → API development tools

### 2. Configure

```bash
# In .env
TG_INGEST_MODE=userbot
TG_API_ID=<your_api_id>
TG_API_HASH=<your_api_hash>
TG_USERBOT_PHONE=+861234567890       # the phone used to log into your Telegram account
```

### 3. One-time: create the userbot session (LOCALLY, not in Docker)

Login requires typing an SMS/Telegram code interactively, so run it on your laptop first:

```bash
uv sync --all-extras
set -a; source .env; set +a
uv run python scripts/telegram_login.py
# enter the verification code when prompted
```

This creates `data/userbot.session`. If you're deploying to a remote server, **copy the entire `data/` directory** to the server before `docker compose up`.

### 4. Run

```bash
docker compose up -d
docker compose logs -f safe_monitor
```

Logs show `tg.channel_bound` per channel instead of `rsshub.poll_done`.

---

## Operations

- **Logs**: `docker compose logs -f --tail=100 safe_monitor`
- **Logs (RSSHub only)**: `docker compose logs -f --tail=100 rsshub`
- **Restart**: `docker compose restart safe_monitor`
- **Stop all**: `docker compose down`
- **Upgrade code**: `git pull && docker compose build && docker compose up -d`
- **Inspect DB**:

  ```bash
  docker compose exec safe_monitor \
    sqlite3 /app/data/safe_monitor.db \
    "SELECT source, severity, title FROM event_log ORDER BY id DESC LIMIT 20;"
  ```

- **Back up DB**: `cp data/safe_monitor.db data/backup-$(date +%F).db`

## Troubleshooting

| Symptom | Action |
|---|---|
| `telegram.publish_failed` with `429` | Bot hit Telegram rate limit — reduce push frequency or upgrade chat to supergroup |
| `rsshub.poll_error` many in a row | `docker compose logs rsshub` — probably rate-limited by Telegram; RSSHub will retry |
| Empty `rsshub.poll_done emitted=0` for a channel forever | Username in `config.yaml` may be wrong or channel is private. Verify via `curl http://localhost:1200/telegram/channel/<username>` |
| `defillama.poll_error` transient | Ignored, next poll cycle will retry |
| Container restarts repeatedly | `docker compose logs --tail=200 safe_monitor` to see the stack trace |
| Duplicate events | Check `fingerprints` table; increase `dedup.ttl_days` in config |
| `tg.channel_resolve_failed` (userbot mode) | Username changed or channel made private — update `config.yaml` |

## Development

```bash
uv sync --all-extras
uv run pytest -v              # full suite (32 tests)
uv run pytest -m smoke        # smoke only
uv run ruff check .
uv run ruff format .
```

## Scope (v0 vs later)

v0 does NOT include: multi-user subscriptions, Web UI, Discord/Slack/email push, Twitter source, portfolio impact analysis, paid sources (Hypernative/CertiK Skynet), real on-chain exposure calculation, MarkdownV2 rich formatting. See spec §13.
