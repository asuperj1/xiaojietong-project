#pragma once
// 校捷通 C++ 数据访问层 · LifeDAO（校园生活服务 / 通知 / 外卖）
//
// 【骨架】对应表（见 docs/architecture.md §6.2）：
//   merchant / menu_item / takeaway_order / campus_notice / notice_read
// 表结构由成员4 设计；确定后在 src/dao/life_dao.cpp 补实现（参照 user_dao.cpp 范式）。

#include <optional>
#include <string>

#include "jt_db/types.h"

namespace jt_db {

class LifeDAO {
public:
    // 通知列表（按分类 / 目标年级过滤；AI 精准推送）
    QueryResult page_notices(int page, int size, const std::string& category = "",
                             const std::string& target_grade = "");

    // 记录通知已读（精准推送回执）
    bool mark_notice_read(long long user_id, long long notice_id);

    // 商家分页
    QueryResult page_merchants(int page, int size, const std::string& category = "");

    // 商家菜单
    QueryResult menu_items(long long merchant_id);

    // 下单（返回订单 id，失败 -1；建议在事务中校验）
    long long create_order(long long user_id, long long merchant_id,
                           const std::string& items_json, double amount);

    // ======================================================= C25 · 代收闭环
    // 三要素（出处：docs/二阶段整改方案-前端UI重构与后端支撑.md §3.7.1）：
    //   ① 取件码   `takeaway_order.pickup_code`
    //   ② 驿站     `pickup_point`（固定取件点**单选**）
    //   ③ 到件通知 `takeaway_order.arrived_at` / `notified_at`
    //
    // 口径：
    //   · 「代收**不计费**」⇒ `delivery_fee` 恒为 0；金额字段保留但不引入支付流程；
    //   · 历史「代买」订单（`biz_type = 1`）**不删**，新建代收订单 `biz_type = 2`；
    //   · `biz_type` 显式写入而不依赖表默认值 —— 将来改默认值不会让这里跟着走样。
    //
    // ⚠️ 给「代收接口」调用方的契约：
    //   · `create_pickup_order` **内部生成并写入**取件码（随机 + 唯一性校验），
    //     调用方**不要自己造码**；
    //   · 取件码是**取件凭证**，必须随机 —— 不要用订单 id 派生，否则可被猜到（=可冒领）；
    //   · `mark_order_arrived` 要求 order_id 与 pickup_code **同时匹配**，
    //     防止拿一个码去标别人的订单；重复标记是**幂等**的（只写首次时间）。

    // 驿站列表：默认只返回「启用且未软删」，按 `sort DESC, id ASC`
    QueryResult page_pickup_points(bool include_disabled = false, int limit = 50);

    // 取单条驿站（不存在或已软删返回 nullopt）
    std::optional<Row> find_pickup_point(long long id);

    // 增驿站，返回新行 id（失败 -1）
    long long create_pickup_point(const std::string& name, const std::string& address,
                                  const std::string& business_hours,
                                  const std::string& contact_phone, long long sort = 0,
                                  long long status = 1);

    // **软删**驿站（is_deleted=1）：历史订单的 pickup_point_id 还指向它，不能物理删
    bool remove_pickup_point(long long id);

    // 生成一个未被占用的取件码（默认 6 位，已去掉易看错的 I/O）。
    // 一般由 create_pickup_order 内部调用；撞码重试 8 次仍失败则返回空串。
    std::string generate_pickup_code(int length = 6);

    // 代收下单：biz_type=2、delivery_fee=0、自动生成并写入取件码；返回订单 id（失败 -1）
    long long create_pickup_order(long long user_id, long long merchant_id,
                                  const std::string& items_json, double amount,
                                  long long pickup_point_id);

    // 取件码回查（取件码 → 订单）
    std::optional<Row> find_order_by_pickup_code(const std::string& pickup_code);

    // 驿站到件：要求订单与取件码同时匹配；写 arrived_at（幂等，只写首次）
    bool mark_order_arrived(long long order_id, const std::string& pickup_code);

    // 到件通知已发：写 notified_at（幂等：非空即不再改，避免重复打扰）
    bool mark_order_notified(long long order_id);
};

}  // namespace jt_db
