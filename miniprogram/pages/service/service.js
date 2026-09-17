// 服务页（Tab）—— 需求 7 / F15：顶部「路线维保中心」吸顶卡 + 8 功能 2×4 玻璃宫格
// 依据：docs/二阶段整改方案-前端UI重构与后端支撑.md §2.3（视觉规范 §1.1~§1.3）
// 说明：gridItems 的条目名 / 跳转 url / tab 标志是业务契约，由
//      tools/verify_f11_icon_set.js D 段逐条锁定 —— 本次只改视觉，不动业务。
Page({
  data: {
    // F10 运行时降级：能力探测判定不支持毛玻璃时置 true → 根节点挂 .is-glass-fallback
    glassFallback: false,

    gridItems: [
      { id: 1, name: '图书馆预约', icon: '/static/icons/book-open.svg', url: '/pages/library/index', tab: false },
      { id: 2, name: '查空教室', icon: '/static/icons/building-library.svg', url: '/pages/library/freeRoom', tab: false },
      { id: 3, name: '二手集市', icon: '/static/icons/shopping-bag.svg', url: '/pages/secondhand/index', tab: false },
      { id: 4, name: '兼职实习', icon: '/static/icons/briefcase.svg', url: '/pages/job/index', tab: false },
      { id: 5, name: '校园地图', icon: '/static/icons/map.svg', url: '/pages/map/index', tab: false },
      { id: 6, name: '外卖点餐', icon: '/static/icons/shopping-cart.svg', url: '/pages/life/index', tab: false },
      { id: 7, name: '通知公告', icon: '/static/icons/bell.svg', url: '/pages/life/notices', tab: false },
      { id: 8, name: '任务中心', icon: '/static/icons/clipboard-document-list.svg', url: '/pages/agent/index', tab: false },
    ],
  },

  onLoad() {
    // F10：能力探测只在 app.js onLaunch 做一次，这里只读结果、不重复探测
    const app = getApp()
    this.setData({
      glassFallback: !(app && app.globalData && app.globalData.glassSupported),
    })
  },

  // 路线维保中心：暂无后端接口与目标页，给出明确反馈而不是静默无响应
  onMaintTap() {
    wx.showToast({ title: '路线维保中心建设中', icon: 'none' })
  },

  onGridTap(e) {
    const { url, tab } = e.currentTarget.dataset
    if (tab) {
      wx.switchTab({ url })
    } else {
      wx.navigateTo({ url })
    }
  },
})
