// 我的帖子（F7）：GET /topics/mine
const { request } = require('../../services/request')
const { formatTime } = require('../../utils/format')

const AUDIT_TEXT = { 0: '待审核', 1: '已通过', 2: '未通过' }

Page({
  data: {
    loading: true,
    error: '',
    items: [],
  },

  onShow() {
    this.fetch()
  },

  fetch() {
    this.setData({ loading: true, error: '' })
    request('/topics/mine')
      .then((res) => {
        const items = ((res && res.items) || []).map((t) => ({
          ...t,
          auditText: AUDIT_TEXT[Number(t.audit_status)] || '未知',
          auditOk: Number(t.audit_status) === 1,
          auditBad: Number(t.audit_status) === 2,
          time: formatTime(t.created_at),
        }))
        this.setData({ items, loading: false })
      })
      .catch(() => this.setData({ loading: false, error: '加载失败，请稍后重试' }))
  },

  onItemTap(e) {
    wx.navigateTo({ url: '/pages/forum/detail?id=' + e.currentTarget.dataset.id })
  },
})
