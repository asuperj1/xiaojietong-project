"""管理端：指标 / 知识库 / 论坛审核 / 训练语料 / 通知调度。

契约：docs/api.md §11（需管理员角色）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.deps import get_current_admin
from app.core.response import err_param, err_server, ok, paged
from app.db import cpp_bridge
from app.services import knowledge
from app.services.parser import ParseError, finalize, parse_bytes

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/metrics")
def metrics(_admin: dict = Depends(get_current_admin)):
    stats = cpp_bridge.pool_stats()
    users = cpp_bridge.query("SELECT COUNT(*) AS c FROM user WHERE is_deleted = 0")
    topics = cpp_bridge.query("SELECT COUNT(*) AS c FROM topic WHERE is_deleted = 0")
    return ok(
        {
            "users": int(users[0]["c"]),
            "topics": int(topics[0]["c"]),
            "db": {"pool": stats},
        }
    )


class KnowledgeIn(BaseModel):
    """JSON 入库入参（B16 扩展：新增 index/dedup/overwrite/dry_run 开关）。

    兼容旧契约：只传 ``title`` + ``content`` 时行为与旧版一致（入库并向量化）。
    """

    title: str = ""
    category: str = ""
    content: str
    source_url: str = ""
    index: bool = True          # false=只入库不向量化（status=0 待向量化）
    dedup: bool = False         # 按 source_url 去重（B17 续传同口径）
    overwrite: bool = False     # 同来源内容变化时是否覆盖
    dry_run: bool = False       # 只解析不入库（预览）


def _as_bool(value: object, default: bool = False) -> bool:
    """表单字段转布尔（multipart 里全是字符串）。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


async def _ingest_json(request: Request) -> dict:
    """JSON 通道：直接入库一段文本。"""
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001 - 统一转契约错误（而非 422 detail）
        raise err_param(f"请求体不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise err_param("请求体必须是 JSON 对象")
    try:
        body = KnowledgeIn(**payload)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        raise err_param(f"参数错误：{first.get('loc')} {first.get('msg')}") from exc
    if not (body.content or "").strip():
        raise err_param("内容不能为空")

    if body.dry_run:
        doc = finalize(body.content, title=body.title, fmt="text", fallback_title="未命名文档")
        return {"dry_run": True, "channel": "json", **doc.to_dict(preview=300)}

    result = await knowledge.ingest_text(
        title=body.title,
        content=body.content,
        category=body.category,
        source_url=body.source_url,
        index=body.index,
        dedup=body.dedup,
        overwrite=body.overwrite,
    )
    result["channel"] = "json"
    return result


async def _ingest_upload(request: Request) -> dict:
    """文件通道（B16）：HTML / PDF / Markdown / txt → 解析 → 入库。"""
    form = await request.form()
    upload = form.get("file") or form.get("upload")
    if upload is None or not hasattr(upload, "read"):
        raise err_param("缺少文件字段 file（请用 multipart/form-data 上传）")
    # 文件名取值顺序：显式 filename 字段 > multipart 头里的文件名 > 占位名。
    # 之所以支持显式字段：部分客户端上传非 ASCII 文件名时 multipart 头会丢失
    # filename（实测 PowerShell 7.6 `-Form` 上传中文名文件即为空），此时靠
    # filename 表单字段或内容嗅探兜底（见 services/parser.detect_kind）。
    filename = (
        str(form.get("filename") or form.get("name") or "").strip()
        or (getattr(upload, "filename", "") or "").strip()
        or "upload"
    )
    data = await upload.read()
    if not data:
        raise err_param("上传文件为空（0 字节）")

    try:
        doc = parse_bytes(filename, data)
    except ParseError as exc:
        raise err_param(f"文档解析失败：{exc}") from exc

    dry_run = _as_bool(form.get("dry_run"), False)
    index = _as_bool(form.get("index"), True)
    dedup = _as_bool(form.get("dedup"), True)      # 文件通道默认按来源去重
    overwrite = _as_bool(form.get("overwrite"), False)
    title = str(form.get("title") or "").strip()
    category = str(form.get("category") or "").strip()
    source_url = str(form.get("source_url") or "").strip() or filename

    if dry_run:
        return {
            "dry_run": True,
            "channel": "file",
            "file": filename,
            **doc.to_dict(preview=300),
        }

    result = await knowledge.ingest_document(
        doc,
        title=title,
        category=category,
        source_url=source_url,
        index=index,
        dedup=dedup,
        overwrite=overwrite,
    )
    if result["action"] == "empty":
        raise err_param(
            "解析结果为空文本（疑似扫描件/图片版 PDF，需 OCR）；"
            f"解析器={doc.fmt}，告警={'；'.join(doc.warnings) or '无'}"
        )
    result.update({"channel": "file", "file": filename, "fmt": doc.fmt, "chars": doc.chars})
    return result


@router.post("/knowledge/ingest")
async def ingest(request: Request, _admin: dict = Depends(get_current_admin)):
    """知识入库（B16 升级：JSON 文本 + 文件上传双通道）。

    **通道 1 · JSON**（Content-Type: application/json）
      ``{ "title":"图书馆借阅规则", "category":"图书馆", "content":"...", "source_url":"..." }``

    **通道 2 · 文件**（Content-Type: multipart/form-data，B16 新增）
      字段：``file``（必填，支持 .html/.htm/.pdf/.md/.markdown/.txt）+ 可选
      ``title``/``category``/``source_url``/``index``/``dedup``/``overwrite``/``dry_run``

    响应（两种通道一致）：
      ``{ "doc_id":28, "action":"created", "status":"ok", "chunks":12, "title":"...",
          "fmt":"pdf", "chars":3210, "warnings":[] }``
      - action：created 新建 / updated 覆盖 / skipped 内容未变 / conflict 需人工确认；
      - status：ok 已向量化 / pending 待向量化（index=false）/ embed_failed 向量服务不可用；
      - dry_run=true 时只返回解析预览（title/fmt/chars/preview），不写库。
    """
    ctype = (request.headers.get("content-type") or "").lower()
    if ctype.startswith("multipart/form-data"):
        return ok(await _ingest_upload(request))
    return ok(await _ingest_json(request))


@router.get("/knowledge/docs")
def knowledge_docs(
    status: int | None = None,
    category: str = "",
    keyword: str = "",
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _admin: dict = Depends(get_current_admin),
):
    """知识库文档列表（B17 配套）：分页 + 真实 total（供导入进度核对）。"""
    items, total = knowledge.list_docs(
        page=page, size=size, status=status, category=category, keyword=keyword
    )
    return ok(paged(items, total, page, size))


@router.get("/knowledge/stats")
def knowledge_stats(_admin: dict = Depends(get_current_admin)):
    """知识库规模概览（B17 验收：≥120 篇硬指标可直接读 total）。"""
    return ok(knowledge.stats())


@router.delete("/knowledge/docs/{doc_id}")
def knowledge_delete(doc_id: int, _admin: dict = Depends(get_current_admin)):
    """删除知识文档（含分块与向量）。"""
    if not knowledge.delete_doc(doc_id):
        raise err_param(f"文档不存在：{doc_id}")
    return ok({"doc_id": doc_id, "deleted": True})


class KnowledgePurgeIn(BaseModel):
    """按来源前缀批量清理（B17 测试数据回滚用）。

    ``dry_run=true`` 只返回将要删除的清单（含 ``pattern``），不实际删除——
    破坏性操作先预览（PR #60 审查 P1 护栏之一）。
    """

    source_prefix: str
    purge_vectors: bool = True
    dry_run: bool = False


@router.post("/knowledge/purge")
def knowledge_purge(body: KnowledgePurgeIn, _admin: dict = Depends(get_current_admin)):
    """按 ``source_url`` 前缀批量删除知识文档（误导入回滚 / 基准数据清理）。

    前缀按**字面**匹配（``%``/``_`` 会被转义，不当通配符）；短于 3 字符直接拒绝，
    防止 ``%`` 这类输入误删全库。
    """
    try:
        result = knowledge.purge_by_source_prefix(
            body.source_prefix, purge_vectors=body.purge_vectors, dry_run=body.dry_run
        )
    except ValueError as exc:
        raise err_param(str(exc)) from exc
    return ok(result)


# ============================================================ 通知调度 ====


class NoticeDispatchIn(BaseModel):
    """分层推送调度入参（B18）。"""

    stages: list[str] | None = None      # 缺省读 XJT_NOTICE_PUSH_STAGES（D7,D2）
    now: str = ""                        # ISO 时间覆盖（回归验证用，如 2026-09-20T08:00:00）
    dry_run: bool = False                # 预演：只统计不写库
    user_id: int | None = None           # 只处理某用户的提醒（联调）
    async_run: bool = Field(default=True, alias="async")
    model_config = ConfigDict(populate_by_name=True)


@router.post("/notices/dispatch")
def notices_dispatch(body: NoticeDispatchIn, _admin: dict = Depends(get_current_admin)):
    """触发一次分层推送调度（B18：D-7 / D-2）。

    - Celery 启用（``async=true``）：投递后台任务，立即返回 ``celery_task_id``；
    - 未启用 / ``async=false``：eager 同步执行并直接返回调度统计。

    统计含 ``pushed[]``（逐条：kind/ref_id/stage/days_left/notice_id/delivery_id）、
    ``skipped{}``（not_due / already_pushed / read / …）与 ``errors[]``，
    便于回答"为什么这条没推"。
    """
    from app.core.config import settings
    from app.tasks import dispatch_notice_push

    payload = {
        "stages": body.stages,
        "now": body.now or None,
        "dry_run": body.dry_run,
        "user_id": body.user_id,
    }
    if settings.celery_enabled and body.async_run:
        try:
            task = dispatch_notice_push.delay(**payload)
        except Exception as exc:  # noqa: BLE001 - 投递失败转契约错误
            raise err_server(f"通知调度任务投递失败：{exc}") from exc
        return ok({
            "async": True,
            "celery_task_id": task.id,
            "note": "可用 /admin/knowledge/index-status/{task_id} 查询任务状态",
        })
    try:
        result = dispatch_notice_push(**payload)
    except ValueError as exc:
        raise err_param(str(exc)) from exc
    return ok({"async": False, **result})


@router.get("/notices/pending")
def notices_pending(
    limit: int = Query(200, ge=1, le=1000),
    _admin: dict = Depends(get_current_admin),
):
    """待办到期一览（只读）：每条显示剩余天数、命中档位、是否已推送。"""
    from app.services import notice_scheduler

    return ok({
        "stages": notice_scheduler.parse_stages(None),
        "deadline_supported": notice_scheduler.notice_deadline_supported(),
        "items": notice_scheduler.pending_summary(limit=limit),
    })


class NoticePurgeIn(BaseModel):
    """清理私密推送行（演示复位 / 回归数据回收）。"""

    kind: str = ""          # reminder / notice；留空=全部
    ref_id: int | None = None


@router.post("/notices/purge-private")
def notices_purge_private(body: NoticePurgeIn, _admin: dict = Depends(get_current_admin)):
    """删除私密推送通知行及其投递记录（B18 运行数据回收）。"""
    from app.services import notice_scheduler

    return ok(notice_scheduler.purge_private(kind=body.kind, ref_id=body.ref_id))


@router.post("/takeaway/orders/{order_id}/arrive")
def takeaway_arrive(order_id: int, admin: dict = Depends(get_current_admin)):
    """代收到件登记（B31）：写 `arrived_at` 并向下单人发**一条**站内通知。

    驿站签收是运营动作 —— 本期没有驿站账号体系，由管理员代行（`operator_id` 回显操作人）。
    幂等：重复调用**不重复投递**，只回报当前状态与 `notified=false`。
    """
    from app.services import takeaway

    return ok(takeaway.arrive(order_id, operator_id=int(admin["id"])))


@router.post("/knowledge/index")
def knowledge_index(force: bool = False, _admin: dict = Depends(get_current_admin)):
    """重建知识库索引（B15：Celery 异步执行）。

    - Celery 启用：立即返回 `{async, celery_task_id}`，用
      `GET /admin/knowledge/index-status/{task_id}` 查询进度；
    - 未启用（无 Redis）：eager 同步执行并直接返回构建结果（与旧版一致）。
    """
    from app.core.config import settings
    from app.tasks import build_knowledge_index

    try:
        task = build_knowledge_index.delay(force)
    except Exception as exc:  # noqa: BLE001 - 投递失败转契约错误
        raise err_server(f"索引构建任务投递失败：{exc}") from exc
    if settings.celery_enabled:
        return ok(
            {
                "async": True,
                "celery_task_id": task.id,
                "note": "索引构建已投递，可用 /admin/knowledge/index-status/{task_id} 查询",
            }
        )
    return ok(task.result)


@router.get("/knowledge/index-status/{task_id}")
def knowledge_index_status(task_id: str, _admin: dict = Depends(get_current_admin)):
    """查询异步索引构建状态（B15）。

    注意：XJT_CELERY_ENABLED=false（eager 模式）时结果不写入 backend，
    任意 task_id 均返回 state=PENDING，此时该接口无实际意义。
    """
    from app.tasks import task_state

    return ok(task_state(task_id))


@router.get("/forum/audit")
def pending_audit(
    limit: int = Query(50, ge=1, le=200),
    _admin: dict = Depends(get_current_admin),
):
    rows = cpp_bridge.forum_dao().pending_audit(limit)
    return ok({"items": rows})


class AuditBatchIn(BaseModel):
    """批量审核入参（topic_ids + 通过与否）。"""

    model_config = ConfigDict(populate_by_name=True)

    topic_ids: list[int]
    pass_flag: bool = Field(default=True, alias="pass")
    summary: str = ""


@router.post("/forum/audit/batch")
def audit_batch(body: AuditBatchIn, _admin: dict = Depends(get_current_admin)):
    """批量审核（B6）：一次通过/拒绝多个帖子；summary 非空时统一写入。"""
    if not body.topic_ids:
        raise err_param("topic_ids 不能为空")
    status = 1 if body.pass_flag else 2
    with cpp_bridge.begin():
        for tid in body.topic_ids:
            cpp_bridge.execute(
                "UPDATE topic SET audit_status = ?, "
                "ai_summary = IF(? = '', ai_summary, ?) WHERE id = ?",
                [status, body.summary, body.summary, tid],
            )
    return ok({"processed": len(body.topic_ids), "audit_status": status})


class AuditIn(BaseModel):
    """人工审核入参（字段名 pass 与契约一致，内部用 pass_flag 承载）。"""

    model_config = ConfigDict(populate_by_name=True)

    pass_flag: bool = Field(default=True, alias="pass")
    summary: str = ""


@router.post("/forum/audit/{topic_id}")
def audit(topic_id: int, body: AuditIn, _admin: dict = Depends(get_current_admin)):
    status = 1 if body.pass_flag else 2
    cpp_bridge.execute(
        "UPDATE topic SET audit_status = ?, ai_summary = ? WHERE id = ?",
        [status, body.summary, topic_id],
    )
    return ok({"topic_id": topic_id, "audit_status": status})


@router.get("/train/corpus")
def train_corpus(
    source_type: str = "",
    is_cleaned: int | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _admin: dict = Depends(get_current_admin),
):
    sql = "SELECT * FROM train_corpus WHERE 1=1 "
    params: list = []
    if source_type:
        sql += "AND source_type = ? "
        params.append(source_type)
    if is_cleaned is not None:
        sql += "AND is_cleaned = ? "
        params.append(is_cleaned)
    sql += "ORDER BY id DESC LIMIT ? OFFSET ?"
    params += [size, (page - 1) * size]
    rows = cpp_bridge.query(sql, params)
    return ok(paged(rows, len(rows), page, size))
