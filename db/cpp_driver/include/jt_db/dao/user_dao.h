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

    // ======================================================= C23 · 搜索历史
    // 表：user_search_history(id, user_id, keyword, created_at)
    //   UNIQUE uk_user_keyword(user_id, keyword) —— 同人同词只留一行
    //   INDEX  idx_user_created(user_id, created_at) —— 倒序拉取用
    //
    // ⚠️ 给 B22 接口（成员2）的调用契约，避免重复实现：
    //   · add 已经保证「每人·每词最多 1 行」，重复搜索是**把旧行顶到最新**，
    //     不是插新行 —— 调用方**不要再自己写 INSERT**，否则会撞 1062；
    //   · keyword 会**先 trim、再按 UTF-8 字符截到 128 字符**（表列宽），
    //     trim 后为空则直接拒绝（返回 -1，不落库）；
    //   · delete 的 WHERE 里带 user_id ⇒ **天然防越权**，别人的历史删不掉；
    //     返回 false 既可能是“不存在”也可能是“不是你的”，调用方按“未删除”处理即可。

    // 增：写入并去重（同人同词只留一行，重复搜索把 created_at 顶到最新）；返回行 id，失败 -1
    long long add_search_history(long long user_id, const std::string& keyword);

    // 查：按 (user_id, created_at DESC) 取最近 limit 条（走 idx_user_created）
    QueryResult list_search_history(long long user_id, int limit = 20);

    // 单删：仅能删本人的（user_id 参与 WHERE）；返回是否真的删掉一行
    bool delete_search_history(long long user_id, long long history_id);

    // 清空：删除该用户全部历史，返回删除行数
    long long clear_search_history(long long user_id);

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
