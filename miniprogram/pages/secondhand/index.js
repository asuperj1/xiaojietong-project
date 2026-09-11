// 二手集市（F9-A）：列表 + 搜索 + 求购 + 发布入口
const { request } = require('../../services/request')
const { formatTime } = require('../../utils/format')

const CATS = [
  { label: '全部', value: '' },
  { label: '教材', value: '教材' },
  { label: '数码', value: '数码' },
  { label: '生活', value: '生活' },
  { label: '其他', value: '其他' },
]

function buildDetailUrl(it) {
  const qs = [
    'id=' + it.id,
    'title=' + encodeURIComponent(it.title || ''),
    'description=' + encodeURIComponent(it.description || ''),
    'category=' + encodeURIComponent(it.category || ''),
    'price=' + (it.price || 0),
    'condition=' + (it.condition_level != null ? it.condition_level : ''),
    'trust=' + (it.trust_score != null ? it.trust_score : ''),
    'seller=' + encodeURIComponent(it.seller_name || ''),
    'created=' + encodeURIComponent(it.created_at || ''),
  ].join('&')
  return '/pages/secondhand/detail?' + qs
}

Page({
  data: {
    cats: CATS.map((c) => c.label),
    catIndex: 0,
    q: '',
    items: [],
    page: 1,
    loading: true,
    error: '',
    finished: false,
    // 求购表单
    showWish: false,
    wishContent: '',
    wishCategory: '',
    wishBudget: '',
  },

  onLoad() {
    this.fetchList(true)
  },

  onPullDownRefresh() {
    this.refresh(() => wx.stopPullDownRefresh())
  },

  onReachBottom() {
    if (this.data.finished || this.data.loading) return
    this.fetchList(false)
  },

  onCatChange(e) {
    this.setData({ catIndex: Number(e.detail.value) }, () => this.refresh())
  },

  onSearchInput(e) {
    this.setData({ q: e.detail.value })
  },
  onSearch() {
    this.refresh()
  },

  refresh(done) {
    this.setData({ page: 1, items: [], finished: false })
    this.fetchList(true, done)
  },

  fetchList(reset, done) {
    if (this.data.loading) return
    const cat = CATS[this.data.catIndex]
    const page = reset ? 1 : this.data.page
    this.setData({ loading: true, error: '' })
    request('/secondhand/items', {
      data: { category: cat.value, q: (this.data.q || '').trim(), page, size: 20 },
    })
      .then((res) => {
        const raw = (res && res.items) || []
        const items = raw.map((it) => ({
          ...it,
          time: formatTime(it.created_at),
        }))
        this.setData({
          items: reset ? items : this.data.items.concat(items),
          page: page + 1,
          loading: false,
          finished: raw.length < 20,
        })
        if (done) done()
      })
      .catch(() => {
        this.setData({ loading: false, error: '加载失败，请稍后重试' })
        if (done) done()
      })
  },

  onItemTap(e) {
    const item = this.data.items.find((i) => i.id === Number(e.currentTarget.dataset.id))
    if (item) wx.navigateTo({ url: buildDetailUrl(item) })
  },

  onPublish() {
    wx.navigateTo({ url: '/pages/secondhand/publish' })
  },

  onMine() {
    wx.navigateTo({ url: '/pages/secondhand/mine' })
  },

  onOpenWish() {
    this.setData({ showWish: true })
  },
  onCloseWish() {
    this.setData({ showWish: false })
  },
  onWishContent(e) {
    this.setData({ wishContent: e.detail.value })
  },
  onWishCategory(e) {
    this.setData({ wishCategory: e.detail.value })
  },
  onWishBudget(e) {
    this.setData({ wishBudget: e.detail.value })
  },

  // 发布求购 → 立即查询匹配
  onSubmitWish() {
    const content = (this.data.wishContent || '').trim()
    if (!content) {
      wx.showToast({ title: '求购内容不能为空', icon: 'none' })
      return
    }
    request('/secondhand/wishes', {
      method: 'POST',
      data: {
        content,
        category: (this.data.wishCategory || '').trim(),
        budget: Number(this.data.wishBudget) || 0,
      },
    })
      .then((res) => {
        const wishId = res.wish_id
        this.setData({ showWish: false, wishContent: '', wishCategory: '', wishBudget: '' })
        request('/secondhand/wishes/' + wishId + '/match')
          .then((m) => {
            const list = (m && m.items) || []
            if (list.length === 0) {
              wx.showModal({ title: '暂无匹配', content: '暂未找到匹配的闲置物品', showCancel: false })
            } else {
              const text = list
                .map((x) => x.title + '（¥' + x.price + '，卖家 ' + (x.seller_name || '') + '）')
                .join('\n')
              wx.showModal({ title: '匹配结果', content: text, showCancel: false })
            }
          })
          .catch(() => {})
      })
      .catch(() => {})
  },
})
