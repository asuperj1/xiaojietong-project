# `B26` 采集合规与限速

> **职责**：在"发请求"之前把住三道关 —— **允许抓吗（robots）**、**还要等多久（限速）**、**留下了什么痕迹（日志）**。
> **定位**：`B27` 配置驱动采集器的地基。B26 不抓业务页面，只提供闸门。

```
    ┌────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────┐
    │ 源配置(C21) │ → │ RobotsGate   │ → │ RateLimiter  │ → │ 真正的请求 │
    │ sources[]  │   │ 允许抓吗？    │   │ 还要等多久？  │   │ (B27)    │
    └────────────┘   └──────────────┘   └──────────────┘   └──────────┘
                             └──────── CollectJournal 全程留痕 ───────┘
```

---

## 1. 30 秒上手

```python
from app.collector import FetchGuard

guard = FetchGuard()                       # 默认落盘 backend/data/collect/collect.jsonl

for source in cfg.enabled_sources():       # C21 的 SourceConfig
    url = source.url
    decision = guard.before(source, url)   # ① robots ② 限速 ③ 记日志
    if not decision.allowed:
        continue                           # 被 robots 拒绝，原因已进日志

    t0 = time.perf_counter()
    try:
        resp = requests.get(url, headers={"User-Agent": guard.user_agent}, timeout=10)
        guard.after(source, url, status=resp.status_code, size=len(resp.content),
                    elapsed_ms=int((time.perf_counter() - t0) * 1000))
    except Exception as exc:
        guard.after(source, url, error=f"{type(exc).__name__}: {exc}")
```

`before()` **不发任何业务请求**，只做 robots 判定与限速等待；`after()` 记录结果。
两者之间放什么由 B27 决定（requests / httpx / 浏览器渲染都行）。

---

## 2. 三个组件

| 组件 | 文件 | 解决什么 |
|---|---|---|
| `RobotsGate` | `robots.py` | 按 origin 缓存 robots.txt；回答"这个 URL 允许抓吗"；顺带取出 `Crawl-delay` |
| `RateLimiter` | `limiter.py` | 按源的最小间隔限速（同步 + 异步），取 `max(1/qps, Crawl-delay)` |
| `CollectJournal` | `journal.py` | 追加写 JSONL 的采集日志，`recent()` / `iterate()` / `stats()` 可查 |

---

## 3. 合规语义（重点）

### 3.1 robots.txt 取不到时怎么办

| 情况 | 处理 | 依据 |
|---|---|---|
| HTTP 404 / 410 | **允许**（该站没有 robots.txt = 无限制） | RFC 9309 §2.3.1.3 |
| 网络错误 / 超时 | **拒绝**（默认） | 规则未知时不得默认"可以抓" |
| HTTP 5xx | **拒绝**（默认） | 同上 |

需要放宽时显式传 `on_error="allow"`，**绝不做隐式放行**。

### 3.2 限速取"更严的那个"

```python
RateLimiter("lib", 0.5).interval(crawl_delay=7)   # → 7.0s，而不是 2.0s
RateLimiter("lib", 0.1).interval(crawl_delay=3)   # → 10.0s，配置更严就用配置
```

### 3.3 身份要诚实

`DEFAULT_USER_AGENT` 带 `XJTCampusBot/1.0` 与联系方式，**不伪装浏览器**；
可通过 `FetchGuard(user_agent=...)` 覆盖。

---

## 4. 采集前自检（CLI）

```bash
cd backend
python -m app.collector check app/adapters/configs/xiaojietong.yml
```

输出每个启用源的 robots 判定与**实际生效间隔**，退出码 `1` 表示存在不可抓取的源：

```
  ✅ library-notice       qps=0.5    有效间隔≈2.00s　robots.txt 允许
  ✅ jwc-notice           qps=0.5    有效间隔≈2.00s　robots.txt 允许，Crawl-delay=7.0s
  ⏭ xsc-notice            已停用，跳过
```

`--offline` 只校验参数（不联网）；`--json report.json` 落盘报告。
**注意**：联网模式会真的去读目标站点的 robots.txt —— 本地/内网跑，别放进没有外网的 CI。

---

## 5. 日志可追溯

每条事件一行 JSON（`backend/data/collect/collect.jsonl`，路径可用 `XJT_COLLECT_LOG` 覆盖）：

```json
{"ts":"2026-09-16T15:30:01","source":"library-notice","url":"https://lib.../notice/","phase":"robots","outcome":"blocked","detail":"robots.txt 禁止该路径（https://lib.../robots.txt）","status":null,"bytes":null,"waited_ms":0,"elapsed_ms":3,"extra":{}}
{"ts":"2026-09-16T15:30:09","source":"library-notice","url":"https://lib.../notice/1.html","phase":"rate_limit","outcome":"ok","detail":"等待 7.000s（qps=0.5，crawl_delay=7.0）","waited_ms":7000,...}
{"ts":"2026-09-16T15:30:09","source":"library-notice","url":"https://lib.../notice/1.html","phase":"fetch","outcome":"ok","status":200,"bytes":18234,"elapsed_ms":212,...}
```

`phase` 取值：`robots`（判定）、`rate_limit`（等待）、`fetch`（结果）；
`outcome` 取值：`ok` / `blocked` / `skipped` / `http_error` / `network_error`。
出问题时 `grep '"outcome":"blocked"' collect.jsonl` 就能定位是哪个源、哪条规则拦的。

---

## 6. 设计约定（后续维护者请遵守）

1. **零业务依赖**：本包不 import `app.db` / `app.core.config` / `app.services`，
   也不 import `app.adapters` —— 源配置通过 duck typing 读取字段
   （`key` / `rate_limit_qps` / `respect_robots`），pydantic 模型与 dict 都能直接传。
2. **不隐式放行**：规则拿不到就拒绝；要放宽必须写出来（`on_error="allow"`）。
3. **时钟与睡眠可注入**：`clock` / `sleep` / `fetcher` 都能替换，单测全程离线、零 sleep。
4. **日志失败不拖垮采集**：写盘异常只 warning。
5. **限速器按 key 复用**：走 `limiter_for()`，不要自己 `RateLimiter(...)`
   —— 否则同一源存在多个窗口，限速形同虚设。

---

## 7. 单测

```bash
cd backend
python -m pytest tests/test_collector.py -q      # 30 项，纯离线（不联网/不连库/不需 Ollama）
```

覆盖：robots 允许/禁止/404 放行/5xx 保守拒绝/网络故障、缓存与过期、
`Crawl-delay` 解析、限速取更严值、注册表复用、JSONL 落盘与坏行容错、
`FetchGuard` 的拒绝路径 / 等待路径 / `respect_robots=false` 绕过路径 / 结果记录。

作者：成员2（后端+AI）· B26
