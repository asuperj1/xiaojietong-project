-- ============================================================
-- 模块 M2 · AI Agent 自动化任务执行
-- ============================================================
USE xiaojietong;

-- Agent 任务（自然语言指令 → 拆解 → 执行 → 状态回执）
DROP TABLE IF EXISTS `agent_task`;
CREATE TABLE `agent_task` (
    `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `user_id`     BIGINT UNSIGNED NOT NULL COMMENT '发起用户 -> user.id',
    `task_type`   VARCHAR(32)     NOT NULL COMMENT 'reserve/remind/query/publish/apply...',
    `title`       VARCHAR(255)    NOT NULL COMMENT '任务描述（用户原始指令）',
    `status`      TINYINT         NOT NULL DEFAULT 0 COMMENT '0待执行 1执行中 2成功 3失败 4已取消',
    `params_json` JSON            NULL COMMENT '工具调用参数',
    `result_json` JSON            NULL COMMENT '执行结果',
    `error_msg`   VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '失败原因/替代方案',
    `started_at`  DATETIME        NULL DEFAULT NULL,
    `finished_at` DATETIME        NULL DEFAULT NULL,
    `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_user_status` (`user_id`, `status`),
    KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Agent 任务';

-- Agent 工具注册表（Function Call 工具清单）
DROP TABLE IF EXISTS `agent_tool`;
CREATE TABLE `agent_tool` (
    `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `name`          VARCHAR(64)     NOT NULL COMMENT '工具名，如 reserve_seat',
    `description`   VARCHAR(255)    NOT NULL COMMENT '功能描述（给模型看）',
    `endpoint`      VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '对应服务路由/方法',
    `params_schema` JSON            NULL COMMENT '参数 JSON Schema',
    `enabled`       TINYINT         NOT NULL DEFAULT 1 COMMENT '0停用 1启用',
    `created_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Agent 工具注册表';

-- 提醒事项（Agent 创建 + 用户手动创建）
DROP TABLE IF EXISTS `reminder`;
CREATE TABLE `reminder` (
    `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `user_id`    BIGINT UNSIGNED NOT NULL COMMENT '用户 id',
    `content`    VARCHAR(255)    NOT NULL COMMENT '提醒内容',
    `remind_at`  DATETIME        NOT NULL COMMENT '提醒时间',
    `is_done`    TINYINT         NOT NULL DEFAULT 0 COMMENT '0未提醒 1已提醒',
    `task_id`    BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '来源 Agent 任务 -> agent_task.id',
    `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_user_done` (`user_id`, `is_done`),
    KEY `idx_remind_at` (`remind_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='提醒事项';
