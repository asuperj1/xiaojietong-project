# miniprogram/static/icons —— 苹果线性图标集（F11）

> 二阶段任务单 §3.3 **F11**：`static/icons/*.svg`（本地导出，`stroke-width:1.5`），补齐缺失的 `user-solid.svg`。
> 验收标准：**全站无彩色 / 粗重图标**。

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

校验命令（无需微信开发者工具、无需网络）：

```bash
node tools/verify_f11_icon_set.js
```

---

## 2. 图标清单（12 个）

| 文件 | 用途（页面入口） | 来源 |
|---|---|---|
| `chat-bubble-left-right.svg` | AI 助手 | 成员4 `ui/icons/navigation/`（原样接入） |
| `book-open.svg` | 图书馆预约 | 成员4 `ui/icons/business/`（原样接入） |
| `building-library.svg` | 查空教室 | 成员4 `ui/icons/business/`（原样接入） |
| `shopping-bag.svg` | 二手集市 | 成员4 `ui/icons/business/`（原样接入） |
| `map.svg` | 校园地图 | 成员4 `ui/icons/navigation/`（原样接入） |
| `bell.svg` | 通知公告 | 成员4 `ui/icons/action/`（原样接入） |
| `user-group.svg` | 校园论坛 | 成员4 `ui/icons/navigation/`（原样接入） |
| `user.svg` | 我的（未选中态） | 成员4 `ui/icons/navigation/`（原样接入） |
| `briefcase.svg` | 兼职实习 | heroicons `briefcase`（成员4 图标集无对应**线性**版本，按同一来源补充） |
| `shopping-cart.svg` | 外卖点餐 | heroicons `shopping-cart`（同上） |
| `clipboard-document-list.svg` | 任务中心 | heroicons `clipboard-document-list`（同上） |
| `user-solid.svg` | 我的（选中态占位） | 补齐项，见 §3 |

- 「原样接入」= 与 `ui/icons/` 下成员4 交付的文件**逐字节一致**，未重画、未改线宽。
- 三个补充项与成员4 的图标**同源**（heroicons，经 Iconify），属性顺序/格式一致，避免风格漂移。

---

## 3. `user-solid.svg` 为什么是线性而非填充

任务单要求「补齐缺失的 `user-solid.svg`」（PR #39 审查 §9.3 记录：`ui/icons/navigation/` 缺该文件，
5 组 TabBar 图标只有 4 组完整）。本仓本轮的处理：

- **保留该文件名** —— 消费者按名引用即可拿到图标，不再 404；
- **内容遵守线性契约** —— 与 `user.svg` 同形。F11 的验收标准是「全站无彩色 / 粗重图标」，
  且本仓图标体系本轮**统一为线性**（整改方案 §1.1「禁用粗重填充」、§1.5「本轮统一改为线性图标集」）。
  在纯线性体系里，**选中态由颜色表达、不由填充表达**，因此 `-solid` 槽位与线性槽位同形。

> ⚠️ 如果后续要为**原生 tabBar** 导出「选中态实心」位图，那是另一条工序：
> 原生 tabBar 只接受 **81×81 PNG**（不支持 SVG / 网络图 / base64），
> 且 `ui/` 目录归 `feature/ui` 分支（成员4）。见 `docs/PR39-审查报告260912.md` §9.3。

---

## 4. 与旧 PNG 资产的关系

`static/icons/` 下原有 10 个 **192×192 彩色插画 PNG**（`ai-assistant.png` / `food.png` / …），
是首页与服务页宫格的历史图标，与「苹果线性」规范冲突。

- F11 已把**全部页面引用**切换到上面的 SVG，站点不再使用彩色 PNG；
- PNG 文件**保留不删**（任务约束：不要为完成 F11 删除既有 PNG 资产），后续如无引用可另行清理。

---

## 5. 版权

全部来自 **heroicons**（MIT License，经 Iconify 导出为本地 SVG）。
不引入需付费或来源不明的图标资源（整改方案 §6 风险 4：图标资源版权）。
