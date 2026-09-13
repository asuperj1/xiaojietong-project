// 生活服务·外卖点餐（F9-D）：商家列表 GET /life/merchants?category=
const { request } = require('../../services/request')

const CATS = [
  { label: '全部', value: '' },
  { label: '食堂', value: '食堂' },
  { label: '餐厅', value: '餐厅' },
  { label: '超市', value: '超市' },
  { label: '外卖', value: '外卖' },
]

Page({
  data: {
    cats: CATS.map((c) => c.label),
    catIndex: 0,
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

  onCatChange(e) {
    // 分类 tab 是 bindtap（不是 picker）：索引来自 data-index，e.detail 中没有 value
    const index = Number(e.currentTarget.dataset.index)
    if (!Number.isInteger(index) || index < 0 || index >= CATS.length) return
    this.setData({ catIndex: index }, () => this.fetch())
  },

  fetch(done) {
    // 兜底：即使 catIndex 非法（NaN/越界）也不会取到 undefined.value 而抛错
    const cat = CATS[this.data.catIndex] || CATS[0]
    this.setData({ loading: true, error: '' })
    request('/life/merchants', {
      data: { category: cat.value, page: 1, size: 20 },
    })
      .then((res) => {
        this.setData({ items: (res && res.items) || [], loading: false })
        if (typeof done === 'function') done()
      })
      .catch(() => {
        this.setData({ loading: false, error: '加载失败，请稍后重试' })
        if (typeof done === 'function') done()
      })
  },

  onItemTap(e) {
    const { id, name } = e.currentTarget.dataset
    wx.navigateTo({ url: '/pages/life/menu?merchantId=' + id + '&name=' + encodeURIComponent(name || '') })
  },
})
