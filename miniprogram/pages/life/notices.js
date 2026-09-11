// 通知公告（F9-D）：GET /life/notices + GET /life/notice-feed + POST /life/notices/{id}/read
const { request } = require('../../services/request')
const { formatTime } = require('../../utils/format')

Page({
  data: {
    mode: 'all', // all=全部通知 feed=为我推荐
    items: [],
    loading: true,
    error: '',
  },

  onLoad() {
    this.fetch()
  },

  onPullDownRefresh() {
    this.fetch(() => wx.stopPullDownRefresh())
  },

  onMode(e) {
    this.setData({ mode: e.currentTarget.dataset.mode }, () => this.fetch())
  },

  fetch(done) {
    this.setData({ loading: true, error: '' })
    const path = this.data.mode === 'feed' ? '/life/notice-feed' : '/life/notices'
    request(path, { data: { page: 1, size: 20 } })
      .then((res) => {
        const items = ((res && res.items) || []).map((n) => ({
          ...n,
          time: formatTime(n.publish_time),
        }))
        this.setData({ items, loading: false })
        if (done) done()
      })
      .catch(() => {
        this.setData({ loading: false, error: '加载失败，请稍后重试' })
        if (done) done()
      })
  },

  onItemTap(e) {
    const item = this.data.items.find((n) => n.id === Number(e.currentTarget.dataset.id))
    if (!item) return
    // 标记已读（幂等）
    request('/life/notices/' + item.id + '/read', { method: 'POST' }).catch(() => {})
    wx.showModal({
      title: item.title || '通知',
      content: item.content || '',
      showCancel: false,
      confirmText: '知道了',
    })
  },
})
