-- ============================================================
-- 模块 M1 · 校园 AI 智能助手（对话）
-- ============================================================
USE xiaojietong;

-- 对话会话
DROP TABLE IF EXISTS `ai_conversation`;
CREATE TABLE `ai_conversation` (
    `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `user_id`    BIGINT UNSIGNED NOT NULL COMMENT '用户 id -> user.id',
    `title`      VARCHAR(100)    NOT NULL DEFAULT '新对话' COMMENT '会话标题（首条消息摘要）',
    `model_name` VARCHAR(64)     NOT NULL DEFAULT 'xjt-model' COMMENT '使用的模型',
    `status`     TINYINT         NOT NULL DEFAULT 0 COMMENT '0进行中 1已归档',
    `is_deleted` TINYINT         NOT NULL DEFAULT 0 COMMENT '软删除',
    `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_user` (`user_id`, `updated_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='AI 对话会话';

-- 对话消息（多轮上下文）
DROP TABLE IF EXISTS `ai_message`;
CREATE TABLE `ai_message` (
    `id`              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `conversation_id` BIGINT UNSIGNED NOT NULL COMMENT '会话 id -> ai_conversation.id',
    `role`            VARCHAR(16)     NOT NULL COMMENT 'user / assistant / system',
    `content`         TEXT            NOT NULL COMMENT '消息内容',
    `is_flagged`      TINYINT         NOT NULL DEFAULT 0 COMMENT '用户标记反馈（点赞/点踩）',
    `created_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_conv` (`conversation_id`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='AI 对话消息';

-- 快捷指令模板
DROP TABLE IF EXISTS `quick_command`;
CREATE TABLE `quick_command` (
    `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `keyword`       VARCHAR(32)     NOT NULL COMMENT '触发词，如 查空教室',
    `title`         VARCHAR(64)     NOT NULL COMMENT '展示名',
    `template`      TEXT            NOT NULL COMMENT '指令模板（发给模型的 prompt）',
    `target_module` VARCHAR(32)     NOT NULL DEFAULT '' COMMENT '关联模块（library/job/...）',
    `enabled`       TINYINT         NOT NULL DEFAULT 1 COMMENT '0停用 1启用',
    `created_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_keyword` (`keyword`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='AI 快捷指令';

-- 用户反馈（回答/服务满意度，数据闭环来源之一）
DROP TABLE IF EXISTS `feedback`;
CREATE TABLE `feedback` (
    `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `user_id`     BIGINT UNSIGNED NOT NULL COMMENT '用户 id',
    `target_type` VARCHAR(16)     NOT NULL COMMENT 'answer/item/topic/job/notice...',
    `target_id`   BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '目标记录 id',
    `rating`      TINYINT         NOT NULL DEFAULT 0 COMMENT '1-5 评分',
    `content`     TEXT            NULL COMMENT '反馈内容',
    `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_target` (`target_type`, `target_id`),
    KEY `idx_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户反馈';
