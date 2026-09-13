// 校捷通小程序全局入口（F1A 最小骨架）
// 规格：miniprogram/前端页面规格.md §0 —— app.js 在 onLaunch 检查 token
const { detectGlass } = require('./utils/glass')

App({
  globalData: {
    token: '',      // 登录后由登录页写入
    userInfo: null, // 登录后缓存当前用户信息
    pendingSearch: '', // 首页搜索框携带的关键词，AI 页 onShow 读取后自动发送
    chatRestore: null, // AI 历史会话回传：{ conversationId, messages }，chat 页读取后立即清空
    secondhandNeedRefresh: false, // 二手发布成功后置 true，集市列表页 onShow 按需刷新
    glassSupported: false, // F10：探测前默认保守降级，onLaunch 后由 detectGlass() 覆盖
  },

  onLaunch() {
    // F10：探测毛玻璃能力，结果存入 globalData 供页面按需取用（不在此处操作页面）
    this.globalData.glassSupported = !!detectGlass()

    // 启动时恢复本地登录态（token + user）
    this.globalData.token = wx.getStorageSync('token') || ''
    this.globalData.userInfo = wx.getStorageSync('user') || null
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
