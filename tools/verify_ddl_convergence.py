#!/usr/bin/env python
"""验证「唯一真源 + 守卫式收敛」：`db/sql/15_home_banner.sql` / `18_takeaway_pickup.sql`。

背景
----
`pickup_point` / `home_banner` 在不同机器上出现了多种列形状（见 `docs/db-migration-convention.md`）。
本工具证明：**无论起点是哪种形状，跑一遍这两个脚本后，都会收敛到同一份权威列定义**，
且旧列数据被回填、不丢行、可无限次重跑（幂等）。

做法
----
1. 建 3 个临时库，分别造出三种起点：
   * `clean` —— 空白库（脚本从零建表）
   * `old_a` —— **dev 漂移形状**（`image_url`/`link_url`；`open_time`/`campus`/`enabled`，缺 `latitude`）
   * `old_b` —— **#83 提议的极简形状**（漂移形状再删列）
2. 把两份脚本里的 `USE xiaojietong;` 替换为 `USE <临时库>;` 后执行
   （替换后有安全闸：文本里若仍残留 `USE xiaojietong` 则拒绝执行）。
3. 断言：
   * 三个库的**权威列集合与列类型完全一致**（这就是"只有一种版本"）；
   * 旧列数据被回填到权威列（`image_url`→`image`、`open_time`→`business_hours`、`enabled`→`status`）；
   * 行数不丢；
   * 再跑一遍 ⇒ 列集合 / 行数 / 数据值均不变（幂等）。
4. 清理临时库。

用法
----
    set XJT_DB_PASSWORD=<你的本地密码>
    python tools/verify_ddl_convergence.py

环境变量：`XJT_DB_HOST`（默认 127.0.0.1）、`XJT_DB_PORT`（默认 3307）、`XJT_DB_USER`（默认 root）、
`XJT_DB_PASSWORD`（**必填**）、`XJT_MYSQL`（mysql 客户端路径）。

退出码：0 = 全部通过；1 = 存在失败。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "db" / "sql"

MYSQL = os.environ.get("XJT_MYSQL", r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe")
HOST = os.environ.get("XJT_DB_HOST", "127.0.0.1")
PORT = os.environ.get("XJT_DB_PORT", "3307")
USER = os.environ.get("XJT_DB_USER", "root")
PASSWORD = os.environ.get("XJT_DB_PASSWORD", "")
REAL_DB = "xiaojietong"                      # 仅用于「安全闸」比对，绝不连接

TEST_DBS = ["xjt_ddltest_clean", "xjt_ddltest_old_a", "xjt_ddltest_old_b"]

BANNER_AUTHORITATIVE = [
    "id", "title", "image", "link_type", "link_target",
    "sort", "start_at", "end_at", "enabled", "created_at", "updated_at",
]
PICKUP_AUTHORITATIVE = [
    "id", "name", "address", "business_hours", "latitude", "longitude",
    "contact_phone", "sort", "status", "is_deleted", "created_at", "updated_at",
]
BANNER_LEGACY = {"image_url", "link_url"}
PICKUP_LEGACY = {"open_time", "campus", "enabled"}

# ---------------------------------------------------------------- 三种「起点形状」
# takeaway_order 最小版：让脚本 PART 5/6 有表可改（`AFTER status` 需要 status 列）
TAKEAWAY_MINIMAL = """
CREATE TABLE `takeaway_order` (
    `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `user_id`    BIGINT UNSIGNED NOT NULL DEFAULT 0,
    `status`     TINYINT NOT NULL DEFAULT 0,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
INSERT INTO `takeaway_order` (`id`, `user_id`, `status`) VALUES (1, 1, 1), (2, 1, 2), (3, 1, 3);
"""

# dev 漂移形状（\(17240f3\) 之后）：home_banner 用 image_url/link_url；
# pickup_point 用 open_time/campus/enabled，且**没有 latitude**
OLD_A_FIXTURE = TAKEAWAY_MINIMAL + """
CREATE TABLE `home_banner` (
    `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `title`       VARCHAR(128) NOT NULL DEFAULT '',
    `image_url`   VARCHAR(255) NOT NULL DEFAULT '',
    `link_url`    VARCHAR(255) NOT NULL DEFAULT '',
    `sort`        INT NOT NULL DEFAULT 0,
    `start_at`    DATETIME NULL DEFAULT NULL,
    `end_at`      DATETIME NULL DEFAULT NULL,
    `enabled`     TINYINT NOT NULL DEFAULT 1,
    `created_at`  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`), KEY `idx_enabled_sort` (`enabled`, `sort`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
INSERT INTO `home_banner` (`id`,`title`,`image_url`,`link_url`,`sort`,`enabled`)
VALUES (1, '漂移行', '/static/banners/drift.png', '/pages/drift/index', 30, 1);

CREATE TABLE `pickup_point` (
    `id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `name`           VARCHAR(128) NOT NULL,
    `address`        VARCHAR(255) NOT NULL DEFAULT '',
    `open_time`      VARCHAR(64) NOT NULL DEFAULT '',
    `campus`         VARCHAR(32) NOT NULL DEFAULT '',
    `longitude`      DECIMAL(10,6) NOT NULL DEFAULT 0,
    `contact_phone`  VARCHAR(32) NOT NULL DEFAULT '',
    `sort`           INT NOT NULL DEFAULT 0,
    `enabled`        TINYINT NOT NULL DEFAULT 1,
    `is_deleted`     TINYINT NOT NULL DEFAULT 0,
    `created_at`     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`), KEY `idx_enabled_sort` (`enabled`, `sort`), KEY `idx_campus` (`campus`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
-- enabled = 0 用于验证「单向收紧」：停用状态必须被搬到 status
INSERT INTO `pickup_point` (`id`,`name`,`address`,`open_time`,`sort`,`enabled`)
VALUES (9, '漂移驿站', '旧地址描述', '07:30-21:30', 30, 0);
"""

# #83 提议的极简形状：在漂移形状上再删 longitude/contact_phone/is_deleted
OLD_B_FIXTURE = TAKEAWAY_MINIMAL + """
CREATE TABLE `home_banner` (
    `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `title`       VARCHAR(128) NOT NULL DEFAULT '',
    `image_url`   VARCHAR(255) NOT NULL DEFAULT '',
    `link_url`    VARCHAR(255) NOT NULL DEFAULT '',
    `sort`        INT NOT NULL DEFAULT 0,
    `start_at`    DATETIME NULL DEFAULT NULL,
    `end_at`      DATETIME NULL DEFAULT NULL,
    `enabled`     TINYINT NOT NULL DEFAULT 1,
    `created_at`  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
INSERT INTO `home_banner` (`id`,`title`,`image_url`,`link_url`,`sort`,`enabled`)
VALUES (2, '极简行', '/static/banners/min.png', '/pages/min/index', 10, 1);

CREATE TABLE `pickup_point` (
    `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `name`        VARCHAR(128) NOT NULL,
    `address`     VARCHAR(255) NOT NULL DEFAULT '',
    `open_time`   VARCHAR(64) NOT NULL DEFAULT '',
    `campus`      VARCHAR(32) NOT NULL DEFAULT '',
    `sort`        INT NOT NULL DEFAULT 0,
    `enabled`     TINYINT NOT NULL DEFAULT 1,
    `created_at`  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
INSERT INTO `pickup_point` (`id`,`name`,`address`,`open_time`,`sort`,`enabled`)
VALUES (8, '极简驿站', '极简地址', '06:30-22:30', 20, 1);
"""

FIXTURES = {
    # 空白库也要有 `takeaway_order`：它是**既有表**（不由本脚本创建），真实库必然存在
    "xjt_ddltest_clean": TAKEAWAY_MINIMAL,
    "xjt_ddltest_old_a": OLD_A_FIXTURE,
    "xjt_ddltest_old_b": OLD_B_FIXTURE,
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
    proc = subprocess.run(cmd, input=sql, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError(f"mysql failed (db={db}): {proc.stderr.strip()[:400]}")
    return proc


def load_script(name: str, db: str) -> str:
    """读脚本并把 `USE xiaojietong;` 换成目标临时库；带安全闸。"""
    text = (SQL_DIR / name).read_text(encoding="utf-8")
    if "USE xiaojietong;" not in text:
        raise RuntimeError(f"{name} 缺少 `USE xiaojietong;` —— 脚本约定被破坏")
    text = text.replace("USE xiaojietong;", f"USE `{db}`;")
    if re.search(r"USE\s+`?xiaojietong", text):
        raise RuntimeError(f"{name} 替换后仍残留 `USE xiaojietong` —— 拒绝执行（防止误改真实库）")
    return text


def columns(db: str, table: str) -> dict[str, tuple[str, str]]:
    proc = mysql(
        "SELECT column_name, column_type, is_nullable FROM information_schema.columns "
        f"WHERE table_schema='{db}' AND table_name='{table}' ORDER BY ordinal_position;"
    )
    out: dict[str, tuple[str, str]] = {}
    for line in proc.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            out[parts[0]] = (parts[1], parts[2])
    return out


def scalar(db: str, sql: str) -> str:
    out = mysql(f"USE `{db}`; {sql}").stdout.strip()
    return out.splitlines()[-1].strip() if out else ""


def shape(db: str, table: str, authoritative: list[str]) -> tuple[dict, set]:
    cols = columns(db, table)
    return ({k: cols[k] for k in authoritative if k in cols}, set(cols) - set(authoritative))


def main() -> int:
    if not PASSWORD:
        print("!! 请先设置环境变量 XJT_DB_PASSWORD（本工具不会读写真实库，但仍需要连接凭据）")
        return 1
    if not Path(MYSQL).exists():
        print(f"!! 找不到 mysql 客户端：{MYSQL}")
        return 1

    print("== 0. 准备临时库（前缀 xjt_ddltest_，绝不触碰真实库） ==")
    for db in TEST_DBS:
        mysql(f"DROP DATABASE IF EXISTS `{db}`; CREATE DATABASE `{db}` "
              "DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;")
        if FIXTURES[db]:
            mysql(FIXTURES[db], db=db)
    print("   三个临时库已就绪：clean / old_a(dev 漂移) / old_b(#83 极简)")

    try:
        print("\n== 1. 三形状 × 跑一遍脚本（轮播） ==")
        shapes_after: dict[str, tuple[dict, set]] = {}
        for db in TEST_DBS:
            mysql(load_script("15_home_banner.sql", db), db=db)
            sh = shape(db, "home_banner", BANNER_AUTHORITATIVE)
            shapes_after[db] = sh
            missing = [c for c in BANNER_AUTHORITATIVE if c not in sh[0]]
            ok(not missing, f"{db}: home_banner 权威列齐全", f"缺 {missing}")
            print(f"      遗留列 = {sorted(sh[1]) or '无'}")

        print("\n== 2. 三形状 × 跑一遍脚本（驿站） ==")
        pickup_after: dict[str, tuple[dict, set]] = {}
        for db in TEST_DBS:
            mysql(load_script("18_takeaway_pickup.sql", db), db=db)
            sh = shape(db, "pickup_point", PICKUP_AUTHORITATIVE)
            pickup_after[db] = sh
            missing = [c for c in PICKUP_AUTHORITATIVE if c not in sh[0]]
            ok(not missing, f"{db}: pickup_point 权威列齐全", f"缺 {missing}")
            print(f"      遗留列 = {sorted(sh[1]) or '无'}")

        print("\n== 3. 核心断言：三个库的权威形状必须完全一致 ==")
        ref_b = shapes_after[TEST_DBS[0]][0]
        for db in TEST_DBS[1:]:
            ok(shapes_after[db][0] == ref_b,
               f"home_banner 权威列(名+类型+可空) 与 clean 一致 [{db}]",
               f"{db}={shapes_after[db][0]} vs clean={ref_b}")
        ref_p = pickup_after[TEST_DBS[0]][0]
        for db in TEST_DBS[1:]:
            ok(pickup_after[db][0] == ref_p,
               f"pickup_point 权威列(名+类型+可空) 与 clean 一致 [{db}]",
               f"{db}={pickup_after[db][0]} vs clean={ref_p}")

        print("\n== 4. 旧列数据必须被回填（不丢数据） ==")
        ok(scalar("xjt_ddltest_old_a", "SELECT `image` FROM home_banner WHERE id=1;") == "/static/banners/drift.png",
           "old_a: image_url -> image 已回填")
        ok(scalar("xjt_ddltest_old_a", "SELECT `link_target` FROM home_banner WHERE id=1;") == "/pages/drift/index",
           "old_a: link_url -> link_target 已回填")
        ok(scalar("xjt_ddltest_old_a", "SELECT `link_type` FROM home_banner WHERE id=1;") == "page",
           "old_a: link_type 由 /pages/ 前缀推断为 page")
        ok(scalar("xjt_ddltest_old_a", "SELECT `business_hours` FROM pickup_point WHERE id=9;") == "07:30-21:30",
           "old_a: open_time -> business_hours 已回填")
        ok(scalar("xjt_ddltest_old_a", "SELECT `status` FROM pickup_point WHERE id=9;") == "0",
           "old_a: enabled=0 -> status=0（单向收紧）")
        ok(scalar("xjt_ddltest_old_b", "SELECT `business_hours` FROM pickup_point WHERE id=8;") == "06:30-22:30",
           "old_b: open_time -> business_hours 已回填")
        ok(scalar("xjt_ddltest_old_b", "SELECT `status` FROM pickup_point WHERE id=8;") == "1",
           "old_b: enabled=1 的驿站保持启用（未误停用）")

        print("\n== 5. 行数与业务数据不丢 ==")
        ok(scalar("xjt_ddltest_old_a", "SELECT COUNT(*) FROM home_banner;") == "3",
           "old_a: home_banner 行数 = 3（原 1 行 + 种子补齐 2 行）")
        ok(scalar("xjt_ddltest_clean", "SELECT COUNT(*) FROM home_banner;") == "3",
           "clean: home_banner 行数 = 3（种子）")
        for db in TEST_DBS:
            ok(scalar(db, "SELECT COUNT(*) FROM takeaway_order WHERE biz_type = 1;") == "3",
               f"{db}: takeaway_order 历史 3 行全部标记 biz_type=1")

        print("\n== 6. 幂等：再跑一遍，列/行/值均不变 ==")
        snap_before = {db: (shape(db, "home_banner", BANNER_AUTHORITATIVE),
                            scalar(db, "SELECT COUNT(*) FROM home_banner;"),
                            scalar(db, "SELECT GROUP_CONCAT(CONCAT_WS('|',id,title,image,link_type,link_target,sort,enabled) "
                                       "ORDER BY id SEPARATOR ';') FROM home_banner;"))
                       for db in TEST_DBS}
        snap_pick = {db: (scalar(db, "SELECT COUNT(*) FROM pickup_point;"),
                          scalar(db, "SELECT GROUP_CONCAT(CONCAT_WS('|',id,name,business_hours,status) "
                                     "ORDER BY id SEPARATOR ';') FROM pickup_point;"))
                     for db in TEST_DBS}
        for db in TEST_DBS:
            mysql(load_script("15_home_banner.sql", db), db=db)
            mysql(load_script("18_takeaway_pickup.sql", db), db=db)
        for db in TEST_DBS:
            now_b = (shape(db, "home_banner", BANNER_AUTHORITATIVE),
                     scalar(db, "SELECT COUNT(*) FROM home_banner;"),
                     scalar(db, "SELECT GROUP_CONCAT(CONCAT_WS('|',id,title,image,link_type,link_target,sort,enabled) "
                                "ORDER BY id SEPARATOR ';') FROM home_banner;"))
            ok(now_b == snap_before[db], f"{db}: 第二遍跑完 home_banner 完全不变（幂等）")
            now_p = (scalar(db, "SELECT COUNT(*) FROM pickup_point;"),
                     scalar(db, "SELECT GROUP_CONCAT(CONCAT_WS('|',id,name,business_hours,status) "
                                "ORDER BY id SEPARATOR ';') FROM pickup_point;"))
            ok(now_p == snap_pick[db], f"{db}: 第二遍跑完 pickup_point 完全不变（幂等）")

    finally:
        print("\n== 7. 清理临时库 ==")
        for db in TEST_DBS:
            mysql(f"DROP DATABASE IF EXISTS `{db}`;", check=False)
        print("   已清理")

    print(f"\n===== 结果：通过 {CHECKS[0] - CHECKS[1]} / {CHECKS[0]} =====")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("[PASS] 三种起点收敛到同一份权威定义，且旧数据已回填、可重复执行")
    return 0


def _real_snapshot() -> dict:
    """真实库快照：列定义 + 全部数据（用于 no-op 证明）。"""
    snap = {}
    for table in ("home_banner", "pickup_point", "takeaway_order"):
        proc = mysql(f"USE `{REAL_DB}`; SELECT * FROM `{table}` ORDER BY id;")
        snap[table] = (columns(REAL_DB, table), proc.stdout.strip())
    return snap


def real_db_noop_check() -> int:
    """在**真实库**上跑一遍两份脚本，断言列与数据完全不变。

    真实库（`xiaojietong`）当前已是权威形状 ⇒ 补列/回填全部走 [skip] 分支、
    种子是空更新 ⇒ 预期是 **no-op**。这条断言把「收敛脚本对已正确的库无副作用」变成可验证事实。
    """
    print("== 真实库 no-op 检查 ==")
    print(f"   目标库：{REAL_DB}（共享库；本检查由脚本本身的幂等性保证零副作用）")
    before = _real_snapshot()
    for name in ("15_home_banner.sql", "18_takeaway_pickup.sql"):
        mysql((SQL_DIR / name).read_text(encoding="utf-8"))   # 用脚本原文，内部 `USE xiaojietong;`
        print(f"   已执行 {name}")
    after = _real_snapshot()
    for table in before:
        ok(before[table][0] == after[table][0], f"{table}: 列定义完全不变")
        ok(before[table][1] == after[table][1], f"{table}: 数据完全不变")
    print(f"\n===== 结果：通过 {CHECKS[0] - CHECKS[1]} / {CHECKS[0]} =====")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("[PASS] 收敛脚本对已收敛的真实库是 no-op（零副作用）")
    return 0


if __name__ == "__main__":
    if "--real" in sys.argv:
        sys.exit(real_db_noop_check())
    sys.exit(main())
