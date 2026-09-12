// 岗位详情（F9-B）：GET /jobs/{id} + GET /jobs/{id}/trust + POST /jobs/{id}/apply
const { request } = require('../../services/request')
const { formatTime } = require('../../utils/format')

const RISK_TEXT = { 0: '低风险', 1: '中风险', 2: '高风险' }

Page({
  data: {
    id: null,
    job: null,
    trust: null,
    resume: '',
    loading: true,
    error: '',
    applying: false,
  },

  onLoad(options) {
    this.setData({ id: Number(options.id) })
    this.fetch()
  },

  fetch() {
    this.setData({ loading: true, error: '' })
    request('/jobs/' + this.data.id)
      .then((res) => {
        const job = { ...res, time: formatTime(res.created_at) }
        this.setData({ job, loading: false })
        return request('/jobs/' + this.data.id + '/trust')
      })
      .then((t) => {
        this.setData({ trust: { ...t, riskText: RISK_TEXT[Number(t.risk_level)] || '未知' } })
      })
      .catch(() => {
        // 详情失败才提示；trust 失败不影响主体展示
        if (this.data.loading) {
          this.setData({ loading: false, error: '加载失败，请稍后重试' })
        }
      })
  },

  onResume(e) {
    this.setData({ resume: e.detail.value })
  },

  onApply() {
    if (this.data.applying) return
    this.setData({ applying: true })
    request('/jobs/' + this.data.id + '/apply', {
      method: 'POST',
      data: { resume: (this.data.resume || '').trim() },
    })
      .then(() => {
        this.setData({ applying: false })
        wx.showModal({
          title: '投递成功',
          content: '可在「我的兼职申请」中查看进度',
          showCancel: false,
        })
      })
      .catch(() => this.setData({ applying: false }))
  },
})
