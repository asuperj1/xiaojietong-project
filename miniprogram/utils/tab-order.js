// F12 POC：TabBar 规范清单 —— **JS 侧唯一事实来源**
//
// 为什么单独成模块：这份清单此前存在于三处（app.json / 组件 data / utils/tabbar.js 的 TAB_ORDER），
// 只靠校验脚本用正则比对，容易漏改。现在 JS 侧只留这一份，组件与助手都从这里取。
//
// ⚠️ `app.json` 的 `tabBar.list` 仍是**必要副本**：它是 JSON，运行时无法 import，
//    且框架本身要求声明。两份的一致性由 tools/verify_f12_tabbar_poc.js 断言（顺序 + 路径）。
//
// 顺序依据 `docs/二阶段整改方案-前端UI重构与后端支撑.md` §1.4「AI 助手置于正中」：
//   5 项的第 3 位（index 2）才是正中，凸起圆形才不会偏左。
const TAB_LIST = [
  { key: 'index', pagePath: '/pages/index/index', text: '首页' },
  { key: 'service', pagePath: '/pages/service/service', text: '服务' },
  { key: 'chat', pagePath: '/pages/chat/chat', text: 'AI助手', raised: true },
  { key: 'forum', pagePath: '/pages/forum/forum', text: '论坛' },
  { key: 'user', pagePath: '/pages/user/user', text: '我的' },
]

const TAB_ORDER = TAB_LIST.map((item) => item.key)

const TAB_INDEX = TAB_ORDER.reduce((acc, key, i) => {
  acc[key] = i
  return acc
}, {})

module.exports = { TAB_LIST, TAB_ORDER, TAB_INDEX }
