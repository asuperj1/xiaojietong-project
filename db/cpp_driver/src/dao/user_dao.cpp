// 校捷通 C++ 数据访问层 · UserDAO 实现（范式示例）

#include "jt_db/dao/user_dao.h"

#include "jt_db/db_session.h"

namespace jt_db {

std::optional<Row> UserDAO::find_by_openid(const std::string& openid) {
    auto rows = DbSession::current()->query(
        "SELECT id, openid, nickname, avatar, phone, role FROM `user` WHERE openid = ?",
        {std::string(openid)});
    if (rows.empty()) return std::nullopt;
    return rows.front();
}

std::optional<Row> UserDAO::find_by_id(long long id) {
    auto rows = DbSession::current()->query(
        "SELECT id, openid, nickname, avatar, phone, role FROM `user` WHERE id = ?",
        {id});
    if (rows.empty()) return std::nullopt;
    return rows.front();
}

QueryResult UserDAO::page(int page, int size, const std::string& role) {
    if (page < 1) page = 1;
    if (size < 1 || size > 100) size = 20;
    const long long offset = static_cast<long long>((page - 1) * size);
    const long long limit = static_cast<long long>(size);

    if (role.empty()) {
        return DbSession::current()->query(
            "SELECT id, openid, nickname, avatar, phone, role, created_at FROM `user` "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            {limit, offset});
    }
    return DbSession::current()->query(
        "SELECT id, openid, nickname, avatar, phone, role, created_at FROM `user` "
        "WHERE role = ? ORDER BY id DESC LIMIT ? OFFSET ?",
        {std::string(role), limit, offset});
}

long long UserDAO::create(const std::string& openid, const std::string& nickname,
                          const std::string& avatar, const std::string& phone,
                          long long role) {
    auto [affected, id] = DbSession::current()->execute(
        "INSERT INTO `user` (openid, nickname, avatar, phone, role) VALUES (?, ?, ?, ?, ?)",
        {std::string(openid), std::string(nickname), std::string(avatar),
         std::string(phone), role});
    return affected > 0 ? id : -1;
}

bool UserDAO::update_profile(long long id, const std::string& nickname,
                             const std::string& avatar) {
    auto [affected, _] = DbSession::current()->execute(
        "UPDATE `user` SET nickname = ?, avatar = ? WHERE id = ?",
        {std::string(nickname), std::string(avatar), id});
    return affected > 0;
}

bool UserDAO::update_role(long long id, long long role) {
    auto [affected, _] = DbSession::current()->execute(
        "UPDATE `user` SET role = ? WHERE id = ?", {role, id});
    return affected > 0;
}

bool UserDAO::remove(long long id) {
    auto [affected, _] = DbSession::current()->execute(
        "DELETE FROM `user` WHERE id = ?", {id});
    return affected > 0;
}

long long UserDAO::count() {
    auto rows = DbSession::current()->query("SELECT COUNT(*) AS c FROM `user`");
    if (rows.empty()) return 0;
    return std::stoll(rows.front().at("c"));
}

}  // namespace jt_db
