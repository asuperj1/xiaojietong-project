#pragma once
// 校捷通 C++ 数据访问层 · UserDAO（范式示例）
//
// 表结构（`01_user.sql`，共 19 列）：
//   user(id, openid, unionid, phone, password_hash, nickname, avatar, gender,
//        student_no, student_no_updated_at, major, grade, campus,
//        role, status, is_deleted, token_version, last_login_at,
//        created_at, updated_at)
//   ※ 其中 `student_no_updated_at` / `token_version` 由 B19 的
//     `db/sql/17_user_student_no.sql`（C22）提供。
// 说明：本 DAO 为"完整实现"范式，其余 DAO 请参照本文件的模式编写。
// 方法内部通过 DbSession::current() 获取连接（事务内自动复用事务连接）。

#include <optional>
#include <string>

#include "jt_db/types.h"

namespace jt_db {

class UserDAO {
public:
    // 微信登录：按 openid 查询（不存在返回 nullopt）
    std::optional<Row> find_by_openid(const std::string& openid);
    std::optional<Row> find_by_id(long long id);

    // 分页查询；role 传空串表示不过滤
    QueryResult page(int page, int size, const std::string& role = "");

    // 创建用户，返回自增 id（失败返回 -1）
    long long create(const std::string& openid, const std::string& nickname = "",
                     const std::string& avatar = "", const std::string& phone = "",
                     long long role = 0);

    // 更新昵称/头像
    bool update_profile(long long id, const std::string& nickname,
                        const std::string& avatar);
    bool update_role(long long id, long long role);
    bool remove(long long id);

    // ---- C22：学号绑定 / 限频 / token 失效 ----

    // 绑定或修改学号，并把 `student_no_updated_at` 刷成当前时间（限频判定的依据）。
    // 返回 false 表示用户不存在或写入失败。
    // ⚠️ 学号重复会被唯一索引 `uk_student_no` 拦下，底层抛 DbException
    //    （调用方需捕获并转成业务错误，不要让它变成 500）。
    bool update_student_no(long long id, const std::string& student_no);

    // 距离下次可改学号还剩几天：0 = 现在可改；>0 = 还需等这么多天；-1 = 用户不存在。
    // 限频窗口由 `interval_days` 传入（业务约定 7 天），便于配置而不写死。
    // 从未改过（`student_no_updated_at IS NULL`）一律返回 0。
    long long student_no_change_remaining_days(long long id, long long interval_days);

    // token 版本号 +1 —— 登出 / 改密后，该用户所有已签发的 token 立即失效。
    // 返回新版本号；-1 表示用户不存在。
    long long bump_token_version(long long id);

    long long count();
};

}  // namespace jt_db
