-- ============================================================
-- 模块 M8 扩展 · 通知投递与曝光（成员3 · C7 · 2026-09-10）
--
-- 用途：支撑成员2 的 B10「通知未读汇总 + 精准推荐」。
--
-- 与既有表的区别：
--   · `campus_notice`：通知本身（标题/正文/受众年级等）
--   · `notice_read`  ：只记「已读」回执，无投递/曝光/得分概念
--   · `notice_delivery`：**每位用户 × 每条通知**的投递明细，
--     记录推荐得分、命中标签、曝光/阅读时间，用于：
--       ① 未读数统计（idx_user_read）
--       ② 按得分排序的个性化通知流（idx_user_score）
--       ③ 已读率/触达率运营统计
--
-- 幂等：CREATE TABLE IF NOT EXISTS，可重复执行。
-- ============================================================
USE xiaojietong;

CREATE TABLE IF NOT EXISTS `notice_delivery` (
    `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `notice_id`    BIGINT UNSIGNED NOT NULL COMMENT '通知 -> campus_notice.id',
    `user_id`      BIGINT UNSIGNED NOT NULL COMMENT '接收人 -> user.id',
    `channel`      TINYINT         NOT NULL DEFAULT 1 COMMENT '投递渠道：1 站内 2 小程序订阅消息 3 短信',
    `matched_tags` VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '命中的用户兴趣标签，逗号分隔',
    `score`        DECIMAL(6,3)    NOT NULL DEFAULT 0.000 COMMENT '推荐得分，通知流排序依据',
    `reason`       VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '推荐理由（如「你关注了考研」）',
    `is_exposed`   TINYINT         NOT NULL DEFAULT 0 COMMENT '是否已曝光给用户：0 否 1 是',
    `is_read`      TINYINT         NOT NULL DEFAULT 0 COMMENT '是否已读：0 否 1 是',
    `exposed_at`   DATETIME        NULL DEFAULT NULL COMMENT '首次曝光时间',
    `read_at`      DATETIME        NULL DEFAULT NULL COMMENT '阅读时间',
    `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '投递生成时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_notice_user` (`notice_id`, `user_id`),
    KEY `idx_user_read` (`user_id`, `is_read`),
    KEY `idx_user_score` (`user_id`, `score`),
    KEY `idx_exposed` (`is_exposed`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='通知投递/曝光/已读明细';

-- 执行结果自检
SELECT COUNT(*) AS `notice_delivery 已就绪（当前行数）` FROM `notice_delivery`;
