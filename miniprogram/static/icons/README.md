# miniprogram/static/icons —— 苹果线性图标集（F11）

> 二阶段任务单 §3.3 **F11**：`static/icons/*.svg`（本地导出，`stroke-width:1.5`），补齐缺失的 `user-solid.svg`。
> 验收标准：**全站无彩色 / 粗重图标** —— 本任务的可判定口径 = **图标资产**（`static/icons/*.svg|png`）
> 在**全站零彩色引用**（彩色 PNG 引用 = 0，脚本断言）；**不包含 emoji 文本字形**。
> 口径依据、emoji 盘点（含与评审报告 109 处的对账）与归属见 **§5（务必先读）**。

---

## 1. 样式契约（新增图标必须遵守）

| 项 | 取值 |
|---|---|
| `viewBox` | `0 0 24 24` |
| 描边 | `stroke="currentColor"` |
| 线宽 | `stroke-width="1.5"` |
| 端点 / 拐角 | `stroke-linecap="round"` · `stroke-linejoin="round"` |
| 填充 | `fill="none"`（**禁用粗重填充**） |
| 外链 | 禁止 `href` / CDN / `<image>` / `url()`（原型页曾因 `modao.cc` 被拦踩坑） |
| 颜色 | 禁止硬编码颜色，颜色由使用方决定 |

**另外三条来自官方 `image` 组件的 SVG 限制**（[image 组件文档](https://developers.weixin.qq.com/miniprogram/dev/component/image.html)：svg 不支持**百分比单位**、不支持 `<style>` 元素；且 `mode=scaleToFill` 时 WebView 会居中）——因此图标
①不使用 `%` 单位、②不使用 `<style>`、③页面统一用 `mode="aspectFit"`。

校验命令（无需微信开发者工具、无需网络）：

```bash
node tools/verify_f11_icon_set.js
```

---

## 2. 图标清单（12 个）

| 文件 | 用途（页面入口） | 来源 |
|---|---|---|
| `chat-bubble-left-right.svg` | AI 助手 | 成员4 `ui/icons/navigation/`（逐字节接入） |
| `book-open.svg` | 图书馆预约 | 成员4 `ui/icons/business/`（逐字节接入） |
| `building-library.svg` | 查空教室 | 成员4 `ui/icons/business/`（逐字节接入） |
| `shopping-bag.svg` | 二手集市 | 成员4 `ui/icons/business/`（逐字节接入） |
| `map.svg` | 校园地图 | 成员4 `ui/icons/navigation/`（逐字节接入） |
| `bell.svg` | 通知公告 | 成员4 `ui/icons/action/`（逐字节接入） |
| `user-group.svg` | 校园论坛 | 成员4 `ui/icons/navigation/`（逐字节接入） |
| `user.svg` | 我的 | 成员4 `ui/icons/navigation/`（逐字节接入） |
| `briefcase.svg` | 兼职实习 | heroicons `briefcase`（成员4 图标集无对应**线性**版本，按同一来源补充） |
| `shopping-cart.svg` | 外卖点餐 | heroicons `shopping-cart`（同上） |
| `clipboard-document-list.svg` | 任务中心 | heroicons `clipboard-document-list`（同上） |
| `user-solid.svg` | 我的（选中态槽位） | 补齐项，见 §4 |

- 「逐字节接入」= 与 `ui/icons/` 下成员4 交付的文件 **SHA256 一致**，未重画、未改线宽。
- 三个补充项与成员4 的图标**同源**（heroicons，经 Iconify），属性顺序/格式一致，避免风格漂移；
  这 3 个语义（兼职 / 外卖 / 任务）在 `ui/icons/` 下确实没有线性版本。

---

## 3. 渲染与颜色（**已知限制，勿照抄 §1 的「颜色由使用方决定」**）

图标通过 `<image src="/static/icons/*.svg">` 消费。官方文档明确 `image` **支持 SVG**：

> 「图片。支持 JPG、PNG、SVG、WEBP、GIF 等格式」（[image 组件](https://developers.weixin.qq.com/miniprogram/dev/component/image.html)）

但 **`<image>` 里没有 CSS 上下文**，因此 SVG 内部的 `stroke="currentColor"` 会落到 `color` 的初始值 → **渲染为黑色细线**。也就是说：

- ✅ 满足 F11 验收的「**无彩色**」；
- ❌ **达不到**整改方案 §1.1 期望的「主色 `#4A90D9`」着色 —— 想改色必须换消费方式
  （内联 SVG / base64，或 `filter: drop-shadow(...)` 剪影技巧），或为每个颜色导出一份资源。

**本任务不引入着色方案**：F11 的交付物是 SVG 资产 + 页面引用，着色属于各页面的视觉整改（F13~F17）。
**真机渲染与视觉观感属 `MANUAL CHECK REQUIRED`**（见 §6）。

---

## 4. `user-solid.svg` 为什么是线性、且放在本目录

任务单要求「补齐缺失的 `user-solid.svg`」；`docs/PR39-审查报告260912.md` §9.3 记录该缺口位于
`ui/icons/navigation/`（5 组 TabBar 图标只有 4 组完整）。本仓本轮的处理与**边界**：

1. **放在 `miniprogram/static/icons/`** —— F11 的产出列即 `static/icons/*.svg`；`ui/` 归 `feature/ui`
   分支（`docs/分支规划与文件归属.md`），**不由前端任务代写**，否则等于替成员4 伪造交付物。
   ⇒ **`ui/icons/navigation/user-solid.svg` 的缺口仍然存在**，属成员4 / `feature/ui` 范围。
2. **内容遵守线性契约**（与 `user.svg` 同形）—— 本仓图标本轮统一为线性（整改方案 §1.1「禁用粗重填充」、
   §1.5「本轮统一改为线性图标集」），F11 验收又是「全站无彩色 / 粗重图标」。
   在纯线性体系里**选中态由颜色表达、不由填充表达**，故 `-solid` 槽位与线性槽位同形。
   ⚠️ 注意 `ui/README.md` 中 `-solid` 指的是 heroicons 的**填充**变体，本文件沿用文件名但**不是**填充图标。
3. **当前无引用**（`app.json` 的 tabBar 仍是纯文字）。它服务于后续消费方：**自定义 TabBar（F12）
   渲染自己的 WXML，可以正常使用 SVG**（原生 tabBar 不行——原生只接受 81×81 PNG，见 PR39 §9.3）。
4. **「与 `user.svg` 等值」已被断言锁定**（PR #77 评审 🟡 项的处置）：`tools/verify_f11_icon_set.js`
   断言两者**逐字节相同**。三选一的取舍 —— 改名会违背任务单点名的产出文件名（`user-solid.svg`），
   删除会让「补齐缺失」变成缺项，故取「锁死等值」：**只改其中一个立即报错**，防止后续悄然漂移。

---

## 5. 范围口径：F11 覆盖**图标资产**，**不包含** emoji 文本字形

### 5.1 「全站无彩色 / 粗重图标」的准确含义

任务单 §3.3 F11 的验收原文是「全站无彩色 / 粗重图标」。本任务据此把它落成**可判定**的一句：

> **图标资产**（`static/icons/` 下的 SVG / PNG）在**全站任何页面**都**不再有彩色引用** ——
> 彩色 PNG 图标引用数 = **0**（由 `tools/verify_f11_icon_set.js` **C 段断言**）。
> **emoji 文本字形不属于「图标资产」**，其盘点与归属见 §5.2 / §5.3。

依据：三份权威文档在谈图标时，**约束对象都是图标资产，均未把 emoji 字形列为约束**。

| 依据 | 原文（节选） | 约束对象 |
|---|---|---|
| 整改方案 §1.4 图标行 | 现状「部分缺失 / 彩色 PNG」→ 目标「**全部换苹果线性图标**」 | 图标资产 |
| 整改方案 §1.5 图标方案 | 只规定**来源**（Iconify 线性集）、**落地方式**（本地 SVG，不拉 CDN）、**线宽/尺寸** | 图标资产 |
| 整改方案 §7 验收 #1 | 「全部卡片/弹窗/TabBar/输入框走统一玻璃规范；**图标线宽一致**；**无纯色厚块残留**」 | 该行是**全局 UI 一致性**行；其中与图标相关的只有「图标线宽一致 / 无纯色厚块残留」，**未涉及 emoji** |

⚠️ 因此本任务**不声称**「全站已无任何彩色字形」：emoji 字形仍在（见 §5.2），
这是**口径边界**，不是遗漏 —— 若负责人要求改口径（把 emoji 纳入 F11），见 §5.4 的授权说明。

### 5.2 emoji 盘点（**可复现**，非手工计数）

```bash
node tools/verify_f11_icon_set.js    # 看输出 E 段（[INFO] 统计，不影响退出码）
```

**口径 A（本节采用）**：`Extended_Pictographic` 码点 + 独立 `VS16`(`U+FE0F`)；
**范围**：`miniprogram/` 下 `.js` / `.wxml` / `.wxss` / `.json`（页面实际渲染的源文件，不含 `.md` 文档）。
下表每个数字都由上面那条命令的 E 段直接打印：

| 范围 | 含 emoji 的文件数 | 处数 |
|---|---|---|
| `.wxml` | 24 | **83** |
| `.js` | 2 | **8** |
| `.wxss` | 0 | 0 |
| `.json` | 0 | 0 |
| **合计** | **26** | **91** |

`.wxml` 83 处的形态分布（同一口径，按行归类）：

| 形态 | 处数 | 实测字形（去重） |
|---|---|---|
| 错误态 `.xj-empty-icon` | 46 | `⚠️` |
| 空态 `.xj-empty-icon` | 20 | `🎯 ⏰ 💬 📝 💼 📄 🏫 📚 📅 💺 🍜 📢 🗺️ 🛍️ ⭐` |
| 行内文本字形 | 17 | `🤖 ❤️ 🤍 ⭐ 👍 💬 👋 🔍 ⚡ 🔥` |

**口径对账**（与 PR #77 评审报告的「30 文件 / 109 处」对齐 —— 结论：**同一份代码，差异全部来自字符类与 VS16 计法**）：

| 口径 | 字符类 | 结果 |
|---|---|---|
| **A（本节采用）** | `Extended_Pictographic` + 独立 VS16 | **91 处 / 26 文件**（`.wxml` 83 · `.js` 8） |
| 基字形 | 只数 `Extended_Pictographic` | 60 处（`.wxml` 56 · `.js` 4）；A − 基字形 = **独立 VS16 31 处** |
| 参考（含排版符号） | A ∪ `→ ← ↑ ↓` ∪ `①②…` | **108 处 / 30 文件**（`.wxml` 83 · `.js` 22 · `.wxss` 3） |

对账要点：

1. 评审列的 `.js/.wxss` 明细（`services/request.js` 13 · `config/env.js` 3 · `pages/chat/history.js` 3 ·
   `styles/glass.wxss` 3 · `pages/agent/index.js` 2 · `pages/secondhand/index.js` 1 = **25 处 / 6 文件**）
   与「参考口径」**逐文件完全一致** —— 该口径把 `→`（箭头）与 `①②`（带圈数字）也算作 emoji，
   但它们**不是** `Extended_Pictographic`，且都是**注释/文档性文字**，不参与渲染。
2. 评审列的「`⚠️`×27 + `⏳`×31 = 58 处」= 本口径的「基字形 `⚠` **27** 处 + 独立 `VS16` **31** 处」
   （分别见 E 段的「基字形 Top5」与「独立 VS16」两行）；`⏳`(`U+23F3`) 在 `miniprogram/` 下**不存在**
   （已用两种方法独立核实，最接近的是 `⏰` `U+23F0`，1 处），该 31 实为不可见的 `VS16` 变体选择符。
3. 评审的 `.wxml` 84 与本表的 83 相差 1，即单个「基字符 + VS16」的计法差异。
4. 本口径把 `⚠️` 记为 **2 处**（基字符 `U+26A0` + `VS16 U+FE0F`）；只数基字形则 `.wxml` 为 56 处 ——
   两种口径**都由 E 段直接打印**，无需另写脚本。

### 5.3 归属：这些 emoji 由谁清理

| 范围 | 处数 | 归属任务 |
|---|---|---|
| `pages/index/**` | 6 | **F13** 首页重构 |
| `pages/agent/**` + `pages/chat/**` | 10 | **F14** AI 助手 / 会话重构 |
| `pages/forum/**` | 16 | **F16** 论坛重构 |
| `pages/user/**` | 7 | **F17** 我的重构 |
| `pages/map/**` | 6 | **F18** 地图重构 |
| `pages/life/**` | 10 | **F19** 外卖模块 |
| `pages/library/**`（12）· `pages/job/**`（8）· `pages/secondhand/**`（8） | 28 | ⚠️ **任务单 §3.3 未列这三页的 F 任务号** —— 需负责人指定（并入 F20 或单开），本任务不擅自认领 |
| `services/request.js` + `config/env.js` | 8 | **无需任务**：8 处**全部在源码注释里**（`// ⚠️ 必须 try/catch…`），不参与渲染，不构成「彩色图标」 |
| 参考口径多出的部分（`→` `①②`，见 §5.2） | 17 | **无需任务**：均为**箭头 / 带圈数字**等排版符号（非 pictographic），且同样**全部在注释内**（`styles/glass.wxss` 与上述 `.js` 的文件头注释 / 行注释） |

> `pages/service/**`（F15 服务页）本身 **0 处 emoji**，无需处理。
> 上表只列**口径 A**的处数（合计 91 = 6+10+16+7+6+10+28+8）；「参考口径」额外多出的 17 处已在末行单列。

### 5.4 本任务不替换 emoji 的理由（保守、可回退）

emoji 是**文本字形**、不是本目录的图标资产；它们分布在**行内文本节点**里
（如 `👍{{item.likeCount}} · 💬{{item.commentCount}}`），替换会改变 WXML 结构与排版，
无法在本任务内做真机验证；且这些页面本就是各自 F 任务的整改对象，随各页重构落地更安全。

> 👉 若负责人要求 **F11 内**一并清除 emoji，等于扩大交付范围，请**明确授权并指定任务号**
> （或在任务单里把 emoji 写进 §3.3 F11 的产出列）；在那之前本任务按上一节的归属交付，不擅自扩大改动面。

---

## 6. 与旧 PNG 资产的关系

`static/icons/` 下原有 10 个 **192×192 彩色插画 PNG**（`ai-assistant.png` / `food.png` / …）。

- F11 已把**全部页面引用**切到本目录的 SVG，站点不再引用任何彩色 PNG；
- PNG 文件**保留不删**（任务约束：不要为完成 F11 删除既有 PNG 资产），后续如确认无引用可另行清理。

**尺寸说明**：当前宫格 `.grid-icon` 为 96rpx（`pages/index/index.wxss`、`pages/service/service.wxss`）。
整改方案 §1.5 建议「功能卡 56rpx」，那属于 **F15 服务页重构**的视觉改动，不在 F11 内改。

---

## 7. 依赖澄清（任务描述与仓库文档不一致，以仓库为准）

本轮任务描述把 F11 的依赖标为「**D8（成员4）**」。核对仓库文档后：

- 任务单 §3.4：**`D8` = ER 图更新**（`docs/db/ER图.md`），与图标无关；
- 任务单 §3.3 F11 行的「依赖」列是「**—**」（无前置依赖）；
- F11 真正需要的**图标源资产**是 `ui/icons/**`（43 个 SVG），**已合入 `origin/dev`**
  （commit `8731a8d`，PR #39 系列，`git merge-base --is-ancestor` 已验证）。

⇒ F11 的前置条件在 `origin/dev` 上**已满足**，无需等待 D8，也未从任何未合并分支取材。

---

## 8. 来源与版权

全部来自 **heroicons**（MIT License，经 Iconify 导出为本地 SVG）。

- 整改方案 §1.5 曾建议 `solar:*-linear` / `tabler:*-outline` / `ph:*-light`；
  本轮**刻意沿用 heroicons** —— 成员4 已合入 `origin/dev` 的 43 个图标全部是 heroicons，
  混用图标集会破坏线宽/端点的一致性。
- 不引入需付费或来源不明的图标资源（整改方案 §6 风险 4：图标资源版权）。
