#pragma once
// 校捷通 C++ 数据访问层 · UserDAO（范式示例）
//
// 表结构：user(id, openid, nickname, avatar, phone, role, created_at, updated_at)
// 说明：本 DAO 为"完整实现"范式，其余 DAO 请参照本文件的模式编写。
// 方法内部通过 DbSession::current() 获取连接（事务内自动复用事务连接）。

#include <optional>
#include <string>

#include "jt_db/types.h"

namespace jt_db {

class UserDAO {
public:
    // 微信登录：按 openid 查询（不存在返回 nullopt）
    std::optional<Row> find_by_openid(const std::string& openid);
    std::optional<Row> find_by_id(long long id);

    // 分页查询；role 传空串表示不过滤
    QueryResult page(int page, int size, const std::string& role = "");

    // 创建用户，返回自增 id（失败返回 -1）
    long long create(const std::string& openid, const std::string& nickname = "",
                     const std::string& avatar = "", const std::string& phone = "",
                     long long role = 0);

    // 更新昵称/头像
    bool update_profile(long long id, const std::string& nickname,
                        const std::string& avatar);
    bool update_role(long long id, long long role);
    bool remove(long long id);

    long long count();
};

}  // namespace jt_db
