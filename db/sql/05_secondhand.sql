-- ============================================================
-- 模块 M4 · 校园二手循环与共享经济
-- ============================================================
USE xiaojietong;

-- 闲置物品
DROP TABLE IF EXISTS `secondhand_item`;
CREATE TABLE `secondhand_item` (
    `id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `user_id`        BIGINT UNSIGNED NOT NULL COMMENT '卖家 -> user.id',
    `title`          VARCHAR(128)    NOT NULL COMMENT '标题',
    `description`    TEXT            NULL COMMENT '描述',
    `category`       VARCHAR(32)     NOT NULL DEFAULT '' COMMENT '分类：教材/数码/生活/体育/服饰',
    `price`          DECIMAL(10,2)   NOT NULL DEFAULT 0 COMMENT '价格',
    `condition_level` TINYINT        NOT NULL DEFAULT 5 COMMENT '成色 1-10',
    `images_json`    JSON            NULL COMMENT '图片 URL 列表',
    `status`         TINYINT         NOT NULL DEFAULT 0 COMMENT '0在售 1已售 2下架',
    `ai_described`   TINYINT         NOT NULL DEFAULT 0 COMMENT '是否已 AI 辅助生成描述',
    `audit_status`   TINYINT         NOT NULL DEFAULT 0 COMMENT 'AI 审核：0待审 1通过 2拒绝',
    `trust_score`    DECIMAL(5,2)    NOT NULL DEFAULT 50 COMMENT '可信度评分 0-100',
    `view_count`     INT             NOT NULL DEFAULT 0 COMMENT '浏览量',
    `is_deleted`     TINYINT         NOT NULL DEFAULT 0 COMMENT '软删除',
    `created_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_category_status` (`category`, `status`),
    KEY `idx_user` (`user_id`),
    KEY `idx_audit` (`audit_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='二手闲置物品';

-- 求购信息
DROP TABLE IF EXISTS `secondhand_wish`;
CREATE TABLE `secondhand_wish` (
    `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `user_id`    BIGINT UNSIGNED NOT NULL COMMENT '用户 -> user.id',
    `content`    VARCHAR(255)    NOT NULL COMMENT '求购描述',
    `category`   VARCHAR(32)     NOT NULL DEFAULT '' COMMENT '品类',
    `budget`     DECIMAL(10,2)   NOT NULL DEFAULT 0 COMMENT '预算',
    `status`     TINYINT         NOT NULL DEFAULT 0 COMMENT '0求购中 1已达成 2已关闭',
    `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_category_status` (`category`, `status`),
    KEY `idx_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='求购信息';

-- 交易订单
DROP TABLE IF EXISTS `secondhand_order`;
CREATE TABLE `secondhand_order` (
    `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `item_id`    BIGINT UNSIGNED NOT NULL COMMENT '物品 -> secondhand_item.id',
    `buyer_id`   BIGINT UNSIGNED NOT NULL COMMENT '买家 -> user.id',
    `seller_id`  BIGINT UNSIGNED NOT NULL COMMENT '卖家 -> user.id',
    `amount`     DECIMAL(10,2)   NOT NULL DEFAULT 0 COMMENT '成交金额',
    `status`     TINYINT         NOT NULL DEFAULT 0 COMMENT '0待确认 1交易中 2完成 3取消 4退款',
    `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_buyer` (`buyer_id`),
    KEY `idx_seller` (`seller_id`),
    KEY `idx_item` (`item_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='二手交易订单';

-- 物品留言沟通
DROP TABLE IF EXISTS `item_message`;
CREATE TABLE `item_message` (
    `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `item_id`      BIGINT UNSIGNED NOT NULL COMMENT '物品 id',
    `from_user_id` BIGINT UNSIGNED NOT NULL COMMENT '发送者',
    `to_user_id`   BIGINT UNSIGNED NOT NULL COMMENT '接收者',
    `content`      VARCHAR(500)    NOT NULL COMMENT '内容',
    `is_read`      TINYINT         NOT NULL DEFAULT 0 COMMENT '0未读 1已读',
    `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_item` (`item_id`),
    KEY `idx_to_user` (`to_user_id`, `is_read`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='物品留言';
