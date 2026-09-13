"""二手 AI 助手（B9）：智能定价 + 描述生成。

**定价策略（真实数据支撑，替换原硬编码 30/20 元）**：
1. 统计库内**同类**在售/已售商品均价（样本 ≥1）；
2. 无同类样本 → 回退全库均价；
3. 全库也无样本 → 分类通用区间（并明示"样本不足"）。
   按成色（condition_level 1-10）折算系数 0.5 + 0.05×level，输出 ±20% 区间。

**描述生成**：模型（Ollama /api/chat，JSON 模式）输入
标题 / 分类 / 成色 / 用户备注 + **统计定价参考**，输出结构化
``{title, description, selling_points[], suggested_price, price_min, price_max, reason}``；
模型不可用/超时/输出异常 → 模板化文案 + 统计定价（``source=stat``），不阻塞发布。
模型给出的建议价须落在统计区间 [0.5×min, 1.5×max] 内，否则回退统计值（价格护栏）。
"""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx

from app.core.config import settings
from app.db import cpp_bridge

_MODEL_TIMEOUT = 60.0

# 无样本时的分类通用参考区间（元）
_FALLBACK_RANGE: dict[str, tuple[float, float]] = {
    "教材": (15.0, 40.0),
    "数码": (200.0, 2000.0),
    "生活": (10.0, 80.0),
    "体育": (30.0, 200.0),
    "服饰": (20.0, 150.0),
}
_DEFAULT_RANGE = (10.0, 100.0)

_MODEL_PROMPT = (
    "你是校园二手交易助手。请根据物品信息生成发布文案与定价建议。\n"
    "要求：描述控制在 80 字以内、口语自然；卖点 2-4 条；定价须参考给出的同类均价。\n"
    "只输出 JSON，格式：\n"
    '{"title": "优化标题", "description": "描述文案", '
    '"selling_points": ["卖点1", "卖点2"], "suggested_price": 数字, '
    '"price_min": 数字, "price_max": 数字, "reason": "定价依据（引用参考价）"}'
)


def _condition_factor(level: int) -> float:
    """成色 1-10 → 价格折算系数（5 成新≈0.75，10 成新=1.0）。"""
    lv = max(1, min(10, int(level or 5)))
    return round(0.5 + 0.05 * lv, 2)


def price_stats(category: str) -> dict:
    """统计同类商品价格（挂牌 + 已售；排除 0 价与已删除）。"""
    base_sql = (
        "SELECT COUNT(*) AS n, AVG(price) AS avg_price, MIN(price) AS min_price, "
        "MAX(price) AS max_price FROM secondhand_item "
        "WHERE is_deleted = 0 AND price > 0 AND status IN (0, 1) "
    )
    rows = (
        cpp_bridge.query(base_sql + "AND category = ?", [category])
        if category
        else cpp_bridge.query(base_sql, [])
    )
    row: dict[str, Any] = rows[0] if rows else {}
    n = int(row.get("n") or 0)
    scope = "category" if category else "all"

    if n == 0 and category:  # 同类无样本 → 回退全库
        rows = cpp_bridge.query(base_sql, [])
        row = rows[0] if rows else {}
        n = int(row.get("n") or 0)
        scope = "all"

    return {
        "sample_count": n,
        "avg_price": round(float(row.get("avg_price") or 0), 2) if n else 0.0,
        "min_price": round(float(row.get("min_price") or 0), 2) if n else 0.0,
        "max_price": round(float(row.get("max_price") or 0), 2) if n else 0.0,
        "scope": scope,
    }


def suggest_price(category: str, condition_level: int = 8) -> dict:
    """统计定价（真实定价兜底）：返回结构化定价建议。"""
    stats = price_stats(category)
    level = int(condition_level or 8)
    factor = _condition_factor(level)

    if stats["sample_count"] > 0:
        avg = stats["avg_price"]
        suggested = round(avg * factor, 1)
        low, high = round(suggested * 0.8, 1), round(suggested * 1.2, 1)
        scope_label = "同类" if stats["scope"] == "category" else "全库"
        reason = (
            f"库内{scope_label}商品 {stats['sample_count']} 件，均价 {avg} 元，"
            f"按成色 {level}/10 折算"
        )
        source = "stat"
    else:
        low, high = _FALLBACK_RANGE.get(category, _DEFAULT_RANGE)
        suggested = round((low + high) / 2, 1)
        reason = "库内暂无同类挂牌/成交样本，按分类通用区间给出参考价"
        source = "fallback"

    return {
        "category": category,
        "condition_level": level,
        "suggested_price": suggested,
        "price_min": low,
        "price_max": high,
        "sample_count": stats["sample_count"],
        "avg_price": stats["avg_price"],
        "source": source,
        "reason": reason,
    }


async def _model_describe(info: dict) -> Optional[dict]:
    """模型生成描述与定价建议（JSON 模式）；不可用/异常返回 None。"""
    payload = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": _MODEL_PROMPT},
            {"role": "user", "content": json.dumps(info, ensure_ascii=False)},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.4},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_MODEL_TIMEOUT)) as client:
            resp = await client.post(f"{settings.ollama_base_url}/api/chat", json=payload)
        if resp.status_code != 200:
            return None
        data = json.loads(resp.json().get("message", {}).get("content", ""))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 - 降级为模板文案 + 统计定价
        return None


async def describe_and_price(
    title: str,
    category: str,
    condition_level: int = 8,
    user_note: str = "",
) -> dict:
    """生成描述文案 + 定价建议（模型优先，统计兜底）。"""
    price = suggest_price(category, condition_level)
    info = {
        "标题": title or "未填写",
        "分类": category or "未分类",
        "成色": f"{int(condition_level or 8)}/10",
        "用户备注": user_note or "",
        "同类参考": {
            "均价": price["avg_price"],
            "样本数": price["sample_count"],
            "统计区间": [price["price_min"], price["price_max"]],
            "统计依据": price["reason"],
        },
    }

    verdict = await _model_describe(info)
    if verdict:
        # 价格护栏：模型建议价须落在统计区间 [0.5×min, 1.5×max] 内
        try:
            suggested = float(verdict.get("suggested_price") or 0)
        except (TypeError, ValueError):
            suggested = 0.0
        lo, hi = price["price_min"] * 0.5, price["price_max"] * 1.5
        if not suggested or not (lo <= suggested <= hi):
            suggested = price["suggested_price"]
        points = [str(s)[:50] for s in (verdict.get("selling_points") or [])][:5]
        return {
            "title": str(verdict.get("title") or title or "闲置物品")[:100],
            "description": str(verdict.get("description") or "")[:500],
            "selling_points": points,
            "suggested_price": round(suggested, 1),
            "price_min": price["price_min"],
            "price_max": price["price_max"],
            "reason": str(verdict.get("reason") or price["reason"])[:200],
            "category": category,
            "condition_level": price["condition_level"],
            "sample_count": price["sample_count"],
            "avg_price": price["avg_price"],
            "source": "model",
        }

    # 降级：模板文案 + 统计定价
    level = price["condition_level"]
    points: list[str] = []
    if level >= 8:
        points.append(f"成色 {level}/10，保存良好")
    if category:
        points.append(f"{category}分类，同类需求稳定")
    if user_note:
        points.append(user_note[:50])
    description = (
        f"{title or '闲置物品'}，成色 {level}/10。"
        f"{user_note + '。' if user_note else ''}价格可小刀，校内当面交易。"
    )
    return {
        "title": title or "闲置物品",
        "description": description[:500],
        "selling_points": points[:5],
        "suggested_price": price["suggested_price"],
        "price_min": price["price_min"],
        "price_max": price["price_max"],
        "reason": price["reason"],
        "category": category,
        "condition_level": level,
        "sample_count": price["sample_count"],
        "avg_price": price["avg_price"],
        "source": price["source"],  # stat / fallback
    }
