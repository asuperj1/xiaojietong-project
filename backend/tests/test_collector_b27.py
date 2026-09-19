# -*- coding: utf-8 -*-
"""B27 配置驱动采集器 —— 纯离线单测。

不碰网络（HTTP 与 robots 都是假实现）、不碰数据库（store 是假的）、不碰 Ollama，
因此在任何机器上都能跑出确定结论。

覆盖范围：
- `dom.py` 的迷你选择器（含**真实 C21 配置里出现的全部写法**）；
- 列表页 / 详情页提取（相对链接、去重、缺选择器时的退化）；
- 流水线：robots **逐条**拦截、限速作用于每一次请求、Crawl-delay、幂等入库、
  `--dry-run`、`--limit`、单条失败不影响其余；
- CLI：`run --dry-run` 全流程、配置缺失、`check` 回归。
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.collector import FetchGuard, RobotsGate, reset_limiters
from app.collector.__main__ import main as cli_main
from app.collector.dom import (
    absolutize,
    clean_text,
    parse_html,
    parse_selector,
    select_all,
    select_first,
)
from app.collector.fetcher import FetchError, HttpFetcher
from app.collector.journal import CollectJournal
from app.collector.pipeline import (
    collect_source,
    extract_detail,
    extract_list,
    load_sources,
    run_config,
)
from app.collector.robots import DEFAULT_USER_AGENT
from app.collector.safety import check_url

# ------------------------------------------------------------------ 夹具 ----

ORIGIN = "https://lib.example.edu.cn"
ROBOTS_URL = ORIGIN + "/robots.txt"
LIST_URL = ORIGIN + "/notice/index.htm"

ROBOTS_ALLOW = "User-agent: *\nDisallow:\n"
ROBOTS_DELAY = "User-agent: *\nDisallow:\nCrawl-delay: 5\n"
ROBOTS_DENY_PRIVATE = "User-agent: *\nDisallow: /notice/private/\n"
ROBOTS_DENY_ALL = "User-agent: *\nDisallow: /\n"

SOURCE = {
    "key": "lib-notice",
    "name": "图书馆通知",
    "url": LIST_URL,
    "enabled": True,
    "type": "html_list",
    "category": "图书馆",
    "rate_limit_qps": 1.0,
    "respect_robots": True,
    "selectors": {
        "list": "ul.news-list li a",
        "title": "h1.article-title",
        "content": "div.article-content",
        "date": "span.publish-date",
    },
}

LIST_HTML = """<html><body>
<ul class="news-list">
  <li><a href="/notice/1.html">  第一条通知  </a></li>
  <li><a href="/notice/2.html">第二条通知</a></li>
  <li><a href="/notice/1.html">重复的第一条</a></li>
  <li><a href="3.html">第三条通知</a></li>
</ul>
<a href="/notice/9.html">列表之外的链接</a>
</body></html>"""

LIST_WITH_PRIVATE = """<html><body><ul class="news-list">
  <li><a href="/notice/1.html">第一条通知</a></li>
  <li><a href="/notice/private/secret.html">内部通知</a></li>
  <li><a href="/notice/2.html">第二条通知</a></li>
</ul></body></html>"""

DETAIL_HTML = """<html><head><title>忽略</title></head><body>
<div class="article">
  <h1 class="article-title">第{n}条通知</h1>
  <span class="publish-date">2026-09-0{n}</span>
  <div class="article-content"><p>正文{n}</p></div>
</div>
</body></html>"""

DETAIL_NO_CONTENT_SELECTOR = """<html><body><div class="wrap">
  <h1 class="article-title">只有标题</h1>
  <p>正文直接躺在 body 里。</p>
</div></body></html>"""


class FakeClock:
    """假时钟 + 假睡眠：同一个对象注入 clock / sleep，时间随睡眠推进。"""

    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


class FakeRobots:
    """robots.txt 假取手：未登记的 origin 一律 404（= 无限制）。"""

    def __init__(self, routes: dict | None = None) -> None:
        self.routes = dict(routes or {})
        self.calls: list[str] = []

    def __call__(self, url: str, timeout: float, user_agent: str):
        self.calls.append(url)
        return self.routes.get(url, (404, ""))


class FakeHttp:
    """注入 `HttpFetcher` 的 opener；未登记的 URL 抛 `FetchError`（模拟 404/连不上）。"""

    def __init__(self, pages: dict) -> None:
        self.pages = dict(pages)
        self.calls: list[str] = []

    def __call__(self, url: str, timeout: float, user_agent: str):
        self.calls.append(url)
        value = self.pages.get(url)
        if value is None:
            raise FetchError("HTTP 404")
        if isinstance(value, Exception):
            raise value
        return value


class FakeStore:
    """假的 `campus_notice` 写入器；幂等键与真实实现一致：source + title。"""

    def __init__(self, existing=()) -> None:
        self.existing = set(existing)
        self.rows: list[dict] = []

    def exists(self, source_name: str, title: str) -> bool:
        return (source_name, title) in self.existing

    def insert(self, *, title, content, source_name, category, publish_time=""):
        if self.exists(source_name, title):
            return 0
        self.existing.add((source_name, title))
        self.rows.append({"title": title, "content": content, "source": source_name,
                          "category": category, "publish_time": publish_time})
        return len(self.rows)


def _detail_url(n: int) -> str:
    return f"{ORIGIN}/notice/{n}.html"


def _pages(list_html: str = LIST_HTML, numbers=(1, 2, 3)) -> dict:
    pages = {LIST_URL: (200, list_html, LIST_URL)}
    for n in numbers:
        pages[_detail_url(n)] = (200, DETAIL_HTML.format(n=n), _detail_url(n))
    return pages


def _guard(clock: FakeClock, *, routes: dict | None = None, qps: float = 0.0, **kw) -> FetchGuard:
    gate = RobotsGate(fetcher=FakeRobots(routes), clock=clock)
    return FetchGuard(
        robots=gate,
        journal=CollectJournal(path=""),                     # 单测不落盘
        default_qps=qps,
        limiter_kwargs={"clock": clock, "sleep": clock.sleep},
        **kw,
    )


def _run(source=None, *, pages=None, clock=None, store=None, routes=None, qps=0.0, **kw):
    """跑一条源，返回 (result, store, http, clock)。"""
    clock = clock or FakeClock()
    http = FakeHttp(pages if pages is not None else _pages())
    store = store if store is not None else FakeStore()
    guard = _guard(clock, routes=routes, qps=qps)
    result = collect_source(source or SOURCE, guard=guard,
                            fetcher=HttpFetcher(opener=http), store=store, **kw)
    return result, store, http, clock


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个用例前后清空全局限速器注册表，避免互相污染。"""
    reset_limiters()
    yield
    reset_limiters()


@pytest.fixture(autouse=True)
def _no_log_file(monkeypatch):
    """兜底：任何路径上构造的日志器都不落盘，免得测试往工作区写 data/collect。"""
    monkeypatch.setenv("XJT_COLLECT_LOG", "")


# =============================================================== 选择器 ====


def test_selector_picks_list_links_in_document_order():
    root = parse_html(LIST_HTML)
    nodes = select_all(root, "ul.news-list li a")
    assert [clean_text(n.text()) for n in nodes] == [
        "第一条通知", "第二条通知", "重复的第一条", "第三条通知",
    ]
    # 列表之外的链接不该被选中
    assert all("9.html" not in (n.get("href") or "") for n in nodes)


def test_selector_supports_id_and_tag_class():
    root = parse_html('<div id="main"><h2 class="t a">标题</h2></div>')
    assert select_first(root, "#main h2.t") is not None
    assert select_first(root, "div#main h2") is not None
    assert select_first(root, "h2.missing") is None


@pytest.mark.parametrize("expr", ["div > a", "a[href]", "a:hover", "li:nth-child(2)", ""])
def test_unsupported_selector_raises_instead_of_silent_empty(expr):
    """不认识的写法必须**报错**：否则配置写错了，采集只会悄悄没数据。"""
    with pytest.raises(ValueError):
        parse_selector(expr)


def test_real_adapter_configs_use_only_supported_selectors():
    """"配置新源无需改代码"的前提：现有配置里的选择器实现都认。"""
    pytest.importorskip("yaml")           # 配置文件是 YAML
    configs = Path(__file__).resolve().parents[1] / "app" / "adapters" / "configs"
    checked = 0
    for path in sorted(configs.glob("*.y*ml")):
        for src in load_sources(path):
            for name, expr in (src.get("selectors") or {}).items():
                if not expr:
                    continue
                parse_selector(str(expr))          # 不认识就抛 ValueError
                checked += 1
    assert checked > 0, "没有从真实配置里读到任何选择器，测试失去意义"


def test_absolutize_resolves_relative_links():
    assert absolutize("/notice/1.html", LIST_URL) == ORIGIN + "/notice/1.html"
    assert absolutize("3.html", LIST_URL) == ORIGIN + "/notice/3.html"
    assert absolutize(ORIGIN + "/x.htm", LIST_URL) == ORIGIN + "/x.htm"


# ============================================================ 解析提取 ====


def test_extract_list_dedupes_and_absolutizes():
    items = extract_list(LIST_HTML, SOURCE, LIST_URL)
    assert [i["url"] for i in items] == [_detail_url(1), _detail_url(2), _detail_url(3)]
    assert items[0]["title"] == "第一条通知"          # 归一空白
    assert items[2]["url"] == ORIGIN + "/notice/3.html"   # 相对链接补全


def test_extract_list_without_selector_returns_empty():
    assert extract_list(LIST_HTML, {"selectors": {}}, LIST_URL) == []


def test_extract_list_raises_on_bad_selector():
    with pytest.raises(ValueError):
        extract_list(LIST_HTML, {"selectors": {"list": "a > b"}}, LIST_URL)


def test_extract_detail_reads_title_content_date():
    detail = extract_detail(DETAIL_HTML.format(n=2), SOURCE, _detail_url(2))
    assert detail["title"] == "第2条通知"
    assert detail["content"] == "正文2"
    assert detail["publish_time"] == "2026-09-02"


def test_extract_detail_falls_back_to_body_text():
    """很多通知页没有独立的正文容器：缺 content 选择器时取整页 body。"""
    src = {**SOURCE, "selectors": {"title": "h1.article-title"}}
    detail = extract_detail(DETAIL_NO_CONTENT_SELECTOR, src, LIST_URL)
    assert detail["title"] == "只有标题"
    assert "正文直接躺在 body 里。" in detail["content"]


def test_extract_detail_missing_hit_returns_empty_not_crash():
    detail = extract_detail("<html><body><p>x</p></body></html>", SOURCE, LIST_URL)
    assert detail["title"] == ""
    assert detail["content"] == "x"        # 退化为 body
    assert detail["publish_time"] == ""


# ============================================================== 流水线 ====


def test_collect_source_inserts_new_items():
    result, store, http, _ = _run()
    assert (result.ok, result.listed, result.fetched) == (True, 3, 3)
    assert (result.inserted, result.skipped) == (3, 0)
    assert [r["title"] for r in store.rows] == ["第1条通知", "第2条通知", "第3条通知"]
    assert store.rows[0]["source"] == "图书馆通知"      # 幂等键用的是源 name
    assert store.rows[0]["category"] == "图书馆"
    assert store.rows[0]["publish_time"] == "2026-09-01"
    assert http.calls == [LIST_URL, _detail_url(1), _detail_url(2), _detail_url(3)]


def test_collect_source_is_idempotent():
    """重跑整条流水线是安全的：已入库的按 (source, title) 跳过。"""
    pages = _pages()
    store = FakeStore()
    first, _, _, _ = _run(pages=pages, store=store)
    assert first.inserted == 3
    second, _, _, _ = _run(pages=pages, store=store)
    assert (second.inserted, second.skipped) == (0, 3)
    assert len(store.rows) == 3


def test_no_limit_means_all_items_are_fetched():
    """limit 是"最多抓几条"，不是"必须抓几条"：列表不足时全部抓完。"""
    result, _, http, _ = _run(limit=100)
    assert result.fetched == 3
    assert len(http.calls) == 1 + 3


def test_collect_source_blocked_by_robots_makes_no_request():
    result, store, http, _ = _run(routes={ROBOTS_URL: (200, ROBOTS_DENY_PRIVATE)},
                                  source={**SOURCE, "url": ORIGIN + "/notice/private/idx.htm"})
    assert result.ok is False
    assert result.blocked and "robots" in result.blocked
    assert http.calls == []              # 一条业务请求都没发出去
    assert (result.listed, result.inserted) == (0, 0)
    assert store.rows == []


def test_robots_is_checked_for_every_detail_page():
    """只检查列表页是不够的 —— 详情页路径同样可能被 robots 禁止。"""
    result, _, http, _ = _run(pages=_pages(LIST_WITH_PRIVATE, numbers=(1, 2)),
                              routes={ROBOTS_URL: (200, ROBOTS_DENY_PRIVATE)})
    assert result.inserted == 2
    assert len(result.errors) == 1
    assert "robots" in result.errors[0]
    assert ORIGIN + "/notice/private/secret.html" not in http.calls
    assert result.ok is False            # 有被拦项 → 明确标红，不静默吞掉


def test_every_request_goes_through_rate_limiter():
    """列表页与**每一个**详情页都要过限速（qps=1 → 4 次请求共等 3 秒）。"""
    _, _, http, clock = _run(qps=1.0)
    assert len(http.calls) == 4
    assert sum(clock.slept) == pytest.approx(3.0)


def test_crawl_delay_from_robots_wins_over_qps():
    """robots 的 Crawl-delay 与 qps 取更严的那个：5s vs 1s → 5s。"""
    _, _, _, clock = _run(qps=1.0, routes={ROBOTS_URL: (200, ROBOTS_DELAY)})
    assert sum(clock.slept) == pytest.approx(15.0)


def test_detail_fetch_failure_does_not_abort_the_rest():
    pages = _pages()
    pages[_detail_url(2)] = FetchError("HTTP 500")
    result, store, _, _ = _run(pages=pages)
    assert (result.fetched, result.inserted) == (2, 2)
    assert len(result.errors) == 1 and "详情页抓取失败" in result.errors[0]
    assert result.ok is False
    assert [r["title"] for r in store.rows] == ["第1条通知", "第3条通知"]


def test_list_fetch_failure_is_reported():
    result, _, _, _ = _run(pages={})
    assert result.ok is False
    assert result.blocked == ""
    assert result.errors and "列表页抓取失败" in result.errors[0]
    assert (result.listed, result.inserted) == (0, 0)


def test_limit_caps_detail_requests():
    result, _, http, _ = _run(limit=1)
    assert result.listed == 3            # 列表页仍然完整解析
    assert result.fetched == 1
    assert http.calls == [LIST_URL, _detail_url(1)]


def test_missing_url_is_reported_without_request():
    result, _, http, _ = _run(source={k: v for k, v in SOURCE.items() if k != "url"})
    assert result.ok is False
    assert "url" in result.errors[0]
    assert http.calls == []


def test_dry_run_never_touches_the_database(monkeypatch):
    """--dry-run 连 NoticeStore 都不该构造（没配 DB 的机器也能试跑）。"""
    built: list[int] = []

    class Boom:
        def __init__(self, *a, **kw):
            built.append(1)
            raise AssertionError("dry-run 不该构造 NoticeStore")

    monkeypatch.setattr("app.collector.pipeline.NoticeStore", Boom)
    result, _, http, _ = _run(dry_run=True)
    assert result.fetched == 3
    assert (result.inserted, result.skipped) == (0, 0)
    assert built == []
    assert len(http.calls) == 4          # 仍然真的抓了（只是不写）


def test_use_injected_store_instead_of_real_one():
    """非 dry-run 但显式传了 store 时，不该去碰数据库模块。"""
    store = FakeStore()
    result, _, _, _ = _run(store=store)
    assert result.inserted == 3
    assert len(store.rows) == 3


# ================================================================ 编排 ====


def _write_config(tmp_path: Path, sources: list[dict]) -> Path:
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"sources": sources}, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_sources_reads_json(tmp_path):
    path = _write_config(tmp_path, [SOURCE])
    assert [s["key"] for s in load_sources(path)] == ["lib-notice"]


def test_load_sources_rejects_non_list_sources(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"sources": {"key": "x"}}), encoding="utf-8")
    with pytest.raises(SystemExit):
        load_sources(path)


def test_run_config_skips_disabled_and_filters_by_key(tmp_path):
    disabled = {**SOURCE, "key": "off", "name": "已停用源", "enabled": False,
                "url": ORIGIN + "/off/"}
    path = _write_config(tmp_path, [SOURCE, disabled])
    clock = FakeClock()
    http = FakeHttp(_pages())
    guard = _guard(clock)
    fetcher = HttpFetcher(opener=http)
    store = FakeStore()

    every = run_config(path, guard=guard, fetcher=fetcher, store=store)
    assert [r.key for r in every] == ["lib-notice"]       # disabled 不跑

    only_missing = run_config(path, guard=guard, fetcher=fetcher, store=store, only=["off"])
    assert only_missing == []                            # 命中被停用的源 → 不跑

    only_hit = run_config(path, guard=guard, fetcher=fetcher, store=store, only=["lib-notice"])
    assert [r.key for r in only_hit] == ["lib-notice"]


def test_source_result_is_json_serialisable():
    result, _, _, _ = _run(dry_run=True)
    payload = json.loads(json.dumps(result.as_dict(), ensure_ascii=False))
    assert payload["key"] == "lib-notice" and payload["fetched"] == 3
    assert payload["dry_run"] is True


EMPTY_LIST_HTML = '<html><body><ul class="news-list"></ul></body></html>'


def test_empty_list_is_flagged_but_not_an_error():
    """"语法对、但一条都匹配不到"必须能看出来 —— 否则站点改版后采集会安静停掉。"""
    result, _, _, _ = _run(pages={LIST_URL: (200, EMPTY_LIST_HTML, LIST_URL)})
    assert (result.listed, result.ok) == (0, True)     # 默认不判失败：页面可能本来就空
    assert result.empty_list is True
    payload = result.as_dict()
    assert payload["empty_list"] is True
    assert payload["list_selector"] == "ul.news-list li a"   # 告警要指出是哪个选择器


def test_empty_list_flag_ignores_blocked_and_failed_sources():
    """被 robots 拒绝 / 抓取失败都已经有明确原因，不该再报"空列表"。"""
    blocked, _, _, _ = _run(routes={ROBOTS_URL: (200, ROBOTS_DENY_PRIVATE)},
                            source={**SOURCE, "url": ORIGIN + "/notice/private/idx.htm"})
    assert blocked.empty_list is False

    failed, _, _, _ = _run(pages={})
    assert failed.empty_list is False


# ================================================================= 日志 ====


def test_empty_log_env_means_no_file(monkeypatch, tmp_path):
    """XJT_COLLECT_LOG 显式设成空串 = 只走 logging、不落盘（与模块说明一致）。"""
    monkeypatch.setenv("XJT_COLLECT_LOG", "")
    assert CollectJournal().path is None

    monkeypatch.setenv("XJT_COLLECT_LOG", str(tmp_path / "c.jsonl"))
    assert CollectJournal().path == tmp_path / "c.jsonl"

    assert CollectJournal(path="").path is None


# ================================================================== CLI ====


def test_cli_run_dry_run_ok(tmp_path, monkeypatch):
    path = _write_config(tmp_path, [{**SOURCE, "respect_robots": False}])
    http = FakeHttp(_pages())
    monkeypatch.setattr("app.collector.__main__.HttpFetcher",
                        lambda **kw: HttpFetcher(opener=http))
    assert cli_main(["run", str(path), "--dry-run"]) == 0
    assert len(http.calls) == 4


def test_cli_run_missing_config_returns_1(tmp_path):
    assert cli_main(["run", str(tmp_path / "nope.json")]) == 1


def test_cli_run_reports_blocked_source(tmp_path, monkeypatch):
    """robots 拒绝时 CLI 必须返回 1（可被 CI/定时任务判失败），而不是静默成功。"""
    path = _write_config(tmp_path, [{**SOURCE, "respect_robots": True}])
    monkeypatch.setattr("app.collector.__main__.HttpFetcher",
                        lambda **kw: HttpFetcher(opener=FakeHttp(_pages())))
    monkeypatch.setattr("app.collector.__main__.FetchGuard",
                        lambda **kw: _guard(FakeClock(), routes={ROBOTS_URL: (200, ROBOTS_DENY_ALL)}))
    assert cli_main(["run", str(path), "--dry-run"]) == 1


def test_cli_check_offline_still_works(tmp_path):
    """回归：`check` 复用了 pipeline.load_sources，行为不变。"""
    path = _write_config(tmp_path, [SOURCE])
    assert cli_main(["check", str(path), "--offline"]) == 0


def test_cli_warns_on_empty_list_to_stderr(tmp_path, monkeypatch, capsys):
    """空列表默认不判失败，但必须在 **stderr** 留下显式信号（stdout 统计行太容易漏看）。"""
    path = _write_config(tmp_path, [{**SOURCE, "respect_robots": False}])
    http = FakeHttp({LIST_URL: (200, EMPTY_LIST_HTML, LIST_URL)})
    monkeypatch.setattr("app.collector.__main__.HttpFetcher",
                        lambda **kw: HttpFetcher(opener=http))

    assert cli_main(["run", str(path), "--dry-run"]) == 0     # 默认退出码仍是 0
    err = capsys.readouterr().err
    assert "列表选择器匹配到 0 条" in err
    assert "ul.news-list li a" in err                         # 指出实际用的选择器


def test_cli_fail_on_empty_returns_1(tmp_path, monkeypatch, capsys):
    """定时任务用 --fail-on-empty 把"没采到"变成"任务失败"。"""
    path = _write_config(tmp_path, [{**SOURCE, "respect_robots": False}])
    http = FakeHttp({LIST_URL: (200, EMPTY_LIST_HTML, LIST_URL)})
    monkeypatch.setattr("app.collector.__main__.HttpFetcher",
                        lambda **kw: HttpFetcher(opener=http))

    assert cli_main(["run", str(path), "--dry-run", "--fail-on-empty"]) == 1
    capsys.readouterr()                                       # 吃掉输出，别污染其它用例


# ========================================================== 真实请求 ====
#
# 上面所有用例都注入假 opener（这是刻意的：离线、确定、CI 跑得动），
# 但也正因为如此，"请求头到底能不能发出去"从来没被验证过 —— B26 的默认 UA 里
# 带了中文，而 `http.client` 按 latin-1 编码头字段，于是**每一次**请求都在
# 发出去之前抛 UnicodeEncodeError，采集器 100% 抓不到任何东西，71 项单测却全绿。
# 下面三条专门真发请求，把这个盲区钉死。


class _Handler(BaseHTTPRequestHandler):
    """记录收到的 User-Agent，并回一个最小 HTML 页面。"""

    def do_GET(self) -> None:  # noqa: N802 - stdlib 命令式命名
        self.server.seen_ua = self.headers.get("User-Agent")  # type: ignore[attr-defined]
        body = (b"<html><body><ul class='news-list'>"
                b"<li><a href='/1.html'>ok</a></li></ul></body></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass                                              # 别把访问日志打到 stderr


@contextmanager
def _local_site():
    """起一个只监听 127.0.0.1 随机端口的 HTTP 服务。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_default_user_agent_is_header_safe():
    """默认 UA 必须能塞进 HTTP 头：头字段按 latin-1 编码，非 ASCII 会直接抛异常。"""
    DEFAULT_USER_AGENT.encode("latin-1")


def test_real_http_request_carries_default_user_agent():
    """**真发一次请求**（不注入 opener），默认 UA 得能原样到达服务端。

    这是上一条的端到端版本：它能抓住"任何请求头非法"，而不只是 UA 这个常量。
    """
    with _local_site() as server:
        url = f"http://127.0.0.1:{server.server_port}/notice/"
        resp = HttpFetcher(timeout=5).get(url)            # 真的走 urllib
        seen = server.seen_ua

    assert resp.status == 200
    assert "news-list" in resp.text
    assert seen == DEFAULT_USER_AGENT


def test_robots_gate_really_fetches_robots_txt():
    """robots.txt 也走真实请求 —— UA 不合法会让它一律"保守拒绝"，把 check 变成假红。"""
    with _local_site() as server:
        origin = f"http://127.0.0.1:{server.server_port}"
        decision = RobotsGate(timeout=5).check(origin + "/notice/")
        seen = server.seen_ua

    assert decision.allowed is True       # 该路径返回的不是 robots 规则 → 无限制
    assert seen == DEFAULT_USER_AGENT


# ======================= review 复检：SSRF / 入库隔离 / 配置唯一性 ====
#
# 对应 review 第三轮的三条发现。SSRF 那条**必须端到端跑**：它关心的不是
# "函数返回值对不对"，而是"有没有真的把请求打到内网去"。

LIST_HTML_WITH_SSRF = """<html><body><ul class="news-list">
  <li><a href="/notice/1.html">正常通知</a></li>
  <li><a href="http://127.0.0.1:9/secret">内网探测</a></li>
  <li><a href="http://169.254.169.254/latest/meta-data/iam/">云主机元数据</a></li>
</ul></body></html>"""


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/secret",
    "http://127.0.0.1:8080/x",
    "http://10.1.2.3/x",
    "http://192.168.1.1/x",
    "http://172.16.0.9/x",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/x",
    "http://0.0.0.0/x",
    "http://localhost:9000/x",
    "http://db.internal/x",
])
def test_safety_blocks_internal_targets(url):
    allowed, reason = check_url(url)
    assert allowed is False and reason


@pytest.mark.parametrize("url", [
    "ftp://example.com/x",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "data:text/plain;base64,SGk=",
    "",
])
def test_safety_blocks_non_http_schemes(url):
    allowed, reason = check_url(url)
    assert allowed is False and reason


def test_safety_blocks_hostname_that_resolves_to_internal():
    """域名本身看着是公网的，但解析到内网 —— 只看字面量会漏掉这一类。"""
    allowed, reason = check_url("http://content.example.edu.cn/x",
                                resolver=lambda host: ["10.0.0.7"])
    assert allowed is False
    assert "10.0.0.7" in reason


def test_safety_allows_cross_origin_public_host():
    """跨域的**公网**主机仍允许：有些学校把正文放在 content.xxx.edu.cn。

    这是刻意的取舍 —— 强制"详情页必须与源站同源"会漏采合法正文，
    与 robots 的口径一致：只拦"明显不该去的地方"。
    """
    allowed, _ = check_url("http://content.example.edu.cn/x",
                           resolver=lambda host: ["93.184.216.34"])
    assert allowed is True


def test_safety_allows_unresolvable_host():
    """解析不出来就放行 —— 解析失败 ⇒ 连接也会失败，请求发不出去，不构成 SSRF。

    反过来（在这里拒绝）的代价是：DNS 故障会被误报成"安全问题"，把排查带偏。
    错误信息由 HTTP 层给（"抓取失败"）才准确。
    """
    allowed, reason = check_url("http://nope.invalid/x", resolver=lambda host: [])
    assert allowed is True and reason == ""


def test_ssrf_link_is_blocked_before_any_request_is_sent():
    """列表页里指向内网/云元数据的链接：**一条请求都不能发出去**，连 robots 都不问。

    这正是 review 实测的链路 —— 原先 `guard.before` 会先替内网主机取一次
    `robots.txt`（那本身就是一次打到内网的外发请求），而内网对 robots.txt 通常
    返回 404，按 RFC 9309 恰好等于"无限制" ⇒ 放行 ⇒ 正文被抓走入库。
    """
    clock = FakeClock()
    robots = FakeRobots({})          # 未登记 → 一律 404 = 无限制（review 走的正是这条）
    guard = FetchGuard(
        robots=RobotsGate(fetcher=robots, clock=clock),
        journal=CollectJournal(path=""),
        default_qps=0.0,
        limiter_kwargs={"clock": clock, "sleep": clock.sleep},
    )
    http = FakeHttp(_pages(LIST_HTML_WITH_SSRF, numbers=(1,)))
    store = FakeStore()

    result = collect_source(SOURCE, guard=guard, fetcher=HttpFetcher(opener=http), store=store)

    # 正常那条照常采到
    assert result.inserted == 1
    assert [r["title"] for r in store.rows] == ["第1条通知"]
    # 内网那两条：请求一次都没发出去（**包含** robots 探测）
    touched = http.calls + robots.calls
    assert not any(
        host in call for call in touched for host in ("127.0.0.1", "169.254.169.254")
    ), f"不该有任何请求指向内网，实际：{touched}"
    assert robots.calls == [ORIGIN + "/robots.txt"]    # 只问了源站自己的 robots
    # 被拦的两条要报出来（该报警照报，退出码会是 1）
    assert len(result.errors) == 2
    assert all("安全闸门" in e for e in result.errors)
    assert result.ok is False


class _FlakyStore(FakeStore):
    """第 `fail_at` 次 insert 抛异常，其余正常 —— 模拟「标题过长」这类 DB 错误。

    用**调用次数**计数，而不是 `len(self.rows)`：失败的插入不会让行数增加，
    用行数判断会把它后面的每一条都误伤（只剩第一条成功）。
    """

    def __init__(self, fail_at: int = 2, existing=()) -> None:
        super().__init__(existing)
        self.fail_at = fail_at
        self.attempts = 0

    def insert(self, *, title, content, source_name, category, publish_time=""):
        self.attempts += 1
        if self.attempts == self.fail_at:
            raise RuntimeError('(1406, "Data too long for column \'title\' at row 1")')
        return super().insert(title=title, content=content, source_name=source_name,
                              category=category, publish_time=publish_time)


def test_insert_failure_does_not_break_the_rest():
    """一条入库失败不该拖垮整条流水线：该报警照报，其余条目照常入库。"""
    result, store, _, _ = _run(store=_FlakyStore(fail_at=2))
    assert (result.fetched, result.inserted) == (3, 2)
    assert len(result.errors) == 1 and "入库失败" in result.errors[0]
    assert result.ok is False
    assert [r["title"] for r in store.rows] == ["第1条通知", "第3条通知"]


def test_insert_failure_does_not_stop_later_sources(tmp_path):
    """一个源入库失败，**后面的源必须照跑** —— review 实测原先整轮采集会报废。"""
    cfg = _write_config(tmp_path, [
        {**SOURCE, "key": "src-a", "name": "源A"},
        {**SOURCE, "key": "src-b", "name": "源B"},
    ])

    class BoomForA(FakeStore):
        def insert(self, *, title, content, source_name, category, publish_time=""):
            if source_name == "源A":
                raise RuntimeError("模拟 DB 故障")
            return super().insert(title=title, content=content, source_name=source_name,
                                  category=category, publish_time=publish_time)

    clock = FakeClock()
    http = FakeHttp(_pages())
    results = run_config(cfg, guard=_guard(clock), fetcher=HttpFetcher(opener=http),
                         store=BoomForA())

    assert [r.key for r in results] == ["src-a", "src-b"]
    assert results[0].ok is False and results[0].inserted == 0
    assert results[1].ok is True and results[1].inserted == 3    # 后面的源不受影响


def test_load_sources_rejects_duplicate_name(tmp_path):
    """`name` 撞车会让两个源互相判重、静默少采 —— 必须在加载时就报错。"""
    path = _write_config(tmp_path, [
        {"key": "a", "name": "重名", "url": ORIGIN + "/a/"},
        {"key": "b", "name": "重名", "url": ORIGIN + "/b/"},
    ])
    with pytest.raises(SystemExit) as exc:
        load_sources(path)
    assert "name 重复" in str(exc.value)


def test_load_sources_rejects_duplicate_key(tmp_path):
    path = _write_config(tmp_path, [
        {"key": "same", "name": "甲", "url": ORIGIN + "/a/"},
        {"key": "same", "name": "乙", "url": ORIGIN + "/b/"},
    ])
    with pytest.raises(SystemExit) as exc:
        load_sources(path)
    assert "key 重复" in str(exc.value)
