# wublock123 RSS 接入设计

**日期**: 2026-05-14
**状态**: 已批准,待实现

## 背景

`https://wublock123.com/feed` 是吴说(WuBlockchain)的中文资讯平台 feed。希望把它接入 safe_monitor 作为一路安全情报来源。

探查发现两个关键约束:

1. **feed 是 Atom 格式,不是 RSS 2.0**。根节点 `<feed>`、条目是 `<entry>`、链接在 `<link href>` 属性里、时间是 ISO-8601 的 `<published>`/`<updated>`、正文在 `<content type="html">` / `<summary type="html">`(CDATA 包裹)。现有的 `RssFeedPoller` docstring 自称 "Generic RSS 2.0 / Atom poller",但 Atom 分支从未实现 —— 只认 `<item>`/`<pubDate>`。直接配上去会静默 emit 0。
2. **内容是全品类中文资讯**(融资、市场、安全混在一起),不是安全专项。已监控的 X 源 `@WuBlockchain`(tier D)是同一家机构,会有内容重叠。

## 已确认的决策

- **内容范围**:只要安全相关。走现有 severity 评分器 + `filter.min_severity=medium`,融资/市场类条目命中不了安全关键词 → 评 `low` → 被过滤。不改过滤逻辑。
- **去重**:先接受与 `@WuBlockchain` X 源的重复推送。X 是英文/混合、feed 是中文,标题不同,现有 fingerprint(source+title+时间窗)不会误判;上线后观察重复率再决定是否做跨源去重。
- **轮询间隔**:300s,与 X 源一致。feed 拉取免费。

## 方案选择

**采用:扩展 `RssFeedPoller` 支持 Atom(RSS 2.0 + Atom 双格式)。**

它的 docstring 已承诺支持 Atom,本质是补完未完成的实现;一个 poller 管所有独立 feed,未来 Atom feed 免费复用。

已否决:
- 新建专用 `WublockAtomPoller` —— 与 `RssFeedPoller` 重复约 80%,且留着假 docstring。
- 走 RSSHub 代理 —— 本项目正在退役 RSSHub ingest,不值得加基础设施依赖。

## 改动面

5 个改动点:

### 1. `src/safe_monitor/sources/rss_feed.py`

`RssFeedPoller.poll_once` 加格式检测:`root.tag` endswith `feed` → Atom 分支;否则现有 RSS 2.0 分支**完全不动**(保证 rekt_news 不回归)。

Atom 分支:遍历 `<entry>`,逐项提取:
- `<title>` → title
- `<id>` → guid / external_id(吴说的 `<id>` 就是文章 URL)
- `<link>` 的 `href` **属性** → link(不是文本;只取 entry 的直接子节点,避免与 feed 级同名标签串)
- `<content>` 优先,缺失则 `<summary>` → description
- `<published>` → pubDate

游标语义不变(最近条目时间的 ISO 字符串)。

**Atom 时间解析**:`<published>` 是 ISO-8601(`2026-05-14T07:55:35.000Z`),现有 `_parse_pubdate` 只认 RFC822。复用 `rss_feed.py` 已 import 的 `_parse_checkpoint`(本就是解析 `.isoformat()` 的 ISO 解析器),不新增 helper。

### 2. `config.yaml`

`sources.api` 下新增:

```yaml
- name: wublock_news
  endpoint: "https://wublock123.com/feed"
  poll_interval_seconds: 300
```

### 3. `src/safe_monitor/main.py`

`_build_sources` 里 `elif api.name == "rekt_news"` 改为 `elif api.name in ("rekt_news", "wublock_news")`,复用 `RssFeedPoller`。

### 4. `src/safe_monitor/core/parsers/wublock.py`(新增)

`parse_wublock(raw: dict) -> dict`,仿 `parse_rekt` 的形状:

- `title` = `raw["title"]`(真实标题,不是正文首行)
- `body` = `_strip_html(raw["description"])`
- `url` = `raw["link"]`
- `tx_hash` / `attacker_addr` / `loss_usd` / `chain` / `category`:复用 `parse_generic_tg(body)`(已含中英文关键词与正则抽取),取其结果
- **不强制 severity** —— 与 `parse_rekt` 最大的区别。rekt 每条都是事故故写死 `high`;wublock 全品类,把 severity 留空交给 `score_severity` 按关键词打分。这是"只要安全相关"的落地机制。

### 5. `src/safe_monitor/core/normalizer.py`

`Normalizer.normalize` 加分支:`elif raw.source == "wublock_news": parsed = parse_wublock(raw.raw)`。

## 数据流

```
RssFeedPoller(wublock_news)
  → RawEvent(source="wublock_news", source_kind="api", raw={title,description,link,guid}, text=去标签正文, url=link, occurred_at=published)
  → Normalizer → parse_wublock → score_severity 打分
  → Filter(min_severity=medium) 过滤掉非安全(low)条目
  → 中文内容,translator 检测到已是中文跳过翻译
  → TelegramPublisher
```

## 边界处理

- 格式检测靠 `root.tag`,RSS 2.0 路径零改动 → rekt 不回归
- Atom `<link>` 是 `href` 属性而非文本;feed 级与 entry 级有同名标签(`<id>`/`<link>`/`<updated>`)—— 只遍历 `<entry>` 的直接子节点
- 一个 `<entry>` 可能有多个 `<link>`(rel=alternate/self);吴说 entry 通常只有一个,取第一个 `href`
- CDATA + `type="html"`:ElementTree 透明处理 CDATA,`_strip_html` 去 `<p>` 等标签
- feed 不可达 / XML 解析失败:沿用现有 `log.warning + return` 模式

## 测试

- 新增 fixture `tests/fixtures/wublock_feed.xml`(截取真实 feed 数条,含一条安全事件 + 一条融资类)
- `RssFeedPoller` Atom 解析路径集成测试(仿 `tests/integration/test_rss_feed_source.py`),含一条 RSS 2.0 回归用例确认 rekt 不受影响
- `parse_wublock` 单测:安全条目(如"被盗"/"攻击者")评 `high`、融资条目评 `low`

## 不做的事(YAGNI)

- 不做跨源去重(已决定先接受重复)
- 不改 `filter` 逻辑或 `min_severity`
- 不为 wublock 单独建 X tier / 关键词白名单
- 不动 `@WuBlockchain` X 源
