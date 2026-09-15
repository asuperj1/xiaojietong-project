-- ============================================================
-- B19 · 用户搜索历史表 `user_search_history`（成员4 分工 · 本 PR 由成员3 代为起草）
--
-- 目标：给搜索历史接口 `GET/POST/DELETE /search/history`（B22）与 `C23` 的 DAO 提供存储。
--
-- 字段契约（权威出处）：
--   docs/二阶段整改方案-前端UI重构与后端支撑.md L323（旧编号 C29 / 现 C23）：
--   「含 `user_id`/`keyword`/`created_at`，按 `(user_id, created_at)` 索引」
--
-- 幂等性：`CREATE TABLE IF NOT EXISTS` + `information_schema` 守卫建索引；
--         重复执行不报错、不清空数据（详见 15_home_banner.sql 顶部说明）。
--
-- ⚠️ 给 B22 / C23 DAO 的契约（**务必按此实现，否则会踩 1062 唯一键冲突**）：
--   本表有 `uk_user_keyword (user_id, keyword)` ⇒ **同一用户同一个词只能有一行**。
--   「再次搜索同一个词」不能裸 `INSERT`，必须写成：
--       INSERT INTO user_search_history (user_id, keyword)
--       VALUES (?, ?)
--       ON DUPLICATE KEY UPDATE created_at = CURRENT_TIMESTAMP;   -- 把旧记录"顶"到最前
--   这样「写入自动去重 + 倒序拉取」两条验收口径同时成立。
--
-- 回滚：见文件末尾。
-- ============================================================
USE xiaojietong;

CREATE TABLE IF NOT EXISTS `user_search_history` (
    `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `user_id`    BIGINT UNSIGNED NOT NULL                COMMENT '逻辑外键 -> user.id',
    `keyword`    VARCHAR(128)    NOT NULL                COMMENT '搜索词',
    `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '最后一次搜索时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_user_keyword` (`user_id`, `keyword`) COMMENT '去重：同人同词只留一行',
    KEY `idx_user_created` (`user_id`, `created_at`)    COMMENT '倒序拉取（任务单要求的查询路径）'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户搜索历史';

-- ---------------------------------------------------------------- 自检
SELECT column_name AS `列`, column_type AS `类型`, is_nullable AS `可空`, column_comment AS `说明`
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'user_search_history'
ORDER BY ordinal_position;

SELECT index_name AS `索引`, IF(non_unique = 0, '唯一', '普通') AS `类型`,
       GROUP_CONCAT(column_name ORDER BY seq_in_index) AS `列`
FROM information_schema.statistics
WHERE table_schema = DATABASE() AND table_name = 'user_search_history'
GROUP BY index_name, non_unique;

SELECT CONCAT('user_search_history 行数 = ', COUNT(*)) AS `自检` FROM user_search_history;

-- ============================================================
-- 回滚（需要时手工执行；同样幂等）
-- ------------------------------------------------------------
-- DROP TABLE IF EXISTS `user_search_history`;
-- ============================================================
