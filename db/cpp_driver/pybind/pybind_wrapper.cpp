// 校捷通 C++ 数据访问层 · pybind11 绑定
//
// 将 C++ 连接池 / 查询 / 事务暴露为 Python 模块 `jt_db`。
// Python 侧调用约定：
//   import jt_db
//   jt_db.init_pool("127.0.0.1", 3306, "root", "pwd", "xiaojietong")
//   rows = jt_db.query("SELECT * FROM user WHERE id = ?", [1])
//   affected, last_id = jt_db.execute("INSERT INTO ... VALUES (?, ?)", ["a", 2])
//   with jt_db.begin() as tx:   # 自动 commit / 异常自动 rollback
//       ...

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <string>

#include "jt_db/connection_pool.h"
#include "jt_db/db_session.h"
#include "jt_db/dao/user_dao.h"
#include "jt_db/dao/library_dao.h"
#include "jt_db/dao/forum_dao.h"
#include "jt_db/dao/secondhand_dao.h"
#include "jt_db/dao/job_dao.h"
#include "jt_db/dao/life_dao.h"
#include "jt_db/transaction.h"

namespace py = pybind11;
using namespace jt_db;

namespace {

// Python list → Params（支持 int / float / str / None）
Params to_params(const py::list& list) {
    Params params;
    params.reserve(list.size());
    for (const auto& item : list) {
        if (item.is_none()) {
            params.emplace_back(nullptr);
        } else if (py::isinstance<py::int_>(item)) {
            params.emplace_back(item.cast<long long>());
        } else if (py::isinstance<py::float_>(item)) {
            params.emplace_back(item.cast<double>());
        } else {
            params.emplace_back(py::str(item).cast<std::string>());
        }
    }
    return params;
}

// DbException → Python RuntimeError（带 C++ 层上下文）
void translate_db_exception(const DbException& e) {
    PyErr_SetString(PyExc_RuntimeError, e.what());
}

}  // namespace

PYBIND11_MODULE(jt_db, m) {
    m.doc() = "校捷通 C++ 高性能数据访问模块（连接池 + 预处理语句 + 事务）";

    py::register_exception_translator([](std::exception_ptr p) {
        try {
            if (p) std::rethrow_exception(p);
        } catch (const DbException& e) {
            translate_db_exception(e);
        }
    });

    // ---- 连接池 ----
    m.def("init_pool",
          [](const std::string& host, int port, const std::string& user,
             const std::string& password, const std::string& dbname,
             int min_conn, int max_conn) {
              DbConfig cfg;
              cfg.host = host;
              cfg.port = port;
              cfg.user = user;
              cfg.password = password;
              cfg.dbname = dbname;
              ConnectionPool::instance().init(
                  cfg, static_cast<size_t>(min_conn), static_cast<size_t>(max_conn));
          },
          py::arg("host"), py::arg("port"), py::arg("user"), py::arg("password"),
          py::arg("dbname"), py::arg("min_conn") = 2, py::arg("max_conn") = 16,
          "初始化连接池（应用启动时调用一次）");

    m.def("pool_stats", []() {
        auto& pool = ConnectionPool::instance();
        py::dict d;
        d["initialized"] = pool.initialized();
        d["idle"] = pool.idle_count();
        d["active"] = pool.active_count();
        return d;
    }, "返回连接池状态 {initialized, idle, active}");

    m.def("ping", []() {
        auto conn = ConnectionPool::instance().get();
        return conn->ping();
    }, "连接池健康检查");

    // ---- 查询 / 写操作 ----
    m.def("query",
          [](const std::string& sql, py::list params) {
              auto conn = DbSession::current();
              QueryResult result = conn->query(sql, to_params(params));
              py::list out;
              for (const auto& row : result) {
                  py::dict d;
                  for (const auto& [k, v] : row) {
                      d[py::str(k)] = py::str(v);
                  }
                  out.append(d);
              }
              return out;
          },
          py::arg("sql"), py::arg("params") = py::list(),
          "执行查询，返回 [{列名: 值}, ...]（值统一为 str）");

    m.def("execute",
          [](const std::string& sql, py::list params) {
              auto conn = DbSession::current();
              auto [affected, last_id] = conn->execute(sql, to_params(params));
              return py::make_tuple(affected, last_id);
          },
          py::arg("sql"), py::arg("params") = py::list(),
          "执行写操作，返回 (受影响行数, 自增ID)");

    // ---- 事务（上下文管理器；块内 SQL 复用同一事务连接）----
    m.def("begin", []() {
        auto tx = ConnectionPool::instance().begin_transaction();
        DbSession::set_txn(tx->connection());  // 绑定事务连接到当前线程
        return tx;
    }, "开启事务，返回 Transaction（支持 with 语句；块内 execute/query 走同一连接）");

    auto release_txn = []() { DbSession::clear_txn(); };

    py::class_<Transaction, std::shared_ptr<Transaction>>(m, "Transaction")
        .def("commit", [release_txn](std::shared_ptr<Transaction>& self) {
            self->commit();
            release_txn();
        }, "提交事务")
        .def("rollback", [release_txn](std::shared_ptr<Transaction>& self) {
            self->rollback();
            release_txn();
        }, "回滚事务")
        .def("__enter__", [](std::shared_ptr<Transaction>& self) { return self; })
        .def("__exit__",
             [release_txn](std::shared_ptr<Transaction>& self, py::object exc_type,
                py::object, py::object) {
                 if (self->finished()) return false;
                 if (exc_type.is_none()) {
                     self->commit();  // 正常退出 → 提交
                 } else {
                     self->rollback();  // 异常退出 → 回滚
                 }
                 release_txn();
                 return false;  // 不抑制异常
             },
             py::arg("exc_type"), py::arg("exc_value"), py::arg("traceback"))
        .def("__del__", [release_txn](std::shared_ptr<Transaction>& self) {
            // 兜底：对象销毁时解除线程绑定（连接归还连接池）
            release_txn();
        }, "兜底释放（GC 时）");

    // ---- DAO（按业务域聚合查询；其余 DAO 待表结构确定后补充绑定）----
    py::class_<UserDAO>(m, "UserDAO")
        .def(py::init<>())
        .def("find_by_openid", &UserDAO::find_by_openid, py::arg("openid"),
             "按 openid 查询用户（不存在返回 None）")
        .def("find_by_id", &UserDAO::find_by_id, py::arg("id"),
             "按 id 查询用户（不存在返回 None）")
        .def("page", &UserDAO::page, py::arg("page"), py::arg("size"),
             py::arg("role") = "", "分页查询用户（role 空串不过滤）")
        .def("create", &UserDAO::create, py::arg("openid"), py::arg("nickname") = "",
             py::arg("avatar") = "", py::arg("phone") = "", py::arg("role") = 0,
             "创建用户，返回自增 id（失败 -1）")
        .def("update_profile", &UserDAO::update_profile, py::arg("id"),
             py::arg("nickname"), py::arg("avatar"), "更新昵称/头像")
        .def("update_role", &UserDAO::update_role, py::arg("id"), py::arg("role"),
             "更新角色")
        .def("remove", &UserDAO::remove, py::arg("id"), "删除用户")
        .def("count", &UserDAO::count, "用户总数");

    // ---- 图书馆 ----
    py::class_<LibraryDAO>(m, "LibraryDAO")
        .def(py::init<>())
        .def("find_free_rooms", &LibraryDAO::find_free_rooms, py::arg("campus") = "",
             py::arg("floor") = "", py::arg("period") = "", "空教室查询（period 缺省=当前节次）")
        .def("find_seats", &LibraryDAO::find_seats, py::arg("room_id"), py::arg("date"),
             "房间座位列表（含当日预约状态）")
        .def("reserve", &LibraryDAO::reserve, py::arg("seat_id"), py::arg("user_id"),
             py::arg("date"), py::arg("begin_time"), py::arg("end_time"),
             "预约座位，返回预约 id（失败 -1）")
        .def("cancel_reservation", &LibraryDAO::cancel_reservation,
             py::arg("reservation_id"), py::arg("user_id"), "取消预约（仅本人）")
        .def("my_reservations", &LibraryDAO::my_reservations, py::arg("user_id"),
             "我的预约列表")
        .def("occupancy_history", &LibraryDAO::occupancy_history, py::arg("room_id"),
             py::arg("days") = 30, "历史拥挤度")
        .def("predict_occupancy", &LibraryDAO::predict_occupancy, py::arg("room_id"),
             py::arg("datetime") = "", "AI 拥挤度预测（当前为均值占位）");

    // ---- 论坛 ----
    py::class_<ForumDAO>(m, "ForumDAO")
        .def(py::init<>())
        .def("page_topics", &ForumDAO::page_topics, py::arg("page"), py::arg("size"),
             py::arg("category") = "", py::arg("audited_only") = true, "帖子分页")
        .def("create_topic", &ForumDAO::create_topic, py::arg("author_id"),
             py::arg("title"), py::arg("content"), py::arg("category"),
             "发帖，返回帖子 id（失败 -1）")
        .def("add_comment", &ForumDAO::add_comment, py::arg("topic_id"),
             py::arg("author_id"), py::arg("content"), "评论，返回评论 id（失败 -1）")
        .def("toggle_like", &ForumDAO::toggle_like, py::arg("user_id"),
             py::arg("target_type"), py::arg("target_id"), "点赞/取消，返回当前是否已赞")
        .def("pending_audit", &ForumDAO::pending_audit, py::arg("limit") = 50,
             "待 AI 审核帖子")
        .def("hot_topics", &ForumDAO::hot_topics, py::arg("limit") = 20, "热点帖子");

    // ---- 二手 ----
    py::class_<SecondhandDAO>(m, "SecondhandDAO")
        .def(py::init<>())
        .def("page_items", &SecondhandDAO::page_items, py::arg("page"), py::arg("size"),
             py::arg("category") = "", py::arg("keyword") = "",
             py::arg("on_sale_only") = true, "闲置物品分页/搜索")
        .def("publish", &SecondhandDAO::publish, py::arg("user_id"), py::arg("title"),
             py::arg("description"), py::arg("category"), py::arg("price"),
             "发布闲置，返回物品 id（失败 -1）")
        .def("create_wish", &SecondhandDAO::create_wish, py::arg("user_id"),
             py::arg("content"), py::arg("category"), py::arg("budget"),
             "发布求购，返回求购 id（失败 -1）")
        .def("match_items_for_wish", &SecondhandDAO::match_items_for_wish,
             py::arg("wish_id"), py::arg("limit") = 10, "AI 供需匹配")
        .def("update_status", &SecondhandDAO::update_status, py::arg("item_id"),
             py::arg("user_id"), py::arg("status"), "更新物品状态（0在售 1已售 2下架）")
        .def("create_order", &SecondhandDAO::create_order, py::arg("item_id"),
             py::arg("buyer_id"), py::arg("seller_id"), py::arg("amount"),
             "创建交易订单，返回订单 id（失败 -1）");

    // ---- 兼职 ----
    py::class_<JobDAO>(m, "JobDAO")
        .def(py::init<>())
        .def("page_jobs", &JobDAO::page_jobs, py::arg("page"), py::arg("size"),
             py::arg("type") = "", py::arg("keyword") = "", py::arg("active_only") = true,
             "岗位分页/搜索")
        .def("get_trust_score", &JobDAO::get_trust_score, py::arg("job_id"),
             "岗位可信度评分")
        .def("apply", &JobDAO::apply, py::arg("user_id"), py::arg("job_id"),
             py::arg("resume"), "投递，返回申请 id（失败 -1）")
        .def("my_applications", &JobDAO::my_applications, py::arg("user_id"),
             "我的投递记录")
        .def("add_blacklist", &JobDAO::add_blacklist, py::arg("user_id"),
             py::arg("company_id"), py::arg("reason"), "加入诚信黑名单");

    // ---- 生活服务 ----
    py::class_<LifeDAO>(m, "LifeDAO")
        .def(py::init<>())
        .def("page_notices", &LifeDAO::page_notices, py::arg("page"), py::arg("size"),
             py::arg("category") = "", py::arg("target_grade") = "", "通知分页/精准推送")
        .def("mark_notice_read", &LifeDAO::mark_notice_read, py::arg("user_id"),
             py::arg("notice_id"), "标记通知已读")
        .def("page_merchants", &LifeDAO::page_merchants, py::arg("page"), py::arg("size"),
             py::arg("category") = "", "商家分页")
        .def("menu_items", &LifeDAO::menu_items, py::arg("merchant_id"), "商家菜单")
        .def("create_order", &LifeDAO::create_order, py::arg("user_id"),
             py::arg("merchant_id"), py::arg("items_json"), py::arg("amount"),
             "下单，返回订单 id（失败 -1）");
}
