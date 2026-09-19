#!/usr/bin/env node
/**
 * F17（我的 + 资料设置页）行为验证工具
 *
 * 背景
 * ----
 * 需求 9 / F17：`pages/user/user` 顶部个人信息区（**头像 + 昵称 + 学号**，点击整块进资料设置页）、
 * 保留原列表（我的帖子/二手/座位预约/兼职申请/收藏/任务中心/退出登录）、列表单元格玻璃分割；
 * 新增 `pages/user/profile` 资料设置页（上传头像、改昵称、绑学号）。
 * 依据：`docs/二阶段整改方案-前端UI重构与后端支撑.md` §2.5 / §3.6，任务单 §3.3 F17，
 * 契约 `docs/api.md` §2 / §12（`GET|PUT /user/me`、`POST /upload/image`）。
 *
 * 做法
 * ----
 * 在 Node 里 stub `wx` / `Page` / `getApp`，把 `config` / `services` / `utils` / `pages/user`
 * 整目录拷进临时沙箱后**真实加载**两个页面，驱动真实事件处理函数，断言
 * **实际发出的请求**（含 multipart 上传）、页面状态与提示文案。
 * 无需微信开发者工具、无需网络。
 *
 * 覆盖
 * ----
 *   A. 页面注册与跳转目标一致性（app.json ↔ 真实 navigateTo 目标）
 *   B. 个人信息区：头像 / 昵称 / 学号（含 NULL 与未绑定兜底）
 *   C. 列表保留（对 F8 的回归锁）+ 退出登录行为
 *   D. 玻璃样式与 F12 自定义 TabBar 兼容（根类 + 底部留白数值 + 选中态同步）
 *   E. 设置页请求契约（只调用已登记接口，不发明字段/正则/base URL）
 *   F. 防重复提交与成功/失败反馈
 *   G. 学号「两类错误提示」归类（验收核心）
 *   H. loading / error / retry 状态
 *   I. 结构对账（class 有定义、事件函数存在、标签配平、WXSS 注释定界符配平）
 *   J. 职责分离与范围（主页只读、设置页不依赖 TabBar、app.json 注册表自洽）
 *
 * ⚠️ 验证范围（不要外推）
 * ----------------------
 *   ✅ 覆盖：请求分派、状态机、错误归类、结构对账、注册一致性。
 *   ❌ 不覆盖：**视觉呈现**（玻璃模糊、发丝线观感、头像圆形裁切、低端机性能）、
 *      真机选图/相机权限、真实后端联调 —— 属 MANUAL CHECK（见文末）。
 *
 * 用法
 * ----
 *     node tools/verify_f17_user_profile.js [--src <repo-root>]
 *
 * 输出：`[OK]` / `[NG]` 逐项断言 + 末尾汇总；有失败则退出码 1。
 * R 段是**反向对照**：对真实源码做一处语义突变后，对应断言必须转为失败（证明不是空跑）。
 */

'use strict'

const fs = require('fs')
const os = require('os')
const path = require('path')

// ---------------------------------------------------------------- 参数 ----

function argValue(name, fallback) {
  const i = process.argv.indexOf(name)
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback
}

const SRC_ROOT = path.resolve(argValue('--src', path.join(__dirname, '..')))
const MP = path.join(SRC_ROOT, 'miniprogram')
const USER_DIR = path.join(MP, 'pages', 'user')
const APP_JSON = path.join(MP, 'app.json')
const API_DOC = path.join(SRC_ROOT, 'docs', 'api.md')

const USER_JS = path.join(USER_DIR, 'user.js')
const USER_WXML = path.join(USER_DIR, 'user.wxml')
const USER_WXSS = path.join(USER_DIR, 'user.wxss')
const PROFILE_JS = path.join(USER_DIR, 'profile.js')
const PROFILE_WXML = path.join(USER_DIR, 'profile.wxml')
const PROFILE_WXSS = path.join(USER_DIR, 'profile.wxss')
const PROFILE_JSON = path.join(USER_DIR, 'profile.json')

for (const f of [USER_JS, USER_WXML, USER_WXSS, PROFILE_JS, PROFILE_WXML, PROFILE_WXSS, PROFILE_JSON, APP_JSON]) {
  if (!fs.existsSync(f)) {
    console.log(`[FAIL] 找不到 ${f}`)
    process.exit(1)
  }
}

// ---------------------------------------------------------------- 断言 ----

let pass = 0
let fail = 0

function check(name, ok, detail) {
  ok ? pass++ : fail++
  console.log(`  [${ok ? 'OK' : 'NG'}] ${name}` + (ok || !detail ? '' : `\n         <- ${detail}`))
  return ok
}

function bar(title) {
  console.log('\n' + '='.repeat(84))
  console.log(title)
  console.log('='.repeat(84))
}

/** 读取文本并归一化行尾：仓库 blob 为 LF，Windows 检出可能为 CRLF。
 *  突变锚点含 \n，不归一化会让 replace 静默变成空操作（F16 踩过）。 */
function readText(p) {
  return fs.readFileSync(p, 'utf8').replace(/\r\n/g, '\n')
}

const readRel = (rel) => readText(path.join(MP, rel))

// ---------------------------------------------------------------- 结构工具 ----

/** 取页面 WXML 根元素 class token（与 F12 校验同款：只认第一个带 class 的 view） */
function rootClassTokens(src) {
  const m = src.match(/^[ \t]*<view\b[^>]*\bclass="([^"]*)"/m)
  if (!m) return []
  return m[1]
    .replace(/\{\{[\s\S]*?\}\}/g, ' ')
    .split(/\s+/)
    .filter(Boolean)
}

/** 取声明里的基础 rpx 值（支持 calc(Nrpx + env(...))） */
function rpxBase(decl) {
  const m = String(decl).match(/(\d+(?:\.\d+)?)rpx/)
  return m ? Number(m[1]) : NaN
}

const SAFE_AREA = /env\(\s*safe-area-inset-bottom\s*\)/

/** 收集 WXML 里某属性引用的 class token（class= / hover-class= / placeholder-class=） */
function classTokens(src, attr) {
  const out = new Set()
  const re = new RegExp(`(?<![\\w-])${attr}="([^"]*)"`, 'g')
  let m
  while ((m = re.exec(src))) {
    m[1]
      .replace(/\{\{[\s\S]*?\}\}/g, ' ')
      .split(/\s+/)
      .forEach((c) => {
        if (c) out.add(c)
      })
  }
  return out
}

/** WXML 标签配平（无 DevTools 时唯一可做的语法结构校验） */
function checkTagBalance(src) {
  const noComment = src.replace(/<!--[\s\S]*?-->/g, '')
  const stack = []
  const re = /<(\/?)([a-zA-Z][\w-]*)((?:"[^"]*"|'[^']*'|[^>"'])*?)(\/?)>/g
  let m
  while ((m = re.exec(noComment))) {
    const [, closing, tag, , selfClose] = m
    if (selfClose === '/') continue
    if (closing === '/') {
      const top = stack.pop()
      if (top !== tag) return { ok: false, detail: `</${tag}> 与 <${top || '无'}> 不匹配` }
    } else {
      stack.push(tag)
    }
  }
  return stack.length ? { ok: false, detail: `未闭合：${stack.join(' > ')}` } : { ok: true, detail: '' }
}

/**
 * WXSS 注释健康度：两个**互补**的检测（单靠任一个都漏检）。
 *
 * 1. 定界符配平：`/*` 与 `*` + `/` 数量必须相等 —— 抓「注释未闭合」；
 * 2. 注释剥离后不得残留中日韩文字 —— 抓「注释被提前闭合」。
 *
 * 第 2 条来自 F12 一次真实编译事故（commit 1ef3245）：注释正文里写了 glob
 * `pages/` + `*` + `/` + `*.wxss`，其中的注释结束符让注释**提前闭合**，
 * 后续中文说明被当作样式源码解析 → `[WXSS 文件编译错误] unexpected`。
 * 该写法会让 `/*` 与 `*`+`/` **各自多出一个**，计数仍然配平（3 vs 3 也可能），
 * 所以必须再加「剥离注释后不得有中文」这条：合法 WXSS 的源码区不应出现中文。
 */
function wxssCommentHealth(src) {
  const open = (src.match(/\/\*/g) || []).length
  const close = (src.match(/\*\//g) || []).length
  const stripped = src.replace(/\/\*[\s\S]*?\*\//g, '')
  const cjk = /[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]/
  const leakLine = (stripped.split('\n').find((l) => cjk.test(l)) || '').trim()
  return { open, close, balanced: open === close, leakLine }
}

// ---------------------------------------------------------------- 沙箱 ----

const sandboxes = []

/** 建隔离沙箱：整目录拷贝 config / services / utils / pages/user，可注入突变源码 */
function makeSandbox(mutations) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'xjt-f17-'))
  sandboxes.push(dir)
  for (const sub of ['config', 'services', 'utils']) {
    const from = path.join(MP, sub)
    if (!fs.existsSync(from)) throw new Error(`[sandbox] 缺少前提目录 miniprogram/${sub}`)
    fs.cpSync(from, path.join(dir, sub), { recursive: true })
  }
  fs.cpSync(USER_DIR, path.join(dir, 'pages', 'user'), { recursive: true })
  for (const [rel, text] of Object.entries(mutations || {})) {
    fs.writeFileSync(path.join(dir, rel), text, 'utf8')
  }
  return dir
}

const OK = (data) => ({ code: 0, message: 'ok', data })

const FIXTURE_USER = {
  id: 7,
  openid: 'openid-fixture',
  nickname: '小捷',
  avatar: '',
  role: 0,
  student_no: '2024001234',
  major: '软件工程',
  grade: '2024级',
  campus: '前卫南区',
}

const isMeReq = (r) => /\/user\/me(\?|$)/.test(r.url)
const putMe = (st) => st.requests.filter((r) => isMeReq(r) && r.method === 'PUT')
const getMe = (st) => st.requests.filter((r) => isMeReq(r) && r.method === 'GET')

/**
 * 在沙箱里加载一个真实页面，返回 { page, state }。
 * `wx.request` / `wx.uploadFile` / `wx.chooseMedia` 都可被 responder 精确控制
 * （responder 返回 null = 挂起不回调，用于驱动「请求进行中」的并发场景）。
 */
function loadPage(rel, opts = {}) {
  const dir = opts.dir || makeSandbox(opts.mutations)
  const state = {
    requests: [],
    uploads: [],
    toasts: [],
    modals: [],
    navigate: [],
    switchTab: [],
    reLaunch: [],
    navigateBack: 0,
    chooseMedia: 0,
    hanging: [],
    storage: Object.assign({}, opts.storage),
    setStorageKeys: [],
    user: Object.assign({}, FIXTURE_USER, opts.user || {}),
    responder: opts.responder || null,
    uploadResponder: opts.uploadResponder || null,
    chooseMediaResponder: opts.chooseMediaResponder || null,
    navigateFail: !!opts.navigateFail,
    modalConfirm: opts.modalConfirm !== false,
  }

  function defaultResponse(req) {
    if (isMeReq(req) && req.method === 'GET') return OK(state.user)
    if (isMeReq(req) && req.method === 'PUT') {
      state.user = Object.assign({}, state.user, req.data)
      return OK(state.user)
    }
    return OK({})
  }

  const appInstance = {
    globalData: {
      token: 'stub-token',
      userInfo: null,
      glassSupported: opts.glassSupported !== false,
    },
  }

  global.wx = {
    request(o) {
      const req = { url: o.url, method: o.method || 'GET', data: o.data || {}, header: o.header || {} }
      const n = state.requests.push(req)
      const resp = state.responder ? state.responder(req, n) : undefined
      if (resp === null) {
        state.hanging.push({ req, opts: o })
        return
      }
      if (o.success) o.success({ statusCode: 200, data: resp === undefined ? defaultResponse(req) : resp })
    },
    uploadFile(o) {
      state.uploads.push({ url: o.url, filePath: o.filePath, name: o.name, header: o.header || {} })
      const resp = state.uploadResponder
        ? state.uploadResponder(o, state.uploads.length)
        : OK({ url: 'https://cdn.example/avatar.png?e=1&s=sign', size: 2048 })
      if (resp === null) return
      // 真实 uploadFile 的 res.data 是**字符串**（与 wx.request 不同）
      if (o.success) o.success({ statusCode: 200, data: JSON.stringify(resp) })
    },
    chooseMedia(o) {
      state.chooseMedia += 1
      if (state.chooseMediaResponder) {
        state.chooseMediaResponder(o)
        return
      }
      if (o.success) o.success({ tempFiles: [{ tempFilePath: '/tmp/xjt-avatar.png', size: 2048 }] })
    },
    showToast(o) {
      state.toasts.push((o && o.title) || '')
    },
    showModal(o) {
      state.modals.push(o || {})
      if (o && typeof o.success === 'function') o.success({ confirm: state.modalConfirm })
    },
    navigateTo(o) {
      state.navigate.push(o && o.url)
      if (state.navigateFail && o && typeof o.fail === 'function') o.fail({ errMsg: 'navigateTo:fail mock' })
    },
    switchTab(o) {
      state.switchTab.push(o && o.url)
    },
    reLaunch(o) {
      state.reLaunch.push(o && o.url)
    },
    navigateBack() {
      state.navigateBack += 1
    },
    getStorageSync: (k) => (state.storage[k] === undefined ? '' : state.storage[k]),
    setStorageSync: (k, v) => {
      state.storage[k] = v
      state.setStorageKeys.push(k)
    },
    removeStorageSync: (k) => {
      delete state.storage[k]
    },
  }
  global.getApp = () => appInstance

  let cfg = null
  global.Page = (c) => {
    cfg = c
  }

  require(path.join(dir, rel))
  if (!cfg) return { page: null, state, dir, app: appInstance }

  const page = {
    data: JSON.parse(JSON.stringify(cfg.data || {})),
    setData(patch, cb) {
      Object.assign(this.data, patch)
      if (typeof cb === 'function') cb()
    },
  }
  for (const [k, v] of Object.entries(cfg)) {
    if (typeof v === 'function') page[k] = v.bind(page)
  }
  return { page, state, dir, app: appInstance }
}

const tick = () => new Promise((r) => setTimeout(r, 0))
const tapEvent = (dataset) => ({ currentTarget: { dataset: dataset || {} } })
const inputEvent = (value) => ({ detail: { value } })

// ---------------------------------------------- 探针（供断言与反向对照共用） ----

/** 探针：「我的」页进入成功态 */
async function probeUserPage(opts = {}) {
  const { page, state, app } = loadPage('pages/user/user.js', opts)
  if (!page) return { page, state, app }
  page.onLoad()
  page.onShow()
  await tick()
  return { page, state, app }
}

/** 探针：资料设置页加载完成 */
async function probeProfilePage(opts = {}) {
  const { page, state, app } = loadPage('pages/user/profile.js', opts)
  if (!page) return { page, state, app }
  page.onLoad()
  await tick()
  return { page, state, app }
}

/** 探针：保存学号（后端返回指定 message）后，页面上的 studentNoError / formError */
async function probeStudentNoError(opts = {}) {
  const { page, state } = await probeProfilePage({
    user: Object.assign({ student_no: '' }, opts.user || {}),
    mutations: opts.mutations,
    responder: (req) => {
      if (isMeReq(req) && req.method === 'PUT') return { code: 3001, message: opts.message || '', data: {} }
      return undefined
    },
  })
  if (!page) return { studentNoError: null, formError: null, state, loadFailed: true }
  page.onStudentNoInput(inputEvent('2024999999'))
  page.save()
  await tick()
  return { studentNoError: page.data.studentNoError, formError: page.data.formError, state }
}

/** 探针：学号留空（库内原值非空）时点保存 */
async function probeEmptyStudentNoSave(opts = {}) {
  const { page, state } = await probeProfilePage({
    user: Object.assign({ student_no: '2024001234' }, opts.user || {}),
    mutations: opts.mutations,
  })
  if (!page) return { requests: [], toasts: [], studentNoError: null, loadFailed: true }
  const before = state.requests.length
  page.onStudentNoInput(inputEvent(''))
  page.save()
  await tick()
  return {
    requests: state.requests.slice(before),
    toasts: state.toasts.slice(),
    studentNoError: page.data.studentNoError,
    formError: page.data.formError,
  }
}

// ================================================================ 主流程 ====

async function main() {
  const appJson = JSON.parse(readText(APP_JSON))
  const registered = new Set(appJson.pages || [])
  const apiDoc = fs.existsSync(API_DOC) ? readText(API_DOC) : ''
  const userWxml = readText(USER_WXML)
  const userWxss = readText(USER_WXSS)
  const profileWxml = readText(PROFILE_WXML)
  const profileWxss = readText(PROFILE_WXSS)
  const userJs = readText(USER_JS)
  const profileJs = readText(PROFILE_JS)

  // ================================================================ A ====
  bar('A. 页面注册与跳转目标一致性（设置页正式注册 + 跳转目标一致）')

  check('app.json 可解析且 pages 非空', Array.isArray(appJson.pages) && appJson.pages.length > 0)
  check('资料设置页已注册：pages/user/profile', registered.has('pages/user/profile'))

  {
    const { page, state } = await probeUserPage()
    check('「我的」页可被真实加载（Page 配置存在）', !!page)
    if (page) {
      page.onProfileTap()
      const target = state.navigate[0]
      check('点击个人信息区 → navigateTo 资料设置页', target === '/pages/user/profile', String(target))
      // ★ 上面那条单独存在时可能「跳到未注册页」——必须与注册表交叉验证
      const bare = String(target || '').split('?')[0].replace(/^\//, '')
      check('跳转目标确实已在 app.json 注册（不会跳到未注册页）', registered.has(bare), `注册表无 ${bare}`)
    }
  }

  {
    const { page, state } = await probeUserPage({ navigateFail: true })
    if (page) {
      state.toasts.length = 0
      page.onProfileTap()
      check('跳转失败时给出 toast（唯一入口不静默）', state.toasts.length === 1, `toast=${state.toasts.length}`)
    }
  }

  {
    const { page } = await probeUserPage()
    const menus = (page && page.data.menus) || []
    check('菜单条目数量 = 6', menus.length === 6, `menus=${menus.length}`)
    for (const m of menus) {
      const bare = String(m.url || '').replace(/^\//, '').split('?')[0]
      check(`菜单「${m.name}」目标 ${m.url} 已注册`, registered.has(bare), `注册表无 ${bare}`)
    }
  }

  {
    // 全仓扫描：miniprogram 下任何 '/pages/...' 字面量都必须已在 app.json 注册
    const files = []
    ;(function walk(d) {
      for (const e of fs.readdirSync(d, { withFileTypes: true })) {
        const p = path.join(d, e.name)
        if (e.isDirectory()) walk(p)
        else if (/\.(js|wxml)$/.test(e.name)) files.push(p)
      }
    })(MP)
    const seen = new Set()
    const missing = []
    for (const f of files) {
      const re = /['"](\/pages\/[A-Za-z0-9_/-]+)/g
      let m
      while ((m = re.exec(readText(f)))) {
        const p = m[1].replace(/^\//, '').replace(/\/$/, '')
        if (!p || seen.has(p)) continue
        seen.add(p)
        if (!registered.has(p)) missing.push(`${p} (${path.relative(MP, f).replace(/\\/g, '/')})`)
      }
    }
    check(`全仓引用的 ${seen.size} 个页面路径全部已注册`, missing.length === 0, missing.join('; '))
  }

  {
    // 反向：磁盘上的 page 文件必须都已注册（防「写了页面忘了注册」）
    const orphans = []
    ;(function walk(d) {
      for (const e of fs.readdirSync(d, { withFileTypes: true })) {
        const p = path.join(d, e.name)
        if (e.isDirectory()) walk(p)
        else if (e.name.endsWith('.js')) {
          const rel = path.relative(MP, p).replace(/\\/g, '/')
          if (rel.startsWith('pages/') && !registered.has(rel.replace(/\.js$/, ''))) orphans.push(rel)
        }
      }
    })(MP)
    check('磁盘上的 page 文件均已注册（无「写了页面忘了注册」）', orphans.length === 0, orphans.join(', '))
  }

  {
    const pj = JSON.parse(readText(PROFILE_JSON))
    check('资料设置页 profile.json 有非空页面标题', typeof pj.navigationBarTitleText === 'string' && pj.navigationBarTitleText.length > 0, JSON.stringify(pj))
  }

  // ================================================================ B ====
  bar('B. 个人信息区：头像 + 昵称 + 学号（§2.5 要求 1）')

  check('WXML 渲染头像（image 绑定 user.avatar）', /<image[^>]*class="me-avatar"[^>]*src="\{\{user\.avatar\}\}"/.test(userWxml))
  check('无头像时渲染文字兜底（avatarText）', /me-avatar-text/.test(userWxml) && /\{\{avatarText\}\}/.test(userWxml))
  check('WXML 渲染昵称（含未设置兜底）', /class="me-nickname"/.test(userWxml) && /user\.nickname/.test(userWxml))
  check('WXML 渲染学号（studentNoText）', /class="me-sub"/.test(userWxml) && /\{\{studentNoText\}\}/.test(userWxml))

  {
    const { page } = await probeUserPage({ user: { student_no: null } })
    check(
      '学号 NULL → 显示「未绑定学号」（不渲染字符串 null）',
      !!page && page.data.studentNoText === '未绑定学号',
      page ? String(page.data.studentNoText) : '页面加载失败'
    )
  }
  {
    const { page } = await probeUserPage({ user: { student_no: '' } })
    check('学号空串 → 显示「未绑定学号」', !!page && page.data.studentNoText === '未绑定学号', page && String(page.data.studentNoText))
  }
  {
    const { page } = await probeUserPage({ user: { student_no: '  2024001234  ' } })
    check(
      '学号有值 → 显示「学号 <值>」且去除首尾空白',
      !!page && page.data.studentNoText === '学号 2024001234',
      page && String(page.data.studentNoText)
    )
  }
  {
    const { page } = await probeUserPage({ user: { nickname: '阿捷' } })
    check('头像兜底字符取昵称首字', !!page && page.data.avatarText === '阿', page && String(page.data.avatarText))
  }
  {
    const { page } = await probeUserPage({ user: { nickname: '' } })
    check('昵称为空 → 头像兜底字符不为空', !!page && !!page.data.avatarText, page && String(page.data.avatarText))
  }

  // ================================================================ C ====
  bar('C. 列表保留（§2.5 要求 2）+ 退出登录（对 F8 的回归锁）')

  const EXPECTED_MENUS = [
    ['我的帖子', '/pages/forum/mine'],
    ['我的二手', '/pages/secondhand/mine'],
    ['我的座位预约', '/pages/library/myReserve'],
    ['我的兼职申请', '/pages/job/mine'],
    ['我的收藏', '/pages/user/favorites'],
    ['任务中心', '/pages/agent/index'],
  ]
  {
    const { page } = await probeUserPage()
    const menus = (page && page.data.menus) || []
    check('列表项数量 = 6', menus.length === 6, `menus=${menus.length}`)
    check(
      '列表项顺序与 F8 一致（视觉重构不改出口顺序）',
      menus.map((m) => m.name).join(',') === EXPECTED_MENUS.map((m) => m[0]).join(','),
      menus.map((m) => m.name).join(',')
    )
    for (const [name, url] of EXPECTED_MENUS) {
      const hit = menus.find((m) => m.name === name)
      check(`列表项「${name}」→ ${url}（F8 语义未变）`, !!hit && hit.url === url, hit ? hit.url : '缺失')
    }

    const first = menus[0]
    check('首个列表项可解析（用于点击断言）', !!first && !!first.url, JSON.stringify(first || null))
  }
  {
    const { page, state } = await probeUserPage()
    const first = (page.data.menus || [])[0]
    page.onMenuTap(tapEvent({ url: first.url }))
    check('点击列表项 → navigateTo 对应 url', state.navigate[0] === first.url, String(state.navigate[0]))
    state.navigate.length = 0
    page.onMenuTap(tapEvent({}))
    check('反向对照：无 data-url 时不跳转（不跳到 undefined）', state.navigate.length === 0, String(state.navigate))
  }
  check('WXML 仍提供退出登录入口', /bindtap="logout"/.test(userWxml) && /退出登录/.test(userWxml))
  {
    const { page, state } = await probeUserPage({ storage: { token: 't', refresh_token: 'r', user: FIXTURE_USER } })
    page.logout()
    const cleared = state.storage.token === undefined && state.storage.refresh_token === undefined && state.storage.user === undefined
    check('退出登录：清空 token / refresh_token / user', cleared, JSON.stringify(state.storage))
    check('退出登录：reLaunch 到登录页', state.reLaunch[0] === '/pages/auth/login', String(state.reLaunch[0]))
  }
  {
    const { page, state } = await probeUserPage({ storage: { token: 't' }, modalConfirm: false })
    page.logout()
    check('反向对照：取消确认时不退出登录', state.reLaunch.length === 0 && !!state.storage.token, JSON.stringify(state.storage))
  }

  // ================================================================ D ====
  bar('D. 玻璃样式与 F12 自定义 TabBar 兼容')

  {
    const { page } = await probeUserPage({ glassSupported: false })
    check('glassSupported=false → glassFallback=true（走实心降级）', !!page && page.data.glassFallback === true)
  }
  {
    const { page } = await probeUserPage({ glassSupported: true })
    check('反向对照：glassSupported=true → glassFallback=false（保留毛玻璃）', !!page && page.data.glassFallback === false)
  }
  check('WXML 根节点挂 {{glassFallback}} 降级类入口', /glassFallback\s*\?\s*'is-glass-fallback'/.test(userWxml))
  check('资料区与列表用玻璃卡片类 .xj-glass-card', /me-profile xj-glass-card/.test(userWxml) && /me-menu xj-glass-card/.test(userWxml))
  check('页面用低饱和渐变底 .xj-page-bg（§1.1）', /xj-page-bg/.test(userWxml))

  const rootTokens = rootClassTokens(userWxml)
  check('WXML 根节点带 xj-page 类（F12 留白规则不会退化成死规则）', rootTokens.includes('xj-page'), `根类=[${rootTokens.join(' ')}]`)

  {
    // F12 占用高度从组件 wxss 解析（不写死 110），页面留白必须 ≥ 它 + 安全区
    const barWxss = readRel('custom-tab-bar/index.wxss').replace(/\/\*[\s\S]*?\*\//g, '')
    const barBlock = (barWxss.match(/^\s*\.tabbar\s*\{([^}]*)\}/m) || [])[1] || ''
    const barHeight = Number((barBlock.match(/height:\s*(\d+)rpx/) || [])[1])
    const rules = [...userWxss.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/\.xj-page\s*\{([^}]*)\}/g)].map((m) => m[1])
    const last = rules[rules.length - 1] || ''
    const pb = (last.match(/padding-bottom:\s*([^;]+);/) || [])[1] || ''
    const base = rpxBase(pb)
    check(
      `user.wxss .xj-page 底部留白 ≥ 底栏高度(${barHeight}rpx) + 安全区`,
      Number.isFinite(barHeight) && Number.isFinite(base) && base >= barHeight && SAFE_AREA.test(pb),
      `padding-bottom=${pb.trim() || '未声明'}（解析 ${base}rpx）`
    )
  }

  check(
    'user.js 在 onShow 同步自定义 TabBar 选中项（F12 语义保留）',
    /utils\/tabbar/.test(userJs) && /syncTabBar\(this,\s*'user'\)/.test(userJs)
  )
  {
    const onShowAt = userJs.indexOf('onShow()')
    const syncAt = userJs.indexOf("syncTabBar(this, 'user')")
    check(
      'syncTabBar 位于 onShow 函数体内靠前位置（不会被提前 return 跳过）',
      onShowAt > -1 && syncAt > onShowAt && syncAt - onShowAt < 400,
      `onShow@${onShowAt} sync@${syncAt}`
    )
  }

  {
    // §2.5 要求 3：单元格用玻璃分割（细分割线 + 玻璃底）
    const block = (userWxss.match(/\.me-menu-item\s*\{([^}]*)\}/) || [])[1] || ''
    check(
      '列表单元格有发丝分割线（1rpx + hairline 令牌）',
      /border-bottom:\s*1rpx solid/.test(block) && /--xj-border-hairline/.test(block),
      `规则体=${block.trim().replace(/\s+/g, ' ')}`
    )
    check('最后一行不画分割线（避免与卡片底边叠双线）', /\.me-menu-item:last-child\s*\{[^}]*border-bottom:\s*none/.test(userWxss))
  }
  check(
    '按压反馈走 F10 令牌（§1.3 scale(.96)/150ms）',
    /\.me-pressed\s*\{[^}]*scale\(0\.96\)/.test(userWxss) && /--xj-duration-fast/.test(userWxss)
  )

  // ================================================================ E ====
  bar('E. 设置页请求契约（只调用已登记接口、不发明字段）')

  check('设置页加载即 GET /user/me', getMe((await probeProfilePage()).state).length === 1)

  {
    const { page, state } = await probeProfilePage()
    page.onNicknameInput(inputEvent('新昵称'))
    page.save()
    await tick()
    const puts = putMe(state)
    check('改昵称 → PUT /user/me，且只发 {nickname}', puts.length === 1 && JSON.stringify(puts[0].data) === '{"nickname":"新昵称"}', JSON.stringify(puts.map((p) => p.data)))
    check('保存成功 → toast「保存成功」', state.toasts.indexOf('保存成功') !== -1, JSON.stringify(state.toasts))
  }

  {
    const { page, state } = await probeProfilePage({ user: { student_no: '' } })
    page.onStudentNoInput(inputEvent('2024009999'))
    page.save()
    await tick()
    const puts = putMe(state)
    check('绑学号 → PUT /user/me，且只发 {student_no}', puts.length === 1 && JSON.stringify(puts[0].data) === '{"student_no":"2024009999"}', JSON.stringify(puts.map((p) => p.data)))
  }

  {
    const { page, state } = await probeProfilePage({ user: { student_no: '' } })
    page.onNicknameInput(inputEvent('小捷2'))
    page.onStudentNoInput(inputEvent('2024008888'))
    page.save()
    await tick()
    const d = (putMe(state)[0] || {}).data || {}
    check(
      '同时改昵称 + 绑学号 → 一次 PUT 带两个字段（不拆成两次提交）',
      putMe(state).length === 1 && d.nickname === '小捷2' && d.student_no === '2024008888',
      JSON.stringify(putMe(state).map((p) => p.data))
    )
  }

  {
    // 头像：chooseMedia → POST /upload/image（multipart，字段名 file）→ PUT /user/me {avatar}
    const { page, state } = await probeProfilePage({ storage: { token: 'stub-token' } })
    page.chooseAvatar()
    await tick()
    check('选图走 wx.chooseMedia（不自行拼上传通道）', state.chooseMedia === 1, `chooseMedia=${state.chooseMedia}`)
    check('上传调用 wx.uploadFile 且 url = /upload/image', state.uploads.length === 1 && /\/upload\/image$/.test(state.uploads[0].url), JSON.stringify(state.uploads[0] || null))
    check('上传表单字段名为 file（后端 upload_image(file: UploadFile)）', state.uploads[0] && state.uploads[0].name === 'file', state.uploads[0] && state.uploads[0].name)
    check(
      '上传不带 Content-Type: application/json（multipart boundary 由框架生成）',
      !Object.keys((state.uploads[0] || {}).header || {}).some((k) => /content-type/i.test(k)),
      JSON.stringify((state.uploads[0] || {}).header)
    )
    check('有 token 时上传携带 Bearer token', /^Bearer \S+/.test(((state.uploads[0] || {}).header || {}).Authorization || ''), JSON.stringify((state.uploads[0] || {}).header))
    const puts = putMe(state)
    check(
      '上传成功后 → PUT /user/me {avatar: 上传返回的 url}',
      puts.length === 1 && /^https:\/\/cdn\.example\/avatar\.png/.test(String(puts[0].data.avatar)),
      JSON.stringify(puts.map((p) => p.data))
    )
    check('头像更新成功 → toast「头像已更新」', state.toasts.indexOf('头像已更新') !== -1, JSON.stringify(state.toasts))
  }

  {
    // 反向对照：无 token 时不得伪造 Authorization 头
    const { page, state } = await probeProfilePage()
    page.chooseAvatar()
    await tick()
    check(
      '反向对照：无 token 时不发送 Authorization 头（不伪造 Bearer undefined）',
      !((state.uploads[0] || {}).header || {}).Authorization,
      JSON.stringify((state.uploads[0] || {}).header)
    )
  }

  {
    const { page, state } = await probeProfilePage({ uploadResponder: () => OK({ size: 1 }) })
    page.chooseAvatar()
    await tick()
    check('上传响应缺 url 时不发 PUT（不写坏数据）', putMe(state).length === 0, JSON.stringify(putMe(state).map((p) => p.data)))
    check('上传响应缺 url 时给出错误反馈', page.data.formError !== '', `formError=${page.data.formError}`)
  }

  {
    const { page, state } = await probeProfilePage({
      chooseMediaResponder: (o) => o.success({ tempFiles: [{ tempFilePath: '/tmp/big.png', size: 6 * 1024 * 1024 }] }),
    })
    page.chooseAvatar()
    await tick()
    check('超过 5MB 的图片本地拦截（不发上传）', state.uploads.length === 0 && state.toasts.some((t) => /5MB/.test(t)), JSON.stringify(state.toasts))
  }

  {
    // 请求字段白名单：PUT /user/me 只允许 docs/api.md §2 列出的字段
    const ALLOWED = ['nickname', 'avatar', 'major', 'grade', 'campus', 'student_no']
    const fields = new Set()
    for (const m of profileJs.matchAll(/payload(?:\[['"]([a-z_]+)['"]\]|\.([a-z_]+))\s*=/g)) {
      fields.add(m[1] || m[2])
    }
    const illegal = [...fields].filter((f) => ALLOWED.indexOf(f) === -1)
    check(
      `PUT /user/me 的请求字段都在 §2 白名单内（实际：${[...fields].join(', ') || '无'}）`,
      fields.size > 0 && illegal.length === 0,
      `越界字段：${illegal.join(', ') || '（一个字段都没解析到）'}`
    )
  }

  {
    // 页面调用的接口路径必须已在 docs/api.md 登记（严禁凭空发明）
    const paths = new Set()
    for (const src of [userJs, profileJs]) {
      for (const m of src.matchAll(/request\(\s*['"]([^'"]+)['"]/g)) paths.add(m[1])
      if (/uploadImage\(/.test(src)) paths.add('__uploadImage__')
    }
    const undocumented = []
    for (const p of paths) {
      if (p === '__uploadImage__') {
        if (apiDoc.indexOf('/upload/image') === -1) undocumented.push('/upload/image')
        continue
      }
      if (apiDoc.indexOf(p) === -1) undocumented.push(p)
    }
    check(
      `F17 页面调用的 ${paths.size} 个接口路径均已在 docs/api.md 登记`,
      paths.size > 0 && undocumented.length === 0,
      `未登记：${undocumented.join(', ')}（禁止凭空发明接口）`
    )
  }

  check(
    '设置页不写死学号正则（§3.6：格式规则由后端适配器配置提供）',
    !/new RegExp|\\d\{|\\\[A-Z\\\]|\.test\(studentNo/.test(profileJs.replace(/\/\/[^\n]*/g, '')),
    '页面源码里出现了自造的学号格式校验'
  )

  check('页面不硬编码后端地址（不出现 127.0.0.1 / http:// 字面量）', !/127\.0\.0\.1|http:\/\//.test(userJs + profileJs))

  {
    const r = await probeEmptyStudentNoSave()
    check('学号留空且原值非空 → 不发请求（后端不支持解绑）', r.requests.length === 0, JSON.stringify(r.requests.map((x) => x.data)))
    check('学号留空 → 明确提示「不支持解绑」（就地提示，不静默）', /不支持解绑/.test(r.studentNoError || ''), `studentNoError=${r.studentNoError}`)
  }

  {
    const { page, state } = await probeProfilePage()
    const before = state.requests.length
    page.save()
    await tick()
    check('无改动点保存 → 不发请求', state.requests.length === before, `requests=${state.requests.length - before}`)
    check('无改动点保存 → toast「没有需要保存的修改」', state.toasts.some((t) => /没有需要保存的修改/.test(t)), JSON.stringify(state.toasts))
  }

  // ================================================================ F ====
  bar('F. 防重复提交与成功 / 失败反馈')

  {
    const { page, state } = await probeProfilePage({
      responder: (req) => (isMeReq(req) && req.method === 'PUT' ? null : undefined),
    })
    page.onNicknameInput(inputEvent('重复提交'))
    page.save()
    await tick()
    check('首次保存：saving=true 且发出 1 个 PUT', page.data.saving === true && putMe(state).length === 1, `saving=${page.data.saving} puts=${putMe(state).length}`)
    page.save()
    await tick()
    check('saving 期间再次点保存 → 不发第二个 PUT（防重复提交）', putMe(state).length === 1, `puts=${putMe(state).length}`)
    // 收尾：让挂起的请求以错误结束，验证 saving 会复位（不会永久卡在「保存中」）
    state.hanging[0].opts.success({ statusCode: 200, data: { code: 3001, message: '该学号已被其他账号绑定', data: {} } })
    await tick()
    check('请求结束后 saving 复位（不会永久卡在保存中）', page.data.saving === false, `saving=${page.data.saving}`)
  }

  {
    const { page, state } = await probeProfilePage({ uploadResponder: () => null })
    page.chooseAvatar()
    await tick()
    check('首次选头像：avatarUploading=true 且发出 1 次上传', page.data.avatarUploading === true && state.uploads.length === 1, `uploading=${page.data.avatarUploading} uploads=${state.uploads.length}`)
    page.chooseAvatar()
    await tick()
    check('上传中再次点击头像 → 不重复上传', state.uploads.length === 1 && state.chooseMedia === 1, `uploads=${state.uploads.length} chooseMedia=${state.chooseMedia}`)
  }

  {
    const { page, state, app } = await probeProfilePage({ storage: { token: 't', user: { nickname: '旧' } } })
    page.onNicknameInput(inputEvent('新昵称'))
    page.save()
    await tick()
    check('保存成功 → 更新本地缓存 user（其它页读缓存不落伍）', !!state.storage.user && state.storage.user.nickname === '新昵称', JSON.stringify(state.storage.user))
    check('保存成功 → 同步 globalData.userInfo', !!app.globalData.userInfo && app.globalData.userInfo.nickname === '新昵称', JSON.stringify(app.globalData.userInfo))
  }

  check('保存按钮在 saving 期间禁用', /disabled="\{\{saving\}\}"/.test(profileWxml))
  check('保存中按钮文案切换（保存中…）', /saving \? '保存中…'/.test(profileWxml))

  {
    const { page } = await probeProfilePage({
      responder: (req) => (isMeReq(req) && req.method === 'PUT' ? { code: 5001, message: '服务端错误', data: {} } : undefined),
    })
    page.onNicknameInput(inputEvent('失败昵称'))
    page.save()
    await tick()
    check('保存失败 → 页面显示错误（不静默）', page.data.formError !== '', `formError=${page.data.formError}`)
    check('保存失败 → 展示后端原文（不吞错误）', /服务端错误/.test(page.data.formError), page.data.formError)
    check('保存失败 → saving 复位', page.data.saving === false)
  }

  {
    const { page, state } = await probeProfilePage()
    page.onNicknameInput(inputEvent('   '))
    page.save()
    await tick()
    check('昵称纯空白 → 本地拦截并提示', page.data.nicknameError !== '', `nicknameError=${page.data.nicknameError}`)
    check('昵称纯空白 → 不发请求', putMe(state).length === 0)
  }

  // ================================================================ G ====
  bar('G. 学号「两类错误提示」（验收核心）')

  const occupied = await probeStudentNoError({ message: '该学号已被其他账号绑定' })
  check(
    '「已被占用」类（message=该学号已被其他账号绑定）→ 归入 occupied 文案',
    /已被其他账号绑定/.test(occupied.studentNoError || ''),
    `studentNoError=${occupied.studentNoError}`
  )
  check('「已被占用」不进 formError（分类明确，不重复提示）', occupied.formError === '', `formError=${occupied.formError}`)

  const frequent = await probeStudentNoError({ message: '学号 7 天内只能修改一次，还需等待 3 天' })
  check(
    '「修改过于频繁」类（message=学号 7 天内只能修改一次，还需等待 3 天）→ 归入 frequent 文案',
    /7 天内只能修改一次/.test(frequent.studentNoError || ''),
    `studentNoError=${frequent.studentNoError}`
  )
  check('「修改过于频繁」不进 formError', frequent.formError === '', `formError=${frequent.formError}`)
  check(
    '两类提示文案互不相同（不是同一个兜底文案）',
    !!occupied.studentNoError && !!frequent.studentNoError && occupied.studentNoError !== frequent.studentNoError,
    `occupied=${occupied.studentNoError} / frequent=${frequent.studentNoError}`
  )

  const other = await probeStudentNoError({ message: '学号不能为空' })
  check(
    '未识别错误（1001 学号不能为空）→ 走 formError 展示后端原文',
    other.formError === '学号不能为空' && other.studentNoError === '',
    `formError=${other.formError} studentNoError=${other.studentNoError}`
  )

  {
    const { state } = await probeStudentNoError({ message: '该学号已被其他账号绑定' })
    check('学号被拒后回读服务端真实值（GET /user/me ≥ 2 次：初次 + 回读）', getMe(state).length >= 2, `GET /user/me = ${getMe(state).length}`)
  }

  check('WXML 提供学号错误展示位', /class="pf-error" wx:if="\{\{studentNoError\}\}"/.test(profileWxml))
  check('WXML 提供保存失败展示位', /class="pf-error pf-form-error" wx:if="\{\{formError\}\}"/.test(profileWxml))

  // ================================================================ H ====
  bar('H. loading / error / retry 状态')

  {
    const { page, state } = await probeUserPage({
      responder: (req) => (isMeReq(req) && req.method === 'GET' ? { code: 5001, message: '服务端错误', data: {} } : undefined),
    })
    check('「我的」页 GET 失败 → error 文案', page.data.error === '加载失败，请稍后重试', page.data.error)
    check('「我的」页 GET 失败 → loading 结束', page.data.loading === false)
    check('「我的」页错误态提供重试入口（bindtap="fetch"）', /bindtap="fetch"/.test(userWxml))
    state.requests.length = 0
    page.fetch()
    await tick()
    check('重试会重新发起 GET /user/me', getMe(state).length === 1, `GET=${getMe(state).length}`)
  }

  {
    const { page, state } = await probeProfilePage({
      responder: (req) => (isMeReq(req) && req.method === 'GET' ? { code: 5001, message: '服务端错误', data: {} } : undefined),
    })
    check('设置页 GET 失败 → error 文案', page.data.error === '加载失败，请稍后重试', page.data.error)
    check('设置页错误态提供重试入口', /class="xj-btn-plain pf-retry" bindtap="fetch"/.test(profileWxml))
    state.requests.length = 0
    page.fetch()
    await tick()
    check('设置页重试会重新发起 GET /user/me', getMe(state).length === 1)
  }

  check('「我的」页有加载中文案', /class="xj-loading"/.test(userWxml))
  check('设置页有加载中文案', /class="xj-loading"/.test(profileWxml))
  check('未加载完成时不渲染表单（error 分支在前、表单在 wx:else）', /wx:elif="\{\{error\}\}"/.test(profileWxml) && /<block wx:else>/.test(profileWxml))
  check('昵称/学号输入框 maxlength 与 DB 列宽一致（64 / 32）', /maxlength="64"/.test(profileWxml) && /maxlength="32"/.test(profileWxml))

  // ================================================================ I ====
  bar('I. 结构对账（class 定义 / 事件函数 / 标签配平 / WXSS 注释定界符）')

  const globalCss = readRel('app.wxss') + readRel('styles/glass.wxss') + readRel('styles/tokens.wxss')

  for (const [label, wxml, wxss] of [
    ['user', userWxml, userWxss],
    ['profile', profileWxml, profileWxss],
  ]) {
    const used = new Set([...classTokens(wxml, 'class'), ...classTokens(wxml, 'hover-class'), ...classTokens(wxml, 'placeholder-class')])
    const undef = []
    used.forEach((c) => {
      const re = new RegExp('\\.' + c.replace(/[.*+?^${}()|[\]\\-]/g, '\\$&') + '(?![\\w-])')
      if (!re.test(wxss) && !re.test(globalCss)) undef.push(c)
    })
    check(`${label}.wxml 引用的 ${used.size} 个 class 均有样式定义`, undef.length === 0, `未定义：${undef.join(', ')}`)
  }

  for (const [label, wxml, page] of [
    ['user.wxml', userWxml, (await probeUserPage()).page],
    ['profile.wxml', profileWxml, (await probeProfilePage()).page],
  ]) {
    const handlers = new Set()
    const re = /\bbind[a-z]+\s*=\s*"([A-Za-z_$][A-Za-z0-9_$]*)"/g
    let m
    while ((m = re.exec(wxml))) handlers.add(m[1])
    const missing = [...handlers].filter((h) => !page || typeof page[h] !== 'function')
    check(`${label} 绑定的 ${handlers.size} 个事件处理函数均存在`, missing.length === 0, `缺失：${missing.join(', ')}`)
  }

  for (const [label, wxml] of [
    ['user.wxml', userWxml],
    ['profile.wxml', profileWxml],
  ]) {
    const b = checkTagBalance(wxml)
    check(`${label} 标签闭合与嵌套配平`, b.ok, b.detail)
  }

  for (const [label, p] of [
    ['user.wxss', USER_WXSS],
    ['profile.wxss', PROFILE_WXSS],
  ]) {
    const d = wxssCommentHealth(fs.readFileSync(p, 'utf8'))
    check(
      `${label} 注释健康（定界符配平 ${d.open}/${d.close}，且注释外无中文残留）`,
      d.balanced && !d.leakLine,
      d.balanced ? `注释提前闭合，残留源码：${d.leakLine}` : `/*=${d.open} */=${d.close}`
    )
  }

  {
    // 本仓 34 个 WXSS 的同类扫描（[INFO]：跨任务文件只报告，不作为 F17 的退出码依据）
    const all = []
    ;(function walk(d) {
      for (const e of fs.readdirSync(d, { withFileTypes: true })) {
        const p = path.join(d, e.name)
        if (e.isDirectory()) walk(p)
        else if (e.name.endsWith('.wxss')) all.push(p)
      }
    })(MP)
    const bad = all.filter((p) => {
      const h = wxssCommentHealth(fs.readFileSync(p, 'utf8'))
      return !h.balanced || h.leakLine
    })
    console.log(
      `  [INFO] miniprogram 下 ${all.length} 个 WXSS 同类扫描：${bad.length === 0 ? '全部健康' : '异常 ' + bad.map((p) => path.relative(MP, p)).join(', ')}`
    )
  }

  // ================================================================ J ====
  bar('J. 职责分离与范围（主页只读 / 设置页不依赖 TabBar / F12 语义未动）')

  {
    const { page, state } = await probeUserPage()
    page.onProfileTap()
    page.onMenuTap(tapEvent({ url: '/pages/forum/mine' }))
    await tick()
    check(
      '「我的」主页不发起任何写请求（全部为 GET）',
      state.requests.every((r) => r.method === 'GET'),
      JSON.stringify(state.requests.map((r) => `${r.method} ${r.url}`))
    )
    state.requests.length = 0
    page.logout()
    await tick()
    check('「我的」主页也不发起 PUT /user/me（资料修改只在设置页）', putMe(state).length === 0)
  }

  check('设置页不依赖自定义 TabBar（非 Tab 页不 require utils/tabbar）', !/utils\/tabbar/.test(profileJs))
  {
    const rules = [...profileWxss.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/\.pf-page\s*\{([^}]*)\}/g)].map((m) => m[1])
    const last = rules[rules.length - 1] || ''
    const pb = (last.match(/padding-bottom:\s*([^;]+);/) || [])[1] || ''
    check(
      '设置页只为安全区留白（非 Tab 页，不叠加 110rpx 底栏高度）',
      SAFE_AREA.test(pb) && rpxBase(pb) < 110,
      `padding-bottom=${pb.trim() || '未声明'}`
    )
  }
  check(
    'app.json tabBar 仍是 5 项且 custom=true（F12 语义未动）',
    !!(appJson.tabBar && appJson.tabBar.custom === true && (appJson.tabBar.list || []).length === 5),
    JSON.stringify(appJson.tabBar && appJson.tabBar.list)
  )
  check('app.json 首个页面仍是 pages/index/index（启动页未变）', appJson.pages[0] === 'pages/index/index', String(appJson.pages[0]))
  check('tabBar 中「我的」仍指向 pages/user/user', (appJson.tabBar.list || []).some((i) => i.pagePath === 'pages/user/user'))

  // ============================================== R. 反向对照（negative control）====
  bar('R. 反向对照（对真实源码做一处语义突变后，对应断言必须转为失败 —— 证明不是空跑）')

  // R1 跳转目标打错字
  {
    const mutated = userJs.replace("const PROFILE_URL = '/pages/user/profile'", "const PROFILE_URL = '/pages/user/profiel'")
    check('R1 突变可用（源码确实被改写）', mutated !== userJs)
    const { page, state } = await probeUserPage({ mutations: { 'pages/user/user.js': mutated } })
    page.onProfileTap()
    const target = state.navigate[0]
    check(
      'R1 反证：目标打错时 A 段「跳转 + 已注册」会失败',
      !(target === '/pages/user/profile' && registered.has(String(target).replace(/^\//, ''))),
      String(target)
    )
  }

  // R2 去掉保存的防重复守卫
  {
    const mutated = profileJs.replace('if (this.data.saving || this.data.avatarUploading) return // 防重复提交', '// 防重复提交（已移除）')
    check('R2 突变可用（源码确实被改写）', mutated !== profileJs)
    const { page, state } = await probeProfilePage({
      mutations: { 'pages/user/profile.js': mutated },
      responder: (req) => (isMeReq(req) && req.method === 'PUT' ? null : undefined),
    })
    page.onNicknameInput(inputEvent('重复'))
    page.save()
    await tick()
    page.save()
    await tick()
    check('R2 反证：去掉守卫后 F 段「不重复提交」会失败', putMe(state).length === 2, `puts=${putMe(state).length}`)
  }

  // R3 两类学号错误文案合并为一个
  {
    const mutated = profileJs
      .replace("  occupied: '该学号已被其他账号绑定，请核对后重试',", "  occupied: '学号保存失败，请稍后再试',")
      .replace("  frequent: '学号 7 天内只能修改一次，请稍后再试',", "  frequent: '学号保存失败，请稍后再试',")
    check('R3 突变可用（源码确实被改写）', mutated !== profileJs)
    const a = await probeStudentNoError({ message: '该学号已被其他账号绑定', mutations: { 'pages/user/profile.js': mutated } })
    const b = await probeStudentNoError({ message: '学号 7 天内只能修改一次，还需等待 3 天', mutations: { 'pages/user/profile.js': mutated } })
    check(
      'R3 反证：两类文案相同时 G 段「互不相同」会失败',
      !(a.studentNoError && b.studentNoError && a.studentNoError !== b.studentNoError),
      `${a.studentNoError} / ${b.studentNoError}`
    )
  }

  // R4 去掉学号 NULL 兜底
  {
    const mutated = userJs.replace("  const s = raw === null || raw === undefined ? '' : String(raw).trim()", '  const s = String(raw).trim()')
    check('R4 突变可用（源码确实被改写）', mutated !== userJs)
    const { page } = await probeUserPage({ user: { student_no: null }, mutations: { 'pages/user/user.js': mutated } })
    check('R4 反证：去掉 NULL 兜底后 B 段会失败（会渲染出 null）', page.data.studentNoText !== '未绑定学号', String(page.data.studentNoText))
  }

  // R5 去掉 F12 底部留白
  {
    const mutated = userWxss.replace(/\.xj-page \{\n  padding-bottom: calc\(134rpx \+ env\(safe-area-inset-bottom\)\);\n\}\n/, '')
    check('R5 突变可用（源码确实被改写）', mutated !== userWxss)
    const rules = [...mutated.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/\.xj-page\s*\{([^}]*)\}/g)].map((m) => m[1])
    const last = rules[rules.length - 1] || ''
    const pb = (last.match(/padding-bottom:\s*([^;]+);/) || [])[1] || ''
    check('R5 反证：去掉留白后 D 段底栏间距断言会失败', !(Number.isFinite(rpxBase(pb)) && rpxBase(pb) >= 110), `padding-bottom=${pb.trim() || '未声明'}`)
  }

  // R6 头像改走 JSON 请求（跳过 multipart 上传）
  {
    const mutated = profileJs.replace('    uploadImage(filePath)', "    request('/user/me', { method: 'PUT', data: { avatar: filePath } })")
    check('R6 突变可用（源码确实被改写）', mutated !== profileJs)
    const { page, state } = await probeProfilePage({ mutations: { 'pages/user/profile.js': mutated } })
    page.chooseAvatar()
    await tick()
    check('R6 反证：绕过 /upload/image 时 E 段上传断言会失败', state.uploads.length === 0, `uploads=${state.uploads.length}`)
  }

  // R7 去掉「学号留空 = 不改动」保护（守卫 + 载荷条件两处，属同一语义）
  {
    const mutated = profileJs
      .replace(/    if \(!studentNo && currentStudentNo\) \{[\s\S]*?\n    \}\n/, '')
      .replace('if (studentNo && studentNo !== currentStudentNo) payload.student_no = studentNo', 'if (studentNo !== currentStudentNo) payload.student_no = studentNo')
    check('R7 突变可用（源码确实被改写）', mutated !== profileJs)
    const r = await probeEmptyStudentNoSave({ mutations: { 'pages/user/profile.js': mutated } })
    const sent = r.requests.some((x) => x.method === 'PUT' && x.data && x.data.student_no === '')
    check(
      'R7 反证：去掉保护后 E 段「留空不发请求 / 不支持解绑提示」会失败',
      sent && !/不支持解绑/.test(r.studentNoError || ''),
      `PUT=${JSON.stringify(r.requests.map((x) => x.data))} studentNoError=${r.studentNoError}`
    )
  }

  // R8 在 WXSS 注释里写入 F12 那类 glob（注释被提前闭合）
  {
    const mutated = userWxss.replace('/* ---- 退出登录 ---- */', '/* ---- 退出登录 / 见 pages/*/*.wxss ---- */')
    check('R8 突变可用（源码确实被改写）', mutated !== userWxss)
    const d = wxssCommentHealth(mutated)
    check(
      'R8 反证：注释里写入 glob 后 I 段注释健康断言会失败',
      !d.balanced || !!d.leakLine,
      `/*=${d.open} */=${d.close} leak=${JSON.stringify(d.leakLine)}`
    )
  }

  // ================================================================ 汇总 ====

  sandboxes.forEach((d) => {
    try {
      fs.rmSync(d, { recursive: true, force: true })
    } catch (e) {
      /* 清理失败不影响结论 */
    }
  })

  console.log('\n' + '-'.repeat(84))
  if (fail === 0) {
    console.log(`[PASS] F17 我的 + 资料设置页校验通过（${pass} 项）`)
    console.log('⚠️ 仅覆盖请求分派 / 状态机 / 错误归类 / 结构对账 / 注册一致性；')
    console.log('   视觉呈现（玻璃模糊、发丝线观感、头像圆形裁切、低端机性能）、真机选图与权限、')
    console.log('   真实后端联调仍需微信开发者工具 / 真机确认 —— 属 MANUAL CHECK。')
    process.exit(0)
  }
  console.log(`[FAIL] ${fail} 项未通过 / 共 ${pass + fail} 项`)
  process.exit(1)
}

main().catch((err) => {
  console.error('[FAIL] 验证工具自身异常：', err)
  process.exit(1)
})
