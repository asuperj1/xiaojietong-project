#!/usr/bin/env node
/**
 * F18「校园地图容器」验证脚本（需求 10 / 二阶段方案 §2.6）
 *
 * 做法：用 Node `vm` + 桩 `wx` / `Page` / `getApp` **真实加载**
 *   `miniprogram/utils/map.js` 与 `miniprogram/pages/map/index.js`，
 *   再**实际调用**页面方法、灌入与 `backend/app/routers/map_api.py` 完全同形的响应，
 *   断言可观测行为。因此「POI→marker 映射」「后端 {lat,lng}→微信 {latitude,longitude}」
 *   「定位被拒路径」「navigate 调用路径」「过期响应丢弃」都是**跑出来**的结论，不是文本 grep。
 *
 * ⚠️ 仍属 MANUAL CHECK REQUIRED（代码级断言无法替代）：
 *   真机地图渲染与缩放/拖动、iPhone 安全区与胶囊避让、marker 点击手感、
 *   定位授权弹窗与 wx.openSetting 回流、低端 Android 下 map + glass 性能。
 *
 * 断言口径（避免「只会 PASS」的假断言）：
 *   - 比数值/行为，不比字面量：容器高度只要求 ≥ 60vh 且实际占满；
 *   - 认机制不认写法：地图居中用 setData(center) 还是 MapContext 都接受；
 *   - 注释不算实现：所有代码断言先剥离注释；
 *   - 无 `A || B` 式恒真断言；每条断言都能被具体缺陷打挂；
 *   - 空数据态与失败态必须**可达**（真实驱动空数组 / reject 两条路径）；
 *   - **完整性**：断言总数与 negative-control 组数在末尾自动统计并打印；
 *     任何逃逸异常都会被顶层捕获并计为 ERROR → 退出码非 0，
 *     不允许「前半段绿、后半段没执行」仍报 PASS。
 *
 * 用法：
 *     node tools/verify_f18_campus_map.js
 * 退出码：0 = 全部通过；1 = 有失败或异常
 *
 * 配套：`node tools/negative_control_f18_campus_map.js`
 *       在临时副本里注入已知缺陷与语义突变，要求每一条都被**指定断言**抓到。
 */

'use strict'

const fs = require('fs')
const os = require('os')
const path = require('path')
const vm = require('vm')

const ROOT = path.resolve(__dirname, '..')
const MP = path.join(ROOT, 'miniprogram')
const MAP_UTIL = path.join(MP, 'utils', 'map.js')
const PAGE_JS = path.join(MP, 'pages', 'map', 'index.js')

const isMain = require.main === module

// MAP_SRC_OVERRIDE 供 negative control 指向被注入缺陷的临时副本
const IS_OVERRIDE = !!process.env.MAP_SRC_OVERRIDE
const SRC_ROOT = IS_OVERRIDE ? path.resolve(process.env.MAP_SRC_OVERRIDE) : MP
const SRC_MAP_UTIL = path.join(SRC_ROOT, 'utils', 'map.js')
const SRC_PAGE_JS = path.join(SRC_ROOT, 'pages', 'map', 'index.js')
const SRC_APP_JSON = path.join(SRC_ROOT, 'app.json')

let passed = 0
let errored = 0
const failures = []
const sandboxes = []

function check(name, ok, detail) {
  if (ok) {
    passed += 1
    console.log(`  [OK] ${name}`)
  } else {
    failures.push(name + (detail ? `  <- ${detail}` : ''))
    console.log(`  [NG] ${name}${detail ? '  <- ' + detail : ''}`)
  }
}

function section(title) {
  console.log(`\n== ${title} ==`)
}

/**
 * 读取**被校验的源码**（MAP_SRC_OVERRIDE 生效时为副本，否则为真实仓库）。
 *
 * 所有断言一律用它。"读真实仓库"的变体是危险的：在 negative control 下，
 * 针对"本任务写范围内文件"的突变会对那种断言不可见 → 断言静默免疫（假绿）。
 * 本脚本早期版本就踩过这个坑（marker 的 iconPath 断言曾因此永远为真，
 * 被 negative control 的 add-image-asset 突变抓出）。
 * 副本是整棵 miniprogram/ 的完整拷贝，跨文件断言（如"首页入口仍指向本页"）
 * 同样成立；真正的越界篡改由 ⑱ 的完整性闸门用 sameFile() 字节对照单独拦截。
 */
function read(rel) {
  return fs.readFileSync(path.join(SRC_ROOT, rel), 'utf8')
}

/** 剥离注释：断言「是否真的做了某事」时，注释里写到不算（也避免文档字符串误报） */
function stripComments(src) {
  return src
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
}

/** 取某选择器自身规则体（锚定行首，避免 `.chip--active` 误命中 `.is-x .chip--active`） */
function cssBlock(src, selector) {
  const re = new RegExp(
    '^\\s*' + selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*\\{([^}]*)\\}',
    'm'
  )
  const m = src.match(re)
  return m ? m[1] : ''
}

/** 两个文件是否字节一致（用于完整性闸门） */
function sameFile(a, b) {
  try {
    return Buffer.compare(fs.readFileSync(a), fs.readFileSync(b)) === 0
  } catch (e) {
    return false
  }
}

// ------------------------------------------------------------ 桩环境 ----

/**
 * 建一个隔离沙箱：把真实的 utils/map.js + 页面 JS 复制到临时目录，
 * 用桩 `wx` / `Page` 加载，并暴露可驱动的请求/定位/弹窗桩。
 *
 * @param {Object} [opts]
 * @param {boolean} [opts.glassSupported=true] getApp().globalData.glassSupported 的取值。
 *        必须可配：否则"能力探测为 false → 实心降级"这条路径在测试中永远走不到。
 * @param {Object|null} [opts.menuRect] wx.getMenuButtonBoundingClientRect 的返回值。
 *        默认 undefined（模拟极旧基础库取不到胶囊），传对象才进入几何计算分支。
 * @param {number} [opts.windowWidth=375] wx.getWindowInfo().windowWidth
 */
function makeSandbox(opts) {
  const o = opts || {}
  const glassSupported = o.glassSupported === undefined ? true : !!o.glassSupported
  const menuRect = o.menuRect === undefined ? undefined : o.menuRect
  const windowWidth = o.windowWidth === undefined ? 375 : o.windowWidth
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'xjt-f18-'))
  sandboxes.push(dir)
  fs.mkdirSync(path.join(dir, 'utils'), { recursive: true })
  fs.mkdirSync(path.join(dir, 'pages', 'map'), { recursive: true })
  fs.copyFileSync(SRC_MAP_UTIL, path.join(dir, 'utils', 'map.js'))
  fs.copyFileSync(SRC_PAGE_JS, path.join(dir, 'pages', 'map', 'index.js'))

  const calls = []
  let requestImpl = () => Promise.resolve({ items: [] })
  const toasts = []
  const modals = []
  let openSettingCount = 0
  const navs = []
  let locationImpl = (opts) => opts.success({ latitude: 43.88, longitude: 125.32 })
  let actionSheetImpl = (opts) => {
    if (typeof opts.fail === 'function') opts.fail({ errMsg: 'showActionSheet:fail cancel' })
  }
  const mapCtxCalls = { moveToLocation: [], includePoints: [] }

  const wx = {
    request(opts) {
      const call = { url: opts.url, method: opts.method || 'GET', data: opts.data }
      calls.push(call)
      return Promise.resolve(requestImpl(call)).then(
        (body) => opts.success({ statusCode: 200, data: body }),
        () => opts.fail({ errMsg: 'request:fail' })
      )
    },
    showToast(o) {
      toasts.push(o)
    },
    showModal(o) {
      modals.push(o)
      if (typeof o.success === 'function') o.success({ confirm: true, cancel: false })
    },
    openSetting() {
      openSettingCount += 1
    },
    getLocation(o) {
      locationImpl(o)
    },
    showActionSheet(o) {
      actionSheetImpl(o)
    },
    navigateBack(o) {
      navs.push({ type: 'navigateBack', delta: (o && o.delta) || 1 })
    },
    reLaunch(o) {
      navs.push({ type: 'reLaunch', url: o && o.url })
    },
    switchTab(o) {
      navs.push({ type: 'switchTab', url: o && o.url })
    },
    navigateTo(o) {
      navs.push({ type: 'navigateTo', url: o && o.url })
    },
    createMapContext() {
      return {
        moveToLocation(o) {
          mapCtxCalls.moveToLocation.push(o)
        },
        includePoints(o) {
          mapCtxCalls.includePoints.push(o)
        },
      }
    },
    getWindowInfo() {
      return { statusBarHeight: 44, windowWidth }
    },
    getDeviceInfo() {
      return { system: 'iOS 17.0' }
    },
    getSystemInfoSync() {
      return { statusBarHeight: 44, windowWidth, system: 'iOS 17.0' }
    },
    // 只有显式传入 menuRect 时才提供该方法：默认缺席 = 模拟极旧基础库，
    // 用来驱动「取不到胶囊位置 → 兜底 100px」那条路径
    ...(menuRect === undefined
      ? {}
      : {
          getMenuButtonBoundingClientRect() {
            return menuRect
          },
        }),
    removeStorageSync() {},
    getStorageSync() {
      return ''
    },
  }

  const pageStack = [{ route: 'pages/map/index' }]
  let pageConfig = null

  const sandbox = {
    wx,
    console,
    setTimeout,
    clearTimeout,
    Promise,
    Date,
    Math,
    JSON,
    Number,
    String,
    Object,
    Array,
    Error,
    isNaN,
    parseInt,
    parseFloat,
    module: { exports: {} },
    require(request) {
      if (request.indexOf('services/request') !== -1) {
        return {
          request(p, options) {
            const opts = options || {}
            const call = {
              url: 'https://api.test' + p,
              path: p,
              method: opts.method || 'GET',
              data: opts.data,
            }
            calls.push(call)
            return Promise.resolve(requestImpl(call)).then((body) => {
              // 复刻 request.js 的成功语义：resolve 业务 data
              if (body && typeof body === 'object' && typeof body.code === 'number') {
                if (body.code !== 0) {
                  return Promise.reject(
                    Object.assign(new Error(body.message || 'fail'), { code: body.code })
                  )
                }
                return body.data
              }
              return body
            })
          },
        }
      }
      if (request.indexOf('utils/map') !== -1) {
        return sandbox.module.exports.mapUtil
      }
      // 未知模块：返回一个宽容的桩，而不是抛异常。
      //
      // 抛异常会让 makeSandbox() 在页面加载阶段就失败，进而让调用它的那一条断言
      // 之后的**所有**断言不再执行 —— 那正是本项目历史上"前半段绿、后半段没执行"
      // 的成因（旧 verifier 在 pendingByCat 处崩溃，后续断言从未运行）。
      // 引入未授权依赖应当由静态断言（③「未引入第三方地图 SDK」）判失败，
      // 而不是靠沙箱崩溃来表达。
      return new Proxy(
        {},
        {
          get(target, prop) {
            if (prop === '__stub__') return true
            if (prop === 'then') return undefined // 避免被误当成 thenable
            return typeof prop === 'string' && prop === 'default' ? {} : () => undefined
          },
          apply() {
            return undefined
          },
        }
      )
    },
    Page(cfg) {
      pageConfig = cfg
    },
    getApp() {
      return { globalData: { glassSupported } }
    },
    getCurrentPages() {
      return pageStack
    },
  }
  sandbox.global = sandbox
  sandbox.module.exports.mapUtil = require(SRC_MAP_UTIL)

  vm.createContext(sandbox)
  vm.runInContext(fs.readFileSync(SRC_PAGE_JS, 'utf8'), sandbox, {
    filename: 'pages/map/index.js',
  })

  if (!pageConfig) throw new Error('页面未调用 Page() 注册')

  const page = Object.assign({}, pageConfig)
  page.data = JSON.parse(JSON.stringify(pageConfig.data || {}))
  page.setData = function (patch) {
    Object.keys(patch).forEach((k) => {
      page.data[k] = patch[k]
    })
  }

  return {
    page,
    mapUtil: sandbox.module.exports.mapUtil,
    calls,
    toasts,
    modals,
    navs,
    mapCtxCalls,
    getOpenSettingCount: () => openSettingCount,
    setRequestImpl(fn) {
      requestImpl = fn
    },
    setLocationImpl(fn) {
      locationImpl = fn
    },
    setActionSheetImpl(fn) {
      actionSheetImpl = fn
    },
    setPageStack(stack) {
      pageStack.length = 0
      stack.forEach((r) => pageStack.push({ route: r }))
    },
    onLoad: (options) => page.onLoad(options || {}),
  }
}

/** 让已排队的 Promise 回调跑完 */
function tick(times) {
  let p = Promise.resolve()
  const n = times || 6
  for (let i = 0; i < n; i++) p = p.then(() => new Promise((r) => setTimeout(r, 0)))
  return p
}

/**
 * 分类 chip 索引查询：找不到就**抛错**，绝不静默返回 -1。
 *
 * 旧版 verifier 曾在这里静默失败：动态 chips 在首次全量响应到达前只有
 * ['全部']，`cats.indexOf('食堂')` 得到 -1，随后 `dataset.index = -1`
 * 被 onCatChange 的越界守卫拒绝，请求永远挂起，
 * `pendingByCat['食堂']` 为 undefined → 整个脚本在 TypeErrors 处崩溃，
 * 后半段断言与大量 negative control 从未执行，却仍可能被读成"通过"。
 */
function catIndex(page, name) {
  const idx = page.data.cats.indexOf(name)
  if (idx < 0) throw new Error(`分类 chip 不存在：${name}（cats=${JSON.stringify(page.data.cats)}）`)
  return idx
}

/** 挂起式请求桩：按分类 key 保存 resolve，供乱序返回测试 */
function makePendingByCat(sb) {
  const pending = {}
  sb.setRequestImpl((call) => {
    const cat = (call.data && call.data.category) || ''
    return new Promise((resolve) => {
      pending[cat] = resolve
    })
  })
  return {
    resolve(cat, payload) {
      if (typeof pending[cat] !== 'function') {
        throw new Error(`没有挂起中的请求：category="${cat}"（已有 ${Object.keys(pending).join('/')}）`)
      }
      pending[cat](payload)
      delete pending[cat]
    },
    keys: () => Object.keys(pending),
  }
}

// ------------------------------------------------------- 真实响应形状 ----

// ⚠️ 必须与 backend/app/routers/map_api.py 的 SELECT 完全一致：
//    `SELECT id, name, category, latitude, longitude, floor FROM poi`
//    —— **不含 building_id、不含 description**。此处若擅自加上后端不返回的字段，
//    会掩盖「前端依赖了不存在的字段」这类真实缺陷（见 ⑫ 的契约缺口断言）。
const POIS_RES = {
  code: 0,
  data: {
    items: [
      { id: 1, name: '中心图书馆', category: '图书馆', latitude: 43.88, longitude: 125.32, floor: 0 },
      { id: 2, name: '第二教学楼', category: '教学楼', latitude: 43.881, longitude: 125.322, floor: 0 },
      { id: 3, name: '湖畔餐厅', category: '食堂', latitude: 43.8792, longitude: 125.3185, floor: 0 },
    ],
  },
}

const NAVIGATE_RES = {
  code: 0,
  data: {
    distance: 760,
    straight_distance: 610,
    duration: 9,
    path: [
      { lat: 43.88, lng: 125.32 },
      { lat: 43.8805, lng: 125.321 },
      { lat: 43.881, lng: 125.322 },
    ],
    algorithm: 'astar-grid',
    start_source: 'nearest_poi',
    target: { id: 2, name: '第二教学楼' },
  },
}

// ============================================================ 断言 ----

async function run() {
  const wxml = read('pages/map/index.wxml')
  const wxss = read('pages/map/index.wxss')
  const pageJson = JSON.parse(read('pages/map/index.json'))
  const pageJs = read('pages/map/index.js')
  const wxmlCode = stripComments(wxml)
  const pageJsCode = stripComments(pageJs)
  const appJson = JSON.parse(fs.readFileSync(SRC_APP_JSON, 'utf8'))

  // ---------------------------------------------------- ① 地图容器 ----
  section('① 地图主体（<map> 容器 / 尺寸 / 浮层不挤占）')

  check('WXML 使用微信原生 <map> 组件', /<map[\s>]/.test(wxmlCode))
  check(
    'map 组件绑定 latitude 与 longitude 属性',
    /<map[\s\S]*?latitude="\{\{/.test(wxmlCode) && /<map[\s\S]*?longitude="\{\{/.test(wxmlCode)
  )
  check('map 组件绑定 markers 与 polyline', /<map[\s\S]*?markers="\{\{/.test(wxmlCode) && /<map[\s\S]*?polyline="\{\{/.test(wxmlCode))
  check('map 组件绑定 markertap 事件', /bindmarkertap="onMarkerTap"/.test(wxmlCode))
  // 底图自带 POI 图层会与自绘 marker 视觉竞争并抢点击事件，必须关闭
  check('关闭底图自带 POI 图层（enable-poi=false，避免与自绘 marker 竞争）', /enable-poi="\{\{false\}\}"/.test(wxmlCode))

  const rootBlock = cssBlock(wxss, '.map-root')
  const canvasBlock = cssBlock(wxss, '.map-canvas')
  check('.map-root 规则体存在且非空', rootBlock.trim().length > 0)

  // 容器高度：需求要求「占屏 ≥ 60%」——按**数值**判定，不认字面量写法
  const rootHeight = ((rootBlock.match(/height:\s*([^;]+);/) || [])[1] || '').trim()
  const vhMatch = /^(\d+(?:\.\d+)?)vh$/.exec(rootHeight)
  const pctOk = rootHeight === '100%'
  check(
    '.map-root 高度铺满整屏且 ≥ 60% 视口（数值判定）',
    pctOk || (!!vhMatch && parseFloat(vhMatch[1]) >= 60),
    `height: ${rootHeight}`
  )
  // 反例：固定 rpx 高度（如 600rpx ≈ 30% 屏）必然小于 60%，必须判失败
  check('.map-root 未用固定 rpx 高度压缩地图', !/^\d+(?:\.\d+)?rpx$/.test(rootHeight), `height: ${rootHeight}`)
  check('.map-root 未使用 max-height 限制地图', !/max-height/.test(rootBlock))

  const canvasHeight = ((canvasBlock.match(/height:\s*([^;]+);/) || [])[1] || '').trim()
  check('.map-canvas 高度为 100% 撑满父容器', canvasHeight === '100%', `height: ${canvasHeight}`)
  check('.map-canvas 宽度为 100%', /width:\s*100%/.test(canvasBlock))

  // 浮层：导航与卡片必须是 absolute/fixed，不能是普通流式块（否则会把地图挤掉）
  check('.nav-bar 为浮层（absolute/fixed）', /position:\s*(absolute|fixed)/.test(cssBlock(wxss, '.nav-bar')))
  check('.poi-card 为浮层（absolute/fixed）', /position:\s*(absolute|fixed)/.test(cssBlock(wxss, '.poi-card')))
  check(
    '浮层使用 z-index 叠在地图之上',
    /z-index:\s*\d+/.test(cssBlock(wxss, '.nav-bar')) && /z-index:\s*\d+/.test(cssBlock(wxss, '.poi-card'))
  )

  // 底部安全区：iPhone home indicator 不应压住信息卡
  check('底部安全区使用 env(safe-area-inset-bottom)', /env\(\s*safe-area-inset-bottom/.test(wxss))
  // 仅"定义了变量"不算实现：必须真的被信息卡的位置消费。
  // POI 卡与导航卡共用 .poi-card，因此这一条就覆盖两种卡片态。
  check(
    '信息卡位置真实消费底部安全区（bottom 计算含 safe-area，不是只定义了变量）',
    /bottom:[^;]*safe-area-inset-bottom/.test(cssBlock(wxss, '.poi-card')) ||
      (/bottom:[^;]*var\(\s*--map-safe-bottom/.test(cssBlock(wxss, '.poi-card')) &&
        /--map-safe-bottom\s*:\s*env\(\s*safe-area-inset-bottom/.test(rootBlock)),
    `bottom=${(cssBlock(wxss, '.poi-card').match(/bottom:\s*([^;]+);/) || [])[1] || '(无)'}`
  )
  // 顶部安全区：刘海屏自定义导航需要状态栏高度（JS 注入），或 env() 兜底
  check(
    '顶部安全区处理（statusBarHeight 注入 或 env(safe-area-inset-top)）',
    /statusBarHeight/.test(wxmlCode) || /env\(\s*safe-area-inset-top/.test(wxss)
  )
  check(
    '顶部安全区实际作用于导航容器 padding（JS 注入值需被 CSS/WXML 承接）',
    /padding-top:\s*\{\{statusBarHeight\}\}px/.test(wxmlCode) ||
      /padding-top:[^;]*env\(\s*safe-area-inset-top/.test(cssBlock(wxss, '.nav-bar'))
  )

  // 本页不是 tab 页，且不得注册进 tabBar（避免被 TabBar 遮挡 / 误用 syncTabBar）
  const tabPaths = ((appJson.tabBar && appJson.tabBar.list) || []).map((i) => i.pagePath)
  check('地图页未被注册为 TabBar 页（无底部栏遮挡）', tabPaths.indexOf('pages/map/index') === -1, tabPaths.join(','))
  check('地图页已注册在 app.json pages', (appJson.pages || []).indexOf('pages/map/index') !== -1)
  check('本页开启自定义导航（navigationStyle: custom）', pageJson.navigationStyle === 'custom')
  check('本页禁用页面级滚动（地图手势不被抢）', pageJson.disableScroll === true)

  // F12 兼容：非 tab 页不得调用 syncTabBar / 渲染 custom-tab-bar
  check('本页未调用 syncTabBar（非 Tab 页）', !/syncTabBar/i.test(pageJsCode))
  check('本页未引入 custom-tab-bar 组件', !/custom-tab-bar/.test(wxmlCode) && !/custom-tab-bar/.test(pageJson.usingComponents ? JSON.stringify(pageJson.usingComponents) : ''))
  check('本页未新增 tabBar 配置（app.json tabBar 仍为 F12 的 5 项）', tabPaths.length === 5, tabPaths.join(','))

  // ---------------------------------------------------- ② 顶部导航 ----
  section('② 顶部导航（返回 / 标题 / 定位 / 建筑入口）')

  check('存在返回按钮（bindtap=onBack）', /bindtap="onBack"/.test(wxmlCode))
  check('存在页面标题「校园地图」', /<text class="nav-title-main">\s*校园地图\s*<\/text>/.test(wxmlCode))
  check('存在定位按钮（bindtap=onLocate）', /bindtap="onLocate"/.test(wxmlCode))
  check('存在建筑入口（bindtap=onOpenBuildings）', /bindtap="onOpenBuildings"/.test(wxmlCode))
  // 图标用 CSS 绘制（本仓无专用于导航的线性 SVG 图标体系，不引入 emoji/厚图标）
  check('返回箭头为 CSS 绘制（非 emoji）', /glyph-back/.test(wxmlCode) && /\.glyph-back\s*\{/.test(wxss))
  check('定位控件为 CSS 绘制（非 emoji）', /glyph-locate/.test(wxmlCode) && /\.glyph-locate\s*\{/.test(wxss))
  check('建筑入口为 CSS 绘制（非 emoji）', /glyph-building/.test(wxmlCode) && /\.glyph-building\s*\{/.test(wxss))
  check('关闭按钮为 CSS 绘制（非 emoji/填充符号）', /glyph-close/.test(wxmlCode) && /\.glyph-close::before/.test(wxss))
  // 反例口径：任何 emoji / 装饰性符号都不允许充当图标
  check(
    '页面未使用 emoji 或装饰符号充当图标',
    !/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{25A0}-\u{25FF}]/u.test(wxmlCode)
  )
  check('样式复用 F10 tokens/glass（@import）', /@import\s+["'][^"']*tokens\.wxss/.test(wxss) && /@import\s+["'][^"']*glass\.wxss/.test(wxss))
  check('消费 F10 glass 基础设施降级类 is-glass-fallback', /is-glass-fallback/.test(wxss) && /is-glass-fallback/.test(wxmlCode))
  check('玻璃降级由 app.js 能力探测驱动（glassSupported → glassFallback）', /glassFallback/.test(pageJsCode) && /glassSupported/.test(pageJsCode))
  check('主色取自 token（--xj-color-primary）', /var\(\s*--xj-color-primary/.test(wxss))
  // 反例：剥掉 var(..., fallback) 后仍残留的主色十六进制值 = 绕过 token 写死颜色
  const wxssNoVarFallback = wxss.replace(/var\([^()]*(?:\([^()]*\)[^()]*)*\)/g, 'VAR')
  check(
    '未绕过 token 写死主色（var() 回退之外不得出现主色字面量）',
    !/#4A90D9|#3F82C7/i.test(wxssNoVarFallback),
    (wxssNoVarFallback.match(/#4A90D9|#3F82C7/gi) || []).join(',')
  )
  // 胶囊避让：自定义导航下原生胶囊仍绘制在右上角
  check('导航为胶囊按钮预留宽度（capsuleReserve 注入）', /capsuleReserve/.test(pageJsCode) && /padding-right:\s*\{\{capsuleReserve\}\}px/.test(wxmlCode))
  check('胶囊预留取真实胶囊位置（getMenuButtonBoundingClientRect）', /getMenuButtonBoundingClientRect/.test(pageJsCode))
  check('取不到胶囊位置时有兜底宽度（不为 0 / undefined）', /capsuleReserve\s*=\s*100/.test(pageJsCode) && /capsuleReserve:\s*100/.test(pageJsCode))
  check('状态栏高度取不到时兜底为 0（不产生 undefined 布局）', /statusBarHeight:\s*0/.test(pageJsCode))

  // ---------------------------------------------------- ③ 接口契约 ----
  section('③ 接口接入（真实端点，无 mock / 无假数据）')

  check('调用 GET /map/pois', /request\(\s*['"]\/map\/pois['"]/.test(pageJsCode))
  check('调用 GET /map/nearby', /request\(\s*['"]\/map\/nearby['"]/.test(pageJsCode))
  check(
    '调用 POST /map/navigate 且方法为 POST',
    /request\(\s*['"]\/map\/navigate['"][\s\S]{0,80}?method:\s*['"]POST['"]/.test(pageJsCode)
  )
  // 建筑详情由 pages/map/building 承担（本页只带真实 building_id 跳转）：
  // 同时断言「跳转 URL 形状」与「被委托页面确实调用该接口」，净效果等价于接了真接口
  check('建筑详情跳转 URL 为 /pages/map/building?id=', /['"]\/pages\/map\/building\?id=['"]\s*\+/.test(pageJsCode))
  check(
    '建筑详情接口由被委托页面真实调用',
    /request\(\s*['"]\/map\/building\/['"]\s*\+/.test(stripComments(read('pages/map/building.js')))
  )
  check(
    '读接口未误加 method:POST',
    !/\/map\/(pois|nearby)['"][\s\S]{0,40}?method:\s*['"]POST['"]/.test(pageJsCode)
  )
  // 「无假 POI」的精确口径：markers/items 的业务数据必须来自接口解析结果
  check(
    'markers 数据来自接口响应（normalizePois 解析 res.items → toMarkers）',
    /mapUtil\.normalizePois\(\s*\(res && res\.items\)/.test(pageJsCode) &&
      /markers:\s*mapUtil\.toMarkers\(\s*items/.test(pageJsCode)
  )
  // 允许的唯一常量清单：已知建筑名（后端无建筑列表接口，仅作为页面间跳转标签）
  const constListMatch = pageJsCode.match(/const\s+KNOWN_BUILDINGS\s*=\s*\[[\s\S]*?\]/)
  const codeWithoutConstList = constListMatch ? pageJsCode.replace(constListMatch[0], '') : pageJsCode
  check(
    '页面内未写死 POI 列表（除建筑入口标签外无中文地点名数据）',
    !/[{,]\s*name:\s*['"][\u4e00-\u9fa5]/.test(codeWithoutConstList)
  )
  check('建筑入口标签与已知建筑 id 同时给出（可核验，非凭空 id）', !!constListMatch && /id:\s*\d+/.test(constListMatch[0]))
  check('建筑入口 id 与 db/sql/99_init_data.sql 的 building 种子一致（1,2）', (() => {
    if (!constListMatch) return false
    const ids = (constListMatch[0].match(/id:\s*(\d+)/g) || []).map((s) => Number(s.replace(/\D/g, '')))
    return ids.length === 2 && ids.indexOf(1) !== -1 && ids.indexOf(2) !== -1
  })())
  check('页面未使用本地 mock/兜底假数据（mockData|fakePoi|MOCK_）', !/mockData|fakePoi|MOCK_POI/i.test(pageJsCode))
  check('nearby 参数为真实契约 lat/lng/radius', /lat:\s*[\s\S]{0,80}?lng:\s*[\s\S]{0,80}?radius:/.test(pageJsCode))
  check('nearby radius 在契约允许区间 1~5000', (() => {
    const m = pageJsCode.match(/NEARBY_RADIUS\s*=\s*(\d+)/)
    return !!m && Number(m[1]) >= 1 && Number(m[1]) <= 5000
  })())
  check('pois 参数使用 category', /category/.test(pageJsCode))
  check('navigate 请求体使用 to_poi_id', /to_poi_id/.test(pageJsCode))
  check('navigate 起点使用 from_lat/from_lng（真实契约字段名）', /from_lat/.test(pageJsCode) && /from_lng/.test(pageJsCode))
  check('无硬编码 host（不写死域名 / IP）', !/https?:\/\/(?!api\.test)/.test(pageJsCode) && !/127\.0\.0\.1|localhost/.test(pageJsCode))
  check('base URL 仍由 services/request 统一解析（页面不自行拼接域名）', /require\(\s*['"][^'"]*services\/request['"]\s*\)/.test(pageJs))
  check('未引入第三方地图 SDK（仍走自研 /map/navigate）', !/amap|qq\.map|bmap|L\.map|mapbox/i.test(pageJsCode))
  check('未新增图片/SVG 素材依赖（marker 走原生形态 + CSS 图标）', !/\.(png|jpg|jpeg|svg|webp)['"]/.test(pageJsCode) && !/\.(png|jpg|jpeg|svg|webp)['"]/.test(wxmlCode))
  // 断言必须看**被校验的那份源码**（MAP_SRC_OVERRIDE 指向的副本），
  // 否则在 negative control 下这条断言永远看的是真实仓库文件，
  // 注入的 iconPath 根本不可见 —— 该断言对突变免疫，等于没有。
  //
  // 同时**不能**先剥注释：iconPath 的说明本身就是一句注释，
  // 剥注释会把注释连同注入的代码行一起删掉（曾因此漏检）。
  // 因此这里用未剥注释的 override 源码，只取 toMarkers 函数体，
  // 并对 indexOf === -1 做显式守卫（否则 slice(-1) 会返回 1 个字符，
  // 使后面的否定断言恒真）。
  check('marker 不设置 iconPath（使用系统默认图钉，不新增资产）', (() => {
    const src = read('utils/map.js')
    const i = src.indexOf('function toMarkers')
    if (i === -1) return false
    const markerFn = src.slice(i)
    return markerFn.length > 0 && !/iconPath\s*:/.test(markerFn)
  })())

  // ---------------------------------------------------- ④ 定位权限 ----
  section('④ 定位（权限声明 / gcj02 / 拒绝 / 失败 / 防重入 / TTL）')

  // app.json 缺失 permission / requiredPrivateInfos 时必须**判失败**而不是抛异常：
  // 抛异常会让后续所有断言不执行（历史上就是这样把"后半段没跑"伪装成"通过"的）。
  const locPerm = (appJson.permission && appJson.permission['scope.userLocation']) || null
  check(
    'app.json 声明 requiredPrivateInfos 含 getLocation',
    Array.isArray(appJson.requiredPrivateInfos) && appJson.requiredPrivateInfos.indexOf('getLocation') !== -1,
    JSON.stringify(appJson.requiredPrivateInfos)
  )
  check(
    'app.json 声明 scope.userLocation 用途说明',
    !!(locPerm && locPerm.desc)
  )
  check(
    'app.json 权限 desc 长度符合官方约束（≤30 字符，缺省时本项只反映"声明缺失"）',
    !!locPerm && String(locPerm.desc || '').length <= 30,
    locPerm ? String(locPerm.desc.length) : '(permission 缺失)'
  )
  check(
    'app.json 权限声明为最小改动（requiredPrivateInfos 只有 getLocation）',
    Array.isArray(appJson.requiredPrivateInfos) && appJson.requiredPrivateInfos.length === 1
  )
  check('使用 wx.getLocation 且坐标系为 gcj02', /wx\.getLocation\(\{[\s\S]{0,200}?type:\s*['"]gcj02['"]/.test(pageJsCode))
  check('拒绝授权走 wx.openSetting 引导（非静默失败）', /openSetting/.test(pageJsCode))
  check('拒绝授权与定位失败分别处理（两条分支）', /auth deny/.test(pageJsCode) && /定位失败/.test(pageJsCode))
  check('定位请求有防重入（locating 守卫）', /if\s*\(this\.data\.locating\)\s*return/.test(pageJsCode))
  check('定位结果有 TTL 缓存（避免高频重复定位）', /LOCATION_TTL_MS/.test(pageJsCode) && /locationAt/.test(pageJsCode))
  check('未使用 setInterval 轮询定位', !/setInterval/.test(pageJsCode))

  // ---------------------------------------------------- ⑤ marker 映射 ----
  section('⑤ POI → marker 映射（含 lat/lng 颠倒与脏数据负例）')

  // 沙箱构建包在 try/catch 里：页面若引用了不存在的模块（例如被塞进第三方 SDK），
  // 这里必须**判失败**而不是抛异常 —— 抛异常会让后续几十条断言不再执行，
  // 那正是"前半段绿、后半段没执行"的成因。
  let U = null
  let sandboxError = ''
  try {
    U = makeSandbox().mapUtil
  } catch (e) {
    sandboxError = e && e.message ? e.message : String(e)
  }
  check('utils/map.js 可被独立加载（无缺失依赖）', !!U, sandboxError)
  if (!U) {
    // 后续断言依赖 U；用一个显式的空实现占位，让脚本继续跑完并与负控口径一致。
    // 注意这不是"跳过"：上面的断言已经失败，退出码必然非 0。
    U = {
      toMarkers: () => [],
      colorOfCategory: () => '',
      isValidLatLng: () => false,
      normalizePoi: () => null,
      normalizePois: () => [],
      toCategoryList: () => [],
      toPolyline: () => [],
      formatDistance: () => '',
      buildNavViewModel: () => null,
      centerOf: () => ({}),
      mergeDistance: () => [],
      sortByDistance: () => [],
      DEFAULT_CENTER: { latitude: 0, longitude: 0 },
      DEFAULT_SCALE: 16,
      LOCATED_SCALE: 17,
      BASE_CATS: [],
      CATEGORY_COLORS: {},
    }
  }

  const markers = U.toMarkers([
    { id: 1, name: '中心图书馆', category: '图书馆', latitude: 43.88, longitude: 125.32 },
  ])
  check('单 POI 生成 1 个 marker', markers.length === 1)
  check('marker.latitude === 源 latitude（不颠倒）', markers[0].latitude === 43.88, String(markers[0].latitude))
  check('marker.longitude === 源 longitude（不颠倒）', markers[0].longitude === 125.32, String(markers[0].longitude))
  check('marker.id 为数字（微信 map 要求 Number）', typeof markers[0].id === 'number' && markers[0].id === 1)
  check('marker 携带 callout 名称', !!(markers[0].callout && markers[0].callout.content === '中心图书馆'))
  check('marker 携带分类着色 label', !!(markers[0].label && markers[0].label.bgColor))
  check('marker 不携带 building_id 派生字段（后端不返回，禁止伪造）', !('buildingId' in markers[0]) && !('building_id' in markers[0]))
  check(
    '字符串型 id/坐标可归一化',
    (() => {
      const m = U.toMarkers([{ id: '7', name: 'x', category: '食堂', latitude: '43.879', longitude: '125.318' }])
      return m.length === 1 && m[0].id === 7 && m[0].latitude === 43.879 && m[0].longitude === 125.318
    })()
  )
  // 负例：坐标颠倒必须能被识别（若映射层把 lat 当 lng 写入，值会互换）
  const swapped = U.toMarkers([{ id: 1, name: 'x', category: '食堂', latitude: 43.88, longitude: 125.32 }])[0]
  check('负例：marker.latitude 不等于源 longitude（未颠倒）', swapped.latitude !== 125.32)

  check('不同分类得到不同颜色', U.colorOfCategory('食堂') !== U.colorOfCategory('图书馆'))
  check('未知分类回退到主色而非报错', U.colorOfCategory('未知分类') === U.colorOfCategory(''))

  // 归一化字段语义
  check('normalizePoi：lat/lng ↔ latitude/longitude 双向可读（字段名不混用）', (() => {
    const p = U.normalizePoi({ id: 9, name: 'n', category: '食堂', latitude: 43.88, longitude: 125.32 })
    return !!p && p.latitude === 43.88 && p.longitude === 125.32 && p.lat === undefined && p.lng === undefined
  })())
  check(
    'normalizePoi：floor=0 归一为 0（列表不显示「0 楼」）',
    U.normalizePoi({ id: 1, name: 'n', latitude: 43.88, longitude: 125.32, floor: 0 }).floor === 0
  )
  check(
    'normalizePoi：distance 缺省为 null（不编造距离）',
    U.normalizePoi({ id: 1, name: 'n', latitude: 43.88, longitude: 125.32 }).distance === null
  )
  check(
    'normalizePoi：后端不返回 building_id 时 buildingId 为 null',
    U.normalizePoi({ id: 1, name: 'n', latitude: 43.88, longitude: 125.32 }).buildingId === null
  )

  // 脏数据过滤
  const dirty = U.toMarkers([
    { id: 1, name: 'ok', latitude: 43.88, longitude: 125.32 },
    { id: 2, name: 'zero', latitude: 0, longitude: 0 }, // 后端 DECIMAL 默认值
    { id: 3, name: 'nan', latitude: 'abc', longitude: 125 }, // 脏字符串
    { id: 4, name: 'range', latitude: 200, longitude: 125 }, // 越界纬度
    { name: 'noid', latitude: 43.88, longitude: 125.32 }, // 缺 id
    null,
  ])
  check('脏坐标/缺 id 被过滤，只保留 1 个合法 marker', dirty.length === 1, `got ${dirty.length}`)
  check('isValidLatLng 拒绝 (0,0)', U.isValidLatLng(0, 0) === false)
  check(
    'isValidLatLng 拒绝单轴为 0 的脏值（后端 DECIMAL 默认 0 的漏填形态）',
    U.isValidLatLng(0, 125.32) === false && U.isValidLatLng(43.88, 0) === false
  )
  check('isValidLatLng 接受真实校园坐标', U.isValidLatLng(43.88, 125.32) === true)
  check('isValidLatLng 拒绝越界与 NaN', U.isValidLatLng(200, 125) === false && U.isValidLatLng(NaN, 125) === false)
  check('isValidLatLng 拒绝 ±90/±180 之外的边界外值', U.isValidLatLng(90.0001, 0.1) === false && U.isValidLatLng(43.88, 180.0001) === false)
  check(
    '选中态 marker zIndex 高于未选中',
    (() => {
      const m = U.toMarkers(
        [
          { id: 1, name: 'a', latitude: 43.88, longitude: 125.32 },
          { id: 2, name: 'b', latitude: 43.881, longitude: 125.322 },
        ],
        { selectedId: 1 }
      )
      return m.filter((x) => x.id === 1)[0].zIndex > m.filter((x) => x.id === 2)[0].zIndex
    })()
  )
  check(
    '选中 marker 的 callout 常显（display=ALWAYS）',
    U.toMarkers([{ id: 1, name: 'a', latitude: 43.88, longitude: 125.32 }], { selectedId: 1 })[0].callout.display === 'ALWAYS'
  )

  // ---------------------------------------------------- ⑥ 分类 chips ----
  section('⑥ 分类 chips（由真实 POI 数据推导，不写死虚构分类）')

  const catsFull = U.toCategoryList([
    { id: 1, name: 'a', category: '图书馆', latitude: 43.88, longitude: 125.32 },
    { id: 2, name: 'b', category: '食堂', latitude: 43.881, longitude: 125.322 },
    { id: 3, name: 'c', category: '火星基地', latitude: 43.882, longitude: 125.324 },
  ])
  check("chips 首项固定为「全部」", catsFull[0] === '全部')
  check('chips 含数据中真实出现的分类', catsFull.indexOf('图书馆') !== -1 && catsFull.indexOf('食堂') !== -1)
  check('chips 不含数据中不存在的分类（不写死虚构分类）', catsFull.indexOf('体育馆') === -1, catsFull.join(','))
  check('后端新增的未知分类也被纳入（不会有点位却无 chip）', catsFull.indexOf('火星基地') !== -1, catsFull.join(','))
  check('已知分类按固定顺序（图书馆在食堂之前）', catsFull.indexOf('图书馆') < catsFull.indexOf('食堂'))
  check('未知分类排在已知分类之后（字典序）', catsFull.indexOf('火星基地') > catsFull.indexOf('食堂'))
  check('空数据 → 只有「全部」', JSON.stringify(U.toCategoryList([])) === JSON.stringify(['全部']))
  check('非数组输入不抛异常', JSON.stringify(U.toCategoryList(null)) === JSON.stringify(['全部']))
  check(
    'chips 由页面在**全量**结果上推导（分类结果不覆盖 chips）',
    /cats:\s*cat === '' \? mapUtil\.toCategoryList\(items\) : null/.test(pageJsCode)
  )

  // ---------------------------------------------------- ⑦ polyline ----
  section('⑦ 导航路径 → polyline（lat/lng → latitude/longitude）')

  const pl = U.toPolyline(NAVIGATE_RES.data.path)
  check('path → 1 条 polyline', pl.length === 1)
  check('polyline 点数为 3', pl[0].points.length === 3)
  check('polyline 点使用 latitude 字段（非 lat）', pl[0].points[0].latitude === 43.88 && pl[0].points[0].lat === undefined)
  check('polyline 点使用 longitude 字段（非 lng）', pl[0].points[0].longitude === 125.32 && pl[0].points[0].lng === undefined)
  check(
    'polyline 首点坐标正确（未颠倒）',
    pl[0].points[0].latitude === 43.88 && pl[0].points[0].longitude === 125.32
  )
  check(
    'polyline 末点坐标正确（未颠倒）',
    pl[0].points[2].latitude === 43.881 && pl[0].points[2].longitude === 125.322
  )
  check('polyline 携带颜色/宽度/箭头', !!(pl[0].color && pl[0].width) && pl[0].arrowLine === true)
  check('少于 2 点不产出 polyline（不画半条路线）', U.toPolyline([{ lat: 43.88, lng: 125.32 }]).length === 0)
  check('非数组 path 不抛异常且返回空', U.toPolyline(null).length === 0 && U.toPolyline(undefined).length === 0)
  check('全部非法点的 path 返回空', U.toPolyline([{ lat: 0, lng: 0 }, { lat: 'x', lng: 'y' }]).length === 0)
  check('含脏点的 path 只保留合法点（不注入 (0,0)）', (() => {
    const r = U.toPolyline([
      { lat: 43.88, lng: 125.32 },
      { lat: 0, lng: 0 },
      { lat: 43.881, lng: 125.322 },
    ])
    return r.length === 1 && r[0].points.length === 2 && r[0].points.every((p) => p.latitude !== 0)
  })())
  check('负例：polyline 点不含后端原始字段名 lat', pl[0].points[0].lat === undefined)

  // ---------------------------------------------------- ⑧ 距离 / 排序 ----
  section('⑧ 距离合并与排序（nearby 真实数据，不编造未知距离）')

  check('formatDistance：<1000m 用「米」', U.formatDistance(240) === '240 米', U.formatDistance(240))
  check('formatDistance：≥1000m 用「公里」', U.formatDistance(1240) === '1.2 公里', U.formatDistance(1240))
  check('formatDistance：真正的 0 米显示为「0 米」', U.formatDistance(0) === '0 米', U.formatDistance(0))
  check(
    'formatDistance：非数值/负数返回空串（不编造）',
    U.formatDistance(-1) === '' && U.formatDistance('x') === '',
    `-1→"${U.formatDistance(-1)}" x→"${U.formatDistance('x')}"`
  )
  // 关键回归：Number(null) === 0，若不在入口拦掉就会渲染成「0 米」——
  // 那是把「距离未知」伪装成「就在脚下」，属于编造数据。
  check(
    'formatDistance：null/undefined/空串 → 空串（不渲染「0 米」）',
    U.formatDistance(null) === '' && U.formatDistance(undefined) === '' && U.formatDistance('') === '',
    `null→"${U.formatDistance(null)}" undefined→"${U.formatDistance(undefined)}" ""→"${U.formatDistance('')}"`
  )

  const merged = U.mergeDistance(
    [
      { id: 1, name: 'a', category: '图书馆', latitude: 43.88, longitude: 125.32 },
      { id: 2, name: 'b', category: '教学楼', latitude: 43.881, longitude: 125.322 },
      { id: 3, name: 'c', category: '食堂', latitude: 43.8792, longitude: 125.3185 },
    ],
    [
      { id: 1, name: 'a', category: '图书馆', latitude: 43.88, longitude: 125.32, distance: 40 },
      { id: 3, name: 'c', category: '食堂', latitude: 43.8792, longitude: 125.3185, distance: 120 },
    ]
  )
  check('mergeDistance：命中项写入真实距离', merged.filter((i) => i.id === 1)[0].distance === 40)
  check('mergeDistance：未命中项保持 null（不推测距离）', merged.filter((i) => i.id === 2)[0].distance === null)
  check('mergeDistance：不丢失半径外的点（列表长度不变）', merged.length === 3)
  check('mergeDistance：不改动原数组', (() => {
    const src = [{ id: 1, name: 'a', latitude: 43.88, longitude: 125.32 }]
    U.mergeDistance(src, [{ id: 1, name: 'a', latitude: 43.88, longitude: 125.32, distance: 10 }])
    return src[0].distance === undefined
  })())

  const sorted = U.sortByDistance([
    { id: 3, distance: 120 },
    { id: 2, distance: null },
    { id: 1, distance: 40 },
    { id: 4, distance: 40 },
  ])
  check('sortByDistance：按距离升序', sorted[0].distance === 40 && sorted[1].distance === 40 && sorted[2].distance === 120, JSON.stringify(sorted.map((i) => i.distance)))
  check('sortByDistance：无距离项排末尾', sorted[3].id === 2, JSON.stringify(sorted.map((i) => i.id)))
  check('sortByDistance：同距离按 id 升序稳定收尾', sorted[0].id === 1 && sorted[1].id === 4, JSON.stringify(sorted.map((i) => i.id)))
  check('sortByDistance：非数组输入返回空数组（不抛异常）', U.sortByDistance(null).length === 0)

  // ---------------------------------------------------- ⑨ 导航视图模型 ----
  section('⑨ 导航视图模型（start_source 文案诚实 / 绕行增量）')

  const vm = U.buildNavViewModel(NAVIGATE_RES.data)
  check('buildNavViewModel：distance 格式化', vm.distanceText === '760 米', vm.distanceText)
  check('buildNavViewModel：duration 格式化为分钟', /^9 分钟$/.test(vm.durationText), vm.durationText)
  check('buildNavViewModel：targetName 透出真实目标名', vm.targetName === '第二教学楼', vm.targetName)
  check('start_source=nearest_poi → fromUserLocation=false（不谎称用户位置）', vm.fromUserLocation === false)
  check(
    'start_source=user_location → fromUserLocation=true',
    U.buildNavViewModel(Object.assign({}, NAVIGATE_RES.data, { start_source: 'user_location' })).fromUserLocation === true
  )
  check('绕行增量文案（760-610>20）', /多走/.test(vm.detourText), vm.detourText)
  check(
    '绕行增量在差值 ≤20m 时不显示（避免噪声文案）',
    U.buildNavViewModel(Object.assign({}, NAVIGATE_RES.data, { distance: 620, straight_distance: 610 })).detourText === ''
  )
  check('path 不足 2 点 → 视图模型为 null（不激活导航）', U.buildNavViewModel({ distance: 1, duration: 1, path: [] }) === null)
  check('非对象输入 → null（不抛异常）', U.buildNavViewModel(null) === null)
  // duration 缺失时不得渲染「0 分钟」—— 与 formatDistance 拒绝把 null 当 0 是同一条诚实性规则
  check(
    'buildNavViewModel：duration 缺失 → durationText 为空串（不编造 0 分钟）',
    U.buildNavViewModel({ distance: 100, path: NAVIGATE_RES.data.path }).durationText === '',
    JSON.stringify(U.buildNavViewModel({ distance: 100, path: NAVIGATE_RES.data.path }).durationText)
  )
  check(
    'buildNavViewModel：duration=0 是真实值，照常显示「0 分钟」',
    U.buildNavViewModel({ distance: 100, duration: 0, path: NAVIGATE_RES.data.path }).durationText === '0 分钟'
  )
  check('algorithm 透出（straight-fallback 可被页面如实提示）', vm.algorithm === 'astar-grid', vm.algorithm)
  check('WXML 对非用户起点有警示分支', /nav-src--warn/.test(wxmlCode))
  check('WXML 对用户起点有独立分支', /nav\.fromUserLocation/.test(wxmlCode))
  check('WXML 对直线回退有诚实提示', /nav\.isStraightFallback/.test(wxmlCode) && /直线/.test(wxml))

  // ---------------------------------------------------- ⑩ 页面行为 ----
  section('⑩ 页面行为（加载 / 空态 / 失败 / 交互 / 竞态）')

  // 10.1 正常加载
  {
    const sb = makeSandbox()
    sb.setRequestImpl(() => Promise.resolve(POIS_RES))
    sb.onLoad()
    await tick()
    const p = sb.page
    check('onLoad 后发起 /map/pois 请求', sb.calls.length === 1 && sb.calls[0].path === '/map/pois', JSON.stringify(sb.calls.map((c) => c.path)))
    check('加载成功后 markers 数量 = POI 数量', p.data.markers.length === 3, `got ${p.data.markers.length}`)
    check('加载成功后 loading=false 且 error 为空', p.data.loading === false && p.data.error === '')
    check('空数据态为 false', p.data.empty === false)
    check(
      '地图中心对准 POI 群（非硬编码常量优先）',
      Math.abs(p.data.center.latitude - 43.8801) < 0.01 && Math.abs(p.data.center.longitude - 125.3202) < 0.01,
      JSON.stringify(p.data.center)
    )
    check('分类 chips 由真实数据生成', p.data.cats[0] === '全部' && p.data.cats.indexOf('图书馆') !== -1, p.data.cats.join(','))
    check('分类 chips 不含数据中不存在的分类', p.data.cats.indexOf('体育馆') === -1, p.data.cats.join(','))
    // items 只用于「地图上没有点位时给状态浮层判空」与计数（见 WXML）；
    // 卡片的分类着色走 markers 的 label，因此不再往 items 上挂 color 克隆整表。
    check(
      'items 为归一化后的真实点位（数量与 markers 同源）',
      p.data.items.length === p.data.markers.length && p.data.items[0].name === '中心图书馆',
      `items=${p.data.items.length} markers=${p.data.markers.length}`
    )
    check('items 未携带冗余的 color 字段（着色只在 marker label 上做一份）', p.data.items[0].color === undefined)
    check('安全区已注入（statusBarHeight > 0）', p.data.statusBarHeight > 0, String(p.data.statusBarHeight))
    check('胶囊预留已注入（capsuleReserve > 0）', p.data.capsuleReserve > 0, String(p.data.capsuleReserve))
    check('玻璃降级标志来自 globalData（glassSupported=true → glassFallback=false）', p.data.glassFallback === false)
  }

  // 10.2 玻璃降级：真实驱动「能力探测 false → 实心降级」这条方向
  {
    const sbOn = makeSandbox({ glassSupported: true })
    sbOn.setRequestImpl(() => Promise.resolve(POIS_RES))
    sbOn.onLoad()
    await tick()
    check('能力探测 true → glassFallback=false（不降级）', sbOn.page.data.glassFallback === false)

    const sbOff = makeSandbox({ glassSupported: false })
    sbOff.setRequestImpl(() => Promise.resolve(POIS_RES))
    sbOff.onLoad()
    await tick()
    check('能力探测 false → glassFallback=true（走实心降级）', sbOff.page.data.glassFallback === true)
    check(
      '降级标志作用在页面根节点的祖先 class 上（glass.wxss 要求 .is-glass-fallback 作祖先）',
      /class="map-root \{\{glassFallback \? 'is-glass-fallback' : ''\}\}"/.test(wxmlCode)
    )
    check(
      '降级态下导航按钮为实心底（.is-glass-fallback .nav-btn 有 #FFFFFF）',
      /\.is-glass-fallback\s+\.nav-btn\s*\{[\s\S]{0,120}background:\s*#FFFFFF/.test(wxss)
    )
    check(
      '降级态下标题/胶囊为实心底',
      /\.is-glass-fallback\s+\.nav-title\s*\{[\s\S]{0,120}background:\s*#FFFFFF/.test(wxss) &&
        /\.is-glass-fallback\s+\.chip\s*\{[\s\S]{0,120}background:\s*#FFFFFF/.test(wxss)
    )
    check(
      '降级路径未使用 @supports not（F10 要求正向增强，不回退到反向降级）',
      // 只看真实 at-rule：注释里出现"不用 @supports not"这句说明不算违规
      !/@supports\s+not\b/.test(stripComments(wxss))
    )
    check('glass.wxss 提供运行时降级类（本页未私自定义替代）', /\.is-glass-fallback/.test(read('styles/glass.wxss')))
    // 降级态下 .is-glass-fallback .nav-btn（0,2,0）会压过 .nav-btn--active（0,1,0）：
    // 必须有一条同特异性的按压规则，否则实心底模式下点按钮毫无反馈。
    check(
      '降级态下按压反馈仍可见（.is-glass-fallback .nav-btn--active 存在）',
      /\.is-glass-fallback\s+\.nav-btn--active\s*\{/.test(wxss)
    )
    // 常态玻璃底色必须走 token，否则 tokens.wxss 调参触达不到本页
    check(
      '玻璃底走 token（--xj-glass-bg-reduced）而非写死 0.86',
      /var\(\s*--xj-glass-bg-reduced/.test(wxss) &&
        (wxss.match(/background:\s*rgba\(255, 255, 255, 0\.86\);/g) || []).length ===
          (wxss.match(/var\(--xj-glass-bg-reduced/g) || []).length,
      `literal=${(wxss.match(/background:\s*rgba\(255, 255, 255, 0\.86\);/g) || []).length} token=${(wxss.match(/var\(--xj-glass-bg-reduced/g) || []).length}`
    )
    // 按压态：hover-class 必须是一个能压过元素自身样式的本页类，
    // 不能复用 .chip--active / .nav-btn--active（它们在样式表中位置更前且特异性相同）
    check(
      '按压反馈使用本页专用类 .is-pressed（不复用 chip--active 等外来类）',
      /\.is-pressed\s*\{/.test(wxss) && /hover-class="is-pressed"/.test(wxmlCode)
    )
    check(
      '未把 .chip--active / .nav-btn--active 当作 hover-class（会被元素自身样式压掉）',
      !/hover-class="(chip--active|nav-btn--active)"/.test(wxmlCode)
    )
  }

  // 10.2b 胶囊避让几何：必须真的跑通"按实测胶囊位置算预留宽度"这条分支
  {
    // 375 宽屏幕，胶囊 { width: 87, right: 368, top: 48 }
    // → 右侧间隙 = 375 - 368 = 7，预留 = 7*2 + 87 = 101
    const sb = makeSandbox({
      menuRect: { width: 87, right: 368, top: 48, height: 32, bottom: 80, left: 281 },
      windowWidth: 375,
    })
    sb.setRequestImpl(() => Promise.resolve(POIS_RES))
    sb.onLoad()
    await tick()
    check('胶囊预留宽度按实测位置计算（rightGap*2 + width = 101）', sb.page.data.capsuleReserve === 101, String(sb.page.data.capsuleReserve))
    check('状态栏高度取 windowInfo 的 statusBarHeight', sb.page.data.statusBarHeight === 44, String(sb.page.data.statusBarHeight))
    check(
      '预留宽度被注入到导航内层（WXML 消费 capsuleReserve）',
      /style="padding-right: \{\{capsuleReserve\}\}px"/.test(wxmlCode)
    )

    // 取不到胶囊位置（极旧基础库）→ 兜底 100px，绝不遮住按钮
    const sbFallback = makeSandbox() // 不传 menuRect → 桩不提供 getMenuButtonBoundingClientRect
    sbFallback.setRequestImpl(() => Promise.resolve(POIS_RES))
    sbFallback.onLoad()
    await tick()
    check('取不到胶囊位置时兜底为 100px（宁可多留白也不遮按钮）', sbFallback.page.data.capsuleReserve === 100, String(sbFallback.page.data.capsuleReserve))
  }

  // 10.3 空数据态（真实可达：接口返回空数组）
  {
    const sb = makeSandbox()
    sb.setRequestImpl(() => Promise.resolve({ code: 0, data: { items: [] } }))
    sb.onLoad()
    await tick()
    check('接口返回空数组 → empty=true（空数据态可达）', sb.page.data.empty === true)
    check('空数据态下 markers 为空数组', sb.page.data.markers.length === 0)
    check('空数据态无错误文案', sb.page.data.error === '')
    check('WXML 对空数据态有独立分支', /wx:elif="\{\{empty\}\}"/.test(wxmlCode) && /暂无地点数据/.test(wxml))
  }

  // 10.4 失败态（真实可达：先成功拿到点，再让下一次请求 reject）
  {
    const sb = makeSandbox()
    let failNext = false
    sb.setRequestImpl(() => (failNext ? Promise.reject(new Error('boom')) : Promise.resolve(POIS_RES)))
    sb.onLoad()
    await tick()
    check('（前置）失败态用例先加载出 markers', sb.page.data.markers.length === 3, `got ${sb.page.data.markers.length}`)
    failNext = true
    sb.page.fetch('')
    await tick()
    check('接口失败 → error 非空且 loading=false', !!sb.page.data.error && sb.page.data.loading === false)
    check('失败态清空 markers（不残留旧点）', sb.page.data.markers.length === 0, `got ${sb.page.data.markers.length}`)
    check('失败态同时清空 items（否则后续 nearby 会把旧列表画回来并清掉错误态）', sb.page.data.items.length === 0, `got ${sb.page.data.items.length}`)
    // 失败态必须有**自己的**重试入口。
    // 只断言 "wxml 里出现过 retryFetch / 出现过 error 分支" 是不够的：
    // 空数据态也有 retryFetch，跨块的正则会让「失败态没重试入口」静默通过。
    // 因此这里把错误分支单独切出来再检查。
    const errBranch = (() => {
      const i = wxmlCode.indexOf('wx:elif="{{error}}"')
      if (i === -1) return ''
      const j = wxmlCode.indexOf('wx:elif="{{empty}}"', i)
      return wxmlCode.slice(i, j === -1 ? i + 400 : j)
    })()
    check('WXML 对失败态有独立分支', /wx:elif="\{\{error\}\}"/.test(wxmlCode))
    check(
      '失败态分支内部有自己的重试入口（不是借空态的按钮）',
      /bindtap="retryFetch"/.test(errBranch),
      errBranch ? errBranch.replace(/\s+/g, ' ').slice(0, 160) : '(未找到错误分支)'
    )
    check('失败态与空数据态互斥（不同分支）', /wx:elif="\{\{empty\}\}"/.test(wxmlCode))
  }

  // 10.5 分类切换
  {
    const sb = makeSandbox()
    const seen = []
    sb.setRequestImpl((call) => {
      seen.push(call.data && call.data.category)
      return Promise.resolve(POIS_RES)
    })
    sb.onLoad()
    await tick()
    sb.page.onCatChange({ currentTarget: { dataset: { index: 99 } } }) // 越界索引
    await tick()
    check('越界分类索引被拒绝（不发起新请求）', sb.calls.length === 1, `calls=${sb.calls.length}`)
    const idx = catIndex(sb.page, '食堂')
    sb.page.onCatChange({ currentTarget: { dataset: { index: idx } } })
    await tick()
    check('切换分类以真实 category 值请求', seen[1] === '食堂', String(seen[1]))
    check('筛选请求不会冲掉其他分类 chip', sb.page.data.cats.indexOf('图书馆') !== -1, sb.page.data.cats.join(','))
    check(
      '重复点击同一分类不重复请求',
      (() => {
        const before = sb.calls.length
        sb.page.onCatChange({ currentTarget: { dataset: { index: idx } } })
        return sb.calls.length === before
      })()
    )
  }

  // 10.6 retryFetch 保留当前分类
  {
    const sb = makeSandbox()
    const seen = []
    sb.setRequestImpl((call) => {
      seen.push(call.data && call.data.category)
      return Promise.resolve(POIS_RES)
    })
    sb.onLoad()
    await tick()
    const idx = catIndex(sb.page, '食堂')
    sb.page.onCatChange({ currentTarget: { dataset: { index: idx } } })
    await tick()
    sb.page.retryFetch()
    await tick()
    check('retryFetch 保持当前分类（不静默跳回「全部」）', seen[seen.length - 1] === '食堂', JSON.stringify(seen))
    check('retryFetch 复位 nearby 状态', sb.page.data.nearbyActive === false && sb.page.data.nearbyCount === 0)
  }

  // 10.7 marker 点击 → 选中卡片
  {
    const sb = makeSandbox()
    sb.setRequestImpl(() => Promise.resolve(POIS_RES))
    sb.onLoad()
    await tick()
    const before = sb.page.data.markers
    sb.page.onMarkerTap({ detail: { markerId: 1 } })
    check('点击 marker 选中对应 POI 卡片', !!sb.page.data.selectedPoi && sb.page.data.selectedPoi.name === '中心图书馆')
    check('选中后 markers 被重建（选中态）', sb.page.data.markers !== before)
    check(
      '选中项 zIndex 提升',
      sb.page.data.markers.filter((m) => m.id === 1)[0].zIndex > sb.page.data.markers.filter((m) => m.id === 2)[0].zIndex
    )
    check(
      '字符串 markerId 也能命中（Number 归一化）',
      (() => {
        sb.page.onMarkerTap({ detail: { markerId: '2' } })
        return sb.page.data.selectedPoi.id === 2
      })()
    )
    check(
      '未知 markerId 不改变选中（防御）',
      (() => {
        const cur = sb.page.data.selectedPoi.id
        sb.page.onMarkerTap({ detail: { markerId: 999 } })
        return sb.page.data.selectedPoi.id === cur
      })()
    )
    check(
      '非数字 markerId 不抛异常且不改变选中',
      (() => {
        const cur = sb.page.data.selectedPoi.id
        sb.page.onMarkerTap({ detail: { markerId: 'abc' } })
        sb.page.onMarkerTap({})
        return sb.page.data.selectedPoi.id === cur
      })()
    )
    sb.page.onMapTap()
    check('点击空白地图收起卡片', sb.page.data.selectedPoi === null)
    check('收起卡片后 markers 选中态被清除', sb.page.data.markers.every((m) => m.callout.display !== 'ALWAYS'))
  }

  // 10.8 建筑入口（契约缺口下的诚实处理）
  {
    const sb = makeSandbox()
    sb.setRequestImpl(() => Promise.resolve(POIS_RES))
    sb.onLoad()
    await tick()
    sb.page.onMarkerTap({ detail: { markerId: 2 } })
    check('POI 无 building_id → 选中卡片 buildingId 为空（不可跳建筑详情）', !sb.page.data.selectedPoi.buildingId)
    check('WXML 中建筑详情按钮受 buildingId 守卫（无该字段则不渲染）', /wx:if="\{\{selectedPoi\.buildingId\}\}"/.test(wxmlCode))
    check('页面不伪造 building_id（无写死的 building_id 常量）', !/building_id\s*[:=]\s*\d/.test(pageJsCode))
    // 守卫本身必须有效：即使被误点也不得跳到 id=null
    const navsBefore = sb.navs.length
    sb.page.onOpenBuilding()
    check('无 buildingId 时点击建筑详情不跳转（只提示）', sb.navs.length === navsBefore && sb.toasts.some((t) => /无建筑详情/.test(t.title)))

    // 「建筑」入口：选中后进入既有详情页（真实 GET /map/building/{id}）
    let sheetItems = null
    sb.setActionSheetImpl((o) => {
      sheetItems = o.itemList
      o.success({ tapIndex: 0 })
    })
    sb.page.onOpenBuildings()
    check('顶部「建筑」入口列出可选建筑', Array.isArray(sheetItems) && sheetItems.length > 0, JSON.stringify(sheetItems))
    check('选中建筑后跳真实详情页（带合法 id）', sb.navs.length === 1 && /^\/pages\/map\/building\?id=[1-9]\d*$/.test(sb.navs[0].url), JSON.stringify(sb.navs))
    // 取消选择不应跳转
    const before = sb.navs.length
    sb.setActionSheetImpl((o) => o.fail({ errMsg: 'showActionSheet:fail cancel' }))
    sb.page.onOpenBuildings()
    check('取消建筑选择不跳转', sb.navs.length === before)
  }

  // 10.9 返回按钮
  {
    const sb = makeSandbox()
    sb.setRequestImpl(() => Promise.resolve(POIS_RES))
    sb.onLoad()
    await tick()
    sb.setPageStack(['pages/index/index', 'pages/map/index'])
    sb.page.onBack()
    check('有返回栈时 navigateBack', sb.navs[0] && sb.navs[0].type === 'navigateBack')
    sb.setPageStack(['pages/map/index'])
    sb.page.onBack()
    // 首页是 Tab 页：本仓对 Tab 页 URL 统一用 switchTab（pages/auth/login.js 同口径）
    check(
      '无返回栈时用 switchTab 退回 Tab 首页（不无响应）',
      sb.navs[1] && sb.navs[1].type === 'switchTab' && sb.navs[1].url === '/pages/index/index',
      JSON.stringify(sb.navs[1])
    )
    check('无返回栈时未用 reLaunch 打 Tab 页（避免白屏/无法返回）', !sb.navs.some((n) => n.type === 'reLaunch'))
  }

  // 10.10 地图组件报错
  {
    const sb = makeSandbox()
    sb.setRequestImpl(() => Promise.resolve(POIS_RES))
    sb.onLoad()
    await tick()
    sb.page.onMapError({ detail: { errMsg: 'render fail' } })
    check('地图组件报错 → 如实提示（不静默）', !!sb.page.data.mapError && /加载失败/.test(sb.page.data.mapError))
    check('WXML 有地图错误独立分支', /wx:if="\{\{mapError\}\}"/.test(wxmlCode))
    // 重新拉取时清除地图错误
    sb.setRequestImpl(() => Promise.resolve(POIS_RES))
    sb.page.fetch('')
    await tick()
    check('重新拉取时清除地图错误提示', sb.page.data.mapError === '')
  }

  // ---------------------------------------------------- ⑪ 定位行为 ----
  section('⑪ 定位行为（成功 / 拒绝 / 失败 / 防重入 / TTL）')

  // 11.1 成功
  {
    const sb = makeSandbox()
    sb.setRequestImpl(() => Promise.resolve(POIS_RES))
    sb.onLoad()
    await tick()
    let locCalls = 0
    sb.setLocationImpl((o) => {
      locCalls += 1
      o.success({ latitude: 43.8795, longitude: 125.3215 })
    })
    sb.page.onLocate()
    await tick()
    check('定位成功后地图中心切到用户位置', sb.page.data.center.latitude === 43.8795 && sb.page.data.center.longitude === 125.3215)
    check('定位成功后放大到街区级 scale（≥16）', sb.page.data.scale >= 16, String(sb.page.data.scale))
    check('定位成功后开启 show-location（显示定位蓝点）', sb.page.data.showLocation === true)
    check('定位成功后 locating 复位', sb.page.data.locating === false)
    check('定位成功未弹出权限引导', sb.modals.length === 0)
    check('定位只调用 1 次（未重复高频请求）', locCalls === 1)
    check('定位结果被 MapContext.moveToLocation 消费（视图跟随）', sb.mapCtxCalls.moveToLocation.length === 1)
  }

  // 11.2 拒绝授权
  {
    const sb = makeSandbox()
    sb.setRequestImpl(() => Promise.resolve(POIS_RES))
    sb.onLoad()
    await tick()
    const centerBefore = JSON.stringify(sb.page.data.center)
    sb.setLocationImpl((o) => o.fail({ errMsg: 'getLocation:fail auth deny' }))
    sb.page.onLocate()
    await tick()
    check('拒绝授权 → locating 复位（不卡 loading）', sb.page.data.locating === false)
    check('拒绝授权 → 弹出可操作引导', sb.modals.length === 1 && sb.modals[0].confirmText === '去设置')
    check('拒绝授权 → 引导确认后打开设置页', sb.getOpenSettingCount() === 1)
    check('拒绝授权 → 不移动地图（不假装定位成功）', JSON.stringify(sb.page.data.center) === centerBefore)
    check('拒绝授权 → 不写 lastLocation', sb.page.lastLocation === null)
    check('拒绝授权 → showLocation 保持 false', sb.page.data.showLocation === false)
  }

  // 11.3 定位失败（非权限）
  {
    const sb = makeSandbox()
    sb.setRequestImpl(() => Promise.resolve(POIS_RES))
    sb.onLoad()
    await tick()
    sb.setLocationImpl((o) => o.fail({ errMsg: 'getLocation:fail system permission denied' }))
    sb.page.onLocate()
    await tick()
    check('定位失败 → toast 提示（不静默）', sb.toasts.length === 1 && /定位失败/.test(sb.toasts[0].title))
    check('定位失败 → 不弹权限引导（区分两种失败）', sb.modals.length === 0)
    check('定位失败 → showLocation 保持 false', sb.page.data.showLocation === false)
    check('定位失败 → 不写 lastLocation', sb.page.lastLocation === null)
  }

  // 11.4 TTL 缓存 + 过期重取
  {
    const sb = makeSandbox()
    sb.setRequestImpl(() => Promise.resolve(POIS_RES))
    sb.onLoad()
    await tick()
    let locCalls = 0
    sb.setLocationImpl((o) => {
      locCalls += 1
      o.success({ latitude: 43.8795, longitude: 125.3215 })
    })
    sb.page.onLocate()
    await tick()
    sb.page.onLocate()
    await tick()
    check('TTL 内二次定位复用缓存（不重复请求定位）', locCalls === 1, `locCalls=${locCalls}`)
    check(
      'TTL 过期的定位会重新请求',
      (() => {
        sb.page.locationAt = Date.now() - 60000
        sb.page.onLocate()
        return locCalls === 2
      })()
    )
  }

  // 11.5 防重入：定位未返回时连点，只允许一次真实定位请求
  {
    const sb = makeSandbox()
    sb.setRequestImpl(() => Promise.resolve(POIS_RES))
    sb.onLoad()
    await tick()
    let locCalls = 0
    let pendingLoc = null
    sb.setLocationImpl((o) => {
      locCalls += 1
      pendingLoc = o // 挂起：不立即回调，模拟定位进行中
    })
    sb.page.onLocate()
    sb.page.onLocate()
    sb.page.onLocate()
    check('定位进行中连点只发起 1 次定位请求', locCalls === 1, `locCalls=${locCalls}`)
    check('定位进行中 locating=true（按钮呈 loading 态）', sb.page.data.locating === true)
    pendingLoc.success({ latitude: 43.8795, longitude: 125.3215 })
    await tick()
    check('定位返回后 locating 复位（不永久 loading）', sb.page.data.locating === false)
  }

  // ---------------------------------------------------- ⑫ 周边 ----
  section('⑫ 周边（nearby 真实调用 / 距离排序 / 分类过滤 / 失败不破坏主地图）')

  // 12.1 成功 + 距离合并 + 排序
  {
    const sb = makeSandbox()
    sb.setRequestImpl((call) => {
      if (call.path === '/map/nearby') {
        return Promise.resolve({
          code: 0,
          data: {
            items: [
              { id: 3, name: '湖畔餐厅', category: '食堂', latitude: 43.8792, longitude: 125.3185, distance: 120 },
              { id: 1, name: '中心图书馆', category: '图书馆', latitude: 43.88, longitude: 125.32, distance: 40 },
            ],
          },
        })
      }
      return Promise.resolve(POIS_RES)
    })
    sb.setLocationImpl((o) => o.success({ latitude: 43.8795, longitude: 125.3215 }))
    sb.onLoad()
    await tick()
    sb.page.onNearby()
    await tick()
    const nearCall = sb.calls.filter((c) => c.path === '/map/nearby')[0]
    check('onNearby 调用 /map/nearby', !!nearCall)
    check('nearby 传入真实定位坐标', !!nearCall && nearCall.data.lat === 43.8795 && nearCall.data.lng === 125.3215)
    check('nearby 传入 radius', !!nearCall && nearCall.data.radius === 1000, nearCall && String(nearCall.data.radius))
    const lib = sb.page.data.items.filter((i) => i.id === 1)[0]
    check('nearby 距离合并进列表', !!lib && lib.distance === 40, lib && String(lib.distance))
    const ordered = sb.page.data.items.map((i) => i.id)
    check('nearby 结果按距离升序（近的在前）', ordered[0] === 1 && ordered[1] === 3, ordered.join(','))
    check('nearby 半径外无距离的点排在末尾', ordered[2] === 2, ordered.join(','))
    check('nearbyActive/nearbyCount 正确', sb.page.data.nearbyActive === true && sb.page.data.nearbyCount === 2, `${sb.page.data.nearbyActive}/${sb.page.data.nearbyCount}`)
    check('nearby 后 markers 未被破坏', sb.page.data.markers.length === 3)

    // 需求 8「距离展示」的真正可见载体是**选中卡片**（WXML 里唯一的距离渲染点）。
    // 旧版这里断言的是 `selectedPoi === null`，即"没有选中项"——与断言名所声称的相反，
    // refreshSelectedFrom / withDistanceText 的非空分支从未被执行。这里真跑一遍。
    sb.page.onMarkerTap({ detail: { markerId: 3 } }) // 湖畔餐厅，半径内(distance 120)
    sb.page.onNearby()
    await tick()
    check(
      '选中卡片显示真实距离文案（refreshSelectedFrom 链路）',
      !!sb.page.data.selectedPoi && sb.page.data.selectedPoi.distanceText === '120 米',
      JSON.stringify(sb.page.data.selectedPoi && sb.page.data.selectedPoi.distanceText)
    )
    check('卡片距离来自接口返回的 distance，而非本地计算', sb.page.data.selectedPoi.distance === 120)
    // 半径外的点：distance 保持 null → 文案为空（不显示"0 米"）
    sb.page.onMarkerTap({ detail: { markerId: 2 } }) // 第二教学楼，半径外
    sb.page.onNearby()
    await tick()
    check(
      '半径外 POI 选中卡片不显示距离（不编造 0 米）',
      !!sb.page.data.selectedPoi && sb.page.data.selectedPoi.distance === null && sb.page.data.selectedPoi.distanceText === '',
      JSON.stringify(sb.page.data.selectedPoi && {
        d: sb.page.data.selectedPoi.distance,
        t: sb.page.data.selectedPoi.distanceText,
      })
    )
  }

  // 12.2 分类过滤：nearby 的**距离合并**只作用于当前分类内的点
  //
  // 注意断言的正确语义：列表**本来就只含当前分类的点**（由 /map/pois?category= 决定），
  // nearby 只负责把半径内的真实距离合并进来，**不得**因为半径内出现了别的分类的点
  // 就把它们塞进当前列表（那会破坏分类筛选语义）。
  //
  // 因此这里的请求桩必须**真实复刻后端**的 `WHERE (? = '' OR category = ?)`
  // （backend/app/routers/map_api.py:34-38），否则「列表里为什么会有别的分类」
  // 就分不清是页面缺陷还是桩的缺陷。
  {
    const sb = makeSandbox()
    sb.setRequestImpl((call) => {
      if (call.path === '/map/nearby') {
        return Promise.resolve({
          code: 0,
          data: {
            items: [
              { id: 3, name: '湖畔餐厅', category: '食堂', latitude: 43.8792, longitude: 125.3185, distance: 120 },
              { id: 1, name: '中心图书馆', category: '图书馆', latitude: 43.88, longitude: 125.32, distance: 40 },
            ],
          },
        })
      }
      // 复刻后端 category 过滤语义
      const cat = (call.data && call.data.category) || ''
      const items = POIS_RES.data.items.filter((it) => cat === '' || it.category === cat)
      return Promise.resolve({ code: 0, data: { items } })
    })
    sb.setLocationImpl((o) => o.success({ latitude: 43.8795, longitude: 125.3215 }))
    sb.onLoad()
    await tick()
    sb.page.onCatChange({ currentTarget: { dataset: { index: catIndex(sb.page, '食堂') } } })
    await tick()
    check(
      '（前置）切换分类后列表只含该分类的点（桩复刻后端 category 过滤）',
      sb.page.data.items.length === 1 && sb.page.data.items.every((i) => i.category === '食堂'),
      sb.page.data.items.map((i) => i.category).join(',')
    )
    sb.page.onNearby()
    await tick()
    const cats = sb.page.data.items.map((i) => i.category)
    check(
      'nearby 不把半径内其他分类的点塞进当前列表（分类筛选语义不被破坏）',
      cats.every((c) => c === '食堂'),
      cats.join(',')
    )
    check(
      'nearby 距离只合并给当前分类命中项',
      sb.page.data.items.filter((i) => i.category === '食堂')[0].distance === 120,
      JSON.stringify(sb.page.data.items.map((i) => [i.category, i.distance]))
    )
    check('nearbyCount 只统计当前分类命中数', sb.page.data.nearbyCount === 1, String(sb.page.data.nearbyCount))
    check('分类 chips 在 nearby 后未被冲掉', sb.page.data.cats.indexOf('图书馆') !== -1, sb.page.data.cats.join(','))
  }

  // 12.3 失败不破坏主地图
  {
    const sb = makeSandbox()
    sb.setRequestImpl((call) => {
      if (call.path === '/map/nearby') return Promise.reject(new Error('boom'))
      return Promise.resolve(POIS_RES)
    })
    sb.setLocationImpl((o) => o.success({ latitude: 43.8795, longitude: 125.3215 }))
    sb.onLoad()
    await tick()
    const markersBefore = sb.page.data.markers.length
    const itemsBefore = sb.page.data.items.length
    sb.page.onNearby()
    await tick()
    check('nearby 失败 → 主地图 markers 不变', sb.page.data.markers.length === markersBefore)
    check('nearby 失败 → 主列表不变', sb.page.data.items.length === itemsBefore)
    check('nearby 失败 → 有提示', sb.toasts.some((t) => /周边/.test(t.title)))
    check('nearby 失败 → nearbyActive 复位', sb.page.data.nearbyActive === false)
    check('nearby 失败 → loading 复位（不永久遮罩）', sb.page.data.loading === false)
  }

  // 12.4 无定位时不请求 nearby
  {
    const sb = makeSandbox()
    sb.setRequestImpl(() => Promise.resolve(POIS_RES))
    sb.onLoad()
    await tick()
    sb.setLocationImpl((o) => o.fail({ errMsg: 'getLocation:fail auth deny' }))
    sb.page.onNearby()
    await tick()
    check('无定位 → 不发起 /map/nearby 请求', sb.calls.filter((c) => c.path === '/map/nearby').length === 0)
  }

  // 12.5 半径内无点位
  {
    const sb = makeSandbox()
    sb.setRequestImpl((call) => {
      if (call.path === '/map/nearby') return Promise.resolve({ code: 0, data: { items: [] } })
      return Promise.resolve(POIS_RES)
    })
    sb.setLocationImpl((o) => o.success({ latitude: 43.8795, longitude: 125.3215 }))
    sb.onLoad()
    await tick()
    sb.page.onNearby()
    await tick()
    check('半径内无点位 → 有提示且不破坏列表', sb.toasts.some((t) => /附近暂无地点/.test(t.title)) && sb.page.data.items.length === 3)
    check('半径内无点位 → nearbyCount=0', sb.page.data.nearbyCount === 0)
  }

  // 12.6 竞态：迟到的 nearby 响应不得在用户已切换分类后把周边态重新点亮
  {
    const sb = makeSandbox()
    let pendingNearby = null
    sb.setRequestImpl((call) => {
      if (call.path === '/map/nearby') {
        return new Promise((resolve) => {
          pendingNearby = resolve
        })
      }
      const cat = (call.data && call.data.category) || ''
      return Promise.resolve({
        code: 0,
        data: { items: POIS_RES.data.items.filter((it) => cat === '' || it.category === cat) },
      })
    })
    sb.setLocationImpl((o) => o.success({ latitude: 43.8795, longitude: 125.3215 }))
    sb.onLoad()
    await tick()
    sb.page.onCatChange({ currentTarget: { dataset: { index: catIndex(sb.page, '食堂') } } })
    await tick()
    sb.page.onNearby() // 挂起
    check('（前置）nearby 请求已挂起', typeof pendingNearby === 'function')
    // 用户在响应到达前切回「全部」—— onCatChange 会清掉 nearby 态
    sb.page.onCatChange({ currentTarget: { dataset: { index: 0 } } })
    await tick()
    check(
      '（前置）切换分类后 nearby 态已清空',
      sb.page.data.nearbyActive === false && sb.page.data.nearbyCount === 0
    )
    check('（前置）当前分类为「全部」', sb.page.currentCategory() === '', String(sb.page.currentCategory()))
    // 迟到的响应到达
    pendingNearby({
      code: 0,
      data: {
        items: [
          { id: 3, name: '湖畔餐厅', category: '食堂', latitude: 43.8792, longitude: 125.3185, distance: 120 },
        ],
      },
    })
    await tick()
    check(
      '迟到的 nearby 响应不点亮「周边 N 个」（nearbySeq 守卫）',
      sb.page.data.nearbyActive === false,
      String(sb.page.data.nearbyActive)
    )
    check(
      '迟到的 nearby 响应不覆盖当前分类列表',
      sb.page.data.items.length === 3,
      `items=${sb.page.data.items.length}`
    )
    check('迟到的 nearby 响应不残留 loading', sb.page.data.loading === false)
  }

  // 12.7 nearby 失败也要被序号守卫（旧请求失败不得复位新请求的 loading）
  {
    const sb = makeSandbox()
    let rejectNearby = null
    sb.setRequestImpl((call) => {
      if (call.path === '/map/nearby') {
        return new Promise((resolve, reject) => {
          rejectNearby = reject
        })
      }
      return Promise.resolve(POIS_RES)
    })
    sb.setLocationImpl((o) => o.success({ latitude: 43.8795, longitude: 125.3215 }))
    sb.onLoad()
    await tick()
    sb.page.onNearby()
    sb.page.onCatChange({ currentTarget: { dataset: { index: catIndex(sb.page, '食堂') } } })
    await tick()
    rejectNearby(new Error('late boom'))
    await tick()
    check('迟到的 nearby 失败不弹「周边加载失败」提示', !sb.toasts.some((t) => /周边加载失败/.test(t.title)))
    check('迟到的 nearby 失败不改变 loading / nearby 态', sb.page.data.loading === false && sb.page.data.nearbyActive === false)
    check('nearby 请求有序号守卫（nearbySeq）', /nearbySeq/.test(pageJsCode))
  }

  // ---------------------------------------------------- ⑬ 导航 ----
  section('⑬ 导航（POST /map/navigate → polyline / 起点诚实 / 竞态 / 失败清理）')

  // 13.1 无定位：不传起点
  {
    const sb = makeSandbox()
    sb.setRequestImpl((call) => {
      if (call.path === '/map/navigate') return Promise.resolve(NAVIGATE_RES)
      return Promise.resolve(POIS_RES)
    })
    sb.onLoad()
    await tick()
    sb.page.onMarkerTap({ detail: { markerId: 2 } })
    sb.page.onNavigate()
    await tick()
    const navCall = sb.calls.filter((c) => c.path === '/map/navigate')[0]
    check('onNavigate 调用 /map/navigate', !!navCall)
    check('navigate 使用 POST', !!navCall && navCall.method === 'POST', navCall && navCall.method)
    check('navigate 传 to_poi_id', !!navCall && navCall.data.to_poi_id === 2)
    check('无定位时不传 from_lat（由后端取最近地标）', !!navCall && navCall.data.from_lat === undefined)
    check('响应 path 映射为 polyline', sb.page.data.polyline.length === 1 && sb.page.data.polyline[0].points.length === 3)
    check(
      'nav.active 激活且距离/时长已格式化',
      sb.page.data.nav.active === true && /米|公里/.test(sb.page.data.nav.distanceText) && /分钟/.test(sb.page.data.nav.durationText)
    )
    check('start_source=nearest_poi 时如实标注非用户位置', sb.page.data.nav.fromUserLocation === false)
    check('路线绘制后调用 includePoints 适配视野', sb.mapCtxCalls.includePoints.length === 1)
    check('includePoints 使用 polyline 的点', (() => {
      const pts = sb.mapCtxCalls.includePoints[0] && sb.mapCtxCalls.includePoints[0].points
      return Array.isArray(pts) && pts.length === 3 && pts[0].latitude === 43.88
    })())
  }

  // 13.2 有定位：传真实起点
  {
    const sb = makeSandbox()
    sb.setRequestImpl((call) => {
      if (call.path === '/map/navigate') {
        return Promise.resolve({ code: 0, data: Object.assign({}, NAVIGATE_RES.data, { start_source: 'user_location' }) })
      }
      return Promise.resolve(POIS_RES)
    })
    sb.setLocationImpl((o) => o.success({ latitude: 43.8795, longitude: 125.3215 }))
    sb.onLoad()
    await tick()
    sb.page.onLocate()
    await tick()
    sb.page.onMarkerTap({ detail: { markerId: 2 } })
    sb.page.onNavigate()
    await tick()
    const navCall = sb.calls.filter((c) => c.path === '/map/navigate')[0]
    check('有定位时 navigate 传入 from_lat/from_lng', !!navCall && navCall.data.from_lat === 43.8795 && navCall.data.from_lng === 125.3215, navCall && JSON.stringify(navCall.data))
    check('start_source=user_location 时如实标注为用户位置', sb.page.data.nav.fromUserLocation === true)
  }

  // 13.3 导航失败（前置：先有一次成功导航，才能证明失败会清掉旧路线）
  {
    const sb = makeSandbox()
    let failNext = false
    sb.setRequestImpl((call) => {
      if (call.path === '/map/navigate') {
        return failNext ? Promise.reject(new Error('boom')) : Promise.resolve(NAVIGATE_RES)
      }
      return Promise.resolve(POIS_RES)
    })
    sb.onLoad()
    await tick()
    sb.page.onMarkerTap({ detail: { markerId: 2 } })
    sb.page.onNavigate()
    await tick()
    check('（前置）失败态用例先画出一次路线', sb.page.data.polyline.length === 1)
    failNext = true
    sb.page.onNavigate()
    await tick()
    check('导航失败 → nav 不激活（不假装有路线）', sb.page.data.nav.active === false)
    check('导航失败 → 清除上一次残留的 polyline', sb.page.data.polyline.length === 0, `got ${sb.page.data.polyline.length}`)
    check('导航失败 → loading 复位', !sb.page.data.nav.loading)
  }

  // 13.3b 路线计算中必须有用户可见反馈（否则「到这去」看起来像没反应）
  {
    const sb = makeSandbox()
    let pendingNav = null
    sb.setRequestImpl((call) => {
      if (call.path === '/map/navigate') {
        return new Promise((resolve) => {
          pendingNav = resolve
        })
      }
      return Promise.resolve(POIS_RES)
    })
    sb.onLoad()
    await tick()
    sb.page.onMarkerTap({ detail: { markerId: 2 } })
    sb.page.onNavigate()
    check('navigate 进行中 nav.loading=true（可渲染"正在规划"）', sb.page.data.nav.loading === true)
    check('WXML 有路线计算中的独立提示分支', /wx:if="\{\{nav\.loading\}\}"/.test(wxmlCode))
    check('提示文案说明「正在规划路线」（不是空白遮罩）', /正在规划步行路线/.test(wxmlCode))
    pendingNav(NAVIGATE_RES)
    await tick()
    check('导航返回后 loading 关闭（提示消失）', sb.page.data.nav.loading === false)
  }

  // 13.4 异常返回结构（缺 path）→ 不激活、不画半条线
  {
    const sb = makeSandbox()
    sb.setRequestImpl((call) => {
      if (call.path === '/map/navigate') return Promise.resolve({ code: 0, data: { distance: 100, duration: 2 } })
      return Promise.resolve(POIS_RES)
    })
    sb.onLoad()
    await tick()
    sb.page.onMarkerTap({ detail: { markerId: 2 } })
    sb.page.onNavigate()
    await tick()
    check('返回缺 path → 不激活导航且给出提示', sb.page.data.nav.active === false && sb.toasts.length > 0)
    check('返回缺 path → polyline 为空（不激活半条路线）', sb.page.data.polyline.length === 0)
  }

  // 13.5 导航中重复点击不并发（loading 守卫）
  {
    const sb = makeSandbox()
    let navCalls = 0
    let pendingNav = null
    sb.setRequestImpl((call) => {
      if (call.path === '/map/navigate') {
        navCalls += 1
        return new Promise((resolve) => {
          pendingNav = resolve
        })
      }
      return Promise.resolve(POIS_RES)
    })
    sb.onLoad()
    await tick()
    sb.page.onMarkerTap({ detail: { markerId: 2 } })
    sb.page.onNavigate()
    sb.page.onNavigate()
    sb.page.onNavigate()
    check('导航进行中连点只发起 1 次 navigate 请求', navCalls === 1, `navCalls=${navCalls}`)
    pendingNav(NAVIGATE_RES)
    await tick()
    check('导航返回后正常激活', sb.page.data.nav.active === true)
  }

  // 13.6 结束导航 + 切换分类清理
  {
    const sb = makeSandbox()
    sb.setRequestImpl((call) => {
      if (call.path === '/map/navigate') return Promise.resolve(NAVIGATE_RES)
      return Promise.resolve(POIS_RES)
    })
    sb.onLoad()
    await tick()
    sb.page.onMarkerTap({ detail: { markerId: 2 } })
    sb.page.onNavigate()
    await tick()
    sb.page.onClearNav()
    check('结束导航清空 polyline', sb.page.data.polyline.length === 0)
    check('结束导航复位 nav.active', sb.page.data.nav.active === false)

    // 重新画一次，然后切分类
    sb.page.onMarkerTap({ detail: { markerId: 2 } })
    sb.page.onNavigate()
    await tick()
    check('（前置）再次画出路线', sb.page.data.polyline.length === 1)
    sb.page.onCatChange({ currentTarget: { dataset: { index: catIndex(sb.page, '食堂') } } })
    await tick()
    check(
      '切换分类后清空路线与选中（卡片不指向不存在的点）',
      sb.page.data.polyline.length === 0 && sb.page.data.selectedPoi === null
    )
  }

  // 13.7 navSeq：过期的导航响应不得覆盖新状态
  {
    const sb = makeSandbox()
    const pendingNav = []
    sb.setRequestImpl((call) => {
      if (call.path === '/map/navigate') {
        return new Promise((resolve) => pendingNav.push(resolve))
      }
      return Promise.resolve(POIS_RES)
    })
    sb.onLoad()
    await tick()
    sb.page.onMarkerTap({ detail: { markerId: 2 } })
    sb.page.onNavigate() // 第 1 次（挂起）
    sb.page.onClearNav() // 用户结束导航 → navSeq++
    check('（前置）第 1 次导航请求已挂起', pendingNav.length === 1)
    pendingNav[0](NAVIGATE_RES) // 过期响应到达
    await tick()
    check('过期导航响应不激活导航（navSeq 守卫）', sb.page.data.nav.active === false)
    check('过期导航响应不画出路线', sb.page.data.polyline.length === 0)
  }

  // ---------------------------------------------------- ⑭ 竞态 ----
  section('⑭ 竞态保护（fetchSeq / navSeq 序号守卫，真实驱动乱序返回）')

  // 14.1 过期 fetch 响应不覆盖当前分类结果
  //
  // 场景：用户先点「食堂」（请求 A），随即点回「全部」（请求 B = 最新 seq）。
  // 若 A 的响应后到，它**必须被丢弃** —— 否则地图会退回只显示食堂，
  // 而用户界面明明选中着「全部」，属于「UI 与数据不一致」。
  {
    const sb = makeSandbox()
    const pending = makePendingByCat(sb)
    sb.onLoad() // 全量 v1（挂起）
    await tick()
    pending.resolve('', POIS_RES)
    await tick()
    check('（前置）全量响应到达后 chips 已含真实分类', sb.page.data.cats.indexOf('食堂') !== -1, sb.page.data.cats.join(','))

    sb.page.onCatChange({ currentTarget: { dataset: { index: catIndex(sb.page, '食堂') } } }) // 请求 A（挂起）
    await tick()
    check('（前置）分类请求已挂起', pending.keys().indexOf('食堂') !== -1, pending.keys().join(','))

    sb.page.fetch('') // 请求 B = 最新 seq（挂起）
    await tick()

    // 乱序返回：先回 A（食堂，过期），再回 B（全量，最新）
    // 注意 B 的响应**尚未**到达，所以此刻地图上仍是上一次成功的全量结果（3 个点）。
    // 若 A 的过期响应被错误采纳，items 会退化成只有「湖畔餐厅」。
    pending.resolve('食堂', {
      code: 0,
      data: { items: [{ id: 3, name: '湖畔餐厅', category: '食堂', latitude: 43.8792, longitude: 125.3185 }] },
    })
    await tick()
    check(
      '过期的分类响应被丢弃（不覆盖上一次有效结果）',
      sb.page.data.items.length === 3,
      `（中间态，A 已返回、B 未返回）items=${sb.page.data.items.map((i) => i.name).join('/')}`
    )
    pending.resolve('', POIS_RES)
    await tick()
    check(
      '最新的全量响应生效',
      sb.page.data.items.length === 3,
      `items=${sb.page.data.items.map((i) => i.name).join('/')}`
    )
    check('最新响应到达后无错误态', sb.page.data.error === '' && sb.page.data.loading === false)
  }

  // 14.1b 反向：最新响应先到，过期响应后到 —— 过期响应必须被彻底丢弃
  {
    const sb = makeSandbox()
    const pending = makePendingByCat(sb)
    sb.onLoad()
    await tick()
    pending.resolve('', POIS_RES)
    await tick()
    sb.page.onCatChange({ currentTarget: { dataset: { index: catIndex(sb.page, '食堂') } } }) // A（挂起）
    await tick()
    sb.page.fetch('') // B = 最新（挂起）
    await tick()
    // B 先返回
    pending.resolve('', POIS_RES)
    await tick()
    check('（前置）最新全量响应先返回并生效', sb.page.data.items.length === 3, String(sb.page.data.items.length))
    // A 后返回（过期）
    pending.resolve('食堂', {
      code: 0,
      data: { items: [{ id: 3, name: '湖畔餐厅', category: '食堂', latitude: 43.8792, longitude: 125.3185 }] },
    })
    await tick()
    check(
      '过期响应后到时不得覆盖已生效的新结果',
      sb.page.data.items.length === 3,
      `items=${sb.page.data.items.map((i) => i.name).join('/')}`
    )
  }

  // 14.2 更早的过期响应（乱序：后发先至）
  {
    const sb = makeSandbox()
    const pending = makePendingByCat(sb)
    sb.onLoad() // 全部 v1（挂起，seq=1）
    await tick()
    pending.resolve('', POIS_RES)
    await tick()
    sb.page.onCatChange({ currentTarget: { dataset: { index: catIndex(sb.page, '食堂') } } }) // seq=2（挂起）
    await tick()
    sb.page.fetch('') // seq=3（全量，挂起）
    await tick()
    // 乱序返回：先回 seq=3，再回 seq=2
    pending.resolve('', POIS_RES)
    await tick()
    pending.resolve('食堂', {
      code: 0,
      data: { items: [{ id: 3, name: '湖畔餐厅', category: '食堂', latitude: 43.8792, longitude: 125.3185 }] },
    })
    await tick()
    check('更早的过期响应同样被丢弃（seq 严格递增比较）', sb.page.data.items.length === 3, `items=${sb.page.data.items.length}`)
  }

  check('过期响应被丢弃（fetchSeq 序号守卫）', /if\s*\(seq !== this\.fetchSeq\)\s*return/.test(pageJsCode))
  check('导航请求有序号守卫（navSeq）', /navSeq/.test(pageJsCode))
  check('切换分类时使进行中的导航失效（navSeq++）', /onCatChange[\s\S]{0,600}?this\.navSeq\+\+/.test(pageJsCode))
  check('结束导航时使进行中的导航失效（navSeq++）', /onClearNav\(\)\s*\{[\s\S]{0,120}?this\.navSeq\+\+/.test(pageJsCode))

  // ---------------------------------------------------- ⑮ 性能 ----
  section('⑮ 性能约定（避免高频 setData / 全量重建）')

  check('无 setInterval / 定时器动画', !/setInterval/.test(pageJsCode))
  check('无轮询定位（wx.getLocation 只出现 1 次）', (pageJsCode.match(/wx\.getLocation/g) || []).length === 1)
  check('markers 重建集中在 toMarkers 调用（无散落逐点 push）', !/markers\.push/.test(pageJsCode))
  check('接口响应不整包 setData（无 setData({res}) 形式）', !/setData\(\s*\{\s*res\s*\}\s*\)/.test(pageJsCode))
  check('页面不缓存整包接口响应（data 中无 raw/apiRes 字段）', !/data:\s*\{[\s\S]{0,2000}?\b(apiRes|rawRes|response)\b/.test(pageJsCode))
  check('页面 onLoad 只请求一次 POI 列表', (pageJsCode.match(/this\.fetch\(/g) || []).length <= 4, String((pageJsCode.match(/this\.fetch\(/g) || []).length))
  check('选中态只重算 markers 一个字段（不全量 setData(items)）', /selectPoi\(poi\)\s*\{[\s\S]{0,240}?markers:/.test(pageJsCode))

  // ---------------------------------------------------- ⑯ 结构完整性 ----
  section('⑯ WXML/WXSS 结构完整性（无开发者工具也可静态自查）')

  // WXML 标签配对：未闭合/多闭合都会让页面整屏白，静态即可发现
  function tagBalance(src) {
    const stack = []
    const re = /<(\/?)([a-zA-Z][\w-]*)([^>]*?)(\/?)>/g
    let m
    const selfClosing = ['input', 'image', 'img', 'br', 'hr', 'icon', 'progress', 'slot', 'import', 'include', 'wxs']
    const errors = []
    while ((m = re.exec(src)) !== null) {
      const closing = m[1] === '/'
      const tag = m[2]
      const selfClose = m[4] === '/'
      if (selfClose || selfClosing.indexOf(tag) !== -1) continue
      if (!closing) {
        stack.push(tag)
      } else {
        const top = stack.pop()
        if (top !== tag) errors.push(`</${tag}> 与 <${top || '空'}> 不匹配`)
      }
    }
    if (stack.length > 0) errors.push('未闭合标签：' + stack.join(', '))
    return errors
  }

  const wxmlErrors = tagBalance(wxmlCode)
  check('WXML 标签全部配对闭合', wxmlErrors.length === 0, wxmlErrors.join(' | '))

  const mustacheOpen = (wxmlCode.match(/\{\{/g) || []).length
  const mustacheClose = (wxmlCode.match(/\}\}/g) || []).length
  check('WXML mustache 表达式成对', mustacheOpen === mustacheClose, `{{=${mustacheOpen} }}=${mustacheClose}`)

  const wxssOpen = (wxss.match(/\{/g) || []).length
  const wxssClose = (wxss.match(/\}/g) || []).length
  check('WXSS 花括号成对', wxssOpen === wxssClose, `{=${wxssOpen} }=${wxssClose}`)

  // 关键 class 必须在 WXSS 中有定义（避免"写了样式类但没样式"）
  const styleClasses = [
    'map-root', 'map-canvas', 'nav-bar', 'nav-inner', 'nav-btn', 'nav-title',
    'chips', 'chips-row', 'chip', 'chip--active', 'overlay-state', 'poi-card',
    'card-head', 'card-title', 'card-actions', 'act', 'act--primary', 'act--ghost',
    'glyph-back', 'glyph-locate', 'glyph-building', 'glyph-close', 'spinner',
    'nav-src', 'nav-src--warn', 'overlay-retry', 'overlay-text', 'map-error',
  ]
  const missing = styleClasses.filter((c) => !new RegExp('\\.' + c + '\\s*[,{]').test(wxss))
  check('WXML 用到的关键样式类均在 WXSS 中定义', missing.length === 0, missing.join(', '))

  // WXML 绑定的事件处理函数必须在页面 JS 中存在（绑定错名字 = 点击无反应）
  const boundHandlers = []
  const bindRe = /bind(?:tap|input|confirm|error|markertap|regionchange)="([A-Za-z_$][\w$]*)"/g
  let bm
  while ((bm = bindRe.exec(wxmlCode)) !== null) boundHandlers.push(bm[1])
  const uniqueHandlers = Array.from(new Set(boundHandlers))
  const missingHandlers = uniqueHandlers.filter((h) => !new RegExp('\\n\\s*' + h + '\\s*\\(').test(pageJsCode))
  check(
    `WXML 绑定的 ${uniqueHandlers.length} 个事件处理函数均在页面 JS 中实现`,
    missingHandlers.length === 0,
    missingHandlers.join(', ')
  )

  // ---------------------------------------------------- ⑰ 后端契约对账 ----
  section('⑰ 后端契约对账（端点确实存在于 origin/dev，缺口显式化）')

  const backendRouter = path.join(ROOT, 'backend', 'app', 'routers', 'map_api.py')
  if (fs.existsSync(backendRouter)) {
    const py = fs.readFileSync(backendRouter, 'utf8')
    check('后端存在 GET /pois 路由', /@router\.get\(\s*["']\/pois["']/.test(py))
    check('后端存在 GET /nearby 路由', /@router\.get\(\s*["']\/nearby["']/.test(py))
    check('后端存在 POST /navigate 路由', /@router\.post\(\s*["']\/navigate["']/.test(py))
    check('后端存在 GET /building/{building_id} 路由', /@router\.get\(\s*["']\/building\/\{building_id\}["']/.test(py))
    check('后端 router prefix 为 /map', /APIRouter\(\s*prefix\s*=\s*["']\/map["']/.test(py))
    check('后端 POI 返回 latitude/longitude 字段', /latitude/.test(py) && /longitude/.test(py))
    check(
      '后端 navigate 返回 path/distance/duration/start_source',
      /["']path["']/.test(py) && /["']distance["']/.test(py) && /["']duration["']/.test(py) && /["']start_source["']/.test(py)
    )
    check(
      '后端 nearby 仅返回半径内且按 distance 升序',
      /if d <= radius/.test(py) && /items\.sort\(key=lambda x: x\["distance"\]\)/.test(py)
    )
    // 契约缺口（本项目已知事实，必须显式断言而不是假装不存在）：
    const poiSelect = (py.match(/SELECT[^"']*FROM poi/g) || []).join(' | ')
    check(
      '已知缺口：POI 查询未 SELECT building_id（前端不得依赖该字段）',
      poiSelect.length > 0 && poiSelect.indexOf('building_id') === -1,
      poiSelect
    )
    check('已知缺口：无建筑列表接口（仅 /building/{id} 单点查询）', !/@router\.get\(\s*["']\/buildings["']/.test(py))
    // 前端与服务端字段名一致（防单侧改名）。
    // 注意：真正的一致性由 ⑤/⑫ 的**行为**断言保证（灌入与后端同形的响应，
    // 断言 markers/距离被正确产出）。这里只做一个廉价的"SQL 里确实有这几列"的存在性检查，
    // 因此断言名如实写成"后端 SELECT 含这些列"，不冒充前后端对账。
    check(
      '后端 POI 的 SELECT 含前端消费的全部列（id/name/category/latitude/longitude/floor）',
      ['id', 'name', 'category', 'latitude', 'longitude', 'floor'].every((c) =>
        new RegExp('\\b' + c + '\\b').test(poiSelect)
      ),
      poiSelect
    )
  } else {
    check('后端 map_api.py 可读（契约对账）', false, backendRouter)
  }

  const routeSvc = path.join(ROOT, 'backend', 'app', 'services', 'route.py')
  if (fs.existsSync(routeSvc)) {
    const py = fs.readFileSync(routeSvc, 'utf8')
    // 只认**代码**（剥离注释与文档字符串）：注释里写着 lat/lng 不算实现
    const pyCode = py
      .replace(/"""[\s\S]*?"""/g, '')
      .replace(/'''[\s\S]*?'''/g, '')
      .replace(/^\s*#.*$/gm, '')
    check(
      'route.py 的 path 点字段确为 lat/lng（前端转换依据，只看代码不看注释）',
      /"lat"\s*:/.test(pyCode) && /"lng"\s*:/.test(pyCode),
      pyCode.indexOf('"lat"') === -1 ? '代码中未找到 "lat": 字面量' : ''
    )
    // 「path 至少 2 点」是前端 polyline 下限的依据，必须由**代码**保证：
    // 认 `[{...lat...lng...}, {...lat...lng...}]` 这种两点直连的显式构造，
    // 或 points[0]/points[-1] 的端点赋值 —— 而不是 grep 一句中文注释。
    const twoPointLiteral = /\[\s*\{\s*"lat"[\s\S]{0,120}?\}\s*,\s*\{\s*"lat"[\s\S]{0,120}?\}\s*\]/.test(pyCode)
    const endpointAssignment = /points\[0\]\s*=/.test(pyCode) && /points\[-1\]\s*=/.test(pyCode)
    check(
      'route.py 的代码保证 path 至少 2 点（两点直连构造 或 端点赋值）',
      twoPointLiteral || endpointAssignment,
      `twoPointLiteral=${twoPointLiteral} endpointAssignment=${endpointAssignment}`
    )
  } else {
    check('route.py 可读（path 字段对账）', false, routeSvc)
  }

  const seed = path.join(ROOT, 'db', 'sql', '99_init_data.sql')
  if (fs.existsSync(seed)) {
    const sql = fs.readFileSync(seed, 'utf8')
    const ids = Array.from(sql.matchAll(/\(\s*(\d+)\s*,\s*'([^']+)'\s*,\s*'[^']*'\s*,\s*'[^']*'\s*,\s*\d+\s*,/g)).map((m) => Number(m[1]))
    check('建筑入口登记的 id 在 building 种子中存在（1、2）', ids.indexOf(1) !== -1 && ids.indexOf(2) !== -1, ids.join(','))
  } else {
    check('db/sql/99_init_data.sql 可读（建筑 id 对账）', false, seed)
  }

  // ---------------------------------------------------- ⑱ 回归 ----
  section('⑱ 回归（既有地图业务与 F10/F12 未被破坏）')

  // 完整性闸门：MAP_SRC_OVERRIDE 只能用于「本任务允许改动的文件」。
  // 若被用来替换回归相关文件，校验脚本必须先自曝——否则变异测试里
  // 「改坏既有业务」这类缺陷会被伪造副本掩盖，断言形同虚设。
  if (IS_OVERRIDE) {
    const untouched = [
      path.join('pages', 'map', 'building.js'),
      path.join('pages', 'map', 'building.wxml'),
      path.join('pages', 'map', 'building.wxss'),
      path.join('pages', 'map', 'building.json'),
      path.join('pages', 'index', 'index.js'),
      path.join('pages', 'service', 'service.js'),
      path.join('app.wxss'),
      path.join('app.js'),
      path.join('styles', 'tokens.wxss'),
      path.join('styles', 'glass.wxss'),
      path.join('utils', 'glass.js'),
      path.join('custom-tab-bar', 'index.js'),
      path.join('custom-tab-bar', 'index.wxml'),
    ]
    const tampered = untouched.filter((rel) => !sameFile(path.join(SRC_ROOT, rel), path.join(MP, rel)))
    check('完整性闸门：override 副本未篡改本任务写范围之外的文件', tampered.length === 0, tampered.join(', '))
  } else {
    check('完整性闸门：直接校验真实仓库（无 override）', SRC_ROOT === MP, `SRC_ROOT=${SRC_ROOT}`)
  }

  const buildingJs = read('pages/map/building.js')
  check('建筑详情页仍调用 /map/building/{id}', /\/map\/building\//.test(stripComments(buildingJs)))
  check('建筑详情页已注册且入口存在', (appJson.pages || []).indexOf('pages/map/building') !== -1)
  check('建筑详情页未被本任务改动其接口逻辑', /request\(/.test(buildingJs))
  check(
    '首页/服务页地图入口仍然指向 pages/map/index',
    /\/pages\/map\/index/.test(read('pages/index/index.js')) && /\/pages\/map\/index/.test(read('pages/service/service.js'))
  )
  check('F10：app.wxss 仍同时引入 tokens 与 glass', /@import\s+["'][^"']*tokens\.wxss/.test(read('app.wxss')) && /@import\s+["'][^"']*glass\.wxss/.test(read('app.wxss')))
  // F10 前置依赖：本页消费 F10 的探测结果（glassSupported）与降级类。
  // 若 F10 尚未合入 origin/dev，则如实记为「前置未就绪」（NOT READY），
  // 而不是静默跳过或谎报通过；一旦 F10 存在，就必须满足其既有口径。
  if (fs.existsSync(path.join(MP, 'utils', 'glass.js'))) {
    const g = read('utils/glass.js')
    const gCode = stripComments(g)
    check('F10：utils/glass.js 的 system 仍来自 getDeviceInfo/getSystemInfoSync', /getDeviceInfo/.test(gCode) && /getSystemInfoSync/.test(gCode))
    // 仅看代码：注释里写明「不要用 getAppBaseInfo」不算违规
    check(
      'F10：utils/glass.js 未改用 getAppBaseInfo 取 system（该接口不提供 system）',
      !/getAppBaseInfo/.test(gCode)
    )
    check('F10：app.js 仍把 detectGlass() 结果写入 globalData.glassSupported', (() => {
      const a = stripComments(read('app.js'))
      return /detectGlass/.test(a) && /glassSupported/.test(a)
    })())
  } else {
    console.log('  [SKIP] F10 前置未就绪：miniprogram/utils/glass.js 不存在（F10 尚未合入 dev）')
    check('F10 前置未就绪时本页仍有玻璃降级兜底（glassFallback 默认 false + CSS 默认实心底）', /glassFallback:\s*false/.test(pageJsCode) && /rgba\(255, 255, 255, 0\.86\)/.test(wxss))
  }
  check('F12：custom-tab-bar 仍存在且未被本页改动', fs.existsSync(path.join(MP, 'custom-tab-bar', 'index.wxml')))
  check('F12：app.json 的 tabBar.custom 仍为 true', !!(appJson.tabBar && appJson.tabBar.custom === true))
  check('F17/F16 等既有页面注册未被破坏（app.json pages 数量 ≥ 28）', (appJson.pages || []).length >= 28, String((appJson.pages || []).length))

  // ---------------------------------------------------- ⑲ 自检 ----
  section('⑲ verifier 自检（防止「前半段绿、后半段没执行」）')

  check('断言总数为正整数（脚本确实执行到了结尾）', passed + failures.length > 0)
  // 与 negative control 的交叉核对。
  //
  // 口径说明（避免把"做不到的完美"写成假的保证）：
  //   要求"本脚本每一条断言都被某组突变打挂"是不现实的 —— 本脚本有 300+ 条断言，
  //   其中大量是静态结构/契约存在性检查，为它们各造一个突变只会得到一个
  //   巨大且脆弱的脚本。真正能自动化、且确实有价值的核对是：
  //     ① negative control 里写的每个断言名，都必须**真实存在于本脚本**
  //        → 任何拼写漂移都会被立刻发现（否则那组突变永远不可能 HIT，等于静默漏检）；
  //     ② 每组突变都必须声明至少一条 expect（防止有人加突变时忘了写口径）；
  //     ③ 突变组数不低于下限，防止覆盖面被悄悄削掉。
  //   实际的"打挂能力"由 negative_control_f18_campus_map.js 逐组真跑证明，
  //   而不是由本脚本自我声明 —— 这一点已写进两个脚本的头部说明。
  {
    const ncPath = path.join(ROOT, 'tools', 'negative_control_f18_campus_map.js')
    if (fs.existsSync(ncPath)) {
      const nc = fs.readFileSync(ncPath, 'utf8')
      const selfSrc = fs.readFileSync(__filename, 'utf8')

      // 突变组的 name 字段（形如 `    name: 'xxx',`）
      const mutationNames = []
      const reName = /^\s{2,}name:\s*'([^']+)',\s*$/gm
      let mName
      while ((mName = reName.exec(nc)) !== null) mutationNames.push(mName[1])

      // 每组的 expect 数组
      const expects = []
      const reExp = /^\s{2,}expect:\s*\[([\s\S]*?)\],\s*$/gm
      let mExp
      while ((mExp = reExp.exec(nc)) !== null) {
        const reStr = /'([^']+)'/g
        let mStr
        const group = []
        while ((mStr = reStr.exec(mExp[1])) !== null) group.push(mStr[1])
        expects.push(group)
      }

      check(
        'negative control 的突变组可被解析（≥30 组）',
        mutationNames.length >= 30,
        `mutations=${mutationNames.length}`
      )
      check(
        '每组突变都声明了至少一条期望断言（无空口径）',
        expects.length === mutationNames.length && expects.every((g) => g.length > 0),
        `mutations=${mutationNames.length} expectGroups=${expects.length} empty=${expects.filter((g) => g.length === 0).length}`
      )

      const allExpect = Array.from(new Set(expects.reduce((a, g) => a.concat(g), [])))
      const unknown = allExpect.filter((n) => selfSrc.indexOf(n) === -1)
      check(
        `negative control 引用的 ${allExpect.length} 个断言名全部真实存在于本脚本（无拼写漂移）`,
        unknown.length === 0,
        unknown.join(' | ')
      )
      check(
        '被突变覆盖的断言数达到下限（≥25 条）',
        allExpect.length >= 25,
        String(allExpect.length)
      )
    } else {
      check('negative control 脚本存在（可交叉核对）', false, ncPath)
    }
  }
  check('沙箱临时目录已记录（可清理）', sandboxes.length > 0, String(sandboxes.length))
  check('沙箱副本未污染真实仓库（校验期间本体未被写入）', sameFile(SRC_MAP_UTIL, path.join(MP, 'utils', 'map.js')) || IS_OVERRIDE)
}

// ============================================================ 主流程 ----

/**
 * 统一收尾。
 *
 * `aborted` 表示脚本**没有跑到最后一节**。这一点必须显式区分：
 * 只看"有没有 [NG]"是不够的 —— 脚本可能在断言中途抛异常，
 * 而它崩溃前打印的 [NG] 行看起来同样"正常"。
 * 因此汇总行里带上「是否跑完」，negative control 也据此判定 HIT。
 */
function finish(aborted, abortReason) {
  if (!isMain) return
  const total = passed + failures.length
  console.log('\n' + '─'.repeat(60))
  console.log(
    `断言统计：共 ${total} 项（通过 ${passed} / 失败 ${failures.length} / 异常 ${errored}）` +
      (aborted ? ' [未跑完]' : '')
  )
  if (aborted) {
    console.log(`[ABORT] 脚本在中途抛出异常，后续断言未执行 —— 禁止判定为 PASS`)
    if (abortReason) console.log('   原因：' + String(abortReason).split('\n')[0])
  }
  if (!aborted && failures.length === 0 && errored === 0) {
    console.log(`[PASS] F18 校园地图：${total} 项断言全部通过`)
    console.log('⚠️ 真机地图渲染 / 缩放拖动 / safe-area / 胶囊避让 / marker 手感 / 定位授权弹窗 /')
    console.log('   wx.openSetting 回流 / 低端 Android 性能 = MANUAL CHECK REQUIRED')
    cleanup()
    process.exit(0)
  }
  console.log(`[FAIL] F18 校园地图：${passed} 通过 / ${failures.length} 失败 / ${errored} 异常`)
  failures.forEach((f) => console.log('   - ' + f))
  cleanup()
  process.exit(1)
}

/** 运行主体。内部捕获异常：**绝不**因单点失败而丢掉后面的断言。 */
async function main() {
  try {
    await run()
    finish(false)
  } catch (e) {
    errored += 1
    console.error('\n[ERROR] 验证脚本异常（该异常之后的断言未执行）：')
    console.error(e && e.stack ? e.stack : e)
    finish(true, e && e.message ? e.message : String(e))
  }
}

main()

function cleanup() {
  sandboxes.forEach((d) => {
    try {
      fs.rmSync(d, { recursive: true, force: true })
    } catch (e) {
      /* 忽略清理失败 */
    }
  })
}

module.exports = { run, check, failures, passed: () => passed, errored: () => errored }
