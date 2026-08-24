-- ============================================================
-- 校捷通 · 数据库初始化
-- 创建数据库（utf8mb4），之后按模块执行 01~10
-- 执行顺序：00 → 01..10 → 99_init_data
--   mysql -u root -p --default-character-set=utf8mb4 < 00_database.sql
--   mysql -u root -p --default-character-set=utf8mb4 xiaojietong < 01_user.sql
--   ...
-- ============================================================

CREATE DATABASE IF NOT EXISTS xiaojietong
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE xiaojietong;
