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
-- 实测事实（2026-09-14）：
--   · 真实表名是 **`takeaway_order`**（`life_order` 不存在 —— 文档曾用错名）
--   · 现有列：id/user_id/merchant_id/items_json/total_amount/delivery_fee/pay_amount/
--             status/address/contact/contact_phone/remark/created_at/updated_at
--   · 现有索引：PRIMARY / idx_merchant / idx_user_status
--   · **只有 3 行数据** ⇒ 回填风险极低
--
-- ⚠️ `biz_type` 回填的**顺序陷阱**：不能直接 `ADD COLUMN biz_type TINYINT NOT NULL DEFAULT 2`，
--   否则 3 行历史"代买"会被一并标成 2（代收）—— **历史数据被打错标**。
--   正确做法是 STEP 2/3/4 三步：先加可空列 → 回填历史为 1 → 再改成 NOT NULL DEFAULT 2。
--
-- 幂等性：`CREATE TABLE IF NOT EXISTS` + 列/索引存在性守卫；回填用 `WHERE biz_type IS NULL`；
--         重复执行不报错、不丢数据、不重复打标。
--
-- 回滚：见文件末尾。
-- ============================================================
USE xiaojietong;

-- ============================================== PART A · pickup_point 建表
CREATE TABLE IF NOT EXISTS `pickup_point` (
    `id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `name`           VARCHAR(128)    NOT NULL                COMMENT '驿站名（如 菜鸟驿站·一食堂店）',
    `address`        VARCHAR(255)    NOT NULL DEFAULT ''     COMMENT '地址 / 位置描述',
    `open_time`    VARCHAR(64)     NOT NULL DEFAULT ''     COMMENT '营业时间（如 07:30-21:00）',
    `campus`       VARCHAR(32)     NOT NULL DEFAULT ''     COMMENT '所属校区（空=全部）',
    `sort`           INT             NOT NULL DEFAULT 0      COMMENT '排序，越大越靠前',
    `enabled`      TINYINT         NOT NULL DEFAULT 1      COMMENT '0 停用 1 启用',
    `created_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_enabled_sort` (`enabled`, `sort`),
    KEY `idx_campus` (`campus`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='取件驿站（代收业务）';

-- 种子（**固定主键 + ON DUPLICATE KEY UPDATE**：可补齐缺行、可更新文案、不重复插入）
INSERT INTO `pickup_point` (`id`, `name`, `address`, `open_time`, `campus`, `sort`, `enabled`) VALUES
  (1, '三教快递柜',       '第三教学楼东侧一层',  '24 小时',     '',         50, 1),
  (2, '中心馆驿站',       '中心图书馆北门旁',    '07:30-21:30', '',         40, 1),
  (3, '行政楼快递站',     '行政楼 108 旁',       '08:30-18:00', '',         30, 1),
  (4, '学生活动中心驿站', '学生活动中心西侧',    '08:00-20:00', '',         20, 1),
  (5, '南区菜鸟驿站',     '南区生活区 3 号楼下', '07:00-22:00', '前卫南区', 10, 1)
ON DUPLICATE KEY UPDATE `name`=VALUES(`name`), `address`=VALUES(`address`), `open_time`=VALUES(`open_time`),
  `campus`=VALUES(`campus`), `sort`=VALUES(`sort`), `enabled`=VALUES(`enabled`);

-- ============================================== PART B · takeaway_order 改造
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

-- 到件时间 / 到件通知时间
SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `takeaway_order` ADD COLUMN `arrived_at` DATETIME NULL DEFAULT NULL COMMENT ''到件时间（驿站签收）'' AFTER `pickup_point_id`',
        'SELECT ''[skip] takeaway_order.arrived_at 已存在'' AS `结果`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'takeaway_order' AND column_name = 'arrived_at'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `takeaway_order` ADD COLUMN `notified_at` DATETIME NULL DEFAULT NULL COMMENT ''到件通知发出时间（幂等：非空即不再重复通知）'' AFTER `arrived_at`',
        'SELECT ''[skip] takeaway_order.notified_at 已存在'' AS `结果`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'takeaway_order' AND column_name = 'notified_at'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- ============================================== PART C · 索引
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

-- ---------------------------------------------------------------- 自检
SELECT column_name AS `列`, column_type AS `类型`, is_nullable AS `可空`, column_comment AS `说明`
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'takeaway_order'
  AND column_name IN ('biz_type', 'pickup_code', 'pickup_point_id', 'arrived_at', 'notified_at')
ORDER BY ordinal_position;

SELECT CONCAT('历史代买存量 = ', SUM(`biz_type` = 1), ' 行，代收 = ', SUM(`biz_type` = 2),
              ' 行，总行数 = ', COUNT(*)) AS `回填自检（历史行必须全是 biz_type=1）`
FROM `takeaway_order`;

-- 驿站自检：真实表用的是 `enabled`（0 停用 / 1 启用），
-- 原写法引用 `status` 会报 ERROR 1054 Unknown column 'status'。
SELECT CONCAT('pickup_point 行数 = ', COUNT(*), '，启用 = ', SUM(`enabled` = 1)) AS `驿站自检`
FROM `pickup_point`;

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
-- ============================================================
