# miniprogram/static/icons —— 苹果线性图标集（F11）

> 二阶段任务单 §3.3 **F11**：`static/icons/*.svg`（本地导出，`stroke-width:1.5`），补齐缺失的 `user-solid.svg`。
> 验收标准：**全站无彩色 / 粗重图标**（残留范围见 **§5**，务必先读）。

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

---

## 5. ⚠️ 范围边界：本任务**没有**清除 emoji 字形

F11 验收写作「全站无彩色 / 粗重图标」。本任务按整改方案 §1.4 的口径执行 ——
那里的「现状」是「**部分缺失 / 彩色 PNG**」，「目标」是「**全部换苹果线性图标**」，
即针对**图标资产**（`static/icons/*.png` 10 个 192×192 彩色插画）。

**彩色 PNG 已 100% 清除**（由 `tools/verify_f11_icon_set.js` 断言：全站零 `.png` 图标引用）。
但**彩色 emoji 字形仍在**，实测 **24 个 wxml / 83 处**：

| 形态 | 数量 | 例子 |
|---|---|---|
| `.xj-empty-icon` 错误态 `⚠️` | 23 | `pages/agent/index.wxml`、`pages/forum/forum.wxml` |
| `.xj-empty-icon` 空态 | 约 13 | `🎯 💼 📄 🏫 📚 📅 💺 🍜 📢 🗺️ 🛍️ 💬` |
| 行内文本字形 | 约 47 | `🔍`(搜索框) `👋` `⚡` `🔥` `👍` `💬` `❤️` `🤍` `⭐` `☆` |

**未替换的理由**（保守、可回退）：emoji 是**文本字形**、不是本目录的图标资产；它们分布在
**行内文本节点**里（如 `👍{{item.likeCount}} · 💬{{item.commentCount}}`），替换会改变
WXML 结构与排版，无法在本任务内做真机验证；且这些页面本就是 F13（首页）/ F15（服务页）/
F16（论坛）/ F17（我的）的整改对象，随各页重构落地更安全。

> 👉 **需要产品/负责人确认**：若要求 F11 内一并清除 emoji，请开独立任务（或并入 F13~F17），
> 本任务不擅自扩大改动面。

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
