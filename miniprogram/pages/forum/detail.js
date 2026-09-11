// 帖子详情（F7）：GET /topics/{id} + 点赞 + 评论 + 收藏
const { request } = require('../../services/request')
const { formatTime } = require('../../utils/format')

Page({
  data: {
    id: null,
    topic: null,
    comments: [],
    commentInput: '',
    loading: true,
    error: '',
    sending: false,
  },

  onLoad(options) {
    this.setData({ id: Number(options.id) })
    this.fetch()
  },

  fetch() {
    this.setData({ loading: true, error: '' })
    request('/topics/' + this.data.id)
      .then((res) => {
        const topic = {
          ...res,
          time: formatTime(res.created_at),
          commentList: (res.comments || []).map((c) => ({
            id: c.id,
            content: c.content,
            authorName: c.author_name || '',
            time: formatTime(c.created_at),
          })),
        }
        this.setData({ topic, comments: topic.commentList, loading: false })
      })
      .catch(() => this.setData({ loading: false, error: '加载失败，请稍后重试' }))
  },

  onLike() {
    const id = this.data.id
    request('/topics/' + id + '/like', { method: 'POST' })
      .then((res) => {
        const topic = this.data.topic
        this.setData({
          'topic.liked': !!res.liked,
          'topic.like_count': res.like_count != null ? res.like_count : topic.like_count,
        })
      })
      .catch(() => {})
  },

  onFavorite() {
    const id = this.data.id
    request('/favorites', {
      method: 'POST',
      data: { target_type: 'topic', target_id: id },
    })
      .then((res) => {
        this.setData({ 'topic.favorited': !!res.favorited })
        wx.showToast({ title: res.favorited ? '已收藏' : '已取消收藏', icon: 'none' })
      })
      .catch(() => {})
  },

  onCommentInput(e) {
    this.setData({ commentInput: e.detail.value })
  },

  onSendComment() {
    const content = (this.data.commentInput || '').trim()
    if (!content || this.data.sending) return
    this.setData({ sending: true })
    request('/topics/' + this.data.id + '/comments', {
      method: 'POST',
      data: { content },
    })
      .then(() => {
        this.setData({ commentInput: '', sending: false })
        this.fetch()
      })
      .catch(() => this.setData({ sending: false }))
  },
})
