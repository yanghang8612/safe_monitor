---
name: safe_monitor v0 监控服务设计规格
description: Web3 安全事件实时推送服务的 v0 设计。Python + Docker + SQLite 单容器架构，10 个源（2 API + 8 Telegram 频道），推送到 Telegram。
type: spec
status: approved
date: 2026-04-20
author: safe_monitor project
parent_spec: docs/superpowers/specs/2026-04-20-web3-security-feed-design.md
---

# safe_monitor v0 — 监控服务设计规格

> 本 spec 依据阶段一调研报告（`docs/research/2026-04-20-web3-security-channels.md`）的 §10 MVP 推荐做裁剪与适配，产出一个"最快能跑起来的 v0 监控服务"。

---

## §1. 目标

搭建一个**单容器、单用户、单 Telegram 推送目标**的 Web3 安全事件监控服务：

- 持续订阅 8 个公开 Telegram 频道 + 轮询 2 个 HTTP API
- 去重、归一化、过滤、分级
- 通过 Telegram Bot 推送到指定 chat

**v0 成功标准**：
1. `docker compose up -d` 一条命令启动
2. 重启容器不丢事件、不重发事件
3. 至少 7 天稳定运行无需人工干预
4. 端到端延迟（事件发生 → TG 推送）**P50 ≤ 60 秒**，**P95 ≤ 5 分钟**（受限于源自身的首发速度）

---

## §2. 接入源清单（v0 锁定）

### §2.1 API 源（2 个）

| # | 源 | 接入方式 | 轮询间隔 | 备注 |
|---|---|---|---|---|
| 1 | DeFiLlama Hacks | `GET https://api.llama.fi/hacks` | 5 分钟 | JSON 结构化，按 `date` 字段增量 |
| 2 | OFAC SDN List | `GET` 官方 XML/JSON 下载地址 | 24 小时 (02:00 UTC) | 增量比对 `publishDate`，新增地址单独推送 |

### §2.2 Telegram 频道源（8 个）

| # | 频道 username | 预期覆盖 | 备注 |
|---|---|---|---|
| 3 | `@peckshield` | a/b/e/g 链上攻击首发 | 首选 |
| 4 | `@SlowMist_Team` | a/b/c/d/e/g/h 综合 | 中英双语 |
| 5 | `@CertiKAlert` | a/b/e 攻击告警 | 补充 |
| 6 | `@whale_alert_io` | 大额转账 | 异常转账信号 |
| 7 | `@scam_sniffer` | e 钓鱼监控 | 必接 |
| 8 | `@wublockchain` | 中文圈首发 | 亚洲时段关键 |
| 9 | `@binance_announcements` | c/h/i Binance 公告 | — |
| 10 | `@Bybit_Announcements` | c/h/i Bybit 公告 | — |

**频道 ID 验证策略**：上述 username 可能与实际频道略有出入（如大小写、是否带 `_bot` 后缀）。启动阶段每个频道需手动用 telethon 的 `get_entity()` 验证一次，配置文件里保存**数字 chat_id**而非 username，避免运行时解析失败。

---

## §3. 技术栈与部署

### §3.1 技术栈

| 组件 | 选型 | 理由 |
|---|---|---|
| 语言/运行时 | Python 3.11 + asyncio | telethon 生态最成熟 |
| 依赖管理 | `uv` | 比 poetry 快 10 倍，锁文件可重现 |
| TG 入站 | `telethon >=1.35` | userbot 事实标准 |
| TG 出站 | `python-telegram-bot >=20` | Bot API 封装完善 |
| HTTP 客户端 | `httpx` | 异步原生 |
| 调度 | `apscheduler` | 轻量，足以应付 2 个定时任务 |
| 日志 | `structlog` | JSON 结构化输出到 stdout |
| 配置 | `pydantic` + `pydantic-settings` | YAML + .env 合并、类型校验 |
| DB | `aiosqlite`（异步 SQLite） | 零运维 |
| 容器 | Docker + docker-compose | 部署痛点消除 |
| 测试 | `pytest` + `pytest-asyncio` + `vcr.py` | 录制 HTTP 回放 |

### §3.2 部署

单容器 + 一个 volume 挂载：

```yaml
# docker-compose.yml (示意)
services:
  safe_monitor:
    build: .
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./data:/app/data
      - ./config.yaml:/app/config.yaml:ro
    environment:
      TZ: UTC
```

Dockerfile 基于 `python:3.11-slim`，使用 `uv` 同步锁文件。镜像目标大小 < 200 MB。

---

## §4. 存储设计

SQLite 单文件位于 `/app/data/safe_monitor.db`。表结构：

### §4.1 `fingerprints` — 去重表

| 字段 | 类型 | 说明 |
|---|---|---|
| `fingerprint` | TEXT PRIMARY KEY | SHA256(source + normalized_title[:100] + date_yyyymmdd) |
| `source` | TEXT | 源标识（`peckshield_tg` / `defillama_api` 等） |
| `first_seen_at` | DATETIME | 首次看到时间 |
| `event_count` | INTEGER | 重复命中次数（用于观察噪声） |

- 索引：`first_seen_at`（用于 TTL 清理）
- TTL：每晚清理 > 7 天的记录

### §4.2 `checkpoints` — 源进度表

| 字段 | 类型 | 说明 |
|---|---|---|
| `source` | TEXT PRIMARY KEY | 源标识 |
| `kind` | TEXT | `tg_channel` / `api_poll` |
| `cursor` | TEXT | TG: 最后处理的 message_id；API: ISO 时间戳 |
| `updated_at` | DATETIME | — |

### §4.3 `event_log` — 事件审计表

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | — |
| `source` | TEXT | 来源 |
| `received_at` | DATETIME | 入站时间 |
| `published_at` | DATETIME | 推送时间（NULL 表示被过滤） |
| `severity` | TEXT | critical/high/medium/low |
| `title` | TEXT | 归一化标题 |
| `url` | TEXT | 原链接 |
| `raw_json` | TEXT | 原始 payload |
| `filter_decision` | TEXT | `published` / `filtered:<reason>` |

- 索引：`received_at`、`source`
- 不做 TTL（供后续分析）

### §4.4 `failed_events` — 推送失败重试表

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | — |
| `event_id` | INTEGER | 关联 `event_log.id` |
| `last_error` | TEXT | 最后一次错误信息 |
| `retry_count` | INTEGER | 已重试次数 |
| `next_retry_at` | DATETIME | 下次重试时间 |

---

## §5. 数据模型

### §5.1 `RawEvent`（源产出）

```python
class RawEvent(BaseModel):
    source: str                    # "peckshield_tg" 等标识
    source_kind: Literal["tg","api"]
    external_id: str               # TG message_id 或 API 记录主键
    received_at: datetime
    raw: dict                      # 原始 payload
    # 便于 normalizer 的预解析字段（可选）
    text: Optional[str] = None
    url: Optional[str] = None
    occurred_at: Optional[datetime] = None
```

### §5.2 `Event`（归一化后）

```python
class Event(BaseModel):
    fingerprint: str
    source: str
    title: str                     # ≤ 200 字，归一化
    body: Optional[str] = None
    url: Optional[str] = None
    severity: Severity             # critical/high/medium/low
    category: list[EventCategory]  # a-j 的子集
    chain: Optional[str] = None
    tx_hash: Optional[str] = None
    attacker_addr: Optional[str] = None
    loss_usd: Optional[float] = None
    occurred_at: Optional[datetime] = None
    received_at: datetime
    raw: dict                      # 保留
```

---

## §6. 组件设计

### §6.1 组件清单与职责

| 模块 | 类 | 输入 | 输出 | 只做 |
|---|---|---|---|---|
| `sources/base.py` | `Source` (抽象) | — | `AsyncIterator[RawEvent]` | 定义接口 |
| `sources/telegram.py` | `TelegramIngestor` | 频道 ID 列表 | `AsyncIterator[RawEvent]` | 订阅 TG 新消息，包装为 RawEvent |
| `sources/defillama.py` | `DefiLlamaHacksPoller` | — | `AsyncIterator[RawEvent]` | 定时拉 API，按 checkpoint 增量 |
| `sources/ofac.py` | `OfacSdnPoller` | — | `AsyncIterator[RawEvent]` | 每日下载 SDN，diff 出新增数字货币地址 |
| `core/normalizer.py` | `Normalizer` | `RawEvent` | `Event` | 抽取字段、计算 fingerprint、初判 category/severity |
| `core/deduper.py` | `Deduper` | `Event` | `Event` 或 `None` | 查/写 `fingerprints` 表 |
| `core/filter.py` | `Filter` | `Event` | `Event` 或 `None` | 应用用户规则，打最终 severity 标签 |
| `publishers/base.py` | `Publisher` (抽象) | `Event` | `bool`（成功） | 定义接口 |
| `publishers/telegram.py` | `TelegramPublisher` | `Event` | `bool` | 格式化消息、Bot API 推送 |
| `core/orchestrator.py` | `Orchestrator` | — | — | 启动所有源协程、串联 pipeline |
| `storage/db.py` | `Database` | — | — | SQLite 初始化、迁移、DAO |
| `config.py` | `Settings`（pydantic） | `config.yaml` + `.env` | — | 配置加载与校验 |
| `main.py` | — | — | — | 入口：构建各组件 → 启动 orchestrator → 捕获信号优雅退出 |

### §6.2 Source 抽象

```python
class Source(Protocol):
    name: str
    async def run(self, sink: asyncio.Queue[RawEvent]) -> None: ...
```

每个 Source 的 `run()` 在自己的协程中跑，直接向共享 Queue 写；崩溃由 Orchestrator 的 supervisor 兜底重启。

### §6.3 Normalizer 的分发

Normalizer 是**有限状态机 + per-source 解析器**：

- `telegram.py` 的 RawEvent 进入 `normalize_tg_message(source_name, text, entities)`
  - 按频道选择解析器（`parse_peckshield` / `parse_slowmist` / `parse_whale_alert` 等）
  - 正则抽取 tx_hash、address、loss_usd、chain
- `defillama.py` 的 RawEvent 进入 `normalize_defillama_hack(raw)` — 字段映射直接完成
- `ofac.py` 的 RawEvent 进入 `normalize_ofac_entry(raw)` — 提取地址 + 制裁国/实体

严重度初判规则（Normalizer 给出"hint"，Filter 最终定级）：
- 含 `hacked|exploit|被盗|attack` 且 `loss_usd >= $10M` → critical
- 含上述关键词且 `loss_usd >= $1M` → high
- 含 `sanctioned|OFAC|制裁` → high
- 含 `phishing|drainer|钓鱼` → medium
- 其他 → low

### §6.4 Filter 规则引擎

配置在 `config.yaml`：

```yaml
filter:
  min_severity: medium       # 低于此级别丢弃
  deny_keywords:             # 包含这些词的事件丢弃（常见营销噪声）
    - "airdrop"
    - "giveaway"
    - "NFT mint"
  allow_sources:             # 若非空，只有这些源能发 critical
    critical:
      - peckshield_tg
      - slowmist_tg
      - defillama_api
```

v0 规则很简单，但实现用 Pydantic 模型 + 函数式判断，留好扩展点。

### §6.5 TelegramPublisher 消息格式

参考**用户提供的业内示范格式**（2026-04-20 追加）并按用户决定精简为**两段式**（PORTFOLIO IMPACT 段 v0 不做，见 §13 YAGNI）：**摘要 / 详情**，中英双语并列。

```
🚨 摘要 SUMMARY
Resolv 协议的 $80M $USR 稳定币遭到攻击利用，部分资金已被兑换成约 5,500 ETH 和 USDC。
Resolv protocol suffered an exploit of $80M $USR stablecoin, with part of the funds swapped into approximately 5,500 ETH and USDC.

📋 详情 DETAILS
- 受影响的协议/项目 Protocol/project affected: Resolv / $USR
- 预估损失 Estimated loss: $80M USD
- 攻击类型 Type of attack: Exploit
- 链 Chain: Ethereum
- 攻击者地址 Attacker: 0xabcd...1234
- 攻击交易 Tx hash: 0xdeadbeef...
- 来源 Source: @BeosinAlert
- 链接 Link: https://nitter.net/BeosinAlert/status/...
- 严重度 Severity: 🔴 CRITICAL
- 时间 Timestamp: 2026-04-20 13:45:02 UTC
```

格式规则：
- **v0 发送纯文本**（不传 `parse_mode`）。MarkdownV2 转义延期到 v0.1：需要对每个动态字段（标题、地址、金额字符串）逐一 escape 十余种特殊字符，任一遗漏即触发 Telegram 400。v0 formatter 本就没有 Markdown 语法，纯文本发送零风险。**Deferred to v0.1**：`parse_mode=MarkdownV2` + `telegram.helpers.escape_markdown(value, version=2)`。
- 超过 4000 字符时截断 body，保留 DETAILS 完整
- 字段缺失时**整行省略**而非显示 "N/A"，避免无意义占位行
- 英文句子未来可引入 LLM 翻译；v0 仅在原文已含英文时双语呈现，否则只保留原文并在开头标 `[CN]` / `[EN]`

---

## §7. Orchestrator 与生命周期

### §7.1 启动顺序

1. 加载配置（`config.yaml` + `.env`），校验失败直接退出（exit 1）
2. 初始化 SQLite，执行未应用的 migration
3. 构建 Publisher（先构建，以便启动完成通知）
4. 构建各 Source，每个 Source 注入：shared Queue、checkpoint DAO
5. 启动 APScheduler（TTL 清理任务、重试任务）
6. 启动 Orchestrator 主循环：N 个 Source 协程 + 1 个处理协程
7. 通过 Bot 发送启动通知（"safe_monitor started, X sources active"）
8. 捕获 SIGTERM/SIGINT，优雅退出（等 Queue 排空后停 Source，再 flush DB）

### §7.2 Supervisor 模式

每个 Source 协程由 Orchestrator 包装：

```python
async def supervised(source: Source, queue: Queue):
    backoff = 1
    while not shutdown.is_set():
        try:
            await source.run(queue)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("source_crashed", source=source.name, error=str(e))
            await asyncio.sleep(min(backoff, 300))
            backoff = min(backoff * 2, 300)
```

---

## §8. 错误处理与韧性

| 场景 | 策略 |
|---|---|
| telethon 断线 | telethon 自带重连；我们额外 log 警告 |
| API 429/5xx | httpx 重试：3 次，间隔 1/5/30 秒；仍失败本轮跳过，下轮再来 |
| Bot 推送失败（429/网络）| 重试 3 次；仍失败写 `failed_events`，由重试任务每 10 分钟扫一次 |
| SQLite `database is locked` | 所有写操作通过单一 writer 协程（asyncio.Queue 串行化） |
| 配置错误 | 启动时 Pydantic validation；错误打印到 stdout 并 exit 1 |
| 某 TG 频道被封/改名 | 启动时 `get_entity()` 失败只 log，不阻塞；运行时接收不到消息会被 telethon 忽略 |
| 容器 OOM/崩溃 | Docker `restart: unless-stopped`；checkpoints 保证不丢不重 |

---

## §9. 测试策略

| 层 | 覆盖 | 工具 |
|---|---|---|
| 单元 | Normalizer 每个 per-source 解析器；Deduper 指纹算法；Filter 决策逻辑 | `pytest` |
| 集成 | DefiLlama/OFAC 用 VCR 录制响应回放；Deduper + DB；Publisher 对接 mock Bot | `pytest` + `vcr.py` + `respx` |
| 冒烟 | `pytest -m smoke`：构造一条假 RawEvent，端到端跑通到 mock Publisher | — |

**不做**：
- 真实 TG 集成测试（需要 session 和 risk，不适合 CI）
- 负载测试（v0 量级不需要）
- E2E（用手动冒烟替代）

目标覆盖率：Normalizer/Deduper/Filter 核心逻辑 ≥ 80%。

---

## §10. 项目结构

```
safe_monitor/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── uv.lock
├── .env.example
├── config.yaml
├── README.md
├── src/safe_monitor/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── orchestrator.py
│   │   ├── normalizer.py
│   │   ├── deduper.py
│   │   ├── filter.py
│   │   └── severity.py
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── telegram.py
│   │   ├── defillama.py
│   │   └── ofac.py
│   ├── publishers/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   └── telegram.py
│   └── storage/
│       ├── __init__.py
│       ├── db.py
│       └── migrations/
│           └── 001_init.sql
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_normalizer_peckshield.py
│   │   ├── test_normalizer_slowmist.py
│   │   ├── test_deduper.py
│   │   └── test_filter.py
│   ├── integration/
│   │   ├── test_defillama_poller.py
│   │   ├── test_ofac_poller.py
│   │   └── test_db.py
│   └── fixtures/
│       ├── defillama_hacks_response.json
│       └── ofac_sdn_sample.xml
└── data/                                     # SQLite 持久化
    └── .gitkeep
```

---

## §11. 配置文件样例

### §11.1 `.env.example`

```bash
# Telegram userbot (telethon)
TG_API_ID=your_api_id
TG_API_HASH=your_api_hash
TG_USERBOT_PHONE=+1234567890
TG_USERBOT_SESSION=/app/data/userbot.session

# Telegram publisher bot
TG_BOT_TOKEN=123456:ABC-DEF...
TG_TARGET_CHAT_ID=123456789

# Logging
LOG_LEVEL=INFO
```

### §11.2 `config.yaml`

```yaml
sources:
  api:
    - name: defillama_hacks
      endpoint: "https://api.llama.fi/hacks"
      poll_interval_seconds: 300
    - name: ofac_sdn
      endpoint: "https://sanctionslistservice.ofac.treas.gov/..."  # 启动前验证
      poll_interval_seconds: 86400

  telegram:
    - name: peckshield_tg
      username: "peckshield"
    - name: slowmist_tg
      username: "SlowMist_Team"
    - name: certik_alert_tg
      username: "CertiKAlert"
    - name: whale_alert_tg
      username: "whale_alert_io"
    - name: scam_sniffer_tg
      username: "scam_sniffer"
    - name: wublockchain_tg
      username: "wublockchain"
    - name: binance_ann_tg
      username: "binance_announcements"
    - name: bybit_ann_tg
      username: "Bybit_Announcements"

filter:
  min_severity: medium
  deny_keywords:
    - "airdrop"
    - "giveaway"

dedup:
  ttl_days: 7
```

---

## §12. 安全与合规注意

- `.env` 入 `.gitignore`，**绝不**提交
- TG userbot session 文件（`userbot.session`）**绝不**入 git；挂在容器 volume 里
- 镜像启动用非 root 用户（`USER app`）
- 日志**不要**打印完整 API key / session token
- OFAC 制裁地址是公开数据，但存储/使用遵循合规原则

---

## §12bis. 消息格式溯源

§6.5 的输出格式参考了用户 2026-04-20 提供的业内示范消息（来源：某团队的 Resolv / $USR 事件告警模板）。原参考格式是**三段式**（摘要 / 详情 / 投资组合影响）+ 中英双语。

本 spec v0 **采用前两段**（摘要 / 详情），PORTFOLIO IMPACT 段**不在 v0 范围**（用户 2026-04-20 明确选择跳过），留待 v0.5+ 评估。

---

## §13. 明确不做（YAGNI）

- 多用户 / 多 chat 订阅
- Web UI / REST API / Prometheus 指标
- 分布式多实例（SQLite 限制）
- Discord / Slack / Email 推送
- Twitter 原生源（用 TG 镜像替代）
- Hypernative / CertiK 付费 API
- 全文搜索历史事件
- 机器翻译（中/英混推即可）
- 自动钓鱼地址扫描
- **PORTFOLIO IMPACT 段**（用户示范格式的第三段；v0 完全不实现，消息只有摘要+详情两段）
- 自动中英翻译（v0 仅双语并列源文，不做翻译）

这些进入 v1+ 再评估。

---

## §14. 明确由用户 / 运维承担

1. `.env` 中的 TG api_id / api_hash / bot_token / chat_id
2. 首次 telethon 登录（本地 run 一次完成手机号验证码交互，把 session 文件上传服务器）
3. 部署目标机器（VPS / 家用机），本 spec 假设有 Docker 环境
4. OFAC 官方下载地址变动时，更新 `config.yaml`

---

## §15. 后续里程碑（非本 spec 范围）

- v0.1：加 Twitter 源（当 Nitter/免费 API 可用时）
- v0.2：加 Forta / Statuspage 聚合
- v1：多用户订阅、Web UI
- v1.5：付费源 Hypernative / CertiK Skynet
- v2：多实例 + Postgres

---

## §16. 成功标准再声明（与 §1 对应）

本 spec 的实施交付必须全部满足：

1. `docker compose up -d` 一条命令启动成功
2. 重启容器不丢事件、不重发事件（checkpoint + dedup 保证）
3. 至少 7 天稳定运行，log 中无未处理异常
4. 端到端延迟 P50 ≤ 60 秒、P95 ≤ 5 分钟
5. 单元/集成测试覆盖 Normalizer/Deduper/Filter ≥ 80%
6. README 能让一个新人 30 分钟内把服务跑起来

---

*本 spec 经用户 2026-04-20 明确批准（"开始"），下一步进入 `writing-plans` 生成实施计划。*
