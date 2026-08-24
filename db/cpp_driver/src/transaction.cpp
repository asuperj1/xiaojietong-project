// 校捷通 C++ 数据访问层 · 事务实现

#include "jt_db/transaction.h"

namespace jt_db {

Transaction::Transaction(std::shared_ptr<MysqlConnection> conn) : conn_(std::move(conn)) {}

Transaction::~Transaction() {
    // 未显式结束则自动回滚（异常安全）
    if (!finished_ && conn_) {
        try {
            conn_->rollback();
        } catch (...) {
        }
    }
}

void Transaction::commit() {
    conn_->commit();
    finished_ = true;
}

void Transaction::rollback() {
    conn_->rollback();
    finished_ = true;
}

}  // namespace jt_db
