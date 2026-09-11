# deploy —— 部署与运维

规划文件（待第 14-15 周落实）：

- `docker-compose.yml`：nginx + api + celery + redis + mysql + ollama
- `nginx.conf`：反向代理、HTTPS、限流、静态资源
- `backend/Dockerfile`：编译 C++ 扩展(.so) → 安装依赖 → 启动
- `.env.example`：环境变量模板
- 服务器：阿里云/腾讯云轻量（2C4G+，学生优惠），Ubuntu 22.04，HTTPS 证书（微信小程序强制）。

> 当前目录为规划占位，容器化脚本在第 14 周前后补充。

---

## ⚠️ 前置：域名备案（时间瓶颈，建议尽早启动）

微信小程序要求请求域名**必须 https 且已 ICP 备案**，而**备案需 7~20 个工作日**，
是整条上线链路中**唯一需要等待**的环节。**建议与开发并行推进，不要等到要发布才启动。**

详细步骤（含 Nginx 配置、证书签发、小程序后台域名配置、常见坑）见：

> **📘 [`docs/前端上线-域名与HTTPS方案.md`](../docs/前端上线-域名与HTTPS方案.md)**

其中两个最容易踩的坑（已写入该文档）：

1. **`proxy_buffering off`** —— 不关的话 `/chat/send` 的 SSE 逐字输出会失效
2. **`X-Forwarded-For` 透传** —— 不透传会导致后端限流全部按 Nginx 的 IP 计数
