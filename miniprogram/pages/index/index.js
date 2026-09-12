// 首页（Tab）—— F3：10 宫格入口 + 快捷指令
const { request } = require('../../services/request')

Page({
  data: {
    keyword: '',        // 搜索框输入
    quickLoading: false, // 快捷指令请求中（防重复点击）
    // 10 个宫格入口：统一 data 数组渲染，避免写十份重复 WXML
    gridItems: [
      { id: 1, name: 'AI 助手', icon: '/static/icons/ai-assistant.png', url: '/pages/chat/chat', tab: true },
      { id: 2, name: '图书馆预约', icon: '/static/icons/library.png', url: '/pages/library/index', tab: false },
      { id: 3, name: '查空教室', icon: '/static/icons/free-room.png', url: '/pages/library/freeRoom', tab: false },
      { id: 4, name: '二手集市', icon: '/static/icons/secondhand.png', url: '/pages/secondhand/index', tab: false },
      { id: 5, name: '兼职实习', icon: '/static/icons/job.png', url: '/pages/job/index', tab: false },
      { id: 6, name: '校园地图', icon: '/static/icons/map.png', url: '/pages/map/index', tab: false },
      { id: 7, name: '外卖点餐', icon: '/static/icons/food.png', url: '/pages/life/index', tab: false },
      { id: 8, name: '通知公告', icon: '/static/icons/notice.png', url: '/pages/life/notices', tab: false },
      { id: 9, name: '校园论坛', icon: '/static/icons/forum.png', url: '/pages/forum/forum', tab: true },
      { id: 10, name: '任务中心', icon: '/static/icons/task.png', url: '/pages/agent/index', tab: false },
    ],
    quickCommands: ['查空教室', '查校历', '预约图书馆'],
    hotTopics: [], // 首页热门帖子（F3 收尾：GET /topics/hot）
  },

  onLoad() {
    // getApp() 需在页面生命周期内调用（模块顶层时 App 可能尚未初始化完成）
    this._app = getApp()
    // 页面初始化完成后检查登录态（保留 F2 登录逻辑，不破坏）
    if (this._app) this._app.checkLogin()
  },

  onShow() {
    // 每次回到首页刷新热门帖子
    this.fetchHot()
  },

  fetchHot() {
    request('/topics/hot', { data: { limit: 5 } })
      .then((res) => {
        const hotTopics = ((res && res.items) || []).map((t) => ({
          id: t.id,
          title: t.title,
          likeCount: t.like_count || 0,
          commentCount: t.comment_count || 0,
        }))
        this.setData({ hotTopics })
      })
      .catch(() => {
        // 首页装饰性内容失败静默，不影响主功能
      })
  },

  onHotTap(e) {
    wx.navigateTo({ url: '/pages/forum/detail?id=' + e.currentTarget.dataset.id })
  },

  // 搜索框输入
  onKeywordInput(e) {
    this.setData({ keyword: e.detail.value })
  },

  // 搜索提交：切换到 AI Tab（真正 AI 对话由 F4 完成，F3 不实现 SSE）
  onSearch() {
    const keyword = (this.data.keyword || '').trim()
    // 暂存关键词，供 F4 的 AI 页读取后自动带入
    const app = this._app || getApp()
    if (keyword && app && app.globalData) app.globalData.pendingSearch = keyword
    wx.switchTab({ url: '/pages/chat/chat' })
  },

  // 宫格入口：tab 用 switchTab，普通页面用 navigateTo
  onGridTap(e) {
    const { url, tab } = e.currentTarget.dataset
    if (tab) {
      wx.switchTab({ url })
    } else {
      wx.navigateTo({ url })
    }
  },

  // 快捷指令：POST /chat/quick { keyword }
  async onQuickTap(e) {
    const { keyword } = e.currentTarget.dataset
    if (!keyword || this.data.quickLoading) return

    this.setData({ quickLoading: true })
    try {
      const data = await request('/chat/quick', { method: 'POST', data: { keyword } })
      // 真实后端返回 { conversation_id, answer, action: { type } }
      wx.showModal({
        title: keyword,
        content: (data && data.answer) || '暂无回答',
        showCancel: false,
      })
    } catch (err) {
      // 错误提示已由 services/request.js 统一处理，这里仅兜底
    } finally {
      this.setData({ quickLoading: false })
    }
  },
})
