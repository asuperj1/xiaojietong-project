// 校捷通 C++ 数据访问层 · UserDAO 实现（范式示例）

#include "jt_db/dao/user_dao.h"

#include "jt_db/db_session.h"

namespace jt_db {

// 说明（审计 SEC-06）：用户行的查询必须包含
// status / is_deleted / student_no / major / grade / campus 这几列，否则：
//   · backend/app/core/deps.py 的「账号已禁用」校验永远拿不到 status → 死代码；
//   · user.py 的 _view() 与 life.py 的按年级推送永远拿到空值。
// 另：find_by_id 额外过滤 is_deleted = 0，使软删用户的既有 token 立即失效。
//
// C22 追加：鉴权路径必须带上 `token_version` 与 `student_no_updated_at`，否则：
//   · deps.py 的 token_version 校验恒为 0 → 登出后旧 token 仍能用（失效功能成死代码）；
//   · user.py 的「学号 1 次/7 天」限频拿不到上次修改时间 → 限频失效。
//   这两列由 db/sql/17_user_student_no.sql 提供，部署前必须先执行该脚本。

std::optional<Row> UserDAO::find_by_openid(const std::string& openid) {
    auto rows = DbSession::current()->query(
        "SELECT id, openid, nickname, avatar, phone, role, status, is_deleted, "
        "student_no, student_no_updated_at, major, grade, campus, token_version FROM `user` "
        "WHERE openid = ?",
        {std::string(openid)});
    if (rows.empty()) return std::nullopt;
    return rows.front();
}

std::optional<Row> UserDAO::find_by_id(long long id) {
    auto rows = DbSession::current()->query(
        "SELECT id, openid, nickname, avatar, phone, role, status, is_deleted, "
        "student_no, student_no_updated_at, major, grade, campus, token_version FROM `user` "
        "WHERE id = ? AND is_deleted = 0",
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
            "SELECT id, openid, nickname, avatar, phone, role, status, is_deleted, "
            "student_no, major, grade, campus, created_at FROM `user` "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            {limit, offset});
    }
    return DbSession::current()->query(
        "SELECT id, openid, nickname, avatar, phone, role, status, is_deleted, "
        "student_no, major, grade, campus, created_at FROM `user` "
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

// ------------------------------------------------------------------ C22

bool UserDAO::update_student_no(long long id, const std::string& student_no) {
    auto [affected, _] = DbSession::current()->execute(
        "UPDATE `user` SET student_no = ?, student_no_updated_at = NOW() "
        "WHERE id = ? AND is_deleted = 0",
        {std::string(student_no), id});
    return affected > 0;
}

long long UserDAO::student_no_change_remaining_days(long long id,
                                                    long long interval_days) {
    if (interval_days < 0) interval_days = 0;
    auto rows = DbSession::current()->query(
        "SELECT IF(student_no_updated_at IS NULL, 0, "
        "          GREATEST(0, CEIL(TIMESTAMPDIFF(SECOND, NOW(), "
        "                   student_no_updated_at + INTERVAL ? DAY) / 86400))) AS remaining_days "
        "FROM `user` WHERE id = ? AND is_deleted = 0",
        {interval_days, id});
    if (rows.empty()) return -1;
    const auto it = rows.front().find("remaining_days");
    if (it == rows.front().end() || it->second.empty()) return 0;
    return std::stoll(it->second);
}

long long UserDAO::bump_token_version(long long id) {
    auto [affected, _] = DbSession::current()->execute(
        "UPDATE `user` SET token_version = token_version + 1 WHERE id = ?",
        {id});
    if (affected <= 0) return -1;
    auto rows = DbSession::current()->query(
        "SELECT token_version FROM `user` WHERE id = ?", {id});
    if (rows.empty()) return -1;
    const auto it = rows.front().find("token_version");
    if (it == rows.front().end() || it->second.empty()) return -1;
    return std::stoll(it->second);
}

long long UserDAO::count() {
    auto rows = DbSession::current()->query("SELECT COUNT(*) AS c FROM `user`");
    if (rows.empty()) return 0;
    return std::stoll(rows.front().at("c"));
}

}  // namespace jt_db
