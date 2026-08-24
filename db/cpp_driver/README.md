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
├── include/jt_db/dao/     # DAO 层
│   ├── user_dao.h         # ✅ 完整实现（范式）
│   ├── library_dao.h      # ⏳ 骨架（座位/空教室/拥挤度，待表结构）
│   ├── forum_dao.h        # ⏳ 骨架（帖子/评论/点赞）
│   ├── secondhand_dao.h   # ⏳ 骨架（二手/求购/匹配）
│   ├── job_dao.h          # ⏳ 骨架（岗位/投递/评分）
│   └── life_dao.h         # ⏳ 骨架（通知/商家/外卖）
├── pybind/pybind_wrapper.cpp  # pybind11 绑定（含 UserDAO）
├── test/
│   ├── main.cpp           # C++ 原生测试
│   ├── test_py.py         # Python 侧集成测试（连接池/事务）
│   └── test_dao.py        # DAO 层集成测试
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

先初始化数据库：
```bash
mysql -u root -p < ../sql/01_schema.sql
mysql -u root -p xiaojietong < ../sql/02_init_data.sql
```

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

其余 DAO（Library/Forum/Secondhand/Job/Life）已给出**方法签名骨架**，表结构由成员4 设计后，在 `src/dao/` 下参照 `user_dao.cpp` 范式补实现并绑定。
