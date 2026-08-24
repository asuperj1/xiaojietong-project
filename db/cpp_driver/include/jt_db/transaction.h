#pragma once
// 校捷通 C++ 数据访问层 · 事务
//
// 事务对象独占一个连接（该连接在事务期间不归还连接池）。
// 析构时若未 commit/rollback，自动回滚（RAII，异常安全）。

#include <memory>

#include "jt_db/mysql_connection.h"

namespace jt_db {

class Transaction {
public:
    explicit Transaction(std::shared_ptr<MysqlConnection> conn);
    ~Transaction();

    Transaction(const Transaction&) = delete;
    Transaction& operator=(const Transaction&) = delete;

    void commit();
    void rollback();

    // 供 pybind11 上下文管理器使用
    bool finished() const { return finished_; }
    // 事务独占的连接（pybind 层用于绑定线程，保证事务内 SQL 走同一连接）
    std::shared_ptr<MysqlConnection> connection() const { return conn_; }

private:
    std::shared_ptr<MysqlConnection> conn_;
    bool finished_ = false;
};

}  // namespace jt_db
