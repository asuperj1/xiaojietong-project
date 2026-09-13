"""AI 助手：对话（SSE 流式）/ 快捷指令 / 会话管理。

契约：docs/api.md §3
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.core.response import BizError, err_param, ok
from app.db import cpp_bridge
from app.services.citation_check import check_citations, clean_answer, should_refuse
from app.services.model_client import model_client
from app.services.rag import build_system_prompt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

MAX_HISTORY = 8  # 送入模型的最近消息轮数

# `C20` 拒答文案：检索结果判定为「不足以回答」时直接用这句，不给模型编造的机会。
REFUSE_TEXT = (
    "抱歉，校园知识库里没有找到能支撑这个问题的资料，我不做猜测。"
    "你可以换个说法再问一次，或者直接联系相关职能部门确认。"
)


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
        owned = cpp_bridge.query(
            "SELECT id FROM ai_conversation WHERE id = ? AND user_id = ? AND is_deleted = 0",
            [conv_id, uid],
        )
        if not owned:
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
        # `C20` 闸门一：检索结果明显支撑不了这个问题 → 直接拒答。
        # 省一次生成，更不给模型“先看到无关资料再编”的机会。
        # 注意：拒答时**不下发 sources**，否则前端会把无关文档当成“依据”展示。
        refused, reason = should_refuse(user_text, sources)
        if refused:
            logger.info("C20 拒答 conv=%s reason=%s", conv_id, reason)
            saved = cpp_bridge.execute(
                "INSERT INTO ai_message (conversation_id, role, content) VALUES (?, 'assistant', ?)",
                [conv_id, REFUSE_TEXT],
            )
            yield (
                "event: refused\n"
                f"data: {json.dumps({'delta': REFUSE_TEXT, 'reason': reason}, ensure_ascii=False)}\n\n"
            )
            yield (
                "event: done\n"
                f"data: {json.dumps({'conversation_id': conv_id, 'message_id': saved[1], 'refused': True}, ensure_ascii=False)}\n\n"
            )
            return

        yield f"event: sources\ndata: {json.dumps(sources, ensure_ascii=False)}\n\n"

        parts: list[str] = []
        async for chunk in model_client.stream_chat(messages):
            parts.append(chunk)
            yield f"event: chunk\ndata: {json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
        assistant_text = "".join(parts)

        # `C20` 闸门二：引用反向校验 —— 删掉模型编造的引用标记。
        # 正文已经流给前端了，这里额外下发 `citations` 事件带上修正后的全文，
        # 由前端擦除重写；校验本身出问题不能影响对话，所以整块包 try。
        final_text = assistant_text
        fabricated: list[str] = []
        try:
            report = check_citations(assistant_text, sources, question=user_text)
            final_text = clean_answer(assistant_text, report)
            fabricated = [c.value for c in report.fabricated]
            if fabricated:
                logger.warning("C20 剔除伪造引用 conv=%s %s", conv_id, fabricated)
        except Exception as exc:  # noqa: BLE001
            logger.warning("C20 引用校验异常（已忽略）conv=%s %s", conv_id, exc)

        saved = cpp_bridge.execute(
            "INSERT INTO ai_message (conversation_id, role, content) VALUES (?, 'assistant', ?)",
            [conv_id, final_text],
        )
        if fabricated or final_text != assistant_text:
            yield (
                "event: citations\n"
                f"data: {json.dumps({'fabricated': fabricated, 'final': final_text}, ensure_ascii=False)}\n\n"
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
    # 审计 SEC-03：先校验会话归属，避免遍历 conv_id 批量读取他人 AI 对话记录
    owned = cpp_bridge.query(
        "SELECT id FROM ai_conversation WHERE id = ? AND user_id = ? AND is_deleted = 0",
        [conv_id, int(user["id"])],
    )
    if not owned:
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
