# DataGrip 连接与查看表结构指南

> DataGrip 是 JetBrains 的数据库 IDE（与 IDEA/PyCharm 同家族）。用它可视化查看校捷通的 42 张表。

## 一、新建连接

1. 菜单 **File → New → Data Source → MySQL**
2. 填写连接信息：

| 字段 | 值 |
|---|---|
| Host | `127.0.0.1` |
| Port | **`3307`**（不是默认 3306！） |
| User | `root` |
| Password | `jhq000000` |
| Database | `xiaojietong` |
| 驱动 | 首次连接会提示下载 MySQL Driver，点 Download 自动安装 |

3. 点 **Test Connection**，出现 `Successfully connected` 即成功。

> ⚠️ 本机 MySQL 实例跑在 3307 端口（服务名 MySQL803307）。填 3306 会连不上。

## 二、查看表结构（三种方式）

### 方式 1：左侧 Database 面板（最常用）
- 窗口左侧 **Database** 面板 → 展开 `xiaojietong` → **Tables**
- 看到全部 42 张表，按模块前缀可辨（如 `seat_reservation` 属图书馆）
- **双击表名** → 下方打开数据表格（可编辑/筛选）
- **右键表名 → Modify Table** → 图形化查看/修改列、索引、注释
- **右键表名 → Diagrams → Show Diagram** → ER 图（注意：本项目用逻辑外键，无物理外键线）

### 方式 2：SQL 控制台（精确确认）
在顶部打开 Console，执行：
```sql
USE xiaojietong;
SHOW CREATE TABLE user;          -- 建表 DDL（最权威）
DESC seat_reservation;            -- 列结构 + 类型 + 注释
SHOW INDEX FROM topic;            -- 索引
SHOW TABLES;                      -- 全部表
SELECT COUNT(*) FROM information_schema.tables
  WHERE table_schema='xiaojietong';   -- 表总数（应为 42）
```

### 方式 3：字段注释（表内注释很全）
每列都有中文注释（如 `role TINYINT COMMENT '0学生 1管理员'`），在 Modify Table 或 `SHOW FULL COLUMNS` 中查看：
```sql
SHOW FULL COLUMNS FROM job_post;   -- 含注释
```

## 三、常用技巧

| 操作 | 方法 |
|---|---|
| 看某表数据 | 双击表名 / 右键→Edit Data |
| 建 SQL 文件 | 右键 schema → New → Console |
| 导出建表脚本 | 右键表 → Dump → DDL |
| 全文搜索列名 | Ctrl+Shift+F 搜 `information_schema` 或直接搜 SQL |
| 刷新结构 | 右键 schema → Refresh |

## 四、设计约定提醒（对照表结构）

- **逻辑外键**：字段注释里 `-> user.id` 表示引用关系，但没有物理 FOREIGN KEY（DataGrip 的 ER 图不会画连线，看注释即可）
- **软删除**：业务表都有 `is_deleted`，查询时默认过滤
- **JSON 字段**：`images_json` / `params_json` 等，DataGrip 可直接查看并编辑 JSON
- **时间**：统一 `DATETIME`，`created_at` 自动填充

## 五、42 张表清单（模块对照）

| 模块 | 表 |
|---|---|
| 用户 | user, user_tag, student_profile |
| AI 对话 | ai_conversation, ai_message, quick_command, feedback |
| Agent | agent_task, agent_tool, reminder |
| 图书馆 | building, room, seat, seat_reservation, occupancy_record, classroom_schedule |
| 二手 | secondhand_item, secondhand_wish, secondhand_order, item_message |
| 兼职 | company, job_post, job_application, user_job_blacklist |
| 论坛 | topic, comment, like_record, favorite, report |
| 地图 | poi, navigation_log |
| 生活 | merchant, menu_item, takeaway_order, campus_notice, notice_read |
| AI 数据 | train_corpus, train_annotation, model_version, knowledge_doc, knowledge_chunk, image_asset |

> 建表脚本在 `db/sql/`（00_database ~ 10_ai_train + 99_init_data），字段设计说明见 `docs/architecture.md` §6.2。
