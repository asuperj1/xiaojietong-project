-- ============================================================
-- B19 · 代收闭环数据改造（`pickup_point` 建表 + `takeaway_order` 改造 + 历史回填）
--        对应 `C25`；字段契约出处：docs/二阶段整改方案-前端UI重构与后端支撑.md §3.7.1「代收三要素」
--
-- §3.7.1 已确认的三要素（本期做**最小可用 + 字段预留**）：
--   ① 取件码      → `takeaway_order.pickup_code`
--   ② 驿站        → 新建 `pickup_point` 表，「固定取件点**单选**」
--   ③ 到件通知    → `takeaway_order.arrived_at` / `notified_at`
-- 另：「历史代买订单**不删**，加 `biz_type` 区分」（同文档 L301 / L367），「代收**不计费**」（L304）
--
-- ★★ `pickup_point` 的唯一真源（Single Source of Truth）★★
--   PART 1 的 `CREATE TABLE` 即**唯一权威定义**；旧环境形状不一致时用 PART 2/3 的
--   「守卫式收敛」把物理表收敛到本定义，**不得**反向改定义。规范：`docs/db-migration-convention.md`（PR #86）。
--
-- 权威列（文档 §3.7.1 / DAO / 真实库三方对齐）：
--   `id` `name` `address` `business_hours` `latitude` `longitude` `contact_phone`
--   `sort` `status` `is_deleted` `created_at` `updated_at`
--   索引：`PRIMARY`、`idx_status_sort (status, sort)`
--
-- ⚠️ 历史漂移（本次收敛的起因）
--   commit `17240f3` 为迁就某台机器的旧库，把
--     `business_hours` → `open_time`、`status` → `enabled`，**并删掉了 `latitude`**，
--   还多加了 `campus` 列与 `idx_campus` 索引，种子也换成另一套（5 行）数据。
--   后果：`latitude` 与 `longitude` 失配（地图定位成对字段缺一个）、
--   与文档/真实库不一致、`status` 语义被换成 `enabled`。
--   处置：**定义回到权威形状**；旧列用守卫式收敛兼容（回填数据、**不 DROP**），
--   待观察一轮确认无代码引用后再单独清理。
--
-- ⚠️ `takeaway_order.biz_type` 回填的**顺序陷阱**：
--   不能直接 `ADD COLUMN biz_type TINYINT NOT NULL DEFAULT 2`，
--   否则历史"代买"行会被一并标成 2（代收）—— **历史数据被打错标**。
--   正确做法是 STEP 1/2/3 三步：先加可空列 → 回填历史为 1 → 再收紧为 NOT NULL DEFAULT 2。
--
-- 实测事实（2026-09-14）：
--   · 真实表名是 **`takeaway_order`**（`life_order` 不存在 —— 文档曾用错名）
--   · 该表当前只有 **3 行**数据 ⇒ 回填风险极低
--
-- 幂等性：建表 `IF NOT EXISTS` + 列/索引存在性守卫 + 回填限定条件；重复执行不报错、不丢数据。
--
-- 回滚：见文件末尾。
-- ============================================================
USE xiaojietong;

-- ================================================== PART 1 · pickup_point 建表（唯一真源）
CREATE TABLE IF NOT EXISTS `pickup_point` (
    `id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `name`           VARCHAR(128)    NOT NULL                COMMENT '驿站名（如 菜鸟驿站·一食堂店）',
    `address`        VARCHAR(255)    NOT NULL DEFAULT ''     COMMENT '地址 / 位置描述',
    `business_hours` VARCHAR(64)     NOT NULL DEFAULT ''     COMMENT '营业时间',
    `latitude`       DECIMAL(10,6)   NOT NULL DEFAULT 0      COMMENT '纬度',
    `longitude`      DECIMAL(10,6)   NOT NULL DEFAULT 0      COMMENT '经度',
    `contact_phone`  VARCHAR(32)     NOT NULL DEFAULT ''     COMMENT '联系电话',
    `sort`           INT             NOT NULL DEFAULT 0      COMMENT '排序，越大越靠前',
    `status`         TINYINT         NOT NULL DEFAULT 1      COMMENT '1启用 0停用',
    `is_deleted`     TINYINT         NOT NULL DEFAULT 0      COMMENT '软删除 0否 1是',
    `created_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_status_sort` (`status`, `sort`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='快递驿站 / 取件点';

-- ================================================== PART 2 · 收敛：补权威列
-- 表已存在（漂移形状）时建表语句被跳过 ⇒ 缺的列在这里补。
-- ⚠️ `latitude` 是漂移中被**删掉**的列，旧形状里没有对应数据源，
--    只能补列并置默认 0（经纬度成对；置 0 表示"未录入"，待运营补录真实坐标）。

SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `pickup_point` ADD COLUMN `business_hours` VARCHAR(64) NOT NULL DEFAULT '''' COMMENT ''营业时间'' AFTER `address`',
        'SELECT ''[skip] pickup_point.business_hours 已存在'' AS `收敛-补列`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'pickup_point' AND column_name = 'business_hours'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `pickup_point` ADD COLUMN `latitude` DECIMAL(10,6) NOT NULL DEFAULT 0 COMMENT ''纬度'' AFTER `business_hours`',
        'SELECT ''[skip] pickup_point.latitude 已存在'' AS `收敛-补列`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'pickup_point' AND column_name = 'latitude'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `pickup_point` ADD COLUMN `longitude` DECIMAL(10,6) NOT NULL DEFAULT 0 COMMENT ''经度'' AFTER `latitude`',
        'SELECT ''[skip] pickup_point.longitude 已存在'' AS `收敛-补列`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'pickup_point' AND column_name = 'longitude'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `pickup_point` ADD COLUMN `contact_phone` VARCHAR(32) NOT NULL DEFAULT '''' COMMENT ''联系电话'' AFTER `longitude`',
        'SELECT ''[skip] pickup_point.contact_phone 已存在'' AS `收敛-补列`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'pickup_point' AND column_name = 'contact_phone'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `pickup_point` ADD COLUMN `status` TINYINT NOT NULL DEFAULT 1 COMMENT ''1启用 0停用'' AFTER `sort`',
        'SELECT ''[skip] pickup_point.status 已存在'' AS `收敛-补列`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'pickup_point' AND column_name = 'status'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `pickup_point` ADD COLUMN `is_deleted` TINYINT NOT NULL DEFAULT 0 COMMENT ''软删除 0否 1是'' AFTER `status`',
        'SELECT ''[skip] pickup_point.is_deleted 已存在'' AS `收敛-补列`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'pickup_point' AND column_name = 'is_deleted'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- ================================================== PART 3 · 收敛：回填旧列数据
-- 漂移形状的数据在 `open_time` / `enabled` 里，搬到权威列；**不 DROP 旧列**。
-- 回填条件刻意保守，保证可重跑且不覆盖权威数据。

-- 3.1 `open_time` → `business_hours`（仅当权威列为空时）
SET @has := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'pickup_point' AND column_name = 'open_time'
);
SET @sql := IF(@has = 1,
    'UPDATE `pickup_point` SET `business_hours` = `open_time` WHERE (`business_hours` IS NULL OR `business_hours` = '''') AND COALESCE(`open_time`, '''') <> ''''',
    'SELECT ''[skip] 无 open_time 旧列，无需回填'' AS `收敛-回填`');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- 3.2 `enabled` → `status`（**单向收紧**：旧列说"停用"才把权威列也置停用）
--     方向刻意只做 1→0，绝不把 0 放宽成 1 —— 宁可少启用，不可错启用。
SET @has := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'pickup_point' AND column_name = 'enabled'
);
SET @sql := IF(@has = 1,
    'UPDATE `pickup_point` SET `status` = 0 WHERE `status` = 1 AND `enabled` = 0',
    'SELECT ''[skip] 无 enabled 旧列，无需回填'' AS `收敛-回填`');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- 3.3 权威索引守卫：漂移形状建的是 `idx_enabled_sort`，权威索引是 `idx_status_sort`。
--     必须放在 PART 2 补列之后（否则 `status` 不存在会报错）。
SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `pickup_point` ADD KEY `idx_status_sort` (`status`, `sort`)',
        'SELECT ''[skip] idx_status_sort 已存在'' AS `收敛-索引`')
    FROM information_schema.statistics
    WHERE table_schema = DATABASE() AND table_name = 'pickup_point' AND index_name = 'idx_status_sort'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- ================================================== PART 3B · 旧列清理（受控 DROP）
-- 目的：让**所有环境**的 `pickup_point` 列集合完全一致 —— B19「统一全组结构」的硬要求。
-- 前提：PART 3 已把 `open_time` → `business_hours`、`enabled` → `status` 回填完毕。
-- 安全守卫：旧列若仍有"独有数据"就**保留并警告**，绝不静默丢数据。

-- 3B.1 open_time：仅当没有"open_time 有值但 business_hours 为空"的行时才 DROP
SET @has_open_time := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'pickup_point' AND column_name = 'open_time'
);
SET @sql := IF(@has_open_time = 1,
    'SELECT COUNT(*) INTO @orphan FROM `pickup_point` WHERE COALESCE(`open_time`, '''') <> '''' AND COALESCE(`business_hours`, '''') = ''''',
    'SET @orphan := 0');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @ddl := IF(@has_open_time = 0,
    'SELECT ''[skip] 无 open_time 旧列'' AS `收敛-清理`',
    IF(@orphan = 0,
        'ALTER TABLE `pickup_point` DROP COLUMN `open_time`',
        'SELECT ''[warn] open_time 仍有独有数据，保留不删，请人工确认后再清理'' AS `收敛-清理`'));
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- 3B.2 enabled：其语义已被 `status` 完整接管（回填是**单向收紧**，`status=0` 比 `enabled=1` 更保守，
--       不存在"只有 enabled 知道"的信息）⇒ 只要 `status` 在，就可以直接清理。
SET @has_enabled := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'pickup_point' AND column_name = 'enabled'
);
SET @has_status := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'pickup_point' AND column_name = 'status'
);
SET @ddl := IF(@has_enabled = 0,
    'SELECT ''[skip] 无 enabled 旧列'' AS `收敛-清理`',
    IF(@has_status = 1,
        'ALTER TABLE `pickup_point` DROP COLUMN `enabled`',
        'SELECT ''[warn] status 缺失，保留 enabled 不删'' AS `收敛-清理`'));
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- 3B.3 campus：是漂移期**新增**的列，权威定义里没有对应字段 ⇒ 数据无处安放。
--       因此**只有全空**才 DROP；一旦有人填过校区，就保留并警告（交人工决定去留）。
SET @has_campus := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'pickup_point' AND column_name = 'campus'
);
SET @sql := IF(@has_campus = 1,
    'SELECT COUNT(*) INTO @orphan FROM `pickup_point` WHERE COALESCE(`campus`, '''') <> ''''',
    'SET @orphan := 0');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @ddl := IF(@has_campus = 0,
    'SELECT ''[skip] 无 campus 旧列'' AS `收敛-清理`',
    IF(@orphan = 0,
        'ALTER TABLE `pickup_point` DROP COLUMN `campus`',
        'SELECT ''[warn] campus 有非空数据，保留不删（权威定义无此字段），请人工确认去留'' AS `收敛-清理`'));
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- 3B.4 漂移期建的非权威索引：`idx_enabled_sort`（enabled 已删，索引会一并消失）；
--       若因故残留则显式清理，保证索引集合一致。
SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'SELECT ''[skip] 无 idx_enabled_sort 残留'' AS `收敛-清理`',
        'ALTER TABLE `pickup_point` DROP INDEX `idx_enabled_sort`')
    FROM information_schema.statistics
    WHERE table_schema = DATABASE() AND table_name = 'pickup_point' AND index_name = 'idx_enabled_sort'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- ================================================== PART 4 · 种子（幂等 · **空更新**）
-- 固定主键 ⇒ 缺行会补上；`ON DUPLICATE KEY UPDATE id = id` 是**空更新** ⇒
-- 已有行**一个字段都不改**，重跑无副作用，也不会覆盖运营已改的内容（规范 §5.2）。
INSERT INTO `pickup_point` (`id`, `name`, `address`, `business_hours`, `sort`, `status`) VALUES
    (1, '菜鸟驿站·一食堂店', '第一食堂东侧 10 米',       '08:00-20:00', 30, 1),
    (2, '京东快递·图书馆店', '图书馆一层北门内',         '09:00-19:00', 20, 1),
    (3, '顺丰驿站·三公寓店', '第三学生公寓 1 号楼门厅',   '08:30-20:30', 10, 1)
ON DUPLICATE KEY UPDATE `id` = `id`;

-- ================================================== PART 5 · takeaway_order 改造
-- ---------------------------------------------- STEP 1/4 先加**可空** biz_type
SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `takeaway_order` ADD COLUMN `biz_type` TINYINT NULL DEFAULT NULL COMMENT ''业务类型 1=代买(历史) 2=代收(默认)'' AFTER `status`',
        'SELECT ''[skip] takeaway_order.biz_type 已存在'' AS `结果`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'takeaway_order' AND column_name = 'biz_type'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- ---------------------------------------------- STEP 2/4 回填历史为「代买」
-- 此刻列刚加上、全是 NULL ⇒ 把历史行标成 1（代买）。重跑命中 0 行，天然幂等。
UPDATE `takeaway_order` SET `biz_type` = 1 WHERE `biz_type` IS NULL;

-- ---------------------------------------------- STEP 3/4 再收紧为 NOT NULL DEFAULT 2
-- 顺序很重要：必须在 STEP 2 之后，否则历史行会被 DEFAULT 2 打错标。
-- MODIFY 到同一目标定义是幂等的，重复执行无副作用。
ALTER TABLE `takeaway_order`
    MODIFY COLUMN `biz_type` TINYINT NOT NULL DEFAULT 2 COMMENT '业务类型 1=代买(历史) 2=代收(默认)';

-- ---------------------------------------------- STEP 4/4 代收三要素其余列
-- 取件码
SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `takeaway_order` ADD COLUMN `pickup_code` VARCHAR(16) NULL DEFAULT NULL COMMENT ''取件码（代收三要素之一）'' AFTER `biz_type`',
        'SELECT ''[skip] takeaway_order.pickup_code 已存在'' AS `结果`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'takeaway_order' AND column_name = 'pickup_code'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- 驿站（逻辑外键 -> pickup_point.id，不建物理 FK）
SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `takeaway_order` ADD COLUMN `pickup_point_id` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT ''逻辑外键 -> pickup_point.id'' AFTER `pickup_code`',
        'SELECT ''[skip] takeaway_order.pickup_point_id 已存在'' AS `结果`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'takeaway_order' AND column_name = 'pickup_point_id'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- 到件时间
SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `takeaway_order` ADD COLUMN `arrived_at` DATETIME NULL DEFAULT NULL COMMENT ''到件时间（驿站签收）'' AFTER `pickup_point_id`',
        'SELECT ''[skip] takeaway_order.arrived_at 已存在'' AS `结果`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'takeaway_order' AND column_name = 'arrived_at'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- 到件通知时间
SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `takeaway_order` ADD COLUMN `notified_at` DATETIME NULL DEFAULT NULL COMMENT ''到件通知发出时间（幂等：非空即不再重复通知）'' AFTER `arrived_at`',
        'SELECT ''[skip] takeaway_order.notified_at 已存在'' AS `结果`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'takeaway_order' AND column_name = 'notified_at'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- ================================================== PART 6 · takeaway_order 索引
-- 取件码回查（「取件码可生成与回查」是 C25 的验收口径）
SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `takeaway_order` ADD KEY `idx_pickup_code` (`pickup_code`)',
        'SELECT ''[skip] idx_pickup_code 已存在'' AS `结果`')
    FROM information_schema.statistics
    WHERE table_schema = DATABASE() AND table_name = 'takeaway_order' AND index_name = 'idx_pickup_code'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- 代收订单按状态列表（驿站/我的页面）
SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `takeaway_order` ADD KEY `idx_biz_status` (`biz_type`, `status`)',
        'SELECT ''[skip] idx_biz_status 已存在'' AS `结果`')
    FROM information_schema.statistics
    WHERE table_schema = DATABASE() AND table_name = 'takeaway_order' AND index_name = 'idx_biz_status'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- ================================================== PART 7 · 自检
SELECT column_name AS `列`, column_type AS `类型`, is_nullable AS `可空`, column_comment AS `说明`
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'takeaway_order'
  AND column_name IN ('biz_type', 'pickup_code', 'pickup_point_id', 'arrived_at', 'notified_at')
ORDER BY ordinal_position;

SELECT CONCAT('历史代买存量 = ', SUM(`biz_type` = 1), ' 行，代收 = ', SUM(`biz_type` = 2),
              ' 行，总行数 = ', COUNT(*)) AS `回填自检（历史行必须全是 biz_type=1）`
FROM `takeaway_order`;

SELECT CONCAT('pickup_point 行数 = ', COUNT(*), '，启用 = ', SUM(`status` = 1)) AS `驿站自检`
FROM `pickup_point`;

-- 收敛自检：应当只有权威列；若列出 `open_time`/`campus`/`enabled`，说明该库是漂移形状
-- （数据已回填到权威列），旧列留待观察一轮后单独清理。
SELECT IF(COUNT(*) = 0,
          '✅ 无漂移遗留列，形状已统一',
          CONCAT('⚠️ 存在漂移遗留列（数据已回填，待清理）：', GROUP_CONCAT(column_name))) AS `收敛自检-遗留列`
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'pickup_point'
  AND column_name IN ('open_time', 'campus', 'enabled');

-- 权威形状自检：12 列齐全才通过。
SELECT IF(COUNT(*) = 12,
          CONCAT('✅ pickup_point 权威列齐全（', COUNT(*), ' 列）'),
          CONCAT('❌ 权威列缺失，仅有 ', COUNT(*), ' 列，请检查 PART 2 执行结果')) AS `收敛自检-权威列`
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'pickup_point'
  AND column_name IN ('id','name','address','business_hours','latitude','longitude',
                      'contact_phone','sort','status','is_deleted','created_at','updated_at');

-- ============================================================
-- 回滚（需要时手工执行；同样幂等）
-- ------------------------------------------------------------
-- ALTER TABLE `takeaway_order` DROP INDEX `idx_biz_status`;
-- ALTER TABLE `takeaway_order` DROP INDEX `idx_pickup_code`;
-- ALTER TABLE `takeaway_order` DROP COLUMN `notified_at`;
-- ALTER TABLE `takeaway_order` DROP COLUMN `arrived_at`;
-- ALTER TABLE `takeaway_order` DROP COLUMN `pickup_point_id`;
-- ALTER TABLE `takeaway_order` DROP COLUMN `pickup_code`;
-- ALTER TABLE `takeaway_order` DROP COLUMN `biz_type`;
-- DROP TABLE IF EXISTS `pickup_point`;
--
-- 若只想撤掉收敛补的三列（会丢这三列的数据）：
-- ALTER TABLE `pickup_point` DROP COLUMN `status`;
-- ALTER TABLE `pickup_point` DROP COLUMN `latitude`;
-- ALTER TABLE `pickup_point` DROP COLUMN `business_hours`;
-- ============================================================
