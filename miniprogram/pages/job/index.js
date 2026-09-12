// 兼职实习（F9-B）：岗位列表 GET /jobs?type=&q=
const { request } = require('../../services/request')
const { formatTime } = require('../../utils/format')

const TYPES = [
  { label: '全部', value: '' },
  { label: '校内勤工', value: '校内勤工' },
  { label: '实习', value: '实习' },
  { label: '兼职', value: '兼职' },
]

Page({
  data: {
    types: TYPES.map((t) => t.label),
    typeIndex: 0,
    q: '',
    items: [],
    page: 1,
    loading: false, // 初始必须为 false：fetchList() 用 loading 防重复请求，若初始为 true 则首次进入不请求
    error: '',
    finished: false,
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

  onTypeChange(e) {
    // 类型 tab 是 bindtap（不是 picker）：索引来自 data-index，e.detail 中没有 value
    const index = Number(e.currentTarget.dataset.index)
    if (!Number.isInteger(index) || index < 0 || index >= TYPES.length) return
    this.setData({ typeIndex: index }, () => this.refresh())
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
    const type = TYPES[this.data.typeIndex]
    const page = reset ? 1 : this.data.page
    this.setData({ loading: true, error: '' })
    request('/jobs', {
      data: { type: type.value, q: (this.data.q || '').trim(), page, size: 20 },
    })
      .then((res) => {
        const raw = (res && res.items) || []
        const items = raw.map((j) => ({
          ...j,
          time: formatTime(j.created_at),
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
    wx.navigateTo({ url: '/pages/job/detail?id=' + e.currentTarget.dataset.id })
  },
})
