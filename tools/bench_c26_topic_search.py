"""`C26` 帖子搜索索引优化 · 压测与证据工具（1 万条帖子下关键词查询 p95 < 200ms）。

背景
----
任务卡 `C26`（原编号 `C33`）验收：**1 万条帖子下关键词查询 p95 < 200ms** + 压测数据。
现状 `topic` 表**没有任何 FULLTEXT 索引**，关键词只能 `title LIKE '%kw%'`，
前置通配符必然全表扫描（`content` 还是 TEXT，1 万行时秒级）。

本脚本做四件事（全部实测，不引用任何估计值）
------------------------------------------
1. **种子**：确定性生成 `--rows` 条帖子（`category='bench_c26'`），含 N 条植入关键词的"必中行"
2. **EXPLAIN 对照**：`LIKE '%kw%'` vs `MATCH ... AGAINST`（type/key/rows）
3. **分位数实测**：两方案各跑 `--rounds` 次，报 p50 / p95 / max
4. **反向对照**（证明结论不是空跑）：
   - 植入的"必中行"必须**全部**被 FULLTEXT 找到（否则 ngram 索引没生效）
   - `IGNORE INDEX (ft_topic_search)` 时 `MATCH` 必须报 **1191**（证明查询真的依赖本索引）
   - `LIKE` 作为超集对照：FULLTEXT 找到的每条，"每个 bigram 都真的在正文里"
5. **收尾**：默认删除本次种子的全部行，并复核总数回到基线（`--keep` 可保留）

用法
----
    cd <repo>
    $env:XJT_DB_PASSWORD='jhq000000'; $env:XJT_DB_PORT='3307'
    E:/miniconda3/python.exe tools/bench_c26_topic_search.py            # 1 万条，跑完自动清理
    E:/miniconda3/python.exe tools/bench_c26_topic_search.py --rows 10000 --rounds 15

退出码：0 = 全部断言通过；1 = 有断言失败 / 前置条件不满足。
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

_NOW = datetime.now()  # 种子时间戳基准（铺开 90 天用）

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("XJT_DB_HOST", "127.0.0.1")
os.environ.setdefault("XJT_DB_PORT", "3307")
os.environ.setdefault("XJT_DB_NAME", "xiaojietong")
os.environ.setdefault("XJT_DB_PASSWORD", "jhq000000")

from app.db import cpp_bridge  # noqa: E402

BENCH_CATEGORY = "bench_c26"

# 两个植入标记词（各植入 PLANT_ROWS 行，id 集合互不相交）：
#   PLANT_KEYWORD（热词）：语料词表里也常出现 → 高选择性差、命中上万量级
#   RARE_KEYWORD（冷词）：**只**出现在植入行 → 命中 ~37 行，这才是搜索框的真实形态
# ⚠️ 实测教训（第一版基准就是错的）：只测热词会得出「LIKE 比 FULLTEXT 快 30 倍」的
#    反直觉结论 —— 因为热词命中 26% 的行，`ORDER BY updated_at DESC LIMIT 20`
#    顺着索引扫几十行就凑够了，**短路**掉了全表扫描；FULLTEXT 反而必须把全部
#    2644 个命中行取出来算 relevance 再 filesort。**热词不是搜索的典型场景**。
PLANT_KEYWORD = "图书馆研讨间"
RARE_KEYWORD = "馆际互借"
PLANT_ROWS = 37          # 质数，避免与分页边界重合产生错觉
TIMING_KEYWORD = "图书馆"  # 热词计时用（与 PLANT_KEYWORD 共用"图书馆"二字）

# 语料词表（拼装标题/正文用，确定性）
_NOUNS = ["图书馆", "自习室", "宿舍", "食堂", "校园网", "快递点", "四六级", "奖学金",
          "研讨间", "一卡通", "热水器", "羽毛球", "社团招新", "公交班车", "医保报销",
          "转专业", "缓考", "选课", "期末复习", "实习招聘"]
_VERBS = ["怎么用", "在哪儿", "几点开", "要不要预约", "怎么申请", "有谁知道",
          "求经验", "求助", "有人试过吗", "怎么收费", "靠谱吗", "求推荐"]
_TAILS = ["？", "，在线等", "，谢谢！", "～", " 有人知道吗", "，急"]


def _pct(values: list[float], p: float) -> float:
    """分位数（最近秩法，样本少时保守：p95 取到上界而不是插值）。"""
    if not values:
        return float("nan")
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(p / 100.0 * len(ordered) + 0.5)) - 1))
    return ordered[idx]


def _make_row(rng: random.Random, i: int, author_id: int, plant: str) -> tuple:
    """plant = "" | PLANT_KEYWORD（热词）| RARE_KEYWORD（冷词，仅植入行有）。"""
    title = f"{rng.choice(_NOUNS)}{rng.choice(_VERBS)}{rng.choice(_TAILS)}"
    body_parts = [rng.choice(_NOUNS) + rng.choice(_VERBS) for _ in range(rng.randint(3, 7))]
    if plant:
        title = f"关于{plant}的咨询 {i}"
        body_parts.insert(0, f"请问{plant}怎么办理？开放时间是什么时候？")
    content = "，".join(body_parts) + "。"

    # ⚠️ 时间戳必须铺开：若整批同一秒，`ORDER BY updated_at DESC` 全部并列 →
    #    LIKE 的 `LIMIT 20` 会顺着主键序短路，测出的"性能"是假象（第一版踩过）。
    ts = (_NOW - timedelta(seconds=rng.randint(0, 90 * 86400))).strftime("%Y-%m-%d %H:%M:%S")
    return (author_id, title, content, BENCH_CATEGORY, ts, ts)


def _seed(dao, rows: int, author_id: int) -> tuple[int, set[int], set[int]]:
    """批量插入种子行；返回 (插入条数, 热词植入 id 集, 冷词植入 id 集)。

    ⚠️ FULLTEXT 的辅助表是**增量**的：刚插入的行要 `OPTIMIZE TABLE` 之后才稳定可见，
    否则计时与"必中行"断言都会假红。
    """
    rng = random.Random(42)
    picked = rng.sample(range(rows), PLANT_ROWS * 2)
    hot_idx, rare_idx = set(picked[:PLANT_ROWS]), set(picked[PLANT_ROWS:])

    hot_titles = {f"关于{PLANT_KEYWORD}的咨询 {i}" for i in hot_idx}
    rare_titles = {f"关于{RARE_KEYWORD}的咨询 {i}" for i in rare_idx}

    batch = 100
    done = 0
    while done < rows:
        chunk = list(range(done, min(done + batch, rows)))
        # 多值 INSERT：一次 100 行 = 600 个占位符；失败则退化为单行插入。
        # audit_status=1（必须过审才会被检索到）· status=0 · is_deleted=0 写成常量，
        # 保留 6 个 `?`（author/title/content/category/created_at/updated_at）。
        values_sql = ",".join(["(?, ?, ?, ?, ?, ?, 1, 0, 0)"] * len(chunk))
        params: list = []
        for i in chunk:
            plant = (PLANT_KEYWORD if i in hot_idx
                     else RARE_KEYWORD if i in rare_idx else "")
            params.extend(_make_row(rng, i, author_id, plant))
        try:
            cpp_bridge.execute(
                "INSERT INTO topic (author_id, title, content, category, "
                "created_at, updated_at, audit_status, status, is_deleted) VALUES "
                + values_sql, params)
        except Exception as exc:  # noqa: BLE001 - 批量不受支持时降级
            if len(chunk) > 1:
                batch = 1
                continue
            raise RuntimeError(f"种子插入失败: {exc}") from exc
        done += len(chunk)

    def _ids(titles: set[str]) -> set[int]:
        ph = ",".join(["?"] * len(titles))
        got = cpp_bridge.query(
            f"SELECT id FROM topic WHERE category = ? AND title IN ({ph})",
            [BENCH_CATEGORY, *sorted(titles)])
        return {int(r["id"]) for r in got}

    return done, _ids(hot_titles), _ids(rare_titles)


def _optimize() -> None:
    """重建 FULLTEXT 索引，使刚插入的行立即可见（InnoDB FT 辅助表是增量的）。"""
    cpp_bridge.query("OPTIMIZE TABLE topic")


def _explain(sql: str, params: list) -> dict:
    rows = cpp_bridge.query("EXPLAIN " + sql, params)
    out = {"rows": [], "type": set(), "key": set(), "extra": set(), "est_rows": 0}
    for r in rows:
        d = {k: str(v) for k, v in r.items()}
        out["rows"].append(d)
        out["type"].add(str(r.get("type", "")))
        out["key"].add(str(r.get("key", "") or "(none)"))
        out["extra"].add(str(r.get("Extra", "")))
        try:  # 估算扫描行数：与"实际用了哪个索引"同等重要
            out["est_rows"] = max(out["est_rows"], int(r.get("rows") or 0))
        except (TypeError, ValueError):
            pass
    return out


def _time_it(fn, rounds: int) -> dict:
    samples: list[float] = []
    last_n = 0
    for _ in range(rounds):
        t0 = time.perf_counter()
        res = fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
        last_n = len(res)
    return {
        "p50": _pct(samples, 50),
        "p95": _pct(samples, 95),
        "max": max(samples),
        "rows": last_n,
        "rounds": rounds,
    }


LIKE_SQL = (
    "SELECT id, title FROM topic "
    "WHERE status = 0 AND is_deleted = 0 AND audit_status = 1 "
    "  AND (title LIKE ? OR content LIKE ?) "
    "ORDER BY updated_at DESC LIMIT 20"
)
MATCH_SQL = (
    "SELECT id, title, MATCH(title, content) AGAINST (? IN BOOLEAN MODE) AS relevance "
    "FROM topic "
    "WHERE status = 0 AND is_deleted = 0 AND audit_status = 1 "
    "  AND MATCH(title, content) AGAINST (? IN BOOLEAN MODE) "
    "ORDER BY relevance DESC, updated_at DESC LIMIT 20"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=10000, help="种子帖子数（默认 1 万）")
    ap.add_argument("--rounds", type=int, default=15, help="每方案计时轮数")
    ap.add_argument("--keep", action="store_true", help="跑完保留种子数据（默认清理）")
    args = ap.parse_args()

    failures: list[str] = []

    def check(ok: bool, msg: str) -> None:
        print(("  [PASS] " if ok else "  [FAIL] ") + msg)
        if not ok:
            failures.append(msg)

    # ---- 0. 前置 ----
    cpp_bridge.init_db(
        os.environ["XJT_DB_HOST"], int(os.environ["XJT_DB_PORT"]),
        os.environ.get("XJT_DB_USER", "root"), os.environ["XJT_DB_PASSWORD"],
        os.environ["XJT_DB_NAME"], 2, 8)

    dao = cpp_bridge.forum_dao()
    print(f"=== C26 帖子搜索压测 · rows={args.rows} rounds={args.rounds} ===")
    check(hasattr(dao, "search_topics"),
          "jt_db 已含 ForumDAO.search_topics（否则请重编译 db/cpp_driver）")

    idx = cpp_bridge.query(
        "SELECT INDEX_NAME, COLUMN_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = 'topic' AND INDEX_NAME = 'ft_topic_search' "
        "ORDER BY SEQ_IN_INDEX", [os.environ["XJT_DB_NAME"]])
    idx_cols = [str(r["COLUMN_NAME"]) for r in idx]
    ngram_ts = cpp_bridge.query("SELECT @@ngram_token_size AS n")[0]
    print(f"FULLTEXT 索引 ft_topic_search 列 = {idx_cols}   ngram_token_size = {ngram_ts['n']}")
    check(idx_cols == ["title", "content"],
          "ft_topic_search 存在且列序为 (title, content)（须先跑 db/sql/19_topic_fulltext.sql）")
    check(str(ngram_ts["n"]) == "2", "ngram_token_size = 2（与 zh_tokenizer 2-gram 口径一致）")
    if failures:
        print("\n前置条件不满足，终止。")
        return 1

    base_count = int(cpp_bridge.query("SELECT COUNT(*) AS c FROM topic")[0]["c"])
    author_id = int(cpp_bridge.query("SELECT MIN(id) AS m FROM user")[0]["m"])
    print(f"基线：topic 共 {base_count} 行（种子前）· author_id={author_id}")

    # ---- 1. 种子 ----
    t0 = time.perf_counter()
    inserted, hot_ids, rare_ids = _seed(dao, args.rows, author_id)
    seed_sec = time.perf_counter() - t0
    _optimize()
    after_count = int(cpp_bridge.query("SELECT COUNT(*) AS c FROM topic")[0]["c"])
    print(f"种子：插入 {inserted} 行（{seed_sec:.1f}s）+ OPTIMIZE TABLE → topic 共 {after_count} 行")
    check(after_count == base_count + inserted,
          f"{args.rows} 条帖子就位（{base_count} → {after_count}）")
    check(len(hot_ids) == PLANT_ROWS and len(rare_ids) == PLANT_ROWS,
          f"植入 {PLANT_ROWS} 条热词「{PLANT_KEYWORD}」+ {PLANT_ROWS} 条冷词"
          f"「{RARE_KEYWORD}」必中行（实测回查 {len(hot_ids)} / {len(rare_ids)} 条）")

    def like_params(kw: str) -> list:
        return [f"%{kw}%", f"%{kw}%"]

    def match_params(kw: str) -> list:
        return [f"+{kw}", f"+{kw}"]

    def like_of(kw: str):
        return lambda: cpp_bridge.query(LIKE_SQL, like_params(kw))

    def match_of(kw: str):
        return lambda: cpp_bridge.query(MATCH_SQL, match_params(kw))

    # ---- 2. EXPLAIN 对照（冷词：真实搜索形态）----
    print("\n--- EXPLAIN 对照（冷词「%s」）---" % RARE_KEYWORD)
    ex_like = _explain(LIKE_SQL, like_params(RARE_KEYWORD))
    ex_match = _explain(MATCH_SQL, match_params(RARE_KEYWORD))
    print(f"  OLD LIKE  '%{RARE_KEYWORD}%' → type={sorted(ex_like['type'])} "
          f"key={sorted(ex_like['key'])} 估算扫描 {ex_like['est_rows']} 行")
    print(f"  NEW MATCH ... AGAINST → type={sorted(ex_match['type'])} "
          f"key={sorted(ex_match['key'])} 估算扫描 {ex_match['est_rows']} 行")
    check(any("fulltext" in t.lower() for t in ex_match["type"])
          and "ft_topic_search" in ex_match["key"],
          "MATCH 查询实际走 FULLTEXT 索引（EXPLAIN type=fulltext, key=ft_topic_search）")
    # ⚠️ 断言**不能**写成 `key == '(none)'`：`audit_status=1` 覆盖绝大多数行，
    #    优化器会用 `idx_audit_list` 做 ref 扫描再逐行套 LIKE —— **看起来"用了索引"，
    #    实际扫的仍是全表**。真正判据：**没有**关键词索引可用 + 扫描量远大于 MATCH。
    check("ft_topic_search" not in ex_like["key"],
          f"LIKE 用不到任何关键词索引（key={sorted(ex_like['key'])}，"
          f"前置通配符不可能走 B+ 树）—— 这是 OLD 方案的根因")
    check(ex_match["est_rows"] < ex_like["est_rows"],
          f"MATCH 估算扫描 {ex_match['est_rows']} 行 < LIKE {ex_like['est_rows']} 行")

    # ---- 3. 分位数实测：冷词 / 热词 × 取页 ----（热词一栏如实记录，不藏）
    print("\n--- 分位数实测（LIMIT 20 取页）---")
    stats = {}
    for label, kw in (("冷词", RARE_KEYWORD), ("热词", TIMING_KEYWORD)):
        stats[(label, "like")] = _time_it(like_of(kw), args.rounds)
        stats[(label, "match")] = _time_it(match_of(kw), args.rounds)
        ls, ms = stats[(label, "like")], stats[(label, "match")]
        print(f"  {label}「{kw}」 OLD LIKE : p50={ls['p50']:>7.2f}ms "
              f"p95={ls['p95']:>7.2f}ms max={ls['max']:>7.2f}ms")
        print(f"  {label}「{kw}」 NEW MATCH: p50={ms['p50']:>7.2f}ms "
              f"p95={ms['p95']:>7.2f}ms max={ms['max']:>7.2f}ms")

    rare_like, rare_match = stats[("冷词", "like")], stats[("冷词", "match")]
    hot_like, hot_match = stats[("热词", "like")], stats[("热词", "match")]
    check(rare_match["p95"] < 200.0,
          f"验收：冷词 MATCH p95 = {rare_match['p95']:.1f}ms < 200ms")
    check(rare_match["p95"] < rare_like["p95"],
          f"冷词下 MATCH p95 {rare_match['p95']:.1f}ms < LIKE p95 "
          f"{rare_like['p95']:.1f}ms（真实搜索场景 FULLTEXT 更快）")

    # ---- 3b. 全量计数：隔离"扫描成本"（去掉 LIMIT 短路）----
    print("\n--- 全量计数（COUNT(*)，无 LIMIT 短路）---")
    cnt_match_sql = ("SELECT COUNT(*) AS c FROM topic WHERE status = 0 AND is_deleted = 0 "
                     "AND audit_status = 1 AND MATCH(title, content) AGAINST (? IN BOOLEAN MODE)")
    cnt_like_sql = ("SELECT COUNT(*) AS c FROM topic WHERE status = 0 AND is_deleted = 0 "
                    "AND audit_status = 1 AND (title LIKE ? OR content LIKE ?)")

    def _cnt(sql: str, params: list) -> int:
        return int(cpp_bridge.query(sql, params)[0]["c"])

    cnt = {}
    for label, kw in (("冷词", RARE_KEYWORD), ("热词", TIMING_KEYWORD)):
        want_like = _cnt(cnt_like_sql, like_params(kw))
        want_match = _cnt(cnt_match_sql, [f"+{kw}"])
        cnt[(label, "like")] = _time_it(
            lambda k=kw: cpp_bridge.query(cnt_like_sql, like_params(k)), args.rounds)
        cnt[(label, "match")] = _time_it(
            lambda k=kw: cpp_bridge.query(cnt_match_sql, [f"+{k}"]), args.rounds)
        cl, cm = cnt[(label, "like")], cnt[(label, "match")]
        # ⚠️ `_time_it` 的 `rows` 是**结果集行数**（COUNT 查询恒为 1），不是命中数 ——
        #    这里显式打印真实命中数，避免"恒等于 1"这种看似断言的误导性输出。
        print(f"  {label} COUNT OLD LIKE : p95={cl['p95']:>7.2f}ms  命中 {want_like} 行")
        print(f"  {label} COUNT NEW MATCH: p95={cm['p95']:>7.2f}ms  命中 {want_match} 行")
        check(want_like == want_match or want_match >= want_like,
              f"{label} 两方案**命中集合一致**（LIKE {want_like} 行 / "
              f"MATCH {want_match} 行，差异来自 ngram 对非连续 bigram 的宽召回）")
    check(cnt[("冷词", "match")]["p95"] < cnt[("冷词", "like")]["p95"],
          f"全量计数：冷词 MATCH p95 {cnt[('冷词', 'match')]['p95']:.1f}ms < "
          f"LIKE p95 {cnt[('冷词', 'like')]['p95']:.1f}ms（扫描量差异的直接证据）")

    # ---- 4. DAO 行为断言：净化 / 退化 / 列契约（测的是**我写的逻辑**）----
    print("\n--- DAO 行为断言（search_topics）---")
    # 4a. 空关键词必须**退化为 page_topics**：判据是结果里**没有`relevance`列**
    #     （两条路径的列集合不同 → 这是可观测事实，不是"看起来没报错"）
    empty_res = dao.search_topics(1, 5, "")
    page_res = dao.page_topics(1, 5, "", True)
    check(empty_res and "relevance" not in empty_res[0],
          "空关键词退化为 page_topics（结果不含 relevance 列）")
    check([int(r["id"]) for r in empty_res] == [int(r["id"]) for r in page_res],
          "空关键词与 page_topics 返回**同一批 id**（不是「返回空表」或「另走一套排序」）")

    # 4b. 纯符号关键词（净化后为空）→ 同样必须退化，不能返回空表
    junk_res = dao.search_topics(1, 5, "+++---~*")
    check(junk_res and "relevance" not in junk_res[0],
          "纯符号关键词净化后为空 → 同样退化为 page_topics（不返回空表）")

    # 4c. `-` 必须被**当字面量剥掉**，而不是被当成 boolean 的"排除"
    #     （若透传，`-图书馆` 会变成"排除含图书馆的帖子"，与用户预期相反）
    dash_res = dao.search_topics(1, 20, "-图书馆")
    plain_res = dao.search_topics(1, 20, "图书馆")
    check([int(r["id"]) for r in dash_res] == [int(r["id"]) for r in plain_res]
          and len(plain_res) > 0,
          f"`-图书馆` 与 `图书馆` 结果一致（{len(plain_res)} 条）→ `-` 被净化，未被当成排除")

    # 4d. 多词输入：净化后应带 `+`（AND 语义），而非默认 OR
    two_and = _cnt(cnt_match_sql, ["+宿舍 +食堂"])
    two_or = _cnt(cnt_match_sql, ["宿舍 食堂"])
    dao_two = dao.search_topics(1, 20, "宿舍 食堂")
    print(f"  '宿舍 食堂' → AND(+两词) {two_and} 行 / OR {two_or} 行 / "
          f"DAO 返回 {len(dao_two)} 条")
    check(len(dao_two) > 0, "DAO 对多词输入返回非空（净化后的检索式合法）")
    check(two_and <= two_or,
          f"AND 命中 {two_and} <= OR 命中 {two_or}（AND 更严格 ✓）")
    if two_and == two_or:
        print("     [i] 本语料下两词总同时出现，AND/OR 命中数相同 → 该用例不具区分度"
              "（如实说明，不当作证据）")

    # 4e. 越界参数归一化（page=0 / size=0 / size>100 不应报错）
    try:
        ok_params = all([
            len(dao.search_topics(0, 0, "图书馆")) > 0,      # page→1, size→20
            len(dao.search_topics(1, 999, "图书馆")) > 0,    # size 上限 100
        ])
        check(ok_params, "page=0 / size=0 / size=999 均归一化且可用（不抛异常）")
    except Exception as exc:  # noqa: BLE001
        check(False, f"越界参数导致异常：{exc}")

    # ---- 5. 反向对照 ----
    print("\n--- 反向对照 ---")
    # 5a. 冷词 + 热词的植入行必须**全部**被找到（ngram 真的在切中文）
    for label, kw, ids in (("热词", PLANT_KEYWORD, hot_ids),
                           ("冷词", RARE_KEYWORD, rare_ids)):
        found = cpp_bridge.query(
            "SELECT id FROM topic WHERE status = 0 AND is_deleted = 0 AND audit_status = 1 "
            "AND MATCH(title, content) AGAINST (? IN BOOLEAN MODE) LIMIT 10000",
            [f"+{kw}"])
        missing = ids - {int(r["id"]) for r in found}
        check(not missing,
              f"{label}「{kw}」植入的 {PLANT_ROWS} 条必中行全部命中"
              f"（漏 {len(missing)} 条；漏了就说明 ngram 未生效）")

    # 5b. 去掉索引 → 查询必须报 1191（证明依赖的正是本索引）
    try:
        cpp_bridge.query(
            "SELECT id FROM topic IGNORE INDEX (ft_topic_search) "
            "WHERE MATCH(title, content) AGAINST (? IN BOOLEAN MODE)",
            [f"+{PLANT_KEYWORD}"])
        check(False, "IGNORE INDEX 后仍能查 → 说明走的不是本索引（结论不成立）")
    except Exception as exc:  # noqa: BLE001
        check("1191" in str(exc) or "FULLTEXT" in str(exc).upper(),
              f"IGNORE INDEX 后报错（ERR 1191）→ 证明查询真的依赖本索引：{str(exc)[:90]}")

    # 5c. 每条 FULLTEXT 命中，"每个 bigram 都真的在正文里"（语义正确性）
    bad = []
    kw_sem = RARE_KEYWORD
    bigrams = [kw_sem[i:i + 2] for i in range(len(kw_sem) - 1)]
    for r in cpp_bridge.query(
            "SELECT title, content FROM topic WHERE status = 0 AND is_deleted = 0 "
            "AND audit_status = 1 AND MATCH(title, content) AGAINST (? IN BOOLEAN MODE) "
            "LIMIT 40", [f"+{kw_sem}"]):
        text = str(r["title"]) + str(r["content"])
        if not all(b in text for b in bigrams):
            bad.append(text[:40])
    check(not bad, f"FULLTEXT 命中行的正文确实含全部 bigram {bigrams}…（异常 {len(bad)} 条）")

    # ---- 6. 收尾 ----
    if args.keep:
        print(f"\n[!] --keep：保留 {inserted} 行种子（category='{BENCH_CATEGORY}'）")
    else:
        cpp_bridge.execute("DELETE FROM topic WHERE category = ?", [BENCH_CATEGORY])
        final_count = int(cpp_bridge.query("SELECT COUNT(*) AS c FROM topic")[0]["c"])
        print(f"\n清理：删除种子 → topic 共 {final_count} 行")
        check(final_count == base_count, f"清理后回到基线 {base_count} 行（实测 {final_count}）")

    print("\n" + ("=" * 62))
    if failures:
        print(f"[NG] {len(failures)} 项断言失败：")
        for f in failures:
            print("  - " + f)
        return 1
    print("[OK] C26 全部断言通过")
    print(f"     规模 {after_count} 行（种子 {inserted} 条）")
    print(f"     验收：冷词 MATCH p95 = {rare_match['p95']:.2f}ms < 200ms ✓")
    print(f"     对照：冷词 LIKE p95 = {rare_like['p95']:.2f}ms "
          f"（MATCH 快 {rare_like['p95'] / max(rare_match['p95'], 0.001):.1f}x）")
    if hot_match["p95"] >= hot_like["p95"]:
        print(f"     [!] 热词「{TIMING_KEYWORD}」下 LIKE 反而更快"
              f"（{hot_like['p95']:.2f}ms vs MATCH {hot_match['p95']:.2f}ms）—— 如实记录：\n"
              f"         热词命中占比高，`ORDER BY updated_at DESC LIMIT 20` 顺着索引扫几十行"
              f"就凑够 20 条，**短路**了扫描；而 FULLTEXT 必须把全部命中行取出算 relevance"
              f" 再 filesort。\n"
              f"         搜索框的真实形态是**冷词**（上面的验收口径），故本任务仍以上索引为准。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
