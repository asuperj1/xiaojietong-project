// 校捷通 C++ 数据访问层 · C++ 原生集成测试
//
// 需要可用的 MySQL，且已执行 db/sql/01_schema.sql + 02_init_data.sql。
// 连接参数通过环境变量注入（避免硬编码密码）：
//   XJT_DB_HOST     默认 127.0.0.1
//   XJT_DB_PORT     默认 3307（本机 MySQL 实例端口，按实际修改）
//   XJT_DB_USER     默认 root
//   XJT_DB_PASSWORD 必填
//   XJT_DB_NAME     默认 xiaojietong
//
// 示例：XJT_DB_PASSWORD=xxx ./jt_db_test

#include <cstdlib>
#include <iostream>
#include <string>

#include "jt_db/connection_pool.h"
#include "jt_db/transaction.h"

using namespace jt_db;

namespace {
std::string env(const char* name, const std::string& def = "") {
    const char* v = std::getenv(name);
    return (v != nullptr) ? v : def;
}
}  // namespace

int main() {
    DbConfig cfg;
    cfg.host = env("XJT_DB_HOST", "127.0.0.1");
    cfg.port = std::stoi(env("XJT_DB_PORT", "3307"));
    cfg.user = env("XJT_DB_USER", "root");
    cfg.password = env("XJT_DB_PASSWORD");
    cfg.dbname = env("XJT_DB_NAME", "xiaojietong");

    if (cfg.password.empty()) {
        std::cerr << "请先设置环境变量 XJT_DB_PASSWORD 再运行测试" << std::endl;
        return 2;
    }

    try {
        auto& pool = ConnectionPool::instance();
        pool.init(cfg, /*min*/ 2, /*max*/ 8);

        // [1] 基础查询
        auto rows = pool.get()->query("SELECT * FROM `user`");
        std::cout << "[1] 查询 user 表行数: " << rows.size() << std::endl;

        // [2] 参数化查询（预处理语句，? 占位）
        auto rows2 = pool.get()->query(
            "SELECT id, nickname FROM `user` WHERE role = ?", {0LL});
        for (const auto& r : rows2) {
            std::cout << "    id=" << r.at("id")
                      << " nickname=" << r.at("nickname") << std::endl;
        }

        // [3] 事务：插入后回滚（验证事务边界与连接复用）
        {
            auto tx = pool.begin_transaction();
            auto [affected, id] = pool.get()->execute(
                "INSERT INTO `user` (openid, nickname, role) VALUES (?, ?, ?)",
                {std::string("oXJT_CPP_TEST"), std::string("C++事务测试"), 0LL});
            std::cout << "[3] 事务内插入 affected=" << affected
                      << " last_insert_id=" << id << std::endl;
            tx->rollback();
            std::cout << "    已回滚（user 表不应出现该测试数据）" << std::endl;
        }

        // [4] 事务：提交（数据可回查）
        {
            auto tx = pool.begin_transaction();
            auto [affected, id] = pool.get()->execute(
                "INSERT INTO `user` (openid, nickname, role) VALUES (?, ?, ?)",
                {std::string("oXJT_CPP_TEST_COMMIT"), std::string("C++提交测试"), 0LL});
            std::cout << "[4] 事务内插入 affected=" << affected
                      << " last_insert_id=" << id << std::endl;
            tx->commit();
            auto chk = pool.get()->query(
                "SELECT COUNT(*) AS c FROM `user` WHERE openid = ?",
                {std::string("oXJT_CPP_TEST_COMMIT")});
            std::cout << "    已提交，回查条数: " << chk.front().at("c") << std::endl;
        }

        // [5] 连接池统计
        std::cout << "[5] 连接池统计 idle=" << pool.idle_count()
                  << " active=" << pool.active_count() << std::endl;

        std::cout << "\n【成功】C++ 数据访问层全部测试通过！" << std::endl;
        return 0;
    } catch (const DbException& e) {
        std::cerr << "\n【数据库异常】" << e.what() << std::endl;
        return 1;
    }
}
