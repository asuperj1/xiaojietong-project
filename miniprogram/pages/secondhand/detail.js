// 二手商品详情（F9-A）：数据来自列表 URL 参数（后端暂无详情接口），下单走 POST /secondhand/orders
const { request } = require('../../services/request')
const { formatTime } = require('../../utils/format')

Page({
  data: {
    id: null,
    title: '',
    description: '',
    category: '',
    price: '0',
    condition: '',
    trust: '',
    seller: '',
    created: '',
    remark: '',
    buying: false,
  },

  onLoad(options) {
    this.setData({
      id: Number(options.id),
      title: decodeURIComponent(options.title || ''),
      description: decodeURIComponent(options.description || ''),
      category: decodeURIComponent(options.category || ''),
      price: options.price || '0',
      condition: options.condition || '',
      trust: options.trust || '',
      seller: decodeURIComponent(options.seller || ''),
      created: formatTime(decodeURIComponent(options.created || '')),
    })
  },

  onRemark(e) {
    this.setData({ remark: e.detail.value })
  },

  onBuy() {
    if (this.data.buying) return
    this.setData({ buying: true })
    request('/secondhand/orders', {
      method: 'POST',
      data: { item_id: this.data.id, remark: (this.data.remark || '').trim() },
    })
      .then((res) => {
        this.setData({ buying: false })
        wx.showModal({
          title: '下单成功',
          content: '订单号：' + res.order_id + '\n金额：¥' + res.amount,
          showCancel: false,
        })
      })
      .catch(() => this.setData({ buying: false }))
  },
})
