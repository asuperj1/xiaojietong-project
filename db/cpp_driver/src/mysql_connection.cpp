// 校捷通 C++ 数据访问层 · 单连接封装实现

#include "jt_db/mysql_connection.h"

#include <cstring>
#include <vector>

namespace jt_db {
namespace {

// 参数绑定辅助：将 Params 转成 MYSQL_BIND 数组，同时持有数据缓冲
// （数据必须存活到 mysql_stmt_execute 完成）
struct ParamBindings {
    std::vector<MYSQL_BIND> binds;
    std::vector<long long> ints;
    std::vector<double> dbls;
    std::vector<std::string> strs;
    std::vector<unsigned long> lengths;
    // MySQL 8.0 中 is_null 为 bool*，且 my_bool 已移除；
    // 用 unsigned char 存储 NULL 标记，绑定处强转为 bool*
    std::vector<unsigned char> nulls;

    explicit ParamBindings(const Params& params) {
        if (params.empty()) return;
        binds.resize(params.size());
        lengths.resize(params.size());
        nulls.resize(params.size(), 0);
        ints.reserve(params.size());
        dbls.reserve(params.size());
        strs.reserve(params.size());

        for (size_t i = 0; i < params.size(); ++i) {
            MYSQL_BIND& b = binds[i];
            std::memset(&b, 0, sizeof(b));
            b.is_null = reinterpret_cast<bool*>(&nulls[i]);
            b.length = &lengths[i];

            const ParamValue& v = params[i];
            if (std::holds_alternative<long long>(v)) {
                ints.push_back(std::get<long long>(v));
                b.buffer_type = MYSQL_TYPE_LONGLONG;
                b.buffer = &ints.back();
            } else if (std::holds_alternative<double>(v)) {
                dbls.push_back(std::get<double>(v));
                b.buffer_type = MYSQL_TYPE_DOUBLE;
                b.buffer = &dbls.back();
            } else if (std::holds_alternative<std::string>(v)) {
                strs.push_back(std::get<std::string>(v));
                lengths[i] = static_cast<unsigned long>(strs.back().size());
                b.buffer_type = MYSQL_TYPE_STRING;
                b.buffer = const_cast<char*>(strs.back().data());
                b.buffer_length = static_cast<unsigned long>(strs.back().size());
            } else {
                b.buffer_type = MYSQL_TYPE_NULL;
                nulls[i] = 1;
            }
        }
    }

    MYSQL_BIND* data() { return binds.data(); }
    size_t size() const { return binds.size(); }
};

}  // namespace

MysqlConnection::MysqlConnection(const DbConfig& cfg) : cfg_(cfg) {}

MysqlConnection::~MysqlConnection() { close(); }

void MysqlConnection::connect() {
    if (connected_) return;

    if (mysql_ == nullptr) {
        mysql_ = mysql_init(nullptr);
        if (mysql_ == nullptr) {
            throw DbException("mysql_init 失败（内存不足）");
        }
    }

    unsigned int connect_timeout = 5;
    mysql_options(mysql_, MYSQL_OPT_CONNECT_TIMEOUT, &connect_timeout);

    if (mysql_real_connect(mysql_, cfg_.host.c_str(), cfg_.user.c_str(),
                           cfg_.password.c_str(), cfg_.dbname.c_str(), cfg_.port,
                           nullptr, 0) == nullptr) {
        last_error_ = mysql_error(mysql_);
        mysql_close(mysql_);
        mysql_ = nullptr;
        connected_ = false;
        throw DbException("连接 MySQL 失败: " + last_error_);
    }

    mysql_set_character_set(mysql_, "utf8mb4");
    connected_ = true;
}

void MysqlConnection::ensure_connected() {
    if (!connected_) connect();
}

bool MysqlConnection::ping() {
    if (mysql_ == nullptr) return false;
    if (mysql_ping(mysql_) == 0) {
        connected_ = true;
        return true;
    }
    connected_ = false;
    return false;
}

void MysqlConnection::close() {
    if (mysql_ != nullptr) {
        mysql_close(mysql_);
        mysql_ = nullptr;
    }
    connected_ = false;
    in_transaction_ = false;
}

MYSQL_STMT* MysqlConnection::prepare_statement(const std::string& sql) {
    ensure_connected();
    MYSQL_STMT* stmt = mysql_stmt_init(mysql_);
    if (stmt == nullptr) {
        throw DbException("mysql_stmt_init 失败");
    }
    if (mysql_stmt_prepare(stmt, sql.c_str(), static_cast<unsigned long>(sql.size())) != 0) {
        std::string err = mysql_stmt_error(stmt);
        mysql_stmt_close(stmt);
        throw DbException("SQL 预处理失败: " + err + " | SQL: " + sql);
    }
    return stmt;
}

QueryResult MysqlConnection::query(const std::string& sql, const Params& params) {
    MYSQL_STMT* stmt = prepare_statement(sql);
    try {
        if (mysql_stmt_param_count(stmt) != params.size()) {
            throw DbException("参数数量与 SQL 占位符不一致: SQL=" + sql);
        }

        ParamBindings bindings(params);
        if (bindings.size() > 0 &&
            mysql_stmt_bind_param(stmt, bindings.data()) != 0) {
            throw DbException("绑定参数失败: " + std::string(mysql_stmt_error(stmt)));
        }

        if (mysql_stmt_execute(stmt) != 0) {
            throw DbException("执行失败: " + std::string(mysql_stmt_error(stmt)));
        }

        QueryResult result;
        MYSQL_RES* meta = mysql_stmt_result_metadata(stmt);
        if (meta != nullptr) {
            mysql_stmt_store_result(stmt);
            const unsigned int ncols = mysql_num_fields(meta);
            // 注意：必须 store_result 之后再取字段，max_length 才是真实值
            MYSQL_FIELD* fields = mysql_fetch_fields(meta);

            std::vector<MYSQL_BIND> rb(ncols);
            std::vector<std::vector<char>> bufs(ncols);
            std::vector<unsigned long> lens(ncols, 0);
            std::vector<unsigned char> is_null(ncols, 0);

            for (unsigned int i = 0; i < ncols; ++i) {
                const unsigned long cap =
                    (fields[i].max_length > 0) ? fields[i].max_length + 1 : 255;
                bufs[i].resize(cap);
                std::memset(&rb[i], 0, sizeof(rb[i]));
                rb[i].buffer_type = MYSQL_TYPE_STRING;  // 统一按字符串取回
                rb[i].buffer = bufs[i].data();
                rb[i].buffer_length = static_cast<unsigned long>(bufs[i].size());
                rb[i].length = &lens[i];
                rb[i].is_null = reinterpret_cast<bool*>(&is_null[i]);
            }
            mysql_stmt_bind_result(stmt, rb.data());

            while (true) {
                const int rc = mysql_stmt_fetch(stmt);
                if (rc == 0) {
                    Row row;
                    for (unsigned int i = 0; i < ncols; ++i) {
                        if (is_null[i]) {
                            row[fields[i].name] = "";
                        } else {
                            row[fields[i].name].assign(bufs[i].data(), lens[i]);
                        }
                    }
                    result.push_back(std::move(row));
                } else if (rc == MYSQL_NO_DATA) {
                    break;
                } else {
                    throw DbException("读取结果失败: " + std::string(mysql_stmt_error(stmt)));
                }
            }
            mysql_free_result(meta);
        }
        mysql_stmt_close(stmt);
        return result;
    } catch (...) {
        mysql_stmt_close(stmt);
        throw;
    }
}

ExecResult MysqlConnection::execute(const std::string& sql, const Params& params) {
    MYSQL_STMT* stmt = prepare_statement(sql);
    try {
        if (mysql_stmt_param_count(stmt) != params.size()) {
            throw DbException("参数数量与 SQL 占位符不一致: SQL=" + sql);
        }

        ParamBindings bindings(params);
        if (bindings.size() > 0 &&
            mysql_stmt_bind_param(stmt, bindings.data()) != 0) {
            throw DbException("绑定参数失败: " + std::string(mysql_stmt_error(stmt)));
        }

        if (mysql_stmt_execute(stmt) != 0) {
            throw DbException("执行失败: " + std::string(mysql_stmt_error(stmt)));
        }

        const long long affected = static_cast<long long>(mysql_stmt_affected_rows(stmt));
        const long long last_id = static_cast<long long>(mysql_stmt_insert_id(stmt));
        mysql_stmt_close(stmt);
        return {affected, last_id};
    } catch (...) {
        mysql_stmt_close(stmt);
        throw;
    }
}

void MysqlConnection::begin_transaction() {
    ensure_connected();
    if (in_transaction_) return;
    if (mysql_autocommit(mysql_, 0) != 0) {
        throw DbException("开启事务失败: " + std::string(mysql_error(mysql_)));
    }
    in_transaction_ = true;
}

void MysqlConnection::commit() {
    if (!in_transaction_) throw DbException("当前无活动事务，无法 commit");
    if (mysql_commit(mysql_) != 0) {
        in_transaction_ = false;
        throw DbException("commit 失败: " + std::string(mysql_error(mysql_)));
    }
    mysql_autocommit(mysql_, 1);
    in_transaction_ = false;
}

void MysqlConnection::rollback() {
    if (!in_transaction_) throw DbException("当前无活动事务，无法 rollback");
    if (mysql_rollback(mysql_) != 0) {
        in_transaction_ = false;
        throw DbException("rollback 失败: " + std::string(mysql_error(mysql_)));
    }
    mysql_autocommit(mysql_, 1);
    in_transaction_ = false;
}

}  // namespace jt_db
