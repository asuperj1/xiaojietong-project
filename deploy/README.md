# deploy —— 部署与运维

规划文件（待第 14-15 周落实）：

- `docker-compose.yml`：nginx + api + celery + redis + mysql + ollama
- `nginx.conf`：反向代理、HTTPS、限流、静态资源
- `backend/Dockerfile`：编译 C++ 扩展(.so) → 安装依赖 → 启动
- `.env.example`：环境变量模板
- 服务器：阿里云/腾讯云轻量（2C4G+，学生优惠），Ubuntu 22.04，HTTPS 证书（微信小程序强制）。

> 当前目录为规划占位，容器化脚本在第 14 周前后补充。
