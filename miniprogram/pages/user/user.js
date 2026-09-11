// 我的页（Tab）—— F8：用户基础信息 + 各业务入口 + 退出登录
const { request } = require('../../services/request')

const app = getApp()

Page({
  data: {
    loading: true,
    error: '',
    user: null,
    menus: [
      { name: '我的帖子', url: '/pages/forum/mine' },
      { name: '我的二手', url: '/pages/secondhand/mine' },
      { name: '我的座位预约', url: '/pages/library/myReserve' },
      { name: '我的兼职申请', url: '/pages/job/mine' },
      { name: '我的收藏', url: '/pages/user/favorites' },
      { name: '任务中心', url: '/pages/agent/index' },
    ],
  },

  onShow() {
    this.fetch()
  },

  fetch() {
    this.setData({ loading: true, error: '' })
    request('/user/me')
      .then((res) => this.setData({ user: res, loading: false }))
      .catch(() => this.setData({ loading: false, error: '加载失败，请稍后重试' }))
  },

  onMenuTap(e) {
    wx.navigateTo({ url: e.currentTarget.dataset.url })
  },

  logout() {
    wx.showModal({
      title: '退出登录',
      content: '确定退出当前账号吗？',
      success: (r) => {
        if (!r.confirm) return
        wx.removeStorageSync('token')
        wx.removeStorageSync('refresh_token')
        wx.removeStorageSync('user')
        if (app.globalData) {
          app.globalData.token = ''
          app.globalData.userInfo = null
        }
        wx.reLaunch({ url: '/pages/auth/login' })
      },
    })
  },
})
