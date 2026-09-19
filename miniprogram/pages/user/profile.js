// 资料设置（需求 9 / F17）：上传头像 + 改昵称 + 绑学号
//
// 依据：docs/二阶段整改方案-前端UI重构与后端支撑.md §2.5（我的）、§3.6（用户资料）
//      docs/成员任务单-二阶段整改260912.md §3.3 F17（验收：学号可绑定；两类错误提示）
// 契约（docs/api.md §2 / §12，**不新增任何接口或字段**）：
//   GET  /user/me        —— 读取当前资料
//   PUT  /user/me        —— 改 nickname / avatar / student_no
//   POST /upload/image   —— multipart（字段名 file），返回 { url, size }；魔数白名单 + 5MB 上限
//
// 三条**来自后端契约**的硬约束（前端行为据此设计，不是猜测）：
//   1. 学号格式规则由**后端适配器配置**提供（§3.6「代码不写死正则」）→ 前端只校验非空，
//      格式 / 唯一性 / 限频一律以后端 message 为准；
//   2. 学号两类业务错误都是 `code=3001`，仅 message 不同（docs/api.md §2）：
//      「该学号已被其他账号绑定」/「学号 7 天内只能修改一次，还需等待 N 天」
//      → 必须按 message 区分展示（本任务验收点），见 classifyStudentNoError；
//   3. **不支持清空学号**（`''` 会与其它账号撞唯一索引，文档明确拒绝）→ 输入留空 = 不改动。
//
// 职责分离：本页是**唯一**发起 `PUT /user/me` 的页面；「我的」主页只读 + 做导航。
const { request, uploadImage } = require('../../services/request')
// 学号归一化 / 头像兜底字符与「我的」页共用（utils/profile.js），避免两页各写一套
const { studentNoOf, firstGlyph } = require('../../utils/profile')

/** 昵称长度上限：db/sql/01_user.sql `nickname VARCHAR(64)` */
const MAX_NICKNAME = 64
/**
 * 学号长度范围：任务单 §7.2 的**默认放宽规则** —— `^\S{4,20}$`（仅非空 + 长度）。
 *
 * ⚠️ 这不是「自造正则」：规格明确学号格式**因校而异**、由后端适配器配置提供
 * （方案 §3.6「代码不写死正则」），4~20 是规格写死的**默认**兜底。
 * 后端目前尚未把学校规则下发给客户端（B23/C21 遗留，见校验脚本 E 段的记录），
 * 因此本地按默认规则提示；真正的判定仍以后端 message 为准（`classifyStudentNoError`）。
 * 注：DB 列宽是 VARCHAR(32)，严格宽于 20 —— 本地上限取规格的 20，不取列宽。
 */
const STUDENT_NO_MIN = 4
const STUDENT_NO_MAX = 20
/** 头像大小上限：backend/app/routers/upload.py `MAX_SIZE = 5MB`（前端先拦，省一次往返） */
const MAX_AVATAR_BYTES = 5 * 1024 * 1024

/**
 * 两类学号错误的**用户可读文案**（本任务验收点之一）。
 * 键名与 `classifyStudentNoError()` 的返回值一一对应。
 */
const STUDENT_NO_ERRORS = {
  occupied: '该学号已被其他账号绑定，请核对后重试',
  frequent: '学号 7 天内只能修改一次，请稍后再试',
}

/**
 * 把后端 `PUT /user/me` 的学号错误归类。
 *
 * ⚠️ 两类错误**都是 code=3001**，唯一区别在 message（docs/api.md §2 的表格），
 * 因此只能按文案归类；归类失败返回 ''，由调用方展示后端原文（不吞错误）。
 *
 * 同时兼容规格里的措辞（任务单 §7.2 写的是「学号修改过于频繁，请 X 天后重试」，
 * 而当前实现拼的是「学号 7 天内只能修改一次，还需等待 N 天」）——两边都认，
 * 后端哪天对齐规格文案也不会让这里退化成「未识别」。
 */
function classifyStudentNoError(err) {
  const msg = String((err && err.message) || '')
  // 唯一索引冲突 → 「该学号已被其他账号绑定」
  if (/已被其他账号绑定|已被占用|已被绑定/.test(msg)) return 'occupied'
  // 限频（当前实现「学号 7 天内只能修改一次，还需等待 N 天」；规格措辞「修改过于频繁」）
  if (/只能修改一次|还需等待|过于频繁/.test(msg)) return 'frequent'
  return ''
}

/** 取错误文案：`request()` 已 toast 过同一句话，这里把它固定显示在页面上 */
function messageOf(err, fallback) {
  const msg = String((err && err.message) || '').trim()
  return msg || fallback
}

/**
 * 去掉上传返回 URL 上的**签名 query**，得到可入库的稳定路径。
 *
 * 依据（后端契约，不是猜测）：
 * - `POST /upload/image` 返回的是**带签名**的访问 URL（`?e=过期时间戳&s=HMAC`，默认 7 天）；
 * - 而 `services/storage.py` 的 `resign()` 明确要求「**入库一律保存不带签名的稳定路径**
 *   （避免过期签名入库）」—— `upload.py` 自己写 `image_asset` 时也做 `access_url.split('?')[0]`；
 * - `/user/me` 对外返回时会重新签名，所以库里存裸路径不影响显示；
 *   反之把签名存进库，7 天后 `user.avatar` 就带着一个**已过期**的签名。
 *
 * ⚠️ 只对本地签名上传路径剥离：换成对象存储后返回的可能是第三方预签名 URL
 * （如 S3 的 `X-Amz-Signature`），剥掉 query 会让它直接失效 —— 判定规则与后端 `resign()` 一致。
 */
function stableUploadPath(url) {
  const s = String(url || '')
  const path = s.split('?')[0]
  return path.indexOf('/static/uploads/') !== -1 ? path : s
}

Page({
  data: {
    loading: true,
    error: '',
    user: null,

    // 表单值（用户可编辑）
    nickname: '',
    studentNo: '',
    avatarText: '校',

    // 状态与反馈
    saving: false, // 保存中（防重复提交）
    avatarUploading: false, // 头像上传中（防重复选择/上传）
    nicknameError: '',
    studentNoError: '',
    formError: '',

    // F10 运行时降级：不支持毛玻璃时根节点挂 .is-glass-fallback
    glassFallback: false,
  },

  onLoad() {
    // F10：能力探测只在 app.js onLaunch 做一次，这里只读结果
    const app = getApp()
    this.setData({
      glassFallback: !(app && app.globalData && app.globalData.glassSupported),
    })
    this.fetch()
  },

  fetch() {
    this.setData({ loading: true, error: '' })
    request('/user/me')
      .then((res) => this.fill(res))
      .catch(() => this.setData({ loading: false, error: '加载失败，请稍后重试' }))
  },

  /** 用服务端值填充表单（首次加载 / 保存成功后） */
  fill(user) {
    const u = user || {}
    this.setData({
      user: u,
      nickname: u.nickname || '',
      studentNo: studentNoOf(u.student_no),
      avatarText: firstGlyph(u.nickname),
      loading: false,
      error: '',
      nicknameError: '',
      studentNoError: '',
      formError: '',
    })
  },

  onNicknameInput(e) {
    this.setData({ nickname: e.detail.value, nicknameError: '', formError: '' })
  },

  onStudentNoInput(e) {
    this.setData({ studentNo: e.detail.value, studentNoError: '', formError: '' })
  },

  // ---------------------------------------------------------------- 头像 ----

  /** 点击头像行 → 选图 → 上传 → 落库（upload.py 只接受 jpg/png/webp） */
  chooseAvatar() {
    if (this.data.avatarUploading || this.data.saving) return // 防重复

    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sizeType: ['compressed'],
      sourceType: ['album', 'camera'],
      success: (res) => {
        const file = ((res && res.tempFiles) || [])[0]
        const filePath = file && file.tempFilePath
        if (!filePath) {
          wx.showToast({ title: '未取到图片，请重试', icon: 'none' })
          return
        }
        if (file.size && file.size > MAX_AVATAR_BYTES) {
          // 与 upload.py 的 5MB 上限一致；先在本地拦，省一次往返
          wx.showToast({ title: '图片不能超过 5MB', icon: 'none' })
          return
        }
        this.uploadAvatar(filePath)
      },
      fail: (err) => {
        // 用户主动取消不算失败，不打扰
        const msg = String((err && err.errMsg) || '')
        if (msg.indexOf('cancel') !== -1) return
        wx.showToast({ title: '选择图片失败，请重试', icon: 'none' })
      },
    })
  },

  uploadAvatar(filePath) {
    this.setData({ avatarUploading: true, formError: '' })
    uploadImage(filePath)
      .then((data) => {
        const url = data && data.url
        // 上传成功但没拿到地址属异常响应：显式失败，避免把 undefined 写进库
        if (!url) throw new Error('上传成功但未返回图片地址，请重试')
        // 落库用**不带签名**的稳定路径（见 stableUploadPath 的契约说明）
        return request('/user/me', { method: 'PUT', data: { avatar: stableUploadPath(url) } })
      })
      .then((user) => {
        this.setData({ avatarUploading: false })
        this.applySaved(user)
        wx.showToast({ title: '头像已更新', icon: 'success' })
      })
      .catch((err) => {
        this.setData({
          avatarUploading: false,
          formError: messageOf(err, '头像更新失败，请稍后重试'),
        })
      })
  },

  // ---------------------------------------------------------------- 保存 ----

  /** 保存昵称 / 学号。只发**有改动**的字段，学号留空 = 不改动。 */
  save() {
    if (this.data.saving || this.data.avatarUploading) return // 防重复提交

    const nickname = String(this.data.nickname || '').trim()
    const studentNo = String(this.data.studentNo || '').trim()
    const currentNickname = String((this.data.user && this.data.user.nickname) || '')
    const currentStudentNo = studentNoOf(this.data.user && this.data.user.student_no)

    if (!nickname) {
      this.setData({ nicknameError: '昵称不能为空' })
      return
    }
    if (nickname.length > MAX_NICKNAME) {
      this.setData({ nicknameError: `昵称最多 ${MAX_NICKNAME} 个字符` })
      return
    }
    if (studentNo.length > STUDENT_NO_MAX) {
      this.setData({ studentNoError: `学号最长 ${STUDENT_NO_MAX} 位` })
      return
    }
    if (studentNo && (studentNo.length < STUDENT_NO_MIN || /\s/.test(studentNo))) {
      // 规格 §7.2 默认规则 `^\S{4,20}$`：非空 + 4~20 位非空白字符
      this.setData({ studentNoError: `学号应为 ${STUDENT_NO_MIN}~${STUDENT_NO_MAX} 位且不含空格` })
      return
    }
    if (!studentNo && currentStudentNo) {
      // docs/api.md §2：不支持清空/解绑学号（'' 会与其它账号撞唯一索引）
      this.setData({ studentNoError: '学号不支持解绑，如需修改请填写新学号' })
      return
    }

    const payload = {}
    if (nickname !== currentNickname) payload.nickname = nickname
    if (studentNo && studentNo !== currentStudentNo) payload.student_no = studentNo

    if (!Object.keys(payload).length) {
      wx.showToast({ title: '没有需要保存的修改', icon: 'none' })
      return
    }

    this.setData({ saving: true, nicknameError: '', studentNoError: '', formError: '' })
    request('/user/me', { method: 'PUT', data: payload })
      .then((user) => {
        this.setData({ saving: false })
        this.applySaved(user)
        wx.showToast({ title: '保存成功', icon: 'success' })
      })
      .catch((err) => {
        const kind = payload.student_no !== undefined ? classifyStudentNoError(err) : ''
        this.setData({
          saving: false,
          studentNoError: kind ? STUDENT_NO_ERRORS[kind] : '',
          formError: kind ? '' : messageOf(err, '保存失败，请稍后重试'),
        })
        // 后端对 nickname/avatar 的写入**先于**学号校验（backend/app/routers/user.py:97-106），
        // 学号被拒时昵称可能已落库 → 回读服务端真实值，避免页面继续显示与库内不一致的资料。
        this.resync()
      })
  },

  /**
   * 保存失败后回读服务端真实值。
   * 只覆盖 `user`（服务端事实），**不覆盖**用户正在编辑的输入框，便于改完再提交。
   */
  resync() {
    request('/user/me')
      .then((user) => {
        const u = user || {}
        this.setData({ user: u, avatarText: firstGlyph(u.nickname) })
      })
      .catch(() => {
        // 回读失败不叠加提示：原错误已展示，用户可下拉/重进页面刷新
      })
  },

  /** 保存成功：同步本地登录态缓存，其它页读缓存时才不会看到旧昵称/头像 */
  applySaved(user) {
    const u = user || {}
    this.setData({
      user: u,
      nickname: u.nickname || '',
      studentNo: studentNoOf(u.student_no),
      avatarText: firstGlyph(u.nickname),
      nicknameError: '',
      studentNoError: '',
      formError: '',
    })
    wx.setStorageSync('user', u)
    const app = getApp()
    if (app && app.globalData) app.globalData.userInfo = u
  },
})
