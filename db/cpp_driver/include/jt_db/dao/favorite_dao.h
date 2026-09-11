#pragma once
// 校捷通 C++ 数据访问层 · FavoriteDAO（C10 收敛）
//
// 表结构：favorite(id, user_id, target_type, target_id, created_at)
//   · 唯一键 uk_user_target(user_id, target_type, target_id) —— 天然防重复收藏
//   · target_type 约定：topic（帖子）/ item（二手物品）
//
// 背景：收藏功能此前完全由后端 `backend/app/routers/favorite.py` 用原生 SQL 拼接，
//       本 DAO 把它收敛到 C++ 数据层（"所有 DB 访问走 C++ 层" → 同时走 DAO 抽象）。

#include <string>

#include "jt_db/types.h"

namespace jt_db {

class FavoriteDAO {
public:
    // 是否已收藏
    bool is_favorited(long long user_id, const std::string& target_type,
                      long long target_id);

    // 幂等切换：已收藏 → 取消并返回 false；未收藏 → 新增并返回 true
    // target_type 非 topic/item 时直接返回 false（白名单校验）
    bool toggle(long long user_id, const std::string& target_type, long long target_id);

    // 校验收藏对象是否存在且可见
    //   topic：未删除且未锁定（is_deleted = 0 AND status != 1）
    //   item ：未删除（is_deleted = 0）
    bool target_exists(const std::string& target_type, long long target_id);

    // 我的收藏 · 帖子（分页；自动剔除已删除/锁定的帖子）
    QueryResult page_topics(long long user_id, int limit, int offset);
    long long count_topics(long long user_id);

    // 我的收藏 · 二手物品（分页；自动剔除已删除的物品）
    QueryResult page_items(long long user_id, int limit, int offset);
    long long count_items(long long user_id);
};

}  // namespace jt_db
