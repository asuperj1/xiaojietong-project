// 通用格式化工具（F5-F9 共用）
// 不做过度封装：仅提供金额/时间的安全兜底格式化。

// 安全格式化金额：非法值兜底为 '0'
function money(v) {
  const n = Number(v)
  if (isNaN(n)) return '0'
  return n % 1 === 0 ? String(n) : n.toFixed(2)
}

// 安全格式化时间：兼容 'YYYY-MM-DD HH:MM:SS' / ISO 等，截取到分钟
function formatTime(v) {
  if (!v) return ''
  const s = String(v).replace('T', ' ')
  return s.length >= 16 ? s.slice(0, 16) : s
}

// 安全格式化日期：仅取 YYYY-MM-DD
function formatDate(v) {
  const t = formatTime(v)
  return t.length >= 10 ? t.slice(0, 10) : t
}

module.exports = { money, formatTime, formatDate }
