// 商家菜单与下单（F9-D）：GET /life/merchants/{id}/menu + POST /life/orders
const { request } = require('../../services/request')

Page({
  data: {
    merchantId: null,
    merchantName: '',
    items: [],
    totalCount: 0,
    totalPrice: '0',
    address: '',
    contact: '',
    contactPhone: '',
    remark: '',
    loading: true,
    error: '',
    submitting: false,
  },

  onLoad(options) {
    this.setData({
      merchantId: Number(options.merchantId),
      merchantName: decodeURIComponent(options.name || ''),
    })
    this.fetch()
  },

  fetch() {
    this.setData({ loading: true, error: '' })
    request('/life/merchants/' + this.data.merchantId + '/menu')
      .then((res) => {
        const items = ((res && res.items) || []).map((it) => ({ ...it, num: 0 }))
        this.setData({ items, loading: false })
        this.recompute()
      })
      .catch(() => this.setData({ loading: false, error: '加载失败，请稍后重试' }))
  },

  onPlus(e) {
    const id = Number(e.currentTarget.dataset.id)
    const items = this.data.items.map((it) => (it.id === id ? { ...it, num: it.num + 1 } : it))
    this.setData({ items }, () => this.recompute())
  },
  onMinus(e) {
    const id = Number(e.currentTarget.dataset.id)
    const items = this.data.items.map((it) => (it.id === id ? { ...it, num: Math.max(0, it.num - 1) } : it))
    this.setData({ items }, () => this.recompute())
  },

  recompute() {
    let count = 0
    let price = 0
    this.data.items.forEach((it) => {
      count += it.num
      price += Number(it.price || 0) * it.num
    })
    this.setData({ totalCount: count, totalPrice: price.toFixed(2) })
  },

  onAddress(e) {
    this.setData({ address: e.detail.value })
  },
  onContact(e) {
    this.setData({ contact: e.detail.value })
  },
  onPhone(e) {
    this.setData({ contactPhone: e.detail.value })
  },
  onRemark(e) {
    this.setData({ remark: e.detail.value })
  },

  onSubmit() {
    const cart = this.data.items.filter((it) => it.num > 0).map((it) => ({ id: it.id, num: it.num }))
    if (cart.length === 0) {
      wx.showToast({ title: '请先选择商品', icon: 'none' })
      return
    }
    if (this.data.submitting) return
    this.setData({ submitting: true })
    request('/life/orders', {
      method: 'POST',
      data: {
        merchant_id: this.data.merchantId,
        items: cart,
        address: (this.data.address || '').trim(),
        contact: (this.data.contact || '').trim(),
        contact_phone: (this.data.contactPhone || '').trim(),
        remark: (this.data.remark || '').trim(),
      },
    })
      .then((res) => {
        this.setData({ submitting: false })
        wx.redirectTo({ url: '/pages/life/order?orderId=' + res.order_id })
      })
      .catch(() => this.setData({ submitting: false }))
  },
})
