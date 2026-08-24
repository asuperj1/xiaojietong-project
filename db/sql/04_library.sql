-- ============================================================
-- 模块 M3 · 图书馆智能预约与学习辅助
-- ============================================================
USE xiaojietong;

-- 建筑（教学楼/图书馆/食堂...）
DROP TABLE IF EXISTS `building`;
CREATE TABLE `building` (
    `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `name`       VARCHAR(64)     NOT NULL COMMENT '建筑名',
    `campus`     VARCHAR(32)     NOT NULL DEFAULT '' COMMENT '校区',
    `address`    VARCHAR(128)    NOT NULL DEFAULT '' COMMENT '地址',
    `floor_count` INT            NOT NULL DEFAULT 1 COMMENT '楼层数',
    `latitude`   DECIMAL(10,6)   NOT NULL DEFAULT 0 COMMENT '纬度',
    `longitude`  DECIMAL(10,6)   NOT NULL DEFAULT 0 COMMENT '经度',
    `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_campus` (`campus`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='建筑';

-- 房间（阅览室/自习室/教室）
DROP TABLE IF EXISTS `room`;
CREATE TABLE `room` (
    `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `building_id` BIGINT UNSIGNED NOT NULL COMMENT '所属建筑 -> building.id',
    `floor`       INT             NOT NULL DEFAULT 1 COMMENT '楼层',
    `name`        VARCHAR(64)     NOT NULL COMMENT '房间名，如 二楼阅览室',
    `room_type`   TINYINT         NOT NULL DEFAULT 0 COMMENT '0阅览室 1自习室 2教室',
    `capacity`    INT             NOT NULL DEFAULT 0 COMMENT '容量（座位数）',
    `has_power`   TINYINT         NOT NULL DEFAULT 0 COMMENT '是否带插座',
    `is_classroom` TINYINT        NOT NULL DEFAULT 0 COMMENT '是否教室（空教室查询用）',
    `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_building_floor` (`building_id`, `floor`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='房间';

-- 座位
DROP TABLE IF EXISTS `seat`;
CREATE TABLE `seat` (
    `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `room_id`    BIGINT UNSIGNED NOT NULL COMMENT '房间 -> room.id',
    `seat_no`    VARCHAR(16)     NOT NULL COMMENT '座位号',
    `has_power`  TINYINT         NOT NULL DEFAULT 0 COMMENT '是否带插座',
    `is_window`  TINYINT         NOT NULL DEFAULT 0 COMMENT '是否靠窗',
    `status`     TINYINT         NOT NULL DEFAULT 0 COMMENT '0可用 1锁定 2停用',
    `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_room_seat` (`room_id`, `seat_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='座位';

-- 座位预约
DROP TABLE IF EXISTS `seat_reservation`;
CREATE TABLE `seat_reservation` (
    `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `seat_id`      BIGINT UNSIGNED NOT NULL COMMENT '座位 -> seat.id',
    `user_id`      BIGINT UNSIGNED NOT NULL COMMENT '用户 -> user.id',
    `reserve_date` DATE            NOT NULL COMMENT '预约日期',
    `begin_time`   TIME            NOT NULL COMMENT '开始时间',
    `end_time`     TIME            NOT NULL COMMENT '结束时间',
    `status`       TINYINT         NOT NULL DEFAULT 0 COMMENT '0已预约 1已签到 2已取消 3已过期 4已完成',
    `checkin_at`   DATETIME        NULL DEFAULT NULL COMMENT '签到时间',
    `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_seat_date` (`seat_id`, `reserve_date`),
    KEY `idx_user_date` (`user_id`, `reserve_date`),
    KEY `idx_date_status` (`reserve_date`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='座位预约';

-- 人流/拥挤度历史（AI 预测训练数据）
DROP TABLE IF EXISTS `occupancy_record`;
CREATE TABLE `occupancy_record` (
    `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `room_id`       BIGINT UNSIGNED NOT NULL COMMENT '房间 -> room.id',
    `record_date`   DATE            NOT NULL COMMENT '日期',
    `period`        TINYINT         NOT NULL COMMENT '时段编号（如 0-12 按小时）',
    `occupancy_rate` DECIMAL(5,2)   NOT NULL DEFAULT 0 COMMENT '占用率 0-100',
    `user_count`    INT             NOT NULL DEFAULT 0 COMMENT '在座人数',
    `created_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_room_date_period` (`room_id`, `record_date`, `period`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='人流拥挤度历史';

-- 教室课表（空教室判定）
DROP TABLE IF EXISTS `classroom_schedule`;
CREATE TABLE `classroom_schedule` (
    `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `room_id`      BIGINT UNSIGNED NOT NULL COMMENT '教室 -> room.id',
    `weekday`      TINYINT         NOT NULL COMMENT '星期 1-7',
    `period`       TINYINT         NOT NULL COMMENT '节次 1-12',
    `course_name`  VARCHAR(128)    NOT NULL DEFAULT '' COMMENT '课程名（空=空闲）',
    `class_name`   VARCHAR(128)    NOT NULL DEFAULT '' COMMENT '班级',
    `teacher`      VARCHAR(64)     NOT NULL DEFAULT '' COMMENT '教师',
    `is_class`     TINYINT         NOT NULL DEFAULT 0 COMMENT '0空闲 1上课',
    `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_room_weekday_period` (`room_id`, `weekday`, `period`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='教室课表';
