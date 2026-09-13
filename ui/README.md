# ui —— UI 素材库（成员4 协助维护）

> 素材来源：墨刀 HTML 原型 `xiaojietong-app`（18 个页面）中抠取、分类、归档。
> 供**前端（成员1）**在 `miniprogram/` 开发时直接取用。
> 机器可读清单见 [`manifest.json`](manifest.json)（图标/配图/原型页索引 + 被哪些页面使用）。

---

## 1. 目录结构

```
ui/
├── icons/                  # 从原型抠出的图标（SVG 矢量，透明底）
│   ├── navigation/         # 导航类：TabBar / 返回 / 地图
│   ├── action/             # 操作类：搜索 / 点赞 / 收藏 / 发送 / 通知 / 设置
│   ├── status/             # 状态类：状态栏 / 警告 / 锁定 / 时间
│   └── business/           # 业务类：课表 / 图书馆 / 校园卡 / 二手 / AI
├── images/                 # 原型中的配图（JPEG，已语义化命名）
├── prototype/              # 18 个 HTML 原型页存档（设计蓝本，浏览器直接打开）
│   ├── _all.html           # ★ 总览页：一屏缩略预览全部 18 个原型
│   └── vendor/             # ★ 本地化第三方脚本（见 §4.1，勿手动改）
├── manifest.json           # 素材索引（图标分类 + 使用页面 + 图片说明 + 原型页清单）
├── yemian.drawio           # 页面流程图（drawio 源文件）
└── README.md               # 本文件
```

**统计**：43 个图标 · 5 张配图 · 18 个原型页。

---

## 2. 图标清单（全部来自 heroicons，经 Iconify 抠取）

图标为 `currentColor` 矢量，**颜色由使用方决定**（原型中主色 `#3b82f6` 蓝、未选中 `#9ca3af` 灰）。

### 🧭 navigation —— 导航 / TabBar（13）

| 图标 | 用途 | 使用页面 |
|---|---|---|
| `home-solid` / `home` | 首页（选中 / 未选中） | home 等全部 |
| `arrow-left-20-solid` | 返回上一页 | 15 个二级页 |
| `squares-2x2` / `squares-2x2-solid` | 服务中心 TabBar | services |
| `user` | 我的 TabBar | profile |
| `user-group` / `user-group-solid` | 社区 TabBar | community |
| `chat-bubble-left-right` / `-solid` / `-20-solid` | AI 助手 TabBar | ai |
| `map` / `map-20-solid` | 校园地图 | map |

### ⚡ action —— 操作 / 交互（11）

| 图标 | 用途 | 使用页面 |
|---|---|---|
| `magnifying-glass-20-solid` | 搜索 | forum_new、secondhand_list、services |
| `hand-thumb-up-20-solid` | 点赞 | forum_*、secondhand_detail |
| `paper-airplane-20-solid` | 发送（发帖 / 评论 / 对话） | forum_new、ai |
| `bookmark-20-solid` | 收藏 | forum_*、secondhand_detail |
| `photo-20-solid` | 图片 / 相册（发布时选图） | secondhand_publish、publish_entry |
| `bell-20-solid` / `bell-alert-20-solid` / `bell` | 通知 / 告警 / 线框通知 | notifications、profile |
| `cog-6-tooth-20-solid` | 设置 | profile |
| `check-circle-20-solid` | 成功 / 已完成 | 状态反馈 |
| `hand-raised` | 举手 / 互动 | community |

### 📶 status —— 状态栏 / 系统（6）

| 图标 | 用途 |
|---|---|
| `signal-20-solid` / `wifi-20-solid` / `battery-50-20-solid` | 手机状态栏（信号 / WiFi / 电量） |
| `exclamation-triangle-20-solid` | 警告 / 错误状态（error 页） |
| `lock-closed-20-solid` | 锁定 / 隐私 |
| `clock-20-solid` | 时间 / 待处理 |

### 📚 business —— 业务内容（13）

| 图标 | 用途 | 使用页面 |
|---|---|---|
| `calendar-days` / `calendar-20-solid` | 课表 / 日期 | home、services |
| `building-library` / `building-library-20-solid` | 图书馆 / 教室预约 | home、services |
| `credit-card` | 校园卡 | home |
| `book-open` / `book-open-20-solid` | 借阅查询 | home |
| `shopping-bag` / `shopping-bag-20-solid` | 二手交易 | secondhand_* |
| `chart-bar` | 数据 / 统计 | services |
| `cake-20-solid` | 个人资料 / 生日 | profile |
| `sparkles-20-solid` | AI 推荐 / 智能标识 | recommend_explain、ai |
| `chat-bubble-oval-left-ellipsis-20-solid` | AI 对话 / 更多消息 | ai、messages |

> 完整「图标 ↔ 使用页面」映射见 `manifest.json` 的 `icons[].used_in`。

---

## 3. 配图清单（images/）

| 文件 | 说明 | 来源页面 |
|---|---|---|
| `campus-scenery-library.jpg` | 首页「今日风景」——中心图书馆的午后 | home |
| `secondhand-textbook.jpg` | 二手商品图——教材 | secondhand_list |
| `secondhand-ipad-air4.jpg` | 二手商品图——iPad Air 4 | secondhand_list |
| `secondhand-bicycle.jpg` | 二手商品图——自行车 | secondhand_list |
| `secondhand-exam-papers.jpg` | 二手商品图——考研资料 | secondhand_list |

> 均为 1024×1024 演示用图（原型自带），仅用于开发期占位；正式上线请替换为真实图片并确认版权。

---

## 4. HTML 原型页（prototype/）

18 个页面存档，可直接用浏览器打开查看设计：

| 分组 | 页面 |
|---|---|
| 主框架 | `home`（首页）、`ai`（AI 助手）、`services`（服务中心）、`community`（社区中心）、`profile`（我的） |
| 二手交易 | `secondhand_list`、`secondhand_detail`、`secondhand_publish`、`publish_entry` |
| 论坛社区 | `forum_hot`（热榜）、`forum_new`（论坛）、`messages`（消息）、`notifications`（通知） |
| 其他 | `map`（校园地图）、`recommend_explain`（推荐说明） |
| 状态页 | `loading`（加载）、`empty`（空结果）、`error`（错误） |

### 4.1 离线化第三方依赖（`prototype/vendor/`）

> 历史问题：原型原先从 `modao.cc` CDN 加载 Tailwind CSS 与 Iconify，**离线 / 内网打开会丢失样式与图标**（封测演示现场尤易翻车）。

已于 **2026-09-12** 完成本地化：

| 文件 | 作用 | 来源 |
|---|---|---|
| `prototype/vendor/tailwindcss.js`（约 397 KB） | Tailwind Play CDN 脚本 | 官方 CDN 快照 |
| `prototype/vendor/iconify-icon.min.js`（约 21 KB） | Iconify Web Component | 官方 CDN 快照 |

- 18 个原型页的 `<script src="...">` 已全部改为**相对路径** `vendor/*.js` → **断网可用**。
- `prototype/_all.html` 为**总览页**，一屏缩略预览全部 18 页，适合汇报 / 评审时快速翻页。

> ⚠️ `vendor/` 下为**第三方压缩产物，勿手动修改**；升级请整文件替换并同步本表。
> 图标仍为本地 SVG（`icons/`），与 `vendor/` 无关。

---

## 5. 使用说明（前端）

1. **图标换色**：SVG 使用 `currentColor`，改 CSS `color` 即可（如选中态 `#3b82f6`、未选中 `#9ca3af`）。
2. **小程序引用**：`<image>` 对 SVG 支持有限，建议二选一——
   - 转 PNG（@2x/@3x）后放 `miniprogram/static/icons/`；
   - 或做成 iconfont / 内联 base64，减少请求数。
3. **尺寸**：原型中 TabBar 图标约 `24px`（`text-2xl`）、页面内图标 `16~20px`，按此导出即可。
4. **命名**：新增素材请沿用「语义化小写 + 连字符」命名（如 `secondhand-bicycle.jpg`），并同步更新 `manifest.json`。

---

## 6. 维护约定

- 本目录归属 **`feature/ui`** 分支（见 `docs/团队Git合作协议.md` §1.2、`docs/分支规划与文件归属.md`）。
- 新增素材 → 提交到 `feature/ui` → PR 合入 `dev`，勿直接改 `main`。
- 素材更新时同步更新 `manifest.json` 与本 README，保持索引与文件一致。
