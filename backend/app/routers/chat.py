"""AI 助手：对话（SSE 流式）/ 快捷指令 / 会话管理。

契约：docs/api.md §3
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.core.response import BizError, err_param, ok
from app.db import cpp_bridge
from app.services.model_client import model_client
from app.services.rag import build_system_prompt

router = APIRouter(prefix="/chat", tags=["chat"])

MAX_HISTORY = 8  # 送入模型的最近消息轮数
MAX_TITLE_LEN = 100  # ai_conversation.title 列宽 VARCHAR(100)
DEFAULT_TITLE = "新对话"  # 与建表默认值一致


def _clean_title(raw: str, *, allow_default: bool) -> str:
    """会话标题归一化：去首尾空白 + 限长。

    `allow_default=True`（新建）时空标题回落「新对话」；重命名时空标题**拒绝** ——
    静默复位成「新对话」会让用户以为改名成功了。
    """
    title = (raw or "").strip()
    if not title:
        if allow_default:
            return DEFAULT_TITLE
        raise err_param("标题不能为空")
    if len(title) > MAX_TITLE_LEN:
        raise err_param(f"标题最多 {MAX_TITLE_LEN} 字")
    return title


def _owned_conversation(conv_id: int, uid: int) -> bool:
    """会话存在、未删除且属于该用户。

    审计 SEC-03/04：读消息、发消息、改名、删除统一走这一条判定；越权与「不存在」
    **同码 1001**，不让攻击者用错误码差异探测他人会话是否存在。
    """
    rows = cpp_bridge.query(
        "SELECT id FROM ai_conversation WHERE id = ? AND user_id = ? AND is_deleted = 0",
        [conv_id, uid],
    )
    return bool(rows)


class ChatIn(BaseModel):
    conversation_id: int | None = None
    content: str
    quick: str = ""


@router.post("/send")
async def chat_send(body: ChatIn, user: dict = Depends(get_current_user)):
    uid = int(user["id"])
    if not body.content.strip() and not body.quick:
        raise err_param("内容不能为空")

    # 1) 会话：未指定则新建；**指定时必须属于当前用户**
    #    审计 SEC-04：否则可向他人会话注入消息，并把他人历史送入模型（提示注入外泄）
    conv_id = body.conversation_id
    if not conv_id:
        title = (body.quick or body.content).strip()[:20]
        rows = cpp_bridge.execute(
            "INSERT INTO ai_conversation (user_id, title) VALUES (?, ?)", [uid, title]
        )
        conv_id = rows[1]
    else:
        if not _owned_conversation(conv_id, uid):
            raise BizError(1001, "会话不存在")

    # 2) 保存用户消息
    user_text = body.content or body.quick
    cpp_bridge.execute(
        "INSERT INTO ai_message (conversation_id, role, content) VALUES (?, 'user', ?)",
        [conv_id, user_text],
    )

    # 3) 历史 + RAG 增强
    history = cpp_bridge.query(
        "SELECT role, content FROM ai_message WHERE conversation_id = ? ORDER BY id",
        [conv_id],
    )
    messages = [{"role": r["role"], "content": r["content"]} for r in history][-MAX_HISTORY:]
    system_prompt, sources = await build_system_prompt(user_text)
    messages.insert(0, {"role": "system", "content": system_prompt})

    # 4) 流式返回（SSE）
    async def gen():
        yield f"event: sources\ndata: {json.dumps(sources, ensure_ascii=False)}\n\n"
        parts: list[str] = []
        async for chunk in model_client.stream_chat(messages):
            parts.append(chunk)
            yield f"event: chunk\ndata: {json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
        assistant_text = "".join(parts)
        saved = cpp_bridge.execute(
            "INSERT INTO ai_message (conversation_id, role, content) VALUES (?, 'assistant', ?)",
            [conv_id, assistant_text],
        )
        yield (
            "event: done\n"
            f"data: {json.dumps({'conversation_id': conv_id, 'message_id': saved[1]}, ensure_ascii=False)}\n\n"
        )

    return StreamingResponse(gen(), media_type="text/event-stream")


class QuickIn(BaseModel):
    keyword: str


@router.post("/quick")
async def quick(body: QuickIn, user: dict = Depends(get_current_user)):
    rows = cpp_bridge.query(
        "SELECT * FROM quick_command WHERE keyword = ? AND enabled = 1", [body.keyword]
    )
    if not rows:
        raise BizError(1001, "快捷指令不存在")
    cmd = rows[0]
    answer = await model_client.chat(
        [
            {"role": "system", "content": "你是校捷通的校园智能助手，请用中文简洁回答。"},
            {"role": "user", "content": cmd["template"]},
        ]
    )
    conv = cpp_bridge.execute(
        "INSERT INTO ai_conversation (user_id, title) VALUES (?, ?)",
        [int(user["id"]), cmd["title"]],
    )
    cpp_bridge.execute(
        "INSERT INTO ai_message (conversation_id, role, content) VALUES (?, 'user', ?)",
        [conv[1], cmd["template"]],
    )
    cpp_bridge.execute(
        "INSERT INTO ai_message (conversation_id, role, content) VALUES (?, 'assistant', ?)",
        [conv[1], answer],
    )
    return ok(
        {
            "conversation_id": conv[1],
            "answer": answer,
            "action": {"type": cmd.get("target_module", "")},
        }
    )


class CreateConvIn(BaseModel):
    title: str = ""


@router.post("/conversations")
def create_conversation(
    body: CreateConvIn | None = None, user: dict = Depends(get_current_user)
):
    """显式新建会话（B27）：点「新建」即拿到 `conversation_id`，**空会话也能直接对话**。

    请求体可省略（前端「点新建」通常不打 body）：无 body / `{}` → 标题取「新对话」。
    """
    uid = int(user["id"])
    title = _clean_title(body.title if body else "", allow_default=True)
    _, conv_id = cpp_bridge.execute(
        "INSERT INTO ai_conversation (user_id, title) VALUES (?, ?)", [uid, title]
    )
    rows = cpp_bridge.query(
        "SELECT id, title, created_at FROM ai_conversation WHERE id = ?", [conv_id]
    )
    view = rows[0] if rows else {"id": conv_id, "title": title, "created_at": ""}
    return ok(
        {
            "conversation_id": int(view["id"]),
            "title": view["title"],
            "created_at": view["created_at"],
        }
    )


class RenameConvIn(BaseModel):
    title: str = ""


@router.patch("/conversations/{conv_id}")
def rename_conversation(
    conv_id: int, body: RenameConvIn | None = None, user: dict = Depends(get_current_user)
):
    """重命名会话（B27 的可选件）。

    请求体缺省时按空标题处理 → 返回契约错误 `1001`（而不是 FastAPI 的 422 detail）。
    ⚠️ **置顶未实现**：`ai_conversation` 没有 `is_pinned`/`sort` 列，加列属 DDL 变更
    （需走 `db/sql` 迁移批次），且任务单里本项本就标注为「可选」。
    """
    uid = int(user["id"])
    title = _clean_title(body.title if body else "", allow_default=False)
    if not _owned_conversation(conv_id, uid):
        raise BizError(1001, "会话不存在")
    cpp_bridge.execute(
        "UPDATE ai_conversation SET title = ? WHERE id = ? AND user_id = ? AND is_deleted = 0",
        [title, conv_id, uid],
    )
    return ok({"conversation_id": conv_id, "title": title})


@router.get("/conversations")
def conversations(user: dict = Depends(get_current_user)):
    rows = cpp_bridge.query(
        "SELECT id, title, updated_at FROM ai_conversation "
        "WHERE user_id = ? AND is_deleted = 0 ORDER BY updated_at DESC LIMIT 50",
        [int(user["id"])],
    )
    return ok({"items": rows})


@router.get("/conversations/{conv_id}/messages")
def messages(conv_id: int, user: dict = Depends(get_current_user)):
    # 审计 SEC-03：先校验会话归属，避免遍历 conv_id 批量读取他人 AI 对话记录
    if not _owned_conversation(conv_id, int(user["id"])):
        raise BizError(1001, "会话不存在")
    rows = cpp_bridge.query(
        "SELECT id, role, content, created_at FROM ai_message "
        "WHERE conversation_id = ? ORDER BY id",
        [conv_id],
    )
    return ok({"items": rows})


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: int, user: dict = Depends(get_current_user)):
    cpp_bridge.execute(
        "UPDATE ai_conversation SET is_deleted = 1 WHERE id = ? AND user_id = ?",
        [conv_id, int(user["id"])],
    )
    return ok({"conversation_id": conv_id})


class FeedbackIn(BaseModel):
    target_type: str
    target_id: int
    rating: int = 5
    content: str = ""


@router.post("/feedback")
def feedback(body: FeedbackIn, user: dict = Depends(get_current_user)):
    if body.rating < 1 or body.rating > 5:
        raise err_param("评分 1-5")
    cpp_bridge.execute(
        "INSERT INTO feedback (user_id, target_type, target_id, rating, content) "
        "VALUES (?, ?, ?, ?, ?)",
        [int(user["id"]), body.target_type, body.target_id, body.rating, body.content],
    )
    return ok()
