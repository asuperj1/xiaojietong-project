# 校捷通 · Copilot 开发指令

> 本文件会被 GitHub Copilot 自动加载。请严格遵循以下约束生成/修改代码。

## 项目一句话

校园一站式智能服务平台（微信小程序 + FastAPI 后端 + C++ 高性能数据层 + MySQL + 轻量大模型 RAG/Agent）。仓库根：`xiaojietong-project/`。

## 输出语言

- 代码注释、文档、接口消息一律使用**简体中文**。
- 代码标识符（变量/函数/表名）用英文。

## 技术栈速览

| 层 | 技术 | 位置 |
|---|---|---|
| 前端 | 微信小程序原生 (WXML/WXSS/JS) | `miniprogram/` |
| 后端 | Python 3.10+ / FastAPI / Uvicorn / pyjwt | `backend/` |
| 数据层 | **C++17 + MySQL C API + pybind11**（模块名 `jt_db`） | `db/cpp_driver/` |
| 数据库 | MySQL 8 (utf8mb4)，42 张表 | `db/sql/*.sql` |
| AI | Ollama(GGUF) + RAG + Agent(Function Call) | `backend/app/services/`、`ai/` |

## 架构铁律（改代码前必读）

1. **所有数据库访问必须走 C++ 数据层**（`from app.db import cpp_bridge`），禁止 Python 直连 MySQL、禁止 SQLAlchemy 承担主数据通道。
   - 业务查询优先用已绑定 DAO：`cpp_bridge.user_dao()/library_dao()/forum_dao()/secondhand_dao()/job_dao()/life_dao()`
   - 无对应 DAO 时用 `cpp_bridge.query(sql, params)` / `cpp_bridge.execute(sql, params)`，SQL 一律 `?` 占位符防注入。
2. **事务**：`with cpp_bridge.begin():` 块内所有操作走同一连接。**正常退出=commit，回滚需显式 `tx.rollback()` 或抛异常**。
3. **统一响应体**：`{"code":0,"message":"ok","data":...}`。成功用 `ok(data)`；业务错误抛 `BizError(code, msg)`（错误码：100x参数/200x认证/300x业务冲突/500x服务端）。
4. **JSON 列写入必须 `json.dumps(...)`**，绝不能 `str(dict)`（会写入非法 JSON 导致 MySQL 报错）。
5. 认证：JWT Bearer，用 `Depends(get_current_user)` / `get_current_admin`。
6. C++ 侧新增 DAO 必须：头文件放 `include/jt_db/dao/`，实现放 `src/dao/`，通过 `DbSession::current()` 取连接，并在 `CMakeLists.txt` 的 `jt_db_core` 加源文件、在 `pybind/pybind_wrapper.cpp` 绑定，最后跑 `test_all_dao.py`。

## 编码规范

- **Python**：FastAPI + pydantic；路由请求体用 `class XxxIn(BaseModel)`；分页统一 `page/size`；中文注释。
- **C++**：C++17；连接/事务用 RAII；预处理语句防注入；MSVC 已开 `/utf-8`；不要改表结构 DDL（SQL 归 `db/sql/`，由成员4 负责）。
- **SQL**：逻辑外键（注释 `-> 表.列`，不建物理 FK）；软删除 `is_deleted`；JSON 存可变结构；统一 `created_at/updated_at`。

## 常用命令（Windows Git Bash）

```bash
# 构建 C++ 数据层（改了 C++ 必须重编）
cmake --build db/cpp_driver/build --config Release --target jt_db

# 跑 C++ 数据层测试（需数据库）
cd db/cpp_driver/test && XJT_DB_PORT=3307 XJT_DB_PASSWORD=jhq000000 python test_all_dao.py

# 启动后端
cd backend && XJT_DB_PORT=3307 XJT_DB_PASSWORD=jhq000000 python -m uvicorn app.main:app --reload --port 8000
# Swagger: http://127.0.0.1:8000/docs

# 导入数据库脚本（utf8mb4 必须！）
mysql -u root -P 3307 -p --default-character-set=utf8mb4 < db/sql/00_database.sql
```

## 待办（按优先级，Copilot 应优先协助）

- [ ] **AI 对话接入真实模型**：`services/model_client.py` 现为 Ollama 客户端但模型未就绪（降级占位）。加载微调模型后即可用。
- [x] **RAG 向量化（框架已落地）**：`services/rag.py` 已接 bge-m3(Ollama `/api/embed`) + ChromaDB 向量检索（缺依赖自动降级 numpy；模型未就绪降级关键词分词匹配）。Ollama 加载 bge-m3 后执行 `cd backend && python ../ai/rag/build_index.py --force` 全量建索引即可生效。
- [ ] **Agent 真实编排**：`routers/agent.py` 意图识别为关键词规则占位，可换模型 Function Call。
- [ ] 前端小程序按 `docs/api.md` 对接。
- [ ] 生产部署（Docker + HTTPS），见 `deploy/`。
- [ ] 新增业务 DAO（如对话历史、通知个性化推荐）时遵循上面 DAO 铁律。

## 文档索引

- `docs/architecture.md` — 系统架构与技术决策（ADR）
- `docs/api.md` — 接口契约 v1.0（前后端对齐基线）
- `docs/DEVELOPMENT.md` — 完整开发文档与工作量
- `docs/datagrip.md` — 数据库连接查看
- `backend/README.md` / `db/cpp_driver/README.md` — 各层说明
