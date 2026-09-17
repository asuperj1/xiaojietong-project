#!/usr/bin/env node
/**
 * F16（论坛搜索 + 标签栏）行为验证工具
 *
 * 背景
 * ----
 * 需求 8 / F16：论坛页新增**搜索框**与**7 标签栏**
 * （全部 / 学习 / 生活 / 闲置 / 活动 / 热点 / 我的帖子），标签切换复用既有 forum
 * category 参数；关键词检索复用后端 B29 的 `GET /topics?keyword=&category=`。
 *
 * 本工具在 Node 里 stub `wx` / `Page` / `getApp`，直接加载**真实**的
 * `miniprogram/pages/forum/forum.js`（整目录沙箱拷贝，镜像其 require 图），
 * 驱动真实的标签/搜索事件处理函数，并断言**实际发出的请求**与页面状态。
 *
 * 覆盖
 * ----
 *   A. 标签栏口径：7 标签与需求逐字一致
 *   B. 标签分派：每个标签 → 正确的 path + category 参数
 *   C. 搜索分派：关键词 + 当前标签 → `/topics?keyword=&category=`
 *      （热点 / 我的帖子两栏后端无 keyword 参数 → 降级为全部检索并给出提示）
 *   D. 搜索交互：切标签提交待搜词、清空、重复确认去重、纯空白关键词
 *   E. 分页与到底：翻页保留标签与关键词；热点榜不翻页
 *   F. 我的帖子：审核状态映射；其它列表不渲染审核标签
 *   G. 失败路径：后端关键词检索失败关闭（5001）时展示后端原文
 *   H. 乱序守护：过期响应不得覆盖新标签的数据
 *   I. 玻璃降级：能力探测不支持时根节点挂 `.is-glass-fallback`
 *   J. 结构对账：WXML 引用的 class / 事件处理函数都真实存在
 *
 * ⚠️ 验证范围（不要外推）
 * ----------------------
 *   ✅ 覆盖：请求分派（path / 查询参数 / 状态机）、页面状态、失败与乱序路径、结构对账。
 *   ❌ 不覆盖：**视觉呈现**（搜索框与标签栏外观、玻璃模糊效果、发丝线、滚动吸顶、
 *      低端机性能）—— 需微信开发者工具 / 真机目视确认，属 MANUAL CHECK。
 *   ❌ 不覆盖：后端检索语义本身（FULLTEXT + ngram 命中与排序）—— 见
 *      `docs/api.md` §8 与 `backend/tests/test_topic_search.py`。
 *
 * 用法
 * ----
 *     node tools/verify_f16_forum_search.js [--src <repo-root>]
 *
 * 输出：`[OK]` / `[NG]` 逐项断言 + 末尾汇总；有失败则退出码 1。
 *
 * 口径说明：A~J 段是断言（决定退出码）；每个「能力」类断言都配**反向对照**
 * （对真实源码做一处语义突变后必须转为失败），用来证明断言不是空跑。
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
const FORUM_DIR = path.join(MP, 'pages', 'forum')
const FORUM_JS = path.join(FORUM_DIR, 'forum.js')
const FORUM_WXML = path.join(FORUM_DIR, 'forum.wxml')
const FORUM_WXSS = path.join(FORUM_DIR, 'forum.wxss')

for (const f of [FORUM_JS, FORUM_WXML, FORUM_WXSS]) {
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
 * 建隔离沙箱：整目录拷贝 `config` / `services` / `utils` / `pages/forum`。
 * 整目录（而不是逐文件列举）——forum.js 的 require 图后续任务仍可能变化，
 * 写死清单会把 MODULE_NOT_FOUND 这种假失败留给下一个任务。
 */
function makeSandbox() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'xjt-f16-'))
  sandboxes.push(dir)
  for (const sub of ['config', 'services', 'utils']) {
    const from = path.join(MP, sub)
    if (!fs.existsSync(from)) {
      throw new Error(`[sandbox] 缺少前提目录 miniprogram/${sub}`)
    }
    fs.cpSync(from, path.join(dir, sub), { recursive: true })
  }
  fs.cpSync(FORUM_DIR, path.join(dir, 'pages', 'forum'), { recursive: true })
  return dir
}

/** 造一页帖子数据；title 编码来源标签，便于分辨「哪次响应落了地」 */
function itemsFor(opts, n, extra) {
  const data = opts.data || {}
  const tag = data.category
    ? data.category
    : opts.url.indexOf('/hot') !== -1
      ? 'HOT'
      : opts.url.indexOf('/mine') !== -1
        ? 'MINE'
        : 'ALL'
  const out = []
  for (let i = 0; i < n; i++) {
    out.push(Object.assign({ id: i + 1, title: `${tag}-${i + 1}` }, extra || {}))
  }
  return out
}

/** 标准成功应答（分页信封） */
function okPaged(n) {
  return (opts) => ({
    data: {
      code: 0,
      message: 'ok',
      data: { items: itemsFor(opts, n), total: n, page: 1, size: 20 },
    },
  })
}

/**
 * 在沙箱里加载真实 forum.js，返回 { page, state }。
 * @param {object} [opts]
 * @param {boolean} [opts.glassSupported] getApp().globalData.glassSupported 取值
 * @param {Function} [opts.responder] (opts, 第几次请求) => 应答 | null（null = 挂起不应答）
 * @param {string} [opts.sourceOverride] 用给定源码替换 forum.js（反向对照用）
 */
function loadForum(opts) {
  const o = opts || {}
  const dir = makeSandbox()
  const file = path.join(dir, 'pages', 'forum', 'forum.js')
  if (o.sourceOverride) fs.writeFileSync(file, o.sourceOverride, 'utf8')

  const state = { requests: [], hanging: [] }
  const responder = o.responder || okPaged(3)

  global.wx = {
    getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
    getStorageSync: () => '',
    setStorageSync: () => {},
    removeStorageSync: () => {},
    showToast: () => {},
    showModal: () => {},
    reLaunch: () => {},
    navigateTo: () => {},
    stopPullDownRefresh: () => {},
    request: (req) => {
      state.requests.push({ url: req.url, data: req.data || {}, method: req.method || 'GET' })
      const res = responder(req, state.requests.length)
      if (res) req.success(res)
      else state.hanging.push(req)
    },
  }

  let cfg = null
  global.Page = (obj) => {
    cfg = obj
  }
  global.getApp = () => ({ globalData: { glassSupported: o.glassSupported !== false } })

  delete require.cache[require.resolve(file)]
  require(file)

  const page = {}
  Object.keys(cfg).forEach((k) => {
    page[k] = cfg[k]
  })
  page.data = JSON.parse(JSON.stringify(cfg.data))
  page.setData = function (patch, cb) {
    Object.keys(patch).forEach((k) => {
      this.data[k] = patch[k]
    })
    if (typeof cb === 'function') cb()
  }

  return { page, state }
}

const tick = () => new Promise((r) => setImmediate(r))

/** 触发一次动作，返回该动作期间新发出的请求（无则 null） */
async function after(state, fn) {
  const before = state.requests.length
  fn()
  await tick()
  return state.requests.length > before ? state.requests[state.requests.length - 1] : null
}

function tapTab(page, index) {
  page.onCatChange({ currentTarget: { dataset: { index } } })
}

async function search(page, state, keyword) {
  page.onSearchInput({ detail: { value: keyword } })
  return after(state, () => page.onSearchConfirm())
}

const isTopics = (r) => !!r && r.url.indexOf('/topics') !== -1 && r.url.indexOf('/topics/') === -1
const qs = (r) => r.data || {}

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
console.log(`被测文件：${FORUM_JS}`)

const REQUIRED_CATS = ['全部', '学习', '生活', '闲置', '活动', '热点', '我的帖子']

// ---------------------------------------------------- A. 标签栏口径 ----
bar('A. 标签栏口径（需求 8 / F16：7 标签逐字一致、顺序一致）')
{
  const { page } = loadForum()
  check(
    `data.cats 恰为 ${REQUIRED_CATS.join(' / ')}`,
    JSON.stringify(page.data.cats) === JSON.stringify(REQUIRED_CATS),
    `实际 ${JSON.stringify(page.data.cats)}`
  )
}

// -------------------------------------------------------- B. 标签分派 ----
bar('B. 标签分派（切换标签 → 正确的接口与 category 参数）')

const TAB_MATRIX = [
  { index: 0, label: '全部', path: '/topics', category: '', paged: true },
  { index: 1, label: '学习', path: '/topics', category: '学习', paged: true },
  { index: 2, label: '生活', path: '/topics', category: '生活', paged: true },
  { index: 3, label: '闲置', path: '/topics', category: '闲置', paged: true },
  { index: 4, label: '活动', path: '/topics', category: '活动', paged: true },
  // 热点榜：后端 GET /topics/hot 一次性返回（无 page/size，只有 limit 默认 20）
  { index: 5, label: '热点', path: '/topics/hot', category: null, paged: false },
  { index: 6, label: '我的帖子', path: '/topics/mine', category: null, paged: true },
]

/** 探针：切到 index 标签后实际发出的请求（反向对照复用同一探针） */
async function probeTab(index, sourceOverride) {
  const { page, state } = loadForum(sourceOverride ? { sourceOverride } : undefined)
  const req = await after(state, () => tapTab(page, index))
  return req
}

for (const t of TAB_MATRIX) {
  const req = await probeTab(t.index)
  check(`[${t.label}] → ${t.path}`, !!req && req.url.endsWith(t.path), req ? req.url : '未发出请求')
  if (t.category === null) {
    check(`[${t.label}] 不携带 category（后端该接口无此参数）`, !!req && !('category' in qs(req)))
  } else {
    check(
      `[${t.label}] category=${JSON.stringify(t.category)}`,
      !!req && qs(req).category === t.category,
      req ? JSON.stringify(qs(req)) : ''
    )
  }
  if (t.paged) {
    check(
      `[${t.label}] 首页请求 page=1 / size=20`,
      !!req && qs(req).page === 1 && qs(req).size === 20,
      req ? JSON.stringify(qs(req)) : ''
    )
  } else {
    // 一次性接口：不得带分页参数，否则会误导后端契约（见 docs/api.md §8 GET /topics/hot）
    check(
      `[${t.label}] 一次性接口不带 page/size`,
      !!req && !('page' in qs(req)) && !('size' in qs(req)),
      req ? JSON.stringify(qs(req)) : ''
    )
  }
}

// -------------------------------------------------------- C. 搜索分派 ----
bar('C. 搜索分派（关键词 + 当前标签 → GET /topics?keyword=&category=）')

const SEARCH_MATRIX = [
  { tab: 0, label: '全部', keyword: '图书馆', category: '', hint: false },
  { tab: 1, label: '学习', keyword: '高数资料', category: '学习', hint: false },
  { tab: 4, label: '活动', keyword: '迎新', category: '活动', hint: false },
  // 热点 / 我的帖子：后端接口不接受 keyword → 降级为全部检索 + 明确提示
  { tab: 5, label: '热点', keyword: '校车', category: '', hint: true },
  { tab: 6, label: '我的帖子', keyword: '校车', category: '', hint: true },
]

/** 探针：在 tab 下搜索 keyword 后实际发出的请求与页面提示 */
async function probeSearch(tab, keyword, sourceOverride) {
  const { page, state } = loadForum(sourceOverride ? { sourceOverride } : undefined)
  tapTab(page, tab)
  await tick()
  const req = await search(page, state, keyword)
  return { req, data: page.data }
}

for (const s of SEARCH_MATRIX) {
  const { req, data } = await probeSearch(s.tab, s.keyword)
  check(
    `[${s.label}] 搜索「${s.keyword}」→ /topics?keyword=&category=`,
    isTopics(req) && qs(req).keyword === s.keyword && qs(req).category === s.category,
    req ? `${req.url} ${JSON.stringify(qs(req))}` : '未发出请求'
  )
  check(
    `[${s.label}] 搜索态提示${s.hint ? '（说明降级口径）' : '（分类内检索无需提示）'}`,
    s.hint ? typeof data.searchHint === 'string' && data.searchHint.length > 0 : data.searchHint === '',
    `searchHint=${JSON.stringify(data.searchHint)}`
  )
  check(`[${s.label}] 已提交关键词落到 searchText`, data.searchText === s.keyword, data.searchText)
}

// ---------------------------------------------------- D. 搜索交互 ----
bar('D. 搜索交互（切标签提交待搜词、清空、去重、纯空白）')
{
  // D1 切标签时把「已输入未回车」的关键词一并提交，避免输入框与列表口径不一致
  const { page, state } = loadForum()
  tapTab(page, 0)
  await tick()
  page.onSearchInput({ detail: { value: ' 高数 ' } })
  const req = await after(state, () => tapTab(page, 1))
  check(
    'D1 切标签提交待搜词（去首尾空白）并在新标签内检索',
    isTopics(req) && qs(req).keyword === '高数' && qs(req).category === '学习',
    req ? `${req.url} ${JSON.stringify(qs(req))}` : '未发出请求'
  )
}
{
  // D2 清空搜索 → 回到普通列表（不带 keyword）
  const { page, state } = loadForum()
  tapTab(page, 1)
  await tick()
  await search(page, state, '高数')
  const req = await after(state, () => page.onSearchClear())
  check(
    'D2 清空搜索 → 回到普通列表（无 keyword、保留当前 category）',
    isTopics(req) && qs(req).keyword === undefined && qs(req).category === '学习',
    req ? `${req.url} ${JSON.stringify(qs(req))}` : '未发出请求'
  )
  check('D2 清空后 keyword / searchText 均为空串', page.data.keyword === '' && page.data.searchText === '')
}
{
  // D3 重复确认同一关键词 → 不重复请求
  const { page, state } = loadForum()
  await search(page, state, '高数')
  const n = state.requests.length
  page.onSearchConfirm()
  await tick()
  check('D3 重复确认同一关键词不重复发请求', state.requests.length === n, `请求数 ${n} → ${state.requests.length}`)
}
{
  // D4 纯空白关键词 = 无搜索（不改变已提交口径）
  const { page, state } = loadForum()
  tapTab(page, 1)
  await tick()
  const req = await search(page, state, '   ')
  check('D4 纯空白关键词不触发搜索（无 keyword 请求）', req === null && page.data.searchText === '', req ? JSON.stringify(qs(req)) : '')
}
{
  // D5 有搜索态时输入空白并确认 → 退出搜索态，回到普通列表
  const { page, state } = loadForum()
  tapTab(page, 1)
  await tick()
  await search(page, state, '高数')
  const req = await search(page, state, '  ')
  check(
    'D5 搜索态下清成空白再确认 → 回到普通列表',
    isTopics(req) && qs(req).keyword === undefined && page.data.searchText === '',
    req ? JSON.stringify(qs(req)) : '未发出请求'
  )
}
{
  // D6 搜索失败后按回车必须能重试（否则用户只能去点「重试」按钮）
  let fail = true
  const { page, state } = loadForum({
    responder: (req) =>
      fail
        ? { data: { code: 5001, message: '关键词搜索暂不可用，请稍后再试' } }
        : { data: { code: 0, message: 'ok', data: { items: itemsFor(req, 2), total: 2, page: 1, size: 20 } } },
  })
  tapTab(page, 1)
  await tick()
  await search(page, state, '高数')
  check('D6 失败后处于 error 态', page.data.error !== '', `error=${JSON.stringify(page.data.error)}`)

  fail = false
  const n = state.requests.length
  page.onSearchConfirm()
  await tick()
  check('D6 失败后再按回车会重新发起请求（可重试）', state.requests.length === n + 1, `请求数 ${n} → ${state.requests.length}`)
  check('D6 重试成功后 error 清空', page.data.error === '', `error=${JSON.stringify(page.data.error)}`)
}

// ---------------------------------------------------- E. 分页与到底 ----
bar('E. 分页与到底（翻页保留标签与关键词；热点榜不翻页）')
{
  // 满页应答（20 条 = PAGE_SIZE）→ finished=false，允许翻页
  const { page, state } = loadForum({ responder: okPaged(20) })
  tapTab(page, 1)
  await tick()
  check('E1 首页 20 条后 finished=false', page.data.finished === false, `finished=${page.data.finished}`)
  const req = await after(state, () => page.onReachBottom())
  check(
    'E1 触底翻页 → page=2 且保留 category',
    isTopics(req) && qs(req).page === 2 && qs(req).category === '学习',
    req ? JSON.stringify(qs(req)) : '未发出请求'
  )
  check('E1 第 2 页结果追加到列表（20 + 20）', page.data.items.length === 40, `实际 ${page.data.items.length}`)
}
{
  // 不满页 → finished=true，触底不再请求
  const { page, state } = loadForum({ responder: okPaged(3) })
  tapTab(page, 1)
  await tick()
  check('E2 不满一页 → finished=true', page.data.finished === true, `finished=${page.data.finished}`)
  const req = await after(state, () => page.onReachBottom())
  check('E2 finished 后触底不再发请求', req === null, req ? JSON.stringify(qs(req)) : '')
}
{
  // 热点榜一次性返回 → 恒 finished，不翻页
  const { page, state } = loadForum({ responder: okPaged(3) })
  tapTab(page, 5)
  await tick()
  check('E3 热点榜 finished=true（后端一次性返回）', page.data.finished === true, `finished=${page.data.finished}`)
  const req = await after(state, () => page.onReachBottom())
  check('E3 热点榜触底不再发请求', req === null, req ? JSON.stringify(qs(req)) : '')
}
{
  // 热点榜请求失败时 finished 仍为 false —— 触底也不得重复追加同一批数据
  const { page, state } = loadForum({ responder: () => ({ data: { code: 1001, message: '出错了' } }) })
  tapTab(page, 5)
  await tick()
  check('E3b 热点榜失败后 finished 仍为 false', page.data.finished === false, `finished=${page.data.finished}`)
  const req = await after(state, () => page.onReachBottom())
  check('E3b 热点榜失败态触底仍不发请求（不会重复追加）', req === null, req ? JSON.stringify(qs(req)) : '')
}
{
  // 翻页时保留关键词
  const { page, state } = loadForum({ responder: okPaged(20) })
  tapTab(page, 1)
  await tick()
  await search(page, state, '高数')
  const req = await after(state, () => page.onReachBottom())
  check(
    'E4 搜索结果翻页保留 keyword 与 category',
    isTopics(req) && qs(req).page === 2 && qs(req).keyword === '高数' && qs(req).category === '学习',
    req ? JSON.stringify(qs(req)) : '未发出请求'
  )
}

// -------------------------------------------------- F. 我的帖子状态 ----
bar('F. 我的帖子（审核状态映射；其它列表不渲染审核标签）')
{
  const rows = (opts) =>
    itemsFor(opts, 0).concat([
      { id: 11, title: 'A', audit_status: 0 },
      { id: 12, title: 'B', audit_status: 1 },
      { id: 13, title: 'C', audit_status: 2 },
    ])
  const { page } = loadForum({
    responder: (opts) => ({ data: { code: 0, message: 'ok', data: { items: rows(opts), total: 3, page: 1, size: 20 } } }),
  })
  tapTab(page, 6)
  await tick()
  const byId = {}
  page.data.items.forEach((it) => {
    byId[it.id] = it
  })
  check('F1 audit_status=0 → 待审核', byId[11] && byId[11].auditText === '待审核', byId[11] && byId[11].auditText)
  check(
    'F1 audit_status=1 → 已通过（auditOk）',
    byId[12] && byId[12].auditText === '已通过' && byId[12].auditOk === true,
    JSON.stringify(byId[12] || null)
  )
  check(
    'F1 audit_status=2 → 未通过（auditBad）',
    byId[13] && byId[13].auditText === '未通过' && byId[13].auditBad === true,
    JSON.stringify(byId[13] || null)
  )
}
{
  // 普通列表（无 audit_status）→ auditText 为空，WXML 据此不渲染审核标签
  const { page } = loadForum()
  tapTab(page, 0)
  await tick()
  check(
    'F2 普通列表条目无审核文案（不误渲染「待审核」）',
    page.data.items.every((it) => it.auditText === ''),
    JSON.stringify(page.data.items.map((it) => it.auditText))
  )
}

// ---------------------------------------------------- G. 失败路径 ----
bar('G. 失败路径（后端关键词检索失败关闭 5001 → 展示后端原文）')
{
  const msg = '关键词搜索暂不可用，请稍后再试'
  const { page, state } = loadForum({
    responder: () => ({ data: { code: 5001, message: msg } }),
  })
  tapTab(page, 1)
  await tick()
  await search(page, state, '高数')
  check('G1 code=5001 时页面 error 采用后端原文', page.data.error === msg, `error=${JSON.stringify(page.data.error)}`)
  check('G1 失败后 loading 复位', page.data.loading === false, `loading=${page.data.loading}`)
}
{
  // 非 5001 的业务错误仍走通用文案（不被后端措辞带偏）
  const { page, state } = loadForum({ responder: () => ({ data: { code: 1001, message: '帖子不存在' } }) })
  tapTab(page, 1)
  await tick()
  await search(page, state, '高数')
  check(
    'G2 非 5001 错误用通用文案（不误报后端细节）',
    page.data.error === '加载失败，请稍后重试',
    `error=${JSON.stringify(page.data.error)}`
  )
}

// ---------------------------------------------------- H. 乱序守护 ----
bar('H. 乱序守护（过期响应不得覆盖新标签的数据）')
{
  // 第 1 次请求挂起，第 2 次立即应答；随后补发第 1 次的响应
  const { page, state } = loadForum({
    responder: (req, n) => {
      if (n === 1) return null // 挂起
      return { data: { code: 0, message: 'ok', data: { items: itemsFor(req, 2), total: 2, page: 1, size: 20 } } }
    },
  })
  tapTab(page, 1) // 请求 1（学习）挂起
  await tick()
  tapTab(page, 2) // 请求 2（生活）立即应答
  await tick()
  const afterSecond = JSON.stringify(page.data.items.map((i) => i.title))
  check('H1 第 2 次请求的结果已落地（生活）', afterSecond.indexOf('生活') !== -1, afterSecond)

  // 补发被挂起的第 1 次响应（过期）
  const stale = state.hanging[0]
  stale.success({ data: { code: 0, message: 'ok', data: { items: itemsFor(stale, 2), total: 2, page: 1, size: 20 } } })
  await tick()
  const afterStale = JSON.stringify(page.data.items.map((i) => i.title))
  check('H1 过期响应被丢弃，未覆盖当前标签数据', afterStale === afterSecond, `${afterSecond} → ${afterStale}`)
  check('H1 过期响应不把 loading 卡住', page.data.loading === false, `loading=${page.data.loading}`)
}
{
  // 切标签时有请求在途：不得出现「列表已清空但永不加载」
  const { page, state } = loadForum({
    responder: (req, n) =>
      n === 1 ? null : { data: { code: 0, message: 'ok', data: { items: itemsFor(req, 2), total: 2, page: 1, size: 20 } } },
  })
  tapTab(page, 1)
  await tick()
  tapTab(page, 2)
  await tick()
  check(
    'H2 在途请求期间切标签仍能加载新标签数据（不空列表）',
    page.data.items.length === 2 && page.data.loading === false,
    `items=${page.data.items.length} loading=${page.data.loading}`
  )
}

// ---------------------------------------------------- I. 玻璃降级 ----
bar('I. 玻璃降级（F10 能力探测：不支持时根节点挂 .is-glass-fallback）')
{
  const { page } = loadForum({ glassSupported: false })
  page.onLoad()
  check('I1 探测不支持 → glassClass=is-glass-fallback', page.data.glassClass === 'is-glass-fallback', page.data.glassClass)
}
{
  const { page } = loadForum({ glassSupported: true })
  page.onLoad()
  check('I2 探测支持 → glassClass 为空（走 @supports 正向增强）', page.data.glassClass === '', JSON.stringify(page.data.glassClass))
}

// -------------------------------------------------- J. 结构对账 ----
bar('J. 结构对账（WXML 的 class 与事件处理函数都真实存在）')
{
  const wxml = fs.readFileSync(FORUM_WXML, 'utf8')
  const wxss = fs.readFileSync(FORUM_WXSS, 'utf8')
  const globalCss = [
    fs.readFileSync(path.join(MP, 'app.wxss'), 'utf8'),
    fs.readFileSync(path.join(MP, 'styles', 'glass.wxss'), 'utf8'),
    fs.readFileSync(path.join(MP, 'styles', 'tokens.wxss'), 'utf8'),
  ].join('\n')

  // J1 搜索框结构
  check('J1 存在搜索框容器 .search-bar', /class="search-bar"/.test(wxml))
  check('J1 搜索输入绑定 value="{{keyword}}"', /value="\{\{keyword\}\}"/.test(wxml))
  check('J1 搜索输入绑定 bindinput="onSearchInput"', /bindinput="onSearchInput"/.test(wxml))
  check('J1 搜索输入绑定 bindconfirm="onSearchConfirm"', /bindconfirm="onSearchConfirm"/.test(wxml))
  check('J1 搜索输入 confirm-type="search"（键盘搜索键）', /confirm-type="search"/.test(wxml))
  check('J1 存在清空控件 bindtap="onSearchClear"', /bindtap="onSearchClear"/.test(wxml))

  // J2 标签栏结构
  check('J2 标签栏用可横向滚动的 scroll-view', /<scroll-view[^>]*scroll-x/.test(wxml))
  check('J2 标签栏遍历 data.cats', /wx:for="\{\{cats\}\}"/.test(wxml))
  check('J2 标签绑定 bindtap="onCatChange" 且携带 data-index', /bindtap="onCatChange"/.test(wxml) && /data-index="\{\{index\}\}"/.test(wxml))
  check('J2 标签高亮态绑定 catIndex', /catIndex === index/.test(wxml))

  // J3 卡片与标签
  check('J3 帖子卡片挂玻璃类 xj-glass-card', /xj-glass-card/.test(wxml))
  check('J3 根节点挂 {{glassClass}}（运行时降级入口）', /\{\{glassClass\}\}/.test(wxml))
  check('J3 帖子内圆角小标签：分类', /class="xj-tag cat-tag"/.test(wxml))
  check('J3 帖子内圆角小标签：审核状态', /xj-tag audit-tag/.test(wxml))

  // J4 WXML 用到的字面量 class 必须有样式定义（防拼写错导致裸样式）
  const used = new Set()
  const classRe = /class="([^"]*)"/g
  let m
  while ((m = classRe.exec(wxml))) {
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
    if (!re.test(wxss) && !re.test(globalCss)) undef.push(c)
  })
  check(
    `J4 WXML 引用的 ${used.size} 个 class 均有样式定义`,
    undef.length === 0,
    `未定义：${undef.join(', ')}`
  )

  // J5 WXML 绑定的事件处理函数必须真实存在（否则静默失效）
  const handlerRe = /\bbind[a-z]+\s*=\s*"([A-Za-z_$][A-Za-z0-9_$]*)"/g
  const handlers = new Set()
  while ((m = handlerRe.exec(wxml))) handlers.add(m[1])
  const { page } = loadForum()
  const missing = []
  handlers.forEach((h) => {
    if (typeof page[h] !== 'function') missing.push(h)
  })
  check(
    `J5 WXML 绑定的 ${handlers.size} 个事件处理函数均存在`,
    missing.length === 0,
    `缺失：${missing.join(', ')}`
  )

  // J6 旧的「我的帖子」右侧跳转入口已并入标签栏（不再残留）
  check('J6 已移除旧 .tab-mine 跳转入口（我的帖子 改为标签）', !/tab-mine/.test(wxml) && typeof page.onMine !== 'function')

  // J7 标签闭合与嵌套配平（无 DevTools 时唯一能做的语法结构校验）
  const balance = checkTagBalance(wxml)
  check(
    'J7 WXML 标签闭合与嵌套配平',
    balance.ok,
    balance.detail
  )

  // J8 空字段不留空位：/topics/hot 不返回 author_name / created_at
  check(
    'J8 作者/时间空值时不渲染空位（wx:if 守卫）',
    /class="meta-item" wx:if="\{\{item\.authorName\}\}"/.test(wxml) &&
      /class="meta-item" wx:if="\{\{item\.time\}\}"/.test(wxml)
  )
}

// -------------------------------------------------- 反向对照 ----
bar('反向对照（对真实源码做一处语义突变后，断言必须转为失败 —— 证明不是空跑）')

// 行尾归一化：仓库 blob 为 LF，但 Windows 检出（core.autocrlf=true）会把工作区文件
// 变成 CRLF。R3 / R4 用**含 \n 的字面量锚点**造突变，不归一化则 replace 静默变成空操作，
// 自检会报「突变不可用」（不是逻辑错，只是环境差异）。归一化只在读取侧进行，
// 不触碰被测源码本身。
const realSource = fs.readFileSync(FORUM_JS, 'utf8').replace(/\r\n/g, '\n')

// R1 突变分类取值：学习 → 学习X
{
  const mutated = realSource.replace("{ label: '学习', value: '学习', kind: 'category' }", "{ label: '学习', value: '学习X', kind: 'category' }")
  check('R1 突变可用（源码确实被改写）', mutated !== realSource)
  const req = await probeTab(1, mutated)
  check(
    'R1 反证：category 取值写错时，B 段「学习」断言会失败',
    !(req && qs(req).category === '学习'),
    req ? JSON.stringify(qs(req)) : '未发出请求'
  )
}

// R2 突变搜索参数名：keyword → keywordX
{
  const mutated = realSource.replace(
    'data: { category: scoped ? cat.value : \'\', keyword, page, size }',
    'data: { category: scoped ? cat.value : \'\', keywordX: keyword, page, size }'
  )
  check('R2 突变可用（源码确实被改写）', mutated !== realSource)
  const { req } = await probeSearch(1, '高数资料', mutated)
  check(
    'R2 反证：搜索参数名写错时，C 段「学习」断言会失败',
    !(isTopics(req) && qs(req).keyword === '高数资料'),
    req ? JSON.stringify(qs(req)) : '未发出请求'
  )
}

// R3 突变标签集合：删掉「活动」
{
  const mutated = realSource.replace("  { label: '活动', value: '活动', kind: 'category' },\n", '')
  check('R3 突变可用（源码确实被改写）', mutated !== realSource)
  const { page } = loadForum({ sourceOverride: mutated })
  check(
    'R3 反证：少一个标签时，A 段断言会失败',
    JSON.stringify(page.data.cats) !== JSON.stringify(REQUIRED_CATS),
    JSON.stringify(page.data.cats)
  )
}

// R4 突变乱序守护：去掉请求序号比对
{
  const mutated = realSource.replace(/if \(seq !== this\.reqSeq\) \{\n          if \(typeof done === 'function'\) done\(\)\n          return\n        \}\n/, '')
  check('R4 突变可用（源码确实被改写）', mutated !== realSource)
  const { page, state } = loadForum({
    sourceOverride: mutated,
    responder: (req, n) =>
      n === 1 ? null : { data: { code: 0, message: 'ok', data: { items: itemsFor(req, 2), total: 2, page: 1, size: 20 } } },
  })
  tapTab(page, 1)
  await tick()
  tapTab(page, 2)
  await tick()
  const stale = state.hanging[0]
  stale.success({ data: { code: 0, message: 'ok', data: { items: itemsFor(stale, 2), total: 2, page: 1, size: 20 } } })
  await tick()
  check(
    'R4 反证：去掉序号守护后，H1 断言会失败（过期响应覆盖新数据）',
    JSON.stringify(page.data.items.map((i) => i.title)).indexOf('生活') === -1,
    JSON.stringify(page.data.items.map((i) => i.title))
  )
}

// R5 突变 WXML：删掉一个闭合标签 → J7 配平检查必须报错
{
  const wxml = fs.readFileSync(FORUM_WXML, 'utf8')
  const broken = wxml.replace('</scroll-view>', '')
  check('R5 突变可用（源码确实被改写）', broken !== wxml)
  const b = checkTagBalance(broken)
  check('R5 反证：WXML 少一个闭合标签时，J7 会失败', b.ok === false, JSON.stringify(b))
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
  console.log(`[PASS] F16 论坛搜索 + 标签栏校验通过（${pass} 项）`)
  console.log('⚠️ 仅覆盖请求分派/状态机/失败与乱序路径/结构对账；')
  console.log('   视觉呈现（搜索框与标签栏外观、玻璃模糊、吸顶滚动、低端机性能）仍需')
  console.log('   微信开发者工具 / 真机目视确认 —— 属 MANUAL CHECK。')
  process.exit(0)
}
console.log(`[FAIL] ${fail} 项未通过 / 共 ${pass + fail} 项`)
process.exit(1)
}

main().catch((err) => {
  console.error('[FAIL] 验证工具自身异常：', err)
  process.exit(1)
})
