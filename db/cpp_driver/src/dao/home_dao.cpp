// 校捷通 C++ 数据访问层 · HomeDAO 实现（首页运营：轮播位）

#include "jt_db/dao/home_dao.h"

#include <vector>

#include "jt_db/db_session.h"

namespace jt_db {

namespace {

// 列表/详情统一取这些列（避免 SELECT * 把 updated_at 之外的东西也带出去）
constexpr const char* kBannerColumns =
    "id, title, image, link_type, link_target, sort, start_at, end_at, enabled, created_at";

// 空串 → SQL NULL。写入用：start_at/end_at 为 NULL 表示"不限制"（立即生效 / 永不过期）。
// jt_db 的参数是 variant<long long,double,string,nullptr_t>，nullptr 即 NULL。
ParamValue nullable_dt(const std::string& value) {
    if (value.empty()) return ParamValue{nullptr};
    return ParamValue{value};
}

// create / update 的参数字典序（八个字段一致），显式 push_back 以免 {} 初始化歧义
std::vector<ParamValue> banner_params(const std::string& title, const std::string& image,
                                      const std::string& link_type,
                                      const std::string& link_target, long long sort,
                                      const std::string& start_at,
                                      const std::string& end_at, long long enabled) {
    std::vector<ParamValue> params;
    params.push_back(title);
    params.push_back(image);
    params.push_back(link_type);
    params.push_back(link_target);
    params.push_back(sort);
    params.push_back(nullable_dt(start_at));
    params.push_back(nullable_dt(end_at));
    params.push_back(enabled);
    return params;
}

}  // namespace

QueryResult HomeDAO::list_banners(int limit) {
    if (limit < 1) limit = 20;
    if (limit > 200) limit = 200;  // 夹紧上限，**不**做"超限就重置为 20"那种静默缩水
    // 有效期口径（与 db/sql/15_home_banner.sql 顶部注释一致）：
    //   start_at IS NULL = 立即生效；end_at IS NULL = 永不过期
    // 注意：不能写成 `(? = '' OR col = ?)` 那种"空参不过滤"的写法 ——
    // 那种写法会让索引失效（审计 C33 已记录）。
    return DbSession::current()->query(
        std::string("SELECT ") + kBannerColumns +
        " FROM home_banner "
        "WHERE enabled = 1 "
        "  AND (start_at IS NULL OR start_at <= NOW()) "
        "  AND (end_at   IS NULL OR end_at   >= NOW()) "
        "ORDER BY sort DESC, id DESC LIMIT ?",
        {static_cast<long long>(limit)});
}

QueryResult HomeDAO::list_all_banners(int limit) {
    if (limit < 1) limit = 100;
    if (limit > 200) limit = 200;
    // 管理端视角：**不做任何业务过滤**。若这里仍过滤有效期，
    // 已过期 / 未生效的轮播在后台就永远看不到，也就无法编辑或重新启用。
    return DbSession::current()->query(
        std::string("SELECT ") + kBannerColumns +
        " FROM home_banner ORDER BY sort DESC, id DESC LIMIT ?",
        {static_cast<long long>(limit)});
}

std::optional<Row> HomeDAO::find_banner(long long id) {
    auto rows = DbSession::current()->query(
        std::string("SELECT ") + kBannerColumns + " FROM home_banner WHERE id = ?", {id});
    if (rows.empty()) return std::nullopt;
    return rows.front();
}

long long HomeDAO::create_banner(const std::string& title, const std::string& image,
                                 const std::string& link_type,
                                 const std::string& link_target, long long sort,
                                 const std::string& start_at, const std::string& end_at,
                                 long long enabled) {
    auto [affected, id] = DbSession::current()->execute(
        "INSERT INTO home_banner "
        "(title, image, link_type, link_target, sort, start_at, end_at, enabled) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        banner_params(title, image, link_type, link_target, sort, start_at, end_at, enabled));
    return affected > 0 ? id : -1;
}

bool HomeDAO::update_banner(long long id, const std::string& title, const std::string& image,
                            const std::string& link_type, const std::string& link_target,
                            long long sort, const std::string& start_at,
                            const std::string& end_at, long long enabled) {
    auto params = banner_params(title, image, link_type, link_target, sort, start_at, end_at,
                                enabled);
    params.push_back(id);
    auto [affected, _] = DbSession::current()->execute(
        "UPDATE home_banner SET title = ?, image = ?, link_type = ?, link_target = ?, "
        "       sort = ?, start_at = ?, end_at = ?, enabled = ? WHERE id = ?",
        params);
    return affected > 0 || banner_exists(id);
}

bool HomeDAO::set_banner_enabled(long long id, long long enabled) {
    auto [affected, _] = DbSession::current()->execute(
        "UPDATE home_banner SET enabled = ? WHERE id = ?", {enabled, id});
    return affected > 0 || banner_exists(id);
}

bool HomeDAO::banner_exists(long long id) {
    // 只在 affected == 0 时被调用（见上面的 ||），用于区分两种"0 行"：
    //   · 行不存在                → 真的失败
    //   · 新值与库内**完全相同**  → MySQL 报 0 行，但这是一次成功的幂等写入
    // 不能只用 affected 判成败，否则"把一个已经上架的轮播再上架一次"会返回失败。
    // 同一取舍见 `life_dao.cpp` 的 `mark_order_arrived`（那里是先匹配查询再写）。
    auto rows = DbSession::current()->query("SELECT id FROM home_banner WHERE id = ?", {id});
    return !rows.empty();
}

bool HomeDAO::remove_banner(long long id) {
    auto [affected, _] = DbSession::current()->execute(
        "DELETE FROM home_banner WHERE id = ?", {id});
    return affected > 0;
}

}  // namespace jt_db
