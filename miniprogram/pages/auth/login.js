// 登录页（F2）：微信一键登录
// 依据：miniprogram/前端页面规格.md §0、docs/api.md §2
const { request } = require('../../services/request.js')

Page({
  data: {
    loading: false, // 登录请求进行中，防止重复点击
  },

  onLogin() {
    // loading 期间拦截重复点击
    if (this.data.loading) return
    this.setData({ loading: true })

    wx.login({
      success: (res) => {
        if (!res.code) {
          // wx.login 成功但未返回 code（罕见）
          this.setData({ loading: false })
          wx.showToast({ title: '登录失败，请重试', icon: 'none' })
          return
        }

        request('/auth/wechat-login', { method: 'POST', data: { code: res.code } })
          .then((payload) => {
            // 保存登录态到本地存储
            wx.setStorageSync('token', payload.token)
            wx.setStorageSync('refresh_token', payload.refresh_token)
            wx.setStorageSync('user', payload.user)

            // 同步全局登录态
            const app = getApp()
            app.globalData.token = payload.token
            app.globalData.userInfo = payload.user

            this.setData({ loading: false })

            // 首页是 TabBar 页面，必须用 switchTab 跳转
            wx.switchTab({ url: '/pages/index/index' })
          })
          .catch(() => {
            // request() 内部已对业务错误/网络错误 toast，这里仅恢复 loading
            this.setData({ loading: false })
          })
      },
      fail: () => {
        // wx.login 自身失败（网络异常、微信登录能力调用失败等）
        this.setData({ loading: false })
        wx.showToast({ title: '微信登录失败，请重试', icon: 'none' })
      },
    })
  },
})
