#pragma once
// 校捷通 C++ 数据访问层 · SecondhandDAO（二手循环经济）
//
// 【骨架】对应表（见 docs/architecture.md §6.2）：
//   secondhand_item / secondhand_wish / secondhand_order / item_message
// 表结构由成员4 设计；确定后在 src/dao/secondhand_dao.cpp 补实现（参照 user_dao.cpp 范式）。

#include <string>

#include "jt_db/types.h"

namespace jt_db {

class SecondhandDAO {
public:
    // 闲置物品分页/搜索（category / keyword）
    QueryResult page_items(int page, int size, const std::string& category = "",
                           const std::string& keyword = "", bool on_sale_only = true);

    // 发布闲置（返回物品 id，失败 -1）
    long long publish(long long user_id, const std::string& title,
                      const std::string& description, const std::string& category,
                      double price);

    // 发布求购（返回求购 id，失败 -1）
    long long create_wish(long long user_id, const std::string& content,
                          const std::string& category, double budget);

    // AI 供需匹配：为用户求购匹配合适的在售物品
    QueryResult match_items_for_wish(long long wish_id, int limit = 10);

    // 标记已售 / 下架
    bool update_status(long long item_id, long long user_id, const std::string& status);

    // 创建交易订单（返回订单 id，失败 -1；建议在事务中校验）
    long long create_order(long long item_id, long long buyer_id, long long seller_id,
                           double amount);
};

}  // namespace jt_db
