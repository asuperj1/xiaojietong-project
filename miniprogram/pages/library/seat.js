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
        const seats = ((res && res.items) || []).map((s) => {
          // 后端字段可能为字符串（如 id="1"、reserved="0"）：统一归一化。
          // 否则 item.id === selectedId 恒不成立（选不上），且字符串 "0" 在
          // WXML 中是真值，会把可用座位误渲染为已占用
          const numId = Number(s.id)
          return {
            ...s,
            id: Number.isFinite(numId) ? numId : s.id,
            reserved: Number(s.reserved) === 1,
            is_window: Number(s.is_window) === 1,
            has_power: Number(s.has_power) === 1,
          }
        })
        this.setData({ seats, loading: false })
      })
      .catch(() => {
        this.setData({ loading: false, error: '加载失败，请稍后重试' })
      })
  },

  onSeatTap(e) {
    const id = Number(e.currentTarget.dataset.id)
    const seat = this.data.seats.find((s) => s.id === id)
    // 找不到就忽略（不写入 selectedId）；已占用座位同样禁止选中
    if (!seat || seat.reserved) return
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
      .catch((err) => {
        this.setData({ submitting: false })
        // 3001：并发抢座冲突（后端事务内校验占用）。需明确提示并刷新最新座位状态，
        // 否则用户看到的仍是过期状态（该座位仍显示可选），会反复点击反复失败。
        if (err && err.code === 3001) {
          wx.showModal({
            title: '该座位已被预约',
            content: err.message || '该座位已被预约，请重新选择',
            showCancel: false,
          })
          this.setData({ selectedId: null })
          this.fetchSeats() // 重新拉取最新座位状态（内部亦会清空 selectedId）
        }
        // 其他错误：已由 services/request.js 统一 toast，这里不重复提示
      })
  },
})
