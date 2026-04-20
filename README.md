# safe_monitor

Real-time Web3 security event monitor. Subscribes to 2 HTTP APIs and 8 public Telegram channels, dedupes/filters/scores events, and pushes alerts to your Telegram chat.

Scope and design: `docs/superpowers/specs/2026-04-20-monitor-service-design.md`
Channel inventory: `docs/research/2026-04-20-web3-security-channels.md`

## Quick start

### 1. Prerequisites

- Docker + Docker Compose
- A Telegram account (for userbot ingestion)
- A Telegram bot (for push, created via `@BotFather`)

### 2. Obtain credentials

- **TG userbot**: go to https://my.telegram.org → API development tools → get `api_id` and `api_hash`
- **TG bot**: talk to `@BotFather` → `/newbot` → save the token
- **Target chat id**: message `@userinfobot` from your chat (or group) → copy the numeric id

### 3. Configure

```bash
cp .env.example .env
# edit .env with the values above
```

Review `config.yaml` — you can tweak channel list, filter keywords, and TTL. The OFAC endpoint may need updating (see spec §2.1).

### 4. One-time: create the TG userbot session (LOCALLY, not in Docker)

Because login needs an interactive SMS code, run this once on your laptop:

```bash
uv sync --all-extras
set -a; source .env; set +a
uv run python scripts/telegram_login.py
# enter SMS/Telegram code when prompted
```

This creates `data/userbot.session`. **Copy the whole `data/` directory** to your server before starting the container.

### 5. Run

```bash
docker compose up -d
docker compose logs -f
```

The first minute you should see:

```
INFO safe_monitor.start sources=["defillama_api","ofac_sdn","telegram_ingestor"]
INFO tg.channel_bound source=peckshield_tg username=peckshield id=...
INFO defillama.poll_done emitted=0 last_ts=...
```

Then the bot will start sending events to the configured chat.

## Operations

- **Logs**: `docker compose logs -f --tail=100`
- **Restart**: `docker compose restart`
- **Stop**: `docker compose down`
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
| TG channel `channel_resolve_failed` | Username changed or channel made private — update `config.yaml` |
| `defillama.poll_error` transient | Ignored, next poll cycle will retry |
| Container restarts repeatedly | `docker compose logs --tail=200` to see the stack trace |
| Duplicate events | Check `fingerprints` table; increase `dedup.ttl_days` in config |

## Development

```bash
uv sync --all-extras
uv run pytest -v              # full suite
uv run pytest -m smoke        # smoke only
uv run ruff check .
uv run ruff format .
```

## Scope (v0 vs later)

v0 does NOT include: multi-user subscriptions, Web UI, Discord/Slack/email push, Twitter source, portfolio impact analysis, paid sources (Hypernative/CertiK Skynet), real on-chain exposure calculation. See spec §13.
