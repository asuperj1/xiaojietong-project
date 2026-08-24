#pragma once
// 校捷通 C++ 数据访问层 · 单连接封装（MySQL C API）
//
// 对 mysql.h 的封装：
//  - 连接建立/关闭/健康检查
//  - 预处理语句查询与写操作（MYSQL_STMT + 参数绑定，防注入）
//  - 事务控制（BEGIN/COMMIT/ROLLBACK）
// 所有方法都可能抛出 DbException。

#include <mysql.h>

#include <string>

#include "jt_db/db_config.h"
#include "jt_db/types.h"

namespace jt_db {

class MysqlConnection {
public:
    explicit MysqlConnection(const DbConfig& cfg);
    ~MysqlConnection();

    MysqlConnection(const MysqlConnection&) = delete;
    MysqlConnection& operator=(const MysqlConnection&) = delete;

    // 建立连接（幂等；失败抛 DbException）
    void connect();
    bool is_connected() const { return connected_; }
    // 轻量健康检查：连接断开则返回 false（不自动重连）
    bool ping();
    // 主动关闭并销毁底层句柄
    void close();

    // 查询：? 占位符 + 参数列表 → 行集合
    QueryResult query(const std::string& sql, const Params& params = {});
    // 写操作：? 占位符 + 参数列表 → {受影响行数, 自增ID}
    ExecResult execute(const std::string& sql, const Params& params = {});

    // 事务控制
    void begin_transaction();
    void commit();
    void rollback();
    bool in_transaction() const { return in_transaction_; }

    const std::string& last_error() const { return last_error_; }

private:
    // 执行预处理语句（query / execute 共用底层；参数绑定在实现内联完成）
    MYSQL_STMT* prepare_statement(const std::string& sql);
    void ensure_connected();

    MYSQL*     mysql_ = nullptr;
    DbConfig   cfg_;
    bool       connected_ = false;
    bool       in_transaction_ = false;
    std::string last_error_;
};

}  // namespace jt_db
