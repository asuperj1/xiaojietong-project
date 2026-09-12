"""内容审核服务（B6）：词库规则 + 模型二次判定 + 审核留痕。

词库与留痕表为 C7 设计（``db/sql/11_audit.sql``）：
- ``audit_word``：敏感词库。``level`` 1 禁止 / 2 可疑；
  ``action`` 1 拒绝 / 2 待审 / 3 打码（当前按待审处理）。
- ``audit_log`` ：审核留痕。``source`` 1 规则 / 2 模型 / 3 人工；
  ``result`` 0 待审 / 1 通过 / 2 拒绝。

对外主入口：
- ``check(text)`` → ``{passed, level, reason, hits}``（纯规则，无副作用）
  level: ``pass`` / ``review`` / ``block``
- ``audit_content(text, *, target_type, target_id, user_id)``
  → ``{passed, level, reason, summary, source, cost_ms}``
  规则 + 模型二次判定；写 ``audit_log`` 留痕、累加 ``audit_word.hit_count``；
  模型不可用/超时 → 自动降级规则，**不阻塞业务**。

调用方落库语义（forum / secondhand）：
- block  → ``audit_status=2`` + 抛 ``err_audit``（错误码 3003）；评论不入库
- review → ``audit_status=0``（转人工待审）
- pass   → ``audit_status=1``（立即可见）
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

import httpx

from app.core.config import settings
from app.db import cpp_bridge

_MODEL_TIMEOUT = 30.0
_WORD_CACHE_TTL = 60.0  # 词库缓存秒数（避免每条内容都查库）

# 正则补充规则：命中 → review 待审（label 用于留痕展示）
_REGEX_RULES: list[tuple[str, str, str]] = [
    (r"(?<!\d)1[3-9]\d{9}(?!\d)", "手机号", "疑似广告：包含手机号"),
    (r"(微信|QQ|VX|vx|V信|威信)\s*[:：]?\s*[A-Za-z0-9_\-]{5,}", "联系方式", "疑似广告：包含联系方式"),
    (r"https?://", "外链", "疑似广告：包含外部链接"),
]

_MODEL_PROMPT = (
    "你是校园论坛的内容审核助手。请对用户内容完成两件事：\n"
    "1) 判断是否违规：广告引流、辱骂攻击、色情低俗、违法违规、政治敏感均属违规；\n"
    "2) 为正常内容生成不超过 50 字的摘要。\n"
    "只输出 JSON，格式："
    '{"violation": true/false, "category": "违规类别或正常", '
    '"reason": "判定理由(简短)", "summary": "内容摘要(正常内容必填，违规则可为空)"}'
)

# 词库缓存
_words_cache: list[dict] = []
_words_ts: float = 0.0


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _hit_level(row: dict) -> str:
    """按 C7 词库语义判定命中级别：action 1 拒绝 / 2 待审 / 3 打码（按待审）。"""
    action = _to_int(row.get("action"))
    if action == 1:
        return "block"
    if action in (2, 3):
        return "review"
    return "block" if _to_int(row.get("level"), default=2) == 1 else "review"


def _load_words() -> list[dict]:
    """加载启用的词条（60s TTL 缓存）。库不可用/无表时返回空表（仅剩正则规则）。"""
    global _words_cache, _words_ts
    now = time.time()
    if _words_cache and now - _words_ts < _WORD_CACHE_TTL:
        return _words_cache
    try:
        rows = cpp_bridge.query(
            "SELECT word, level, action, category FROM audit_word "
            "WHERE enabled = 1 AND word <> ''",
            [],
        )
    except Exception:  # noqa: BLE001 - 词库不可用时仅剩正则规则
        rows = []
    _words_cache = rows
    _words_ts = now
    return _words_cache


def check(text: str) -> dict:
    """规则判定（纯函数，无副作用）：返回 {passed, level, reason, hits}。"""
    content = text or ""
    hits: list[dict] = []
    reason = ""
    level = "pass"

    for row in _load_words():
        word = str(row.get("word") or "")
        if word and word in content:
            hit_level = _hit_level(row)
            hits.append(
                {
                    "word": word,
                    "level": hit_level,
                    "category": row.get("category") or "",
                    "from": "word",
                }
            )
            if hit_level == "block":
                level = "block"
                reason = f"命中敏感词：{word}"
                break
            if level == "pass":
                level = "review"
                reason = f"命中可疑词：{word}（转人工复核）"

    if level != "block":
        for pattern, label, why in _REGEX_RULES:
            if re.search(pattern, content, flags=re.IGNORECASE):
                hits.append({"word": label, "level": "review", "category": "广告", "from": "regex"})
                if level == "pass":
                    level = "review"
                    reason = why

    return {
        "passed": level != "block",
        "level": level,
        "reason": reason,
        "hits": hits,
    }


async def _model_verdict(text: str) -> Optional[dict]:
    """模型二次判定（JSON 模式）。不可用/异常返回 None。"""
    payload = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": _MODEL_PROMPT},
            {"role": "user", "content": text[:4000]},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_MODEL_TIMEOUT), trust_env=False) as client:
            resp = await client.post(f"{settings.ollama_base_url}/api/chat", json=payload)
        if resp.status_code != 200:
            return None
        data = json.loads(resp.json().get("message", {}).get("content", ""))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 - 审核失败按规则降级
        return None


async def audit_content(
    text: str,
    *,
    target_type: str = "",
    target_id: int = 0,
    user_id: int = 0,
) -> dict:
    """规则 + 模型二次判定，并写入 audit_log 留痕。

    返回 {passed, level, reason, summary, source, cost_ms}；source: rule / model。
    """
    t0 = time.perf_counter()
    rule = check(text)
    level = rule["level"]
    reason = rule["reason"]
    hits = rule["hits"]
    summary = ""
    source_code = 1  # 1 规则 / 2 模型

    if level != "block":
        verdict = await _model_verdict(text)
        if verdict is not None:
            source_code = 2
            if level == "pass" and bool(verdict.get("violation")):
                # 仅对"规则放行"的内容允许模型升级为拒绝；
                # review（可疑词命中）按词库语义保持待人工复核
                level = "block"
                reason = str(verdict.get("reason") or "模型判定违规")
            else:
                summary = str(verdict.get("summary") or "").strip()[:500]

    cost_ms = int((time.perf_counter() - t0) * 1000)

    # 词库命中计数（运营排序用；失败不影响主流程）
    for hit in hits:
        if hit.get("from") == "word":
            try:
                cpp_bridge.execute(
                    "UPDATE audit_word SET hit_count = hit_count + 1 WHERE word = ?",
                    [hit["word"]],
                )
            except Exception:  # noqa: BLE001
                pass

    # 审核留痕 audit_log（C7）：source 1规则/2模型，result 0待审/1通过/2拒绝
    hit_words = ",".join(h["word"] for h in hits)[:500]
    try:
        cpp_bridge.execute(
            "INSERT INTO audit_log (target_type, target_id, user_id, source, result, "
            "hit_words, reason, cost_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                target_type or "",
                _to_int(target_id),
                _to_int(user_id),
                source_code,
                {"pass": 1, "review": 0, "block": 2}[level],
                hit_words,
                reason[:255],
                cost_ms,
            ],
        )
    except Exception:  # noqa: BLE001 - 留痕失败不影响审核主流程
        pass

    return {
        "passed": level != "block",
        "level": level,
        "reason": reason,
        "summary": summary,
        "source": "model" if source_code == 2 else "rule",
        "cost_ms": cost_ms,
    }


def status_of(level: str) -> int:
    """审核级别 → topic/secondhand_item.audit_status（1 通过 / 0 待审 / 2 拒绝）。"""
    return {"pass": 1, "review": 0, "block": 2}.get(level, 0)
