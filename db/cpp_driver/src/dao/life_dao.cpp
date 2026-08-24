// 校捷通 C++ 数据访问层 · LifeDAO 实现

#include "jt_db/dao/life_dao.h"

#include "jt_db/db_session.h"

namespace jt_db {

QueryResult LifeDAO::page_notices(int page, int size, const std::string& category,
                                  const std::string& target_grade) {
    if (page < 1) page = 1;
    if (size < 1 || size > 100) size = 20;
    const long long limit = static_cast<long long>(size);
    const long long offset = static_cast<long long>((page - 1) * size);

    return DbSession::current()->query(
        "SELECT id, title, content, source, category, publish_time "
        "FROM campus_notice "
        "WHERE (? = '' OR category = ?) "
        "  AND (? = '' OR target_grade = ? OR target_grade = '') "
        "ORDER BY publish_time DESC LIMIT ? OFFSET ?",
        {std::string(category), std::string(category), std::string(target_grade),
         std::string(target_grade), limit, offset});
}

bool LifeDAO::mark_notice_read(long long user_id, long long notice_id) {
    auto [affected, _] = DbSession::current()->execute(
        "INSERT IGNORE INTO notice_read (notice_id, user_id) VALUES (?, ?)",
        {notice_id, user_id});
    return affected > 0;
}

QueryResult LifeDAO::page_merchants(int page, int size, const std::string& category) {
    if (page < 1) page = 1;
    if (size < 1 || size > 100) size = 20;
    const long long limit = static_cast<long long>(size);
    const long long offset = static_cast<long long>((page - 1) * size);

    return DbSession::current()->query(
        "SELECT id, name, category, address, delivery_fee, min_order, avg_score, "
        "       business_hours, logo, is_campus "
        "FROM merchant WHERE status = 1 AND (? = '' OR category = ?) "
        "ORDER BY avg_score DESC LIMIT ? OFFSET ?",
        {std::string(category), std::string(category), limit, offset});
}

QueryResult LifeDAO::menu_items(long long merchant_id) {
    return DbSession::current()->query(
        "SELECT id, name, description, price, image, sales_count "
        "FROM menu_item WHERE merchant_id = ? AND is_on_sale = 1 "
        "ORDER BY sales_count DESC",
        {merchant_id});
}

long long LifeDAO::create_order(long long user_id, long long merchant_id,
                                const std::string& items_json, double amount) {
    auto [affected, id] = DbSession::current()->execute(
        "INSERT INTO takeaway_order (user_id, merchant_id, items_json, "
        "total_amount, delivery_fee, pay_amount, status) "
        "VALUES (?, ?, ?, ?, 0, ?, 1)",
        {user_id, merchant_id, std::string(items_json), amount, amount});
    return affected > 0 ? id : -1;
}

}  // namespace jt_db
