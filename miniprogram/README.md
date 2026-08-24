# miniprogram —— 微信小程序前端（成员1 负责）

规划目录结构：

```
miniprogram/
├── app.js / app.json / app.wxss        # 全局配置、路由、TabBar
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
├── utils/
└── static/
```

约定：
- 统一请求封装 `services/request.js`，携带 token、统一错误码处理、SSE 流式解析。
- 对话流式：`POST /api/v1/chat/send`（SSE）。
- 接口契约见 `docs/api.md`（待成员4 补全）。

> 本目录当前仅规划骨架，页面开发由前端成员在微信开发者工具中创建。
