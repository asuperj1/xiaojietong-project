"""校捷通 FastAPI 应用入口。"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.core.config import settings
from app.core.response import BizError
from app.routers import (
    admin,
    agent,
    auth,
    chat,
    forum,
    health,
    job,
    library,
    life,
    map_api,
    secondhand,
    upload,
    user,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化 C++ 连接池（若扩展已编译）
    from app.db import cpp_bridge

    if cpp_bridge.available():
        try:
            cpp_bridge.init_db(
                host=settings.db_host,
                port=settings.db_port,
                user=settings.db_user,
                password=settings.db_password,
                dbname=settings.db_name,
                min_conn=settings.db_min_conn,
                max_conn=settings.db_max_conn,
            )
            logging.getLogger("uvicorn").info(
                "✅ jt_db 连接池已初始化 (%s:%s/%s)",
                settings.db_host, settings.db_port, settings.db_name,
            )
        except Exception as exc:  # 连接失败不阻塞启动，由 health 接口暴露状态
            logging.getLogger("uvicorn").warning("⚠️  jt_db 连接池初始化失败: %s", exc)
    else:
        logging.getLogger("uvicorn").warning(
            "⚠️  jt_db C++ 扩展未编译，数据库能力不可用。请构建 db/cpp_driver。"
        )
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="基于轻量化大模型与 RAG + AI Agent 的校园智能服务平台",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境收紧
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(BizError)
async def biz_error_handler(request: Request, exc: BizError):
    return JSONResponse(
        status_code=exc.http_status,
        content={"code": exc.code, "message": exc.message, "data": {}},
    )


# 挂载业务路由（前缀 /api/v1）
_api = settings.api_prefix
for r in (
    auth.router, user.router, chat.router, agent.router,
    library.router, secondhand.router, job.router, forum.router,
    map_api.router, life.router, admin.router, upload.router, health.router,
):
    app.include_router(r, prefix=_api)

# 静态资源（上传图片）
_static_dir = Path(__file__).resolve().parent.parent / "uploads"
app.mount("/static/uploads", StaticFiles(directory=_static_dir, check_dir=False), name="uploads")
