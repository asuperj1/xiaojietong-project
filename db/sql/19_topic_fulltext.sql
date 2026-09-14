-- ============================================================
-- 校捷通 · C26 帖子搜索索引优化（FULLTEXT + ngram parser）
-- ============================================================
-- 目标（任务卡验收）：**1 万条帖子下关键词查询 p95 < 200ms**
-- 压测证据：tools/bench_c26_topic_search.py（EXPLAIN + 分位数实测）
-- 相关任务：C26（原编号 C33，成员3）· B29 `GET /topics?keyword=`（成员2，依赖本索引）
--
-- 为什么必须上 FULLTEXT：
--   现状 `topic` **没有任何 FULLTEXT 索引**，关键词只能 `title LIKE '%kw%'` 或
--   `content LIKE '%kw%'` —— 前置通配符 **必然全表扫描**（B+ 树无法定位），
--   且 `content` 是 TEXT，1 万行时行数 × 正文长度 = 秒级。
--   `13_index_optimize.sql` 补的分类索引对本查询**无用**（不含关键词列）。
--
-- 为什么用 ngram 而不是默认 parser：
--   默认 parser 以**空格**切词 —— 中文整句不切分，`MATCH('图书馆规则')` 必然 0 命中
--   （这与 CAC-25「按空格切词的中文检索全空」是**同一个坑**）。
--   `WITH PARSER ngram` 用**字符 n-gram**（本机 `ngram_token_size=2` → 双字切分），
--   与 `services/zh_tokenizer.py` 的 2-gram 口径**一致**（同一套切分逻辑，便于互相校验）。
--
-- ⚠️ 部署注意（三条，都可导致"索引建了却搜不到"）：
--   1. `ngram_token_size` 是**只读启动变量**（默认 2）。本索引的切分粒度由**服务器**决定，
--      换服务器若该值不同，**必须重建本索引**（DROP 再 ADD），否则命中集不一致。
--   2. 查询必须走 `MATCH(...) AGAINST (... IN BOOLEAN MODE)` 且**检索列顺序一致**
--      （`(title, content)` → 必须 `MATCH(title, content)`；反过来用不到索引）。
--   3. InnoDB FULLTEXT 的**增量写入有延迟**（有辅助表 + 缓存），刚 INSERT 的帖子可能
--      短暂搜不到。B29 的业务语义是"搜索历史帖子"，可接受；**但自动化测试必须
--      用已提交并 `OPTIMIZE TABLE topic` 后的数据**，否则会假红。
--
-- 幂等：重复执行安全（已存在则跳过 ADD）。编号取 19，**避开 B19 占用的 14~18**。
-- 用法：
--   mysql --host=127.0.0.1 --port=3307 -u<user> -p <db> < db/sql/19_topic_fulltext.sql
-- ============================================================

USE xiaojietong;

-- ---- 1. 前置体检：ngram_token_size 必须是 2（与 zh_tokenizer 口径一致）----
SELECT
    @@ngram_token_size AS ngram_token_size,
    IF(@@ngram_token_size = 2,
       '[OK] 与 zh_tokenizer 2-gram 口径一致',
       '[!] 不是 2：索引切分粒度与代码侧不一致，需同步调整') AS ngram_check;

-- ---- 2. 幂等建索引 ----
SET @xjt_ft_exists := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'topic'
      AND INDEX_NAME   = 'ft_topic_search'
);

SET @xjt_ft_ddl := IF(@xjt_ft_exists = 0,
    'ALTER TABLE `topic` ADD FULLTEXT KEY `ft_topic_search` (`title`, `content`) WITH PARSER ngram',
    'SELECT ''[skip] index ft_topic_search already exists'' AS note');

PREPARE xjt_ft_stmt FROM @xjt_ft_ddl;
EXECUTE xjt_ft_stmt;
DEALLOCATE PREPARE xjt_ft_stmt;

-- ---- 3. 建后核验：索引存在 + 实际可用（金丝雀查询，不依赖任何业务数据）----
SELECT INDEX_NAME, INDEX_TYPE, COLUMN_NAME
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'topic'
  AND INDEX_NAME = 'ft_topic_search'
ORDER BY SEQ_IN_INDEX;

-- 金丝雀：空表也要能"跑通不报错"；有数据时验证 ngram 确实切分了中文
SELECT COUNT(*) AS canary_hits
FROM topic
WHERE MATCH(title, content) AGAINST ('图书馆' IN BOOLEAN MODE);

-- ---- 4. 给 B29 的参考查询（**不要再写 LIKE '%kw%'**）----
-- 说明：`?` 为绑定参数；keyword 由调用方做 boolean 模式净化后传入
--       （见 forum_dao.cpp::build_boolean_query —— 去掉 + - > < ( ) ~ * " @ 并把
--         空格分隔的每个词前缀 `+` → AND 语义，等价于"这些字都要出现"）。
--       `relevance` 用 SELECT 别名在 `ORDER BY` 引用（WHERE 不能引用别名）。
--
--   SELECT t.id, t.title, t.category, t.like_count, t.comment_count, t.view_count,
--          t.created_at, u.nickname AS author_name,
--          MATCH(t.title, t.content) AGAINST (? IN BOOLEAN MODE) AS relevance
--   FROM topic t JOIN user u ON t.author_id = u.id
--   WHERE t.status = 0 AND t.is_deleted = 0
--     AND MATCH(t.title, t.content) AGAINST (? IN BOOLEAN MODE)
--     AND (? = '' OR t.category = ?)          -- ⚠️ 本模式令分类索引失效（已知，非本任务）
--     AND t.audit_status = 1                  -- DATA-01：列表只出已过审
--   ORDER BY relevance DESC, t.updated_at DESC
--   LIMIT ? OFFSET ?;
