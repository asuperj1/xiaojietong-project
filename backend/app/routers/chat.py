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


class ChatIn(BaseModel):
    conversation_id: int | None = None
    content: str
    quick: str = ""


@router.post("/send")
async def chat_send(body: ChatIn, user: dict = Depends(get_current_user)):
    uid = int(user["id"])
    if not body.content.strip() and not body.quick:
        raise err_param("内容不能为空")

    # 1) 会话（不存在则新建）
    conv_id = body.conversation_id
    if not conv_id:
        title = (body.quick or body.content).strip()[:20]
        rows = cpp_bridge.execute(
            "INSERT INTO ai_conversation (user_id, title) VALUES (?, ?)", [uid, title]
        )
        conv_id = rows[1]

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
