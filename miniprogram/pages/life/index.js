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
    this.setData({ catIndex: Number(e.detail.value) }, () => this.fetch())
  },

  fetch(done) {
    this.setData({ loading: true, error: '' })
    request('/life/merchants', {
      data: { category: CATS[this.data.catIndex].value, page: 1, size: 20 },
    })
      .then((res) => {
        this.setData({ items: (res && res.items) || [], loading: false })
        if (done) done()
      })
      .catch(() => {
        this.setData({ loading: false, error: '加载失败，请稍后重试' })
        if (done) done()
      })
  },

  onItemTap(e) {
    const { id, name } = e.currentTarget.dataset
    wx.navigateTo({ url: '/pages/life/menu?merchantId=' + id + '&name=' + encodeURIComponent(name || '') })
  },
})
