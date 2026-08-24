#pragma once
// 校捷通 C++ 数据访问层 · 公共类型定义

#include <map>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <variant>
#include <vector>

namespace jt_db {

// 预处理语句参数值：支持 整数 / 浮点 / 字符串 / NULL
using ParamValue = std::variant<long long, double, std::string, std::nullptr_t>;
using Params = std::vector<ParamValue>;

// 查询结果：每行 {列名: 值}，值统一转字符串（原型阶段；后续可类型化）
using Row = std::map<std::string, std::string>;
using QueryResult = std::vector<Row>;

// 写操作结果：{受影响行数, 自增ID}
using ExecResult = std::pair<long long, long long>;

// 统一异常
class DbException : public std::runtime_error {
public:
    explicit DbException(const std::string& msg) : std::runtime_error(msg) {}
};

}  // namespace jt_db
