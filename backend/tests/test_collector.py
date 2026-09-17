# -*- coding: utf-8 -*-
"""B26 采集合规与限速 —— 纯离线单测。

不碰网络（robots.txt 由假 fetcher 提供）、不碰数据库、不碰 Ollama，
因此在任何机器上都能跑出确定结论。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.collector import (
    CollectJournal,
    FetchGuard,
    RateLimiter,
    RobotsGate,
    RobotsUnavailable,
    limiter_for,
    reset_limiters,
)

# ------------------------------------------------------------------ 夹具 ----


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


class FakeFetcher:
    """按 URL 返回预设的 robots.txt；值若是异常对象则抛出（模拟网络故障）。"""

    def __init__(self, routes: dict) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, url: str, timeout: float, user_agent: str):
        self.calls.append(url)
        value = self.routes.get(url, (404, ""))
        if isinstance(value, Exception):
            raise value
        return value


ROBOTS_ALLOW = "User-agent: *\nDisallow:\n"
ROBOTS_DENY = "User-agent: *\nDisallow: /notice/private/\n"
ROBOTS_DELAY = "User-agent: *\nDisallow:\nCrawl-delay: 7\n"

ORIGIN = "https://lib.example.edu.cn"
ROBOTS_URL = ORIGIN + "/robots.txt"


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个用例前后清空全局限速器注册表，避免互相污染。"""
    reset_limiters()
    yield
    reset_limiters()


def _gate(routes: dict, **kw) -> tuple[RobotsGate, FakeFetcher]:
    fetcher = FakeFetcher(routes)
    return RobotsGate(fetcher=fetcher, **kw), fetcher


# ============================================================ RobotsGate ====


def test_robots_allows_path():
    gate, _ = _gate({ROBOTS_URL: (200, ROBOTS_ALLOW)})
    decision = gate.check(ORIGIN + "/notice/1.html")
    assert decision.allowed is True
    assert decision.crawl_delay is None
    assert "robots.txt" in decision.robots_url


def test_robots_blocks_disallowed_path():
    gate, _ = _gate({ROBOTS_URL: (200, ROBOTS_DENY)})
    assert gate.check(ORIGIN + "/notice/private/x.html").allowed is False
    assert gate.check(ORIGIN + "/notice/public.html").allowed is True


def test_robots_404_means_unrestricted():
    """RFC 9309：robots.txt 不存在 = 无限制。"""
    gate, _ = _gate({})  # 默认 404
    decision = gate.check(ORIGIN + "/anything")
    assert decision.allowed is True
    assert "404" in decision.reason


def test_robots_5xx_blocks_conservatively():
    """取不到规则时默认保守拒绝 —— 绝不因为'读不到'就默认可抓。"""
    gate, _ = _gate({ROBOTS_URL: (503, "")})
    decision = gate.check(ORIGIN + "/notice/1.html")
    assert decision.allowed is False
    assert "503" in decision.reason


def test_robots_5xx_allowed_when_on_error_allow():
    gate, _ = _gate({ROBOTS_URL: (503, "")}, on_error="allow")
    assert gate.check(ORIGIN + "/notice/1.html").allowed is True


def test_robots_network_failure_blocks_by_default():
    gate, _ = _gate({ROBOTS_URL: RobotsUnavailable("timeout")})
    decision = gate.check(ORIGIN + "/notice/1.html")
    assert decision.allowed is False
    assert "保守拒绝" in decision.reason


def test_robots_network_failure_can_be_relaxed():
    gate, _ = _gate({ROBOTS_URL: RobotsUnavailable("timeout")}, on_error="allow")
    assert gate.check(ORIGIN + "/notice/1.html").allowed is True


def test_robots_result_is_cached_per_origin():
    """同站点多个 URL 只拉一次 robots.txt。"""
    gate, fetcher = _gate({ROBOTS_URL: (200, ROBOTS_ALLOW)})
    for path in ("/a", "/b", "/c"):
        assert gate.check(ORIGIN + path).allowed is True
    assert fetcher.calls == [ROBOTS_URL]
    assert gate.stats["cache_hit"] == 2


def test_robots_cache_expires_and_refetches():
    clock = FakeClock()
    gate, fetcher = _gate({ROBOTS_URL: (200, ROBOTS_ALLOW)}, ttl=100.0, clock=clock)
    assert gate.check(ORIGIN + "/a").allowed is True
    clock.t = 200.0                     # 越过 TTL
    assert gate.check(ORIGIN + "/b").allowed is True
    assert len(fetcher.calls) == 2
    assert gate.stats["expired"] == 1


def test_robots_invalidate_clears_cache():
    gate, fetcher = _gate({ROBOTS_URL: (200, ROBOTS_ALLOW)})
    gate.check(ORIGIN + "/a")
    gate.invalidate(ORIGIN + "/a")
    gate.check(ORIGIN + "/a")
    assert len(fetcher.calls) == 2


def test_robots_crawl_delay_is_read():
    gate, _ = _gate({ROBOTS_URL: (200, ROBOTS_DELAY)})
    decision = gate.check(ORIGIN + "/a")
    assert decision.allowed is True
    assert decision.crawl_delay == 7.0


def test_robots_rejects_relative_url():
    gate, _ = _gate({})
    with pytest.raises(ValueError):
        gate.check("/notice/1.html")


def test_robots_rejects_bad_on_error():
    with pytest.raises(ValueError):
        RobotsGate(on_error="whatever")


# ============================================================= RateLimiter ====


def test_limiter_interval_from_qps():
    assert RateLimiter("k", 0.5).interval() == pytest.approx(2.0)
    assert RateLimiter("k", 2.0).interval() == pytest.approx(0.5)
    assert RateLimiter("k", 0.0).interval() == pytest.approx(0.0)


def test_limiter_waits_between_requests():
    clock = FakeClock()
    limiter = RateLimiter("k", 0.5, clock=clock, sleep=clock.sleep)
    assert limiter.acquire() == 0.0            # 首次不等待
    assert limiter.acquire() == pytest.approx(2.0)   # 第二次等满一个间隔
    assert clock.slept == pytest.approx([2.0])


def test_limiter_crawl_delay_wins_over_qps():
    """robots 的 Crawl-delay 比配置的 qps 更保守时，取 Crawl-delay。"""
    limiter = RateLimiter("k", 10.0)           # 只要 0.1s 间隔
    assert limiter.interval(crawl_delay=7.0) == pytest.approx(7.0)


def test_limiter_qps_wins_when_stricter():
    limiter = RateLimiter("k", 0.1)            # 要 10s 间隔
    assert limiter.interval(crawl_delay=3.0) == pytest.approx(10.0)


def test_limiter_never_waits_negative_and_tracks_stats():
    clock = FakeClock()
    limiter = RateLimiter("k", 1.0, clock=clock, sleep=clock.sleep)
    limiter.acquire()
    clock.t = 100.0                            # 空闲很久后再来
    assert limiter.acquire() == 0.0
    assert limiter.stats["acquired"] == 2
    assert limiter.stats["waited"] == 0


def test_limiter_registry_reuses_instance():
    a = limiter_for("library-notice", 0.5)
    b = limiter_for("library-notice", 2.0)
    assert a is b and b.qps == 2.0             # 复用实例但更新 qps


def test_limiter_reset_clears_window():
    clock = FakeClock()
    limiter = RateLimiter("k", 1.0, clock=clock, sleep=clock.sleep)
    limiter.acquire()
    limiter.reset()
    assert limiter.acquire() == 0.0


# =========================================================== CollectJournal ====


def test_journal_writes_jsonl_and_recent(tmp_path: Path):
    path = tmp_path / "collect.jsonl"
    journal = CollectJournal(path)
    journal.record(source="s1", url="https://a/x", phase="fetch", outcome="ok", status=200)
    journal.record(source="s1", url="https://a/y", phase="fetch", outcome="http_error",
                   status=500, detail="HTTP 500")

    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 2
    assert lines[0]["source"] == "s1" and lines[0]["status"] == 200
    assert lines[1]["outcome"] == "http_error"
    assert journal.recent(5)[-1].url == "https://a/y"
    assert journal.stats()["recent"] == {"ok": 1, "http_error": 1}


def test_journal_iterate_reads_history(tmp_path: Path):
    path = tmp_path / "collect.jsonl"
    journal = CollectJournal(path)
    for i in range(3):
        journal.record(source="s", url=f"https://a/{i}", phase="fetch", outcome="ok")
    events = list(journal.iterate(limit=2))
    assert len(events) == 2 and events[0].url == "https://a/0"


def test_journal_survives_broken_lines(tmp_path: Path):
    path = tmp_path / "collect.jsonl"
    journal = CollectJournal(path)
    journal.record(source="s", url="https://a/ok", phase="fetch", outcome="ok")
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{ 这不是 JSON\n")
    assert len(list(journal.iterate())) == 1


def test_journal_disabled_without_path():
    journal = CollectJournal(path="")
    journal.record(source="s", url="https://a/x", phase="fetch", outcome="ok")
    assert journal.path is None
    assert journal.stats()["path"] is None


# ============================================================== FetchGuard ====


def _source(**kw) -> dict:
    base = {"key": "library-notice", "rate_limit_qps": 0.5, "respect_robots": True}
    base.update(kw)
    return base


def test_guard_blocks_disallowed_url_and_logs(tmp_path: Path):
    path = tmp_path / "c.jsonl"
    guard = FetchGuard(journal=CollectJournal(path),
                       robots=RobotsGate(fetcher=FakeFetcher({ROBOTS_URL: (200, ROBOTS_DENY)})))
    decision = guard.before(_source(), ORIGIN + "/notice/private/a.html")
    assert decision.allowed is False
    assert bool(decision) is False
    events = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert events[0]["phase"] == "robots" and events[0]["outcome"] == "blocked"


def test_guard_waits_then_logs_rate_limit(tmp_path: Path):
    path = tmp_path / "c.jsonl"
    clock = FakeClock()
    guard = FetchGuard(
        journal=CollectJournal(path),
        robots=RobotsGate(fetcher=FakeFetcher({ROBOTS_URL: (200, ROBOTS_ALLOW)})),
        limiter_kwargs={"clock": clock, "sleep": clock.sleep},
    )
    first = guard.before(_source(), ORIGIN + "/a")
    second = guard.before(_source(), ORIGIN + "/b")
    assert first.allowed and second.allowed
    assert first.waited_ms == 0
    assert second.waited_ms == 2000                # qps=0.5 → 2 秒
    assert clock.slept == pytest.approx([2.0])


def test_guard_skips_robots_when_source_opts_out(tmp_path: Path):
    path = tmp_path / "c.jsonl"
    fetcher = FakeFetcher({ROBOTS_URL: (200, ROBOTS_DENY)})
    guard = FetchGuard(journal=CollectJournal(path), robots=RobotsGate(fetcher=fetcher))
    decision = guard.before(_source(respect_robots=False), ORIGIN + "/notice/private/a.html")
    assert decision.allowed is True
    assert fetcher.calls == []                     # 根本没去读 robots.txt
    assert "respect_robots=false" in decision.reason


def test_guard_after_records_success_and_errors(tmp_path: Path):
    path = tmp_path / "c.jsonl"
    guard = FetchGuard(journal=CollectJournal(path))
    guard.after(_source(), "https://a/1", status=200, size=1234, elapsed_ms=42)
    guard.after(_source(), "https://a/2", status=503, elapsed_ms=10)
    guard.after(_source(), "https://a/3", error="ConnectTimeout: 5s")
    events = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert [e["outcome"] for e in events] == ["ok", "http_error", "network_error"]
    assert events[0]["bytes"] == 1234 and events[1]["status"] == 503


def test_guard_reads_fields_from_object_sources(tmp_path: Path):
    """C21 的 SourceConfig 是 pydantic 模型（属性访问），不是 dict。"""

    class SourceConfig:
        key = "obj-source"
        rate_limit_qps = 0.0
        respect_robots = False

    guard = FetchGuard(journal=CollectJournal(tmp_path / "c.jsonl"))
    decision = guard.before(SourceConfig(), "https://a/x")
    assert decision.allowed is True and decision.source == "obj-source"


def test_guard_carries_crawl_delay_into_decision(tmp_path: Path):
    guard = FetchGuard(journal=CollectJournal(tmp_path / "c.jsonl"),
                       robots=RobotsGate(fetcher=FakeFetcher({ROBOTS_URL: (200, ROBOTS_DELAY)})))
    decision = guard.before(_source(rate_limit_qps=0.0), ORIGIN + "/a")
    assert decision.crawl_delay == 7.0
