// 校捷通 C++ 数据访问层 · 连接池实现

#include "jt_db/connection_pool.h"

#include <chrono>

#include "jt_db/transaction.h"

namespace jt_db {

namespace {
constexpr auto kAcquireTimeout = std::chrono::seconds(5);
}

ConnectionPool& ConnectionPool::instance() {
    static ConnectionPool pool;
    return pool;
}

ConnectionPool::~ConnectionPool() {
    {
        std::lock_guard<std::mutex> lk(mu_);
        closed_ = true;
        idle_.clear();
    }
    cv_.notify_all();
}

void ConnectionPool::init(const DbConfig& cfg, size_t min_conn, size_t max_conn) {
    std::lock_guard<std::mutex> lk(mu_);
    cfg_ = cfg;
    min_ = (min_conn == 0) ? 1 : min_conn;
    max_ = std::max<size_t>(max_conn, min_);
    idle_.clear();
    active_ = 0;
    closed_ = false;
    initialized_ = true;

    // 预热 min 个连接；若一个都没成功，则初始化失败（避免"假成功"）
    size_t ok = 0;
    for (size_t i = 0; i < min_; ++i) {
        try {
            auto raw = std::make_unique<MysqlConnection>(cfg_);
            raw->connect();
            idle_.push_back(std::move(raw));
            ++ok;
        } catch (const DbException& e) {
            last_error_ = e.what();
        }
    }
    if (ok == 0) {
        initialized_ = false;
        throw DbException("连接池初始化失败（预热连接全部失败）: " + last_error_);
    }
    initialized_ = true;
}

bool ConnectionPool::initialized() const {
    std::lock_guard<std::mutex> lk(mu_);
    return initialized_;
}

std::shared_ptr<MysqlConnection> ConnectionPool::get() {
    std::unique_lock<std::mutex> lk(mu_);
    if (!initialized_) throw DbException("连接池未初始化，请先调用 init()");

    // 等待：直到有空闲连接 或 未达上限可新建
    while (idle_.empty() && active_ >= max_ && !closed_) {
        if (cv_.wait_for(lk, kAcquireTimeout) == std::cv_status::timeout) {
            throw DbException("获取数据库连接超时（连接池已满）");
        }
    }
    if (closed_) throw DbException("连接池已关闭");

    if (!idle_.empty()) {
        auto raw = idle_.back().release();
        idle_.pop_back();
        std::shared_ptr<MysqlConnection> conn(raw, [this](MysqlConnection* c) { release(c); });
        // 健康检查：失效则尝试重连
        if (!conn->ping()) {
            try {
                conn->connect();
            } catch (...) {
                // 重连失败，交给上层处理
            }
        }
        ++active_;
        return conn;
    }

    return create_connection_locked();
}

std::shared_ptr<MysqlConnection> ConnectionPool::create_connection_locked() {
    auto raw = std::make_unique<MysqlConnection>(cfg_);
    raw->connect();  // 失败抛 DbException
    ++active_;
    return std::shared_ptr<MysqlConnection>(raw.release(),
                                            [this](MysqlConnection* c) { release(c); });
}

std::shared_ptr<Transaction> ConnectionPool::begin_transaction() {
    auto conn = get();
    conn->begin_transaction();
    return std::make_shared<Transaction>(std::move(conn));
}

void ConnectionPool::release(MysqlConnection* conn) {
    std::lock_guard<std::mutex> lk(mu_);
    if (active_ > 0) --active_;

    if (closed_) {  // 池已关闭，直接销毁
        delete conn;
        return;
    }

    if (conn->ping()) {
        // 若连接还处于事务中，先回滚再归还（保证归还时是干净状态）
        if (conn->in_transaction()) {
            try {
                conn->rollback();
            } catch (...) {
            }
        }
        idle_.push_back(std::unique_ptr<MysqlConnection>(conn));
    } else {
        delete conn;  // 失效连接丢弃
    }
    cv_.notify_one();
}

size_t ConnectionPool::idle_count() const {
    std::lock_guard<std::mutex> lk(mu_);
    return idle_.size();
}

size_t ConnectionPool::active_count() const {
    std::lock_guard<std::mutex> lk(mu_);
    return active_;
}

void ConnectionPool::prune_idle() {
    std::lock_guard<std::mutex> lk(mu_);
    for (auto it = idle_.begin(); it != idle_.end();) {
        if (!(*it)->ping()) {
            it = idle_.erase(it);
        } else {
            ++it;
        }
    }
}

}  // namespace jt_db
