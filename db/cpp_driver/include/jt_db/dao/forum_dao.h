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

    // C26 关键词搜索（标题 + 正文，FULLTEXT + ngram parser，见 db/sql/19_topic_fulltext.sql）
    //
    // keyword：**用户原样输入**（不需要调用方做任何转义）。内部会净化成 boolean 检索式：
    //          去掉 boolean 运算符、按空白拆词、每个词前缀 `+`（= 这些字都要出现）。
    //          ⚠️ 中文由**服务器**按 `ngram_token_size` 切成 n-gram（本机 = 2 字）——
    //          这与 services/zh_tokenizer.py 的 2-gram 口径一致。
    // 返回列：在 page_topics 的列基础上**追加 `relevance`**（相似度，DESC 排序）。
    // 退化：keyword 为空或净化后为空 → **直接转 page_topics**（不返回空表）。
    QueryResult search_topics(int page, int size, const std::string& keyword,
                              const std::string& category = "",
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
