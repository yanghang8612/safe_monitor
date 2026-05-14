# wublock123 RSS 接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把吴说(WuBlockchain)的 Atom feed `https://wublock123.com/feed` 接入 safe_monitor,只放行安全相关条目。

**Architecture:** 扩展现有 `RssFeedPoller` 支持 Atom(它 docstring 早已承诺但未实现),新增 `parse_wublock` 解析器(不强制 severity,交给关键词评分器),在 `normalizer` / `main.py` / `config.yaml` 三处接线。非安全条目(融资/市场)经评分器评为 `low`,被现有 `min_severity=medium` 过滤器丢弃。

**Tech Stack:** Python 3, `xml.etree.ElementTree`, `httpx`, `pytest` + `respx`,pydantic 模型。

设计来源:`docs/superpowers/specs/2026-05-14-wublock-rss-integration-design.md`

---

## 背景速览(实现者必读)

- feed 是 **Atom 格式**:根节点 `<feed>`、条目 `<entry>`、链接是 `<link href="...">` 属性、正文在 `<content type="html">` 或 `<summary type="html">`(CDATA 包裹)、时间是 ISO-8601 的 `<published>`/`<updated>`。
- 现有 `RssFeedPoller`(`src/safe_monitor/sources/rss_feed.py`)只解析 RSS 2.0 的 `<item>`/`<pubDate>`。`rekt_news` 走的就是它,**不能回归**。
- `_strip_html` / `_parse_pubdate` / `_parse_checkpoint` 已在 `src/safe_monitor/sources/rsshub_tg.py` 定义,`rss_feed.py` 已 import 前三者中需要的。`_parse_checkpoint` 是 ISO-8601 解析器,正好用于 Atom 时间。
- `parse_generic_tg`(`src/safe_monitor/core/parsers/generic_tg.py`)已含中英文关键词和 tx/地址/金额/链 的正则抽取,可直接复用。
- `score_severity`(`src/safe_monitor/core/severity.py`)含中文安全关键词(`被盗`/`协议漏洞`/`钓鱼` 等)。

---

## File Structure

- **修改** `src/safe_monitor/sources/rss_feed.py` — `RssFeedPoller` 加 Atom 格式分支;新增模块级 `_is_atom` / `_iter_entries` 函数统一 RSS 2.0 与 Atom 的条目抽取。
- **新建** `src/safe_monitor/core/parsers/wublock.py` — `parse_wublock(raw)` 解析器,仿 `parse_rekt` 形状但不强制 severity。
- **修改** `src/safe_monitor/core/normalizer.py` — 加 `wublock_news` 路由分支。
- **修改** `src/safe_monitor/main.py` — `_build_sources` 复用 `RssFeedPoller` 支持 `wublock_news`。
- **修改** `config.yaml` — `sources.api` 下新增 `wublock_news` 条目。
- **新建** `tests/fixtures/wublock_feed.xml` — Atom 测试夹具(1 条安全 + 1 条融资)。
- **新建** `tests/unit/test_parsers_wublock.py` — `parse_wublock` 单测。
- **修改** `tests/integration/test_rss_feed_source.py` — 加 Atom 解析集成测试。
- **修改** `tests/unit/test_normalizer.py` — 加 `wublock_news` 路由测试。

---

## Task 1: 给 RssFeedPoller 加 Atom 支持

**Files:**
- Create: `tests/fixtures/wublock_feed.xml`
- Modify: `src/safe_monitor/sources/rss_feed.py`
- Test: `tests/integration/test_rss_feed_source.py`

- [ ] **Step 1: 创建 Atom 测试夹具**

Create `tests/fixtures/wublock_feed.xml` with exactly this content:

```xml
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
    <id>https://www.wublock123.com</id>
    <title>吴说 - 区块链快讯与深度内容平台</title>
    <updated>2026-05-14T09:24:56.987Z</updated>
    <entry>
        <title type="html"><![CDATA[金融基础设施公司 Stitch 宣布完成 2500 万美元 A 轮融资]]></title>
        <id>https://www.wublock123.com/news/stitch-raises-25m-series-a-61068</id>
        <link href="https://www.wublock123.com/news/stitch-raises-25m-series-a-61068"/>
        <updated>2026-05-14T07:55:35.000Z</updated>
        <summary type="html"><![CDATA[吴说获悉，金融基础设施公司 Stitch 宣布完成 2500 万美元 A 轮融资，由 a16z 领投。]]></summary>
        <content type="html"><![CDATA[<p style="line-height: 1.75;">吴说获悉，金融基础设施公司 Stitch 宣布完成 2500 万美元 A 轮融资，由 a16z 领投，Arbor Ventures 等参投。</p>]]></content>
        <category label="融资"/>
        <published>2026-05-14T07:55:35.000Z</published>
    </entry>
    <entry>
        <title type="html"><![CDATA[某 DeFi 协议遭攻击被盗约 1200 万美元]]></title>
        <id>https://www.wublock123.com/news/defi-exploit-61067</id>
        <link href="https://www.wublock123.com/news/defi-exploit-61067"/>
        <updated>2026-05-14T07:45:47.000Z</updated>
        <summary type="html"><![CDATA[吴说获悉，某 DeFi 协议遭攻击，攻击者通过协议漏洞被盗约 1200 万美元。]]></summary>
        <content type="html"><![CDATA[<p style="line-height: 1.75;">吴说获悉，某 DeFi 协议在以太坊上遭攻击，攻击者通过协议漏洞被盗约 1200 万美元。</p>]]></content>
        <category label="安全"/>
        <published>2026-05-14T07:45:47.000Z</published>
    </entry>
</feed>
```

- [ ] **Step 2: 写 Atom 解析的失败测试**

Append to `tests/integration/test_rss_feed_source.py`:

```python
@pytest.mark.asyncio
async def test_rss_parses_atom_feed(tmp_path: Path):
    db = Database(tmp_path / "r.db")
    await db.init()
    body = Path("tests/fixtures/wublock_feed.xml").read_text()

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://wublock123.com/feed").mock(
            return_value=Response(200, text=body)
        )
        poller = RssFeedPoller(
            name="wublock_news",
            endpoint="https://wublock123.com/feed",
            poll_interval_seconds=300,
            db=db,
        )
        q: asyncio.Queue = asyncio.Queue()
        await poller.poll_once(q)

    got = []
    while not q.empty():
        got.append(await q.get())

    assert len(got) == 2
    titles = {e.raw["title"] for e in got}
    assert "某 DeFi 协议遭攻击被盗约 1200 万美元" in titles
    # <link href="..."> attribute must be picked up, not the empty element text
    assert all(e.url and e.url.startswith("https://www.wublock123.com/news/") for e in got)
    # <id> becomes external_id
    assert all(e.external_id.startswith("https://www.wublock123.com/news/") for e in got)
    # body text stripped of <p> tags
    sec = next(e for e in got if "遭攻击" in e.raw["title"])
    assert "<p" not in sec.text
    assert "协议漏洞" in sec.text
    assert all(e.source_kind == "api" for e in got)
    await db.close()


@pytest.mark.asyncio
async def test_rss_atom_second_poll_skips_seen_items(tmp_path: Path):
    db = Database(tmp_path / "r.db")
    await db.init()
    body = Path("tests/fixtures/wublock_feed.xml").read_text()

    poller = RssFeedPoller(
        name="wublock_news",
        endpoint="https://wublock123.com/feed",
        poll_interval_seconds=300,
        db=db,
    )
    q: asyncio.Queue = asyncio.Queue()

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://wublock123.com/feed").mock(return_value=Response(200, text=body))
        await poller.poll_once(q)
    while not q.empty():
        await q.get()

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://wublock123.com/feed").mock(return_value=Response(200, text=body))
        await poller.poll_once(q)
    assert q.empty()
    await db.close()
```

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/integration/test_rss_feed_source.py::test_rss_parses_atom_feed -v`
Expected: FAIL — 现有代码只找 `<item>`,Atom 的 `<entry>` 不会被解析,`len(got) == 2` 断言失败(got 为空)。

- [ ] **Step 4: 实现 Atom 支持**

Replace the **entire contents** of `src/safe_monitor/sources/rss_feed.py` with:

```python
from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import structlog

from safe_monitor.core.models import RawEvent
from safe_monitor.sources.base import Source
from safe_monitor.sources.rsshub_tg import _parse_checkpoint, _parse_pubdate, _strip_html
from safe_monitor.storage.db import Database

log = structlog.get_logger(__name__)


def _local_tag(tag: str) -> str:
    """Strip an XML namespace prefix: '{ns}item' -> 'item'."""
    return tag.split("}", 1)[-1]


def _is_atom(root: ET.Element) -> bool:
    """True for an Atom <feed> root, False for an RSS 2.0 <rss> root."""
    return _local_tag(root.tag) == "feed"


def _iter_entries(
    root: ET.Element,
) -> Iterator[tuple[str, str, str, str | None, datetime | None]]:
    """Yield (guid, title, description, link, pub) for each feed entry.

    Handles RSS 2.0 (<item>/<pubDate>) and Atom (<entry>/<published>)
    uniformly. Only the direct children of each entry are read, so
    feed-level <id>/<link>/<updated> elements never leak into an entry.
    """
    atom = _is_atom(root)
    entry_tag = "entry" if atom else "item"
    for node in root.iter():
        if not node.tag.endswith(entry_tag):
            continue
        guid = ""
        title = ""
        description = ""
        link: str | None = None
        pub: datetime | None = None
        for child in node:
            tag = _local_tag(child.tag)
            text = (child.text or "").strip() if child.text else ""
            if atom:
                if tag == "id":
                    guid = text
                elif tag == "title":
                    title = text
                elif tag == "content":
                    if text:
                        description = text
                elif tag == "summary":
                    if text and not description:
                        description = text
                elif tag == "link":
                    href = child.get("href")
                    if href and link is None:
                        link = href
                elif tag == "published":
                    pub = _parse_checkpoint(text)
                elif tag == "updated":
                    pub = pub or _parse_checkpoint(text)
            else:
                if tag == "guid":
                    guid = text
                elif tag == "title":
                    title = text
                elif tag == "description":
                    description = text
                elif tag == "link":
                    link = text or None
                elif tag == "pubDate":
                    pub = _parse_pubdate(text)
        yield guid, title, description, link, pub


class RssFeedPoller(Source):
    """Generic RSS 2.0 / Atom poller. Used for Rekt.news, wublock123, and any
    other standalone feed (i.e. not RSSHub-wrapped Telegram channels).

    Cursor: ISO-8601 string of the most recently seen item's pubDate.
    Emits RawEvent with source_kind='api'.
    """

    def __init__(
        self,
        *,
        name: str,
        endpoint: str,
        poll_interval_seconds: int,
        db: Database,
    ):
        self.name = name
        self._url = endpoint
        self._interval = poll_interval_seconds
        self._db = db

    async def poll_once(self, sink: asyncio.Queue[RawEvent]) -> None:
        cp = await self._db.get_checkpoint(self.name)
        last_cursor = cp["cursor"] if cp and cp.get("cursor") else ""
        last_pubdate = _parse_checkpoint(last_cursor) or datetime.fromtimestamp(0, tz=UTC)

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(self._url)
            r.raise_for_status()
            feed_text = r.text

        try:
            root = ET.fromstring(feed_text)
        except ET.ParseError as e:
            log.warning("rss.parse_error", source=self.name, error=str(e))
            return

        new_max = last_pubdate
        emitted = 0
        for guid, title, description, link, pub in _iter_entries(root):
            if pub is not None and pub <= last_pubdate:
                continue

            clean = _strip_html(description) or title
            if not clean:
                continue

            ev = RawEvent(
                source=self.name,
                source_kind="api",
                external_id=guid or link or clean[:64],
                received_at=datetime.now(UTC),
                raw={"title": title, "description": description, "link": link, "guid": guid},
                text=clean,
                url=link,
                occurred_at=pub,
            )
            await sink.put(ev)
            if pub is not None and pub > new_max:
                new_max = pub
            emitted += 1

        if new_max > last_pubdate:
            await self._db.set_checkpoint(self.name, kind="rss", cursor=new_max.isoformat())
        log.info("rss.poll_done", source=self.name, emitted=emitted)

    async def run(self, sink: asyncio.Queue[RawEvent]) -> None:
        while True:
            try:
                await self.poll_once(sink)
            except Exception as e:
                log.warning("rss.poll_error", source=self.name, error=str(e))
            await asyncio.sleep(self._interval)
```

Note: 这是把原逐项抽取逻辑提到模块级 `_iter_entries`,RSS 2.0 分支(`else:`)与原代码逐字一致 —— rekt 不会回归。

- [ ] **Step 5: 跑测试确认通过(含 rekt 回归)**

Run: `pytest tests/integration/test_rss_feed_source.py -v`
Expected: PASS — 全部 5 个测试通过(原 3 个 rekt RSS 2.0 测试 + 2 个新 Atom 测试)。

- [ ] **Step 6: 提交**

```bash
git add src/safe_monitor/sources/rss_feed.py tests/fixtures/wublock_feed.xml tests/integration/test_rss_feed_source.py
git commit -m "feat(rss): add Atom feed support to RssFeedPoller"
```

---

## Task 2: 新增 parse_wublock 解析器

**Files:**
- Create: `src/safe_monitor/core/parsers/wublock.py`
- Test: `tests/unit/test_parsers_wublock.py`

- [ ] **Step 1: 写 parse_wublock 的失败测试**

Create `tests/unit/test_parsers_wublock.py` with exactly this content:

```python
from safe_monitor.core.parsers.wublock import parse_wublock
from safe_monitor.core.severity import score as score_severity


def _raw(title: str, description: str, link: str) -> dict:
    return {"title": title, "description": description, "link": link, "guid": link}


def test_parse_wublock_keeps_real_title_and_strips_body():
    raw = _raw(
        "某 DeFi 协议遭攻击被盗约 1200 万美元",
        "<p style=\"line-height: 1.75;\">吴说获悉，某 DeFi 协议遭攻击，攻击者通过协议漏洞被盗约 1200 万美元。</p>",
        "https://www.wublock123.com/news/defi-exploit-61067",
    )
    parsed = parse_wublock(raw)
    # title is the headline, not the first line of the body
    assert parsed["title"] == "某 DeFi 协议遭攻击被盗约 1200 万美元"
    # body stripped of HTML tags
    assert "<p" not in parsed["body"]
    assert "协议漏洞" in parsed["body"]
    assert parsed["url"] == "https://www.wublock123.com/news/defi-exploit-61067"
    assert isinstance(parsed["category"], list)
    # parser does NOT force a severity hint — scorer decides
    assert parsed.get("severity") is None


def test_parse_wublock_security_item_scores_high():
    raw = _raw(
        "某 DeFi 协议遭攻击被盗约 1200 万美元",
        "<p>吴说获悉，某 DeFi 协议遭攻击，攻击者通过协议漏洞被盗约 1200 万美元。</p>",
        "https://www.wublock123.com/news/defi-exploit-61067",
    )
    parsed = parse_wublock(raw)
    sev = score_severity(f"{parsed['title']} {parsed['body']}", parsed.get("loss_usd"))
    assert sev.name == "high"


def test_parse_wublock_funding_item_scores_low():
    raw = _raw(
        "金融基础设施公司 Stitch 宣布完成 2500 万美元 A 轮融资",
        "<p>吴说获悉，金融基础设施公司 Stitch 宣布完成 2500 万美元 A 轮融资，由 a16z 领投。</p>",
        "https://www.wublock123.com/news/stitch-61068",
    )
    parsed = parse_wublock(raw)
    sev = score_severity(f"{parsed['title']} {parsed['body']}", parsed.get("loss_usd"))
    # non-security news must fall to 'low' so min_severity=medium filters it
    assert sev.name == "low"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/test_parsers_wublock.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'safe_monitor.core.parsers.wublock'`。

- [ ] **Step 3: 实现 parse_wublock**

Create `src/safe_monitor/core/parsers/wublock.py` with exactly this content:

```python
from __future__ import annotations

import html
import re
from typing import Any

from safe_monitor.core.parsers.generic_tg import parse_generic_tg

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return html.unescape(_TAG_RE.sub("", s or "")).strip()


def parse_wublock(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse a wublock123 (吴说) Atom feed entry.

    Unlike parse_rekt — which forces severity=high because every rekt.news
    post is a confirmed incident — wublock123 carries all-category crypto
    news. We deliberately omit a `severity` hint so the normalizer's
    keyword scorer runs: funding/market items score 'low' and get dropped
    by min_severity=medium, leaving only security-relevant entries.

    tx/address/loss/chain/category extraction is delegated to
    parse_generic_tg, which already handles Chinese + English vocabulary.
    """
    title = (raw.get("title") or "").strip() or "(吴说)"
    link = (raw.get("link") or "").strip() or None
    body = _strip_html(raw.get("description") or "") or title

    extracted = parse_generic_tg(body)

    return {
        "title": title,
        "body": body,
        "url": link,
        "tx_hash": extracted.get("tx_hash"),
        "attacker_addr": extracted.get("attacker_addr"),
        "loss_usd": extracted.get("loss_usd"),
        "chain": extracted.get("chain"),
        "category": extracted.get("category") or [],
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/test_parsers_wublock.py -v`
Expected: PASS — 3 个测试全过。

- [ ] **Step 5: 提交**

```bash
git add src/safe_monitor/core/parsers/wublock.py tests/unit/test_parsers_wublock.py
git commit -m "feat(parsers): add wublock123 feed parser"
```

---

## Task 3: 接线 normalizer / main.py / config.yaml

**Files:**
- Modify: `src/safe_monitor/core/normalizer.py`
- Modify: `src/safe_monitor/main.py`
- Modify: `config.yaml`
- Test: `tests/unit/test_normalizer.py`

- [ ] **Step 1: 写 normalizer 路由的失败测试**

Append to `tests/unit/test_normalizer.py`:

```python
def test_normalizer_routes_wublock_security_item_to_high():
    from datetime import UTC, datetime

    from safe_monitor.core.models import RawEvent, Severity
    from safe_monitor.core.normalizer import Normalizer

    raw = RawEvent(
        source="wublock_news",
        source_kind="api",
        external_id="https://www.wublock123.com/news/defi-exploit-61067",
        received_at=datetime.now(UTC),
        raw={
            "title": "某 DeFi 协议遭攻击被盗约 1200 万美元",
            "description": "<p>吴说获悉，某 DeFi 协议遭攻击，攻击者通过协议漏洞被盗约 1200 万美元。</p>",
            "link": "https://www.wublock123.com/news/defi-exploit-61067",
            "guid": "https://www.wublock123.com/news/defi-exploit-61067",
        },
        text="吴说获悉，某 DeFi 协议遭攻击，攻击者通过协议漏洞被盗约 1200 万美元。",
        url="https://www.wublock123.com/news/defi-exploit-61067",
    )
    ev = Normalizer().normalize(raw)
    assert ev is not None
    assert ev.title == "某 DeFi 协议遭攻击被盗约 1200 万美元"
    assert ev.severity == Severity.high
    assert ev.url == "https://www.wublock123.com/news/defi-exploit-61067"


def test_normalizer_routes_wublock_funding_item_to_low():
    from datetime import UTC, datetime

    from safe_monitor.core.models import RawEvent, Severity
    from safe_monitor.core.normalizer import Normalizer

    raw = RawEvent(
        source="wublock_news",
        source_kind="api",
        external_id="https://www.wublock123.com/news/stitch-61068",
        received_at=datetime.now(UTC),
        raw={
            "title": "金融基础设施公司 Stitch 宣布完成 2500 万美元 A 轮融资",
            "description": "<p>吴说获悉，金融基础设施公司 Stitch 宣布完成 2500 万美元 A 轮融资，由 a16z 领投。</p>",
            "link": "https://www.wublock123.com/news/stitch-61068",
            "guid": "https://www.wublock123.com/news/stitch-61068",
        },
        text="吴说获悉，金融基础设施公司 Stitch 宣布完成 2500 万美元 A 轮融资，由 a16z 领投。",
        url="https://www.wublock123.com/news/stitch-61068",
    )
    ev = Normalizer().normalize(raw)
    assert ev is not None
    assert ev.severity == Severity.low
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/test_normalizer.py::test_normalizer_routes_wublock_security_item_to_high -v`
Expected: FAIL — `Normalizer.normalize` 对未知 source 走 `else: return None`,`ev is not None` 断言失败。

- [ ] **Step 3: 改 normalizer.py、main.py、config.yaml**

3a. In `src/safe_monitor/core/normalizer.py`, add the import next to the other parser imports (after the `parse_rekt` import line):

```python
from safe_monitor.core.parsers.rekt import parse_rekt
from safe_monitor.core.parsers.wublock import parse_wublock
```

3b. In `src/safe_monitor/core/normalizer.py`, add a routing branch right after the `rekt_news` branch:

```python
        elif raw.source == "rekt_news":
            parsed = parse_rekt(raw.raw)
        elif raw.source == "wublock_news":
            parsed = parse_wublock(raw.raw)
```

3c. In `src/safe_monitor/main.py`, change the `rekt_news` branch in `_build_sources` to also handle `wublock_news`. Replace:

```python
        elif api.name == "rekt_news":
            sources.append(
                RssFeedPoller(
                    name="rekt_news",
                    endpoint=api.endpoint,
                    poll_interval_seconds=api.poll_interval_seconds,
                    db=db,
                )
            )
```

with:

```python
        elif api.name in ("rekt_news", "wublock_news"):
            sources.append(
                RssFeedPoller(
                    name=api.name,
                    endpoint=api.endpoint,
                    poll_interval_seconds=api.poll_interval_seconds,
                    db=db,
                )
            )
```

3d. In `config.yaml`, add a new entry under `sources.api:` immediately after the `rekt_news` block (after the `poll_interval_seconds: 3600` line, before the blank line that precedes the `telegram:` comment):

```yaml
    # wublock123 (吴说): all-category Chinese crypto newsflash in Atom format.
    # Non-security items (funding, market) score 'low' and are dropped by
    # min_severity=medium — only security-relevant entries reach the channel.
    - name: wublock_news
      endpoint: "https://wublock123.com/feed"
      poll_interval_seconds: 300
```

- [ ] **Step 4: 跑 normalizer 测试确认通过**

Run: `pytest tests/unit/test_normalizer.py -v`
Expected: PASS — 含 2 个新 wublock 路由测试在内的全部测试通过。

- [ ] **Step 5: 跑完整测试套件确认无回归**

Run: `pytest -q`
Expected: PASS — 全部测试通过,无回归。

- [ ] **Step 6: 提交**

```bash
git add src/safe_monitor/core/normalizer.py src/safe_monitor/main.py config.yaml tests/unit/test_normalizer.py
git commit -m "feat: wire wublock123 RSS source into pipeline"
```

---

## Self-Review 结果

**Spec 覆盖检查:**
- Atom 格式支持 → Task 1 ✓
- Atom 时间解析复用 `_parse_checkpoint` → Task 1 Step 4 `_iter_entries` 的 `published`/`updated` 分支 ✓
- `config.yaml` 新增 `wublock_news` → Task 3 Step 3d ✓
- `main.py` 复用 `RssFeedPoller` → Task 3 Step 3c ✓
- `parse_wublock` 仿 `parse_rekt`、不强制 severity → Task 2 ✓
- `parse_wublock` 复用 `parse_generic_tg` 抽取 → Task 2 Step 3 ✓
- `normalizer` 路由分支 → Task 3 Step 3a/3b ✓
- 边界:格式检测靠 `root.tag`、RSS 2.0 路径不动 → Task 1 Step 4(`else:` 分支逐字保留)✓
- 边界:`<link href>` 属性、只读 entry 直接子节点 → Task 1 `_iter_entries` ✓
- 边界:CDATA + `type="html"` → `_strip_html` 处理,Task 1 测试断言 `"<p" not in` ✓
- 测试:fixture + Atom 集成测试 + RSS 2.0 回归 + `parse_wublock` 单测 → Task 1/2/3 ✓

**占位符扫描:** 无 TBD/TODO,每个代码步骤含完整代码 ✓

**类型一致性:** `parse_wublock` 返回 dict,key(`title`/`body`/`url`/`tx_hash`/`attacker_addr`/`loss_usd`/`chain`/`category`)与 `normalizer` 消费的 key 一致;`_iter_entries` 返回的 5-元组与 `poll_once` 解包一致;`RawEvent` 字段与 `models.py` 定义一致 ✓
