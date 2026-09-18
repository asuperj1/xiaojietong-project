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

覆盖的六条规则（来源见脚本头部注释，逐条给出仓内证据）：

| RULE ID | 规则 |
|---|---|
| `R1_TAP_DETAIL_VALUE` | tap/长按处理器不得读 `e.detail.value`（索引取 `e.currentTarget.dataset.*`） |
| `R2_MISSING_LOWER_TRIGGER` | 分页列表页必须有 `onReachBottom` 或 `scroll-view` 的 `bindscrolltolower` |
| `R3_DATETIME_STRING_PARSE` | 不得用 `new Date('Y-m-d H:i:s')` / 后端时间字段直解（iOS `Invalid Date`） |
| `R4_LIST_FIELD_NO_FALLBACK` | 后端列表字段必须先兜底（`(res && res.items) \|\| []`）再当数组用 |
| `R5_HARDCODED_BASE_URL` | 除 `config/env.js` 外不得出现写死的接口地址 / 静态 `BASE_URL` |
| `R6_PAGE_REGISTRATION` | 页面须注册进 `app.json#pages`；TabBar 项与跳转目标须与 `pages` 一致 |

### 本脚本覆盖不到什么（**不要外推**）

- ✗ **视觉呈现**：玻璃模糊、发丝线、吸顶、凸起位置、动画顺滑度 → 真机 / 开发者工具目视。
- ✗ **真机与低端机性能**、iOS/Android 差异；`@supports` 类 WXSS 语法（不在官方列举内）必须真机抽测。
- ✗ **运行期行为**：请求是否真的发出、状态机 / 乱序守护 / 失败路径 → 见各特性 `verify_*.js`
  （如 `verify_frontend_base_url_guard.js`、`verify_f16_forum_search.js`）。
- ✗ **后端契约**：接口是否存在、返回字段是否真的叫 `items` —— 静态检查只保证"前端写法安全"。
- ✗ **R1** 只解析页面自身 JS：写在 `behaviors` / 混入对象里的处理器不在覆盖内。
- ✗ **R2** 的"替代分页触发"只认 `onReachBottom` 与 `scroll-view` 的 `bindscrolltolower`；
  自定义"加载更多"按钮、或"分页参数由后端默认值决定"不判定。
- ✗ **R3** 只判字面量 `'Y-m-d H:i:s'`（**不含** ISO `T` 形式）与固定白名单后端时间字段；
  变量来自数字时间戳时会误判，需人工确认。
- ✗ **R4** 的"集合字段"口径由仓内既有写法**自校准**（`X.f || []` / `X.f.map(...)`）：
  全新命名的列表字段若全仓都没出现过数组用法，则不在口径内。
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
