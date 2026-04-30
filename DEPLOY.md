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
