-- ============================================================
-- B19 · 首页轮播表 `home_banner`（成员4 分工 · 本 PR 由成员3 起草）
--
-- ★★ 唯一真源（Single Source of Truth）★★
--   本文件的 `CREATE TABLE` 即为 `home_banner` 的**唯一权威定义**。
--   · 任何"我本机库长这样"的结论，**不得**反向修改本定义；
--   · 旧环境形状不一致时，用下方 PART 2/3 的「守卫式收敛」把物理表收敛到本定义。
--   · 规范出处：`docs/db-migration-convention.md`（PR #86）。
--
-- 权威列（文档 L324 / DAO / 真实库三方对齐）：
--   `id` `title` `image` `link_type` `link_target` `sort` `start_at` `end_at` `enabled`
--   `created_at` `updated_at`
--   `link_type` 枚举：`none` / `page` / `notice` / `url`
--
-- ⚠️ 历史漂移（本次收敛的起因）
--   commit `17240f3` 为迁就某台机器的旧库，把建表里的
--   `image`/`link_type`/`link_target` 改写成了 `image_url`/`link_url`（并删掉了 `link_type`），
--   种子也换成另一套文案与图片路径。后果是三处不一致：
--     · DAO `db/cpp_driver/src/dao/home_dao.cpp` 读写的是 `image`/`link_type`/`link_target`
--       → 在漂移形状上必然 `ERROR 1054 Unknown column 'image'`；
--     · B21 `routers/home.py` 被迫用 `information_schema` 探测列名才能兼容；
--     · 真实库与文档 L324 一直是 `image`/`link_type`/`link_target`。
--   处置：**定义回到权威形状**，旧列用「守卫式收敛」兼容（回填数据、**不 DROP**），
--   待观察一轮确认无代码引用后再单独清理旧列。
--
-- 幂等性：
--   · 建表 `CREATE TABLE IF NOT EXISTS` —— 重复执行不报错、不清空数据；
--     ⚠️ 不要照抄 `09_life.sql` 的 `DROP TABLE`（那是首次建库脚本），本脚本线上会被重跑。
--     ⚠️ 注意 `IF NOT EXISTS` 的**漂移陷阱**：表已存在时只跳过，**不校验列名**
--        → 所以必须有 PART 2/3 的收敛段，否则"跑过了"不代表"形状对了"。
--   · 补列/回填全部带 existence 守卫，可无限次重跑。
--   · 种子用「固定主键 + ON DUPLICATE KEY UPDATE **空更新**」
--     —— 能补齐缺失行，但**不覆盖**已有行的数据。
--     规范依据：`docs/db-migration-convention.md` §5.2「运营数据（如 `home_banner`、
--     `pickup_point`）用空更新」，避免把运营同学在页面上改过的文案/图片冲掉。
--
-- 与 B21 的约定：
--   · `sort` 语义为**越大越靠前**（`ORDER BY sort DESC`）；
--   · 有效期过滤：`enabled = 1 AND (start_at IS NULL OR start_at <= NOW())
--                              AND (end_at   IS NULL OR end_at   >= NOW())`。
--
-- 回滚：见文件末尾。
-- ============================================================
USE xiaojietong;

-- ================================================== PART 1 · 建表（唯一真源）
CREATE TABLE IF NOT EXISTS `home_banner` (
    `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `title`       VARCHAR(128)    NOT NULL DEFAULT ''        COMMENT '轮播标题',
    `image`       VARCHAR(255)    NOT NULL DEFAULT ''        COMMENT '图片 URL',
    `link_type`   VARCHAR(32)     NOT NULL DEFAULT 'none'    COMMENT '跳转类型 none/page/notice/url',
    `link_target` VARCHAR(255)    NOT NULL DEFAULT ''        COMMENT '跳转目标（页面路径 / 公告 id / URL）',
    `sort`        INT             NOT NULL DEFAULT 0         COMMENT '排序，越大越靠前',
    `start_at`    DATETIME        NULL     DEFAULT NULL      COMMENT '生效开始，NULL=立即生效',
    `end_at`      DATETIME        NULL     DEFAULT NULL      COMMENT '生效结束，NULL=永不过期',
    `enabled`     TINYINT         NOT NULL DEFAULT 1         COMMENT '1启用 0停用',
    `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_enabled_sort` (`enabled`, `sort`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='首页轮播';

-- ================================================== PART 2 · 收敛：补权威列
-- 表已存在（漂移形状）时 `CREATE TABLE IF NOT EXISTS` 会被跳过 ⇒ 缺的列在这里补。
-- MySQL 不支持 `ADD COLUMN IF NOT EXISTS` ⇒ 用 information_schema 判定 + PREPARE 动态执行。
-- 新库：三个 COUNT(*)=0 分支都不成立 → 全部输出 [skip]，无副作用。

SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `home_banner` ADD COLUMN `image` VARCHAR(255) NOT NULL DEFAULT '''' COMMENT ''图片 URL'' AFTER `title`',
        'SELECT ''[skip] home_banner.image 已存在'' AS `收敛-补列`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'home_banner' AND column_name = 'image'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `home_banner` ADD COLUMN `link_type` VARCHAR(32) NOT NULL DEFAULT ''none'' COMMENT ''跳转类型 none/page/notice/url'' AFTER `image`',
        'SELECT ''[skip] home_banner.link_type 已存在'' AS `收敛-补列`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'home_banner' AND column_name = 'link_type'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

SET @ddl := (
    SELECT IF(COUNT(*) = 0,
        'ALTER TABLE `home_banner` ADD COLUMN `link_target` VARCHAR(255) NOT NULL DEFAULT '''' COMMENT ''跳转目标（页面路径 / 公告 id / URL）'' AFTER `link_type`',
        'SELECT ''[skip] home_banner.link_target 已存在'' AS `收敛-补列`')
    FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'home_banner' AND column_name = 'link_target'
);
PREPARE s FROM @ddl; EXECUTE s; DEALLOCATE PREPARE s;

-- ================================================== PART 3 · 收敛：回填旧列数据
-- 漂移形状里的 `image_url` / `link_url` 装着真实数据，必须搬到权威列；**不 DROP 旧列**。
-- 回填条件刻意保守：只在「权威列为空 且 旧列有值」时写 → 不会覆盖已有正确数据，可重跑。

SET @has := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'home_banner' AND column_name = 'image_url'
);
SET @sql := IF(@has = 1,
    'UPDATE `home_banner` SET `image` = `image_url` WHERE (`image` IS NULL OR `image` = '''') AND COALESCE(`image_url`, '''') <> ''''',
    'SELECT ''[skip] 无 image_url 旧列，无需回填'' AS `收敛-回填`');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @has := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'home_banner' AND column_name = 'link_url'
);
SET @sql := IF(@has = 1,
    'UPDATE `home_banner` SET `link_target` = `link_url` WHERE (`link_target` IS NULL OR `link_target` = '''') AND COALESCE(`link_url`, '''') <> ''''',
    'SELECT ''[skip] 无 link_url 旧列，无需回填'' AS `收敛-回填`');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- `link_type` 旧形状里根本不存在，补列后一律是默认值 `none`；
-- 若 `link_target` 是小程序页面路径（`/pages/...`），语义上应为 `page`。
-- 只修正这一种「矛盾状态」，不触碰已有明确取值。
SET @has := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'home_banner' AND column_name = 'link_url'
);
SET @sql := IF(@has = 1,
    'UPDATE `home_banner` SET `link_type` = ''page'' WHERE `link_type` = ''none'' AND `link_target` LIKE ''/pages/%''',
    'SELECT ''[skip] 无 link_url 旧列，无需推断 link_type'' AS `收敛-推断`');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ================================================== PART 4 · 种子（幂等 · **空更新**）
-- 固定主键 ⇒ 缺行会补上；`ON DUPLICATE KEY UPDATE id = id` 是**空更新** ⇒
-- 已有行**一个字段都不改**，重跑无副作用，也不会覆盖运营已改的内容（规范 §5.2）。
INSERT INTO `home_banner` (`id`, `title`, `image`, `link_type`, `link_target`, `sort`, `enabled`) VALUES
    (1, '校园通知新版上线', '/static/images/banner-notice.png', 'page', '/pages/notice/index',   30, 1),
    (2, 'AI 助手帮你答疑',  '/static/images/banner-ai.png',     'page', '/pages/ai/index',       20, 1),
    (3, '二手交易更放心',   '/static/images/banner-second.png', 'page', '/pages/second/index',   10, 1)
ON DUPLICATE KEY UPDATE `id` = `id`;

-- ================================================== PART 5 · 自检
SELECT column_name AS `列`, column_type AS `类型`, is_nullable AS `可空`, column_comment AS `说明`
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'home_banner'
ORDER BY ordinal_position;

SELECT index_name AS `索引`, GROUP_CONCAT(column_name ORDER BY seq_in_index) AS `列`
FROM information_schema.statistics
WHERE table_schema = DATABASE() AND table_name = 'home_banner'
GROUP BY index_name;

SELECT CONCAT('home_banner 行数 = ', COUNT(*), '，其中启用 = ', SUM(`enabled` = 1)) AS `种子自检`
FROM `home_banner`;

-- 收敛自检：应当只有权威列；若列出 `image_url`/`link_url`，说明该库是漂移形状
-- （数据已回填到权威列），旧列留待观察一轮后单独清理。
SELECT IF(COUNT(*) = 0,
          '✅ 无漂移遗留列，形状已统一',
          CONCAT('⚠️ 存在漂移遗留列（数据已回填，待清理）：', GROUP_CONCAT(column_name))) AS `收敛自检-遗留列`
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'home_banner'
  AND column_name IN ('image_url', 'link_url');

-- 权威形状自检：11 列齐全才通过。
SELECT IF(COUNT(*) = 11,
          CONCAT('✅ home_banner 权威列齐全（', COUNT(*), ' 列）'),
          CONCAT('❌ 权威列缺失，仅有 ', COUNT(*), ' 列，请检查 PART 2 执行结果')) AS `收敛自检-权威列`
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'home_banner'
  AND column_name IN ('id','title','image','link_type','link_target','sort',
                      'start_at','end_at','enabled','created_at','updated_at');

-- ============================================================
-- 回滚（需要时手工执行）
-- ------------------------------------------------------------
-- ⚠️ 下表已不再由脚本创建；仅当确认无代码/数据依赖时执行。
-- DROP TABLE IF EXISTS `home_banner`;
--
-- 若只想撤掉收敛补的三列（会丢这三列的数据）：
-- ALTER TABLE `home_banner` DROP COLUMN `link_target`;
-- ALTER TABLE `home_banner` DROP COLUMN `link_type`;
-- ALTER TABLE `home_banner` DROP COLUMN `image`;
-- ============================================================
