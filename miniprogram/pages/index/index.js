// 首页（Tab）—— F1A 最小占位，业务由 F3 实现
const app = getApp()

Page({
  data: {},

  onLoad() {
    // 页面初始化完成后检查登录态（安全跳转，见 app.js 的 checkLogin）
    app.checkLogin()
  },
})
