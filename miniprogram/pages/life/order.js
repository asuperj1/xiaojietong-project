// 订单详情（F9-D）：GET /life/orders/{id}，并拉取菜单映射商品名
const { request } = require('../../services/request')
const { formatTime } = require('../../utils/format')

const STATUS_TEXT = { 1: '已提交', 2: '商家接单', 3: '配送中', 4: '已完成', 5: '已取消' }

Page({
  data: {
    orderId: null,
    order: null,
    items: [],
    loading: true,
    error: '',
  },

  onLoad(options) {
    this.setData({ orderId: Number(options.orderId) })
    this.fetch()
  },

  fetch() {
    this.setData({ loading: true, error: '' })
    request('/life/orders/' + this.data.orderId)
      .then((order) => {
        const statusText = STATUS_TEXT[Number(order.status)] || '未知状态'
        this.setData({ order: { ...order, statusText, time: formatTime(order.created_at) }, loading: false })
        // 拉菜单映射菜品名
        return request('/life/merchants/' + order.merchant_id + '/menu')
      })
      .then((menu) => {
        const nameMap = {}
        ;((menu && menu.items) || []).forEach((m) => {
          nameMap[m.id] = m.name
        })
        const items = ((this.data.order && this.data.order.items) || []).map((it) => ({
          id: it.id,
          num: it.num,
          name: nameMap[it.id] || ('商品 #' + it.id),
        }))
        this.setData({ items })
      })
      .catch(() => {
        if (this.data.loading) this.setData({ loading: false, error: '加载失败，请稍后重试' })
      })
  },
})
