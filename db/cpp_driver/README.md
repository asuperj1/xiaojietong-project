# db/cpp_driver —— C++ 数据访问层（pybind11 扩展）

高性能 MySQL 数据访问模块：**连接池 + 预处理语句 + 事务**，编译为 Python 扩展 `jt_db`，供后端 FastAPI 直接 `import`。

## 目录

```
cpp_driver/
├── include/jt_db/
│   ├── types.h            # 公共类型（ParamValue/Params/Row/QueryResult）
│   ├── db_config.h        # 连接配置 DbConfig
│   ├── mysql_connection.h # 单连接封装（MySQL C API + 预处理语句 + 事务）
│   ├── connection_pool.h  # 连接池（RAII 获取/归还、超时、健康检查）
│   └── transaction.h      # 事务（析构自动回滚，异常安全）
├── src/                   # 对应实现
│   ├── db_session.cpp     # 会话层（事务/池连接统一供给）
│   └── dao/user_dao.cpp   # UserDAO（范式实现）
├── include/jt_db/dao/     # DAO 层（6 个业务域，**全部已完整实现并绑定**）
│   ├── user_dao.h         # ✅ 用户体系
│   ├── library_dao.h      # ✅ 图书馆（空教室/座位/预约/拥挤度）
│   ├── forum_dao.h        # ✅ 论坛（帖子/评论/点赞/待审/热榜）
│   ├── secondhand_dao.h   # ✅ 二手（商品/求购/匹配/下单）
│   ├── job_dao.h          # ✅ 兼职（岗位/投递/信誉/黑名单）
│   └── life_dao.h         # ✅ 生活（通知/商家/菜单/订单）
├── pybind/pybind_wrapper.cpp  # pybind11 绑定（查询/写操作/事务 + 6 个 DAO）
├── test/
│   ├── main.cpp                    # C++ 原生测试
│   ├── test_py.py                  # Python 侧集成测试（连接池/事务）
│   ├── test_dao.py                 # DAO 层集成测试
│   ├── test_all_dao.py             # 6 个 DAO 全量断言（15 组）
│   ├── test_last_insert_id.py      # C8 回归：last_insert_id 取值正确性
│   ├── bench_dao.py                # C6 单线程基准（vs pymysql）
│   └── bench_concurrent.py         # C9 并发基准（1/4/8/16 线程）
└── CMakeLists.txt         # 跨平台构建（Win .pyd / Linux .so）
```

## 依赖

| 依赖 | 安装 |
|---|---|
| MySQL Server 8.0（C API） | Windows 安装包自带 `include/mysql.h`、`lib/libmysql.lib`；Linux 执行 `sudo apt install libmysqlclient-dev` |
| pybind11 | `pip install pybind11`（CMake 找不到时自动 FetchContent 拉取） |
| CMake ≥ 3.20 | VS2022/VS2026 自带，或独立安装 |
| C++17 编译器 | VS 生成器 / GCC |

## VS Code 开发（推荐）

1. 用 VS Code 打开本目录：`code db/cpp_driver`
2. 安装推荐扩展（首次打开会提示）：C/C++、CMake Tools、Python
3. CMake Tools 自动检测环境（MySQL 目录 / pybind11 / 编译器均自动发现）
4. 底部状态栏选择 preset `windows`（或 `windows-ninja`），点「生成」/ 按 `F7` 构建
5. 调试：`Ctrl+F5` 选择「调试 jt_db_test」；运行 Python 测试走 Tasks 面板

一键构建脚本（自动检测 MySQL，等价于手动步骤）：
```powershell
powershell -ExecutionPolicy Bypass -File scripts/build.ps1        # Release
powershell -ExecutionPolicy Bypass -File scripts/build.ps1 -Config Debug
```

## Windows 构建（开发环境，等效命令）

```bat
cd db\cpp_driver
cmake --preset windows          # 或手动: cmake -B build -A x64
cmake --build --preset windows-release
```

产物：
- `backend/app/db/native/jt_db.pyd` —— Python 扩展（构建时自动拷贝 libmysql.dll）
- `build/Release/jt_db_test.exe` —— C++ 原生测试

> 可用 VS Code/VS 直接打开 `db/cpp_driver` 文件夹（CMake 工程），选择 Release 后构建 `jt_db` 目标。

> ⚠️ Windows 下如果 Python 正加载着 `jt_db.pyd`（如 uvicorn 运行中），重新构建会报 `Permission denied`。
> 构建前先关闭占用进程：`taskkill /F /IM python.exe`，或停止正在运行的 uvicorn。

## Linux 构建（生产环境）

```bash
sudo apt install -y libmysqlclient-dev
pip install pybind11
cd db/cpp_driver
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
# 产物：backend/app/db/native/jt_db.cpython-*.so
```

## 运行测试

先初始化数据库（脚本按模块拆分，执行顺序 `00 → 01..13 → 99`）：
```bash
cd ../sql
mysql < 00_database.sql
for f in 01_user 02_ai_chat 03_agent 04_library 05_secondhand 06_job \
         07_forum 08_map 09_life 10_ai_train 11_audit 12_notice_delivery 13_index_optimize; do
  mysql xiaojietong < "$f.sql"
done
mysql xiaojietong < 99_init_data.sql
```

> 表清单、索引优化说明与导入细节见 `db/sql/README.md`。

> 本机 MySQL 实例运行在 **3307** 端口（非默认 3306），测试前用 `XJT_DB_PORT` 指定，或按实际修改默认值。

C++ 原生测试：
```bash
XJT_DB_PORT=3307 XJT_DB_PASSWORD=你的密码 ./build/Release/jt_db_test.exe
```

Python 集成测试（验证连接池/参数化/事务）：
```bash
cd test
XJT_DB_PORT=3307 XJT_DB_PASSWORD=你的密码 python test_py.py
```

## 性能（C9 实测）

| 场景（16 并发，相对 pymysql 直连） | 比值 |
|---|---|
| 大结果集（500 行） | **2.84x** |
| 分页查询（10 行） | 1.54x |
| 主键点查（单行） | 0.40~0.76x（抖动较大） |

- **关键优化**：`JT_DB_RELEASE_GIL`（CMake 开关，**默认 ON**）—— 让 query/execute 在
  MySQL 网络与协议处理期间释放 GIL，使多个请求真正并行（参数转换与结果构造仍持 GIL）。
- pymysql 的 QPS 随并发下降 **53%**（6904→3263），而 jt_db 大结果集**上升 63%**（3398→5552）。
- 若负载以**极小结果集的高并发点查**为主，可 `-DJT_DB_RELEASE_GIL=OFF` 规避 GIL 调度抖动。

完整数据、优化前后对比与复现命令见 `docs/perf-benchmark.md` 第二部分。

## Python 使用示例

```python
import jt_db

# 1. 初始化连接池（应用启动时一次）
jt_db.init_pool("127.0.0.1", 3306, "root", "密码", "xiaojietong", 2, 16)

# 2. 参数化查询（? 占位符，内部预处理语句防注入）
rows = jt_db.query("SELECT * FROM `user` WHERE role = ?", [0])
print(rows)   # [{'id': '1', 'nickname': '...', ...}, ...]

# 3. 写操作 → (受影响行数, 自增ID)
affected, last_id = jt_db.execute(
    "INSERT INTO `user` (openid, nickname) VALUES (?, ?)", ["oXJT_1", "张三"])

# 4. 事务：with 正常退出自动 commit，异常自动 rollback
with jt_db.begin() as tx:
    jt_db.execute("UPDATE `user` SET nickname = ? WHERE id = ?", ["李四", 1])
    # 可选：tx.commit() / tx.rollback()
```

> 后端封装见 `backend/app/db/cpp_bridge.py`（含日志、未初始化保护、健康检查、`user_dao()`）。
> 整体设计见 `docs/architecture.md` 第 5 章。

## DAO 层（数据访问对象）

按业务域聚合查询，内部统一通过 `DbSession::current()` 获取连接（**事务内自动复用事务连接**，保证事务一致）。

```python
import jt_db

jt_db.init_pool("127.0.0.1", 3307, "root", "密码", "xiaojietong", 2, 16)

dao = jt_db.UserDAO()
u = dao.find_by_openid("oXJT_TEST_0001")   # 返回 dict 或 None
users = dao.page(1, 20, role="")           # 分页
uid = dao.create("oXJT_NEW", "新人", "", "", 0)

with jt_db.begin():                        # 事务内 DAO 走同一连接
    dao.update_profile(uid, "新昵称", "")
    dao.update_role(uid, 1)
```

**6 个 DAO 均已完整实现并绑定**，覆盖 22 张表：`src/dao/` 下依次为 `user_dao.cpp`、`library_dao.cpp`、
`forum_dao.cpp`、`secondhand_dao.cpp`、`job_dao.cpp`、`life_dao.cpp`。
集成验证见 `test/test_all_dao.py`（15 组断言全部通过）。

### 尚未纳入 DAO 的表（属后续 C10 收敛范围）

`favorite`、`report`、`agent_task`、`reminder`、`poi`、`navigation_log`、`knowledge_doc`、`knowledge_chunk` 等
目前由后端经 `cpp_bridge.query/execute` 直接执行（共 **137 处 / 19 个文件**）——
"所有 DB 访问走 C++ 层"已成立，但尚未收敛到 DAO 抽象，这是 C10 的目标。
