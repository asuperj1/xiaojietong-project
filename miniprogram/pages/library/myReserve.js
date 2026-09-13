// 我的座位预约（F6）：GET /library/reservations/me + POST /library/reservations/{id}/cancel
const { request } = require('../../services/request')

const STATUS_TEXT = { 0: '已预约', 1: '已签到', 2: '已取消', 3: '已过期', 4: '已完成' }

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
    request('/library/reservations/me')
      .then((res) => {
        const items = ((res && res.items) || []).map((r) => ({
          ...r,
          statusText: STATUS_TEXT[Number(r.status)] || '未知',
          cancelable: Number(r.status) === 0 || Number(r.status) === 1,
          beginTime: (r.begin_time || '').slice(0, 5),
          endTime: (r.end_time || '').slice(0, 5),
        }))
        this.setData({ items, loading: false })
      })
      .catch(() => {
        this.setData({ loading: false, error: '加载失败，请稍后重试' })
      })
  },

  onCancel(e) {
    const id = e.currentTarget.dataset.id
    wx.showModal({
      title: '取消预约',
      content: '确定取消该座位预约吗？',
      success: (r) => {
        if (!r.confirm) return
        request('/library/reservations/' + id + '/cancel', { method: 'POST' })
          .then(() => this.fetch())
          .catch(() => {})
      },
    })
  },
})
