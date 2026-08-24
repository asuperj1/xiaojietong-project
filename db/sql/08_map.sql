-- ============================================================
-- 模块 M7 · 校园地图可视化与空间服务
-- ============================================================
USE xiaojietong;

-- POI（地图点位：建筑/食堂/校车点...）
DROP TABLE IF EXISTS `poi`;
CREATE TABLE `poi` (
    `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `name`        VARCHAR(128)    NOT NULL COMMENT '名称',
    `category`    VARCHAR(32)     NOT NULL DEFAULT '' COMMENT '教学楼/图书馆/食堂/宿舍/校医院/体育馆/校车点',
    `building_id` BIGINT UNSIGNED NULL DEFAULT NULL COMMENT '关联建筑 -> building.id',
    `latitude`    DECIMAL(10,6)   NOT NULL DEFAULT 0 COMMENT '纬度',
    `longitude`   DECIMAL(10,6)   NOT NULL DEFAULT 0 COMMENT '经度',
    `floor`       INT             NOT NULL DEFAULT 0 COMMENT '楼层（室内点位）',
    `description` VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '描述',
    `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_category` (`category`),
    KEY `idx_building` (`building_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='地图 POI';

-- 导航日志（定位联动服务）
DROP TABLE IF EXISTS `navigation_log`;
CREATE TABLE `navigation_log` (
    `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `user_id`      BIGINT UNSIGNED NOT NULL COMMENT '用户 -> user.id',
    `from_poi_id`  BIGINT UNSIGNED NOT NULL COMMENT '起点 POI',
    `to_poi_id`    BIGINT UNSIGNED NOT NULL COMMENT '终点 POI',
    `path_json`    JSON            NULL COMMENT '规划路径',
    `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='导航日志';
