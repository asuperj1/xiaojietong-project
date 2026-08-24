// 校捷通 C++ 数据访问层 · SecondhandDAO 实现

#include "jt_db/dao/secondhand_dao.h"

#include "jt_db/db_session.h"

namespace jt_db {

QueryResult SecondhandDAO::page_items(int page, int size, const std::string& category,
                                      const std::string& keyword, bool on_sale_only) {
    if (page < 1) page = 1;
    if (size < 1 || size > 100) size = 20;
    const long long limit = static_cast<long long>(size);
    const long long offset = static_cast<long long>((page - 1) * size);

    std::string sql =
        "SELECT i.id, i.title, i.description, i.category, i.price, "
        "       i.condition_level, i.images_json, i.trust_score, i.view_count, "
        "       i.created_at, u.nickname AS seller_name "
        "FROM secondhand_item i JOIN user u ON i.user_id = u.id "
        "WHERE i.is_deleted = 0 "
        "  AND (? = '' OR i.category = ?) "
        "  AND (? = '' OR i.title LIKE CONCAT('%', ?, '%')) ";
    if (on_sale_only) sql += " AND i.status = 0 ";
    sql += "ORDER BY i.created_at DESC LIMIT ? OFFSET ?";

    return DbSession::current()->query(
        sql, {std::string(category), std::string(category), std::string(keyword),
              std::string(keyword), limit, offset});
}

long long SecondhandDAO::publish(long long user_id, const std::string& title,
                                 const std::string& description,
                                 const std::string& category, double price) {
    auto [affected, id] = DbSession::current()->execute(
        "INSERT INTO secondhand_item (user_id, title, description, category, price) "
        "VALUES (?, ?, ?, ?, ?)",
        {user_id, std::string(title), std::string(description),
         std::string(category), price});
    return affected > 0 ? id : -1;
}

long long SecondhandDAO::create_wish(long long user_id, const std::string& content,
                                     const std::string& category, double budget) {
    auto [affected, id] = DbSession::current()->execute(
        "INSERT INTO secondhand_wish (user_id, content, category, budget) "
        "VALUES (?, ?, ?, ?)",
        {user_id, std::string(content), std::string(category), budget});
    return affected > 0 ? id : -1;
}

QueryResult SecondhandDAO::match_items_for_wish(long long wish_id, int limit) {
    return DbSession::current()->query(
        "SELECT i.id, i.title, i.price, i.condition_level, i.trust_score, "
        "       u.nickname AS seller_name "
        "FROM secondhand_item i "
        "JOIN user u ON i.user_id = u.id "
        "JOIN secondhand_wish w ON w.id = ? "
        "WHERE i.category = w.category AND i.price <= w.budget "
        "  AND i.status = 0 AND i.is_deleted = 0 "
        "ORDER BY i.price ASC LIMIT ?",
        {wish_id, static_cast<long long>(limit)});
}

bool SecondhandDAO::update_status(long long item_id, long long user_id,
                                  const std::string& status) {
    // 状态：0 在售 / 1 已售 / 2 下架
    auto [affected, _] = DbSession::current()->execute(
        "UPDATE secondhand_item SET status = ? WHERE id = ? AND user_id = ? AND is_deleted = 0",
        {static_cast<long long>(std::stol(status)), item_id, user_id});
    return affected > 0;
}

long long SecondhandDAO::create_order(long long item_id, long long buyer_id,
                                      long long seller_id, double amount) {
    auto [affected, id] = DbSession::current()->execute(
        "INSERT INTO secondhand_order (item_id, buyer_id, seller_id, amount) "
        "VALUES (?, ?, ?, ?)",
        {item_id, buyer_id, seller_id, amount});
    return affected > 0 ? id : -1;
}

}  // namespace jt_db
