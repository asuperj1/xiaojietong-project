// 我的收藏（F8）：GET /favorites?target_type=topic|item
const { request } = require('../../services/request')
const { formatTime } = require('../../utils/format')

Page({
  data: {
    tab: 'topic',
    items: [],
    loading: true,
    error: '',
  },

  onLoad() {
    this.fetch()
  },

  onTab(e) {
    this.setData({ tab: e.currentTarget.dataset.tab }, () => this.fetch())
  },

  fetch() {
    this.setData({ loading: true, error: '' })
    request('/favorites', { data: { target_type: this.data.tab } })
      .then((res) => {
        const items = ((res && res.items) || []).map((it) => {
          // 后端收藏对象 id 可能为字符串：统一归一化为数字，否则 onItemTap 的严格相等匹配不上
          const numId = Number(it.id)
          return {
            id: Number.isFinite(numId) ? numId : it.id,
            title: it.title,
            category: it.category,
            price: it.price,
            likeCount: it.like_count,
            commentCount: it.comment_count,
            time: formatTime(it.favorited_at || it.created_at),
          }
        })
        this.setData({ items, loading: false })
      })
      .catch(() => this.setData({ loading: false, error: '加载失败，请稍后重试' }))
  },

  onItemTap(e) {
    const { id, tab } = e.currentTarget.dataset
    if (tab === 'topic') {
      wx.navigateTo({ url: '/pages/forum/detail?id=' + id })
      return
    }
    // 二手暂无详情接口，复用列表数据经 URL 参数进入二手详情
    const item = this.data.items.find((i) => i.id === Number(id))
    if (!item) return
    const qs = [
      'id=' + item.id,
      'title=' + encodeURIComponent(item.title || ''),
      'category=' + encodeURIComponent(item.category || ''),
      'price=' + (item.price || 0),
    ].join('&')
    wx.navigateTo({ url: '/pages/secondhand/detail?' + qs })
  },
})
