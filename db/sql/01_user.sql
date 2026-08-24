-- ============================================================
-- 模块 M0 · 用户体系
-- 设计约定：主键 BIGINT 自增；逻辑外键(仅注释引用，不建物理 FK)；软删除 is_deleted
-- ============================================================
USE xiaojietong;

-- 用户主表
DROP TABLE IF EXISTS `user`;
CREATE TABLE `user` (
    `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `openid`       VARCHAR(64)     NOT NULL COMMENT '微信 openid（登录唯一标识）',
    `unionid`      VARCHAR(64)     NOT NULL DEFAULT '' COMMENT '微信 unionid（开放平台，可空）',
    `phone`        VARCHAR(20)     NOT NULL DEFAULT '' COMMENT '手机号',
    `password_hash` VARCHAR(255)   NOT NULL DEFAULT '' COMMENT '密码哈希（备用账号登录，空=仅微信登录）',
    `nickname`     VARCHAR(64)     NOT NULL DEFAULT '' COMMENT '昵称',
    `avatar`       VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '头像 URL',
    `gender`       TINYINT         NOT NULL DEFAULT 0 COMMENT '0未知 1男 2女',
    `student_no`   VARCHAR(32)     NOT NULL DEFAULT '' COMMENT '学号',
    `major`        VARCHAR(64)     NOT NULL DEFAULT '' COMMENT '专业',
    `grade`        VARCHAR(16)     NOT NULL DEFAULT '' COMMENT '年级（如 2024级）',
    `campus`       VARCHAR(32)     NOT NULL DEFAULT '' COMMENT '校区',
    `role`         TINYINT         NOT NULL DEFAULT 0 COMMENT '0学生 1管理员 2超级管理员',
    `status`       TINYINT         NOT NULL DEFAULT 0 COMMENT '0正常 1禁用',
    `is_deleted`   TINYINT         NOT NULL DEFAULT 0 COMMENT '软删除 0否 1是',
    `last_login_at` DATETIME       NULL DEFAULT NULL COMMENT '最后登录时间',
    `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_openid` (`openid`),
    KEY `idx_role_status` (`role`, `status`),
    KEY `idx_student_no` (`student_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户主表';

-- 用户兴趣标签（供内容/通知精准推荐）
DROP TABLE IF EXISTS `user_tag`;
CREATE TABLE `user_tag` (
    `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `user_id`    BIGINT UNSIGNED NOT NULL COMMENT '用户 id -> user.id',
    `tag`        VARCHAR(32)     NOT NULL COMMENT '兴趣标签',
    `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_user_tag` (`user_id`, `tag`),
    KEY `idx_tag` (`tag`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户兴趣标签';

-- 学生画像（AI 推荐用：专业偏好/可支配时间等）
DROP TABLE IF EXISTS `student_profile`;
CREATE TABLE `student_profile` (
    `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `user_id`      BIGINT UNSIGNED NOT NULL COMMENT '用户 id -> user.id',
    `department`   VARCHAR(64)     NOT NULL DEFAULT '' COMMENT '院系',
    `preferences`  JSON            NULL COMMENT '兴趣偏好 JSON，如 {"categories":["学习","求职"],"quiet":true}',
    `free_time`    JSON            NULL COMMENT '可支配时段 JSON，如 {"weekday":[18,22],"weekend":["全天"]}',
    `study_goal`   VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '学习目标',
    `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='学生画像';
