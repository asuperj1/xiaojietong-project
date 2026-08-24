// 校捷通 C++ 数据访问层 · JobDAO 实现

#include "jt_db/dao/job_dao.h"

#include "jt_db/db_session.h"

namespace jt_db {

QueryResult JobDAO::page_jobs(int page, int size, const std::string& type,
                              const std::string& keyword, bool active_only) {
    if (page < 1) page = 1;
    if (size < 1 || size > 100) size = 20;
    const long long limit = static_cast<long long>(size);
    const long long offset = static_cast<long long>((page - 1) * size);

    std::string sql =
        "SELECT j.id, j.title, j.job_type, j.description, j.salary, j.pay_unit, "
        "       j.work_time, j.location, j.risk_level, j.trust_score, j.created_at, "
        "       c.name AS company_name, c.credit_score "
        "FROM job_post j JOIN company c ON j.company_id = c.id "
        "WHERE c.is_blacklisted = 0 "
        "  AND (? = '' OR j.job_type = ?) "
        "  AND (? = '' OR j.title LIKE CONCAT('%', ?, '%')) ";
    if (active_only) sql += " AND j.status = 0 ";
    sql += "ORDER BY j.created_at DESC LIMIT ? OFFSET ?";

    return DbSession::current()->query(
        sql, {std::string(type), std::string(type), std::string(keyword),
              std::string(keyword), limit, offset});
}

long long JobDAO::get_trust_score(long long job_id) {
    auto rows = DbSession::current()->query(
        "SELECT trust_score FROM job_post WHERE id = ?", {job_id});
    if (rows.empty()) return -1;
    return static_cast<long long>(std::stoll(rows.front().at("trust_score")));
}

long long JobDAO::apply(long long user_id, long long job_id,
                        const std::string& resume) {
    auto [affected, id] = DbSession::current()->execute(
        "INSERT INTO job_application (job_id, user_id, resume_text) VALUES (?, ?, ?)",
        {job_id, user_id, std::string(resume)});
    return affected > 0 ? id : -1;
}

QueryResult JobDAO::my_applications(long long user_id) {
    return DbSession::current()->query(
        "SELECT a.id, a.status, a.resume_text, a.created_at, "
        "       j.title AS job_title, j.salary, j.pay_unit, "
        "       c.name AS company_name "
        "FROM job_application a "
        "JOIN job_post j ON a.job_id = j.id "
        "JOIN company c ON j.company_id = c.id "
        "WHERE a.user_id = ? ORDER BY a.created_at DESC",
        {user_id});
}

bool JobDAO::add_blacklist(long long user_id, long long company_id,
                           const std::string& reason) {
    auto [affected, _] = DbSession::current()->execute(
        "INSERT IGNORE INTO user_job_blacklist (user_id, company_id, reason) "
        "VALUES (?, ?, ?)",
        {user_id, company_id, std::string(reason)});
    return affected > 0;
}

}  // namespace jt_db
