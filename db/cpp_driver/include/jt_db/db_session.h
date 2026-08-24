#pragma once
// 校捷通 C++ 数据访问层 · 会话（连接供给）
//
// 统一提供"当前线程可用连接"：
//   - 事务开启期间 → 返回事务独占连接（块内所有 DAO/SQL 走同一连接，保证事务一致）
//   - 非事务       → 从连接池取一个连接
// DAO 层与 pybind 层都应通过 DbSession::current() 获取连接。

#include <memory>

#include "jt_db/mysql_connection.h"

namespace jt_db {

class DbSession {
public:
    // 当前线程可用连接（事务中为事务连接，否则从连接池获取）
    static std::shared_ptr<MysqlConnection> current();

    // 绑定/解除"当前线程事务连接"（由 pybind 事务层在 begin/commit/rollback 时调用）
    static void set_txn(std::shared_ptr<MysqlConnection> conn);
    static void clear_txn();
    static std::shared_ptr<MysqlConnection> txn() { return txn_; }

private:
    static thread_local std::shared_ptr<MysqlConnection> txn_;
};

}  // namespace jt_db
