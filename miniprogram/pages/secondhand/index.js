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
    loading: false, // 初始必须为 false：fetchList() 用 loading 防重复请求，若初始为 true 则首次进入不请求
    error: '',
    finished: false,
    // 求购表单
    showWish: false,
    wishContent: '',
    wishCategory: '',
    wishBudget: '',
  },

  onLoad() {
    // 首屏加载：只在 onLoad 发一次，onShow 不无条件刷新（避免重复首屏请求）
    this.fetchList(true)
  },

  onShow() {
    // 发布成功返回时按需刷新（标记由 publish.js 置位），消费后立即清空
    const app = getApp()
    if (app && app.globalData && app.globalData.secondhandNeedRefresh) {
      app.globalData.secondhandNeedRefresh = false
      this.refresh()
    }
  },

  onPullDownRefresh() {
    this.refresh(() => wx.stopPullDownRefresh())
  },

  onReachBottom() {
    if (this.data.finished || this.data.loading) return
    this.fetchList(false)
  },

  onCatChange(e) {
    // 分类 tab 是 bindtap（不是 picker）：索引来自 data-index，e.detail 中没有 value
    const index = Number(e.currentTarget.dataset.index)
    if (!Number.isInteger(index) || index < 0 || index >= CATS.length) return
    this.setData({ catIndex: index }, () => this.refresh())
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
    if (this.data.loading) {
      // 已有请求在途：不重复发起，但仍需回调 done（下拉刷新需停止动画，否则会一直转）
      // 用 typeof 判断：bindtap="refresh" 会把事件对象当 done 传入
      if (typeof done === 'function') done()
      return
    }
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
        if (typeof done === 'function') done()
      })
      .catch(() => {
        this.setData({ loading: false, error: '加载失败，请稍后重试' })
        if (typeof done === 'function') done()
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
