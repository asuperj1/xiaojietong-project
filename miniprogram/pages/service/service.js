// 服务页（Tab）—— 业务大宫格（与首页宫格一致的常驻入口）
Page({
  data: {
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

  onGridTap(e) {
    const { url, tab } = e.currentTarget.dataset
    if (tab) {
      wx.switchTab({ url })
    } else {
      wx.navigateTo({ url })
    }
  },
})
