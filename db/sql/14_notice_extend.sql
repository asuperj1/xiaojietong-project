-- ============================================================
-- B19 · 通知表结构扩展（成员2 · 二阶段 W1 关键路径）
--
-- 目标：为二阶段「方向 1 · 轻量信息抽取」预留**存储位**——
--       抽取出的「截止时间 / 材料清单 / 重要度」三个结果字段。
--
-- 关键事实（来自 docs/成员任务单-二阶段整改260912.md §0.1 事实纠错）：
--   · 表名是 **`campus_notice`**，**不存在** `notice` 表；
--   · 「受众」字段已以 **`target_grade`** 存在，**不需要**再叫 `target`；
--   · 因此本脚本只加 **3 个字段**：`deadline` / `materials` / `importance`；
--   · 文件名用 **14_** 前缀：`13_index_optimize.sql` 已占用 13，不能重号。
--
-- 幂等性：MySQL 8 没有 `ADD COLUMN IF NOT EXISTS`（那是 MariaDB 语法），
--         故用 information_schema 判定 + 预处理语句动态执行；
--         重复执行只会打印「已存在，跳过」，不会报错、不会丢数据。
-- 兼容性：3 个字段**全部允许 NULL**，现有行保持 NULL——
--         不破坏既有查询/接口（`SELECT *` 的调用方会多出 3 个 NULL 列）。
--
-- 回滚：见文件末尾「回滚脚本」（DROP COLUMN，同样幂等）。
--
-- 与 B18 的关系：`services/notice_scheduler.py` 用 information_schema 探测
--   `deadline` 是否存在——**未导入本脚本时自动跳过**「带截止时间通知」这一类推送来源，
--   导入后**无需改代码**即自动启用（实测见 docs/api.md v1.18）。
-- ============================================================
USE xiaojietong;

-- ---------------------------------------------------------------- 1/3 deadline
SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `campus_notice` ADD COLUMN `deadline` DATETIME NULL DEFAULT NULL COMMENT ''截止时间（抽取结果：报名/申请/提交截止）'' AFTER `publish_time`',
        'SELECT ''[skip] campus_notice.deadline 已存在'' AS `结果`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'campus_notice' AND column_name = 'deadline'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- --------------------------------------------------------------- 2/3 materials
-- 材料清单（抽取结果）：文本保存，便于直接展示；多条用换行/顿号分隔。
SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `campus_notice` ADD COLUMN `materials` TEXT NULL COMMENT ''办理材料清单（抽取结果，多条换行分隔）'' AFTER `deadline`',
        'SELECT ''[skip] campus_notice.materials 已存在'' AS `结果`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'campus_notice' AND column_name = 'materials'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- -------------------------------------------------------------- 3/3 importance
-- 重要度 1~5（抽取/打分结果）：NULL = 未打分，与「0 分」区分开。
SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `campus_notice` ADD COLUMN `importance` TINYINT NULL DEFAULT NULL COMMENT ''重要度 1~5（抽取/打分结果），NULL=未打分'' AFTER `materials`',
        'SELECT ''[skip] campus_notice.importance 已存在'' AS `结果`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'campus_notice' AND column_name = 'importance'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- ------------------------------------------------------------------- 索引
-- `deadline` 是 B18 分层推送与 B23/B24 抽取回填的查询条件，必须建索引，
-- 否则扫描全表（campus_notice 未来会随采集器增长）。
SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `campus_notice` ADD KEY `idx_deadline` (`deadline`)',
        'SELECT ''[skip] idx_deadline 已存在'' AS `结果`')
    FROM information_schema.statistics
    WHERE table_schema = DATABASE() AND table_name = 'campus_notice' AND index_name = 'idx_deadline'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- ============================================================
-- 自检（应看到 3 个新列 + 1 个新索引；现有行数据不受影响）
-- ============================================================
SELECT column_name AS `新增列`, column_type AS `类型`, is_nullable AS `可空`, column_comment AS `说明`
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'campus_notice'
  AND column_name IN ('deadline', 'materials', 'importance')
ORDER BY ordinal_position;

SELECT index_name AS `新增索引`, GROUP_CONCAT(column_name) AS `列`
FROM information_schema.statistics
WHERE table_schema = DATABASE() AND table_name = 'campus_notice' AND index_name = 'idx_deadline'
GROUP BY index_name;

SELECT COUNT(*) AS `campus_notice 行数（应保持不变）`,
       SUM(deadline IS NOT NULL) AS `已有截止时间的行数`,
       SUM(importance IS NOT NULL) AS `已有重要度的行数`
FROM campus_notice;

-- ============================================================
-- 回滚脚本（需要时手工执行；同样幂等）
-- ------------------------------------------------------------
-- SET @ddl := (SELECT IF(COUNT(*) > 0,
--     'ALTER TABLE `campus_notice` DROP COLUMN `importance`',
--     'SELECT ''[skip] importance 不存在''') FROM information_schema.columns
--     WHERE table_schema = DATABASE() AND table_name = 'campus_notice' AND column_name = 'importance');
-- PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;
-- （deadline / materials / idx_deadline 同法，改成对应列名即可）
-- ============================================================
