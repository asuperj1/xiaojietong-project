#pragma once
// 校捷通 C++ 数据访问层 · 连接配置

#include <string>

namespace jt_db {

struct DbConfig {
    std::string host = "127.0.0.1";
    int         port = 3306;
    std::string user = "root";
    std::string password;
    std::string dbname = "xiaojietong";
};

}  // namespace jt_db
