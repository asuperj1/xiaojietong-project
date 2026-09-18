// F12 POC：自定义 TabBar 助手（选中态同步 + 全屏态显隐）
//
// 为什么需要它：自定义 TabBar 的组件实例是**每个 Tab 页各一份**，
// 选中项 / 显隐必须由页面在 onShow 里主动同步；且 `this.getTabBar()` 在
// 非 Tab 页、或自定义 TabBar 尚未挂载时会返回 undefined，必须容错。
//
// 归属：F12（二阶段任务单 §3.3）。POC 阶段只做「选中态 + 显隐」两件事。
// Tab 清单与顺序来自 utils/tab-order.js（JS 侧唯一事实来源）。
const { TAB_INDEX } = require('./tab-order')

function tabBarOf(page) {
  if (!page || typeof page.getTabBar !== 'function') return null
  try {
    return page.getTabBar() || null
  } catch (err) {
    console.warn('[tabbar] getTabBar() 不可用：', err)
    return null
  }
}

/**
 * Tab 页 onShow 内调用，同步自定义 TabBar 的选中项。
 * 页面尚未接入自定义 TabBar 时静默返回 false（不阻断页面逻辑）。
 */
function syncTabBar(page, key) {
  const selected = TAB_INDEX[key]
  if (selected === undefined) {
    console.warn('[tabbar] 未知的 Tab key：', key)
    return false
  }
  const bar = tabBarOf(page)
  if (!bar || typeof bar.setSelected !== 'function') return false
  bar.setSelected(selected)
  return true
}

/**
 * 全屏态显隐。POC 由 AI 页顶部按钮触发；
 * F14 起应由「侧边栏展开 / 全屏会话」状态驱动。
 */
function setTabBarHidden(page, hidden) {
  const bar = tabBarOf(page)
  if (!bar || typeof bar.setHidden !== 'function') return false
  bar.setHidden(!!hidden)
  return true
}

module.exports = { syncTabBar, setTabBarHidden }
