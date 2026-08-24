#pragma once
// 校捷通 C++ 数据访问层 · LibraryDAO（图书馆智能预约 / 空教室 / 拥挤度）
//
// 【骨架】对应表（见 docs/architecture.md §6.2）：
//   building / room / seat / seat_reservation / occupancy_record / classroom_schedule
// 表结构由成员4 设计；确定后在 src/dao/library_dao.cpp 补实现（参照 user_dao.cpp 范式）。

#include <optional>
#include <string>

#include "jt_db/types.h"

namespace jt_db {

class LibraryDAO {
public:
    // 空教室查询：按校区/楼层/时间段筛选
    QueryResult find_free_rooms(const std::string& campus = "",
                                const std::string& floor = "",
                                const std::string& period = "");

    // 某房间某日的座位列表（含预约状态）
    QueryResult find_seats(long long room_id, const std::string& date);

    // 预约座位（成功返回预约 id，失败返回 -1；需在事务中校验占用）
    long long reserve(long long seat_id, long long user_id, const std::string& date,
                      const std::string& begin_time, const std::string& end_time);

    // 取消预约（仅本人可取消）
    bool cancel_reservation(long long reservation_id, long long user_id);

    // 我的预约列表
    QueryResult my_reservations(long long user_id);

    // 历史拥挤度（供 AI 预测）
    QueryResult occupancy_history(long long room_id, int days = 30);

    // AI 拥挤度预测：返回预测的占用率(0-100)（可先返回最近均值，后续接模型）
    double predict_occupancy(long long room_id, const std::string& datetime);
};

}  // namespace jt_db
