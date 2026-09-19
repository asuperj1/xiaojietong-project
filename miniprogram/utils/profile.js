// 用户资料展示辅助（F17：我的主页 + 资料设置页共用）
//
// 为什么单独成模块：「我的」页与「资料设置」页都要做同一件事 —— 把后端 user 视图里的
// **可空字段**转成可安全渲染/可编辑的值。分散在两个页面里写过一版后已出现漂移风险
// （学号 NULL / 空串 / 首尾空白三种形态各写一套兜底），故收敛到一处。
//
// 依据：docs/api.md §2（`GET /user/me` 的 user 结构）、
//      db/sql/17_user_student_no.sql（student_no 可空）+ backend/app/routers/user.py `_view()`。
// 职责边界：只做归一化与兜底，不发请求、不读 globalData、不写 storage。

/**
 * 学号归一化：NULL / undefined / 空白 → ''，其余去首尾空白。
 *
 * ⚠️ 这是 **defensive fallback，不是当前真实后端契约**（独立评审实测更正，2026-09）。
 *
 * 真实链路：MySQL NULL 在 C++ 驱动层就被写成**空串**，不是 None ——
 *   · `db/cpp_driver/src/mysql_connection.cpp` 取列时 `if (is_null[i]) row[col] = "";`
 *   · `db/cpp_driver/pybind/pybind_wrapper.cpp` 再统一 `py::str(v)`，
 * 所以 `_view()` 的 `u.get('student_no', '')` 拿到的**永远是 `''`**（该默认值只在键缺失时生效，
 * 而 `find_by_id` 的 SELECT 明确包含 student_no 列，键不会缺失）。
 * 即：前端不写这层兜底也**不会**渲染出字符串 "null"。
 *
 * 保留它的理由：函数是两页共用 + 直连 mock/其它数据源时的口径统一，
 * 且对 `''` / `'  '` / `undefined` 也一并归一，成本为零。
 */
function studentNoOf(raw) {
  return raw === null || raw === undefined ? '' : String(raw).trim()
}

/** 学号展示文案（含未绑定兜底），供「我的」页直接渲染 */
function studentNoText(raw) {
  const s = studentNoOf(raw)
  return s ? '学号 ' + s : '未绑定学号'
}

/**
 * 头像兜底字符：取昵称首字。
 * 用 `Array.from` 而不是 `[0]`：后者会把 emoji 的代理对截成半个字符（渲染成方块）。
 */
function firstGlyph(nickname, fallback) {
  const chars = Array.from(String(nickname || '').trim())
  return chars.length ? chars[0] : fallback || '校'
}

module.exports = { studentNoOf, studentNoText, firstGlyph }
