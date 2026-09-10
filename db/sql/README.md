# db/sql —— MySQL 建表脚本（分模块）

按功能模块拆分，便于分工与评审。

**执行顺序**：`00 → 01..12 → 13(索引优化) → 99_init_data → 99b/99c/99d(增量)`

其中 `11~13` 为 2026-09-10 新增（内容安全治理 / 通知投递 / 索引优化），
均为**幂等脚本**（可重复执行，不会丢数据）。

| 文件 | 模块 | 表 |
|---|---|---|
| `00_database.sql` | 建库（utf8mb4） | — |
| `01_user.sql` | M0 用户体系 | user / user_tag / student_profile |
| `02_ai_chat.sql` | M1 AI 助手 | ai_conversation / ai_message / quick_command / feedback |
| `03_agent.sql` | M2 Agent 任务 | agent_task / agent_tool / reminder |
| `04_library.sql` | M3 图书馆 | building / room / seat / seat_reservation / occupancy_record / classroom_schedule |
| `05_secondhand.sql` | M4 二手循环 | secondhand_item / secondhand_wish / secondhand_order / item_message |
| `06_job.sql` | M5 兼职实习 | company / job_post / job_application / user_job_blacklist |
| `07_forum.sql` | M6 论坛 | topic / comment / like_record / favorite / report |
| `08_map.sql` | M7 校园地图 | poi / navigation_log |
| `09_life.sql` | M8 生活服务 | merchant / menu_item / takeaway_order / campus_notice / notice_read |
| `10_ai_train.sql` | AI 数据闭环 | train_corpus / train_annotation / model_version / knowledge_doc / knowledge_chunk / image_asset |
| **`11_audit.sql`** | **M9 内容安全治理（C7）** | **audit_word（敏感词库，19 词种子）/ audit_log（审核留痕）** |
| **`12_notice_delivery.sql`** | **M8 扩展（C7）** | **notice_delivery（投递/曝光/得分/已读明细）** |
| **`13_index_optimize.sql`** | **性能优化（C7）** | 11 个复合索引（见下） |
| `99_init_data.sql` | 种子数据 | 演示用最小数据集 |
| `99b_knowledge_faq.sql` | 增量 | 知识库 FAQ 追加 |
| `99c_agent_tool_schema.sql` | 增量 | agent_tool 工具 schema 修正 |
| `99d_knowledge_more.sql` | 增量 | 知识库扩料（18 篇，DELETE+INSERT 幂等） |

### 13_index_optimize.sql 补充索引一览

按 DAO 中真实的 `WHERE + ORDER BY` 组合补齐，消除 `Using filesort`：

| 表 | 索引 | 列 | 对应查询 |
|---|---|---|---|
| topic | `idx_audit_list` | audit_status, status, is_deleted, updated_at | `forum_dao.cpp:21` 默认已审列表 |
| topic | `idx_cat_list` | category, status, is_deleted, updated_at | 分类列表 |
| topic | `idx_pending` | audit_status, is_deleted, id | `forum_dao.cpp:74` 后台待审 |
| secondhand_item | `idx_list_order` | is_deleted, status, created_at | `secondhand_dao.cpp:21` |
| secondhand_item | `idx_cat_list` | category, is_deleted, created_at | 分类列表 |
| job_post | `idx_list_order` | status, created_at | `job_dao.cpp:21` |
| merchant | `idx_status_score` / `idx_cat_status_score` | status(+category), avg_score | `life_dao.cpp:42` |
| menu_item | `idx_merchant_on_sale_sales` | merchant_id, is_on_sale, sales_count | `life_dao.cpp:50` |
| image_asset | `idx_md5` | md5 | 图片去重 |
| knowledge_chunk | `idx_hash` | chunk_hash | 分块去重 |

**实测效果**（`EXPLAIN`，2026-09-10）：

| 场景 | key | Extra |
|---|---|---|
| 优化后 | `idx_audit_list` | **Backward index scan**（无排序） |
| 优化前（无该索引） | `idx_audit` | Using where; **Using filesort** |

## 导入（务必用 utf8mb4，否则中文会乱码）

```bash
MYSQL="/c/Program Files/MySQL/MySQL Server 8.0/bin/mysql.exe"
mysql() { "$MYSQL" -h 127.0.0.1 -P 3307 -u root -p"$XJT_DB_PASSWORD" --default-character-set=utf8mb4 "$@"; }

mysql < 00_database.sql
for f in 01_user 02_ai_chat 03_agent 04_library 05_secondhand 06_job 07_forum 08_map 09_life 10_ai_train \
         11_audit 12_notice_delivery 13_index_optimize; do
  mysql xiaojietong < "$f.sql"
done
mysql xiaojietong < 99_init_data.sql
# 增量脚本（按需执行，均可重复执行）
mysql xiaojietong < 99b_knowledge_faq.sql
mysql xiaojietong < 99c_agent_tool_schema.sql
mysql xiaojietong < 99d_knowledge_more.sql
```

> Windows PowerShell 可用 `mysql -e "source <绝对路径>/11_audit.sql"` 方式执行
> （`13_index_optimize.sql` 含 `DELIMITER`，必须用 `source` 或重定向，不能用 `-e "语句"`）。

## 设计约定

- 主键 `BIGINT UNSIGNED AUTO_INCREMENT`；统一 `created_at/updated_at`。
- **逻辑外键**（字段 + 注释引用，不建物理 FK），靠索引保证查询性能。
- 软删除 `is_deleted TINYINT`；业务状态字段 `status TINYINT`（语义见表内注释）。
- 全文表 `utf8mb4_unicode_ci`；JSON 字段用于可变结构（图片列表/参数/偏好）。
- 表清单与字段设计详见 `docs/architecture.md` §6.2（已按本目录落地）。
