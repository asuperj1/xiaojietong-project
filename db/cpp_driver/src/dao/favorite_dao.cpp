// 校捷通 C++ 数据访问层 · FavoriteDAO 实现（C10 收敛）

#include "jt_db/dao/favorite_dao.h"

#include "jt_db/db_session.h"

namespace jt_db {

namespace {

// target_type 白名单：只允许 topic / item
// （值直接作为 SQL 字面量出现在 JOIN 条件中，必须限定取值）
bool allowed_target(const std::string& type) {
    return type == "topic" || type == "item";
}

}  // namespace

bool FavoriteDAO::is_favorited(long long user_id, const std::string& target_type,
                               long long target_id) {
    auto rows = DbSession::current()->query(
        "SELECT id FROM favorite WHERE user_id = ? AND target_type = ? AND target_id = ?",
        {user_id, std::string(target_type), target_id});
    return !rows.empty();
}

bool FavoriteDAO::target_exists(const std::string& target_type, long long target_id) {
    if (target_type == "topic") {
        auto rows = DbSession::current()->query(
            "SELECT id FROM topic WHERE id = ? AND is_deleted = 0 AND status != 1",
            {target_id});
        return !rows.empty();
    }
    if (target_type == "item") {
        auto rows = DbSession::current()->query(
            "SELECT id FROM secondhand_item WHERE id = ? AND is_deleted = 0",
            {target_id});
        return !rows.empty();
    }
    return false;
}

bool FavoriteDAO::toggle(long long user_id, const std::string& target_type,
                         long long target_id) {
    if (!allowed_target(target_type)) return false;

    // 依赖 uk_user_target 唯一键：先查后写，事务内保证语义（调用方包 with begin()）
    if (is_favorited(user_id, target_type, target_id)) {
        DbSession::current()->execute(
            "DELETE FROM favorite WHERE user_id = ? AND target_type = ? AND target_id = ?",
            {user_id, std::string(target_type), target_id});
        return false;
    }
    DbSession::current()->execute(
        "INSERT INTO favorite (user_id, target_type, target_id) VALUES (?, ?, ?)",
        {user_id, std::string(target_type), target_id});
    return true;
}

QueryResult FavoriteDAO::page_topics(long long user_id, int limit, int offset) {
    return DbSession::current()->query(
        "SELECT f.id AS favorite_id, f.created_at AS favorited_at, "
        "t.id, t.title, t.category, t.like_count, t.comment_count, t.view_count, "
        "t.audit_status, t.ai_summary, t.is_hot, t.created_at "
        "FROM favorite f JOIN topic t ON t.id = f.target_id "
        "WHERE f.user_id = ? AND f.target_type = 'topic' "
        "AND t.is_deleted = 0 AND t.status != 1 "
        "ORDER BY f.id DESC LIMIT ? OFFSET ?",
        {user_id, static_cast<long long>(limit), static_cast<long long>(offset)});
}

long long FavoriteDAO::count_topics(long long user_id) {
    auto rows = DbSession::current()->query(
        "SELECT COUNT(*) AS c FROM favorite f JOIN topic t ON t.id = f.target_id "
        "WHERE f.user_id = ? AND f.target_type = 'topic' "
        "AND t.is_deleted = 0 AND t.status != 1",
        {user_id});
    if (rows.empty()) return 0;
    return std::stoll(rows.front().at("c"));
}

QueryResult FavoriteDAO::page_items(long long user_id, int limit, int offset) {
    return DbSession::current()->query(
        "SELECT f.id AS favorite_id, f.created_at AS favorited_at, "
        "t.id, t.title, t.category, t.price, t.condition_level, t.images_json, "
        "t.status, t.audit_status, t.trust_score, t.created_at "
        "FROM favorite f JOIN secondhand_item t ON t.id = f.target_id "
        "WHERE f.user_id = ? AND f.target_type = 'item' AND t.is_deleted = 0 "
        "ORDER BY f.id DESC LIMIT ? OFFSET ?",
        {user_id, static_cast<long long>(limit), static_cast<long long>(offset)});
}

long long FavoriteDAO::count_items(long long user_id) {
    auto rows = DbSession::current()->query(
        "SELECT COUNT(*) AS c FROM favorite f "
        "JOIN secondhand_item t ON t.id = f.target_id "
        "WHERE f.user_id = ? AND f.target_type = 'item' AND t.is_deleted = 0",
        {user_id});
    if (rows.empty()) return 0;
    return std::stoll(rows.front().at("c"));
}

}  // namespace jt_db
