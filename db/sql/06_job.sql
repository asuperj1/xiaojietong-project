-- ============================================================
-- 模块 M5 · 大学生兼职实习诚信服务
-- ============================================================
USE xiaojietong;

-- 企业/雇主
DROP TABLE IF EXISTS `company`;
CREATE TABLE `company` (
    `id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `name`           VARCHAR(128)    NOT NULL COMMENT '企业名',
    `qualification`  VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '资质说明',
    `credit_score`   DECIMAL(5,2)    NOT NULL DEFAULT 50 COMMENT '信用分 0-100',
    `address`        VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '地址',
    `contact`        VARCHAR(64)     NOT NULL DEFAULT '' COMMENT '联系人',
    `contact_phone`  VARCHAR(32)     NOT NULL DEFAULT '' COMMENT '联系电话',
    `is_blacklisted` TINYINT         NOT NULL DEFAULT 0 COMMENT '是否拉黑',
    `created_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_credit` (`credit_score`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='企业';

-- 岗位信息
DROP TABLE IF EXISTS `job_post`;
CREATE TABLE `job_post` (
    `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `company_id`    BIGINT UNSIGNED NOT NULL COMMENT '企业 -> company.id',
    `title`         VARCHAR(128)    NOT NULL COMMENT '岗位名',
    `job_type`      VARCHAR(32)     NOT NULL DEFAULT '' COMMENT '校内勤工/实习/兼职',
    `description`   TEXT            NULL COMMENT '描述',
    `salary`        DECIMAL(10,2)   NOT NULL DEFAULT 0 COMMENT '薪酬',
    `pay_unit`      VARCHAR(16)     NOT NULL DEFAULT '元/时' COMMENT '元/时 元/天 元/月',
    `work_time`     VARCHAR(128)    NOT NULL DEFAULT '' COMMENT '工作时间',
    `location`      VARCHAR(128)    NOT NULL DEFAULT '' COMMENT '工作地点',
    `tags_json`     JSON            NULL COMMENT '标签',
    `risk_level`    TINYINT         NOT NULL DEFAULT 0 COMMENT 'AI 风险等级 0低 1中 2高',
    `trust_score`   DECIMAL(5,2)    NOT NULL DEFAULT 50 COMMENT '可信度评分',
    `status`        TINYINT         NOT NULL DEFAULT 0 COMMENT '0招聘中 1已满 2下架',
    `is_ai_audited` TINYINT         NOT NULL DEFAULT 0 COMMENT '是否已 AI 审核',
    `created_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_type_status` (`job_type`, `status`),
    KEY `idx_company` (`company_id`),
    KEY `idx_risk` (`risk_level`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='兼职实习岗位';

-- 投递记录
DROP TABLE IF EXISTS `job_application`;
CREATE TABLE `job_application` (
    `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `job_id`      BIGINT UNSIGNED NOT NULL COMMENT '岗位 -> job_post.id',
    `user_id`     BIGINT UNSIGNED NOT NULL COMMENT '用户 -> user.id',
    `resume_text` TEXT            NULL COMMENT '投递简历/自我介绍',
    `status`      TINYINT         NOT NULL DEFAULT 0 COMMENT '0待处理 1通过 2拒绝 3已取消',
    `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_job_user` (`job_id`, `user_id`),
    KEY `idx_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='岗位投递';

-- 诚信黑名单（企业/用户）
DROP TABLE IF EXISTS `user_job_blacklist`;
CREATE TABLE `user_job_blacklist` (
    `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `user_id`    BIGINT UNSIGNED NOT NULL COMMENT '用户 -> user.id',
    `company_id` BIGINT UNSIGNED NOT NULL COMMENT '企业 -> company.id',
    `reason`     VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '拉黑原因',
    `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_user_company` (`user_id`, `company_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='兼职诚信黑名单';
