// 图书馆座位预约（F6）入口：阅览室列表
// 说明：后端暂无 GET /library/rooms 列表接口，图书馆建筑 id=1（种子数据「中心图书馆」），
// 这里经真实地图建筑详情接口 GET /map/building/1 取阅览室楼层平面。
const { request } = require('../../services/request')

Page({
  data: {
    loading: true,
    error: '',
    building: null,
    rooms: [],
  },

  onShow() {
    this.fetch()
  },

  fetch() {
    this.setData({ loading: true, error: '' })
    request('/map/building/1')
      .then((res) => {
        const rooms = (res && res.floor_plan) || []
        this.setData({ building: res || null, rooms, loading: false })
      })
      .catch(() => {
        this.setData({ loading: false, error: '加载失败，请稍后重试' })
      })
  },

  onRoomTap(e) {
    const { id, name } = e.currentTarget.dataset
    wx.navigateTo({
      url: '/pages/library/seat?roomId=' + id + '&name=' + encodeURIComponent(name || ''),
    })
  },

  onMyReserve() {
    wx.navigateTo({ url: '/pages/library/myReserve' })
  },
})
