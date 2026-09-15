# custom-tab-bar —— 自定义 TabBar **POC**（F12）

> 依据：`docs/二阶段整改方案-前端UI重构与后端支撑.md` §1.4（原生 tabBar 无法做凸起圆形按钮 → 改用自定义）
> `docs/成员任务单-二阶段整改260912.md` §3.3 **F12**：「**先做 POC**」；验收：真机凸起位置正确 / 长按有震动并弹窗 / 全屏页正确隐藏。
>
> ⚠️ **这是 POC，不是最终实现。** 它的目的是在真机上先验证「自定义 TabBar 这条路走不走得通」，
> 再决定是否铺开（方案 §6 风险 2：改造面大，不可行则退化为「普通 5 tab + 独立语音按钮」）。

---

## 1. 本 POC 要验证的 5 件事

| # | 验收点 | 实现落点 | 自动化断言 |
|---|---|---|---|
| ① | 5 项可显示、可切换 | `index.wxml` / `onItemTap` → `wx.switchTab` | ✅ 真实调用断言 |
| ② | AI **居中**凸起圆形 | `list[2].raised` + `.tabbar-fab`（96rpx 圆、`top: -20rpx` 上浮） | ✅ 位置与样式断言 |
| ③ | 长按 AI 唤起语音触发链路 | `bindlongpress` → `onItemLongPress`（仅 `raised` 项响应） | ✅ 含**反向对照** |
| ④ | 震动 + 弹窗 | `wx.vibrateShort` + `wx.showModal` | ✅ 真实调用断言 |
| ⑤ | 全屏页隐藏机制 | `setHidden()` + `.tabbar--hidden` + `utils/tabbar.js` | ✅ 转发与容错断言 |

一键自查（无需微信开发者工具、无需网络）：

```bash
node tools/verify_f12_tabbar_poc.js              # 80 项断言
node tools/negative_control_f12_tabbar_poc.js    # 反向对照：证明上面这套断言真的会 FAIL
```

> 上浮量用**绝对定位的 `top: -20rpx`** 表达（而非负 `margin-top`）：圆形按钮需要溢出
> `.tabbar` 顶边，`position: absolute` 才能让溢出量精确可控、且不受 flex 行高影响。
> 真机若觉得凸起不足/过多，**只改 `index.wxss` 里这一个值**。

---

## 2. 真机验证步骤（**MANUAL CHECK REQUIRED**）

代码级断言**不能**替代以下人工检查 —— 请在微信开发者工具 + 真机上逐条确认：

1. **凸起位置**：底部 5 项均匀分布，AI 圆形按钮**位于正中**（水平居中）且**凸出 TabBar 顶边**，未遮挡页面内容、未被裁剪。
2. **长按语音**：长按 AI 圆形按钮 → **机身震动** → 弹出「语音输入（POC）」弹窗；点「录音自检」→ 2.5 秒后弹出录音结果。
   - 长按热区是**整个 AI 项**（圆 + 「AI助手」文字），不是只有 96rpx 的圆 —— 圆太小真机上不好按。
     语义不变：`data-raised` 守卫保证只有 AI 项响应。
   - 首次会请求录音权限（`scope.record`）；拒绝后应弹出「录音失败」而非静默无反应。
   - 短按（点击）应正常切到 AI 页，**不应**弹窗。
3. **非 AI 项**：点击其它 4 项正常切换；长按它们**不应**有震动或弹窗。
4. **全屏隐藏**：AI 页顶部「全屏POC」→ TabBar 应整体下移淡出；**输入条应随之落到底部**（全屏态不再保留底栏留白）；按钮变为「显示底栏」，再点恢复。
5. **安全区**：iPhone（带 Home Indicator）上 TabBar 不应被底部横条压住。
6. **玻璃降级**：低端 Android 上应走实心底（`.is-glass-fallback`），文字可读、无明显掉帧。

---

## 3. POC 边界（刻意不做的事）

| 边界 | 原因 |
|---|---|
| **不含任何图标**（AI 圆内是文字「AI」） | F11 的线性 SVG 图标集**尚未合入 `dev`**；且刻意**不叠 F11 分支、不复用将被 F11 淘汰的彩色 PNG**。图标接入待 F11 合入 `dev` 后由 F12 正式实现完成。 |
| **不做语音转写** | 转写有两条路，**当前一条都未接通**：<br>① **B28 · 自建后端**（方案 §3.4 路线 B）：`POST /voice/transcribe` 在 `origin/dev` 上不存在（已核查：`backend/` 下无任何 `voice` 模块，`routers/` 只有 admin/agent/auth/chat/favorite/forum/health/job/library/life/map_api/secondhand/upload/user）。<br>② **微信原生 · 零后端路线**（方案 §3.4 **推荐路线 A**）：`wx.getRecorderManager()` 录音 + 微信同声传译插件 / `wx.serviceMarket` —— **不需要后端成本**、延迟低，代价是依赖微信配额，且需在小程序后台开通插件；属 F12 之后的独立接入决策。<br>因此本 POC 弹窗内只做**录音自检**（本地录音、不上传、不假装能转写）；**长按 → 震动 → 弹窗**这条链路本身已端到端可验证，转写只是它的下游。 |
| **全屏隐藏用「顶栏按钮」触发** | 让「全屏隐藏」这条验收**在真机上可反复验证且可逆**，而不是留一个永不触发的机制。F14 接入侧边栏后应删除该按钮，改由侧边栏/全屏会话状态驱动。 |
| **不调整宫格图标尺寸等视觉细节** | 属 F15 服务页重构范围。 |

---

## 3.1 F10 设计令牌在自定义组件内的可用性（**已知边界**）

`styles/tokens.wxss` 把 CSS 变量定义在 `page` 选择器上（其文件头亦注明「自定义组件需结合
`styleIsolation` / 独立 WXSS 处理」）。变量定义在 `page` 上、而组件节点是其后代时**继承应当成立**，
但这一点无法靠静态检查证实，需要在开发者工具里确认。

因此本组件的硬性约定是：**每个 `var()` 都必须带 literal fallback**（由校验脚本断言，
当前 15/15 处全部带 fallback）。这样即使令牌完全取不到，样式也不会丢失 —— 这正是 F10
progressive enhancement 的「安全默认态」原则。

同理，`glass.wxss` 的 `.is-glass-fallback .xj-glass-strong` 会重置 `border-color` 与 `box-shadow`，
故凸起圆形的**蓝色描边在 `@import` 之后用更高权重再声明一次**，保证降级态下凸起圆形仍可辨识
（白色实心圆 + 蓝色描边），不退化成一片白。

---

## 4. 一处**有意的行为变更**：Tab 顺序

方案 §1.4 要求「**AI 助手置于正中**」，而现状 `app.json` 的顺序是 首页 / AI / 服务 / 论坛 / 我的（AI 在第 2 位，凸起会偏左）。因此本 POC 把顺序调整为：

```
首页 · 服务 · AI助手(凸起) · 论坛 · 我的
```

- 影响面：`wx.switchTab` 按 **path** 跳转，顺序变化**不影响任何路由**；`app.json` 的 `list` 顺序在 `custom: true` 下不参与渲染。
- 唯一事实来源：`utils/tab-order.js` 的 `TAB_ORDER`（JS 侧唯一一份，组件与助手都从这里取；与 `app.json` 的一致性由校验脚本断言）。

---

## 5. 接入方式与回退

- 接入：`app.json` 的 `tabBar.custom = true`；每个 Tab 页在 `onShow` 调 `syncTabBar(this, '<key>')`。
  （自定义 TabBar 的组件实例**每页各一份**，选中态必须由页面主动同步；`getTabBar()` 在非 Tab 页返回 `undefined`，助手已容错。）
- **回退（POC 建议保留的后路）**：删掉 `app.json` 里的 `"custom": true` 即回到原生文字 TabBar，其余代码不参与渲染、无副作用。
  ⚠️ 但**回退不会还原 Tab 顺序** —— `app.json` 的 `list` 已经是「首页·服务·AI·论坛·我的」，
  原生 TabBar 会照样按这个顺序显示。若确实要回到旧顺序，需同时把 `list` 改回原样（并同步 `utils/tab-order.js`）。

---

## 6. 文件清单

| 文件 | 作用 |
|---|---|
| `custom-tab-bar/index.{js,json,wxml,wxss}` | 自定义 TabBar 组件（5 项 / 凸起圆形 / 长按语音 / 显隐） |
| `miniprogram/utils/tab-order.js` | Tab 清单与顺序（**JS 侧唯一事实来源**，组件与助手共用） |
| `miniprogram/utils/tabbar.js` | 选中态同步 + 显隐助手（含 `getTabBar()` 容错） |
| `miniprogram/app.json` | 打开 `tabBar.custom`、调整 Tab 顺序 |
| `miniprogram/pages/*/`（5 个 Tab 页） | `onShow` 同步选中项；AI 页额外提供 POC 全屏开关 |
| `tools/verify_f12_tabbar_poc.js` | 80 项自动化断言（含反向对照） |
| `tools/negative_control_f12_tabbar_poc.js` | 变异测试：故意改坏副本，证明上面 80 项断言不是「只会 PASS」 |

> `app.json` 的 `tabBar.list` 是**必要副本**（JSON 无法 import，且框架要求声明），
> 与 `utils/tab-order.js` 的顺序/路径一致性由校验脚本断言。
