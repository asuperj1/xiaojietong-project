// 发布二手（F9-A）：POST /secondhand/items；可选 AI 描述 POST /secondhand/items/ai-describe
const { request } = require('../../services/request')

const CATS = ['教材', '数码', '生活', '其他']

Page({
  data: {
    cats: CATS,
    catIndex: 0,
    title: '',
    description: '',
    price: '',
    submitting: false,
    aiLoading: false,
  },

  onTitle(e) {
    this.setData({ title: e.detail.value })
  },
  onDescription(e) {
    this.setData({ description: e.detail.value })
  },
  onPrice(e) {
    this.setData({ price: e.detail.value })
  },
  onCatChange(e) {
    this.setData({ catIndex: Number(e.detail.value) })
  },

  onSubmit() {
    const title = (this.data.title || '').trim()
    if (!title) {
      wx.showToast({ title: '标题不能为空', icon: 'none' })
      return
    }
    if (this.data.submitting) return
    this.setData({ submitting: true })
    request('/secondhand/items', {
      method: 'POST',
      data: {
        title,
        description: (this.data.description || '').trim(),
        category: CATS[this.data.catIndex],
        price: Number(this.data.price) || 0,
      },
    })
      .then(() => {
        this.setData({ submitting: false })
        wx.showToast({ title: '发布成功', icon: 'success' })
        setTimeout(() => wx.navigateBack(), 800)
      })
      .catch(() => this.setData({ submitting: false }))
  },

  // AI 辅助描述（增强功能，不阻塞主流程）
  onAiDescribe() {
    if (this.data.aiLoading) return
    const note = (this.data.description || this.data.title || '').trim()
    this.setData({ aiLoading: true })
    request('/secondhand/items/ai-describe', {
      method: 'POST',
      data: { user_note: note },
    })
      .then((res) => {
        const catIndex = CATS.indexOf(res.category) >= 0 ? CATS.indexOf(res.category) : 0
        this.setData({
          catIndex,
          title: res.title || this.data.title,
          description: res.description || this.data.description,
          price: res.suggested_price != null ? String(res.suggested_price) : this.data.price,
          aiLoading: false,
        })
      })
      .catch(() => this.setData({ aiLoading: false }))
  },
})
