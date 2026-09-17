# backend —— Python 后端（FastAPI）

核心栈：`Python 3.10+` · `FastAPI` · `Uvicorn` · `pybind11 扩展 jt_db`（C++ 数据层）· `Celery + Redis`（异步任务）· `httpx`（调用 Ollama 推理）。

## 目录

```
backend/
├── app/
│   ├── main.py           # FastAPI 入口
│   ├── core/config.py    # 环境配置（DB/Ollama/Redis）
│   ├── db/cpp_bridge.py  # C++ jt_db 扩展桥接层（唯一入口）
│   ├── models/           # pydantic 请求/响应模型
│   ├── routers/          # 业务路由（health 已就位，其余按模块补）
│   └── services/         # 业务服务（RAG/Agent/多模态）
├── tests/
├── requirements.txt
└── Dockerfile            # 见 deploy/
```

## 运行（依赖 C++ 扩展已编译）

```bash
pip install -r requirements.txt pyjwt python-multipart
# 1) 编译 C++ 扩展 → backend/app/db/native/jt_db.pyd（见 db/cpp_driver/README.md）
# 2) 配置环境变量（本机 MySQL 在 3307）
set XJT_DB_PORT=3307
set XJT_DB_PASSWORD=<你的数据库密码>
set XJT_DB_NAME=xiaojietong
# 3) 启动
uvicorn app.main:app --reload --port 8000
# 访问 http://127.0.0.1:8000/docs（Swagger UI，自动生成接口文档）
```

## 接口实现状态（对照 docs/api.md v1.0）

| 模块 | 路由文件 | 状态 |
|---|---|---|
| 认证/用户 | `routers/auth.py` `user.py` | ✅ 微信登录(JWT)/资料/标签 |
| AI 助手 | `routers/chat.py` | ✅ SSE 流式（模型/RAG 接入点已留，Ollama 未就绪时降级占位） |
| Agent | `routers/agent.py` | ✅ 任务/提醒（意图规则占位，可换模型 Function Call） |
| 图书馆 | `routers/library.py` | ✅ 空教室/座位/预约(事务冲突校验)/拥挤度 |
| 二手 | `routers/secondhand.py` | ✅ 物品/求购/匹配/订单 |
| 兼职 | `routers/job.py` | ✅ 岗位/投递/可信度 |
| 论坛 | `routers/forum.py` | ✅ 帖子/评论/点赞/举报/热点 |
| 地图 | `routers/map_api.py` | ✅ POI/周边(haversine)/导航/建筑 |
| 生活 | `routers/life.py` | ✅ 商家/菜单/外卖/通知 |
| 管理 | `routers/admin.py` | ✅ 指标/知识库/论坛审核/语料 |
| 上传 | `routers/upload.py` | ✅ 图片上传(本地 uploads/) |

## 结构化抽取模型（C36：统一入口 + 与对话模型隔离）

改造前后端有**三处**各自手写 Ollama `/api/chat` 调用（`model_client.py` 对话、
`agent_executor.py` 工具规划、`secondhand_ai.py` 描述抽取），共用同一套
`ollama_base_url` / `ollama_model`。C36 把**抽取**这一路收成一个入口
`app/services/extract_model.py`，并给了一组独立开关：

| 配置 | 作用 |
|---|---|
| `XJT_EXTRACT_BACKEND` | `ollama`（默认）/ `http`（自建抽取服务）/ `none`（明确关闭） |
| `XJT_EXTRACT_BASE_URL` | 留空沿用 `ollama_base_url`；**指向另一台 Ollama 才是真隔离** |
| `XJT_EXTRACT_MODEL` | 留空沿用 `ollama_model`；**可换抽取专用小模型** |
| `XJT_EXTRACT_HTTP_URL` | `http` 模式的服务地址（契约见下） |
| `XJT_EXTRACT_MAX_CHARS` | 输入截断上限（默认 4000，按字符）；截断会标记，不静默 |
| `XJT_EXTRACT_MAX_CONCURRENCY` | 同时在飞的抽取请求上限（默认 2；`0` = 不限） |
| `XJT_EXTRACT_KEEP_ALIVE` | 传给 Ollama 的 `keep_alive`（如 `5m`、`0` = 用完即卸）；空 = Ollama 默认 |

隔离的意义：抽取是「批量、短输出、要确定性」的负载，与对话（长输出、流式、
占并发）放在同一模型/同一实例上会互相挤显存与队列，且换抽取模型不该影响线上对话。

> ⚠️ **「换了模型名」不等于「隔离了」**：只要 `XJT_EXTRACT_BASE_URL` 还指向同一台 Ollama，
> 两个模型就共享同一个进程的显存与请求队列（Ollama 按需 load/evict）。
> 真隔离是**换地址**；默认值（留空 = 沿用 `ollama_*`）就是「隔离未生效」。
> `/health/detail` 的 `extract.isolation` 会如实报告（`isolated` 仅在换地址时为 `true`）。
> 同机部署想缓解挤占，先用 `XJT_EXTRACT_MAX_CONCURRENCY` + `XJT_EXTRACT_KEEP_ALIVE`。

```python
from app.services.extract_model import extract_json, ExtractFailure, ExtractUnavailable

result = await extract_json(text, instruction="抽取时间与地点，只输出 JSON",
                            schema_hint='{"time": str, "place": str}')
result.data          # dict
result.truncated     # 输入被截断过吗（不静默丢内容）
```

**失败语义（不静默降级）**：只可能拿到 `ExtractResult`，或抛 `ExtractUnavailable`（用不了）
/ `ExtractFailure`（这次抽失败）—— **绝不**返回 `{}` 冒充抽取成功。
调用方若愿意降级（如 `secondhand_ai` 回退模板文案 + 统计定价），由调用方**显式**决定，
并在响应里把 `source` 标出来，降级对用户可见。

`http` 模式契约：`POST` JSON `{"text", "instruction", "schema"}` → 响应 JSON **对象**
（顶层即结果，或 `{"data": {...}}` 包一层；`data` 存在但**不是对象** → `ExtractFailure`，
不把整个信封当结果返回）。回环地址会自动绕过系统代理
（见 `app/core/net.py`：Windows 注册表代理会把 `127.0.0.1:11434` 也接走并回 502）。

**输入过长怎么办**：`extract_json` 的 `XJT_EXTRACT_MAX_CHARS` 是**按字符**的通用护栏，
会从任意位置切断 —— 所以「整份 JSON 当正文」的调用方（`secondhand_ai`）必须先在**调用方**
裁掉无上限字段（备注/标题），保证送进模型的始终是合法 JSON，并把 `truncated` 透出去
（`POST /secondhand/items/ai-describe` 的响应字段 `model_truncated`）。

**可观测**：`GET /health/detail` 新增 `extract` 段（backend/地址/模型/`available`/`reason`/
`ready`/`isolation`/`max_concurrency`），运维不必去猜「抽取到底通没通、隔离到底生效没」。

> 验证：`tests/test_extract_model.py`（56 例，不需 DB、不需真 Ollama）真起 uvicorn 桩服务走真 HTTP，
> 并带**反向对照**：对话模型换成另一个时抽取请求里必须出现抽取模型名；非回环地址不得关闭代理；
> 坏输出必须报错而不能返回 `{}`；`max_concurrency=0` 时不得打闸；不超限时不得裁剪。

## 冒烟测试

```bash
# 登录拿 token（开发模式 code 直接映射 openid）
curl -X POST http://127.0.0.1:8000/api/v1/auth/wechat-login \
     -H "Content-Type: application/json" -d '{"code":"test1"}'
# 带 token 访问受保护接口
curl http://127.0.0.1:8000/api/v1/user/me -H "Authorization: Bearer <token>"
```

> ⚠️ Windows Git Bash 里 curl -d 传中文可能编码异常，建议用 Python/Postman 调试 POST 接口。
