// 图书馆座位选择（F6）：GET /library/rooms/{roomId}/seats + POST /library/reservations
const { request } = require('../../services/request')

function todayStr() {
  const d = new Date()
  const p = (n) => (n < 10 ? '0' + n : '' + n)
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate())
}

Page({
  data: {
    roomId: null,
    roomName: '',
    date: '',
    beginTime: '09:00',
    endTime: '11:00',
    seats: [],
    selectedId: null,
    loading: true,
    error: '',
    submitting: false,
  },

  onLoad(options) {
    this.setData({
      roomId: Number(options.roomId),
      roomName: decodeURIComponent(options.name || ''),
      date: todayStr(),
    })
    this.fetchSeats()
  },

  onDateChange(e) {
    this.setData({ date: e.detail.value }, () => this.fetchSeats())
  },
  onBeginChange(e) {
    this.setData({ beginTime: e.detail.value })
  },
  onEndChange(e) {
    this.setData({ endTime: e.detail.value })
  },

  fetchSeats() {
    this.setData({ loading: true, error: '', selectedId: null })
    request('/library/rooms/' + this.data.roomId + '/seats', {
      data: { date: this.data.date },
    })
      .then((res) => {
        this.setData({ seats: (res && res.items) || [], loading: false })
      })
      .catch(() => {
        this.setData({ loading: false, error: '加载失败，请稍后重试' })
      })
  },

  onSeatTap(e) {
    const id = Number(e.currentTarget.dataset.id)
    const seat = this.data.seats.find((s) => s.id === id)
    if (seat && seat.reserved) return
    this.setData({ selectedId: id })
  },

  onReserve() {
    const { selectedId, date, beginTime, endTime, submitting } = this.data
    if (!selectedId) {
      wx.showToast({ title: '请先选择座位', icon: 'none' })
      return
    }
    if (submitting) return
    this.setData({ submitting: true })
    request('/library/reservations', {
      method: 'POST',
      data: { seat_id: selectedId, date, begin_time: beginTime, end_time: endTime },
    })
      .then(() => {
        this.setData({ submitting: false })
        wx.showModal({
          title: '预约成功',
          content: '可在「我的预约」中查看或取消',
          showCancel: false,
          success: () => wx.navigateTo({ url: '/pages/library/myReserve' }),
        })
      })
      .catch(() => {
        this.setData({ submitting: false })
      })
  },
})
