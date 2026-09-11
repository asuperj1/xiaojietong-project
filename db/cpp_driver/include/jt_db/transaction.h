#pragma once
// 校捷通 C++ 数据访问层 · 事务
//
// 事务对象独占一个连接（该连接在事务期间不归还连接池）。
// 析构时若未 commit/rollback，自动回滚（RAII，异常安全）。

#include <memory>
#include <thread>

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

    // 创建该事务的线程（审计 CON-02）。
    // 事务连接绑定在 DbSession 的 thread_local 上，解绑必须发生在同一线程；
    // 否则 Transaction.__del__ 在其他线程被 GC 时会误清那个线程自身的事务绑定。
    bool owned_by_current_thread() const {
        return owner_thread_ == std::this_thread::get_id();
    }

private:
    std::shared_ptr<MysqlConnection> conn_;
    bool finished_ = false;
    std::thread::id owner_thread_ = std::this_thread::get_id();
};

}  // namespace jt_db
