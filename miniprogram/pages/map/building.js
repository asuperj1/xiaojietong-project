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
        this.setData({ building: res, rooms: (res && res.floor_plan) || [], loading: false })
      })
      .catch(() => this.setData({ loading: false, error: '加载失败，请稍后重试' }))
  },
})
