// 校捷通 C++ 数据访问层 · ForumDAO 实现

#include "jt_db/dao/forum_dao.h"

#include "jt_db/db_session.h"

namespace jt_db {

QueryResult ForumDAO::page_topics(int page, int size, const std::string& category,
                                  bool audited_only) {
    if (page < 1) page = 1;
    if (size < 1 || size > 100) size = 20;
    const long long limit = static_cast<long long>(size);
    const long long offset = static_cast<long long>((page - 1) * size);

    std::string sql =
        "SELECT t.id, t.title, t.category, t.content, t.like_count, "
        "       t.comment_count, t.view_count, t.ai_summary, t.is_hot, t.created_at, "
        "       u.nickname AS author_name "
        "FROM topic t JOIN user u ON t.author_id = u.id "
        "WHERE t.status = 0 AND t.is_deleted = 0 "
        "  AND (? = '' OR t.category = ?) ";
    if (audited_only) sql += " AND t.audit_status = 1 ";
    sql += "ORDER BY t.updated_at DESC LIMIT ? OFFSET ?";

    return DbSession::current()->query(sql, {std::string(category), std::string(category),
                                             limit, offset});
}

long long ForumDAO::create_topic(long long author_id, const std::string& title,
                                 const std::string& content, const std::string& category) {
    auto [affected, id] = DbSession::current()->execute(
        "INSERT INTO topic (author_id, title, content, category, audit_status) "
        "VALUES (?, ?, ?, ?, 0)",
        {author_id, std::string(title), std::string(content), std::string(category)});
    return affected > 0 ? id : -1;
}

long long ForumDAO::add_comment(long long topic_id, long long author_id,
                                const std::string& content) {
    auto conn = DbSession::current();
    auto [affected, id] = conn->execute(
        "INSERT INTO comment (topic_id, author_id, content) VALUES (?, ?, ?)",
        {topic_id, author_id, std::string(content)});
    if (affected > 0) {
        conn->execute(
            "UPDATE topic SET comment_count = comment_count + 1 WHERE id = ?",
            {topic_id});
    }
    return affected > 0 ? id : -1;
}

bool ForumDAO::toggle_like(long long user_id, const std::string& target_type,
                           long long target_id) {
    auto conn = DbSession::current();
    auto exists = conn->query(
        "SELECT id FROM like_record WHERE user_id = ? AND target_type = ? AND target_id = ?",
        {user_id, std::string(target_type), target_id});
    if (exists.empty()) {
        conn->execute(
            "INSERT INTO like_record (user_id, target_type, target_id) VALUES (?, ?, ?)",
            {user_id, std::string(target_type), target_id});
        return true;  // 点赞成功
    }
    conn->execute(
        "DELETE FROM like_record WHERE user_id = ? AND target_type = ? AND target_id = ?",
        {user_id, std::string(target_type), target_id});
    return false;  // 已取消赞
}

QueryResult ForumDAO::pending_audit(int limit) {
    return DbSession::current()->query(
        "SELECT id, title, content, category, author_id, created_at "
        "FROM topic WHERE audit_status = 0 AND is_deleted = 0 "
        "ORDER BY id ASC LIMIT ?",
        {static_cast<long long>(limit)});
}

QueryResult ForumDAO::hot_topics(int limit) {
    return DbSession::current()->query(
        "SELECT id, title, category, like_count, comment_count, view_count, ai_summary "
        "FROM topic WHERE is_hot = 1 AND status = 0 AND is_deleted = 0 "
        "ORDER BY (like_count + comment_count * 3 + view_count * 0.1) DESC LIMIT ?",
        {static_cast<long long>(limit)});
}

}  // namespace jt_db
