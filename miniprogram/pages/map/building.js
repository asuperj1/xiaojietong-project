// 建筑详情（F9-C）：GET /map/building/{id}
const { request } = require('../../services/request')

Page({
  data: {
    id: null,
    building: null,
    rooms: [],
    loading: true,
    error: '',
  },

  onLoad(options) {
    this.setData({ id: Number(options.id) })
    this.fetch()
  },

  fetch() {
    this.setData({ loading: true, error: '' })
    request('/map/building/' + this.data.id)
      .then((res) => {
        const rooms = ((res && res.floor_plan) || []).map((r) => ({
          ...r,
          // 后端 has_power 可能为字符串 "0"/"1"：统一归一化为布尔
          has_power: Number(r.has_power) === 1,
        }))
        this.setData({ building: res, rooms, loading: false })
      })
      .catch(() => this.setData({ loading: false, error: '加载失败，请稍后重试' }))
  },
})
