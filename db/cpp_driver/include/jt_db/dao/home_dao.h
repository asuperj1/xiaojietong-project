#pragma once
// 校捷通 C++ 数据访问层 · HomeDAO（首页运营：轮播位）
//
// 表：home_banner(id, title, image, link_type, link_target, sort,
//                 start_at, end_at, enabled, created_at, updated_at)
//   索引：idx_enabled_sort(enabled, sort)
//   由 db/sql/15_home_banner.sql 创建（B19 交付，**已在库**：11 列 + 3 行种子）
//
// 字段契约（权威出处：docs/二阶段整改方案-前端UI重构与后端支撑.md L324）：
//   title / image / link_type / link_target / sort / start_at / end_at / enabled
//   · `sort` 语义：**越大越靠前**（ORDER BY sort DESC）
//   · `link_type` ∈ none / page / notice / url
//   · `start_at` / `end_at` 为 NULL 表示"不限制"（立即生效 / 永不过期）
//
// ⚠️ 给 B21（成员2 · `GET /home/banners`）的调用契约，避免两边重复实现：
//   · B21 只需要调 `list_banners()`（默认已过滤 enabled=1 且在有效期内），
//     **不要自己在 Python 里拼 SQL**，否则排序/有效期口径会走样；
//   · 传时间字段时用空串 "" 表示 NULL（不限制），格式 "YYYY-MM-DD HH:MM:SS"；
//     非空但格式非法时 MySQL 会直接报错，**格式校验由调用方负责**；
//   · create 返回新行 id（失败 -1）；update / set_enabled / remove 返回是否命中一行。

#include <optional>
#include <string>

#include "jt_db/types.h"

namespace jt_db {

class HomeDAO {
public:
    // 前台列表（给 B21）：只返回「启用 + 在有效期内」的轮播，按 `sort DESC, id DESC`
    QueryResult list_banners(int limit = 20);

    // 管理端列表：**不做任何业务过滤**（含停用 / 未生效 / 已过期），按 `sort DESC, id DESC`
    //
    // ⚠️ 为什么不把这两个合一用 ``include_disabled`` 布尔开关：
    //   一开始确实是那样的，但测试当场打回 —— 布尔开关只关掉了 ``enabled`` 过滤，
    //   有效期过滤仍在生效 ⇒ 管理端**看不到已过期的轮播**，无法编辑或重新启用。
    //   一个叫 ``include_disabled`` 的参数却偷偷管着有效期，名字本身就在误导。
    //   拆成两个方法后，语义各自单一、调用方不会选错。
    QueryResult list_all_banners(int limit = 100);

    // 取单条（不存在返回 nullopt）
    std::optional<Row> find_banner(long long id);

    // 增：返回新行 id（失败 -1）。start_at / end_at 传 "" 表示 NULL
    long long create_banner(const std::string& title, const std::string& image,
                            const std::string& link_type, const std::string& link_target,
                            long long sort = 0, const std::string& start_at = "",
                            const std::string& end_at = "", long long enabled = 1);

    // 改：**整行更新**（调用方先 find_banner 拿到现值再改，避免把未传字段清空）
    bool update_banner(long long id, const std::string& title, const std::string& image,
                       const std::string& link_type, const std::string& link_target,
                       long long sort, const std::string& start_at,
                       const std::string& end_at, long long enabled);

    // 上 / 下架（运营最常用；单独提供以免调用方做"读-改-写"引入竞态）
    bool set_banner_enabled(long long id, long long enabled);

    // 删
    bool remove_banner(long long id);

private:
    // 内部：轮播行是否存在（`update_banner` / `set_banner_enabled` 在 affected==0 时用它
    // 区分「行不存在」与「新值和库内一样」—— 后者是成功的幂等写入，不能报失败）
    bool banner_exists(long long id);
};

}  // namespace jt_db
