// 论坛页（Tab）—— F7：帖子列表（最新/分类/热点）+ 发帖入口
const { request } = require('../../services/request')
const { formatTime } = require('../../utils/format')

const CATS = [
  { label: '全部', value: '' },
  { label: '学习', value: '学习' },
  { label: '生活', value: '生活' },
  { label: '闲置', value: '闲置' },
  { label: '活动', value: '活动' },
  { label: '热点', value: 'HOT' },
]

Page({
  data: {
    cats: CATS.map((c) => c.label),
    catIndex: 0,
    items: [],
    page: 1,
    loading: true,
    error: '',
    finished: false,
  },

  onShow() {
    // Tab 每次进入刷新列表（发帖/点赞返回后保持最新）
    this.refresh()
  },

  onPullDownRefresh() {
    this.refresh(() => wx.stopPullDownRefresh())
  },

  onReachBottom() {
    if (this.data.finished || this.data.loading) return
    const cat = CATS[this.data.catIndex]
    if (cat.value === 'HOT') return
    this.fetchList(false)
  },

  onCatChange(e) {
    this.setData({ catIndex: Number(e.detail.value) }, () => this.refresh())
  },

  onPublish() {
    wx.navigateTo({ url: '/pages/forum/create' })
  },

  onMine() {
    wx.navigateTo({ url: '/pages/forum/mine' })
  },

  onItemTap(e) {
    wx.navigateTo({ url: '/pages/forum/detail?id=' + e.currentTarget.dataset.id })
  },

  refresh(done) {
    this.setData({ page: 1, items: [], finished: false })
    this.fetchList(true, done)
  },

  fetchList(reset, done) {
    if (this.data.loading) return
    const cat = CATS[this.data.catIndex]
    const isHot = cat.value === 'HOT'
    const page = reset ? 1 : this.data.page
    this.setData({ loading: true, error: '' })

    const path = isHot ? '/topics/hot' : '/topics'
    const data = isHot ? {} : { category: cat.value, page, size: 20 }

    request(path, { data })
      .then((res) => {
        const raw = (res && res.items) || []
        const items = raw.map((t) => ({
          id: t.id,
          title: t.title,
          category: t.category,
          summary: t.ai_summary || (t.content || '').slice(0, 60),
          likeCount: t.like_count || 0,
          commentCount: t.comment_count || 0,
          viewCount: t.view_count || 0,
          authorName: t.author_name || '',
          isHot: !!t.is_hot,
          time: formatTime(t.created_at),
        }))
        this.setData({
          items: reset ? items : this.data.items.concat(items),
          page: page + 1,
          loading: false,
          finished: isHot || raw.length < 20,
        })
        if (done) done()
      })
      .catch(() => {
        this.setData({ loading: false, error: '加载失败，请稍后重试' })
        if (done) done()
      })
  },
})
