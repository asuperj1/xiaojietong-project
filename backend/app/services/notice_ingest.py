"""通知入库与抽取接线（B29 / B30）。

本模块把「通知入库」与「信息抽取」接在一起 —— 此前 `extract_deadline`（C27）与
`score_importance`（C28）都已交付，但 `campus_notice` 的入库路径**一个都没调**，
所以 `deadline` / `materials` / `importance` 三列一直是空的。

对外三件事：

| 函数 | 用途 |
|---|---|
| `extract_materials()` / `extract_materials_detail()` | 从正文里抽**材料清单**（C27 只做了时间，材料清单此前无人实现） |
| `ingest_notice()` | **统一入库入口**：写 `campus_notice` 时自动抽取三个扩展字段 |
| `backfill_notices()` | 对**存量**行补抽取并回写（B30 的"回填"） |

两条硬约束（B29/B30 的验收口径）
--------------------------------
1. **抽取失败不阻塞入库**：抽取整段都包在 try/except 里，任何异常都降级为空值，
   入库照常成功 —— 宁可少一个字段，也不能让一条通知写不进去。
   注意区分：**写库本身**失败仍然抛异常（那是真的失败，不该吞）。
2. **扩展列不存在时不写扩展列**：未导入 `14_notice_extend.sql` 的环境里，
   往不存在的列 INSERT 会直接报错，所以按 `notice_extended_columns()` 动态拼列
   —— 与 B20 的读取侧同一口径，保证"没导入 = 行为与旧版完全一致"。

调用方**已知准确值**时（例如 B18 的推送行本来就知道待办到期日），
应通过 `deadline=` / `importance=` 显式传入 —— 别让抽取器去猜系统自己生成的文案。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from app.db import cpp_bridge
from app.services.notice_extract import extract_deadline
from app.services.notice_importance import score_importance
from app.services.notice_scheduler import notice_extended_columns

# --------------------------------------------------------------- 材料清单 ----
#: 材料**标记词**：命中它才认为后面跟的是清单。
#: 刻意不把裸的「材料」当标记 —— "材料科学与工程学院"、"材料力学" 都会误命中。
#: 长、短两种形式都列出来（`需提交材料：…` 与 `需提交：…` 都常见），
#: 匹配时同一位置**取更长的那个**，避免把 `需提交材料` 记成 `需提交` 而漏掉后面的冒号。
_MATERIAL_MARKS = (
    "需要材料", "所需材料", "提交材料", "携带材料", "材料清单", "必备材料", "材料如下",
    "需提交材料", "请提交材料", "须提交材料", "需携带材料", "请携带材料", "须携带材料",
    "需携带", "请携带", "须携带", "需提交", "请提交", "须提交",
)

#: 清单的结束符（截到此为止）
_MATERIAL_STOPS = "。；;！!？?\n\r"

#: 清单长度上下限：太短多半是误命中，太长多半不是一份"清单"（宁可抽不到）
_MATERIAL_MIN, _MATERIAL_MAX = 2, 120

#: 枚举分隔符 —— 有它才认为这是一份"清单"而不是一句描述
_MATERIAL_SEPS = ("、", "，", ",", "/", "；")

#: 标题列宽度（`campus_notice.title` 是 VARCHAR(128)）
MAX_TITLE_CHARS = 128


@dataclass(frozen=True)
class MaterialsResult:
    """材料清单抽取结果（**可解释**：带上命中的标记词与置信度，便于人工复核）。"""

    text: str          # 抽出的清单
    matched: str       # 命中的标记词
    confidence: float  # 0~1：含枚举分隔符 0.9，否则 0.7


def extract_materials_detail(text: str) -> Optional[MaterialsResult]:
    """抽取材料清单；没有明确写成清单就返回 None。

    **只认「标记词 + 冒号」的形式**（如 `需提交材料：成绩单、推荐信`）。
    为什么不支持「请携带身份证、学生证到教务处办理」这类没有冒号的写法：
    中文没有词边界，规则无法可靠判断"清单到哪里结束、说明从哪里开始"——
    按句末截会抽成 `身份证、学生证到教务处办理`，按动词截又会误伤
    （「报到证」里的"到"、"材料力学"里的"材料"）。而**回填时抽错比不抽更糟**
    （会把说明文字写进材料列，还可能被推送到用户面前），所以这里选择漏抽。
    以后要扩，扩展点是明确的：加更可靠的形式，而不是放宽"猜"的范围。
    """
    raw = text or ""
    if not raw.strip():
        return None

    hits = [(idx, mark) for mark in _MATERIAL_MARKS if (idx := raw.find(mark)) >= 0]
    if not hits:
        return None
    # 最靠前的标记优先；同一位置取**更长**的那个（「需提交材料」比「需提交」更具体）
    idx, mark = min(hits, key=lambda t: (t[0], -len(t[1])))

    start = idx + len(mark)
    while start < len(raw) and raw[start] in " \t\u3000":
        start += 1
    if start >= len(raw) or raw[start] not in "：:":
        return None          # 不是「材料：…」这种明确形式 → 不猜（见上方设计说明）
    start += 1
    while start < len(raw) and raw[start] in " \t\u3000":
        start += 1
    end = start
    while end < len(raw) and raw[end] not in _MATERIAL_STOPS:
        end += 1

    candidate = re.sub(r"[ \t\u3000]+", " ", raw[start:end]).strip()
    candidate = candidate.strip("，,、;；:：。.")
    if not (_MATERIAL_MIN <= len(candidate) <= _MATERIAL_MAX):
        return None
    confidence = 0.9 if any(sep in candidate for sep in _MATERIAL_SEPS) else 0.7
    return MaterialsResult(text=candidate, matched=mark, confidence=confidence)


def extract_materials(text: str) -> Optional[str]:
    """便捷入口：只要清单文本，抽不到返回 None。"""
    result = extract_materials_detail(text)
    return result.text if result else None


# ------------------------------------------------------------------ 入库 ----

@dataclass
class IngestResult:
    """一次入库的结果与各字段的来源（便于排查"为什么这列是空的"）。"""

    notice_id: int
    deadline: Optional[datetime] = None
    materials: Optional[str] = None
    importance: Optional[int] = None
    origin: dict[str, str] = field(default_factory=dict)   # 字段 -> given/extract/none/skipped
    errors: list[str] = field(default_factory=list)        # 抽取失败的原因
    notes: list[str] = field(default_factory=list)         # 非致命提示（如标题被截断）

    def as_dict(self) -> dict:
        return {
            "notice_id": self.notice_id,
            "deadline": self.deadline.strftime("%Y-%m-%d %H:%M:%S") if self.deadline else None,
            "materials": self.materials,
            "importance": self.importance,
            "origin": self.origin,
            "errors": self.errors,
            "notes": self.notes,
        }


def _to_sql(value: Any) -> Any:
    """把 datetime 转成 MySQL 能收的字符串；None 保持 None（写入即 SQL NULL）。"""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return value


def _try(fn: Callable[[], Any], label: str, errors: list[str]) -> Any:
    """跑一次抽取，失败就记一笔并返回 None —— **绝不向上抛**（B30 的验收）。"""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - 抽取失败一律降级，不允许拖垮入库
        errors.append(f"{label}: {type(exc).__name__}: {exc}")
        return None


def _blank(value: Any) -> bool:
    """判断「这一列是空的」。

    ⚠️ 必须**同时**认 `None` 与 `""`：jt_db 把 SQL `NULL` 读成**空串**，
    只写 `is None` 会让这类判断恒为假。回填的 `importance` 补写就曾因此
    静默失效 —— 一条都没补上，命令却报告"无变化"、测试也照过
    （因为断言写成了 `x is not None`，对 `''` 恒真）。B21 的两个接口也踩过同一个坑。
    """
    return value is None or value == ""


def ingest_notice(
    *,
    title: str,
    content: str = "",
    source: str = "",
    category: str = "综合",
    target_grade: str = "",
    publish_time: Optional[Any] = None,
    deadline: Optional[datetime] = None,
    materials: Optional[str] = None,
    importance: Optional[int] = None,
    now: Optional[datetime] = None,
) -> IngestResult:
    """统一入库入口：写入 `campus_notice`，并自动抽取 `deadline`/`materials`/`importance`。

    - 显式传入的字段优先（`origin=given`），其余交给抽取器；
    - 抽不到就是 `NULL`（`origin=none`），**不是错误**；
    - 抽取抛异常时记进 `errors` 并降级为空值，**入库照常成功**；
    - 目标环境没有扩展列时跳过这三个字段（`origin=skipped`），
      插入语句里也不会出现它们 —— 与 B20「未导入 = 与旧版一致」的口径相同。
    """
    columns = notice_extended_columns()
    errors: list[str] = []
    notes: list[str] = []
    origin: dict[str, str] = {}
    values: dict[str, Any] = {}

    title = title or ""
    if len(title) > MAX_TITLE_CHARS:
        notes.append(f"标题超过 {MAX_TITLE_CHARS} 字，已截断")
        title = title[:MAX_TITLE_CHARS]

    text = f"{title}\n{content or ''}"

    # ① deadline —— 先算，因为重要度要用它
    deadline_obj: Optional[datetime] = None
    if "deadline" not in columns:
        origin["deadline"] = "skipped"
    elif deadline is not None:
        deadline_obj = deadline
        values["deadline"] = _to_sql(deadline)
        origin["deadline"] = "given"
    else:
        deadline_obj = _try(lambda: extract_deadline(text, now=now), "deadline", errors)
        values["deadline"] = _to_sql(deadline_obj)
        origin["deadline"] = "extract" if deadline_obj else "none"

    # ② materials
    if "materials" not in columns:
        origin["materials"] = "skipped"
    elif materials:
        values["materials"] = materials
        origin["materials"] = "given"
    else:
        got = _try(lambda: extract_materials(text), "materials", errors)
        values["materials"] = got
        origin["materials"] = "extract" if got else "none"

    # ③ importance（依赖 deadline，所以放最后）
    if "importance" not in columns:
        origin["importance"] = "skipped"
    elif importance is not None:
        values["importance"] = int(importance)
        origin["importance"] = "given"
    else:
        got = _try(
            lambda: score_importance(title, content or "", deadline=values.get("deadline"),
                                     now=now),
            "importance", errors,
        )
        values["importance"] = int(got.score) if got else None
        origin["importance"] = "extract" if got else "none"

    base_columns = ["title", "content", "source", "category", "target_grade", "publish_time"]
    extra_columns = [c for c in ("deadline", "materials", "importance") if c in columns]
    all_columns = base_columns + extra_columns

    params: list[Any] = [
        title, content or "", source, category, target_grade,
        _to_sql(publish_time) if publish_time is not None
        else datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ]
    params += [values.get(c) for c in extra_columns]

    _, notice_id = cpp_bridge.execute(
        f"INSERT INTO campus_notice ({', '.join(all_columns)}) "
        f"VALUES ({', '.join('?' for _ in all_columns)})",
        params,
    )

    return IngestResult(
        notice_id=int(notice_id or 0),
        deadline=deadline_obj,
        materials=values.get("materials"),
        importance=values.get("importance"),
        origin=origin,
        errors=errors,
        notes=notes,
    )


# ------------------------------------------------------------------ 回填 ----

def backfill_notices(
    *,
    limit: int = 0,
    dry_run: bool = False,
    only_missing: bool = True,
    now: Optional[datetime] = None,
) -> dict:
    """对**存量**通知补抽取并回写（B30 的"回填"）。

    - `only_missing=True`（默认）只挑「三列里有空值的」行，且**逐列只补空的那一列**
      —— 已经抽好的值不会被覆盖；
    - `dry_run=True` 只返回将要写入的内容，不落库；
    - 未导入 `14_notice_extend.sql` 时返回 `supported=False` 并**不做任何事**
      （而不是报错）。

    抽取失败同样不阻塞：该行跳过、记进 `errors`，其余行照常处理。
    """
    columns = [c for c in ("deadline", "materials", "importance")
               if c in notice_extended_columns()]
    if not columns:
        return {"supported": False, "scanned": 0, "updated": 0, "unchanged": 0,
                "errors": [], "dry_run": dry_run, "preview": []}

    where = " OR ".join(f"`{c}` IS NULL" for c in columns) if only_missing else "1 = 1"
    sql = (f"SELECT id, title, content, {', '.join(columns)} FROM campus_notice "
           f"WHERE {where} ORDER BY id")
    if limit > 0:
        sql += f" LIMIT {int(limit)}"
    rows = cpp_bridge.query(sql)

    updated = unchanged = 0
    errors: list[str] = []
    preview: list[dict] = []

    for row in rows:
        nid = int(row.get("id") or 0)
        title = str(row.get("title") or "")
        content = str(row.get("content") or "")
        text = f"{title}\n{content}"
        patch: dict[str, Any] = {}

        if "deadline" in columns and _blank(row.get("deadline")):
            got = _try(lambda: extract_deadline(text, now=now), f"#{nid}.deadline", errors)
            if got:
                patch["deadline"] = _to_sql(got)
        if "materials" in columns and _blank(row.get("materials")):
            got = _try(lambda: extract_materials(text), f"#{nid}.materials", errors)
            if got:
                patch["materials"] = got
        if "importance" in columns and _blank(row.get("importance")):
            got = _try(
                lambda: score_importance(title, content,
                                         deadline=patch.get("deadline") or row.get("deadline"),
                                         now=now),
                f"#{nid}.importance", errors,
            )
            if got:
                patch["importance"] = int(got.score)

        if not patch:
            unchanged += 1
            continue
        if len(preview) < 20:
            preview.append({"id": nid, "title": title[:40], **patch})
        if dry_run:
            updated += 1
            continue

        assignments = ", ".join(f"`{k}` = ?" for k in patch)
        affected, _ = cpp_bridge.execute(
            f"UPDATE campus_notice SET {assignments} WHERE id = ?",
            [*patch.values(), nid],
        )
        updated += 1 if affected else 0

    return {
        "supported": True,
        "scanned": len(rows),
        "updated": updated,
        "unchanged": unchanged,
        "errors": errors,
        "dry_run": dry_run,
        "preview": preview,
    }
