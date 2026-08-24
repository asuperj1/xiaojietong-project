#pragma once
// 校捷通 C++ 数据访问层 · ForumDAO（校园论坛）
//
// 【骨架】对应表（见 docs/architecture.md §6.2）：
//   topic / comment / like_record / favorite
// 表结构由成员4 设计；确定后在 src/dao/forum_dao.cpp 补实现（参照 user_dao.cpp 范式）。

#include <string>

#include "jt_db/types.h"

namespace jt_db {

class ForumDAO {
public:
    // 帖子分页（category 空串为全部；is_audited 过滤已审核）
    QueryResult page_topics(int page, int size, const std::string& category = "",
                            bool audited_only = true);

    // 发帖（返回帖子 id，失败 -1）
    long long create_topic(long long author_id, const std::string& title,
                           const std::string& content, const std::string& category);

    // 评论（返回评论 id，失败 -1）
    long long add_comment(long long topic_id, long long author_id,
                          const std::string& content);

    // 点赞/取消点赞（返回当前是否已赞）
    bool toggle_like(long long user_id, const std::string& target_type,
                     long long target_id);

    // 待审核帖子（AI 审核消费）
    QueryResult pending_audit(int limit = 50);

    // 帖子热度排序（热点识别）
    QueryResult hot_topics(int limit = 20);
};

}  // namespace jt_db
