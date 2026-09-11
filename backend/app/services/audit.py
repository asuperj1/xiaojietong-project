"""内容审核服务（B6）：词库规则 + 模型二次判定。

对外主入口：
- ``check(text)`` → ``{passed, level, reason, hits}``
  - level: ``pass``（直接通过）/ ``review``（转人工待审）/ ``block``（拒绝）
  - 词库来源：``audit_word`` 表（level=2/action=block → 拒绝；level=1/action=review → 待审），
    另附正则补充规则（手机号 / 联系方式 / 外链 → 待审）。
- ``audit_content(text)`` → ``{passed, level, reason, summary, source}``
  在 check 基础上做**模型二次判定**（可选，失败自动降级为规则，不阻塞业务）：
  - block 直接返回；
  - pass / review 且模型可用 → 调 Ollama 判定违规 + 生成摘要；
    模型判违规 → 升级为 block；否则保留原级别并附摘要（source=model）。
  - 模型不可用/超时/输出异常 → 原样返回（source=rule）。

调用方落库语义（forum.create_topic / comments、secondhand.publish）：
- block  → ``audit_status=2`` + 抛 ``err_audit``（错误码 3003）
- review → ``audit_status=0``（待人工，管理端处理）
- pass   → ``audit_status=1``（立即可见）
"""

from __future__ import annotations

import json
import re
import time
from typing import Optional

import httpx

from app.core.config import settings
from app.db import cpp_bridge

_MODEL_TIMEOUT = 30.0
_WORD_CACHE_TTL = 60.0  # 词库缓存秒数（避免每条内容都查库）

# 正则补充规则（命中 → review 待审）
_REGEX_RULES: list[tuple[str, str]] = [
    (r"(?<!\d)1[3-9]\d{9}(?!\d)", "疑似广告：包含手机号"),
    (r"(微信|QQ|VX|vx|V信|威信)\s*[:：]?\s*[A-Za-z0-9_\-]{5,}", "疑似广告：包含联系方式"),
    (r"https?://", "疑似广告：包含外部链接"),
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


def _load_words() -> list[dict]:
    """加载启用的词条（60s TTL 缓存）。库不可用/无表时返回空表（规则降级）。"""
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
    """规则判定：返回 {passed, level, reason, hits}。

    level: pass / review / block；block 时 passed=False。
    """
    content = text or ""
    hits: list[dict] = []
    reason = ""
    level = "pass"

    for row in _load_words():
        word = str(row.get("word") or "")
        if word and word in content:
            action = str(row.get("action") or "review").lower()
            hit_level = "block" if action == "block" else "review"
            hits.append(
                {
                    "word": word,
                    "level": hit_level,
                    "category": row.get("category") or "",
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
        for pattern, why in _REGEX_RULES:
            if re.search(pattern, content, flags=re.IGNORECASE):
                hits.append({"word": pattern, "level": "review", "category": "广告"})
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
        async with httpx.AsyncClient(timeout=httpx.Timeout(_MODEL_TIMEOUT)) as client:
            resp = await client.post(f"{settings.ollama_base_url}/api/chat", json=payload)
        if resp.status_code != 200:
            return None
        data = json.loads(resp.json().get("message", {}).get("content", ""))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 - 审核失败按规则降级
        return None


async def audit_content(text: str) -> dict:
    """规则 + 模型二次判定。

    返回 {passed, level, reason, summary, source}
    source: rule（规则判定）/ model（含模型结论）
    """
    rule = check(text)
    result = {
        "passed": rule["passed"],
        "level": rule["level"],
        "reason": rule["reason"],
        "summary": "",
        "source": "rule",
    }
    if rule["level"] == "block":
        return result

    verdict = await _model_verdict(text)
    if verdict is None:
        return result  # 模型不可用：按规则结果放行/待审，不阻塞

    if rule["level"] == "pass" and bool(verdict.get("violation")):
        # 仅对"规则放行"的内容允许模型升级为拒绝；
        # review（可疑词命中）按契约语义保持待人工复核，不被模型升级为 block
        result.update(
            passed=False,
            level="block",
            reason=str(verdict.get("reason") or "模型判定违规"),
            source="model",
        )
        return result

    result["summary"] = str(verdict.get("summary") or "").strip()[:500]
    result["source"] = "model"
    return result


def status_of(level: str) -> int:
    """审核级别 → topic/secondhand_item.audit_status（1 通过 / 0 待审 / 2 拒绝）。"""
    return {"pass": 1, "review": 0, "block": 2}.get(level, 0)
