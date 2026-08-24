// 校捷通 C++ 数据访问层 · LibraryDAO 实现

#include "jt_db/dao/library_dao.h"

#include <ctime>

#include "jt_db/db_session.h"

namespace jt_db {

namespace {
// 当前星期（1=周一 ... 7=周日），用于空教室查询
int weekday_now() {
    std::time_t t = std::time(nullptr);
    std::tm lt{};
#if defined(_WIN32)
    localtime_s(&lt, &t);
#else
    localtime_r(&t, &lt);
#endif
    return lt.tm_wday == 0 ? 7 : lt.tm_wday;  // tm_wday: 0=周日
}
}  // namespace

QueryResult LibraryDAO::find_free_rooms(const std::string& campus,
                                        const std::string& floor,
                                        const std::string& period) {
    const int weekday = weekday_now();
    // period 缺省取当前节次（按小时估算：8点→第1节）
    const int period_no = period.empty() ? ([] {
        std::time_t t = std::time(nullptr);
        std::tm lt{};
#if defined(_WIN32)
        localtime_s(&lt, &t);
#else
        localtime_r(&t, &lt);
#endif
        return lt.tm_hour - 7;  // 第1节≈8点
    })() : std::stoi(period);

    return DbSession::current()->query(
        "SELECT r.id, b.name AS building_name, r.floor, r.name AS room_name, "
        "       r.capacity, r.has_power "
        "FROM room r JOIN building b ON r.building_id = b.id "
        "WHERE r.is_classroom = 1 "
        "  AND (? = '' OR b.campus = ?) "
        "  AND (? = '' OR r.floor = ?) "
        "  AND NOT EXISTS (SELECT 1 FROM classroom_schedule cs "
        "                  WHERE cs.room_id = r.id AND cs.weekday = ? "
        "                    AND cs.period = ? AND cs.is_class = 1) "
        "ORDER BY b.name, r.floor, r.name",
        {std::string(campus), std::string(campus), std::string(floor),
         std::string(floor), weekday, period_no});
}

QueryResult LibraryDAO::find_seats(long long room_id, const std::string& date) {
    return DbSession::current()->query(
        "SELECT s.id, s.seat_no, s.has_power, s.is_window, "
        "       CASE WHEN EXISTS (SELECT 1 FROM seat_reservation sr "
        "                         WHERE sr.seat_id = s.id AND sr.reserve_date = ? "
        "                           AND sr.status IN (0, 1)) "
        "            THEN 1 ELSE 0 END AS reserved "
        "FROM seat s WHERE s.room_id = ? AND s.status = 0 "
        "ORDER BY s.seat_no",
        {std::string(date), room_id});
}

long long LibraryDAO::reserve(long long seat_id, long long user_id,
                              const std::string& date, const std::string& begin_time,
                              const std::string& end_time) {
    auto [affected, id] = DbSession::current()->execute(
        "INSERT INTO seat_reservation "
        "(seat_id, user_id, reserve_date, begin_time, end_time, status) "
        "VALUES (?, ?, ?, ?, ?, 0)",
        {seat_id, user_id, std::string(date), std::string(begin_time),
         std::string(end_time)});
    return affected > 0 ? id : -1;
}

bool LibraryDAO::cancel_reservation(long long reservation_id, long long user_id) {
    auto [affected, _] = DbSession::current()->execute(
        "UPDATE seat_reservation SET status = 2 "
        "WHERE id = ? AND user_id = ? AND status IN (0, 1)",
        {reservation_id, user_id});
    return affected > 0;
}

QueryResult LibraryDAO::my_reservations(long long user_id) {
    return DbSession::current()->query(
        "SELECT sr.id, sr.reserve_date, sr.begin_time, sr.end_time, sr.status, "
        "       s.seat_no, r.name AS room_name, b.name AS building_name "
        "FROM seat_reservation sr "
        "JOIN seat s ON sr.seat_id = s.id "
        "JOIN room r ON s.room_id = r.id "
        "JOIN building b ON r.building_id = b.id "
        "WHERE sr.user_id = ? AND sr.status IN (0, 1) "
        "ORDER BY sr.reserve_date, sr.begin_time",
        {user_id});
}

QueryResult LibraryDAO::occupancy_history(long long room_id, int days) {
    return DbSession::current()->query(
        "SELECT record_date, period, occupancy_rate, user_count "
        "FROM occupancy_record "
        "WHERE room_id = ? AND record_date >= DATE_SUB(CURDATE(), INTERVAL ? DAY) "
        "ORDER BY record_date, period",
        {room_id, static_cast<long long>(days)});
}

double LibraryDAO::predict_occupancy(long long room_id, const std::string& /*datetime*/) {
    // AI 占位：返回最近 7 天平均占用率；后续可替换为模型预测
    auto rows = DbSession::current()->query(
        "SELECT AVG(occupancy_rate) AS a FROM occupancy_record "
        "WHERE room_id = ? AND record_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)",
        {room_id});
    if (rows.empty() || rows.front().at("a").empty()) return 0.0;
    return std::stod(rows.front().at("a"));
}

}  // namespace jt_db
