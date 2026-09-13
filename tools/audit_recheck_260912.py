"""2026-09-12 审计结论逐项复核（静态断言版）。

用途：把 `docs/项目审计报告20260912序1.md` 中的关键条目转成**可自动判定的断言**，
回答一个问题：**当前工作树里的代码，这些问题是「仍存在」还是「已修复」？**

运行：
    E:/miniconda3/python.exe -X utf8 tools/audit_recheck_260912.py

判定语义：
- `bad`  模式命中 ⇒ **问题特征仍在**（未修）
- `good` 模式命中 ⇒ **修复特征已出现**（已修）
- 只声明 `bad` 而全部未命中 ⇒ 代码已变，标 `须人工`（需看是否有别的改法）

基线（可用 --base 参数覆盖）：
    后端 dev@7a3260c ｜ 前端 feature/frontend@ca9db97（2026-09-12 同步后）
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- 路径 ----

TOOLS = Path(__file__).resolve().parent
BACKEND_ROOT = TOOLS.parent                 # .../xiaojietong-project
REPO_ROOT = BACKEND_ROOT                    # 同一目录（后端仓库根）
FRONTEND_ROOT = BACKEND_ROOT.parent / "xjt-frontend"   # 前端为同仓库另一检出


def B(rel: str) -> Path:
    """后端/仓库内文件。"""
    return REPO_ROOT / rel


def F(rel: str) -> Path:
    """前端（xjt-frontend 检出）内文件。"""
    return FRONTEND_ROOT / rel


# ------------------------------------------------------------ 检查定义 ----

@dataclass
class Check:
    cid: str                    # 报告编号
    title: str
    hits: list[tuple[Path, str, str]] = field(default_factory=list)
    # hits: [(文件, 正则, 'bad'|'good')]
    note: str = ""


CHECKS: list[Check] = [
    # ---------------- P0 ----------------
    Check("SEC-17", "登录限流可被伪造 X-Forwarded-For 绕过",
          [(B("backend/app/core/ratelimit.py"),
            r'request\.headers\.get\(\s*["\']x-forwarded-for', "bad"),
           (B("backend/app/core/ratelimit.py"), r'trusted_proxies|trusted_proxy', "good")]),
    Check("SEC-18", "默认 JWT 密钥入库且 env 默认 dev",
          [(B("backend/app/core/config.py"), r'_DEFAULT_JWT_SECRET\s*=\s*["\']xjt-dev-secret', "bad"),
           (B("backend/app/core/config.py"), r'jwt_secret:\s*str\s*=\s*_DEFAULT_JWT_SECRET', "bad")]),
    Check("DATA-01", "二手读路径不过滤 audit_status（审核可绕过）",
          [(B("db/cpp_driver/src/dao/secondhand_dao.cpp"),
            r'page_items[\s\S]{0,900}?audit_status', "good"),
           (B("db/cpp_driver/src/dao/secondhand_dao.cpp"), r'AND i\.status = 0 ', "bad")]),
    Check("DATA-02", "Agent 工具链发布二手不过审",
          [(B("backend/app/services/agent_executor.py"),
            r'_exec_post_secondhand[\s\S]{0,700}?audit_content', "good"),
           (B("backend/app/services/agent_executor.py"), r'secondhand_dao\(\)\.publish', "bad")]),
    Check("FRONT-01", "前端 baseURL 写死 127.0.0.1 明文 http",
          [(F("miniprogram/services/request.js"), r"BASE_URL\s*=\s*'http://127\.0\.0\.1", "bad"),
           (F("miniprogram/services/request.js"), r"envVersion|getAccountInfoSync", "good")]),
    Check("FRONT-02", "5 个页面分类 tab 用 e.detail.value（bindtap）",
          [(F("miniprogram/pages/forum/forum.js"), r'catIndex:\s*Number\(e\.detail\.value\)', "bad"),
           (F("miniprogram/pages/job/index.js"), r'typeIndex:\s*Number\(e\.detail\.value\)', "bad"),
           (F("miniprogram/pages/life/index.js"), r'catIndex:\s*Number\(e\.detail\.value\)', "bad"),
           (F("miniprogram/pages/map/index.js"), r'catIndex:\s*Number\(e\.detail\.value\)', "bad"),
           (F("miniprogram/pages/secondhand/index.js"), r'catIndex:\s*Number\(e\.detail\.value\)', "bad"),
           (F("miniprogram/pages/forum/forum.js"), r'currentTarget\.dataset\.index', "good"),
           (F("miniprogram/pages/job/index.js"), r'currentTarget\.dataset\.index', "good"),
           (F("miniprogram/pages/life/index.js"), r'currentTarget\.dataset\.index', "good"),
           (F("miniprogram/pages/map/index.js"), r'currentTarget\.dataset\.index', "good"),
           (F("miniprogram/pages/secondhand/index.js"), r'currentTarget\.dataset\.index', "good")]),
    # ---------------- P1 · 安全 ----------------
    Check("SEC-19", "/health/detail、/health/selfcheck 免鉴权",
          [(B("backend/app/routers/health.py"),
            r'def health_detail\([\s\S]{0,300}?Depends\(get_current_admin\)', "good")]),
    Check("SEC-20", "缺 RequestValidationError / 通用 Exception 处理器",
          [(B("backend/app/main.py"), r'RequestValidationError', "good"),
           (B("backend/app/main.py"), r'exception_handler\(Exception\)', "good")]),
    Check("SEC-21", "refresh 不校验账号状态 + logout 空操作",
          [(B("backend/app/routers/auth.py"),
            r'def refresh\([\s\S]{0,600}?status', "good"),
           (B("backend/app/routers/auth.py"),
            r'def logout\(\):\s*\n\s*(?:#[^\n]*\n\s*)*return ok\(\)', "bad")]),
    Check("SEC-22", "前端未处理 2003（被禁用账号卡死）",
          [(F("miniprogram/services/request.js"), r'code === 2001 \|\| code === 2002', "bad"),
           (F("miniprogram/services/request.js"), r'AUTH_FAILURE_CODES|code === 2003', "good")]),
    Check("SEC-23", "httpx 未隔离环境代理 / 未检查状态码",
          [(B("backend/app/routers/auth.py"), r'httpx\.AsyncClient\(timeout=10\)', "bad"),
           (B("backend/app/routers/auth.py"), r'trust_env=False', "good")]),
    # ---------------- P1 · 数据一致性 ----------------
    Check("DATA-03", "论坛详情不过滤 audit_status",
          [(B("backend/app/routers/forum.py"),
            r'def topic_detail[\s\S]{0,900}?audit_status', "good")]),
    Check("DATA-04", "岗位详情不过滤 status/黑名单",
          [(B("backend/app/routers/job.py"),
            r'def job_detail[\s\S]{0,600}?is_blacklisted', "good")]),
    Check("DATA-05", "收藏链路不过滤审核状态",
          [(B("db/cpp_driver/src/dao/favorite_dao.cpp"),
            r'target_exists[\s\S]{0,500}?audit_status', "good")]),
    Check("DATA-06", "分页 total 用本页条数（9 处）",
          [(B("backend/app/routers/forum.py"), r'paged\(items, len\(items\)', "bad"),
           (B("backend/app/routers/job.py"), r'paged\(rows, len\(rows\)', "bad"),
           (B("backend/app/routers/life.py"), r'paged\(rows, len\(rows\)', "bad"),
           (B("backend/app/routers/admin.py"), r'paged\(rows, len\(rows\)', "bad"),
           (B("backend/app/routers/secondhand.py"), r'paged\(items, len\(items\)', "bad")]),
    Check("DATA-07", "外卖订单收货四字段被丢弃",
          [(B("backend/app/routers/life.py"),
            r'create_order\(\s*\n?\s*int\(user\["id"\]\), body\.merchant_id, items_json[\s\S]{0,80}?\)', "bad")]),
    Check("DATA-08", "外卖数量可为负 / 跨商家 / 0 元计价",
          [(B("backend/app/routers/life.py"), r'num:\s*int\s*=\s*1\b', "bad"),
           (B("backend/app/routers/life.py"), r'price_map\.get\(it\.id, 0\)', "bad"),
           (B("backend/app/routers/life.py"), r'merchant_id = \? AND is_on_sale', "good")]),
    Check("DATA-09", "secondhand.py 使用未导入的 err_server",
          [(B("backend/app/routers/secondhand.py"),
            r'from app\.core\.response import[^\n]*err_server', "good"),
           (B("backend/app/routers/secondhand.py"), r'raise err_server\(', "bad")]),
    Check("DATA-10", "二手详情接口缺失（前端靠 URL 传参）",
          [(B("backend/app/routers/secondhand.py"),
            r'@router\.get\(\s*["\']/items/\{item_id\}["\']', "good")]),
    # ---------------- P1 · 并发/性能 ----------------
    Check("CON-09", "async def 端点内直接调用同步 DB",
          [(B("backend/app/routers/chat.py"), r'async def chat_send[\s\S]{0,2500}?cpp_bridge\.(query|execute)', "bad")]),
    Check("CAC-15", "SSE 无异常兜底 / 无禁用缓冲响应头",
          [(B("backend/app/routers/chat.py"), r'event: error', "good"),
           (B("backend/app/routers/chat.py"), r'X-Accel-Buffering', "good"),
           (B("backend/app/routers/chat.py"), r'media_type="text/event-stream"', "bad")]),
    Check("CAC-16", "类型信息丢失：status 靠字符串比较",
          [(B("backend/app/core/deps.py"), r'user\.get\("status"\)\s*==\s*"1"', "bad"),
           (B("backend/app/core/deps.py"), r'int\(user\.get\("status"\)', "good")]),
    Check("CAC-17", "(? = '' OR col = ?) 致分类索引失效",
          [(B("db/cpp_driver/src/dao/secondhand_dao.cpp"), r"\(\? = '' OR i\.category = \?\)", "bad"),
           (B("db/cpp_driver/src/dao/forum_dao.cpp"), r"\(\? = '' OR t\.category = \?\)", "bad")]),
    Check("CAC-18", "限流单进程内存 + BaseHTTPMiddleware",
          [(B("backend/app/core/ratelimit.py"), r'class RateLimitMiddleware\(BaseHTTPMiddleware\)', "bad"),
           (B("backend/app/core/ratelimit.py"), r'^\s*(?:import redis|from redis)', "good")]),
    # ---------------- P1 · 前端 ----------------
    Check("FRONT-03", "refresh_token 只存不用",
          [(F("miniprogram/services/request.js"), r'/auth/refresh|refreshToken\(', "good"),
           (F("miniprogram/pages/auth/login.js"), r"setStorageSync\('refresh_token'", "bad")]),
    Check("FRONT-04", "空 catch 静默吞错（含座位 3001）",
          [(F("miniprogram/pages/library/seat.js"), r'err\.code === 3001|code === 3001', "good"),
           (F("miniprogram/pages/library/myReserve.js"), r'\.catch\(\(\)\s*=>\s*\{\}\)', "bad")]),
    Check("FRONT-05", "二手无图片上传",
          [(F("miniprogram/pages/secondhand/publish.js"), r'chooseMedia', "good"),
           (F("miniprogram/pages/secondhand/publish.js"), r'/upload/image', "good")]),
    Check("FRONT-06", "AI 页无停止生成 / 无历史会话",
          [(F("miniprogram/pages/chat/chat.js"), r'onStop\(', "good"),
           (F("miniprogram/pages/chat/history.js"), r'chat/conversations', "good")]),
    Check("FRONT-12", "AI 无反馈入口 / 发帖无审核状态轮询",
          [(F("miniprogram/pages/chat/chat.js"), r'chat/feedback', "good"),
           (F("miniprogram/pages/forum/create.js"), r'audit-status', "good")]),
    Check("FRONT-07", "地图/图书馆写死 + POI 无种子数据",
          [(F("miniprogram/pages/library/index.js"), r"request\(\s*['\"]/map/building/1['\"]", "bad"),
           (B("db/sql/99_init_data.sql"), r'INSERT INTO `?poi`?', "good")]),
    # ---------------- P1 · 工程 ----------------
    Check("ENG-01", "无 CI / 无容器化文件",
          [(REPO_ROOT / ".github/workflows", r".", "good"),
           (B("deploy/docker-compose.yml"), r".", "good"),
           (B("deploy/nginx.conf"), r".", "good"),
           (B("backend/Dockerfile"), r".", "good")]),
    Check("ENG-02", "nginx 缺 client_max_body_size",
          [(B("docs/前端上线-域名与HTTPS方案.md"), r'client_max_body_size', "good")]),
    Check("ENG-03", "依赖声明不符（redis/celery 零使用；pymysql 未声明）",
          [(B("backend/requirements.txt"), r'^(redis|celery|SQLAlchemy|alembic)', "bad"),
           (B("db/cpp_driver/requirements-test.txt"), r'pymysql', "good")]),
    Check("ENG-04", "文档与实现漂移（表数/DAO 数/示例路径）",
          [(B(".github/copilot-instructions.md"), r'42 张表', "bad"),
           (B(".github/copilot-instructions.md"), r'45 张表', "good")]),
    # ---------------- P2 · 可自动化样本 ----------------
    Check("DATA-11", "座位预约：时间字典序比较 + 校验缺失",
          [(B("backend/app/routers/library.py"), r'body\.begin_time >= body\.end_time', "bad"),
           (B("backend/app/routers/library.py"), r'datetime\.time|Field\(pattern', "good")]),
    Check("DATA-13", "写操作不判 affected（假成功）",
          [(B("backend/app/routers/agent.py"), r'UPDATE agent_task SET status = 4[\s\S]{0,200}?affected', "good")]),
    Check("DATA-19", "Agent 模型超时 120s 阻塞 + 无幂等",
          [(B("backend/app/services/agent_executor.py"), r'_MODEL_TIMEOUT\s*=\s*120\.0', "bad")]),
    Check("CAC-19", "embedder 缓存无界 + 每请求新建 AsyncClient",
          [(B("backend/app/services/embedder.py"), r'self\._cache:\s*dict\[str, list\[float\]\]\s*=\s*\{\}', "bad"),
           (B("backend/app/services/embedder.py"), r'AsyncClient\(timeout=httpx\.Timeout\(self\.timeout\)\)', "bad")]),
    Check("CAC-22", "上传文件名可覆盖 + async 内同步写盘",
          [(B("backend/app/routers/upload.py"), r'path\.write_bytes\(data\)', "bad"),
           (B("backend/app/routers/upload.py"), r'to_thread\(.*write_bytes', "good")]),
    Check("CAC-23", "audit_word.hit_count 逐词同步更新（写热点）",
          [(B("backend/app/services/audit.py"), r'UPDATE audit_word SET hit_count = hit_count \+ 1', "bad")]),
    Check("ENG-08", "C++ stoi/stoll/stod 无异常保护",
          [(B("db/cpp_driver/src/dao/library_dao.cpp"), r'std::stoi\(period\)', "bad"),
           (B("db/cpp_driver/src/dao/library_dao.cpp"), r'try\s*\{[\s\S]{0,200}?std::stoi', "good")]),
    Check("ENG-10", "openid 随登录响应返回前端",
          [(B("backend/app/routers/auth.py"), r'"openid":\s*u\.get\("openid"', "bad")]),
]


# ---------------------------------------------------------------- 主流程 ----

def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        return ""
    except IsADirectoryError:
        return "<dir>"


def locate(text: str, pattern: str) -> tuple[int, str]:
    """返回 (行号, 该行内容)。"""
    m = re.search(pattern, text, flags=re.MULTILINE)
    if not m:
        return 0, ""
    line_no = text[: m.start()].count("\n") + 1
    line = text.splitlines()[line_no - 1].strip() if line_no <= len(text.splitlines()) else ""
    return line_no, line


def evaluate(chk: Check) -> tuple[str, list[str]]:
    """返回 (状态, 证据行列表)。

    判定语义：
    - `bad` 模式命中 ⇒ 问题特征仍在
    - `good` 模式命中 ⇒ 已出现修复特征
    - 两者皆未命中而只声明了 `good` ⇒ **修复尚未发生（仍存在）**
    """
    ev: list[str] = []
    saw_bad = False
    saw_good = False
    for path, pattern, mode in chk.hits:
        rel = _rel(path)
        if path.is_dir():
            text = "<dir>" if path.exists() else ""
        else:
            text = read(path)
        if text == "":
            ev.append(f"{rel} → 不存在")
            if mode == "good":
                saw_bad = True          # 期望的修复载体不存在 → 修复未发生
            continue
        line_no, line = locate(text, pattern)
        if line_no:
            ev.append(f"{rel}:{line_no} {line[:90]}")
            if mode == "bad":
                saw_bad = True
            else:
                saw_good = True
        else:
            ev.append(f"{rel} → 未命中[{mode}]")

    if saw_bad and saw_good:
        return "部分修复", ev
    if saw_good:
        return "已修复", ev
    if saw_bad:
        return "仍存在", ev
    # 只声明了 bad 且全部未命中：代码已改，但需人工确认改法是否真的修好了
    if any(mode == "bad" for _, _, mode in chk.hits):
        return "须人工", ev
    return "仍存在", ev


def _rel(path: Path) -> str:
    for root, tag in ((REPO_ROOT, ""), (FRONTEND_ROOT, "xjt-frontend/")):
        try:
            return tag + str(path.relative_to(root)).replace("\\", "/")
        except ValueError:
            continue
    return str(path)


ICON = {"仍存在": "❌", "已修复": "✅", "部分修复": "⚠️", "须人工": "❓"}


def main() -> int:
    print("=" * 96)
    print("审计结论逐项复核 · 基线：dev@7a3260c（后端） / feature/frontend@534be12（前端）")
    print(f"后端根：{REPO_ROOT}")
    print(f"前端根：{FRONTEND_ROOT}")
    print("=" * 96)

    stats = {"仍存在": 0, "已修复": 0, "部分修复": 0, "须人工": 0}
    rows: list[tuple[str, str, str, list[str]]] = []
    for chk in CHECKS:
        status, ev = evaluate(chk)
        stats[status] = stats.get(status, 0) + 1
        rows.append((chk.cid, status, chk.title, ev))

    for cid, status, title, ev in rows:
        print(f"\n{ICON.get(status, '?')} [{cid}] {title} —— {status}")
        for line in ev:
            print(f"    └ {line}")

    print("\n" + "=" * 96)
    print("汇总")
    print("=" * 96)
    total = len(CHECKS)
    print(f"  检查项：{total}")
    for key in ("仍存在", "部分修复", "已修复", "须人工"):
        if stats.get(key):
            print(f"  {ICON[key]} {key}: {stats[key]}")
    print(f"\n  未修复（仍存在 + 部分修复）= {stats['仍存在'] + stats['部分修复']} / {total}")
    print("  注：本脚本为静态断言级判定，判定语义见文件头 docstring。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
