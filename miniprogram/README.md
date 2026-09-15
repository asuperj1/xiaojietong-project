# miniprogram —— 微信小程序前端（成员1 负责）

规划目录结构：

```
miniprogram/
├── app.js / app.json / app.wxss        # 全局配置、路由、TabBar
├── custom-tab-bar/      # 自定义 TabBar（F12，目录名由微信框架固定：必须叫 custom-tab-bar）
├── pages/
│   ├── chat/            # AI 助手对话页（SSE 流式）
│   ├── agent/           # Agent 任务中心
│   ├── library/         # 图书馆预约 / 空教室
│   ├── secondhand/      # 二手
│   ├── job/             # 兼职实习
│   ├── forum/           # 论坛
│   ├── map/             # 校园地图
│   ├── life/            # 生活服务
│   └── user/            # 我的
├── components/          # 复用组件
├── services/            # API 封装（request.js）
├── utils/               # format.js / glass.js（F10）/ tab-order.js + tabbar.js（F12）
└── static/
```

约定：
- 统一请求封装 `services/request.js`，携带 token、统一错误码处理、SSE 流式解析。
- 对话流式：`POST /api/v1/chat/send`（SSE）。
- 接口契约见 `docs/api.md`（待成员4 补全）。

## API 地址配置（FRONT-01）

API Base URL 由 `config/env.js` 的 `getBaseUrl()` 统一解析，`services/request.js` 的普通请求与 SSE 都走它，**不要在页面里硬编码后端地址**。

| 运行环境 | 取值 |
|---|---|
| 开发者工具（`envVersion=develop`） | 默认 `http://127.0.0.1:8000/api/v1`；可用 storage 覆盖 |
| 体验版（`trial`） | 同上：默认本机地址，可用 storage 覆盖 |
| 正式版（`release`） | 固定读 `config/env.js` 的 `RELEASE_BASE_URL`；**上线前必须配置**为已备案的 https 域名（未配置时 `getBaseUrl()` 直接抛出配置错误，fail fast，**不会回退到 127.0.0.1**） |

覆盖读取顺序：`storage[xjt_api_base_url]` → 环境默认值。地址会做最小规范化（去首尾空白、去掉末尾多余的 `/`）。

### 开发者工具（默认）

后端本机启动即可，无需任何额外配置；开发者工具需勾选「不校验合法域名」：

```bash
cd backend && python -m uvicorn app.main:app --reload --port 8000
```

### 真机调试

1. 电脑后端必须监听所有网卡（否则手机连不上）：
   ```bash
   cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
2. 手机与电脑必须在**可互访的同一局域网**（同一 Wi-Fi；注意电脑防火墙放行 8000 端口）。
3. 真机打开「远程调试」，在其控制台写入电脑的局域网地址：
   ```js
   wx.setStorageSync('xjt_api_base_url', 'http://<电脑局域网IP>:8000/api/v1')
   ```
4. 恢复默认（用回 `127.0.0.1`）：
   ```js
   wx.removeStorageSync('xjt_api_base_url')
   ```

> ⚠️ 不要把个人局域网 IP 提交进仓库（storage 只存在手机上，代码里保持占位符 `<电脑局域网IP>`）。

### release（正式版）

- 微信小程序只接受 **https**，且域名须已备案并在小程序后台配置 `request` 合法域名。
- 在上线前填写 `miniprogram/config/env.js` 的 `RELEASE_BASE_URL`（例如 `https://api.xxx.edu.cn/api/v1`）；格式校验/域名落地见 `docs/前端上线-域名与HTTPS方案.md`。
- release 不允许被 storage 覆盖，避免测试地址误带到线上。
- **未配置时 fail fast**：`release` 下调用 `getBaseUrl()` 会直接 `throw`（错误信息指向 `RELEASE_BASE_URL`），不会回退到 `DEFAULT_BASE_URL`/`127.0.0.1`——宁可发布前暴露，也不让正式包静默连本机地址。

> 本目录当前仅规划骨架，页面开发由前端成员在微信开发者工具中创建。
