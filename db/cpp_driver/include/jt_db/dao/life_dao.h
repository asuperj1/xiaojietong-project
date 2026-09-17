#pragma once
// 校捷通 C++ 数据访问层 · LifeDAO（校园生活服务 / 通知 / 外卖）
//
// 【骨架】对应表（见 docs/architecture.md §6.2）：
//   merchant / menu_item / takeaway_order / campus_notice / notice_read
// 表结构由成员4 设计；确定后在 src/dao/life_dao.cpp 补实现（参照 user_dao.cpp 范式）。

#include <string>

#include "jt_db/types.h"

namespace jt_db {

class LifeDAO {
public:
    // 通知列表（按分类 / 目标年级过滤；AI 精准推送）
    //
    // include_extended（B20）：是否在 SELECT 里带出 B19 的
    // deadline / materials / importance 三列。**默认 false** —— 它们由
    // db/sql/14_notice_extend.sql 提供，未导入该脚本的库上硬 SELECT 会 ERROR 1054；
    // 调用方（app/services/notice.py::page_notices_rows）按真实表结构探测后传参，
    // 因此旧库仍按旧列返回，行为向后兼容。
    QueryResult page_notices(int page, int size, const std::string& category = "",
                             const std::string& target_grade = "",
                             bool include_extended = false);

    // 记录通知已读（精准推送回执）
    bool mark_notice_read(long long user_id, long long notice_id);

    // 商家分页
    QueryResult page_merchants(int page, int size, const std::string& category = "");

    // 商家菜单
    QueryResult menu_items(long long merchant_id);

    // 下单（返回订单 id，失败 -1；建议在事务中校验）
    long long create_order(long long user_id, long long merchant_id,
                           const std::string& items_json, double amount);
};

}  // namespace jt_db
