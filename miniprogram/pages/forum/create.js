// 发帖（F7）：POST /topics {title, content, category}
const { request } = require('../../services/request')

const CATEGORIES = ['综合', '学习', '生活', '闲置', '活动']

Page({
  data: {
    categories: CATEGORIES,
    catIndex: 0,
    title: '',
    content: '',
    submitting: false,
  },

  onTitle(e) {
    this.setData({ title: e.detail.value })
  },
  onContent(e) {
    this.setData({ content: e.detail.value })
  },
  onCatChange(e) {
    this.setData({ catIndex: Number(e.detail.value) })
  },

  onSubmit() {
    const title = (this.data.title || '').trim()
    const content = (this.data.content || '').trim()
    if (!title) {
      wx.showToast({ title: '标题不能为空', icon: 'none' })
      return
    }
    if (this.data.submitting) return
    this.setData({ submitting: true })

    request('/topics', {
      method: 'POST',
      data: { title, content, category: CATEGORIES[this.data.catIndex] },
    })
      .then(() => {
        this.setData({ submitting: false })
        const app = getApp()
        if (app && app.globalData) app.globalData.forumNeedRefresh = true
        wx.showToast({ title: '发布成功', icon: 'success' })
        setTimeout(() => wx.navigateBack(), 800)
      })
      .catch(() => this.setData({ submitting: false }))
  },
})
