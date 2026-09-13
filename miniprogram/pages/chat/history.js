// 历史会话列表（F4）：GET /chat/conversations → 点选后加载消息并回传 AI 页
// 跨页传递：消息条数多、不适合 URL query，改用 globalData.chatRestore 临时传递，
// 由 chat 页 onShow 消费后立即清空。
const { request } = require('../../services/request')
const { formatTime } = require('../../utils/format')

Page({
  data: {
    loading: true,
    error: '',
    items: [],   // { id, title, time }
  },

  onLoad() {
    // 官方建议：getApp() 在页面生命周期内调用
    this._app = getApp()
    this.fetch()
  },

  fetch() {
    this.setData({ loading: true, error: '' })
    request('/chat/conversations')
      .then((res) => {
        const items = ((res && res.items) || []).map((c) => ({
          id: c.id,
          title: c.title || '未命名会话',
          time: formatTime(c.updated_at),
        }))
        this.setData({ items, loading: false })
      })
      .catch(() => {
        this.setData({ loading: false, error: '加载失败，请稍后重试' })
      })
  },

  // 点选会话：拉取历史消息 → 暂存 globalData → 切回 AI 页
  onTapConversation(e) {
    const id = Number(e.currentTarget.dataset.id)
    if (!id || this._loading) return
    this._loading = true
    wx.showLoading({ title: '加载中…', mask: true })

    request('/chat/conversations/' + id + '/messages')
      .then((res) => {
        const messages = (res && res.items) || []
        if (this._app && this._app.globalData) {
          this._app.globalData.chatRestore = { conversationId: id, messages }
        }
        this._loading = false
        wx.hideLoading()
        // chat 为 TabBar 页，switchTab 不能带参数，故经 globalData 传递
        wx.switchTab({ url: '/pages/chat/chat' })
      })
      .catch(() => {
        // 错误提示已由 services/request.js 统一处理
        this._loading = false
        wx.hideLoading()
      })
  },
})
