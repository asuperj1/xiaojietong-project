// 查空教室（F5）：真实调用 GET /library/free-rooms
const { request } = require('../../services/request')

Page({
  data: {
    loading: false,
    error: '',
    items: [],
    campus: '',
    floor: '',
    periods: [],
    periodIndex: 0,
  },

  onLoad() {
    // 节次选项：0=自动（后端按当前时间估算），1-12 对应第 N 节
    const periods = ['自动（当前节次）']
    for (let i = 1; i <= 12; i++) periods.push('第' + i + '节')
    this.setData({ periods })
    this.fetch()
  },

  onCampus(e) {
    this.setData({ campus: e.detail.value })
  },
  onFloor(e) {
    this.setData({ floor: e.detail.value })
  },
  onPeriod(e) {
    this.setData({ periodIndex: Number(e.detail.value) })
  },

  onSearch() {
    this.fetch()
  },

  fetch() {
    this.setData({ loading: true, error: '' })
    const period = this.data.periodIndex === 0 ? '' : String(this.data.periodIndex)
    request('/library/free-rooms', {
      data: {
        campus: (this.data.campus || '').trim(),
        floor: (this.data.floor || '').trim(),
        period,
      },
    })
      .then((res) => {
        this.setData({ items: (res && res.items) || [], loading: false })
      })
      .catch(() => {
        this.setData({ loading: false, error: '加载失败，请稍后重试' })
      })
  },
})
