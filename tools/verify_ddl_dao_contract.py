#!/usr/bin/env python
"""B19 验收：`db/sql/14~18_*.sql` 的列定义必须与 C++ DAO 引用的列**严格一致**。

为什么需要这个工具
------------------
B19 的验收标准是「**幂等可重跑；与 `C22~C25` / `B20` DAO 字段严格一致**」。
而 DAO 是把列名**硬编码在 SQL 字符串里**的（C++ 层），一旦 DDL 的列名对不上，
运行时就是 `ERROR 1054 Unknown column 'x'` —— 而且往往在合并后才炸。

本工具把这条验收变成**可执行、可复跑**的检查：

1. 从 C++ 源码里提取 DAO 真正引用的列：
   * 列清单常量：`constexpr const char* kXxxColumns = "id, title, ...";`
   * SQL 字面量：`INSERT INTO t (a, b) ...`、`UPDATE t SET a = ? ...`
2. 在**临时库**上用 `db/sql` 的脚本建出权威形状（绝不碰真实库）；
3. 对每条 `(表, 列清单)` 执行 `SELECT <列清单> FROM <表> LIMIT 1`：
   * 成功 → 契约一致；
   * `ERROR 1054` → 不一致，打印缺失列；
4. **反向对照**：同样的检查在「漂移形状」库上**必须失败** ——
   否则说明工具抓不住问题（空跑）。

用法
----
    set XJT_DB_PASSWORD=<你的本地密码>
    python tools/verify_ddl_dao_contract.py                 # 扫当前工作树
    python tools/verify_ddl_dao_contract.py --dao-ref github/feat/c23-c25-dao

`--dao-ref` 可指向任意 git ref（含远程分支），用来检查**尚未合并**的 DAO 分支。

环境变量同 `verify_ddl_convergence.py`。退出码：0 = 契约一致；1 = 存在不一致。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "db" / "sql"
DAO_ROOT = "db/cpp_driver"

MYSQL = os.environ.get("XJT_MYSQL", r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe")
HOST = os.environ.get("XJT_DB_HOST", "127.0.0.1")
PORT = os.environ.get("XJT_DB_PORT", "3307")
USER = os.environ.get("XJT_DB_USER", "root")
PASSWORD = os.environ.get("XJT_DB_PASSWORD", "")

DB_OK = "xjt_contract_ok"      # 权威形状（跑完 db/sql 脚本）
DB_DRIFT = "xjt_contract_drift"  # 漂移形状（17240f3 那套列名）

# 本工具只关心 **B19 交付 / 收敛涉及**的表：其余业务表不在本 PR 范围，
# 它们不存在于临时库会产生 `ERROR 1146` 噪声，掩盖真正要看的 `ERROR 1054`。
B19_TABLES = {
    "home_banner", "pickup_point", "takeaway_order",
    "user_search_history", "user", "campus_notice",
}


def clean_cols(raw: str) -> str | None:
    """把提取到的列清单规范化；含 `"` 说明它跨行拼接、提取不完整 —— 丢弃以免**假通过**。"""
    if '"' in raw:
        return None
    parts = [p.strip().strip("`") for p in raw.split(",")]
    parts = [p for p in parts if re.fullmatch(r"[a-z_][a-z0-9_]*", p)]
    return ", ".join(dict.fromkeys(parts)) if parts else None

# --- B19 交付的脚本；顺序即执行顺序 ---
B19_SCRIPTS = [
    "14_notice_extend.sql",
    "15_home_banner.sql",
    "16_search_history.sql",
    "17_user_student_no.sql",
    "18_takeaway_pickup.sql",
]

# --- 漂移形状 fixture：复刻 17240f3 之后的列名（用于反向对照） ---
DRIFT_FIXTURE = """
CREATE TABLE `home_banner` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, `title` VARCHAR(128) NOT NULL DEFAULT '',
    `image_url` VARCHAR(255) NOT NULL DEFAULT '', `link_url` VARCHAR(255) NOT NULL DEFAULT '',
    `sort` INT NOT NULL DEFAULT 0, `start_at` DATETIME NULL, `end_at` DATETIME NULL,
    `enabled` TINYINT NOT NULL DEFAULT 1,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE `pickup_point` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, `name` VARCHAR(128) NOT NULL,
    `address` VARCHAR(255) NOT NULL DEFAULT '', `open_time` VARCHAR(64) NOT NULL DEFAULT '',
    `campus` VARCHAR(32) NOT NULL DEFAULT '', `longitude` DECIMAL(10,6) NOT NULL DEFAULT 0,
    `contact_phone` VARCHAR(32) NOT NULL DEFAULT '', `sort` INT NOT NULL DEFAULT 0,
    `enabled` TINYINT NOT NULL DEFAULT 1, `is_deleted` TINYINT NOT NULL DEFAULT 0,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE `user_search_history` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, `user_id` BIGINT UNSIGNED NOT NULL,
    `keyword` VARCHAR(128) NOT NULL, `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE `takeaway_order` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, `user_id` BIGINT UNSIGNED NOT NULL DEFAULT 0,
    `merchant_id` BIGINT UNSIGNED NOT NULL DEFAULT 0, `items_json` TEXT,
    `total_amount` DECIMAL(10,2) NOT NULL DEFAULT 0, `delivery_fee` DECIMAL(10,2) NOT NULL DEFAULT 0,
    `pay_amount` DECIMAL(10,2) NOT NULL DEFAULT 0, `status` TINYINT NOT NULL DEFAULT 0,
    `address` VARCHAR(255) NOT NULL DEFAULT '', `contact` VARCHAR(64) NOT NULL DEFAULT '',
    `contact_phone` VARCHAR(32) NOT NULL DEFAULT '', `remark` VARCHAR(255) NOT NULL DEFAULT '',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE `user` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `openid` VARCHAR(64) NOT NULL DEFAULT '', `nickname` VARCHAR(64) NOT NULL DEFAULT '',
    `avatar` VARCHAR(255) NOT NULL DEFAULT '', `phone` VARCHAR(20) NOT NULL DEFAULT '',
    `role` TINYINT NOT NULL DEFAULT 0,
    `student_no` VARCHAR(32) NOT NULL DEFAULT '',
    `major` VARCHAR(64) NOT NULL DEFAULT '', `grade` VARCHAR(32) NOT NULL DEFAULT '',
    `campus` VARCHAR(32) NOT NULL DEFAULT '', `status` TINYINT NOT NULL DEFAULT 1,
    `is_deleted` TINYINT NOT NULL DEFAULT 0,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE `campus_notice` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, `title` VARCHAR(128) NOT NULL DEFAULT '',
    `content` TEXT, `source` VARCHAR(64) NOT NULL DEFAULT '', `category` VARCHAR(32) NOT NULL DEFAULT '',
    `target_grade` VARCHAR(32) NOT NULL DEFAULT '', `publish_time` DATETIME NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# 列清单常量：`constexpr const char* kXxxColumns = "...";`
COLS_CONST_RE = re.compile(r'constexpr\s+const\s+char\s*\*\s*(k\w*Columns)\s*=\s*"([^"]*)"')
# 紧随其后的 SQL 里找表名
FROM_RE = re.compile(r'FROM\s+`?([a-z_][a-z0-9_]*)`?', re.I)
# 直接写在 SQL 字面量里的列清单
INSERT_RE = re.compile(r'INSERT\s+INTO\s+`?([a-z_][a-z0-9_]*)`?\s*\(([^)]*)\)', re.I)
SET_RE = re.compile(r'(\b[a-z_][a-z0-9_]*)\s*=\s*(?:\?|NOW\(\)|IFNULL\(|CURRENT_TIMESTAMP|LAST_INSERT_ID)', re.I)
UPDATE_TABLE_RE = re.compile(r'UPDATE\s+`?([a-z_][a-z0-9_]*)`?\s+SET', re.I)

SQL_NOISE = {
    "select", "from", "where", "and", "or", "order", "by", "limit", "insert", "into",
    "values", "update", "set", "delete", "as", "desc", "asc", "null", "not", "like",
    "count", "now", "ifnull", "current_timestamp", "on", "duplicate", "key", "id",
}

FAILURES: list[str] = []
CHECKS = [0, 0]


def ok(cond: bool, label: str, detail: str = "") -> None:
    CHECKS[0] += 1
    if cond:
        print(f"  [PASS] {label}")
    else:
        CHECKS[1] += 1
        FAILURES.append(label)
        print(f"  [FAIL] {label}" + (f"  -> {detail}" if detail else ""))


def mysql(sql: str, db: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    cmd = [MYSQL, f"--host={HOST}", f"--port={PORT}", f"-u{USER}", f"-p{PASSWORD}",
           "--default-character-set=utf8mb4", "-N", "-B"]
    if db:
        cmd.append(db)
    return subprocess.run(cmd, input=sql, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def load_script(name: str, db: str) -> str:
    text = (SQL_DIR / name).read_text(encoding="utf-8")
    if "USE xiaojietong;" not in text:
        raise RuntimeError(f"{name} 缺少 `USE xiaojietong;` —— 脚本约定被破坏")
    text = text.replace("USE xiaojietong;", f"USE `{db}`;")
    if re.search(r"USE\s+`?xiaojietong", text):
        raise RuntimeError(f"{name} 替换后仍残留 `USE xiaojietong` —— 拒绝执行")
    return text


def git_show(ref: str, path: str) -> str | None:
    """取某个 ref 下的文件内容；HEAD 直接读工作树。"""
    if ref == "HEAD":
        p = ROOT / path
        return p.read_text(encoding="utf-8", errors="replace") if p.exists() else None
    proc = subprocess.run(["git", "-C", str(ROOT), "show", f"{ref}:{path}"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.stdout if proc.returncode == 0 else None


def collect_dao_columns(ref: str) -> list[tuple[str, str, str]]:
    """返回 [(表名, 列清单, 出处)]。"""
    listing = subprocess.run(["git", "-C", str(ROOT), "ls-tree", "-r", "--name-only", ref, "--", DAO_ROOT],
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
    paths = [p for p in listing.stdout.splitlines() if p.endswith((".h", ".cpp"))]

    found: dict[tuple[str, str], str] = {}

    for path in paths:
        text = git_show(ref, path)
        if not text:
            continue
        # 1) 列清单常量 + 就近的 FROM 表名
        for m in COLS_CONST_RE.finditer(text):
            cols = clean_cols(m.group(2))
            tail = text[m.end():m.end() + 600]
            tbl_m = FROM_RE.search(tail)
            if tbl_m and cols:
                found[(tbl_m.group(1).lower(), cols)] = f"{path}: {m.group(1)}"
        # 2) INSERT INTO t (a, b)
        for m in INSERT_RE.finditer(text):
            cols = clean_cols(m.group(2))
            if cols:
                found[(m.group(1).lower(), cols)] = f"{path}: INSERT INTO {m.group(1).lower()}"
        # 3) UPDATE t SET a = ?, b = ?
        for m in UPDATE_TABLE_RE.finditer(text):
            table = m.group(1).lower()
            seg = text[m.end():m.end() + 500]
            cols = ", ".join(dict.fromkeys(c for c in SET_RE.findall(seg)
                                           if c.lower() not in SQL_NOISE))
            if cols:
                found[(table, cols)] = f"{path}: UPDATE {table}"

    # 只保留 B19 相关表（其它表的 ERROR 1146 会淹没真正要看的 ERROR 1054）
    return [(t, c, src) for (t, c), src in sorted(found.items()) if t in B19_TABLES]


def main() -> int:
    if not PASSWORD:
        print("!! 请先设置环境变量 XJT_DB_PASSWORD")
        return 1
    if not Path(MYSQL).exists():
        print(f"!! 找不到 mysql 客户端：{MYSQL}")
        return 1

    ref = "HEAD"
    if "--dao-ref" in sys.argv:
        ref = sys.argv[sys.argv.index("--dao-ref") + 1]
    print(f"== B19 契约检查：DAO ref = {ref} ==")

    print("\n-- 1. 提取 DAO 引用的列 --")
    contracts = collect_dao_columns(ref)
    if not contracts:
        print("   !! 没有从 DAO 提取到任何列清单（路径或 ref 不对？）")
        return 1
    for table, cols, src in contracts:
        print(f"   {table:<22} <- {cols}")
        print(f"       ({src})")

    try:
        print("\n-- 2. 建临时库 --")
        mysql(f"DROP DATABASE IF EXISTS `{DB_OK}`; CREATE DATABASE `{DB_OK}` "
              "DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;")
        # 先铺一份「漂移形状 + 既有表」的底子：14_/17_/18_ 是**加列**脚本，依赖既有表存在；
        # 随后跑 db/sql 脚本把 home_banner/pickup_point 收敛到权威形状。
        mysql(DRIFT_FIXTURE, db=DB_OK)
        for name in B19_SCRIPTS:
            proc = mysql(load_script(name, DB_OK), db=DB_OK)
            if proc.returncode != 0:
                print(f"   !! {name} 执行失败：{(proc.stderr or '').strip().splitlines()[-1:]}")
                return 1
        print(f"   已用 db/sql 的 {len(B19_SCRIPTS)} 个脚本把 {DB_OK} 收敛到权威形状")

        mysql(f"DROP DATABASE IF EXISTS `{DB_DRIFT}`; CREATE DATABASE `{DB_DRIFT}` "
              "DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;")
        mysql(DRIFT_FIXTURE, db=DB_DRIFT)
        print(f"   已建出 {DB_DRIFT}（17240f3 漂移形状，**不**跑脚本，用于反向对照）")

        print("\n-- 3. 核心断言：权威形状上 DAO 的每条列清单都必须可查询 --")
        for table, cols, src in contracts:
            proc = mysql(f"SELECT {cols} FROM `{table}` LIMIT 1;", db=DB_OK)
            ok(proc.returncode == 0,
               f"{table}: DAO 列清单可查询 [{src}]",
               (proc.stderr or "").strip().splitlines()[-1] if proc.stderr else "")

        print("\n-- 4. 反向对照：同样的检查在漂移形状上必须失败 --")
        failed_as_expected = 0
        for table, cols, src in contracts:
            if table not in ("home_banner", "pickup_point"):
                continue      # 只有这两张表发生过漂移
            proc = mysql(f"SELECT {cols} FROM `{table}` LIMIT 1;", db=DB_DRIFT)
            if proc.returncode != 0 and "1054" in (proc.stderr or ""):
                failed_as_expected += 1
        ok(failed_as_expected == 2,
           "漂移形状上 home_banner/pickup_point 的 DAO 列清单确实报 1054（证明检查有效）",
           f"实际报错的表数 = {failed_as_expected}（期望 2）")

    finally:
        print("\n-- 5. 清理临时库 --")
        for db in (DB_OK, DB_DRIFT):
            mysql(f"DROP DATABASE IF EXISTS `{db}`;")
        print("   已清理")

    print(f"\n===== 结果：通过 {CHECKS[0] - CHECKS[1]} / {CHECKS[0]} =====")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("[PASS] DDL 与 DAO 列名严格一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
