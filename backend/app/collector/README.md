# 采集器（`B26` 合规地基 + `B27` 配置驱动采集）

> **B26 职责**：在"发请求"之前把住三道关 —— **允许抓吗（robots）**、**还要等多久（限速）**、**留下了什么痕迹（日志）**。
> **B27 职责**：按 C21 的 `sources[]` 真正抓列表页与详情页，解析后**幂等**写入 `campus_notice`；新增一个源只需要写配置，不改代码。
> **关系**：B27 的每一次请求都从 B26 的 `FetchGuard` 过 —— 列表页与**每个详情页**各过一次。

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

### 3.3 身份要诚实，但**只能用 ASCII**

`DEFAULT_USER_AGENT` 带 `XJTCampusBot/1.0` 与联系方式，**不伪装浏览器**；
可通过 `FetchGuard(user_agent=...)` 覆盖。

⚠️ 自定义 UA 时**只能放 ASCII**：HTTP 头字段在 `http.client` 里按 latin-1 编码，
混进中文会让**每一次**请求在发出去之前就抛 `UnicodeEncodeError`
—— B26 曾因此 100% 抓不到任何页面（robots.txt 与业务页面一起失效，报错还被包成
"抓取失败"，看起来像对方站点的问题），而当时单测全程注入假 opener，一次真请求都没发过。
现在有三项测试专门钉住这一点：一项检查常量可 latin-1 编码，
一项**真起本地 HTTP 服务发一次请求**并核对服务端收到的 UA，一项让 `RobotsGate` 真去取 robots.txt。

### 3.4 出网安全闸门（SSRF）

`extract_list` 只对页面里的 `href` 做 `urljoin` 补全，**不校验主机** —— 列表页里一条指向
别的主机的链接，会让采集器真的去抓它；而且在抓之前还会先替这个主机取一次 `robots.txt`
（`FetchGuard.before` 干的），**那本身就已经是一次打到内网的外发请求**。
内网服务对 `robots.txt` 通常返回 404，按 RFC 9309 恰好等于"无限制"
—— 于是**连最保守的 `on_robots_error="block"` 都放行**；云主机上的
`http://169.254.169.254/latest/meta-data/...` 走的正是这条路（实例凭据会变成一条"校园通知"）。

所以每个详情页 URL 在 `FetchGuard.before` **之前**先过 `safety.check_url`：

| 拒绝 | 例子 |
|---|---|
| 非 http/https | `ftp://`、`file://`、`javascript:`、`data:` |
| 私有 / 回环 / 链路本地 / 保留地址 | `10.x`、`192.168.x`、`127.0.0.1`、`169.254.169.254`、`::1`、`0.0.0.0` |
| 一眼就是内网的主机名 | `localhost`、`*.internal`、`*.local`… |
| **DNS 解析结果**落在内网 | `content.example.edu.cn` → `10.0.0.7`（只查字面量会漏掉这类） |

**允许**跨域的公网主机（有些学校把正文放在 `content.xxx.edu.cn`）—— 强制"详情页必须同源"
会漏采合法正文；这与 robots 的口径一致：只拦"明显不该去的地方"，不替运维做同源的决定。

**解析失败是放行的**：解析不出来 ⇒ 连接也必然失败、请求发不出去、不构成 SSRF；
在这里拒绝只会把普通的 DNS 故障误报成"安全问题"，把排查方向带偏。
错误交给 HTTP 层报"抓取失败"才准确。

**已知边界**：挡不住 DNS rebinding（解析时返回公网 IP、连接时返回内网 IP）——
那要在**连接层**绑定 IP 校验才能根治，属比出网闸门更大的改造（应落在 `fetcher` 层），
不能靠这里多写两行假装解决了。

---

## 4. CLI

### 4.1 `check` —— 采集前自检（不写任何数据）

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

### 4.2 `run` —— 真正抓取并入库（B27）

```bash
python -m app.collector run <config.yml> --dry-run            # 试跑：抓取+解析，不写库
python -m app.collector run <config.yml> --source jwc-notice --limit 10
python -m app.collector run <config.yml> --json result.json   # 结果落盘
```

| 参数 | 说明 |
|---|---|
| `--source KEY` | 只跑指定源（可重复）；默认跑全部 `enabled: true` 的源 |
| `--limit N` | 每个源最多抓几条详情（默认 20；列表不足时不补齐） |
| `--dry-run` | 试跑：真实抓取与解析，但**不构造 `NoticeStore`** —— 连数据库模块都不导入，没配 DB 的机器也能预览 |
| `--timeout` | 单请求超时秒数（默认 10） |
| `--qps` | 源里没写 `rate_limit_qps` 时的默认值（默认 0.5） |
| `--fail-on-empty` | 列表页一条都没匹配到时**以退出码 1 结束**（默认只在 stderr 告警） |

退出码：`0` = 每个源都没错误；`1` = 至少一个源报错或被 robots 拒绝
（便于接定时任务 / CI）。被拒绝的源会打印**具体原因**，且**一条业务请求都不会发出去**。

**"空列表"怎么处理**：选择器语法写错会抛 `ValueError`（见 §4.3），但
**语法正确却匹配不到元素**（站点改版、类名换了）不会有任何异常 —— 于是
`列表 0 条` 只会混在统计行里被忽略。所以：

- **默认**：在 **stderr** 打一条显式 `⚠️ 列表选择器匹配到 0 条`（附上实际使用的
  `selectors.list`），但**不判失败** —— 页面本来就可能暂时没有新内容；
- **接定时任务时加 `--fail-on-empty`**，把"没采到"变成"任务失败"，避免站点改版后
  采集**安静地停止工作**，直到有人发现库里不再有新通知。

被 robots 拒绝或抓取失败**不算**空列表（那些已经有明确的错误原因）。
`--json` 报告里也有 `empty_list` 与 `list_selector` 两个字段，便于事后排查。

### 4.3 新增一个源要改什么

**只改配置，不改代码。** 在 `sources[]` 里加一项即可：

```yaml
- key: jwc-notice
  name: 教务处通知
  url: https://jwc.example.edu.cn/tzgg/
  enabled: true
  type: html_list
  category: 教务
  rate_limit_qps: 0.5
  respect_robots: true
  selectors:
    list: 'div.list-item a'        # 列表页：每条通知的链接
    title: 'h2'                    # 详情页：标题
    content: 'div.content'         # 详情页：正文（缺省则取整页 body 文本）
    date: 'span.pub-date'          # 详情页：发布时间（可选）
```

`selectors` 支持 `tag` / `.class` / `#id` / `tag.class` 与后代组合（如 `ul.news-list li a`）；
**不支持的写法（`>`、`[attr]`、`:pseudo`）会直接报错**，而不是静默返回空
—— "配置写错了、采集却悄悄没数据"比报错难查得多。

**幂等**：以 `(source, title)` 判重，整条流水线重复跑不会写入重复通知。

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
6. **逐条过闸门**（B27）：不只对列表页查 robots，**每一条详情页 URL 都要再查一次**
   —— 详情页路径同样可能被禁止；每条请求也都要过限速。
7. **UA 一致**（B27）：抓取用的 `User-Agent` 必须与 robots 判定时**完全相同**
   —— 用 A 去问"我能抓吗"、再用 B 去抓，等于绕过了刚拿到的许可。
8. **选择器不认识就报错**（B27）：`parse_selector` 抛 `ValueError`，绝不静默返回空。
9. **试跑不碰数据库**（B27）：`--dry-run` 连 `NoticeStore` 都不构造，保证它能在任何机器上跑。
10. **安全闸门必须排在 robots 之前**（B27 review P1）：`safety.check_url` 要跑在
    `FetchGuard.before` 前 —— 先问 robots 等于先把请求打到内网去，那时候再拦已经晚了。
11. **抓取路径与入库路径对称隔离**（B27 review P2）：详情页失败 `continue`，
    入库失败同样 `continue` 并记进 `errors` —— 一条数据有问题不该让整轮采集报废。
12. **配置里的 `key` 与 `name` 都强制唯一**（B27 review P3）：判重键落在入库的
    `source` 列（= name）上，name 撞车会让两个源互相判重、**静默少采**；
    宁可加载时直接报错。
13. **两种 `ValueError` 不能混**（B27 review P2）：`extract_list` 在内部消化畸形 href
    （单条跳过并如实上报），让 `collect_source` 的 `except ValueError` **只接**
    "选择器语法错"。混在一起会把 URL 畸形报成「`selectors.list` 无效」，
    把运维引向一个本来没问题的配置 —— 而且会让整源零采集。

---

## 7. 单测

```bash
cd backend
python -m pytest tests/test_collector.py tests/test_collector_b27.py -q    # 97 项（其中 3 项真发本地 HTTP 请求）
```

`test_collector.py`（B26，30 项）覆盖：robots 允许/禁止/404 放行/5xx 保守拒绝/网络故障、
缓存与过期、`Crawl-delay` 解析、限速取更严值、注册表复用、JSONL 落盘与坏行容错、
`FetchGuard` 的拒绝路径 / 等待路径 / `respect_robots=false` 绕过路径 / 结果记录。

`test_collector_b27.py`（B27，70 项）覆盖：选择器（含**真实 C21 配置里的全部写法**）、
列表页去重与相对链接补全、缺 `content` 选择器时退化为 body、
**详情页逐条过 robots**（被禁的那条一条请求都不发、其余照常入库）、
限速作用于每一次请求、`Crawl-delay` 压过 qps、单条失败不拖累其余、
幂等重跑、`--dry-run` 不碰数据库、`--limit`、
**空列表告警与 `--fail-on-empty`**、CLI 退出码与 `check` 回归；
另有 review 复检补的四组：**出网安全闸门**（含"内网链接连 robots 都不去问"的端到端断言）、
**单条入库失败不拖垮整轮**（含"后面的源照跑"）、**配置 key/name 唯一性**、
**单条畸形链接 vs 选择器错误**（两种 `ValueError` 分开报，别把 URL 问题说成配置问题）。

**其中最后 3 项是"真发请求"的**（起一个只监听 `127.0.0.1` 随机端口的
`http.server`，不注入 opener）：校验默认 UA 可 latin-1 编码、HTTP 请求能真的把 UA
送达服务端、`RobotsGate` 能真的取到 robots.txt。它们防的是同一类问题 ——
**假 opener 让"请求根本发不出去"永远不会被测到**。

作者：成员2（后端+AI）· B26 / B27
