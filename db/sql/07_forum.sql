-- ============================================================
-- 模块 M6 · 校园论坛与智能内容治理
-- ============================================================
USE xiaojietong;

-- 帖子
DROP TABLE IF EXISTS `topic`;
CREATE TABLE `topic` (
    `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `author_id`    BIGINT UNSIGNED NOT NULL COMMENT '作者 -> user.id',
    `title`        VARCHAR(128)    NOT NULL COMMENT '标题',
    `content`      TEXT            NOT NULL COMMENT '正文',
    `category`     VARCHAR(32)     NOT NULL DEFAULT '综合' COMMENT '学习/生活/闲置/活动/热点/情感...',
    `like_count`   INT             NOT NULL DEFAULT 0 COMMENT '点赞数',
    `comment_count` INT            NOT NULL DEFAULT 0 COMMENT '评论数',
    `view_count`   INT             NOT NULL DEFAULT 0 COMMENT '浏览数',
    `audit_status` TINYINT         NOT NULL DEFAULT 0 COMMENT 'AI 审核：0待审 1通过 2拒绝',
    `ai_summary`   VARCHAR(500)    NOT NULL DEFAULT '' COMMENT 'AI 生成摘要',
    `is_hot`       TINYINT         NOT NULL DEFAULT 0 COMMENT '是否热点',
    `status`       TINYINT         NOT NULL DEFAULT 0 COMMENT '0正常 1已删除 2锁定',
    `is_deleted`   TINYINT         NOT NULL DEFAULT 0 COMMENT '软删除',
    `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_category_status` (`category`, `status`),
    KEY `idx_hot` (`is_hot`),
    KEY `idx_audit` (`audit_status`),
    KEY `idx_author` (`author_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='论坛帖子';

-- 评论
DROP TABLE IF EXISTS `comment`;
CREATE TABLE `comment` (
    `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `topic_id`   BIGINT UNSIGNED NOT NULL COMMENT '帖子 -> topic.id',
    `parent_id`  BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '父评论（楼中楼）',
    `author_id`  BIGINT UNSIGNED NOT NULL COMMENT '作者 -> user.id',
    `content`    VARCHAR(500)    NOT NULL COMMENT '内容',
    `like_count` INT             NOT NULL DEFAULT 0 COMMENT '点赞数',
    `status`     TINYINT         NOT NULL DEFAULT 0 COMMENT '0正常 1删除',
    `is_deleted` TINYINT         NOT NULL DEFAULT 0 COMMENT '软删除',
    `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_topic` (`topic_id`),
    KEY `idx_author` (`author_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='评论';

-- 点赞记录（通用：帖子/评论/物品）
DROP TABLE IF EXISTS `like_record`;
CREATE TABLE `like_record` (
    `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `user_id`     BIGINT UNSIGNED NOT NULL COMMENT '用户 -> user.id',
    `target_type` VARCHAR(16)     NOT NULL COMMENT 'topic/comment/item...',
    `target_id`   BIGINT UNSIGNED NOT NULL COMMENT '目标 id',
    `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_user_target` (`user_id`, `target_type`, `target_id`),
    KEY `idx_target` (`target_type`, `target_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='点赞记录';

-- 收藏（通用）
DROP TABLE IF EXISTS `favorite`;
CREATE TABLE `favorite` (
    `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `user_id`     BIGINT UNSIGNED NOT NULL COMMENT '用户 -> user.id',
    `target_type` VARCHAR(16)     NOT NULL COMMENT 'topic/merchant/item...',
    `target_id`   BIGINT UNSIGNED NOT NULL COMMENT '目标 id',
    `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_user_target` (`user_id`, `target_type`, `target_id`),
    KEY `idx_target` (`target_type`, `target_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='收藏';

-- 举报
DROP TABLE IF EXISTS `report`;
CREATE TABLE `report` (
    `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `reporter_id` BIGINT UNSIGNED NOT NULL COMMENT '举报人',
    `target_type` VARCHAR(16)     NOT NULL COMMENT 'topic/comment/item/job...',
    `target_id`   BIGINT UNSIGNED NOT NULL COMMENT '目标 id',
    `reason`      VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '举报原因',
    `status`      TINYINT         NOT NULL DEFAULT 0 COMMENT '0待处理 1已处理 2忽略',
    `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_target` (`target_type`, `target_id`),
    KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='举报';
