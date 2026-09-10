// 校捷通小程序全局入口（F1A 最小骨架）
// 规格：miniprogram/前端页面规格.md §0 —— app.js 在 onLaunch 检查 token
App({
  globalData: {
    token: '',      // 登录后由登录页写入
    userInfo: null, // 登录后缓存当前用户信息
  },

  onLaunch() {
    // 启动时读取本地 token（仅检查，不做复杂状态管理）
    this.globalData.token = wx.getStorageSync('token') || ''
  },

  // 登录态检查：无 token 时跳登录页。
  // 由首页（首个 Tab 页）在 onLoad 后调用，避免 App.onLaunch 阶段页面尚未初始化导致的跳转失败。
  checkLogin() {
    const token = this.globalData.token || wx.getStorageSync('token')
    if (!token) {
      wx.reLaunch({ url: '/pages/auth/login' })
      return false
    }
    return true
  },
})
