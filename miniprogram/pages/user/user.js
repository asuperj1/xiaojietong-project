// 我的（Tab）—— 需求 9 / F17：个人信息区（头像 + 昵称 + 学号）+ 玻璃分割列表
//
// 依据：docs/二阶段整改方案-前端UI重构与后端支撑.md §2.5（我的）
//      docs/成员任务单-二阶段整改260912.md §3.3 F17
// 契约：GET /user/me（docs/api.md §2）—— 本页只读；资料修改在资料设置页 pages/user/profile。
//
// 职责分离（§2.5 要求「我的主页」与「资料设置页」分开）：
//   · 本页 = 展示 + 各业务出口；
//   · pages/user/profile = 上传头像 / 改昵称 / 绑学号（唯一发起 PUT /user/me 的地方）。
//
// F12（自定义 TabBar）：本页是 5 个 Tab 页之一 —— onShow 必须同步选中项，
//   且必须为 fixed 底栏自留底部空间（见 user.wxss 末尾），两条都由
//   tools/verify_f12_tabbar_poc.js 守护，本任务不改其语义。
const { request } = require('../../services/request')
const { syncTabBar } = require('../../utils/tabbar')

/** 资料设置页路径（唯一事实来源：onProfileTap 与校验脚本共用同一常量） */
const PROFILE_URL = '/pages/user/profile'

/**
 * 菜单项是 **F8 既有的业务出口**：F17 只改视觉（玻璃底 + 细分割线），不改去向。
 * 由 tools/verify_f17_user_profile.js 的 C 段逐条锁定，并断言每个 url 已在 app.json 注册。
 */
const MENUS = [
  { name: '我的帖子', url: '/pages/forum/mine' },
  { name: '我的二手', url: '/pages/secondhand/mine' },
  { name: '我的座位预约', url: '/pages/library/myReserve' },
  { name: '我的兼职申请', url: '/pages/job/mine' },
  { name: '我的收藏', url: '/pages/user/favorites' },
  { name: '任务中心', url: '/pages/agent/index' },
]

/**
 * 学号展示文案（含未绑定兜底）。
 *
 * ⚠️ 后端 `_view()` 对 NULL 学号返回的是 **null** 而不是 ''（`u.get('student_no', '')`
 * 只在键缺失时兜底，而 db/sql/17_user_student_no.sql 把空串清成了 NULL 且列可空）。
 * 直接用模板插值会渲染出字符串 "null"，故在 JS 侧统一归一化。
 */
function studentNoDisplay(raw) {
  const s = raw === null || raw === undefined ? '' : String(raw).trim()
  return s ? '学号 ' + s : '未绑定学号'
}

/** 头像兜底字符：取昵称首字（用 Array.from 而非 [0]，避免把 emoji 的代理对截成半个字符） */
function firstGlyph(nickname) {
  const chars = Array.from(String(nickname || '').trim())
  return chars.length ? chars[0] : '校'
}

Page({
  data: {
    loading: true,
    error: '',
    user: null,
    studentNoText: '未绑定学号',
    avatarText: '校',
    // F10 运行时降级：能力探测判定不支持毛玻璃时置 true → 根节点挂 .is-glass-fallback
    glassFallback: false,
    menus: MENUS,
  },

  onLoad() {
    // F10：能力探测只在 app.js onLaunch 做一次，这里只读结果、不重复探测
    const app = getApp()
    this.setData({
      glassFallback: !(app && app.globalData && app.globalData.glassSupported),
    })
  },

  onShow() {
    // F12：同步自定义 TabBar 选中项（必须位于任何提前 return 之前）
    syncTabBar(this, 'user')
    // 从资料设置页返回后要看到最新资料，故每次 onShow 都重新拉取
    this.fetch()
  },

  fetch() {
    this.setData({ loading: true, error: '' })
    request('/user/me')
      .then((res) => this.applyUser(res))
      .catch(() => this.setData({ loading: false, error: '加载失败，请稍后重试' }))
  },

  applyUser(user) {
    const u = user || {}
    this.setData({
      user: u,
      studentNoText: studentNoDisplay(u.student_no),
      avatarText: firstGlyph(u.nickname),
      loading: false,
      error: '',
    })
  },

  /** 需求 9-1：点击整块个人信息区 → 资料设置页 */
  onProfileTap() {
    wx.navigateTo({
      url: PROFILE_URL,
      // 失败不能静默：这里是进入资料设置的唯一入口，静默失败 = 用户点了没反应
      fail: (err) => {
        console.error('[user] 打开资料设置页失败：', err)
        wx.showToast({ title: '打开失败，请重试', icon: 'none' })
      },
    })
  },

  onMenuTap(e) {
    const { url } = e.currentTarget.dataset
    if (!url) return
    wx.navigateTo({
      url,
      fail: (err) => {
        console.error('[user] 打开菜单失败：', url, err)
        wx.showToast({ title: '打开失败，请重试', icon: 'none' })
      },
    })
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
        // getApp() 在方法内按需获取（不在模块顶层调用）
        const app = getApp()
        if (app && app.globalData) {
          app.globalData.token = ''
          app.globalData.userInfo = null
        }
        wx.reLaunch({ url: '/pages/auth/login' })
      },
    })
  },
})
