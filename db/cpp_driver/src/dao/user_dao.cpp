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

// ------------------------------------------------------- C23 · 搜索历史

namespace {

// 把 UTF-8 串截到至多 max_chars 个**字符**（不是字节）。
// 表列是 VARCHAR(128)（按字符计数）；若按字节截断，会把一个汉字的 3 字节切断，
// 产生非法 UTF-8，写库时反而报 1366。
std::string utf8_truncate(const std::string& text, std::size_t max_chars) {
    std::size_t chars = 0;
    std::size_t i = 0;
    while (i < text.size()) {
        if (chars == max_chars) return text.substr(0, i);
        const unsigned char c = static_cast<unsigned char>(text[i]);
        if (c < 0x80) {
            i += 1;
        } else if ((c & 0xE0) == 0xC0) {
            i += 2;
        } else if ((c & 0xF0) == 0xE0) {
            i += 3;
        } else if ((c & 0xF8) == 0xF0) {
            i += 4;
        } else {
            i += 1;  // 非法起始字节：当作单字节前进，不越界
        }
        ++chars;
    }
    return text;
}

// trim 首尾 ASCII 空白（包含制表 / 换行）
std::string trim_ascii(const std::string& text) {
    const char* kWs = " \t\r\n\v\f";
    const auto begin = text.find_first_not_of(kWs);
    if (begin == std::string::npos) return "";
    const auto end = text.find_last_not_of(kWs);
    return text.substr(begin, end - begin + 1);
}

}  // namespace

long long UserDAO::add_search_history(long long user_id, const std::string& keyword) {
    const std::string kw = utf8_truncate(trim_ascii(keyword), 128);
    if (kw.empty()) return -1;  // 空关键词不落库（否则历史里会出现空白条目）

    // `id = LAST_INSERT_ID(id)` 是经典写法：命中唯一键走 UPDATE 分支时，
    // 也能从 last_insert_id 拿回该行 id，省掉一次 SELECT。
    auto [affected, new_id] = DbSession::current()->execute(
        "INSERT INTO user_search_history (user_id, keyword) VALUES (?, ?) "
        "ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id), created_at = CURRENT_TIMESTAMP",
        {user_id, std::string(kw)});
    if (new_id > 0) return new_id;

    // 兑底：同一秒内重复搜索时 UPDATE 是空操作，MySQL 可能不回报 id ⇒ 查一次
    auto rows = DbSession::current()->query(
        "SELECT id FROM user_search_history WHERE user_id = ? AND keyword = ?",
        {user_id, std::string(kw)});
    if (rows.empty()) return -1;
    return std::stoll(rows.front().at("id"));
}

QueryResult UserDAO::list_search_history(long long user_id, int limit) {
    if (limit < 1 || limit > 200) limit = 20;
    // 主排序 created_at DESC 直接走 idx_user_created；
    // 补一个 id DESC 只为兜住“同一秒内多次搜索”的并列顺序。
    return DbSession::current()->query(
        "SELECT id, keyword, created_at FROM user_search_history "
        "WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
        {user_id, static_cast<long long>(limit)});
}

bool UserDAO::delete_search_history(long long user_id, long long history_id) {
    // user_id 必须进 WHERE：否则传入别人的历史 id 就能删掉（越权）
    auto [affected, _] = DbSession::current()->execute(
        "DELETE FROM user_search_history WHERE id = ? AND user_id = ?",
        {history_id, user_id});
    return affected > 0;
}

long long UserDAO::clear_search_history(long long user_id) {
    auto [affected, _] = DbSession::current()->execute(
        "DELETE FROM user_search_history WHERE user_id = ?", {user_id});
    return affected;
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
