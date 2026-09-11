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
    loading: true,
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
    this.setData({ typeIndex: Number(e.detail.value) }, () => this.refresh())
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
        if (done) done()
      })
      .catch(() => {
        this.setData({ loading: false, error: '加载失败，请稍后重试' })
        if (done) done()
      })
  },

  onItemTap(e) {
    wx.navigateTo({ url: '/pages/job/detail?id=' + e.currentTarget.dataset.id })
  },
})
