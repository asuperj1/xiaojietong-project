"""配置驱动的采集流水线（B27）—— 按 C21 配置抓取并入库。

一条源的完整流程：

```
  C21 配置 sources[i]
        │
        ├─ FetchGuard.before(source, url)   ← robots + 限速（B26），拒绝就记 blocked
        ├─ HttpFetcher.get(列表页)
        ├─ extract_list()                    ← selectors.list，解析出 [{title, url}]
        └─ 对每条（上限 limit）：
               FetchGuard.before(source, 详情页 url)   ← 逐条过闸门，不是只过一次
               HttpFetcher.get(详情页)
               extract_detail()               ← selectors.title / content / date
               NoticeStore.exists() ? skip : insert   ← 幂等：source + title
```

**验收对应**：
- "配置新源后无需改代码即可入库" —— 全流程只读 `sources[]` 的字段，代码里没有源名硬编码；
- "遵守 robots.txt + 限速" —— 每次请求（列表页与**每个详情页**）都过 `FetchGuard`；
- "有日志可追溯" —— 每个请求都落 `collect.jsonl`（B26 的 CollectJournal）。

零业务依赖：`store` 通过参数注入（默认实现才去碰 `cpp_bridge`），
所以整条流水线可以在单测里用假 fetcher + 假 store 全量跑通。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from .dom import absolutize, clean_text, parse_html, select_all, select_first
from .fetcher import FetchError, HttpFetcher
from .robots import DEFAULT_USER_AGENT


# ------------------------------------------------------------------ 结构 ----

@dataclass
class SourceResult:
    """一条源的采集结果（也是 CLI 的输出单元）。"""

    key: str
    name: str = ""
    ok: bool = True
    blocked: str = ""                 # 列表页被 robots 拒绝时的原因
    listed: int = 0                   # 列表页解析出的条数
    fetched: int = 0                  # 成功抓到的详情页数
    inserted: int = 0                 # 新入库
    skipped: int = 0                  # 已存在（幂等跳过）
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "key": self.key, "name": self.name, "ok": self.ok, "blocked": self.blocked,
            "listed": self.listed, "fetched": self.fetched,
            "inserted": self.inserted, "skipped": self.skipped,
            "dry_run": self.dry_run, "errors": self.errors,
        }


def load_sources(config_path: str | Path) -> list[dict[str, Any]]:
    """读 C21 的学校配置，取出 sources 数组（yml/yaml 需 PyYAML，json 零依赖）。"""
    path = Path(config_path)
    if not path.is_file():
        raise SystemExit(f"配置不存在：{path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yml", ".yaml"):
        try:
            import yaml  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise SystemExit(f"读取 YAML 需要 PyYAML：{exc}") from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise SystemExit(f"配置顶层必须是对象：{path}")
    sources = data.get("sources") or []
    if not isinstance(sources, list):
        raise SystemExit(f"{path} 的 sources 必须是数组")
    return [s for s in sources if isinstance(s, dict)]


def _field(source: Any, name: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


# ------------------------------------------------------------------ 提取 ----

def _selectors(source: Any) -> dict[str, str]:
    """取 `selectors` 映射，兼容 C21 的 pydantic 模型与普通 dict。"""
    sel = _field(source, "selectors", None) or {}
    if isinstance(sel, Mapping):
        return {str(k): str(v or "") for k, v in sel.items()}
    return {name: str(getattr(sel, name, "") or "")
            for name in ("list", "title", "content", "date")}


def extract_list(html: str, source: Any, base_url: str) -> list[dict[str, str]]:
    """按 `selectors.list` 解析列表页，返回 `[{title, url}]`（去重、保持文档顺序）。"""
    expr = _selectors(source).get("list", "")
    if not expr:
        return []
    root = parse_html(html)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for node in select_all(root, expr):
        href = node.get("href") or node.get("data-href")
        if not href:
            continue
        url = absolutize(href, base_url)
        if url in seen:
            continue
        seen.add(url)
        title = clean_text(node.text()) or clean_text(node.get("title"))
        out.append({"title": title, "url": url})
    return out


def extract_detail(html: str, source: Any, url: str) -> dict[str, str]:
    """按 `selectors.title / content / date` 解析详情页。

    缺 `content` 选择器时退化为整页 `body` 文本（很多通知页正文就在 body 里）。
    """
    sels = _selectors(source)
    root = parse_html(html)

    def pick(name: str) -> str:
        expr = sels.get(name, "")
        if not expr:
            return ""
        node = select_first(root, expr)
        return clean_text(node.text()) if node is not None else ""

    title = pick("title")
    content = pick("content")
    if not content:
        body = select_first(root, "body")
        content = clean_text(body.text()) if body is not None else ""
    return {"title": title, "content": content, "publish_time": pick("date"), "url": url}


# ------------------------------------------------------------------ 入库 ----

class NoticeStore:
    """把采集条目写进 `campus_notice`。

    **幂等键是 `(source, title)`**：`campus_notice` 没有 url 列（DDL 里没有，
    也不该为采集单独加），而"同一个来源下标题相同"在本项目的数据里已经足够判重
    （通知标题带日期/编号，重复抓取只会拿到一模一样的标题）。
    查不到就插，查到就 skip —— 重跑整条流水线是安全的。
    """

    def __init__(self, *, query=None, execute=None) -> None:
        if query is None or execute is None:
            from app.db import cpp_bridge  # noqa: PLC0415 - 只有真实入库时才碰 DB

            query = query or cpp_bridge.query
            execute = execute or cpp_bridge.execute
        self._query = query
        self._execute = execute

    def exists(self, source_name: str, title: str) -> bool:
        rows = self._query(
            "SELECT id FROM campus_notice WHERE source = ? AND title = ? LIMIT 1",
            [source_name, title],
        )
        return bool(rows)

    def insert(self, *, title: str, content: str, source_name: str,
               category: str, publish_time: str = "") -> int:
        sql = (
            "INSERT INTO campus_notice (title, content, source, category, target_grade, publish_time) "
            "VALUES (?, ?, ?, ?, '', COALESCE(NULLIF(?, ''), CURRENT_TIMESTAMP))"
        )
        affected, new_id = self._execute(
            sql, [title, content, source_name, category or "综合", publish_time or ""]
        )
        return int(new_id or 0)


# ------------------------------------------------------------------ 编排 ----

def collect_source(
    source: Any,
    *,
    guard,
    fetcher: Optional[HttpFetcher] = None,
    store: Optional[NoticeStore] = None,
    limit: int = 20,
    dry_run: bool = False,
) -> SourceResult:
    """采集**一条源**。逐条（列表页 + 每个详情页）过合规闸门，全程留痕。"""
    key = str(_field(source, "key", "") or _field(source, "name", "") or "?")
    name = str(_field(source, "name", "") or key)
    url = str(_field(source, "url", "") or "")
    category = str(_field(source, "category", "") or "综合")
    result = SourceResult(key=key, name=name, dry_run=dry_run)

    if not url:
        result.ok = False
        result.errors.append("配置里没有 url")
        return result

    # UA 必须与 robots 判定时用的**一模一样**：用 A 去问"我能抓吗"、
    # 再用 B 去抓，等于绕过了刚拿到的许可。
    if fetcher is None:
        fetcher = HttpFetcher(user_agent=getattr(guard, "user_agent", "") or DEFAULT_USER_AGENT)
    if dry_run:
        store = None          # 试跑连数据库模块都不导入，没 DB 环境也能预览
    elif store is None:
        store = NoticeStore()

    # ① 列表页
    decision = guard.before(source, url)
    if not decision.allowed:
        result.blocked = decision.reason
        result.ok = False
        return result

    t0 = time.perf_counter()
    try:
        page = fetcher.get(url)
    except FetchError as exc:
        result.ok = False
        result.errors.append(f"列表页抓取失败：{exc}")
        guard.after(source, url, error=str(exc))
        return result
    guard.after(source, url, status=page.status, size=len(page),
                elapsed_ms=int((time.perf_counter() - t0) * 1000))

    # ② 解析列表
    try:
        listed = extract_list(page.text, source, page.final_url or url)
    except ValueError as exc:                 # 选择器写错 —— 明确报出来，别静默零条
        result.ok = False
        result.errors.append(f"selectors.list 无效：{exc}")
        return result
    result.listed = len(listed)

    # ③ 逐条详情（**每条都要过闸门**，不是只过一次）
    for item in listed[:max(0, limit)]:
        detail_url = item["url"]
        d2 = guard.before(source, detail_url)
        if not d2.allowed:
            result.errors.append(f"robots 拦截详情页：{detail_url}（{d2.reason}）")
            continue

        t1 = time.perf_counter()
        try:
            detail_page = fetcher.get(detail_url)
        except FetchError as exc:
            result.errors.append(f"详情页抓取失败：{detail_url}（{exc}）")
            guard.after(source, detail_url, error=str(exc))
            continue
        guard.after(source, detail_url, status=detail_page.status, size=len(detail_page),
                    elapsed_ms=int((time.perf_counter() - t1) * 1000))

        try:
            detail = extract_detail(detail_page.text, source, detail_url)
        except ValueError as exc:
            result.errors.append(f"详情页选择器无效：{exc}")
            continue
        result.fetched += 1

        title = detail["title"] or item["title"]
        if not title:
            result.errors.append(f"标题为空，跳过：{detail_url}")
            continue
        if dry_run:
            continue

        if store.exists(name, title):
            result.skipped += 1
            continue
        store.insert(title=title, content=detail["content"], source_name=name,
                     category=category, publish_time=detail["publish_time"])
        result.inserted += 1

    result.ok = not result.errors
    return result


def run_config(
    config_path: str | Path,
    *,
    guard,
    fetcher: Optional[HttpFetcher] = None,
    store: Optional[NoticeStore] = None,
    only: Iterable[str] = (),
    limit: int = 20,
    dry_run: bool = False,
) -> list[SourceResult]:
    """按配置跑全部**启用中**的源（`--source` 可只跑其中几个）。"""
    wanted = {s.strip() for s in only if s.strip()}
    results: list[SourceResult] = []
    for source in load_sources(config_path):
        key = str(_field(source, "key", "") or "")
        if wanted and key not in wanted:
            continue
        if not _field(source, "enabled", True):
            continue
        results.append(collect_source(source, guard=guard, fetcher=fetcher, store=store,
                                      limit=limit, dry_run=dry_run))
    return results
