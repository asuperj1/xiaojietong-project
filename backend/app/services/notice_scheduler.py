"""分层推送调度（B18）：待办到期前 D-7 / D-2 分层触达，落 notice_delivery。

**为什么需要它**：B10 的投递记录是"用户打开 App 时懒生成"（拉取即投递），
属于**被动**触达；B18 补的是**主动**触达——定时任务在待办到期前 7 天 / 2 天
（可配 ``D0`` 当天）自动生成投递记录，用户下次打开即有未读提醒。

**两类待办来源**
1. ``reminder`` 表（Agent 工具 ``add_reminder`` 与 ``POST /agent/reminders`` 创建的个人提醒）；
2. ``campus_notice.deadline``（B19 ``14_notice_extend.sql`` 新增列）——**列存在才启用**，
   用 ``information_schema`` 探测，B19 未合入时自动跳过该类来源，不影响功能。

**私密推送行设计（不新增表）**
``campus_notice`` 无"仅某人可见"字段，且 B18 明确要求复用 ``notice_delivery``
（该表对 ``(notice_id, user_id)`` 有唯一键，天然适合"一人一条"）。因此：

- 每次「某待办 × 某档位」生成一条**推送用通知行**，把幂等键写进 ``target_grade``：
  ``__push:reminder:12:D7`` / ``__push:notice:5:D2``，``source='系统提醒'``、
  ``category='待办提醒'``；
- 该行是**唯一一行、多用户共享**的（例如全校通知的 D-2 推送），
  用户绑定靠 ``notice_delivery(user_id, notice_id)``，因此**不会**为每个用户
  复制通知正文；
- ``target_grade`` 带 ``__push:`` 前缀 → 永不等于任何真实年级，B10 的
  ``_visible_notices`` 与 ``/life/notices`` 会**显式排除**，不会泄漏给他人；
  用户自己的未读/信息流是按 ``notice_delivery`` 取的，所以能正常看到。

**幂等性**：``(待办, 档位)`` 已推送过就不再推送 → 手动重复触发调度不会产生重复消息。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from app.core.config import settings
from app.db import cpp_bridge

# 私密推送行的 target_grade 前缀（B10 同步使用同一常量做过滤）
PRIVATE_TARGET_PREFIX = "__push:"
PUSH_SOURCE = "系统提醒"
PUSH_CATEGORY = "待办提醒"

# 档位定义：stage -> (提前天数阈值, 排序权重, 文案前缀)
STAGE_RULES: dict[str, tuple[int, float, str]] = {
    "D7": (7, 2.0, "还有 7 天"),
    "D2": (2, 3.0, "还有 2 天"),
    "D0": (0, 4.0, "已到期"),
}
DEFAULT_STAGES = ("D7", "D2")
MAX_TITLE_CHARS = 120          # campus_notice.title VARCHAR(128)


# ------------------------------------------------------------------ 工具 ----

def is_private_audience(target_grade: Any) -> bool:
    """该通知行是否为「私密推送行」（B10/前端列表据此过滤）。"""
    return str(target_grade or "").startswith(PRIVATE_TARGET_PREFIX)


def private_notice_ids() -> set[int]:
    """全部私密推送行的 id 集合。

    ⚠️ 必须按 **id** 过滤，不能依赖行里的 ``target_grade`` 字段：
    ``LifeDAO.page_notices`` 的 SELECT 只取
    ``id,title,content,source,category,publish_time``（不含 target_grade），
    因此调用方拿到的行里**根本没有**该字段，按字段过滤会静默失效
    （PR #60 审查 P0：/life/notices 私密行泄漏）。
    """
    rows = cpp_bridge.query(
        "SELECT id FROM campus_notice WHERE target_grade LIKE ?",
        [f"{PRIVATE_TARGET_PREFIX}%"],
    )
    return {int(r["id"]) for r in rows}


def parse_stages(value: str | Iterable[str] | None) -> list[str]:
    """解析档位配置（``"D7,D2"`` / ``["D7"]``）→ 合法档位列表。"""
    if value is None:
        value = settings.notice_push_stages
    if isinstance(value, str):
        raw = [p.strip().upper() for p in value.replace("，", ",").split(",")]
    else:
        raw = [str(p).strip().upper() for p in value]
    stages = [s for s in raw if s in STAGE_RULES]
    return stages or list(DEFAULT_STAGES)


def _to_dt(value: Any) -> Optional[datetime]:
    """jt_db 返回的时间可能是 datetime 或字符串，统一成 datetime。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[: len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
    return None


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def due_stage(days_left: int, stages: list[str]) -> Optional[str]:
    """按剩余天数选**最紧急**的已达条件档位。

    例：``stages=["D7","D2"]``、剩余 5 天 → D7；剩余 2 天 → D2；
    调度停摆后恢复（剩余 1 天，D7 未推）→ 只推 D2（避免补发过期档位造成轰炸）。
    """
    eligible = [s for s in stages if days_left <= STAGE_RULES[s][0]]
    if not eligible:
        return None
    return sorted(eligible, key=lambda s: STAGE_RULES[s][0])[0]


def _stage_head(stage: str, days_left: int) -> str:
    """档位短文案（不含截止时间）——用于 reason 等单行场景。"""
    if days_left > 1:
        return f"还有 {days_left} 天"
    if days_left == 1:
        return "明天到期"
    if days_left == 0:
        return "今天到期"
    return f"已逾期 {abs(days_left)} 天"


def _stage_wording(stage: str, days_left: int, deadline: datetime) -> str:
    """生成可读的档位文案（避免"还有 7 天"与实际剩余天数不符）。"""
    return f"【{_stage_head(stage, days_left)}】截止 {_fmt(deadline)}"


# ------------------------------------------------------- 推送行（通知） ----

def _marker(kind: str, ref_id: int, stage: str) -> str:
    return f"{PRIVATE_TARGET_PREFIX}{kind}:{int(ref_id)}:{stage}"


def _find_push_notice(kind: str, ref_id: int, stage: str) -> Optional[int]:
    rows = cpp_bridge.query(
        "SELECT id FROM campus_notice WHERE target_grade = ? LIMIT 1",
        [_marker(kind, ref_id, stage)],
    )
    return int(rows[0]["id"]) if rows else None


def _create_push_notice(
    kind: str, ref_id: int, stage: str, title: str, content: str, now: datetime
) -> int:
    _, notice_id = cpp_bridge.execute(
        "INSERT INTO campus_notice (title, content, source, category, target_grade, publish_time) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            title[:MAX_TITLE_CHARS],
            content,
            PUSH_SOURCE,
            PUSH_CATEGORY,
            _marker(kind, ref_id, stage),
            now.strftime("%Y-%m-%d %H:%M:%S"),
        ],
    )
    return int(notice_id)


def _deliver(
    notice_id: int, user_id: int, score: float, reason: str, channel: Optional[int] = None
) -> int:
    """写投递记录（幂等：唯一键冲突时刷新得分/理由/渠道）。返回 delivery id。"""
    cpp_bridge.execute(
        "INSERT INTO notice_delivery (notice_id, user_id, channel, score, reason) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON DUPLICATE KEY UPDATE channel = VALUES(channel), score = VALUES(score), "
        "reason = VALUES(reason)",
        [
            int(notice_id),
            int(user_id),
            int(channel if channel is not None else settings.notice_push_channel),
            float(score),
            reason[:255],
        ],
    )
    rows = cpp_bridge.query(
        "SELECT id FROM notice_delivery WHERE notice_id = ? AND user_id = ?",
        [int(notice_id), int(user_id)],
    )
    return int(rows[0]["id"]) if rows else 0


# ------------------------------------------------------------ 待办来源 ----

def _reminder_rows(now: datetime, user_id: Optional[int], limit: int) -> list[dict]:
    """待提醒的个人提醒（未完成）。"""
    sql = (
        "SELECT id, user_id, content, remind_at FROM reminder "
        "WHERE is_done = 0 AND remind_at IS NOT NULL"
    )
    params: list = []
    if user_id:
        sql += " AND user_id = ?"
        params.append(int(user_id))
    sql += " ORDER BY remind_at LIMIT ?"
    params.append(int(limit))
    return cpp_bridge.query(sql, params)


_EXTENDED_COLUMNS: Optional[set[str]] = None
EXTENDED_COLUMN_NAMES = ("deadline", "materials", "importance")


def notice_extended_columns(refresh: bool = False) -> set[str]:
    """``campus_notice`` 上**已存在**的扩展列（B19 ``14_notice_extend.sql`` 导入与否）。

    结果进程内缓存；导入 SQL 后重启服务即自动启用，无需改代码/配置。
    探测失败按「都没有」处理（降级：功能跳过而不是报错）。
    """
    global _EXTENDED_COLUMNS
    if _EXTENDED_COLUMNS is not None and not refresh:
        return _EXTENDED_COLUMNS
    try:
        rows = cpp_bridge.query(
            "SELECT column_name AS c FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'campus_notice' "
            "AND column_name IN ('deadline','materials','importance')"
        )
        _EXTENDED_COLUMNS = {str(r.get("c") or "") for r in rows if r.get("c")}
    except Exception:  # noqa: BLE001 - 探测失败按"未扩展"降级
        _EXTENDED_COLUMNS = set()
    return _EXTENDED_COLUMNS


def notice_deadline_supported(refresh: bool = False) -> bool:
    """``campus_notice.deadline`` 是否存在（B19 的 ``14_notice_extend.sql`` 是否已导入）。"""
    return "deadline" in notice_extended_columns(refresh)


def importance_of(row: dict) -> int:
    """从通知行取重要度（1~5；缺失/非法/未打分 → 0）。"""
    try:
        value = int(row.get("importance") or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(5, value))


def _notice_rows(now: datetime, limit: int) -> list[dict]:
    """带截止时间的校园通知（仅当 B19 已扩展 deadline 列）。

    扩展列按实际存在情况拼接（B20）：只加了 ``deadline`` 时也能跑，
    ``materials`` / ``importance`` 存在才带上（重要度参与推送打分）。
    """
    if not notice_deadline_supported():
        return []
    cols = ["id", "title", "content", "source", "category", "target_grade", "deadline"]
    present = notice_extended_columns()
    cols += [c for c in ("materials", "importance") if c in present]
    window_start = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    window_end = (now + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    return cpp_bridge.query(
        f"SELECT {', '.join(cols)} FROM campus_notice WHERE deadline IS NOT NULL "
        "AND deadline BETWEEN ? AND ? AND target_grade NOT LIKE ? "
        "ORDER BY deadline LIMIT ?",
        [window_start, window_end, f"{PRIVATE_TARGET_PREFIX}%", int(limit)],
    )


def _target_users(target_grade: str, limit: int) -> list[dict]:
    """通知的受众用户（全员 / 指定年级），受 fanout 上限保护。"""
    if target_grade:
        return cpp_bridge.query(
            "SELECT id, grade FROM user WHERE is_deleted = 0 AND grade = ? LIMIT ?",
            [target_grade, int(limit)],
        )
    return cpp_bridge.query(
        "SELECT id, grade FROM user WHERE is_deleted = 0 ORDER BY id LIMIT ?", [int(limit)]
    )


def _already_read_users(notice_id: int) -> set[int]:
    """已读过原通知的用户（不再打扰）。"""
    rows = cpp_bridge.query(
        "SELECT user_id FROM notice_delivery WHERE notice_id = ? AND is_read = 1",
        [int(notice_id)],
    )
    return {int(r["user_id"]) for r in rows}


# ------------------------------------------------------------- 调度主体 ----

def dispatch(
    *,
    stages: str | Iterable[str] | None = None,
    now: Optional[datetime] = None,
    dry_run: bool = False,
    kinds: Iterable[str] = ("reminder", "notice"),
    user_id: Optional[int] = None,
    limit: Optional[int] = None,
) -> dict:
    """执行一次分层推送调度（可被定时任务、管理端手动触发、回归测试调用）。

    Args:
        stages: 启用档位（默认读 ``XJT_NOTICE_PUSH_STAGES``）。
        now: 时间基准（默认当前时间；测试可指定以模拟"再过 5 天"）。
        dry_run: 只统计不写库（预演）。
        kinds: 参与的待办来源（reminder / notice）。
        user_id: 只处理该用户的提醒（联调用）。
        limit: 单次处理条数上限（默认 ``XJT_NOTICE_PUSH_MAX_ITEMS``）。

    Returns:
        ``{now, stages, dry_run, scanned, pushed[], skipped{}, errors[], summary}``
    """
    base = now or datetime.now()
    stage_list = parse_stages(stages)
    kind_set = {str(k).strip() for k in kinds}
    max_items = int(limit or settings.notice_push_max_items)

    pushed: list[dict] = []
    skipped: dict[str, int] = {
        "not_due": 0, "already_pushed": 0, "read": 0, "no_user": 0, "invalid_time": 0,
    }
    errors: list[dict] = []
    scanned = {"reminder": 0, "notice": 0}

    # ---- 来源 1：个人提醒 ----
    if "reminder" in kind_set:
        try:
            rows = _reminder_rows(base, user_id, max_items)
        except Exception as exc:  # noqa: BLE001
            rows = []
            errors.append({"kind": "reminder", "error": f"查询失败：{exc}"})
        scanned["reminder"] = len(rows)
        for row in rows:
            deadline = _to_dt(row.get("remind_at"))
            if deadline is None:
                skipped["invalid_time"] += 1
                continue
            days_left = (deadline.date() - base.date()).days
            stage = due_stage(days_left, stage_list)
            if stage is None:
                skipped["not_due"] += 1
                continue
            uid = int(row["user_id"])
            if _find_push_notice("reminder", int(row["id"]), stage):
                skipped["already_pushed"] += 1
                continue
            content_text = str(row.get("content") or "待办事项")
            head = _stage_wording(stage, days_left, deadline)
            short = _stage_head(stage, days_left)
            if dry_run:
                pushed.append({
                    "kind": "reminder", "ref_id": int(row["id"]), "user_id": uid,
                    "stage": stage, "days_left": days_left, "title": content_text,
                    "dry_run": True,
                })
                continue
            try:
                notice_id = _create_push_notice(
                    "reminder", int(row["id"]), stage,
                    f"{head} {content_text}".strip(),
                    f"你的待办「{content_text}」{short}。"
                    f"\n截止时间：{_fmt(deadline)}",
                    base,
                )
                delivery_id = _deliver(
                    notice_id, uid, STAGE_RULES[stage][1],
                    f"你设置的提醒：{content_text}（{short}）",
                )
                if settings.reminder_auto_done and stage == "D0" and days_left <= 0:
                    cpp_bridge.execute("UPDATE reminder SET is_done = 1 WHERE id = ?", [int(row["id"])])
                pushed.append({
                    "kind": "reminder", "ref_id": int(row["id"]), "user_id": uid,
                    "stage": stage, "days_left": days_left, "notice_id": notice_id,
                    "delivery_id": delivery_id, "title": content_text,
                })
            except Exception as exc:  # noqa: BLE001 - 单条失败不影响整批
                errors.append({"kind": "reminder", "ref_id": int(row["id"]), "error": str(exc)[:200]})

    # ---- 来源 2：带截止时间的校园通知 ----
    if "notice" in kind_set:
        try:
            rows = _notice_rows(base, max_items)
        except Exception as exc:  # noqa: BLE001
            rows = []
            errors.append({"kind": "notice", "error": f"查询失败：{exc}"})
        scanned["notice"] = len(rows)
        for row in rows:
            deadline = _to_dt(row.get("deadline"))
            if deadline is None:
                skipped["invalid_time"] += 1
                continue
            days_left = (deadline.date() - base.date()).days
            stage = due_stage(days_left, stage_list)
            if stage is None:
                skipped["not_due"] += 1
                continue
            nid = int(row["id"])
            if _find_push_notice("notice", nid, stage):
                skipped["already_pushed"] += 1
                continue
            title = str(row.get("title") or "校园通知")
            head = _stage_wording(stage, days_left, deadline)
            short = _stage_head(stage, days_left)
            users = _target_users(str(row.get("target_grade") or ""), settings.notice_push_max_fanout)
            if not users:
                skipped["no_user"] += 1
                continue
            readers = _already_read_users(nid)
            if dry_run:
                pushed.append({
                    "kind": "notice", "ref_id": nid, "stage": stage, "days_left": days_left,
                    "title": title, "audience": len(users), "read_skip": len(readers), "dry_run": True,
                })
                continue
            try:
                # B20：重要度（抽取/打分结果）参与推送排序与文案，缺失时按 0 处理
                imp = importance_of(row)
                score = STAGE_RULES[stage][1] + 0.2 * imp
                reason = f"{short}：{title}" + (f"（重要度 {imp}）" if imp else "")
                materials = str(row.get("materials") or "").strip()
                body = (
                    f"{row.get('content') or title}\n\n{head}，请及时办理。"
                    f"\n截止时间：{_fmt(deadline)}\n来源：{row.get('source') or '校园通知'}"
                )
                if materials:
                    body += f"\n需要材料：{materials}"
                notice_id = _create_push_notice(
                    "notice", nid, stage, f"{head} {title}", body, base,
                )
                sent = 0
                for u in users:
                    uid = int(u["id"])
                    if uid in readers:
                        skipped["read"] += 1
                        continue
                    _deliver(notice_id, uid, score, reason)
                    sent += 1
                pushed.append({
                    "kind": "notice", "ref_id": nid, "stage": stage, "days_left": days_left,
                    "notice_id": notice_id, "title": title, "delivered": sent,
                    "importance": imp,
                })
            except Exception as exc:  # noqa: BLE001
                errors.append({"kind": "notice", "ref_id": nid, "error": str(exc)[:200]})

    return {
        "now": base.strftime("%Y-%m-%d %H:%M:%S"),
        "stages": stage_list,
        "dry_run": dry_run,
        "scanned": scanned,
        "pushed": pushed,
        "skipped": skipped,
        "errors": errors,
        "summary": {
            "pushed_items": len(pushed),
            "pushed_messages": sum(
                int(p.get("delivered", 1)) for p in pushed if not p.get("dry_run")
            ),
            "skipped": sum(skipped.values()),
            "errors": len(errors),
        },
        "notice_deadline_supported": notice_deadline_supported(),
    }


# -------------------------------------------------------------- 维护 ----

def purge_private(kind: str = "", ref_id: Optional[int] = None) -> dict:
    """清理私密推送行及其投递记录（演示复位 / 测试数据回收）。

    Args:
        kind: 仅清理某类来源（reminder / notice）；留空清理全部。
        ref_id: 仅清理某个待办 id。
    """
    sql = "SELECT id, target_grade FROM campus_notice WHERE target_grade LIKE ?"
    params: list = [f"{PRIVATE_TARGET_PREFIX}%"]
    if kind:
        params[0] = f"{PRIVATE_TARGET_PREFIX}{kind}:%"
    rows = cpp_bridge.query(sql, params)
    ids: list[int] = []
    for r in rows:
        marker = str(r.get("target_grade") or "")
        if ref_id is not None:
            parts = marker.split(":")
            if len(parts) < 3 or parts[2] != str(int(ref_id)):
                continue
        ids.append(int(r["id"]))
    if not ids:
        return {"notices": 0, "deliveries": 0}

    placeholders = ",".join("?" for _ in ids)
    deliveries, _ = cpp_bridge.execute(
        f"DELETE FROM notice_delivery WHERE notice_id IN ({placeholders})", ids
    )
    notices, _ = cpp_bridge.execute(
        f"DELETE FROM campus_notice WHERE id IN ({placeholders})", ids
    )
    return {"notices": int(notices), "deliveries": int(deliveries)}


def pending_summary(now: Optional[datetime] = None, limit: int = 200) -> list[dict]:
    """待办到期一览（管理端排查"为什么没推送"用，只读）。"""
    base = now or datetime.now()
    stages = parse_stages(None)
    out: list[dict] = []
    for row in _reminder_rows(base, None, limit):
        deadline = _to_dt(row.get("remind_at"))
        if deadline is None:
            continue
        days_left = (deadline.date() - base.date()).days
        stage = due_stage(days_left, stages)
        out.append({
            "kind": "reminder", "ref_id": int(row["id"]), "user_id": int(row["user_id"]),
            "title": str(row.get("content") or ""), "deadline": _fmt(deadline),
            "days_left": days_left, "stage": stage,
            "pushed": bool(stage and _find_push_notice("reminder", int(row["id"]), stage)),
        })
    for row in _notice_rows(base, limit):
        deadline = _to_dt(row.get("deadline"))
        if deadline is None:
            continue
        days_left = (deadline.date() - base.date()).days
        stage = due_stage(days_left, stages)
        out.append({
            "kind": "notice", "ref_id": int(row["id"]), "title": str(row.get("title") or ""),
            "deadline": _fmt(deadline), "days_left": days_left, "stage": stage,
            "pushed": bool(stage and _find_push_notice("notice", int(row["id"]), stage)),
        })
    return out
