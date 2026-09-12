// 我的发布（F9-A）：GET /secondhand/items/mine + PUT /secondhand/items/{id}/status
const { request } = require('../../services/request')
const { formatTime } = require('../../utils/format')

const STATUS_TEXT = { 0: '在售', 1: '已售', 2: '已下架' }
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
    request('/secondhand/items/mine')
      .then((res) => {
        const items = ((res && res.items) || []).map((it) => ({
          ...it,
          statusText: STATUS_TEXT[Number(it.status)] || '未知',
          auditText: AUDIT_TEXT[Number(it.audit_status)] || '',
          time: formatTime(it.created_at),
          onSale: Number(it.status) === 0,
          offSale: Number(it.status) === 2,
        }))
        this.setData({ items, loading: false })
      })
      .catch(() => this.setData({ loading: false, error: '加载失败，请稍后重试' }))
  },

  onToggleStatus(e) {
    const { id, status } = e.currentTarget.dataset
    request('/secondhand/items/' + id + '/status', {
      method: 'PUT',
      data: { status },
    })
      .then(() => this.fetch())
      .catch(() => {})
  },
})
