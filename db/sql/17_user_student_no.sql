-- ============================================================
-- B19 · 学号可写 + 唯一 + 限频 + token 失效（对应 `C22` / `B23`）
--
-- 目标：让 `/user/me` 可绑定学号，并支持
--   · 学号唯一（重复学号被拒）
--   · 修改限频「1 次 / 7 天」→ 需要 `student_no_updated_at`
--   · 登出使旧 token 失效 → 需要 `token_version`
--
-- ⚠️⚠️ 本脚本有一个**必须按顺序做**的坑（任务单给的方案会直接报错）：
--   任务单写「先 `UPDATE user SET student_no=NULL WHERE student_no=''`」，
--   但实测 `user.student_no` 是 **`NOT NULL`**！在 MySQL 8 默认严格模式下，
--   往 NOT NULL 列写 NULL 会直接报 **ERROR 1048**。
--   ⇒ 必须**先 STEP 1 把列改成可空，再 STEP 2 清洗**。顺序颠倒必失败。
--
-- 实测事实（2026-09-14）：
--   · `user.student_no` = `varchar(32) NOT NULL`（没有默认值）
--   · 既有索引 `idx_student_no` **存在但是非唯一**（non_unique = 1）⇒ 要替换，不能直接 ADD
--   · 空串学号 = **64 行**（任务单写 63 —— 少算 1 行，所以**绝不能写死行数**）
--   · 非空重复学号 = **0 组** ⇒ 唯一索引建得起来
--
-- 幂等性：每步都有 `information_schema` 守卫；重复执行不报错、不丢数据。
--
-- 回滚：见文件末尾。
-- ============================================================
USE xiaojietong;

-- --------------------------------------------------- STEP 1/5 列改可空
-- 必须先做这一步：否则 STEP 2 的 `SET student_no = NULL` 会 ERROR 1048。
-- 守卫条件＝「当前是 NOT NULL」（COUNT(*) = 1）才改；已是可空则 skip。
-- 注：原列注释为「学号」；因列语义确实由 NOT NULL 变为可空，此处把新语义补进注释。
SET @ddl := (
    SELECT IF(COUNT(*) = 1,
        'ALTER TABLE `user` MODIFY COLUMN `student_no` VARCHAR(32) NULL DEFAULT NULL COMMENT ''学号（可空=未绑定；唯一索引 uk_student_no）''',
        'SELECT ''[skip] user.student_no 已是可空'' AS `结果`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'user'
      AND column_name = 'student_no' AND is_nullable = 'NO'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- --------------------------------------------------- STEP 2/5 清洗空串
-- '' 在唯一索引下会被当成「同一个学号」而互相冲突 ⇒ 必须转成 NULL
-- （MySQL 唯一索引允许多个 NULL，正好表达"未绑定"语义）。
-- `WHERE student_no = ''` 天然幂等：重跑命中 0 行。
UPDATE `user` SET `student_no` = NULL WHERE `student_no` = '';

-- --------------------------------------------------- STEP 3/5 限频时间戳
SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `user` ADD COLUMN `student_no_updated_at` DATETIME NULL DEFAULT NULL COMMENT ''学号最近修改时间（限频 1 次/7 天）'' AFTER `student_no`',
        'SELECT ''[skip] user.student_no_updated_at 已存在'' AS `结果`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'user' AND column_name = 'student_no_updated_at'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- --------------------------------------------------- STEP 4/5 token 版本号
-- 登出 / 改密时 +1 ⇒ 中间件比对旧 token 里的版本，不等即失效。
-- ⚠️ 老 token 里没有这个字段，后端须**缺省按 0 处理**，否则会把所有人踢下线。
SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `user` ADD COLUMN `token_version` INT NOT NULL DEFAULT 0 COMMENT ''token 版本号，登出 +1 使旧 token 失效''',
        'SELECT ''[skip] user.token_version 已存在'' AS `结果`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'user' AND column_name = 'token_version'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- --------------------------------------------------- STEP 5/5 非唯一 → 唯一
-- 先做两道安全检查，避免脚本硬失败、也避免"静默跳过"：
--   · 已有唯一索引     → skip
--   · 存在重复学号     → blocked（打印明文，提示人工清理后重跑）
--   · 有旧的非唯一索引 → DROP 旧的 + ADD 唯一的（唯一索引同样能服务等值查询，无性能回退）
SET @hasUniq := (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = DATABASE() AND table_name = 'user'
      AND column_name = 'student_no' AND non_unique = 0
);
SET @dupGroups := (
    SELECT COUNT(*) FROM (
        SELECT `student_no` FROM `user`
        WHERE `student_no` IS NOT NULL AND `student_no` <> ''
        GROUP BY `student_no` HAVING COUNT(*) > 1
    ) d
);
SET @hasOldIdx := (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = DATABASE() AND table_name = 'user' AND index_name = 'idx_student_no'
);
SET @ddl := IF(@hasUniq > 0,
    'SELECT ''[skip] user 上已有 student_no 唯一索引'' AS `结果`',
    IF(@dupGroups > 0,
        'SELECT CONCAT(''[blocked] 存在 '', @dupGroups, '' 组重复 student_no，请先人工清理后再重跑本脚本'') AS `结果`',
        IF(@hasOldIdx > 0,
            'ALTER TABLE `user` DROP INDEX `idx_student_no`, ADD UNIQUE INDEX `uk_student_no` (`student_no`)',
            'ALTER TABLE `user` ADD UNIQUE INDEX `uk_student_no` (`student_no`)'))
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- ---------------------------------------------------------------- 自检
SELECT column_name AS `列`, column_type AS `类型`, is_nullable AS `可空`, column_comment AS `说明`
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'user'
  AND column_name IN ('student_no', 'student_no_updated_at', 'token_version')
ORDER BY ordinal_position;

SELECT index_name AS `索引`, IF(non_unique = 0, '唯一', '普通') AS `类型`
FROM information_schema.statistics
WHERE table_schema = DATABASE() AND table_name = 'user' AND column_name = 'student_no'
GROUP BY index_name, non_unique;

SELECT CONCAT('空串学号剩余 = ', SUM(`student_no` = ''), '，已绑定(NULL) = ', SUM(`student_no` IS NULL),
              '，总行数 = ', COUNT(*)) AS `数据自检`
FROM `user`;

-- ============================================================
-- 回滚（需要时手工执行）
-- ------------------------------------------------------------
-- ALTER TABLE `user` DROP INDEX `uk_student_no`;
-- ALTER TABLE `user` ADD INDEX `idx_student_no` (`student_no`);
-- ALTER TABLE `user` DROP COLUMN `token_version`;
-- ALTER TABLE `user` DROP COLUMN `student_no_updated_at`;
-- -- ⚠️ 学号列改回 NOT NULL 前，必须先把 NULL 回填成 ''，否则同样报 1048：
-- -- UPDATE `user` SET `student_no` = '' WHERE `student_no` IS NULL;
-- -- ALTER TABLE `user` MODIFY COLUMN `student_no` VARCHAR(32) NOT NULL DEFAULT '';
-- ============================================================
