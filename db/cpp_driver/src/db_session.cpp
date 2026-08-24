// 校捷通 C++ 数据访问层 · 会话实现

#include "jt_db/db_session.h"

#include "jt_db/connection_pool.h"

namespace jt_db {

thread_local std::shared_ptr<MysqlConnection> DbSession::txn_;

std::shared_ptr<MysqlConnection> DbSession::current() {
    if (txn_) return txn_;
    return ConnectionPool::instance().get();
}

void DbSession::set_txn(std::shared_ptr<MysqlConnection> conn) {
    txn_ = std::move(conn);
}

void DbSession::clear_txn() {
    txn_.reset();
}

}  // namespace jt_db
