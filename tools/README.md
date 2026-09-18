# tools/ —— 校验与运维脚本

本目录放**不依赖微信开发者工具 / MySQL / 后端 / 网络**也能跑的校验脚本。
约定：`[OK]` / `[NG]` 逐项断言 + 末尾 `[PASS]` / `[FAIL]` 汇总；
退出码 `0` = 通过、`1` = 有失败。涉及断言的脚本都要有**反向对照**
（缺陷样本 / 突变注入），否则"现在通过"证明不了任何事。

## F22 · 小程序端静态检查

```bash
node tools/verify_miniprogram_static_rules.js             # 只读扫描 miniprogram/（退出码 0/1）
node tools/verify_miniprogram_static_rules.js --self-test  # 六条规则的阴性对照（TEMP fixture）
node tools/verify_miniprogram_static_rules.js --src <repo-root>
```

> 📌 **文件名说明**：任务单（`docs/成员1任务单-20260918.md` §2.3 / §5 第 7 项）里写的是
> `tools/verify_miniprogram_rules.js`；本仓实际落地文件是
> **`tools/verify_miniprogram_static_rules.js`**。接 CI（`B34`）时请用实际路径。

覆盖的六条规则（来源见脚本头部注释，逐条给出仓内证据）：

| RULE ID | 规则 |
|---|---|
| `R1_TAP_DETAIL_VALUE` | tap/长按处理器不得读 `e.detail.value`（索引取 `e.currentTarget.dataset.*`） |
| `R2_MISSING_LOWER_TRIGGER` | 分页列表页必须有 `onReachBottom` 或 `scroll-view` 的 `bindscrolltolower` |
| `R3_DATETIME_STRING_PARSE` | 不得用 `new Date('Y-m-d H:i:s')` / 后端时间字段直解（iOS `Invalid Date`） |
| `R4_LIST_FIELD_NO_FALLBACK` | 后端列表字段必须先兜底（`(res && res.items) \|\| []`）再当数组用 |
| `R5_HARDCODED_BASE_URL` | 除 `config/env.js` 外不得出现写死的接口地址 / 静态 `BASE_URL` |
| `R6_PAGE_REGISTRATION` | 页面须注册进 `app.json#pages`；TabBar 项与跳转目标须与 `pages` 一致 |

### 输出里的两档结论

1. **违规（决定退出码）**：`RULE ID` + 文件:行 + 证据 + 修复建议。
2. **覆盖提示（INFO，不计入退出码）**：说明某处**为什么没被判违规** —— 例如
   "有 `X.f || []` 兜底但没有 `X &&` 守卫"、"渲染列表但请求未带分页参数"、
   "某个 tap 处理器未能在页面 JS 中定位"。它们不会让 CI 变红，但把静态分析的边界摆到台面上。

### 有意从宽的口径（评审确认过，不要当成漏检）

- `R4` 只判"**完全没兜底**"：`res.comments || []` 这种**字段兜底但接收者未守卫**的写法
  算通过（字段缺失这一坑已兜住；`res` 为 null 的风险另见覆盖提示，当前仓内 1 处：
  `miniprogram/pages/forum/detail.js:29`）。要收紧成 `(res && res.items) || []` 需先与成员1 对齐口径。
- `R3` 只判字面量 `'Y-m-d H:i:s'` 与固定白名单后端时间字段；`new Date()`（无参）、
  `new Date(Date.now())`、ISO `T` 形式都**不算**违规 —— 本仓 `pages/library/seat.js:5`
  被走查报告点名的就是无参 `new Date()`，它本身是安全的。
- `R5` 允许 `config/env.js` 内出现 `http://127.0.0.1:8000/api/v1`（develop 默认值，`FRONT-01` 的设计）。
  其它文件若确实需要写死某个**外部链接**，把该字面量加进脚本里的 `URL_LITERAL_ALLOWLIST`
  并在同一行注释里写明理由（当前清单为空）。

### 本脚本覆盖不到什么（**不要外推**）

- ✗ **视觉呈现**：玻璃模糊、发丝线、吸顶、凸起位置、动画顺滑度 → 真机 / 开发者工具目视。
- ✗ **真机与低端机性能**、iOS/Android 差异；`@supports` 类 WXSS 语法（不在官方列举内）必须真机抽测。
- ✗ **运行期行为**：请求是否真的发出、状态机 / 乱序守护 / 失败路径 → 见各特性 `verify_*.js`
  （如 `verify_frontend_base_url_guard.js`、`verify_f16_forum_search.js`）。
- ✗ **后端契约**：接口是否存在、返回字段是否真的叫 `items` —— 静态检查只保证"前端写法安全"。
- ✗ **R1** 只解析页面自身 JS 里"可定位"的处理器；箭头函数属性 / `behaviors` / 混入对象里的
  处理器不做判定（有这类情况会在**覆盖提示**里点名）。当前 29 页 83 个 tap 处理器全部可定位。
- ✗ **R2** 的"替代分页触发"只认 `onReachBottom` 与 `scroll-view` 的 `bindscrolltolower`；
  **请求不带分页参数的列表页一律不判定**（可能后端根本不支持分页），只在覆盖提示里列出
  —— 任务单/走查报告说的"12 个列表页"就是这一类，需人工确认后再补钩子或补后端分页。
- ✗ **R3** 变量来自数字时间戳时会误判为"后端时间字段直解"，需人工确认字段真实类型。
- ✗ **R4** 的"集合字段"口径由仓内既有写法**自校准**（`X.f || []` / `X.f.map(...)`）：
  全新命名的列表字段若全仓都没出现过数组用法，则不在口径内（要覆盖它得先让仓内出现一次对照写法）。
- ✗ **R5** 只扫 JS 字面量与 `BASE_URL` 标识符；WXML/WXSS 里的外链图片地址不判定。
- ✗ **R6** 只认字符串字面量形式的跳转目标（`'/pages/x/y'`）；拼接出来的动态路径不判定。

## 相邻脚本索引（同目录）

| 脚本 | 覆盖 |
|---|---|
| `verify_glass_probe.js` | F10 设计令牌 / 玻璃库能力探测与降级 |
| `verify_f11_icon_set.js` | F11 图标集样式契约（viewBox/currentColor/stroke-width） |
| `verify_frontend_base_url_guard.js` | FRONT-01 地址解析守卫的**运行期**行为（stub `wx`） |
| `verify_f16_forum_search.js` | F16 论坛搜索 + 7 标签栏的请求分派与状态机 |
| `verify_miniprogram_static_rules.js` | **F22 六条静态规则（本文件）** |
