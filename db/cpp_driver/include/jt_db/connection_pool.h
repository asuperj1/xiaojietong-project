#pragma once
// 校捷通 C++ 数据访问层 · 连接池
//
// 线程安全。通过 shared_ptr 的 RAII 语义管理连接生命周期：
//  - get()        从池获取一个连接（shared_ptr）
//  - 最后一个 shared_ptr 析构时，连接自动归还空闲队列（或失效丢弃）
//  - 支持连接最大上限与超时获取（默认 5s）
//  - 单例模式，全局唯一。

#include <condition_variable>
#include <memory>
#include <mutex>
#include <vector>

#include "jt_db/db_config.h"
#include "jt_db/mysql_connection.h"

namespace jt_db {

class ConnectionPool {
public:
    static ConnectionPool& instance();

    ConnectionPool(const ConnectionPool&) = delete;
    ConnectionPool& operator=(const ConnectionPool&) = delete;

    // 初始化（仅一次；重复调用会重建池）
    void init(const DbConfig& cfg, size_t min_conn = 2, size_t max_conn = 16);
    bool initialized() const;

    // 获取连接（超时抛 DbException）
    std::shared_ptr<MysqlConnection> get();
    // 开启事务：独占一个连接并关闭 autocommit
    std::shared_ptr<class Transaction> begin_transaction();

    size_t idle_count() const;
    size_t active_count() const;

    // 对所有空闲连接做 ping（后台维护用）
    void prune_idle();

private:
    ConnectionPool() = default;
    ~ConnectionPool();

    // shared_ptr 归还回调
    void release(MysqlConnection* conn);
    // 创建新连接（加锁状态下调用）
    std::shared_ptr<MysqlConnection> create_connection_locked();

    DbConfig cfg_;
    size_t   min_ = 2;
    size_t   max_ = 16;
    bool     initialized_ = false;
    std::string last_error_;  // 最近一次预热/连接失败原因

    mutable std::mutex mu_;
    std::condition_variable cv_;
    std::vector<std::unique_ptr<MysqlConnection>> idle_;
    size_t active_ = 0;   // 在池外流通的连接数
    bool   closed_ = false;  // 池已关闭（析构中），不再回收连接
};

}  // namespace jt_db
