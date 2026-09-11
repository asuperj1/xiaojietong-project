-- ============================================================
-- 校捷通 · 内容审核词库（B6）
-- 表 audit_word：敏感/可疑词表，驱动 services/audit.py 的规则判定
--   level=2 / action=block  → 命中直接拒绝（audit_status=2 + 错误码 3003）
--   level=1 / action=review → 命中转人工待审（audit_status=0）
-- 执行：导入本文件后无需重启（服务端按 TTL 缓存读取）
-- ============================================================
USE xiaojietong;

DROP TABLE IF EXISTS `audit_word`;
CREATE TABLE `audit_word` (
    `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `word`       VARCHAR(64)     NOT NULL COMMENT '敏感/可疑词',
    `level`      TINYINT         NOT NULL DEFAULT 1 COMMENT '级别：1可疑 2敏感',
    `action`     VARCHAR(16)     NOT NULL DEFAULT 'review' COMMENT '动作：review待审 / block拒绝',
    `category`   VARCHAR(32)     NOT NULL DEFAULT '' COMMENT '类别：违规/广告/辱骂...',
    `enabled`    TINYINT         NOT NULL DEFAULT 1 COMMENT '0停用 1启用',
    `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_word` (`word`),
    KEY `idx_level_action` (`level`, `action`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='内容审核词库';

-- 初始词库（演示最小集，可持续扩充；词表命中为子串匹配）
INSERT INTO `audit_word` (`word`, `level`, `action`, `category`) VALUES
    ('代考',       2, 'block',  '违规'),
    ('代写',       2, 'block',  '违规'),
    ('刷单',       2, 'block',  '违规'),
    ('博彩',       2, 'block',  '违规'),
    ('赌球',       2, 'block',  '违规'),
    ('办证',       2, 'block',  '违规'),
    ('贷款秒批',   2, 'block',  '违规'),
    ('招嫖',       2, 'block',  '违规'),
    ('博眼球',     2, 'block',  '违规'),
    ('加微信',     1, 'review', '广告'),
    ('加QQ',       1, 'review', '广告'),
    ('私聊',       1, 'review', '广告'),
    ('低价出',     1, 'review', '广告'),
    ('刷屏',       1, 'review', '广告'),
    ('返利',       1, 'review', '广告')
ON DUPLICATE KEY UPDATE `level` = VALUES(`level`), `action` = VALUES(`action`);
