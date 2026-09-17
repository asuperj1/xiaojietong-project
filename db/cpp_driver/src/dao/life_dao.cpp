// 校捷通 C++ 数据访问层 · LifeDAO 实现

#include "jt_db/dao/life_dao.h"

#include <random>
#include <string>

#include "jt_db/db_session.h"

namespace jt_db {

QueryResult LifeDAO::page_notices(int page, int size, const std::string& category,
                                  const std::string& target_grade, bool include_extended) {
    if (page < 1) page = 1;
    if (size < 1 || size > 100) size = 20;
    const long long limit = static_cast<long long>(size);
    const long long offset = static_cast<long long>((page - 1) * size);

    // B20：扩展列由 14_notice_extend.sql 提供，是否带出交给调用方决定
    // （未导入该脚本的库上传 true 会 ERROR 1054，故默认 false）。
    const std::string cols =
        include_extended
            ? "id, title, content, source, category, publish_time, deadline, materials, importance "
            : "id, title, content, source, category, publish_time ";

    return DbSession::current()->query(
        "SELECT " + cols +
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

// ---------------------------------------------------------- C25 · 代收闭环

namespace {

// 取件码字母表：剔掉 I / O —— 取件码要口报/手写，这两个字母容易和 1 / 0 看错。
constexpr const char kCodeAlphabet[] = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ";

constexpr const char* kPickupPointColumns =
    "id, name, address, business_hours, latitude, longitude, contact_phone, sort, status";

constexpr const char* kOrderByCodeColumns =
    "id, user_id, merchant_id, status, biz_type, pickup_code, pickup_point_id, "
    "arrived_at, notified_at, pay_amount, created_at";

}  // namespace

std::string LifeDAO::generate_pickup_code(int length) {
    if (length < 4 || length > 16) length = 6;  // 表列 VARCHAR(16)
    std::mt19937_64 rng(std::random_device{}());
    std::uniform_int_distribution<std::size_t> dist(0, sizeof(kCodeAlphabet) - 2);
    constexpr int kMaxAttempts = 8;
    for (int attempt = 0; attempt < kMaxAttempts; ++attempt) {
        std::string code;
        code.reserve(static_cast<std::size_t>(length));
        for (int i = 0; i < length; ++i) {
            code.push_back(kCodeAlphabet[dist(rng)]);
        }
        // 唯一性：连历史/已完成订单一起查，避免同一码被复用
        auto rows = DbSession::current()->query(
            "SELECT id FROM takeaway_order WHERE pickup_code = ? LIMIT 1",
            {std::string(code)});
        if (rows.empty()) return code;
    }
    return {};  // 6 位 34 进制下连撞 8 次的概率可忽略；返回空串交由调用方处理
}

QueryResult LifeDAO::page_pickup_points(bool include_disabled, int limit) {
    if (limit < 1) limit = 50;
    if (limit > 200) limit = 200;
    std::string sql = std::string("SELECT ") + kPickupPointColumns +
                      " FROM pickup_point WHERE is_deleted = 0 ";
    if (!include_disabled) sql += " AND status = 1 ";
    sql += "ORDER BY sort DESC, id ASC LIMIT ?";
    return DbSession::current()->query(sql, {static_cast<long long>(limit)});
}

std::optional<Row> LifeDAO::find_pickup_point(long long id) {
    auto rows = DbSession::current()->query(
        std::string("SELECT ") + kPickupPointColumns +
            " FROM pickup_point WHERE id = ? AND is_deleted = 0",
        {id});
    if (rows.empty()) return std::nullopt;
    return rows.front();
}

long long LifeDAO::create_pickup_point(const std::string& name, const std::string& address,
                                       const std::string& business_hours,
                                       const std::string& contact_phone, long long sort,
                                       long long status) {
    auto [affected, id] = DbSession::current()->execute(
        "INSERT INTO pickup_point (name, address, business_hours, contact_phone, sort, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        {std::string(name), std::string(address), std::string(business_hours),
         std::string(contact_phone), sort, status});
    return affected > 0 ? id : -1;
}

bool LifeDAO::remove_pickup_point(long long id) {
    // 软删：驿站会被历史订单的 pickup_point_id 引用，物理删会让历史订单查不到驿站
    auto [affected, _] = DbSession::current()->execute(
        "UPDATE pickup_point SET is_deleted = 1 WHERE id = ? AND is_deleted = 0", {id});
    return affected > 0;
}

long long LifeDAO::create_pickup_order(long long user_id, long long merchant_id,
                                       const std::string& items_json, double amount,
                                       long long pickup_point_id) {
    const std::string code = generate_pickup_code();
    if (code.empty()) return -1;  // 取件码生成失败 ⇒ 宁可不建单，也不要建一个没有凭证的单
    auto [affected, id] = DbSession::current()->execute(
        "INSERT INTO takeaway_order (user_id, merchant_id, items_json, total_amount, "
        "       delivery_fee, pay_amount, status, biz_type, pickup_code, pickup_point_id) "
        "VALUES (?, ?, ?, ?, 0, ?, 1, 2, ?, ?)",
        {user_id, merchant_id, std::string(items_json), amount, amount, std::string(code),
         pickup_point_id});
    return affected > 0 ? id : -1;
}

std::optional<Row> LifeDAO::find_order_by_pickup_code(const std::string& pickup_code) {
    auto rows = DbSession::current()->query(
        std::string("SELECT ") + kOrderByCodeColumns +
            " FROM takeaway_order WHERE pickup_code = ? LIMIT 1",
        {std::string(pickup_code)});
    if (rows.empty()) return std::nullopt;
    return rows.front();
}

bool LifeDAO::mark_order_arrived(long long order_id, const std::string& pickup_code) {
    // 先确认「订单 + 取件码」确实匹配：否则拿一个码就能标别人的订单（越权）
    auto rows = DbSession::current()->query(
        "SELECT id FROM takeaway_order WHERE id = ? AND pickup_code = ?",
        {order_id, std::string(pickup_code)});
    if (rows.empty()) return false;
    // 幂等：只写首次到件时间（重复扫码不会把时间往后推）。
    // 注意不能用 UPDATE 的 affected 判成败 —— 值没变时 MySQL 报 0 行，
    // 会被误判为"失败"，所以成败由上面的匹配查询决定。
    DbSession::current()->execute(
        "UPDATE takeaway_order SET arrived_at = IFNULL(arrived_at, NOW()) WHERE id = ?",
        {order_id});
    return true;
}

bool LifeDAO::mark_order_notified(long long order_id) {
    auto rows = DbSession::current()->query(
        "SELECT id FROM takeaway_order WHERE id = ?", {order_id});
    if (rows.empty()) return false;
    // 幂等：notified_at 非空即不再改（重复触发不会重复打扰）
    DbSession::current()->execute(
        "UPDATE takeaway_order SET notified_at = IFNULL(notified_at, NOW()) WHERE id = ?",
        {order_id});
    return true;
}

}  // namespace jt_db
