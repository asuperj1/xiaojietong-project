"""健康检查：暴露服务与 C++ 连接池状态。"""

from fastapi import APIRouter

from app.db import cpp_bridge

router = APIRouter()


@router.get("/health")
def health():
    db_status = "not_ready"
    if cpp_bridge.pool_ready():
        try:
            db_status = "ok" if cpp_bridge.ping() else "error"
        except RuntimeError:
            db_status = "error"
    return {
        "status": "ok",
        "db": db_status,
        "cpp_ext": cpp_bridge.available(),
        "pool": cpp_bridge.pool_stats(),
    }
