# db/sql —— MySQL 建表脚本（分模块）

按功能模块拆分，便于分工与评审。执行顺序：`00 → 01..10 → 99_init_data`。

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
| `99_init_data.sql` | 种子数据 | 演示用最小数据集 |

## 导入（务必用 utf8mb4，否则中文会乱码）

```bash
MYSQL="/c/Program Files/MySQL/MySQL Server 8.0/bin/mysql.exe"
mysql() { "$MYSQL" -h 127.0.0.1 -P 3307 -u root -p"$XJT_DB_PASSWORD" --default-character-set=utf8mb4 "$@"; }

mysql < 00_database.sql
for f in 01_user 02_ai_chat 03_agent 04_library 05_secondhand 06_job 07_forum 08_map 09_life 10_ai_train; do
  mysql xiaojietong < "$f.sql"
done
mysql xiaojietong < 99_init_data.sql
```

## 设计约定

- 主键 `BIGINT UNSIGNED AUTO_INCREMENT`；统一 `created_at/updated_at`。
- **逻辑外键**（字段 + 注释引用，不建物理 FK），靠索引保证查询性能。
- 软删除 `is_deleted TINYINT`；业务状态字段 `status TINYINT`（语义见表内注释）。
- 全文表 `utf8mb4_unicode_ci`；JSON 字段用于可变结构（图片列表/参数/偏好）。
- 表清单与字段设计详见 `docs/architecture.md` §6.2（已按本目录落地）。
