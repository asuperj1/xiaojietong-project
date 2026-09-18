# tools/ —— 校验与运维脚本

本目录放**不依赖微信开发者工具 / MySQL / 后端 / 网络**也能跑的校验脚本。
约定：`[OK]` / `[NG]` 逐项断言 + 末尾 `[PASS]` / `[FAIL]` 汇总；
退出码 `0` = 通过、`1` = 有失败。涉及断言的脚本都要有**反向对照**
（缺陷样本 / 突变注入），否则"现在通过"证明不了任何事。

## F22 · 小程序端静态检查

```bash
node tools/verify_miniprogram_static_rules.js                   # 门禁模式（读 baseline）
node tools/verify_miniprogram_static_rules.js --strict          # 严格模式：忽略 baseline
node tools/verify_miniprogram_static_rules.js --baseline <file> # 指定 baseline 文件（相对路径按 --src 解析）
node tools/verify_miniprogram_static_rules.js --list-candidates # R2 候选清单（信息模式，恒 0；不能与 --strict/--baseline 同用）
node tools/verify_miniprogram_static_rules.js --print-baseline  # 打印 baseline 片段（只打印，不写文件）
node tools/verify_miniprogram_static_rules.js --self-test       # 六条规则 + baseline 的阴性对照
node tools/verify_miniprogram_static_rules.js --src <repo-root>
```

> ⚠️ `--list-candidates` 是**信息模式**（恒退出 0，不参与门禁）；与 `--strict` / `--baseline` /
> `--print-baseline` 同时使用会**直接报错退出 1**（否则会出现"有 3 处违规却 exit 0"的假绿）。

> 📌 **文件名说明**：任务单最初写的是 `tools/verify_miniprogram_rules.js`；实际落地文件是
> **`tools/verify_miniprogram_static_rules.js`**（任务单分支已同步该文件名）。接 CI（`B34`）时用实际路径。

覆盖的六条规则（来源见脚本头部注释，逐条给出仓内证据）：

| RULE ID | 规则 |
|---|---|
| `R1_TAP_DETAIL_VALUE` | tap/长按处理器不得读 `e.detail.value`（索引取 `e.currentTarget.dataset.*`） |
| `R2_MISSING_LOWER_TRIGGER` | 分页列表页必须有 `onReachBottom` 或 `scroll-view` 的 `bindscrolltolower` |
| `R3_DATETIME_STRING_PARSE` | 不得用 `new Date('Y-m-d H:i:s')` / 后端时间字段直解（iOS `Invalid Date`） |
| `R4_LIST_FIELD_NO_FALLBACK` | 后端列表字段必须先兜底（`(res && res.items) \|\| []`）再当数组用 |
| `R5_HARDCODED_BASE_URL` | 除 `config/env.js` 外不得出现写死的接口地址 / 静态 `BASE_URL` |
| `R6_PAGE_REGISTRATION` | 页面须注册进 `app.json#pages`；TabBar 项与跳转目标须与 `pages` 一致 |

### 门禁语义：KNOWN / NEW / STALE（决定退出码）

`dev` 上本来就有历史违规（`FRONT-08` 的 3 处 R2）。为了不让门禁"红着出生"、随后被当噪声忽略，
脚本把结果分成三类，**只有后两类决定退出码**：

| 分类 | 含义 | 退出码 |
|---|---|---|
| **KNOWN** | 已登记在 `tools/miniprogram_static_rules_baseline.json` 的历史违规 | 打印，**不阻塞** |
| **NEW** | 本次扫描新出现的违规 | **阻塞**（exit 1） |
| **STALE** | baseline 里登记、但现在已经不存在的条目 | **阻塞**（exit 1，强制清理） |

**为什么 STALE 也阻塞**（而不是只 warning）：baseline 一旦"只进不出"就会退化成永久白名单 ——
页面修好了没人删条目，下一个人还会以为这些页面仍然有问题，技术债静默残留。
让 STALE 失败 = 强制"修页面"与"删条目"发生在**同一个 PR** 里；代价只是删 1 个 JSON 块。

**匹配身份**：`rule + file + identity`，**不含行号**（行号会随无关改动漂移）。
`identity` 是每条 finding 的稳定身份（`R1` → `handler:onCatTap`、`R4` → `list-array-use:res.items`、
`R5` → `url-literal:http://…#<hash>`、`R6` → `nav-target-unregistered:/pages/x/y`；`R2` 一页最多一条，故为空串）。
被 `clip` 截断的长字面量一律追加 8 位 FNV-1a 短哈希 —— 否则两条 >60 字符、前缀相同的地址字面量会塌成同一个身份，
一条 baseline 条目就能把两条都豁免掉（评审实测）。

两条细节：

- **同一文件里同名身份自动消歧**：两行写了同一个地址字面量时，第 2 条起是 `…#2`/`#3`，
  否则一条 baseline 只能豁免其中一条，另一条永远 NEW（`--print-baseline` 也会打出重复条目）。
  编号按 `rule/file/行号` 排序后的出现次序；
- **同一行多条证据被合并成一条 finding**（见下），此时要求它的**全部身份**都登记才算 KNOWN ——
  否则"已登记的那条"会把同一行新增的另一条缺陷顺带掩盖。
- 未登记的条目不会被"猜"着匹配；`R2` 命中某文件的**另一处**分页请求不会被已登记条目覆盖（同文件同规则第二条 finding 仍需登记）。

同文件同规则的**另一条** finding 不会被一条 baseline 条目掩盖（self-test B4/B5/D2/D3 对照）。

**证据漂移提醒**：条目可写可选的 `evidence`（登记时的证据文本）；若某次扫描该处证据变了，
门禁会打 `⚠️ 证据已变化…` 提示（**不阻塞**），提醒复核这条登记是否还准确（self-test B9）。

**baseline 文件**：`tools/miniprogram_static_rules_baseline.json`，格式极简、逐条可审计：

```json
{
  "schema": 1,
  "entries": [
    {
      "rule": "R2_MISSING_LOWER_TRIGGER",
      "file": "pages/life/index.js",
      "identity": "",
      "reason": "FRONT-08 遗留：/life/merchants 是服务端分页但页面无触底钩子",
      "evidence": "分页拉取但无触底钩子：request('/life/merchants', { data: { category: cat.value, page: 1, size: 20 }, })",
      "registered": "2026-09-18",
      "ref": "docs/项目审计报告20260912序1.md:826",
      "owner": "成员1（页面修复任务，非 F22 tooling）"
    }
  ]
}
```

规则：`reason` 必填（少于 4 字或缺失 = 格式错误，脚本直接失败）；未知 `rule`、重复条目、非法 JSON 同样直接失败。
`identity` 必须是字符串（无稳定身份时写 `""`）；`evidence`/`ref`/`owner` 可选。
**没有"自动灌入 baseline"的开关** —— 新增条目必须手工编辑并在 PR 里说明理由，
`--print-baseline` 只打印片段（**逐身份**、不写文件），避免把新违规一键洗成历史债务。
`--baseline` 的相对路径按**被扫描的仓库根目录**（`--src`）解析（baseline 属于它描述的那棵树）。

**文件缺失 vs 空 baseline（收口语义，评审 re-check 后定稿）**

| 情形 | 门禁模式（默认） | `--strict` | `--print-baseline`（信息模式） |
|---|---|---|---|
| baseline 文件**缺失** | **HARD FAIL（exit 1）** —— 它是门禁的版本化配置，缺失属配置完整性错误；报错会给出"从 git 恢复 / 用 `--strict` / 用 `--baseline <file>`"三条出路 | 不受影响（**完全不读 baseline**） | 不受影响（只打印片段，exit 0） |
| baseline 存在且 `entries: []` | **合法稳态**（历史债务清零后就是这样）：不因"为空"失败，此时任何违规都按 NEW 处理（空 baseline ≠ 免检） | 同上 | 同上 |
| baseline 存在且有条目 | 正常 KNOWN / NEW / STALE 判定 | 同上 | 同上 |
| `--baseline` 给了路径但文件不存在 / 缺参数值 | **exit 1**（明确报错：显式路径错误 / 参数缺值） | — | — |

⇒ 扫别的工作树/分支树请用 `--strict`（或 `--baseline <file>` 显式指定），不要靠"删掉 baseline"绕过门禁。
self-test A/B/C 三组对照直接断言以上语义（用生产 `runGate()` 路径）。

### 12 vs N：人工走查口径 ≠ 当前静态候选集（评审 P3-2）

> **数字以命令实际输出为准**：`--list-candidates` 头部会实时打印候选数量。下表里的 **17 / 3** 是
> 2026-09-18 在 `dev` 上的实测值，会随页面增删而变化 —— 本文不把它们当作常量。

| 数字 | 出处 | 口径 |
|---|---|---|
| **12** | 人工走查报告 `docs/验收测试/A-执行报告-前端体验走查260912.md:21,65,103`（任务单 `docs/成员任务单-二阶段整改260912.md:363` 引用了它） | 2026-09-12 人工走查时"长列表只能看第一页"的页面数（当时全仓只有 3 个页面有 `onReachBottom`） |
| **17**（实测，会变） | 本脚本 `--list-candidates` 的静态启发式候选 | 当前 `dev` 上「WXML 有 `wx:for` + 有 `request(` 调用 + 请求参数里**没有** `page`/`offset` 类字段」的页面数 |

**为什么不能直接等价**：走查是人工判断（含"列表会长到需要分页"的业务判断），
启发式只看语法（`wx:for` 也包含购物车、座位表这类**本来就不分页**的小列表，
例如 `life/order`、`library/seat`）；反过来，走查漏掉的页面启发式可能覆盖到。
所以候选数只是**待人工核对的清单长度**，不是缺陷数。

**当前真正被 R2 判违规的**，仍然只有"**请求里带页/偏移类分页证据、却没有任何触底加载钩子**"的页面
（实测全部已登记进 baseline）。要看候选清单与它们的请求路径：

```bash
node tools/verify_miniprogram_static_rules.js --list-candidates
```

**这些候选页面本 PR 一律不动**：谁真需要分页要人工确认（属 `F13`/`F17` 等页面任务的活）。

### 输出里的三档结论

1. **违规**：`RULE ID` + 文件:行 + 身份 + 证据 + 修复建议，并按 KNOWN / NEW / STALE 分列（见上）。
2. **覆盖提示（INFO，不计入退出码）**：说明某处**为什么没被判违规** —— 例如
   "有 `X.f || []` 兜底但没有 `X &&` 守卫"、"渲染列表但请求未带分页参数"、
   "某个 tap 处理器未能在页面 JS 中定位"。它们不会让 CI 变红，但把静态分析的边界摆到台面上。
3. **R2 候选清单**（`--list-candidates`）：见上「12 vs N」。

同一 `rule + file + line` 的多条证据会**合并成一条**（如一行里同时有静态 `BASE_URL` 与写死的地址字面量），
"违规处数"因此不会重复计数；两条稳定身份仍然各自保留（`#2` 消歧），baseline 需**逐身份**登记才算 KNOWN（self-test D1/D2/D3 对照）。

### 自证规模与"真的跑过"的不变量

`--self-test` 当前 **79 项断言**（原 43 + baseline/去重/身份/覆盖/缺失语义/参数校验对照 36），其中 6/6 negative controls 必须全过；
实际断言数每次运行都会打印（`合计 N 项断言全过`），以运行时输出为准。

门禁另有四条**防假绿**不变量（都直接断言生产路径）：

1. **扫描面下限**：`pages/**/*.js + 同名 .wxml` 一个都没扫到 → `[FAIL] 扫描面无覆盖`，不给 PASS；
2. **规则真的执行过**：每条规则记录"工作单元"检查计数（R1/R2 = 页面数，R3/R4/R5 = js 文件数，R6 = 页面 + 注册项 + 跳转扫描）；
   任何一条为 0 → `[FAIL] 规则未执行`。防的是"规则函数被删/没被调用 → 0 命中 → 假绿"（评审实测过这条路）；
3. **baseline 文件缺失** → **HARD FAIL**（版本化配置缺失 = 配置完整性错误），并给出"git 恢复 / `--strict` / `--baseline <file>`"三条出路；
4. **baseline 为空** → 合法稳态（打印 `ℹ️` 一行），此时任何违规都按 NEW 处理（空 baseline ≠ 免检）。

另外：**扫不到任何页面入口时脚本会直接红脸报错**（`[FAIL] 扫描面无覆盖`），
不会在"没检查"的情况下给出 `[PASS]` —— 口径与 `docs/CI.md`「宁可红灯，不要假绿」一致。

### 有意从宽的口径（评审确认过，不要当成漏检）

- `R4` 只判"**完全没兜底**"：`res.comments || []` 这种**字段兜底但接收者未守卫**的写法
  算通过（字段缺失这一坑已兜住；`res` 为 null 的风险另见覆盖提示，当前仓内 1 处：
  `miniprogram/pages/forum/detail.js:29`）。要收紧成 `(res && res.items) || []` 需先与成员1 对齐口径。
  同时这些写法都算"已守卫"、不得误报：`X && X.f.length ? X.f.length : 0`、
  `X.f && X.f.map(...)`、`if (X && X.f.length) { … }` 块内整块赋值。
- `R2` 的"分页"只认**页/偏移**类参数（`page`/`pageNo`/`pageNum`/`pageIndex`/`offset`/`skip`）；
  **只有 `limit` / `size` 的请求是"取前 N 条"的预览**（本仓首页 `/topics/hot` 的 `limit: 5`），不判定。
- `R3` 只判字面量 `'Y-m-d H:i:s'`（含模板串）与固定白名单后端时间字段；`new Date()`（无参）、
  `new Date(Date.now())`、ISO `T` 形式都**不算**违规 —— 本仓 `pages/library/seat.js:5`
  被走查报告点名的就是无参 `new Date()`，它本身是安全的。
- `R5` 允许 `config/env.js` 内出现 `http://127.0.0.1:8000/api/v1`（develop 默认值，`FRONT-01` 的设计）。
  其它文件若确实需要写死某个**外部链接**，把该字面量加进脚本里的 `URL_LITERAL_ALLOWLIST`
  并在同一行注释里写明理由（当前清单为空）。注释与**正则字面量**里的地址一律不看。

### 本脚本覆盖不到什么（**不要外推**）

- ✗ **视觉呈现**：玻璃模糊、发丝线、吸顶、凸起位置、动画顺滑度 → 真机 / 开发者工具目视。
- ✗ **真机与低端机性能**、iOS/Android 差异；`@supports` 类 WXSS 语法（不在官方列举内）必须真机抽测。
- ✗ **运行期行为**：请求是否真的发出、状态机 / 乱序守护 / 失败路径 → 见各特性 `verify_*.js`
  （如 `verify_frontend_base_url_guard.js`、`verify_f16_forum_search.js`）。
- ✗ **后端契约**：接口是否存在、返回字段是否真的叫 `items` —— 静态检查只保证"前端写法安全"。
- ✗ **R1** 只解析页面自身 JS 里"可定位"的处理器；箭头函数属性 / `behaviors` / 混入对象里的
  处理器不做判定（有这类情况会在**覆盖提示**里点名）。当前 29 页 83 个 tap 处理器全部可定位。
- ✗ **R2** 的"替代分页触发"只认 `onReachBottom` 与 `scroll-view` 的 `bindscrolltolower`；
  **请求不带分页参数的列表页一律不判定**（可能后端根本不支持分页），只在覆盖提示里列出、
  并可用 `--list-candidates` 逐个人工核对 —— 与走查报告的"12 个列表页"不是同一口径（见上「12 vs N」）。
- ✗ **R3** 变量来自数字时间戳时会误判为"后端时间字段直解"，需人工确认字段真实类型。
- ✗ **R4** 的"集合字段"口径由仓内既有写法**自校准**（`X.f || []`、`this.data.f.*`、
  响应变量上的 `X.f.map(...)`）：全新命名的列表字段若全仓都没出现过数组用法，则不在口径内。
- ✗ **R5** 只扫 JS 字面量与 `BASE_URL` 标识符；WXML/WXSS 里的外链图片地址不判定。
- ✗ **R6** 只认字符串字面量形式的跳转目标（`'/pages/x/y'`，允许带 `?query`）；
  由变量拼出来的动态路径不判定。

### 已登记的口径欠账（待下批次统一，勿当成漏检）

| # | 事项 | 位置 | 现状与理由 |
|---|---|---|---|
| D-1 | `R4` 接受 `X.f \|\| []`（任务单原文的写法是 `(X && X.f) \|\| []`） | `miniprogram/pages/forum/detail.js:29`（`commentList: (res.comments \|\| []).map(...)`） | **评审已裁决「不收紧」**：该写法已兜住"后端字段缺失 → 白屏"这一**有记录**的坑；收紧会与仓内 30 处既有写法冲突。`res` 本身为 null 的残余风险由脚本 INFO 提示登记（每次运行都会打印该处）。统一口径属**下批次的跨页面任务**（成员1）。 |
| D-2 | `R2` 的"无分页参数列表页"不判定 | 候选清单见 `--list-candidates`（数量会随页面变化） | 见上「12 vs N」：需人工确认后端是否分页；属 `F13`/`F17` 页面任务。 |

> 📌 这两条**只登记在本文档 + PR 描述**里，**没有**写进 `docs/技术方向待处理问题.md`：
> 那份是**跨成员共享的编号登记表**（近期已发生过 `CAC-30 → CAC-31` 撞号改号），
> 在 tooling PR 里占用编号容易与队友的 docs 改动冲突。若协调者希望进总表，
> 请分配编号后告知，我再补一条最小登记。

## 相邻脚本索引（同目录）

| 脚本 | 覆盖 |
|---|---|
| `verify_glass_probe.js` | F10 设计令牌 / 玻璃库能力探测与降级 |
| `verify_f11_icon_set.js` | F11 图标集样式契约（viewBox/currentColor/stroke-width） |
| `verify_frontend_base_url_guard.js` | FRONT-01 地址解析守卫的**运行期**行为（stub `wx`） |
| `verify_f16_forum_search.js` | F16 论坛搜索 + 7 标签栏的请求分派与状态机 |
| `verify_miniprogram_static_rules.js` | **F22 六条静态规则（本文件）** |
