# safe_monitor — 部署手册（CentOS 7 + Docker）

服务器要求：Docker Engine ≥ 20，`docker compose` 插件可用，能直接访问外网。

## 一次性首次部署

### 1. 把代码放到服务器

任选其一：

```bash
# 方式 A: 通过 git
git clone <your-repo-url> /opt/safe_monitor
cd /opt/safe_monitor

# 方式 B: 直接 rsync 当前工作目录（排除 venv/缓存）
rsync -av --exclude '.venv' --exclude '__pycache__' --exclude 'data/*.db-*' \
  ./ user@server:/opt/safe_monitor/
```

### 2. 把三份运行期状态拷过去

这三个文件不进 git，必须手动 scp：

```bash
# 在本地工作目录执行
cd <local-repo>  # i.e. the directory you cloned this repo into

scp .env                     user@server:/opt/safe_monitor/.env
scp data/userbot.session     user@server:/opt/safe_monitor/data/userbot.session
scp data/safe_monitor.db     user@server:/opt/safe_monitor/data/safe_monitor.db
```

> `safe_monitor.db` 里已经种入了 OFAC 全部历史 key（790 条），拷过去能避免服务器首次轮询时再次把整个历史 SDN 列表当成新事件刷一遍。如果你不在意（反正本地已经收过 21 条噪音，可以接受多收一次或干脆删掉），可以不拷这个文件，DB 会自动重建并 seed。

### 3. 修正 data/ 目录权限

容器里跑非 root 用户（uid=10001），bind-mount 的 `./data` 必须让 10001 能写：

```bash
sudo chown -R 10001:10001 /opt/safe_monitor/data
```

### 4. 启动服务

```bash
cd /opt/safe_monitor
docker compose up -d --build
```

> Docker 守护进程开机自启 + compose 的 `restart: unless-stopped` 共同保证服务器重启后服务自动恢复——不需要写 systemd unit。

### 5. 看日志验证启动正常

```bash
docker compose logs -f safe_monitor
```

预期看到：
- `safe_monitor.start sources=[ofac_sdn, telegram_ingestor]`
- `Connection to ...:443/TcpFull complete!`
- `tg.channel_bound source=peckshield_tg ...`（共 6 个，SlowMist + wublockchain 两个用户名是 TODO）
- `ofac.poll_done emitted=0`（如果 DB 是新建的，会先看到 `ofac.poll_seeded`）

## 日常运维

### 拉新代码、重启

```bash
cd /opt/safe_monitor
git pull           # 或 rsync 同步
docker compose up -d --build
```

### 查看日志

```bash
docker compose logs --tail 100 -f safe_monitor
```

### 停止 / 重启

```bash
docker compose stop safe_monitor
docker compose restart safe_monitor
docker compose down                # 停且删容器，data/ 卷保留
```

### 备份运行期数据

```bash
# 任意时刻可热备（SQLite WAL 安全）
tar czf safe_monitor_data_$(date +%F).tgz data/
```

## 故障排查

### Telegram session 过期或被拒

如果服务启动后日志里出现 `AuthKeyError` / 频繁 `Connection closed`：

1. 在服务器上重新走两阶段登录：
   ```bash
   cd /opt/safe_monitor
   set -a && source .env && set +a
   docker compose run --rm --entrypoint "" safe_monitor \
     uv run python scripts/tg_login_phase1.py
   ```
   把 6 位验证码记下来，然后：
   ```bash
   TG_LOGIN_CODE=<code> TG_2FA_PASSWORD='<your-pwd>' \
   docker compose run --rm --entrypoint "" safe_monitor \
     uv run python scripts/tg_login_phase2.py
   ```
2. session 文件会重写到 `./data/userbot.session`，服务自动用新 session。

### Bot 推送 chat_not_found

确保 bot（`@<your-bot-username>`）和目标 chat（id `<your-chat-id>`）至少有过一次互动——
你在客户端给 bot 发任意消息（或 `/start`）即可。

### OFAC 抓取报 SSL/超时

服务器到 `treasury.gov` / `sanctionslistservice.ofac.treas.gov` / `*.amazonaws.com` 三跳要通。
不通的话检查出口防火墙白名单。

## 配置项快速索引

- `config.yaml` — 数据源开关 + 频道列表 + 过滤策略 + 去重 TTL
- `.env` — 所有 secrets（api_id/api_hash/phone/bot_token/chat_id/db_path）
- `docker-compose.yml` — 容器卷挂载和路径覆盖（`/app/data`, `/app/config.yaml`）

## 已知 TODO

- `SlowMist_Team` 和 `wublockchain` 两个频道的真实用户名待查；目前在 `config.yaml` 里被注释。
- DefiLlama hacks API 需要付费（402），已禁用。后续可补 Rekt.news RSS 或 DeFiYield API 替代源。

## X (Twitter) ingestion (TwitterAPI.io)

1. Sign up at https://twitterapi.io/ and top up at least $5 of credits.
2. Add to `.env` (do **not** commit):
   ```
   X_API_KEY=...
   ```
3. Bootstrap the user list (idempotent, safe to re-run):
   ```
   uv run python scripts/resolve_x_handles.py
   ```
4. Verify the table is populated:
   ```
   sqlite3 data/safe_monitor.db 'SELECT COUNT(*) FROM x_users;'   # expect ~47
   ```
5. Re-run `safe_monitor` — you should see `x_websocket` and `x_polling` in
   the source list at startup.

**Degrade behaviour.** If TwitterAPI.io returns `402` (no credits) or the
WebSocket fails to reconnect 5× in a row, the X sources self-disable; the
rest of the pipeline (TG mirrors, RSS, Forta, OFAC) keeps running. The
scheduler probes for recovery every 30 min and re-enables automatically.

**Editing the whitelist.** Add/remove rows under `sources.x.handles` in
`config.yaml`, then re-run `scripts/resolve_x_handles.py`. To force-refresh
an already-resolved handle (e.g. account renamed), pass `--force`.

**WebSocket caveat.** TwitterAPI.io's WebSocket subscription wire format is
not fully documented; the implementation uses a best-effort `{"action":
"subscribe", "rules": [{"follow": [user_id, ...]}]}` frame. If the server
rejects it on first connection, fix `_build_subscription` in
`src/safe_monitor/sources/x_websocket.py` (single point of fix).

## Translation to Simplified Chinese (optional)

Set either `DEEPSEEK_API_KEY` or `OPENAI_API_KEY` in `.env`. Alerts whose
original text is **not** already Chinese-dominant (≥30 % CJK letter ratio)
get title and body translated to Simplified Chinese before being formatted
for Telegram.

**DeepSeek (recommended — cheap + native Chinese model):**
```
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_MODEL=deepseek-chat   # optional override
```

**OpenAI:**
```
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini       # optional override
```

When both are set, **DeepSeek wins** (cheaper). Restart `safe_monitor` and
look for `translator.enabled provider=... model=...` at boot.

**Cost guard.** Translation calls run with an 8 s timeout and fall back to
the original text on any error — translation can never block alert
delivery. Estimate per non-Chinese alert:

| provider           | input + output / alert | $/1500 alerts |
|--------------------|------------------------|---------------|
| `deepseek-chat`    | ~300 + 200 tokens      | ~$0.005       |
| `gpt-4o-mini`      | same                   | ~$0.30        |

## Topic classifier (auto-enabled with translation)

The same LLM key (DeepSeek or OpenAI) also gates X-source alerts on
"is this Web3 security?". S-tier researcher accounts often post off-topic
content (gaming, memes, personal life) — without this gate the bot would
spam you with all of it.

- Runs **only on `x_*` sources** (Forta / OFAC / Rekt are pre-curated,
  trusted without an LLM check).
- 5 s timeout; fails OPEN — a classifier outage never silently drops
  alerts.
- Asks for one token (`YES`/`NO`); ~60 input + 1 output tokens per call.
  At ~1500 X events / month with `deepseek-chat`: ~$0.001/month.
