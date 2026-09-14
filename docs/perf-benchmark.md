# C++ jt_db vs Python 直连 性能基准报告（C6 · 答辩素材）

> 日期：2026-09-10 ｜ 脚本：`db/cpp_driver/test/bench_dao.py` ｜ 数据表：`knowledge_doc`（27 行）

## 1. 目的

量化本项目 **C++ 数据访问层 `jt_db`（连接池 + 预处理语句）** 相对 **Python 直连（pymysql）** 的表现，为架构选型与答辩提供数据支撑。

## 2. 方法与环境

| 项 | 值 |
|---|---|
| 环境 | Windows / MySQL 8（127.0.0.1:3307，库 `xiaojietong`） |
| C++ 侧 | `jt_db.pyd`（连接池 min=2, max=16），经 `backend/app/db/cpp_bridge` 调用 |
| Python 侧 | 直连 `pymysql`（单连接复用、`autocommit=True`） |
| 测试项 | 主键点查、分页查询（两者 SQL 完全相同） |
| 次数 | 每项各 300 次（单线程串行） |
| 复现 | `XJT_DB_PASSWORD=*** python db/cpp_driver/test/bench_dao.py --n 300`（用能加载 `jt_db.pyd` 的 Python） |

## 3. 实测结果（2026-09-10）

| 测试项 | 驱动 | 总耗时(s) | QPS | 平均耗时(ms) |
|---|---|---|---|---|
| 主键点查 | pymysql 直连 | 0.035 | 8497 | 0.118 |
| 分页查询 | pymysql 直连 | 0.049 | 6077 | 0.165 |
| 主键点查 | C++ jt_db（连接池） | 0.076 | 3970 | 0.252 |
| 分页查询 | C++ jt_db（连接池） | 0.082 | 3645 | 0.274 |

**QPS 比值（C++ / pymysql）：主键点查 0.47x、分页查询 0.60x**

## 4. 结论与分析（如实记录）

- **在当前微基准（单线程、单表 27 行、简单查询）下，C++ `jt_db` 未取得速度优势**，QPS 约为直连的 0.5~0.6 倍。
- 主要原因是**测试场景未覆盖连接池/预处理语句的收益场景**：
  1. 单线程串行执行，**连接池的并发复用优势体现不出来**；
  2. 每次查询固定经历 `pybind11` 跨语言调用 + 预处理 `prepare/bind/execute/fetch` 全流程，对**极小结果集**而言，准备开销大于收益；
  3. 数据量仅 27 行，未触及网络往返与结果集解析瓶颈。
- C++ 层的真正价值在于：**连接池并发、批量/大结果集、复杂事务、预处理防注入、类型安全与内存可控**——这些需在更贴近生产的负载下衡量。

## 5. 后续建议（下一步优化与补充测试）

1. **补充并发基准**：多线程（如 8/16 并发）下对比连接池复用与直连（`--threads` 扩展）；
2. **扩大数据量**：在万级/十万级行表（如 `secondhand_item` 造数）上测分页与范围查询；
3. **复杂场景**：多表 JOIN、批量插入、事务回滚、大字段（TEXT/JSON）读取；
4. C++ 侧优化方向：结果集**零拷贝/批量返回**、减少 `pybind11` 转换次数（如 `query_many` 一次取多行）、复用 `MYSQL_STMT` 句柄；
5. 报告中保留**基线数据**，随着优化持续更新，形成可对比的迭代记录。

> 结论一句话：**当前数据说明"简单小查询下 C++ 层不占优"，其设计目标是高并发/大数据量/安全性与进程内高效——需用并发与大规模数据基准来验证价值。** 这正是后续（C6 深化）要做的工作。

---

# 第二部分 · C9 并发基准与 C++ 层优化（2026-09-10）

> 日期：2026-09-10 ｜ 脚本：`db/cpp_driver/test/bench_concurrent.py` ｜ 表：`knowledge_doc`

## 6. 为什么要补并发基准

C6 的单线程微基准得出「C++ 层为 pymysql 的 0.47x/0.60x」，但那个场景恰好**避开了 C++ 层的收益区**：
单线程串行时连接池毫无意义，每次调用反而多付一次 pybind 跨语言 + prepare/bind/execute/fetch 的固定开销。

本部分补上**并发维度**，并据此完成 C++ 层优化。

## 7. 方法

| 项 | 值 |
|---|---|
| 对比 | `jt_db`（共享连接池，max=16） vs `pymysql`（每线程独立连接，持久复用） |
| 并发 | 1 / 4 / 8 / 16 线程（`ThreadPoolExecutor`） |
| 场景 | 主键点查、分页查询（10 行）、大结果集（500 行） |
| 次数 | 每项 600 次 |
| 说明 | 两者执行**完全相同**的 SQL；QPS 波动约 ±15%（多次运行取代表值） |
| 复现 | `python db/cpp_driver/test/bench_concurrent.py --n 600 --threads 1,8,16` |

## 8. 实测结果

### 8.1 优化前（未释放 GIL，`JT_DB_RELEASE_GIL=0`）

| 并发 | 场景 | pymysql QPS | jt_db QPS | 比值 |
|---|---|---|---|---|
| 1 | 主键点查 | 6904 | 4429 | 0.64x |
| 1 | 分页查询 | 6433 | 4205 | 0.65x |
| 1 | 大结果集 | 4467 | 3398 | 0.76x |
| 4 | 大结果集 | 3289 | 4031 | **1.23x** |
| 8 | 分页查询 | 3509 | 4314 | **1.23x** |
| 8 | 大结果集 | 2572 | 3782 | **1.47x** |
| 16 | 主键点查 | 3371 | 4106 | **1.22x** |
| 16 | 分页查询 | 2715 | 4013 | **1.48x** |
| 16 | 大结果集 | 2018 | 3792 | **1.88x** |

**已可看出趋势**：并发越高 C++ 越占优 —— 因为 pymysql 是**纯 Python** 实现，
协议解析全靠 Python 字节码，受 GIL 制约，线程越多争抢越激烈（QPS 从 6904 掉到 3371）；
而 jt_db 的 C++ 调用中 Python 层几乎不执行，QPS 基本稳定（4429 → 4106）。

### 8.2 优化后（默认，MySQL IO 期间释放 GIL）

C++ 层新增 `GilIoGuard`：把**参数转换**与**结果构造**留在持 GIL 区，
中间的 **MySQL 网络/协议处理**放在 `py::gil_scoped_release` 中执行，使多个请求真正并行。

| 并发 | 场景 | pymysql QPS | jt_db QPS | 比值 | 对比 8.1 |
|---|---|---|---|---|---|
| 1 | 主键点查 | 7572 | 4541 | 0.60x | — |
| 1 | 分页查询 | 6851 | 4666 | 0.68x | — |
| 1 | 大结果集 | 3928 | 3965 | **1.01x** | 0.76x ↑ |
| 8 | 主键点查 | 4832 | 2516 | 0.52x | 0.81x ↓ |
| 8 | 分页查询 | 3686 | 5469 | **1.48x** | 1.23x ↑ |
| 8 | 大结果集 | 2465 | 5911 | **2.40x** | 1.47x ↑ |
| 16 | 主键点查 | 3263 | 2484 | 0.76x | 1.22x ↓ |
| 16 | 分页查询 | 2755 | 4233 | **1.54x** | 1.48x ↑ |
| 16 | 大结果集 | 1953 | **5552** | **2.84x** | 1.88x ↑ |

### 8.3 结论

1. **大结果集 / 分页查询收益显著**：并发 16 时大结果集 **2.84x**（优化前 1.88x），分页 1.54x。
2. **pymysql 随并发退化，jt_db 稳定**：
   - pymysql 点查：6904 → 3263（**-53%**）
   - jt_db 大结果集：3398 → 5552（**+63%**）
3. **极小结果集的高并发点查是唯一的取舍点**：释放 GIL 后 1.22x → **0.40~0.76x（抖动较大）**。
   原因是点查的 C++ 执行时间极短（约 0.15 ms），而 GIL 释放/唤醒等待线程的调度开销与之相当，
   16 线程下形成抖动（多轮实测波动明显：同一配置下 0.40x / 0.65x / 0.76x 均出现过）。

## 9. 取舍与开关

已把该行为做成**编译期开关**（`CMakeLists.txt`）：

| 开关 | 行为 | 适用 |
|---|---|---|
| `-DJT_DB_RELEASE_GIL=ON`（**默认**） | MySQL IO 期间让出 GIL | 后端以**列表/分页查询**为主 → 推荐 |
| `-DJT_DB_RELEASE_GIL=OFF` | 全程持 GIL | 极小结果集的**高并发点查**密集场景 |

```bash
# 切换示例
cmake -B build -A x64 -DJT_DB_RELEASE_GIL=OFF -DMYSQL_DIR="C:/Program Files/MySQL/MySQL Server 8.0"
cmake --build build --config Release --target jt_db
```

## 10. 一并修复的正确性问题（C9 附带）

| 问题 | 现象 | 修复 |
|---|---|---|
| **JSON / LONGTEXT 大字段读取失败** | `query()` 遇 `MYSQL_DATA_TRUNCATED` 直接抛「读取结果失败」。实测 MySQL 8 对 JSON 列返回 `metadata.max_length = 0`，按 255 兜底 → `model_version.metrics_json`（946 字节）根本读不出来 | 接受 `MYSQL_DATA_TRUNCATED`，用 `mysql_stmt_fetch_column()` 按真实长度重取（`mysql_connection.cpp`） |
| **构建脚本中文乱码导致无法执行** | `db/cpp_driver/scripts/*.ps1` 为 UTF-8 无 BOM，Windows PowerShell 5.1 按 GBK 解析 → 报「意外的标记 / 字符串缺少终止符」 | 三个脚本统一加 UTF-8 BOM |

## 11. 答辩口径（一句话）

> **单线程小查询**场景下 C++ 数据层不占优（0.6~0.8x）；
> **并发 + 大结果集**场景下反超 pymysql 达 **2.84x**，且 pymysql 的 QPS 随并发**下降 53%** 而 jt_db **上升 63%**。
> 这正是本项目自研 C++ 数据访问层（连接池 + 预处理语句 + GIL 释放）的价值所在。

## 12. 复现命令

```powershell
# 并发基准（需能加载 jt_db.pyd 的解释器）
cd db/cpp_driver/test
$env:XJT_DB_PASSWORD='***'; $env:XJT_DB_PORT='3307'
E:/miniconda3/python.exe bench_concurrent.py --n 600 --threads 1,4,8,16

# 单线程基线（C6 原报告，保留可对比）
E:/miniconda3/python.exe bench_dao.py --n 300
```

---

# 第三部分 · C26 帖子关键词搜索索引（2026-09-14）

> 日期：2026-09-14 ｜ 脚本：`tools/bench_c26_topic_search.py` ｜ 表：`topic`（种子 1 万行）
> 索引脚本：`db/sql/19_topic_fulltext.sql` ｜ DAO：`ForumDAO::search_topics`

## 13. 问题与目标

任务卡 `C26`（原编号 `C33`）验收：**1 万条帖子下关键词查询 p95 < 200ms** + 压测数据。

实测现状（改造前）：

| 项 | 实测值 | 核查命令 |
|---|---|---|
| `topic` 行数 | **23** | `SELECT COUNT(*) FROM topic` |
| FULLTEXT 索引 | **一个都没有** | `information_schema.STATISTICS` |
| `ngram_token_size` | 2（默认，与 `zh_tokenizer` 2-gram 口径一致） | `SHOW VARIABLES LIKE 'ngram%'` |
| 唯一可用的搜索方式 | `title LIKE '%kw%' OR content LIKE '%kw%'`（`content` 还是 TEXT） | — |

前置唯一键可用 ⇒ 前置通配符**不可能走 B+ 树** ⇒ 全表扫描。这与 `CAC-25`（按空格切词导致中文检索全空）是同一族问题的两个面。

## 14. 方法

| 项 | 值 |
|---|---|
| 数据 | 确定性生成 **10 000** 条帖子（`category='bench_c26'`，`random.seed(42)`），`created_at/updated_at` 铺开 90 天 |
| 植入标记 | 热词「图书馆研讨间」37 行 + 冷词「馆际互借」37 行（**冷词只出现在植入行**） |
| 新方案 | `MATCH(title, content) AGAINST (? IN BOOLEAN MODE)` + `FULLTEXT ... WITH PARSER ngram` |
| 旧方案 | `title LIKE ? OR content LIKE ?`（同一 WHERE 的其余条件完全相同） |
| 计时 | 每方案 15 轮，报 p50 / p95 / max（最近秩法，样本少时**保守取上界**） |
| 复现 | `E:/miniconda3/python.exe tools/bench_c26_topic_search.py --rows 10000 --rounds 15`（跑完自动清理） |

⚠️ **两种查询形态都要测**，否则结论会反过来（见 §15.3）：

- **取页** `ORDER BY updated_at DESC LIMIT 20` —— 有 `LIMIT` 短路
- **全量计数** `COUNT(*)` —— 无短路，直接暴露扫描量

⚠️ **冷 / 热词都要测**：热词命中占比高，不是搜索框的典型形态（这是第一版基准踩的坑）。

## 15. 实测结果（10 023 行）

### 15.1 EXPLAIN 对照（冷词）

| 方案 | type | key | 估算扫描行数 |
|---|---|---|---|
| OLD `LIKE '%馆际互借%'` | `ref` | `idx_audit_list` | **4 984** |
| NEW `MATCH ... AGAINST` | `fulltext` | `ft_topic_search` | **1** |

> ⚠️ OLD 的 `key` **不是 NULL** —— `audit_status=1` 覆盖绝大多数行，优化器会用
> `idx_audit_list` 做一次 ref 扫描再逐行套 LIKE，**看起来"用了索引"，实际扫的仍是全表**。
> 所以判定依据必须是「**没有**关键词索引可用 + 扫描量远大于 MATCH」，不能只看 `key=(none)`。

### 15.2 分位数

| 场景 | OLD LIKE p95 | NEW MATCH p95 | 结论 |
|---|---|---|---|
| **冷词** 取页 | 9.53 ms | **1.42 ms** | MATCH 快 **6.7x** ✅ 验收通过 |
| **冷词** 全量计数 | 10.92 ms | **0.92 ms** | MATCH 快 **11.9x** |
| 热词 取页 | **0.70 ms** | 32.32 ms | LIKE 更快（见 §15.3） |
| 热词 全量计数 | 11.01 ms | 12.28 ms | 基本持平 |

命中集一致性复核（两方案 `COUNT(*)` 必须相同）：

| 词 | OLD LIKE 命中 | NEW MATCH 命中 |
|---|---|---|
| 冷词「馆际互借」 | 37 | **37** ✅ |
| 热词「图书馆」 | 2 684 | **2 684** ✅ |

### 15.3 ⚠️ 如实记录：热词下 `LIKE` 反而更快

| 词 | 命中占比 | LIKE p95 | MATCH p95 |
|---|---|---|---|
| 冷词「馆际互借」 | 0.37% | 9.53 ms | 1.42 ms |
| 热词「图书馆」 | **26%** | **0.70 ms** | 32.32 ms |

**原因**：`ORDER BY updated_at DESC LIMIT 20` 与 `idx_audit_list` 的索引序一致，
热词命中密集 ⇒ 顺着索引扫几十行就凑够 20 条，**短路**掉了全表扫描；
而 FULLTEXT 必须把所有命中行取出来算 relevance、再 `filesort`，命中 2 684 行时反而更贵。

**结论**：FULLTEXT 的收益来自**冷词 / 无短路**，而搜索框的真实形态就是冷词
（用户搜的是具体的东西）。这**不**说明 FULLTEXT 在所有场景都更快——
**热词 + 按时间取页**这种组合下旧方案更划算，若将来要优化，正确做法是
按命中占比做一个路由（本任务不做，仅登记）。

## 16. 附带验证（反向对照，证明结论不是空跑）

| 断言 | 结果 |
|---|---|
| 冷/热词各 37 条植入行**全部**被 MATCH 找到 | ✅ 漏 0 条（漏了说明 ngram 未生效） |
| `IGNORE INDEX (ft_topic_search)` 后查询报错 | ✅ `ERR 1191 Can't find FULLTEXT index` → 证明依赖的正是本索引 |
| MATCH 命中行的正文确实含**全部 bigram** | ✅ 异常 0 条 |
| 空词 / 纯符号 `"+++---~*"` → 退化为 `page_topics` | ✅ 结果**不含 `relevance` 列**且与 `page_topics` 同批 id |
| `"-图书馆"` 与 `"图书馆"` 结果一致 | ✅ `-` 被净化，未被当成 boolean「排除」 |
| 多词 `"宿舍 食堂"` → AND 语义 | ✅ AND 646 行 ≤ OR 4 635 行 |
| `page=0 / size=0 / size=999` 归一化 | ✅ 不抛异常 |
| 索引脚本重复执行 | ✅ `[skip] already exists`，列数仍为 2（幂等） |
| 压测后数据回基线 | ✅ 10 023 → **23** 行 |

## 17. 答辩口径（一句话）

> 帖子表**原本没有任何全文索引**，关键词只能前置通配符 LIKE ⇒ 全表扫描；
> 新增 `FULLTEXT(title, content) WITH PARSER ngram` 后，**1 万条帖子下冷词查询
> p95 从 9.53ms 降到 1.42ms（6.7x），全量计数 10.92ms → 0.92ms（11.9x）**；
> 并**如实记录**了热词因 `LIMIT` 短路而旧方案更快的边界，没有把基准做成"只挑好看的数"。

## 18. 复现命令

```powershell
# 1) 建索引（幂等，可重复执行）
mysql --host=127.0.0.1 --port=3307 -u<user> -p xiaojietong < db/sql/19_topic_fulltext.sql

# 2) 压测（自动种子 1 万行 → 实测 → 清理还原）
cd <repo>
$env:XJT_DB_PASSWORD='***'; $env:XJT_DB_PORT='3307'
E:/miniconda3/python.exe tools/bench_c26_topic_search.py --rows 10000 --rounds 15
```
