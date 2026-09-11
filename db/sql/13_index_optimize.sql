-- ============================================================
-- 索引优化（成员3 · C7 · 2026-09-10）
--
-- 背景：DAO 层多个列表查询存在「过滤字段有索引、排序字段没索引」的问题，
--       导致 MySQL 走 filesort。本脚本按 DAO 中真实的 WHERE + ORDER BY
--       组合补齐复合索引（按「等值条件 → 排序字段」顺序排列）。
--
-- 对应查询（db/cpp_driver/src/dao/*.cpp）：
--   forum_dao.cpp:21-24   topic：status/is_deleted(/category/audit_status) + ORDER BY updated_at DESC
--   forum_dao.cpp:74-75   topic：audit_status=0 AND is_deleted=0 + ORDER BY id ASC
--   job_dao.cpp:21-25     job_post：status(/job_type) + ORDER BY created_at DESC
--   life_dao.cpp:42-43    merchant：status=1(/category) + ORDER BY avg_score DESC
--   life_dao.cpp:50-51    menu_item：merchant_id + is_on_sale=1 + ORDER BY sales_count DESC
--   secondhand_dao.cpp:21-25  secondhand_item：is_deleted(/category/status) + ORDER BY created_at DESC
--   去重查询：image_asset.md5、knowledge_chunk.chunk_hash
--
-- 幂等：MySQL 8 不支持 ADD INDEX IF NOT EXISTS，
--       故用存储过程先查 information_schema 再决定是否执行 DDL。
--       可重复执行，不会报「Duplicate key name」。
-- ============================================================
USE xiaojietong;

DROP PROCEDURE IF EXISTS `sp_add_index_if_missing`;

DELIMITER $$
CREATE PROCEDURE `sp_add_index_if_missing`(
    IN p_table VARCHAR(64),
    IN p_index VARCHAR(64),
    IN p_cols  VARCHAR(255)
)
BEGIN
    DECLARE v_exists INT DEFAULT 0;

    SELECT COUNT(*) INTO v_exists
      FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME   = p_table
       AND INDEX_NAME   = p_index;

    IF v_exists = 0 THEN
        SET @ddl = CONCAT('ALTER TABLE `', p_table, '` ADD INDEX `', p_index, '` (', p_cols, ')');
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
        SELECT CONCAT('[新增] ', p_table, '.', p_index, ' (', p_cols, ')') AS `索引处理结果`;
    ELSE
        SELECT CONCAT('[跳过] ', p_table, '.', p_index, ' 已存在') AS `索引处理结果`;
    END IF;
END$$
DELIMITER ;

-- ---------- topic（论坛帖子列表 / 待审列表） ----------
-- 默认路径：只取已审核（audited_only=true）+ 按更新时间倒序
CALL sp_add_index_if_missing('topic', 'idx_audit_list',
    '`audit_status`, `status`, `is_deleted`, `updated_at`');
-- 分类路径：按分类过滤 + 按更新时间倒序
CALL sp_add_index_if_missing('topic', 'idx_cat_list',
    '`category`, `status`, `is_deleted`, `updated_at`');
-- 后台待审：audit_status=0 AND is_deleted=0 ORDER BY id ASC
CALL sp_add_index_if_missing('topic', 'idx_pending',
    '`audit_status`, `is_deleted`, `id`');

-- ---------- secondhand_item（二手商品列表） ----------
-- 无分类：is_deleted=0(+status) + ORDER BY created_at DESC
CALL sp_add_index_if_missing('secondhand_item', 'idx_list_order',
    '`is_deleted`, `status`, `created_at`');
-- 有分类：category + is_deleted + ORDER BY created_at DESC
CALL sp_add_index_if_missing('secondhand_item', 'idx_cat_list',
    '`category`, `is_deleted`, `created_at`');

-- ---------- job_post（兼职列表） ----------
CALL sp_add_index_if_missing('job_post', 'idx_list_order',
    '`status`, `created_at`');

-- ---------- merchant（商家列表，按评分倒序） ----------
CALL sp_add_index_if_missing('merchant', 'idx_status_score',
    '`status`, `avg_score`');
CALL sp_add_index_if_missing('merchant', 'idx_cat_status_score',
    '`category`, `status`, `avg_score`');

-- ---------- menu_item（菜品按销量倒序） ----------
CALL sp_add_index_if_missing('menu_item', 'idx_merchant_on_sale_sales',
    '`merchant_id`, `is_on_sale`, `sales_count`');

-- ---------- 去重查询 ----------
CALL sp_add_index_if_missing('image_asset', 'idx_md5', '`md5`');
CALL sp_add_index_if_missing('knowledge_chunk', 'idx_hash', '`chunk_hash`');

DROP PROCEDURE IF EXISTS `sp_add_index_if_missing`;

-- ---------- 汇总自检：本脚本涉及的索引应全部存在 ----------
SELECT TABLE_NAME AS `表`, INDEX_NAME AS `索引`,
       GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS `列`
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE()
   AND INDEX_NAME IN ('idx_audit_list','idx_cat_list','idx_pending','idx_list_order',
                      'idx_status_score','idx_cat_status_score','idx_merchant_on_sale_sales',
                      'idx_md5','idx_hash')
 GROUP BY TABLE_NAME, INDEX_NAME
 ORDER BY TABLE_NAME, INDEX_NAME;
