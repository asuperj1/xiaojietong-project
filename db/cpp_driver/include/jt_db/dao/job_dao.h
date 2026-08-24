#pragma once
// 校捷通 C++ 数据访问层 · JobDAO（兼职实习诚信服务）
//
// 【骨架】对应表（见 docs/architecture.md §6.2）：
//   company / job_post / job_application / user_job_blacklist
// 表结构由成员4 设计；确定后在 src/dao/job_dao.cpp 补实现（参照 user_dao.cpp 范式）。

#include <string>

#include "jt_db/types.h"

namespace jt_db {

class JobDAO {
public:
    // 岗位分页/搜索（type: 校内勤工/实习/兼职）
    QueryResult page_jobs(int page, int size, const std::string& type = "",
                          const std::string& keyword = "", bool active_only = true);

    // 岗位可信度评分（AI 计算后写入 trust_score）
    long long get_trust_score(long long job_id);

    // 投递（返回申请 id，失败 -1）
    long long apply(long long user_id, long long job_id, const std::string& resume);

    // 我的投递记录
    QueryResult my_applications(long long user_id);

    // 拉黑企业/用户（诚信档案）
    bool add_blacklist(long long user_id, long long company_id,
                       const std::string& reason);
};

}  // namespace jt_db
