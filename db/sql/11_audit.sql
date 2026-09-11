-- ============================================================
-- 模块 M9 · 内容安全治理（成员3 · C7 · 2026-09-10）
--
-- 用途：为论坛/二手内容提供「敏感词库 + 审核留痕」，
--       支撑成员2 的 B6 内容审核闭环（待审 → 通过 / 拒绝）。
--
-- 设计说明：
--   · 规则审核为主：命中 level=1 → 直接拒绝；level=2 → 转人工待审；
--   · 可选的模型复核结果写入 audit_log.source=2（source: 1规则 2模型 3人工）；
--   · 与既有 `topic.audit_status` / `secondhand_item.audit_status` 语义对齐：
--     0 待审 / 1 通过 / 2 拒绝。
--
-- 幂等：使用 CREATE TABLE IF NOT EXISTS + ON DUPLICATE KEY UPDATE，
--       可重复执行且不会清空已有数据。
-- ============================================================
USE xiaojietong;

-- ------------------------------------------------------------
-- 敏感词库
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `audit_word` (
    `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `word`       VARCHAR(64)     NOT NULL COMMENT '敏感词/短语',
    `category`   VARCHAR(32)     NOT NULL DEFAULT '通用' COMMENT '分类：广告/引流/虚假/违规/通用',
    `level`      TINYINT         NOT NULL DEFAULT 1 COMMENT '1 禁止（直接拒绝） 2 可疑（转人工待审）',
    `action`     TINYINT         NOT NULL DEFAULT 1 COMMENT '处置：1 拒绝 2 待审 3 打码替换',
    `enabled`    TINYINT         NOT NULL DEFAULT 1 COMMENT '是否启用：1 启用 0 停用',
    `hit_count`  INT             NOT NULL DEFAULT 0 COMMENT '累计命中次数（用于词库运营排序）',
    `remark`     VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '运营备注',
    `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_word` (`word`),
    KEY `idx_level_enabled` (`level`, `enabled`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='内容审核敏感词库';

-- ------------------------------------------------------------
-- 审核留痕（谁、什么内容、谁判定、结论、命中词、是否人工复核）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `audit_log` (
    `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `target_type` VARCHAR(16)     NOT NULL COMMENT 'topic/comment/item/job...',
    `target_id`   BIGINT UNSIGNED NOT NULL COMMENT '目标 id',
    `user_id`     BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '发布者 -> user.id',
    `source`      TINYINT         NOT NULL DEFAULT 1 COMMENT '判定来源：1 规则 2 模型 3 人工',
    `result`      TINYINT         NOT NULL DEFAULT 0 COMMENT '结论：0 待审 1 通过 2 拒绝',
    `hit_words`   VARCHAR(500)    NOT NULL DEFAULT '' COMMENT '命中的敏感词，逗号分隔',
    `reason`      VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '判定理由（模型/人工填写）',
    `reviewer_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '人工复核人 -> user.id，0 表示未复核',
    `cost_ms`     INT             NOT NULL DEFAULT 0 COMMENT '判定耗时（毫秒），便于评估模型审核开销',
    `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    KEY `idx_target` (`target_type`, `target_id`),
    KEY `idx_result_created` (`result`, `created_at`),
    KEY `idx_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='内容审核留痕';

-- ------------------------------------------------------------
-- 种子敏感词（演示用示例词；生产环境请由运营维护完整词库）
-- 说明：这里仅收录「广告/引流/虚假宣传」类示例词，用于演示审核链路。
-- ------------------------------------------------------------
INSERT INTO `audit_word` (`word`, `category`, `level`, `action`, `remark`) VALUES
    ('加微信',     '引流', 1, 1, '站外引流'),
    ('加我微信',   '引流', 1, 1, '站外引流'),
    ('私聊',       '引流', 2, 2, '可能引流，转人工'),
    ('扫码进群',   '引流', 1, 1, '站外引流'),
    ('点击链接',   '引流', 1, 1, '外链风险'),
    ('代写',       '虚假', 1, 1, '学术不端'),
    ('代考',       '虚假', 1, 1, '学术不端'),
    ('包过',       '虚假', 1, 1, '虚假承诺'),
    ('保过',       '虚假', 1, 1, '虚假承诺'),
    ('刷单',       '虚假', 1, 1, '违法兼职'),
    ('刷信誉',     '虚假', 1, 1, '违法兼职'),
    ('日赚',       '虚假', 2, 2, '高收益诱导，转人工'),
    ('躺赚',       '虚假', 2, 2, '高收益诱导，转人工'),
    ('无抵押贷款', '违规', 1, 1, '违规金融'),
    ('办证',       '违规', 1, 1, '违规业务'),
    ('低价出',     '广告', 2, 2, '广告倾向，转人工'),
    ('正品代购',   '广告', 2, 2, '广告倾向，转人工'),
    ('免费领取',   '广告', 2, 2, '诱导性广告，转人工'),
    ('内部资料',   '广告', 2, 2, '可能侵权/广告，转人工')
ON DUPLICATE KEY UPDATE
    `category` = VALUES(`category`),
    `level`    = VALUES(`level`),
    `action`   = VALUES(`action`),
    `remark`   = VALUES(`remark`);

-- 执行结果自检
SELECT CONCAT('audit_word 共 ', COUNT(*), ' 个词（启用 ',
              SUM(`enabled` = 1), '）') AS `audit_word 初始化`
FROM `audit_word`;
