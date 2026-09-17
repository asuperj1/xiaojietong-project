# CI 与回归门禁（B34）

> **2026-09-17 修订（真因已由 CI 原始日志定位，勿再猜）**
>
> **真因**：`db/cpp_driver/CMakeLists.txt` 的静态库 `jt_db_core` **缺 PIC**。链接
> `jt_db.so` 时 ld 报：
> `relocation R_X86_64_TPOFF32 against '__tls_guard' can not be used when making a
> shared object; recompile with -fPIC`
> 来源是 `src/db_session.cpp:9` 的 `thread_local std::shared_ptr<MysqlConnection>
> DbSession::txn_;` —— 带动态初始化的 thread_local 在 gcc 下会生成 TLS 守卫
> `__tls_guard`，该重定位在共享库 / PIE 中非法。**MSVC 无 PIC 概念，所以本地
> （Windows）永远编得过** —— 这就是「本地绿、CI 红」的全部原因。
> 修法：`set_target_properties(jt_db_core PROPERTIES POSITION_INDEPENDENT_CODE ON)`，
> 且必须设在**静态库**上（`jt_db_test` 也链它，而 Ubuntu 的 gcc 默认生成 PIE）。
>
> **以下是顺带加固，均非病因**（原始日志已逐条证伪；保留是因为它们本身更正确）：
> 1. 系统依赖补 `python3-dev`、CMake 收窄为 `Development.Module` —— 原始日志显示连最宽的
>    `Development`（含 Embed）在 runner 上都被标记为 found，配置期本来就是成功的；
> 2. `set(PYBIND11_FINDPYTHON ON)` —— 对「只编模块」是更正确的写法，但同样不是病因；
> 3. 触发范围收敛（`on.push` 只留 dev/main）—— 省额度、降噪音，与失败无关；
> 4. **编译/链接失败可诊断** —— 这条是前三轮里**唯一真正推动问题的改动**。原步骤只给一个
>    `exit code 2`，22 次全红都没人看到 ld 那一行；拆成「体检 / configure / 编译」三步后，
>    第一次运行就把失败位置钉到了链接期。**失败信息本身就是给别人看的文档。**
>
> ⚠️ 教训（写给下一个改 CI 的人）：
> - **CI 类改动必须先在作者分支上跑绿、把运行链接贴进 PR 再请人合** —— 这次先合进 dev
>   才第一次运行，于是 22 次全红，没保护到任何人，还把「红灯」稀释成了噪音；
> - **更要紧的**：那 22 次里没有任何人去看失败日志，前三轮的「高概率根因」全是猜的，
>   直到有人把日志拉下来才定住。**持续红灯时先拉日志，别先猜。**

> 目标：**宁可红灯，不要假绿**。
> 本仓库的集成用例在环境缺失时会 `pytest.skip`（见 `backend/tests/conftest.py`），
> 这在本地开发很友好，但在流水线上会退化成"一个用例都没跑，却显示全绿"。
> 本目录的 CI 与 `tools/ci_gate.py` 就是用来堵这个洞的。

## 一、三道门禁

| 编号 | 命令 | 红灯条件 | 环境缺失时 |
|---|---|---|---|
| **G1** | `pytest tests`（经 `--junitxml` 精确计数） | `failed/error > 0`；用例数 = 0（恒真空） | 用例全 skip → **红灯** |
| **G2** | `tools/preflight_check.py` | 退出码 ≠ 0；或「阻塞项：N 个」中 N > 0 | 后端不可达 → `SKIP` |
| **G3** | `ai/eval/rag_bench.py --strict` | 退出码 ≠ 0；或 `hit@3 < 80%` | Ollama 不可达 → `SKIP` |

`SKIP` **不等于通过**，它只是"这台机器没这个环境"：
- 默认模式下只告警，并在汇总里单列（明确写着"未验证"）；
- `--strict`（CI 用）下 **SKIP 直接算红灯**，逼环境补齐。

### `rag_bench --strict` 额外汇总的三类红灯

除 `hit@3` 阈值外，以下情况一律返回退出码 1 —— 它们都会让指标"看着还行、其实没验证到东西"：

1. **题目数为 0**（恒真空，没有任何检索被验证）；
2. **任一题目检索抛异常**（单题失败不再被静默吞掉）；
3. **向量检索完全未生效**（全部走关键词降级，说明 `bge-m3` / Ollama 没起来，指标不具参考性）。

## 二、本地怎么跑

```powershell
# 前置：MySQL 已起、backend/.env 配好、Ollama 在跑（G3 需要）、jt_db 已编译
cd D:\xiaojietong\xiaojietong-project
& ".\backend\.venv\Scripts\python.exe" tools\ci_gate.py --strict
```

常用变体：

```powershell
# 只跑一道门禁
python tools\ci_gate.py --only pytest --strict
python tools\ci_gate.py --only preflight
python tools\ci_gate.py --only rag

# 换后端地址（例如已部署到内网）
python tools\ci_gate.py --only preflight --base http://192.168.1.10:8000/api/v1

# 落盘机器可读汇总
python tools\ci_gate.py --strict --json ci_gate_report.json
```

退出码：`0` = 全绿；`1` = 存在红灯，或（`--strict` 下）存在 SKIP。

## 三、CI（GitHub Actions）

`.github/workflows/ci.yml` 有两个 job：

| job | runner | 内容 | 触发 |
|---|---|---|---|
| `backend-tests` | `ubuntu-latest` | MySQL service + 现场编译 `jt_db` + **G1** | push（`dev`/`main`/`feat/**`/`fix/**`）与 PR |
| `full-gate` | `[self-hosted, xjt]` | **G1 + G2 + G3** 全量 | 仅手动 `workflow_dispatch` 且勾选 `full_gate` |

**为什么全量门禁要 self-hosted**：G2 需要一个**运行中的后端**，G3 需要 **Ollama + bge-m3**。
GitHub 托管 runner 上拉模型既慢又不稳，因此把它们留给自有机器；托管 runner 只跑
不依赖 Ollama 的 G1 —— 这一点已实测：**在无 Ollama 的环境下 `pytest` 仍 72 passed**。

### 手动跑全量门禁

`Actions → CI → Run workflow → 勾选 full_gate`。
self-hosted runner 需要预先打标签 `xjt`，并具备：MySQL、`backend/.env`、Ollama（`bge-m3` / `xjt-3b` / `qwen2.5:3b`）、已编译的 `jt_db`。

## 四、新增门禁怎么写

在 `tools/ci_gate.py` 里加一个 `gate_xxx(args) -> Gate` 函数，登记进 `GATES` 字典即可。
两条硬要求：

1. **拿证据**：不要只信退出码。pytest 用 `--junitxml`、rag_bench 用 `--out` 落盘报告，
   解析出精确的数字（用了多少题、跳过多少条）。
2. **别把"没跑"当"跑过"**：环境不具备时返回 `SKIP` 并写清原因，**不要**返回 `PASS`。

## 五、已知限制

- `preflight_check.py` 里 `E7` 的前端路径是**写死的绝对路径**（`d:\xiaojietongproject\xjt-frontend\...`），
  在非该目录的机器上该检查会跳过 —— 属于历史遗留，改动它需要前后端一起确认。
- `full-gate` 依赖 self-hosted runner，团队尚未配置的话该 job 不会运行 ——
  这时"全量门禁"只在本地跑，**CI 上未验证的部分不会伪装成通过**（job 直接不出现）。
