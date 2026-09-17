// 校捷通 C++ 数据访问层 · ForumDAO 实现

#include "jt_db/dao/forum_dao.h"

#include <cctype>

#include "jt_db/db_session.h"

namespace jt_db {

namespace {

// C26：把用户原样输入净化成 MySQL boolean 模式的检索式。
//
// 只做两件事：
//   ① 去掉 boolean 运算符 —— `+ - > < ( ) ~ * " @` 在 boolean 模式下有语义，
//      原样透传会让用户输入 `-图书馆` 变成"**排除**图书馆"，语义与预期相反；
//   ② 空白拆词，每词前缀 `+` —— 得到 AND 语义（"这些字都要出现"），
//      比默认的 OR 更符合搜索框的直觉；中文由服务器按 ngram 继续切分。
//
// 返回空串 = 净化后没剩下可用词（如 `""` / `"+++"`），调用方应退化为普通分页。
// ⚠️ 长度/词数封顶：boolean 表达式会随词长线性膨胀（ngram 再放大一倍），
//    不封顶等于把请求体当放大器用。
std::string build_boolean_query(const std::string& raw) {
    static const std::string kReserved = "\\+-<>()~*\"@%_";
    const std::size_t kMaxTerms = 8;    // 最多 8 个词
    const std::size_t kMaxTermLen = 64; // 单词语义上限（字节；UTF-8 中文 3 字节/字）

    std::string out;
    std::string term;
    std::size_t terms = 0;

    auto flush = [&]() {
        if (term.empty()) return;
        if (terms >= kMaxTerms) {
            term.clear();
            return;
        }
        if (!out.empty()) out.push_back(' ');
        out.push_back('+');
        out += term;
        ++terms;
        term.clear();
    };

    for (char ch : raw) {
        const unsigned char u = static_cast<unsigned char>(ch);
        if (u >= 0x80) {  // UTF-8 多字节序列（中文/emoji）：一律当普通字符
            if (term.size() < kMaxTermLen) term.push_back(ch);
            continue;
        }
        if (std::isspace(u)) {
            flush();
            continue;
        }
        if (kReserved.find(ch) != std::string::npos) {
            flush();
            continue;
        }
        if (term.size() < kMaxTermLen) term.push_back(ch);
    }
    flush();
    return out;
}

}  // namespace

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
    // 排序必须带唯一 tie-break：
    // updated_at 相同的行顺序未定义，LIMIT/OFFSET 分页会重复或漏行。
    sql += "ORDER BY t.updated_at DESC, t.id DESC LIMIT ? OFFSET ?";

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

QueryResult ForumDAO::search_topics(int page, int size, const std::string& keyword,
                                    const std::string& category, bool audited_only) {
    if (page < 1) page = 1;
    if (size < 1 || size > 100) size = 20;

    const std::string boolean_query = build_boolean_query(keyword);
    // 空关键词（或纯符号）→ 退化为普通分页。**不要**用 AGAINST('') 查：
    // 空 boolean 表达式恒 0 行，用户会以为"搜索坏了"。
    if (boolean_query.empty()) return page_topics(page, size, category, audited_only);

    const long long limit = static_cast<long long>(size);
    const long long offset = static_cast<long long>((page - 1) * size);

    // ⚠️ 三处契约，改动前先看 db/sql/19_topic_fulltext.sql 的部署注意：
    //   ① 检索列与索引定义**同序**（(title, content)），否则用不到 FULLTEXT 索引；
    //   ② 必须 IN BOOLEAN MODE（natural language 模式对短关键词有停用词/阈值干扰）；
    //   ③ `relevance` 只能在 SELECT 里算、在 ORDER BY 里引用（WHERE 不能引用别名）。
    std::string sql =
        "SELECT t.id, t.title, t.category, t.content, t.like_count, "
        "       t.comment_count, t.view_count, t.ai_summary, t.is_hot, t.created_at, "
        "       u.nickname AS author_name, "
        "       MATCH(t.title, t.content) AGAINST (? IN BOOLEAN MODE) AS relevance "
        "FROM topic t JOIN user u ON t.author_id = u.id "
        "WHERE t.status = 0 AND t.is_deleted = 0 "
        "  AND MATCH(t.title, t.content) AGAINST (? IN BOOLEAN MODE) "
        "  AND (? = '' OR t.category = ?) ";
    if (audited_only) sql += " AND t.audit_status = 1 ";
    // 同 page_topics：末尾补唯一 tie-break，保证分页稳定。
    sql += "ORDER BY relevance DESC, t.updated_at DESC, t.id DESC LIMIT ? OFFSET ?";

    return DbSession::current()->query(
        sql, {boolean_query, boolean_query, std::string(category), std::string(category),
              limit, offset});
}

}  // namespace jt_db
