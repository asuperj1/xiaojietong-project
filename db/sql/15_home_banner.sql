-- ============================================================
-- B19 · 首页轮播表 `home_banner`（成员4 分工 · 本 PR 由成员3 代为起草）
--
-- 目标：给首页 `GET /home/banners`（B21）提供**可运营**的轮播数据源。
--
-- 字段契约（权威出处）：
--   docs/二阶段整改方案-前端UI重构与后端支撑.md L324（旧编号 C30 / 现 C24）：
--   `title`/`image`/`link_type`/`link_target`/`sort`/`start_at`/`end_at`/`enabled`
--
-- 幂等性：
--   · 建表用 `CREATE TABLE IF NOT EXISTS` —— 重复执行不报错、**不清空数据**；
--     ⚠️ 不要照抄 `09_life.sql` 的 `DROP TABLE IF EXISTS`（那是首次建库脚本），
--        本脚本线上会被重跑，DROP 等于**丢数据**。
--   · 种子用「固定主键 + ON DUPLICATE KEY UPDATE」——重跑只更新、不产生重复行。
--
-- 与 B21 的约定（**待成员2 确认**）：
--   · `sort` 语义假定为**越大越靠前**（`ORDER BY sort DESC`）；若 B21 用升序，改这里即可。
--   · 有效期过滤：`enabled = 1 AND (start_at IS NULL OR start_at <= NOW())
--                              AND (end_at   IS NULL OR end_at   >= NOW())`
--
-- 回滚：见文件末尾。
-- ============================================================
USE xiaojietong;

CREATE TABLE IF NOT EXISTS `home_banner` (
    `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `title`       VARCHAR(128)    NOT NULL DEFAULT ''        COMMENT '轮播标题',
    `image`       VARCHAR(255)    NOT NULL DEFAULT ''        COMMENT '图片 URL',
    `link_type`   VARCHAR(32)     NOT NULL DEFAULT 'none'    COMMENT '跳转类型 none/page/notice/url',
    `link_target` VARCHAR(255)    NOT NULL DEFAULT ''        COMMENT '跳转目标（页面路径 / 公告 id / URL）',
    `sort`        INT             NOT NULL DEFAULT 0         COMMENT '排序，越大越靠前',
    `start_at`    DATETIME        NULL     DEFAULT NULL      COMMENT '生效开始，NULL=立即生效',
    `end_at`      DATETIME        NULL     DEFAULT NULL      COMMENT '生效结束，NULL=永不过期',
    `enabled`     TINYINT         NOT NULL DEFAULT 1         COMMENT '1启用 0停用',
    `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_enabled_sort` (`enabled`, `sort`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='首页轮播';

-- ---------------------------------------------------------------- 种子（幂等）
-- 固定主键 + ON DUPLICATE KEY UPDATE：重跑只更新，不会重复插入。
INSERT INTO `home_banner` (`id`, `title`, `image`, `link_type`, `link_target`, `sort`, `enabled`)
VALUES
    (1, '校园通知新版上线', '/static/images/banner-notice.png', 'page',   '/pages/notice/index', 30, 1),
    (2, 'AI 助手帮你答疑',  '/static/images/banner-ai.png',     'page',   '/pages/ai/index',     20, 1),
    (3, '二手交易更放心',   '/static/images/banner-second.png', 'page',   '/pages/second/index', 10, 1)
ON DUPLICATE KEY UPDATE
    `title`       = VALUES(`title`),
    `image`       = VALUES(`image`),
    `link_type`   = VALUES(`link_type`),
    `link_target` = VALUES(`link_target`),
    `sort`        = VALUES(`sort`),
    `enabled`     = VALUES(`enabled`);

-- ---------------------------------------------------------------- 自检
SELECT column_name AS `列`, column_type AS `类型`, is_nullable AS `可空`, column_comment AS `说明`
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'home_banner'
ORDER BY ordinal_position;

SELECT index_name AS `索引`, GROUP_CONCAT(column_name ORDER BY seq_in_index) AS `列`
FROM information_schema.statistics
WHERE table_schema = DATABASE() AND table_name = 'home_banner'
GROUP BY index_name;

SELECT CONCAT('home_banner 行数 = ', COUNT(*),
              '，其中启用 = ', SUM(enabled = 1)) AS `种子自检`
FROM home_banner;

-- ============================================================
-- 回滚（需要时手工执行；同样幂等）
-- ------------------------------------------------------------
-- DROP TABLE IF EXISTS `home_banner`;
-- ============================================================
