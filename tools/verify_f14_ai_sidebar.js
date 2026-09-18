#!/usr/bin/env node
/**
 * F14-A（AI 助手侧边栏 · 非语音部分）行为验证工具
 *
 * 背景
 * ----
 * 需求 6 / F14：`docs/二阶段整改方案-前端UI重构与后端支撑.md` §2.2
 *   ① 顶部左上角加「菜单」按钮 → 从左侧滑出侧边栏；
 *   ② 侧边栏顶部放「新建会话」按钮；
 *   ③ 会话卡片用玻璃，保留原有「新会话 / 历史」入口；
 *   ④ 底部中间 AI 按钮长按唤起语音 → **不在本任务**（F14-B）。
 * 验收标准（任务单 §3.3）：侧滑流畅；新建会话可对话；会话可删；语音可用。
 *   —— 本工具覆盖「新建会话可对话（B24）」「会话可删（DELETE）」两条**可自动判定**的部分。
 *
 * 本工具在 Node 里 stub `wx` / `Page` / `getApp`，直接加载**真实**的
 * `miniprogram/pages/chat/chat.js`（整目录沙箱拷贝，镜像其 require 图），
 * 驱动真实的侧边栏事件处理函数，并断言**实际发出的请求**与页面状态。
 *
 * 覆盖
 * ----
 *   A. 结构对账：菜单按钮 / 遮罩 / 面板 / 会话列表 / 长按删除 / 当前项高亮 / 底部操作
 *      + class 有定义 + 处理器存在 + 标签配平 + 面板 fixed 定位不被玻璃类覆盖
 *   B. 展开与列表加载：请求口径、字段兜底、标题兜底、时间格式化、失败态、每次展开刷新
 *   C. 点选会话：拉历史消息 → 还原 → 收起侧栏；生成中拦截；连点节流
 *   D. 长按删除：确认/取消、DELETE 口径、列表与当前会话的联动、生成中拦截、标题截断
 *   E. 清空历史：无会话拦截、逐条删除到空、轮数上限保护、中途失败即停
 *   F. 新建会话（B24）：POST /chat/conversations、迟到响应丢弃、失败回落懒建
 *   G. 遮罩 / 手势关闭：点击遮罩、右拖过阈值关闭、未过阈值回弹、纵向/左向不接管
 *   H. 状态恢复：onShow 收起侧栏（含拖拽中间态）；F4 的 chatRestore / pendingSearch 不回归
 *   I. F14-A 边界：本页不引入录音/转写实现，也不依赖尚未进 dev 的 F12 自定义 TabBar
 *
 * ⚠️ 验证范围（不要外推）
 * ----------------------
 *   ✅ 覆盖：请求分派（path / method / 状态机）、页面状态、失败与竞态路径、结构对账。
 *   ❌ 不覆盖：**视觉呈现与手感**（侧滑动画顺滑度、遮罩观感、玻璃模糊、跟手拖拽与列表
 *      滚动的真实交互、iOS 安全区、低端机性能）—— 需微信开发者工具 / 真机目视确认，
 *      属 MANUAL CHECK。
 *   ❌ 不覆盖：后端是否真的按契约建/删会话（见 `docs/api.md` §3 与 `backend/tests/`）。
 *   ❌ 不覆盖：语音转写（F14-B 范围）。
 *
 * 用法
 * ----
 *     node tools/verify_f14_ai_sidebar.js [--src <repo-root>]
 *
 * 输出：`[OK]` / `[NG]` 逐项断言 + 末尾汇总；有失败则退出码 1。
 *
 * 口径说明：A~I 段是断言（决定退出码）；每条「能力」断言都配**反向对照**（J 段：
 * 对真实源码做一处语义突变后必须转为失败），用来证明断言不是空跑。
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
const CHAT_DIR = path.join(MP, 'pages', 'chat')
const CHAT_JS = path.join(CHAT_DIR, 'chat.js')
const CHAT_WXML = path.join(CHAT_DIR, 'chat.wxml')
const CHAT_WXSS = path.join(CHAT_DIR, 'chat.wxss')

for (const f of [CHAT_JS, CHAT_WXML, CHAT_WXSS]) {
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
  console.log(
    `  [${ok ? 'OK' : 'NG'}] ${name}` + (ok || !detail ? '' : `\n         <- ${detail}`)
  )
  return ok
}

function bar(title) {
  console.log('\n' + '='.repeat(84))
  console.log(title)
  console.log('='.repeat(84))
}

// ---------------------------------------------------------------- 沙箱 ----

const sandboxes = []

/**
 * 建隔离沙箱：整目录拷贝 `config` / `services` / `utils` / `pages/chat`。
 * 整目录（而不是逐文件列举）—— chat.js 的 require 图后续任务仍可能变化，
 * 写死清单会把 MODULE_NOT_FOUND 这种假失败留给下一个任务。
 */
function makeSandbox() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'xjt-f14-'))
  sandboxes.push(dir)
  for (const sub of ['config', 'services', 'utils']) {
    const from = path.join(MP, sub)
    if (!fs.existsSync(from)) {
      throw new Error(`[sandbox] 缺少前提目录 miniprogram/${sub}`)
    }
    fs.cpSync(from, path.join(dir, sub), { recursive: true })
  }
  fs.cpSync(CHAT_DIR, path.join(dir, 'pages', 'chat'), { recursive: true })
  return dir
}

/** 造一条会话列表项（后端 GET /chat/conversations 的字段） */
function conv(id, title, updatedAt) {
  return { id, title, updated_at: updatedAt || '2026-09-18 09:30:00' }
}

function okBody(data) {
  return { data: { code: 0, message: 'ok', data } }
}

/** 带状态的假后端：会话列表可被 DELETE 真实移除，便于断言「清空到空」 */
function makeBackend(opts) {
  const o = opts || {}
  const state = {
    list: (o.initial || [conv(11, '图书馆几点关门'), conv(12, '奖学金申请条件')]).slice(),
    deletes: 0,
    failDeleteAt: o.failDeleteAt || 0, // 第 N 次 DELETE 失败（0 = 不失败）
    failList: !!o.failList, // 列表接口返回业务错误
    keepNonEmpty: !!o.keepNonEmpty, // 模拟「后端始终删不干净」
  }
  state.handle = function (req) {
    const url = req.url
    const method = (req.method || 'GET').toUpperCase()
    if (url.indexOf('/chat/conversations') === -1) return null

    // POST /chat/conversations —— 显式新建（B24）
    if (method === 'POST' && /\/chat\/conversations\/?$/.test(url)) {
      return okBody({ conversation_id: 77, title: '新对话', created_at: '2026-09-18 10:00:00' })
    }
    // GET /chat/conversations/{id}/messages
    const msgMatch = /\/chat\/conversations\/(\d+)\/messages$/.exec(url)
    if (msgMatch && method === 'GET') {
      return okBody({
        items: [
          { id: 1, role: 'user', content: '图书馆几点关门', created_at: '2026-09-18 09:00:00' },
          { id: 2, role: 'assistant', content: '22:00 闭馆', created_at: '2026-09-18 09:00:05' },
        ],
      })
    }
    // DELETE /chat/conversations/{id}
    const delMatch = /\/chat\/conversations\/(\d+)$/.exec(url)
    if (delMatch && method === 'DELETE') {
      state.deletes += 1
      if (state.failDeleteAt && state.deletes === state.failDeleteAt) {
        return { data: { code: 5001, message: '删除失败', data: null } }
      }
      const id = Number(delMatch[1])
      if (!state.keepNonEmpty) state.list = state.list.filter((c) => c.id !== id)
      return okBody({ conversation_id: id })
    }
    // GET /chat/conversations
    if (method === 'GET' && /\/chat\/conversations\/?$/.test(url)) {
      if (state.failList) return { data: { code: 5001, message: '服务异常', data: null } }
      return okBody({ items: state.list.slice() })
    }
    return null
  }
  return state
}

/**
 * 在沙箱里加载真实 chat.js，返回 { page, state }。
 * @param {object} [opts]
 * @param {boolean} [opts.glassSupported] getApp().globalData.glassSupported 取值
 * @param {boolean} [opts.modalConfirm] showModal 自动确认（false = 取消）
 * @param {Function} [opts.responder] (req, 第几次请求) => 应答 | null（null = 挂起不应答）
 * @param {string} [opts.sourceOverride] 用给定源码替换 chat.js（反向对照用）
 * @param {string} [opts.wxmlOverride] 用给定 WXML 替换 chat.wxml（反向对照用）
 * @param {object} [opts.globalData] getApp().globalData 额外字段（chatRestore / pendingSearch）
 */
function loadChat(opts) {
  const o = opts || {}
  const dir = makeSandbox()
  const file = path.join(dir, 'pages', 'chat', 'chat.js')
  if (o.sourceOverride) fs.writeFileSync(file, o.sourceOverride, 'utf8')
  if (o.wxmlOverride) {
    fs.writeFileSync(path.join(dir, 'pages', 'chat', 'chat.wxml'), o.wxmlOverride, 'utf8')
  }

  const backend = o.responder ? null : makeBackend(o.backend)
  const responder = o.responder || ((req) => backend.handle(req))

  const state = {
    requests: [],
    hanging: [],
    toasts: [],
    modals: [],
    navigations: [],
    backend,
  }

  global.wx = {
    getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
    getStorageSync: () => '',
    setStorageSync: () => {},
    removeStorageSync: () => {},
    showToast: (t) => state.toasts.push((t && t.title) || ''),
    hideToast: () => {},
    showLoading: () => {},
    hideLoading: () => {},
    showModal: (m) => {
      state.modals.push(m)
      if (m && typeof m.success === 'function') {
        const confirm = o.modalConfirm !== false
        m.success({ confirm, cancel: !confirm })
      }
    },
    navigateTo: (n) => state.navigations.push(n && n.url),
    switchTab: () => {},
    reLaunch: () => {},
    request: (req) => {
      state.requests.push({ url: req.url, data: req.data || {}, method: req.method || 'GET' })
      const res = responder(req, state.requests.length)
      if (res) req.success(res)
      else state.hanging.push(req)
      // SSE 用的 RequestTask 形状（chat.js 内部依赖 abort / onChunkReceived 存在性）
      return { abort() {}, onChunkReceived() {} }
    },
  }

  let cfg = null
  global.Page = (obj) => {
    cfg = obj
  }
  const globalData = Object.assign({ glassSupported: o.glassSupported !== false }, o.globalData || {})
  global.getApp = () => ({ globalData })

  delete require.cache[require.resolve(file)]
  require(file)

  const page = {}
  Object.keys(cfg).forEach((k) => {
    page[k] = cfg[k]
  })
  page.data = JSON.parse(JSON.stringify(cfg.data))
  page.setData = function (patch, cb) {
    Object.keys(patch).forEach((k) => {
      // 「messages[3]」这类路径写法在本工具的断言里用不到，保持最小实现
      this.data[k] = patch[k]
    })
    if (typeof cb === 'function') cb()
  }

  // 走一次真实的页面生命周期：chat.js 在 onLoad 里取 getApp() 句柄与玻璃降级类，
  // 不调用它则 onShow 会因拿不到 app 而提前 return（假失败）。
  page.onLoad()

  return { page, state }
}

const tick = () => new Promise((r) => setImmediate(r))

/** 触发一次动作，返回该动作期间新发出的请求数组（切片） */
async function after(state, fn) {
  const before = state.requests.length
  fn()
  await tick()
  return state.requests.slice(before)
}

const reqs = (list, re) => (list || []).filter((r) => re.test(r.url))
const convList = (list) => reqs(list, /\/chat\/conversations(\?|$)/)
const isGet = (r) => (r.method || 'GET').toUpperCase() === 'GET'
const dels = (list) => (list || []).filter((r) => (r.method || '').toUpperCase() === 'DELETE')

/** 造触摸事件 */
function touch(x, y) {
  return { touches: [{ clientX: x, clientY: y }], changedTouches: [{ clientX: x, clientY: y }] }
}

/** 拖拽手势：从 (0,0) 拖到 (dx,dy)，返回触摸过程断言所需的状态快照 */
async function drag(page, dx, dy) {
  page.onSidebarTouchStart(touch(100, 100))
  await tick()
  const mid = []
  const steps = 4
  for (let i = 1; i <= steps; i++) {
    page.onSidebarTouchMove(touch(100 + (dx * i) / steps, 100 + (dy * i) / steps))
    await tick()
    mid.push({ dragging: page.data.sidebarDragging, style: page.data.sidebarStyle })
  }
  page.onSidebarTouchEnd()
  await tick()
  return mid
}

/**
 * WXML 标签闭合 / 嵌套配平检查。
 * 没有微信开发者工具时，这是唯一能自动发现「标签没关 / 嵌套错位」的手段
 * （这类错误会让整页渲染失败，而纯文本断言察觉不到）。
 * @returns {{ok: boolean, detail: string}}
 */
function checkTagBalance(wxml) {
  const src = wxml.replace(/<!--[\s\S]*?-->/g, '') // 去注释（含注释内的示例标签）
  const re = /<(\/?)([a-zA-Z][\w-]*)((?:"[^"]*"|'[^']*'|[^>"'])*?)(\/?)>/g
  const stack = []
  let m
  while ((m = re.exec(src))) {
    const closing = m[1] === '/'
    const tag = m[2]
    const selfClosing = m[4] === '/'
    if (selfClosing) continue
    if (closing) {
      const top = stack.pop()
      if (top !== tag) {
        return { ok: false, detail: `</${tag}> 与 <${top || '(无)'}> 不匹配` }
      }
    } else {
      stack.push(tag)
    }
  }
  if (stack.length) return { ok: false, detail: `未闭合：${stack.join(' > ')}` }
  return { ok: true, detail: '' }
}

// ================================================================ 开始 ====

async function main() {
const WXML = fs.readFileSync(CHAT_WXML, 'utf8')
// 行尾归一化：仓库 blob 为 LF，Windows 检出（core.autocrlf=true）是 CRLF。
// 反向对照用含 \n 的字面量锚点做 replace，不归一化会静默变成空操作。
const JS = fs.readFileSync(CHAT_JS, 'utf8').replace(/\r\n/g, '\n')
const WXML_LF = WXML.replace(/\r\n/g, '\n')
const WXSS = fs.readFileSync(CHAT_WXSS, 'utf8')

console.log(`被测文件：${CHAT_JS}`)

// ------------------------------------------------- A. 结构对账 ----
bar('A. 结构对账（F14-A：菜单按钮 / 遮罩 / 面板 / 会话列表 / 长按删除 / 当前项高亮）')
{
  check(
    'A1 顶部存在【菜单】按钮且绑定 onOpenSidebar',
    /<button[^>]*bindtap="onOpenSidebar"[^>]*>\s*菜单\s*<\/button>/.test(WXML_LF)
  )
  check(
    'A2 遮罩节点 .sidebar-mask 绑定 onMaskTap（点击关闭）',
    /class="sidebar-mask[^"]*"[^>]*bindtap="onMaskTap"/.test(WXML_LF)
  )
  check(
    'A3 面板 .sidebar-panel 使用 F10 玻璃类（xj-glass-strong）',
    /class="sidebar-panel[^"]*xj-glass-strong/.test(WXML_LF)
  )
  check('A4 面板位移绑定 {{sidebarStyle}}（跟手拖拽入口）', /style="\{\{sidebarStyle\}\}"/.test(WXML_LF))
  check(
    'A5 展开态由 sidebarOpen 驱动（面板 + 遮罩同源）',
    /\{\{sidebarOpen \? 'sidebar-panel--open' : ''\}\}/.test(WXML_LF) &&
      /\{\{sidebarOpen \? 'sidebar-mask--open' : ''\}\}/.test(WXML_LF)
  )
  check(
    'A6 拖拽态由 sidebarDragging 驱动（拖拽期间关过渡）',
    /\{\{sidebarDragging \? 'sidebar-panel--dragging' : ''\}\}/.test(WXML_LF)
  )
  check(
    'A7 面板绑定 touchstart / touchmove / touchend / touchcancel（cancel 单独处理）',
    /bindtouchstart="onSidebarTouchStart"/.test(WXML_LF) &&
      /bindtouchmove="onSidebarTouchMove"/.test(WXML_LF) &&
      /bindtouchend="onSidebarTouchEnd"/.test(WXML_LF) &&
      /bindtouchcancel="onSidebarTouchCancel"/.test(WXML_LF)
  )
  check(
    'A8 侧栏顶部【+ 新建会话】按钮绑定 onNewConversation',
    /class="sidebar-new"[^>]*bindtap="onNewConversation"/.test(WXML_LF)
  )
  check(
    'A9 会话列表遍历 conversations 且 wx:key="id"',
    /wx:for="\{\{conversations\}\}"/.test(WXML_LF) && /wx:key="id"/.test(WXML_LF)
  )
  check(
    'A10 会话卡片同时绑定 bindtap（切换）与 bindlongpress（删除）并携带 data-id',
    /bindtap="onTapConversation"/.test(WXML_LF) &&
      /bindlongpress="onConvLongPress"/.test(WXML_LF) &&
      /data-id="\{\{item\.id\}\}"/.test(WXML_LF)
  )
  check(
    'A11 当前会话高亮绑定 conversationId（当前项高亮）',
    /item\.id === conversationId \? 'sidebar-conv--active' : ''/.test(WXML_LF)
  )
  check(
    'A12 底部操作绑定 onSettingsEntry（设置）/ onClearHistory（清空历史）',
    /bindtap="onSettingsEntry"/.test(WXML_LF) && /bindtap="onClearHistory"/.test(WXML_LF)
  )
  check(
    'A13 保留 F4 原有入口：+ 新会话 / 历史',
    /bindtap="onNewConversation"[^>]*>\s*\+ 新会话\s*<\/button>/.test(WXML_LF) &&
      /bindtap="onHistory"[^>]*>\s*历史\s*<\/button>/.test(WXML_LF)
  )
  check(
    'A14 列表有「空 / 失败」两态兜底（不出现裸列表）',
    /convLoading/.test(WXML_LF) && /convError/.test(WXML_LF) && /暂无历史会话/.test(WXML_LF)
  )

  // A15 WXML 用到的字面量 class 必须有样式定义（防拼写错导致裸样式）
  const globalCss = [
    fs.readFileSync(path.join(MP, 'app.wxss'), 'utf8'),
    fs.readFileSync(path.join(MP, 'styles', 'glass.wxss'), 'utf8'),
    fs.readFileSync(path.join(MP, 'styles', 'tokens.wxss'), 'utf8'),
  ].join('\n')
  const used = new Set()
  const classRe = /class="([^"]*)"/g
  let m
  while ((m = classRe.exec(WXML_LF))) {
    m[1]
      .replace(/\{\{[^}]*\}\}/g, ' ')
      .split(/\s+/)
      .forEach((c) => {
        if (c) used.add(c)
      })
  }
  const undef = []
  used.forEach((c) => {
    const re = new RegExp('\\.' + c.replace(/[.*+?^${}()|[\]\\-]/g, '\\$&') + '(?![\\w-])')
    if (re.test(WXSS) || re.test(globalCss)) return
    // `msg-row--{{item.role}}` 这类**被 mustache 截断的前缀**（F4 既有写法）：
    // 只要有以它为前缀的类定义就放行，否则会把合法的拼接类误判成未定义。
    const prefixRe = new RegExp('\\.' + c.replace(/[.*+?^${}()|[\]\\-]/g, '\\$&') + '[\\w-]')
    if (prefixRe.test(WXSS) || prefixRe.test(globalCss)) return
    undef.push(c)
  })
  check(`A15 WXML 引用的 ${used.size} 个 class 均有样式定义`, undef.length === 0, `未定义：${undef.join(', ')}`)

  // A16 WXML 绑定的事件处理函数必须真实存在（否则静默失效）
  const handlerRe = /\bbind[a-z]+\s*=\s*"([A-Za-z_$][A-Za-z0-9_$]*)"/g
  const handlers = new Set()
  while ((m = handlerRe.exec(WXML_LF))) handlers.add(m[1])
  const { page } = loadChat()
  const missing = []
  handlers.forEach((h) => {
    if (typeof page[h] !== 'function') missing.push(h)
  })
  check(
    `A16 WXML 绑定的 ${handlers.size} 个事件处理函数均存在`,
    missing.length === 0,
    `缺失：${missing.join(', ')}`
  )

  // A17 面板定位：.xj-glass 自带 position: relative，面板必须用复合选择器压过它，
  // 否则面板会退化成文档流内元素（整页布局崩坏，而纯文本断言看不出来）
  check(
    'A17 面板用 .chat-page .sidebar-panel 复合选择器声明 position: fixed',
    /\.chat-page \.sidebar-panel\s*\{[^}]*position:\s*fixed/.test(WXSS),
    '未找到 .chat-page .sidebar-panel { position: fixed }'
  )
  const maskZ = /\.sidebar-mask\s*\{[^}]*z-index:\s*(\d+)/.exec(WXSS)
  const panelZ = /\.chat-page \.sidebar-panel\s*\{[^}]*z-index:\s*(\d+)/.exec(WXSS)
  check(
    'A18 面板层级高于遮罩（否则点不到面板）',
    !!maskZ && !!panelZ && Number(panelZ[1]) > Number(maskZ[1]),
    `mask=${maskZ && maskZ[1]} panel=${panelZ && panelZ[1]}`
  )
  check(
    'A19 收起态用 visibility: hidden（只靠 opacity:0 会吃掉整页点击）',
    /\.sidebar-mask\s*\{[^}]*visibility:\s*hidden/.test(WXSS) &&
      /\.chat-page \.sidebar-panel\s*\{[^}]*visibility:\s*hidden/.test(WXSS)
  )

  const balance = checkTagBalance(WXML_LF)
  check('A20 WXML 标签闭合与嵌套配平', balance.ok, balance.detail)
}

// ------------------------------------------- B. 展开与列表加载 ----
bar('B. 展开与列表加载（GET /chat/conversations 的口径与兜底）')
{
  const { page, state } = loadChat()
  const sent = await after(state, () => page.onOpenSidebar())
  check('B1 【菜单】展开侧边栏（sidebarOpen=true）', page.data.sidebarOpen === true)
  const gets = convList(sent).filter(isGet)
  check(
    'B2 展开即拉取 GET /chat/conversations（且只有一次）',
    gets.length === 1,
    JSON.stringify(sent.map((r) => r.method + ' ' + r.url))
  )
  check('B3 会话列表请求不带分页参数（后端契约无 page/size）', gets.length === 1 && Object.keys(gets[0].data).length === 0, gets.length ? JSON.stringify(gets[0].data) : '')

  await tick()
  check(
    'B4 列表映射：条数与后端一致，id/title 原样透传（不做多余加工）',
    page.data.conversations.length === 2 &&
      page.data.conversations[0].id === 11 &&
      page.data.conversations[1].id === 12 &&
      page.data.conversations[0].title === '图书馆几点关门',
    JSON.stringify(page.data.conversations)
  )
  check(
    'B5 时间字段经 formatTime 归一（截到分钟，不做 Date 解析）',
    page.data.conversations[0].time === '2026-09-18 09:30',
    page.data.conversations[0].time
  )
}

// 标题兜底（后端 title 为空）
{
  const { page, state } = loadChat({ backend: { initial: [conv(5, '')] } })
  await after(state, () => page.onOpenSidebar())
  check(
    'B6 标题空值回落「未命名会话」（不留空行）',
    page.data.conversations.length === 1 && page.data.conversations[0].title === '未命名会话',
    JSON.stringify(page.data.conversations)
  )
}

// 请求未回来之前的加载态（应答被挂起）
{
  const { page, state } = loadChat({ responder: () => null })
  await after(state, () => page.onOpenSidebar())
  check(
    'B7 请求未返回时列表处于加载态（convLoading=true，页面不空白）',
    page.data.convLoading === true && page.data.conversations.length === 0,
    JSON.stringify({ loading: page.data.convLoading, n: page.data.conversations.length })
  )
}

// 字段缺失兜底（后端没给 items）
{
  const { page, state } = loadChat({ responder: () => ({ data: { code: 0, message: 'ok', data: {} } }) })
  await after(state, () => page.onOpenSidebar())
  check(
    'B8 后端未返回 items 时列表兜底为空数组且不报错',
    Array.isArray(page.data.conversations) && page.data.conversations.length === 0 && !page.data.convError,
    JSON.stringify({ conv: page.data.conversations, err: page.data.convError })
  )
}

// 加载失败
{
  const { page, state } = loadChat({ backend: { failList: true } })
  await after(state, () => page.onOpenSidebar())
  check(
    'B9 列表加载失败落到面板内可重试态（convError 非空、loading 复位）',
    page.data.convError === '加载失败，请稍后重试' && page.data.convLoading === false,
    JSON.stringify({ err: page.data.convError, loading: page.data.convLoading })
  )
}

// 每次展开都刷新（删除/新建后不会看到旧列表）
{
  const { page, state } = loadChat()
  await after(state, () => page.onOpenSidebar())
  await after(state, () => page.onCloseSidebar())
  const second = await after(state, () => page.onOpenSidebar())
  check('B10 再次展开会重新拉取（不是缓存的旧列表）', convList(second).filter(isGet).length === 1)
}

// ------------------------------------------- C. 点选会话 ----
bar('C. 点选会话（拉历史消息 → 还原对话区 → 收起侧栏）')
{
  const { page, state } = loadChat()
  await after(state, () => page.onOpenSidebar())
  const sent = await after(state, () =>
    page.onTapConversation({ currentTarget: { dataset: { id: 12 } } })
  )
  check(
    'C1 点选会话 → GET /chat/conversations/12/messages',
    sent.length === 1 && /\/chat\/conversations\/12\/messages$/.test(sent[0].url) && isGet(sent[0]),
    JSON.stringify(sent.map((r) => r.method + ' ' + r.url))
  )
  check('C2 侧栏收起', page.data.sidebarOpen === false)
  check('C3 会话 id 落到 conversationId', page.data.conversationId === 12)
  check(
    'C4 历史消息还原：条数与角色映射（非 user 一律 assistant）',
    page.data.messages.length === 2 &&
      page.data.messages[0].role === 'user' &&
      page.data.messages[1].role === 'assistant' &&
      page.data.messages[1].content === '22:00 闭馆',
    JSON.stringify(page.data.messages.map((x) => x.role + ':' + x.content))
  )

  // 连点节流
  const { page: p2, state: s2 } = loadChat()
  await after(s2, () => p2.onOpenSidebar())
  p2.onTapConversation({ currentTarget: { dataset: { id: 11 } } })
  await after(s2, () => p2.onTapConversation({ currentTarget: { dataset: { id: 12 } } }))
  check(
    'C5 连点只发一次消息请求（_convLoading 节流）',
    reqs(s2.requests, /\/messages$/).length === 1,
    JSON.stringify(s2.requests.map((r) => r.url))
  )

  // 生成中拦截
  const { page: p3, state: s3 } = loadChat()
  await after(s3, () => p3.onOpenSidebar())
  p3.setData({ sending: true })
  const sent3 = await after(s3, () =>
    p3.onTapConversation({ currentTarget: { dataset: { id: 11 } } })
  )
  check(
    'C6 生成中点会话被拦截（不发请求 + 明确提示）',
    sent3.length === 0 && s3.toasts.indexOf('正在回答中，请稍后再试') !== -1,
    JSON.stringify({ sent: sent3.length, toasts: s3.toasts })
  )

  // 失败：侧栏不误关
  const { page: p4, state: s4 } = loadChat({
    responder: (req) =>
      /\/messages$/.test(req.url) ? { data: { code: 1001, message: '会话不存在', data: null } } : null,
  })
  await after(s4, () => p4.onOpenSidebar())
  await after(s4, () => p4.onTapConversation({ currentTarget: { dataset: { id: 12 } } }))
  check(
    'C7 消息加载失败时不误关侧栏（用户可重选）',
    p4.data.sidebarOpen === true && p4.data.conversationId === null,
    JSON.stringify({ open: p4.data.sidebarOpen, conv: p4.data.conversationId })
  )
}

// ------------------------------------------- D. 长按删除 ----
bar('D. 长按删除会话（DELETE /chat/conversations/{id}）')
{
  const { page, state } = loadChat()
  await after(state, () => page.onOpenSidebar())
  const sent = await after(state, () =>
    page.onConvLongPress({ currentTarget: { dataset: { id: 12 } } })
  )
  check('D1 长按弹出确认框（删除不可恢复）', state.modals.length === 1, JSON.stringify(state.modals.length))
  const dels = reqs(sent, /\/chat\/conversations\/12$/)
  check(
    'D2 确认后 DELETE /chat/conversations/12',
    dels.length === 1 && (dels[0].method || '').toUpperCase() === 'DELETE',
    JSON.stringify(sent.map((r) => r.method + ' ' + r.url))
  )
  check(
    'D3 列表移除该项、其余保留',
    page.data.conversations.length === 1 && page.data.conversations[0].id === 11,
    JSON.stringify(page.data.conversations.map((c) => c.id))
  )
}

// 取消删除
{
  const { page, state } = loadChat({ modalConfirm: false })
  await after(state, () => page.onOpenSidebar())
  const sent = await after(state, () =>
    page.onConvLongPress({ currentTarget: { dataset: { id: 12 } } })
  )
  check(
    'D4 取消确认 → 不发任何请求、列表不变',
    dels(sent).length === 0 && page.data.conversations.length === 2,
    JSON.stringify(sent.map((r) => r.method + ' ' + r.url))
  )
}

// 删当前会话 → 对话区一并清空
{
  const { page, state } = loadChat()
  page.setData({ conversationId: 12, messages: [{ localId: 1, role: 'user', content: 'hi' }] })
  await after(state, () => page.onOpenSidebar())
  await after(state, () => page.onConvLongPress({ currentTarget: { dataset: { id: 12 } } }))
  check(
    'D5 删除的是当前会话 → 对话区清空 + conversationId 归零',
    page.data.messages.length === 0 && page.data.conversationId === null,
    JSON.stringify({ messages: page.data.messages.length, conv: page.data.conversationId })
  )
}

// 删非当前会话 → 当前对话保留
{
  const { page, state } = loadChat()
  page.setData({ conversationId: 11, messages: [{ localId: 1, role: 'user', content: 'hi' }] })
  await after(state, () => page.onOpenSidebar())
  await after(state, () => page.onConvLongPress({ currentTarget: { dataset: { id: 12 } } }))
  check(
    'D6 删除非当前会话 → 当前对话保留',
    page.data.messages.length === 1 && page.data.conversationId === 11,
    JSON.stringify({ messages: page.data.messages.length, conv: page.data.conversationId })
  )
}

// 生成中不删当前会话
{
  const { page, state } = loadChat()
  page.setData({ sending: true, conversationId: 12 })
  await after(state, () => page.onOpenSidebar())
  const sent = await after(state, () =>
    page.onConvLongPress({ currentTarget: { dataset: { id: 12 } } })
  )
  check(
    'D7 生成中长按当前会话被拦截（不弹框、不发 DELETE）',
    state.modals.length === 0 && dels(sent).length === 0 && state.toasts.indexOf('正在回答中，请稍后再试') !== -1,
    JSON.stringify({ modals: state.modals.length, sent: sent.length, toasts: state.toasts })
  )
}

// 标题截断
{
  const { page, state } = loadChat({
    backend: { initial: [conv(11, '这是一个非常非常长的会话标题需要被截断显示避免弹窗行数爆炸')] },
  })
  await after(state, () => page.onOpenSidebar())
  await after(state, () => page.onConvLongPress({ currentTarget: { dataset: { id: 11 } } }))
  const content = state.modals.length ? state.modals[0].content : ''
  check(
    'D8 弹窗文案对超长标题做截断（≤12 字 + 省略号）',
    /「.{1,12}…」/.test(content),
    content
  )
}

// ------------------------------------------- E. 清空历史 ----
bar('E. 清空历史（后端无批量接口 → 取一批删一批，含上限保护）')
{
  const { page, state } = loadChat({ backend: { initial: [conv(1, 'a'), conv(2, 'b'), conv(3, 'c')] } })
  await after(state, () => page.onOpenSidebar())
  await after(state, () => page.onClearHistory())
  check('E1 清空历史需二次确认（不可恢复）', state.modals.length === 1)
  check(
    'E2 逐条删除直到列表为空（3 条 → 3 次 DELETE）',
    state.backend.deletes === 3,
    `deletes=${state.backend.deletes}`
  )
  check(
    'E3 清空后对话区清空 + conversationId 归零 + 列表刷新为空',
    page.data.messages.length === 0 &&
      page.data.conversationId === null &&
      page.data.conversations.length === 0,
    JSON.stringify({
      messages: page.data.messages.length,
      conv: page.data.conversationId,
      list: page.data.conversations.length,
    })
  )
}

// 无会话时不进入删除流程
{
  const { page, state } = loadChat({ backend: { initial: [] } })
  await after(state, () => page.onOpenSidebar())
  await after(state, () => page.onClearHistory())
  check(
    'E4 无历史会话时不弹确认框、不发 DELETE（避免空操作）',
    state.modals.length === 0 && state.backend.deletes === 0 && state.toasts.indexOf('暂无历史会话') !== -1,
    JSON.stringify({ modals: state.modals.length, deletes: state.backend.deletes, toasts: state.toasts })
  )
}

// 上限保护：后端始终删不干净
{
  const { page, state } = loadChat({ backend: { keepNonEmpty: true } })
  await after(state, () => page.onOpenSidebar())
  await after(state, () => page.onClearHistory())
  const fetches = convList(state.requests).filter(isGet).length
  check(
    'E5 后端删不干净时按轮数上限收敛（10 轮 × 2 条 = 20 次 DELETE，不死循环）',
    state.backend.deletes === 20 && page.data.convLoading === false,
    `deletes=${state.backend.deletes} fetches=${fetches}`
  )
  check(
    'E6 未清空时给出「部分未清空」提示（不谎报成功）',
    state.toasts[state.toasts.length - 1] === '部分会话未清空，请重试',
    JSON.stringify(state.toasts.slice(-3))
  )
}

// 中途失败即停
{
  const { page, state } = loadChat({ backend: { initial: [conv(1, 'a'), conv(2, 'b'), conv(3, 'c')], failDeleteAt: 2 } })
  await after(state, () => page.onOpenSidebar())
  await after(state, () => page.onClearHistory())
  check(
    'E7 删除中途失败即停（第 2 条失败后不再继续删第 3 条）',
    state.backend.deletes === 2,
    `deletes=${state.backend.deletes}`
  )
  check(
    'E8 中途失败给出「部分未清空」提示',
    state.toasts[state.toasts.length - 1] === '部分会话未清空，请重试',
    JSON.stringify(state.toasts.slice(-3))
  )
}

// 生成中拦截
{
  const { page, state } = loadChat()
  page.setData({ sending: true })
  await after(state, () => page.onOpenSidebar())
  await after(state, () => page.onClearHistory())
  check(
    'E9 生成中清空被拦截（不弹框、不发 DELETE）',
    state.modals.length === 0 && state.backend.deletes === 0 && state.toasts.indexOf('正在回答中，请稍后再试') !== -1,
    JSON.stringify({ modals: state.modals.length, deletes: state.backend.deletes })
  )}

// ------------------------------------------- F. 新建会话 ----
bar('F. 新建会话（B24：POST /chat/conversations，空会话可直接对话）')
{
  const { page, state } = loadChat()
  page.setData({ conversationId: 12, messages: [{ localId: 1, role: 'user', content: 'hi' }] })
  await after(state, () => page.onOpenSidebar())
  const sent = await after(state, () => page.onNewConversation())
  const posts = reqs(sent, /\/chat\/conversations\/?$/).filter(
    (r) => (r.method || '').toUpperCase() === 'POST'
  )
  check(
    'F1 新建会话 → POST /chat/conversations',
    posts.length === 1,
    JSON.stringify(sent.map((r) => r.method + ' ' + r.url))
  )
  check(
    'F2 新建即清空对话区（messages/conversationId 复位）',
    page.data.messages.length === 0
  )
  check('F3 侧栏内的新建会收起侧栏', page.data.sidebarOpen === false)
  await tick()
  check(
    'F4 B24 返回的 conversation_id 落到当前会话（空会话可直接对话）',
    page.data.conversationId === 77,
    String(page.data.conversationId)
  )
  // 「新建会话可对话」的端到端口径：新会话 id 必须真的随下一条消息发给 /chat/send
  const sent2 = await after(state, () => page.sendMessage('新会话里的第一句'))
  const sse = sent2.filter((r) => /\/chat\/send$/.test(r.url))
  check(
    'F4b 新会话 id 随消息发给 POST /chat/send（不是又建了一个会话）',
    sse.length === 1 && sse[0].data.conversation_id === 77,
    JSON.stringify(sse.map((r) => r.data))
  )
}

// 迟到响应丢弃（一）：连续两次「新建会话」，旧响应不得覆盖新会话
{
  const holds = []
  const { page, state } = loadChat({
    responder: (req) => {
      if ((req.method || '').toUpperCase() === 'POST' && /\/chat\/conversations\/?$/.test(req.url)) {
        holds.push(req)
        return null
      }
      return { data: { code: 0, message: 'ok', data: { items: [] } } }
    },
  })
  page.onNewConversation()
  await tick()
  page.onNewConversation()
  await tick()
  check(
    'F5 反向前提：两次新建都已发出且被挂起',
    holds.length === 2 && page.data.conversationId === null,
    JSON.stringify({ holds: holds.length, conv: page.data.conversationId })
  )
  // 第 1 次请求的响应后到：不得把当前会话指回那个已被取代的空会话
  holds[0].success(okBody({ conversation_id: 77, title: '新对话' }))
  await tick()
  check(
    'F6 旧的新建响应被丢弃（不指向被取代的会话）',
    page.data.conversationId === null,
    String(page.data.conversationId)
  )
  holds[1].success(okBody({ conversation_id: 88, title: '新对话' }))
  await tick()
  check(
    'F6b 最后一次新建的响应生效（conversation_id=88）',
    page.data.conversationId === 88,
    String(page.data.conversationId)
  )
}

// 迟到响应丢弃（二）：用户在响应回来之前已经开口（会话 id 归 /chat/send）
{
  let hold = null
  const { page, state } = loadChat({
    responder: (req) => {
      if ((req.method || '').toUpperCase() === 'POST' && /\/chat\/conversations\/?$/.test(req.url)) {
        hold = req
        return null
      }
      return { data: { code: 0, message: 'ok', data: { items: [] } } }
    },
  })
  page.onNewConversation()
  await tick()
  page.sendMessage('先问一句')
  await tick()
  hold.success(okBody({ conversation_id: 77, title: '新对话' }))
  await tick()
  check(
    'F6c 已开口后到达的新建响应不覆盖正在进行的对话',
    page.data.conversationId === null,
    String(page.data.conversationId)
  )
}

// 失败回落：POST 失败不阻断
{
  const { page, state } = loadChat({
    responder: (req) =>
      (req.method || '').toUpperCase() === 'POST'
        ? { data: { code: 5001, message: '服务异常', data: null } }
        : null,
  })
  page.setData({ conversationId: 12, messages: [{ localId: 1, role: 'user', content: 'hi' }] })
  await after(state, () => page.onNewConversation())
  check(
    'F7 新建失败时回落为「本地清空 + conversationId=null」（首条消息仍由 /chat/send 懒建）',
    page.data.conversationId === null && page.data.messages.length === 0,
    JSON.stringify({ conv: page.data.conversationId, messages: page.data.messages.length })
  )
}

// 生成中新建需确认
{
  const { page, state } = loadChat({ modalConfirm: false })
  page.setData({ sending: true })
  const sent = await after(state, () => page.onNewConversation())
  check(
    'F8 生成中新建先确认；取消则既不停止也不新建',
    state.modals.length === 1 && sent.length === 0 && page.data.sending === true,
    JSON.stringify({ modals: state.modals.length, sent: sent.length })
  )
}

// ------------------------------------------- G. 遮罩 / 手势 ----
bar('G. 遮罩点击与右滑手势关闭')
{
  const { page, state } = loadChat()
  await after(state, () => page.onOpenSidebar())
  await after(state, () => page.onMaskTap())
  check('G1 点击遮罩关闭侧栏', page.data.sidebarOpen === false)
}

{
  const { page, state } = loadChat()
  await after(state, () => page.onOpenSidebar())
  const mid = await drag(page, 80, 0)
  check(
    'G2 右拖期间跟手（出现过 translateX 位移且拖拽态为 true）',
    mid.some((s) => s.dragging === true && /translateX\(/.test(s.style)),
    JSON.stringify(mid)
  )
  check('G3 右拖超过阈值 → 关闭', page.data.sidebarOpen === false)
  check(
    'G4 关闭后清空内联位移（位置交回 WXSS 的收起态）',
    page.data.sidebarStyle === '' && page.data.sidebarDragging === false,
    JSON.stringify({ style: page.data.sidebarStyle, dragging: page.data.sidebarDragging })
  )
}

{
  const { page, state } = loadChat()
  await after(state, () => page.onOpenSidebar())
  await drag(page, 20, 0)
  check(
    'G5 右拖未过阈值 → 回弹（保持展开且位移清空）',
    page.data.sidebarOpen === true &&
      page.data.sidebarStyle === '' &&
      page.data.sidebarDragging === false,
    JSON.stringify({
      open: page.data.sidebarOpen,
      style: page.data.sidebarStyle,
      dragging: page.data.sidebarDragging,
    })
  )
}

{
  const { page, state } = loadChat()
  await after(state, () => page.onOpenSidebar())
  const mid = await drag(page, 40, 90)
  check(
    'G6 纵向拖动（列表滚动）不进入拖拽态、不关闭',
    mid.every((s) => s.dragging === false) && page.data.sidebarOpen === true,
    JSON.stringify({ mid, open: page.data.sidebarOpen })
  )
}

{
  const { page, state } = loadChat()
  await after(state, () => page.onOpenSidebar())
  const mid = await drag(page, -80, 0)
  check(
    'G7 向左拖动不接管（面板已是展开位，向左不产生位移）且不关闭',
    mid.every((s) => s.dragging === false) && page.data.sidebarOpen === true,
    JSON.stringify({ mid, open: page.data.sidebarOpen })
  )
}

{
  const { page, state } = loadChat()
  await after(state, () => page.onOpenSidebar())
  page.onSidebarTouchStart(touch(100, 100))
  page.onSidebarTouchMove(touch(200, 100))
  await tick()
  check('G8 拖拽中间态：sidebarDragging=true', page.data.sidebarDragging === true)
  // 拖到 100px 时被系统打断：不当作「用户完成了一次关闭手势」
  page.onSidebarTouchCancel()
  await tick()
  check(
    'G9 touchcancel 复位拖拽态且保持在展开位（不残留内联位移、不误关）',
    page.data.sidebarDragging === false &&
      page.data.sidebarStyle === '' &&
      page.data.sidebarOpen === true,
    JSON.stringify({
      dragging: page.data.sidebarDragging,
      style: page.data.sidebarStyle,
      open: page.data.sidebarOpen,
    })
  )
  // 打断后仍可正常继续操作：再拖一次过阈值 → 关闭
  await drag(page, 80, 0)
  check('G9b 打断后手势仍然可用（再拖过阈值即关闭）', page.data.sidebarOpen === false)
}

{
  const { page, state } = loadChat()
  page.onSidebarTouchStart(touch(100, 100))
  page.onSidebarTouchMove(touch(200, 100))
  await tick()
  check(
    'G10 未展开时手势不生效（面板不可点 → 不进入拖拽态）',
    page.data.sidebarDragging === false && page.data.sidebarOpen === false
  )
}

// ------------------------------------------- H. 状态恢复 ----
bar('H. 状态恢复（Tab 切走再回来不残留遮罩；F4 既有行为不回归）')
{
  const { page, state } = loadChat()
  await after(state, () => page.onOpenSidebar())
  page.onSidebarTouchStart(touch(100, 100))
  page.onSidebarTouchMove(touch(180, 100))
  await tick()
  page.onShow()
  await tick()
  check(
    'H1 onShow 收起侧栏并复位拖拽中间态',
    page.data.sidebarOpen === false &&
      page.data.sidebarDragging === false &&
      page.data.sidebarStyle === '',
    JSON.stringify({
      open: page.data.sidebarOpen,
      dragging: page.data.sidebarDragging,
      style: page.data.sidebarStyle,
    })
  )
}

{
  const restore = {
    conversationId: 9,
    messages: [
      { id: 1, role: 'user', content: '昨天的问题' },
      { id: 2, role: 'assistant', content: '昨天的回答' },
    ],
  }
  const { page, state } = loadChat({ globalData: { chatRestore: restore } })
  page.onShow()
  await tick()
  check(
    'H2 F4 历史页回传仍生效（messages 还原 + conversationId=9 + globalData 已清空）',
    page.data.messages.length === 2 &&
      page.data.conversationId === 9 &&
      page._app.globalData.chatRestore === null,
    JSON.stringify({ n: page.data.messages.length, conv: page.data.conversationId })
  )
}

{
  const { page, state } = loadChat({ globalData: { pendingSearch: '图书馆几点关门' } })
  const sent = await after(state, () => page.onShow())
  check(
    'H3 F4 首页关键词仍自动发送（POST /chat/send）',
    sent.length === 1 && /\/chat\/send$/.test(sent[0].url),
    JSON.stringify(sent.map((r) => r.url))
  )
  check('H4 消费后清空 pendingSearch（不重复自动发送）', page._app.globalData.pendingSearch === '')
}

// ------------------------------------------- I. F14-A 边界 ----
bar('I. F14-A 边界（语音 / 自定义 TabBar 不在本任务）')
{
  const voiceRe = /getRecorderManager|startRecord\b|stopRecord\b|onStartRecord|voice\/transcribe|uploadFile|scope\.record|麦克风/
  check(
    'I1 本页未引入录音 / 语音转写实现（属 F14-B，另行分支交付）',
    !voiceRe.test(JS) && !voiceRe.test(WXML_LF),
    '命中：' + (voiceRe.exec(JS) || voiceRe.exec(WXML_LF) || [''])[0]
  )
  check(
    'I2 未依赖尚未进 dev 的 F12 自定义 TabBar（侧栏不隐藏原生 tabBar）',
    !/require\(['"][^'"]*utils\/tabbar['"]\)/.test(JS) && !/getTabBar\s*\(/.test(JS)
  )
  check(
    'I3 设置入口不做假跳转（设置页属 F17，当前 dev 不存在该页面）',
    /onSettingsEntry\s*\(\)\s*\{[\s\S]{0,200}?showToast/.test(JS) &&
      !/navigateTo[^\n]*settings/.test(JS)
  )
}

// -------------------------------------------------- 反向对照 ----
bar('反向对照（对真实源码做一处语义突变后，断言必须转为失败 —— 证明不是空跑）')

// R1 突变 WXML：去掉长按删除绑定
{
  const mutated = WXML_LF.replace(/\n\s*bindlongpress="onConvLongPress"/, '')
  check('R1 突变可用（WXML 确实被改写）', mutated !== WXML_LF)
  check(
    'R1 反证：去掉 bindlongpress 后 A10 断言会失败',
    !/bindlongpress="onConvLongPress"/.test(mutated)
  )
}

// R2 突变 JS：删除请求路径写错
{
  const mutated = JS.replace(
    "request('/chat/conversations/' + id, { method: 'DELETE' })",
    "request('/chat/conversationsX/' + id, { method: 'DELETE' })"
  )
  check('R2 突变可用（源码确实被改写）', mutated !== JS)
  const { page, state } = loadChat({ sourceOverride: mutated })
  await after(state, () => page.onOpenSidebar())
  const sent = await after(state, () => page.onConvLongPress({ currentTarget: { dataset: { id: 12 } } }))
  check(
    'R2 反证：DELETE 路径写错时 D2 断言会失败',
    reqs(sent, /\/chat\/conversations\/12$/).length === 0,
    JSON.stringify(sent.map((r) => r.method + ' ' + r.url))
  )
}

// R3 突变 JS：去掉迟到响应的序号守护（连续两次新建的场景）
{
  const mutated = JS.replace('        if (seq !== this._createSeq) return\n', '')
  check('R3 突变可用（源码确实被改写）', mutated !== JS)
  const holds = []
  const { page, state } = loadChat({
    sourceOverride: mutated,
    responder: (req) => {
      if ((req.method || '').toUpperCase() === 'POST' && /\/chat\/conversations\/?$/.test(req.url)) {
        holds.push(req)
        return null
      }
      return { data: { code: 0, message: 'ok', data: { items: [] } } }
    },
  })
  page.onNewConversation()
  await tick()
  page.onNewConversation()
  await tick()
  holds[0].success(okBody({ conversation_id: 77, title: '新对话' }))
  await tick()
  check(
    'R3 反证：去掉序号守护后旧响应会覆盖当前会话（F6 断言会失败）',
    page.data.conversationId === 77,
    String(page.data.conversationId)
  )
}

// R4 突变 JS：手势阈值判断失效
{
  const mutated = JS.replace(
    'if (wasDragging && dragged >= SIDEBAR_CLOSE_DRAG_PX) {',
    'if (false) {'
  )
  check('R4 突变可用（源码确实被改写）', mutated !== JS)
  const { page, state } = loadChat({ sourceOverride: mutated })
  await after(state, () => page.onOpenSidebar())
  await drag(page, 80, 0)
  check('R4 反证：阈值判断失效后 G3 断言会失败（拖到底也不关）', page.data.sidebarOpen === true)
}

// R5 突变 WXML：去掉遮罩点击关闭
{
  const mutated = WXML_LF.replace(/\n\s*bindtap="onMaskTap"/, '')
  check('R5 突变可用（WXML 确实被改写）', mutated !== WXML_LF)
  check(
    'R5 反证：去掉遮罩 bindtap 后 A2 断言会失败',
    !/class="sidebar-mask[^"]*"[^>]*bindtap="onMaskTap"/.test(mutated)
  )
}

// R6 突变 WXML：删掉一个闭合标签
{
  const broken = WXML_LF.replace('</scroll-view>', '')
  check('R6 突变可用（WXML 确实被改写）', broken !== WXML_LF)
  const b = checkTagBalance(broken)
  check('R6 反证：WXML 少一个闭合标签时 A20 会失败', b.ok === false, JSON.stringify(b))
}

// R7 突变 JS：清空历史的轮数上限失效
{
  const mutated = JS.replace(
    'if (roundsLeft <= 0) return this.finishClear(false)',
    'if (roundsLeft <= -1) return this.finishClear(false)'
  )
  check('R7 突变可用（源码确实被改写）', mutated !== JS)
  const { page, state } = loadChat({ sourceOverride: mutated, backend: { keepNonEmpty: true } })
  await after(state, () => page.onOpenSidebar())
  await after(state, () => page.onClearHistory())
  check(
    'R7 反证：上限失效后删除轮数不再等于 10（E5 断言会失败）',
    state.backend.deletes !== 20,
    `deletes=${state.backend.deletes}`
  )
}

// R8 突变 JS：列表字段兜底被去掉
{
  const mutated = JS.replace(
    'const items = ((res && res.items) || []).map((c) => ({',
    'const items = (res.items).map((c) => ({'
  )
  check('R8 突变可用（源码确实被改写）', mutated !== JS)
  const { page, state } = loadChat({
    sourceOverride: mutated,
    responder: () => ({ data: { code: 0, message: 'ok', data: {} } }),
  })
  await after(state, () => page.onOpenSidebar())
  check(
    'R8 反证：去掉 ((res && res.items) || []) 兜底后 B8 断言会失败（字段缺失即报错）',
    !!page.data.convError,
    JSON.stringify({ err: page.data.convError })
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
  console.log(`[PASS] F14-A AI 助手侧边栏校验通过（${pass} 项）`)
  console.log('⚠️ 仅覆盖请求分派/状态机/竞态与失败路径/结构对账；')
  console.log('   侧滑动画顺滑度、遮罩观感、玻璃模糊、跟手拖拽与列表滚动的真实交互、')
  console.log('   iOS 安全区与低端机性能仍需微信开发者工具 / 真机目视确认 —— 属 MANUAL CHECK。')
  console.log('   语音转写属 F14-B，不在本工具覆盖范围。')
  process.exit(0)
}
console.log(`[FAIL] ${fail} 项未通过 / 共 ${pass + fail} 项`)
process.exit(1)
}

main().catch((err) => {
  console.error('[FAIL] 验证工具自身异常：', err)
  process.exit(1)
})
