// 我的兼职申请（F9-B）：GET /jobs/applications/me
const { request } = require('../../services/request')
const { formatTime } = require('../../utils/format')

const STATUS_TEXT = { 0: '待处理', 1: '通过', 2: '拒绝', 3: '已取消' }

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
    request('/jobs/applications/me')
      .then((res) => {
        const items = ((res && res.items) || []).map((a) => ({
          ...a,
          statusText: STATUS_TEXT[Number(a.status)] || '未知',
          time: formatTime(a.created_at),
        }))
        this.setData({ items, loading: false })
      })
      .catch(() => this.setData({ loading: false, error: '加载失败，请稍后重试' }))
  },
})
