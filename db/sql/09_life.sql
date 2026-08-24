-- ============================================================
-- 模块 M8 · 校园生活一站式服务（外卖/通知/基础服务）
-- ============================================================
USE xiaojietong;

-- 商家（校内及周边）
DROP TABLE IF EXISTS `merchant`;
CREATE TABLE `merchant` (
    `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `name`          VARCHAR(128)    NOT NULL COMMENT '商家名',
    `category`      VARCHAR(32)     NOT NULL DEFAULT '' COMMENT '食堂/餐厅/超市/奶茶...',
    `address`       VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '地址',
    `latitude`      DECIMAL(10,6)   NOT NULL DEFAULT 0 COMMENT '纬度',
    `longitude`     DECIMAL(10,6)   NOT NULL DEFAULT 0 COMMENT '经度',
    `delivery_fee`  DECIMAL(10,2)   NOT NULL DEFAULT 0 COMMENT '配送费',
    `min_order`     DECIMAL(10,2)   NOT NULL DEFAULT 0 COMMENT '起送价',
    `avg_score`     DECIMAL(3,2)    NOT NULL DEFAULT 5.00 COMMENT '平均评分',
    `business_hours` VARCHAR(64)    NOT NULL DEFAULT '' COMMENT '营业时间',
    `logo`          VARCHAR(255)    NOT NULL DEFAULT '' COMMENT 'Logo URL',
    `is_campus`     TINYINT         NOT NULL DEFAULT 1 COMMENT '是否校内',
    `status`        TINYINT         NOT NULL DEFAULT 1 COMMENT '0歇业 1营业',
    `created_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_category` (`category`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='外卖商家';

-- 菜单
DROP TABLE IF EXISTS `menu_item`;
CREATE TABLE `menu_item` (
    `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `merchant_id` BIGINT UNSIGNED NOT NULL COMMENT '商家 -> merchant.id',
    `name`        VARCHAR(128)    NOT NULL COMMENT '菜品名',
    `description` VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '描述',
    `price`       DECIMAL(10,2)   NOT NULL DEFAULT 0 COMMENT '价格',
    `image`       VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '图片',
    `is_on_sale`  TINYINT         NOT NULL DEFAULT 1 COMMENT '是否在售',
    `sales_count` INT             NOT NULL DEFAULT 0 COMMENT '销量',
    `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_merchant` (`merchant_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商家菜单';

-- 外卖订单
DROP TABLE IF EXISTS `takeaway_order`;
CREATE TABLE `takeaway_order` (
    `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `user_id`      BIGINT UNSIGNED NOT NULL COMMENT '用户 -> user.id',
    `merchant_id`  BIGINT UNSIGNED NOT NULL COMMENT '商家 -> merchant.id',
    `items_json`   JSON            NOT NULL COMMENT '菜品 [{id,name,price,num}]',
    `total_amount` DECIMAL(10,2)   NOT NULL DEFAULT 0 COMMENT '商品总额',
    `delivery_fee` DECIMAL(10,2)   NOT NULL DEFAULT 0 COMMENT '配送费',
    `pay_amount`   DECIMAL(10,2)   NOT NULL DEFAULT 0 COMMENT '实付',
    `status`       TINYINT         NOT NULL DEFAULT 0 COMMENT '0待支付 1已支付 2配送中 3已完成 4已取消',
    `address`      VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '收货地址',
    `contact`      VARCHAR(64)     NOT NULL DEFAULT '' COMMENT '联系人',
    `contact_phone` VARCHAR(32)    NOT NULL DEFAULT '' COMMENT '联系电话',
    `remark`       VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '备注',
    `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_user_status` (`user_id`, `status`),
    KEY `idx_merchant` (`merchant_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='外卖订单';

-- 校园通知公告
DROP TABLE IF EXISTS `campus_notice`;
CREATE TABLE `campus_notice` (
    `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `title`        VARCHAR(128)    NOT NULL COMMENT '标题',
    `content`      TEXT            NOT NULL COMMENT '内容',
    `source`       VARCHAR(64)     NOT NULL DEFAULT '' COMMENT '来源（教务处/后勤/校团委...）',
    `category`     VARCHAR(32)     NOT NULL DEFAULT '综合' COMMENT '通知/选课/校招/考试/活动...',
    `target_grade` VARCHAR(32)     NOT NULL DEFAULT '' COMMENT '目标年级（空=全部），如 2024级',
    `publish_time` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发布时间',
    `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_category` (`category`),
    KEY `idx_publish` (`publish_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='校园通知';

-- 通知已读回执（精准推送）
DROP TABLE IF EXISTS `notice_read`;
CREATE TABLE `notice_read` (
    `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `notice_id`  BIGINT UNSIGNED NOT NULL COMMENT '通知 -> campus_notice.id',
    `user_id`    BIGINT UNSIGNED NOT NULL COMMENT '用户 -> user.id',
    `read_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '阅读时间',
    `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_notice_user` (`notice_id`, `user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='通知已读记录';
