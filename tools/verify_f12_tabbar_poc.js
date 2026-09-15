#!/usr/bin/env node
/**
 * 验证 F12「自定义 TabBar POC」（二阶段任务单 §3.3 F12，方案 §1.4）
 *
 * POC 要验证的 5 件事：① 5 项 ② AI 居中凸起圆形 ③ 长按语音触发链路
 * ④ 震动 + 弹窗 ⑤ 全屏页隐藏机制。
 *
 * 做法：用 Node `vm` 以桩 `Component` / `wx` / `getApp` **真实加载**自定义组件与
 * `utils/tabbar.js`，再**调用**其方法断言可观测行为（而非只做文本 grep）——
 * 因此长按链路、选中态同步、显隐、切换跳转都是「跑出来的结论」。
 * 无需微信开发者工具、无需网络。
 *
 * ⚠️ 仍属 MANUAL CHECK REQUIRED 的部分：真机凸起位置/布局、真实震动、弹窗观感、
 *    隐藏动画顺滑度、iOS 安全区。代码级断言不能替代这些。
 *
 * 断言口径（避免"只会 PASS"的假断言）：
 *   - **比数值，不比字面量**：页面底部留白只需 ≥ 底栏占用高度（可以在其之上再加呼吸位，
 *     134rpx / 250rpx 都合法），不要求出现 "110rpx" 这个字面量。
 *   - **认机制，不认写法**：上浮用 `top` 还是负 `margin-top` 都可以（精确值由 H 段单独断言）。
 *   - **安全区只认 `env()`**：`constant()` 是 iOS 11.0~11.2 的旧写法，现代 WebView 不认。
 *   - **查规则体，不查选择器是否存在**：`.tabbar--hidden {}`（空规则）必须被判失败。
 *   - **查文件头，不查全文出现**：POC 边界声明必须在模块头部；正文里偶然出现不算数。
 *   - **可失败的断言**：不做 `A || B` 这种"总有一边为真"的写法（曾有一条永远通过）。
 *   - **验收点①可自动化的部分**：凸起圆形必须水平居中（`left:50%` + `translateX(-50%)`）。
 *   - **文档与脚本强绑定**：README 里写死的断言条数 / var() fallback 数必须等于实际值。
 *   - **不留零覆盖的承诺**：README 承诺的失败路径（录音拒绝授权、切换失败）必须真的被驱动。
 *   以上口径均由 tools/negative_control_f12_tabbar_poc.js 用变异测试反向证明有效。
 *
 * 用法：
 *     node tools/verify_f12_tabbar_poc.js
 * 退出码：0 = 全部通过；1 = 有失败
 *
 * 配套：`node tools/negative_control_f12_tabbar_poc.js` —— 在临时副本里注入 31 个已知缺陷，
 *      要求每一个都被本脚本的**指定断言**抓到（证明本脚本不是只会 PASS）。
 *
 * 注意：脚本会故意打印 `[tabbar] 未知的 Tab key： nope`（那是「未知 key 被拒绝」这条
 * 断言触发的被测代码诊断日志，属预期行为），不代表失败；只看 `[OK]`/`[NG]` 与退出码。
 */

'use strict'

const fs = require('fs')
const path = require('path')
const vm = require('vm')
const Module = require('module')

const ROOT = path.resolve(__dirname, '..')
const MP = path.join(ROOT, 'miniprogram')
const BAR_DIR = path.join(MP, 'custom-tab-bar')

let passed = 0
const failures = []

function check(name, ok, detail) {
  if (ok) {
    passed += 1
    console.log(`  [OK] ${name}`)
  } else {
    failures.push(name + (detail ? `  <- ${detail}` : ''))
    console.log(`  [NG] ${name}${detail ? '  <- ' + detail : ''}`)
  }
}

function read(rel) {
  return fs.readFileSync(path.join(MP, rel), 'utf8')
}

/** 去掉 WXSS/JS 注释后再做代码断言：注释里提到 `var()` 之类不算真实用法（避免误报） */
function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, '')
}

/**
 * 取某选择器自己的规则体（而非后代选择器）。
 * 锚定行首可避免 `.tabbar-fab {` 误命中 `.tabbar-item--raised .tabbar-fab {`。
 */
function cssBlock(src, selector) {
  const m = src.match(new RegExp(`^\\s*\\${selector}\\s*\\{([^}]*)\\}`, 'm'))
  return m ? m[1] : ''
}

/**
 * 取文件**开头**的连续行注释（模块 docblock）。
 * 只认文件头，是为了避免"文件里随便哪儿出现过 POC 就算数"这种假断言 ——
 * 真正的 POC 边界声明必须写在模块头部才起到告知作用。
 */
function headerComment(src) {
  const out = []
  for (const line of src.split('\n')) {
    if (line.startsWith('//')) out.push(line)
    else if (out.length > 0 || line.trim() !== '') break
  }
  return out.join('\n')
}

/**
 * 底部安全区的**现代**写法。必须点名 `env()`：`constant()` 是 iOS 11.0~11.2 的旧写法，
 * 现代 WebView 只认 `env()`。只写 `constant()` 时页面在当代 iPhone 上仍会被底部横条压住，
 * 所以不能拿 `constant()` 顶数（这条曾被阴性对照抓出过漏检）。
 */
const SAFE_AREA = /env\(\s*safe-area-inset-bottom\s*\)/

/**
 * 取声明里的「基础 rpx 值」：同时支持 `Nrpx` 与 `calc(Nrpx + env(safe-area-inset-bottom))`。
 * 页面留白允许在底栏高度之上再加呼吸位（134rpx、250rpx…），故断言必须比大小，
 * 而不是比对字面量 "110rpx"。
 */
function rpxBase(decl) {
  const m = String(decl).match(/(\d+(?:\.\d+)?)rpx/)
  return m ? Number(m[1]) : NaN
}

// ------------------------------------------------------- 组件沙箱（真实加载）

const wxCalls = { vibrate: 0, modal: 0, switchTab: [], toast: 0, recorderStart: 0 }

function makeSandbox(glassSupported, rel) {
  const sandbox = {
    console: { log() {}, warn() {}, error() {} },
    module: { exports: {} },
    // 组件内部 require('../utils/tab-order') 必须按**该文件的真实位置**解析，
    // 否则沙箱里 require 未定义 → 加载失败，测出来的就不是真实组件。
    require: Module.createRequire(path.join(MP, rel)),
    getApp: () => ({ globalData: { glassSupported } }),
    wx: {
      vibrateShort: () => {
        wxCalls.vibrate += 1
      },
      showModal: () => {
        wxCalls.modal += 1
      },
      showToast: () => {
        wxCalls.toast += 1
      },
      switchTab: (o) => {
        wxCalls.switchTab.push(o && o.url)
        // 让"切换失败"这条路径可被驱动：真实环境里 switchTab 会失败（目标非 tab 页、
        // 自定义 TabBar 未挂载等），静默失败用户只会觉得"点了没反应"。
        if (sandbox.__switchTabShouldFail && o && typeof o.fail === 'function') {
          o.fail({ errMsg: 'switchTab:fail mock' })
        }
      },
      getRecorderManager: () => ({
        // 回调要**真的存下来**才能驱动：空函数桩会让 onStop/onError 分支永远不执行，
        // README §2「拒绝授权后应弹出录音失败而非静默无反应」就成了零覆盖的承诺。
        onStop(cb) {
          sandbox.__recorderOnStop = cb
        },
        onError(cb) {
          sandbox.__recorderOnError = cb
        },
        start() {
          wxCalls.recorderStart += 1
        },
      }),
    },
    Component: (cfg) => {
      sandbox.__component = cfg
    },
  }
  vm.createContext(sandbox)
  return sandbox
}

function loadComponent(rel, glassSupported) {
  const sandbox = makeSandbox(glassSupported, rel)
  vm.runInContext(fs.readFileSync(path.join(MP, rel), 'utf8'), sandbox, { filename: rel })
  const cfg = sandbox.__component
  if (!cfg) return null
  const inst = {
    data: JSON.parse(JSON.stringify(cfg.data || {})),
    setData(patch) {
      Object.assign(this.data, patch)
    },
  }
  for (const [name, fn] of Object.entries(cfg.methods || {})) inst[name] = fn.bind(inst)
  // 真实生命周期：RecorderManager 监听与玻璃降级判定都在 attached 内完成，
  // 不调用它就等于没进入真实运行态（会漏掉一类"只在真机上才暴露"的缺陷）。
  if (cfg.lifetimes && typeof cfg.lifetimes.attached === 'function') cfg.lifetimes.attached.call(inst)
  return { cfg, inst, sandbox }
}

const tapEvent = (dataset) => ({ currentTarget: { dataset } })

// ---------------------------------------------------------------- A. 结构

console.log('\nA. POC 结构（custom-tab-bar/ 四件套 + app.json 开关）')

for (const f of ['index.js', 'index.json', 'index.wxml', 'index.wxss']) {
  check(`custom-tab-bar/${f} 存在`, fs.existsSync(path.join(BAR_DIR, f)))
}

let barJson = null
try {
  barJson = JSON.parse(fs.readFileSync(path.join(BAR_DIR, 'index.json'), 'utf8'))
} catch (err) {
  /* 下面统一报错 */
}
check('custom-tab-bar/index.json 可解析且 component:true', !!barJson && barJson.component === true)

let appJson = null
try {
  appJson = JSON.parse(read('app.json'))
} catch (err) {
  /* 下面统一报错 */
}
check('app.json 可解析', !!appJson)
check('app.json 已开启 tabBar.custom', !!(appJson && appJson.tabBar && appJson.tabBar.custom === true))

// --------------------------------------------------- B. Tab 顺序单一事实来源

console.log('\nB. Tab 顺序一致性（app.json ↔ utils/tabbar.js ↔ 组件 list）')

const appList = ((appJson && appJson.tabBar && appJson.tabBar.list) || []).map((i) => '/' + i.pagePath)
const { TAB_LIST, TAB_ORDER, TAB_INDEX } = require(path.join(MP, 'utils', 'tab-order.js'))
const { syncTabBar, setTabBarHidden } = require(path.join(MP, 'utils', 'tabbar.js'))

const loaded = loadComponent('custom-tab-bar/index.js', true)
check('自定义组件可被真实加载（Component 配置存在）', !!loaded)
const cfg = loaded && loaded.cfg
const inst = loaded && loaded.inst
const list = (cfg && cfg.data && cfg.data.list) || []

check('Tab 数量 = 5', appList.length === 5 && list.length === 5, `app=${appList.length} component=${list.length}`)
// 期望顺序由 app.json 反推（key = 页面路径末段），避免在测试里硬编码顺序掩盖真实不一致
const appKeys = appList.map((p) => p.split('/').pop())
check(
  'TAB_ORDER 与 app.json 的 Tab 顺序一致',
  TAB_ORDER.join(',') === appKeys.join(','),
  `tabbar=[${TAB_ORDER.join(',')}] app=[${appKeys.join(',')}]`
)
check('app.json 中 AI 页也位于正中（index 2）', appList[2] === '/pages/chat/chat', String(appList[2]))
check(
  '组件 list 与 app.json 的 pagePath 顺序一致',
  list.map((i) => i.pagePath).join(',') === appList.join(','),
  `component=${list.map((i) => i.pagePath).join(',')}`
)
check('组件 list 每项都有 text', list.every((i) => typeof i.text === 'string' && i.text.length > 0))

// JS 侧唯一事实来源：组件必须直接用 utils/tab-order.js 的清单，而不是各自再抄一份
// （此前 tab 清单存在 3 份，只靠正则比对容易漏改）。
check(
  '组件的 list 就是 utils/tab-order.js 的 TAB_LIST（同一份数据，非副本）',
  JSON.stringify(list) === JSON.stringify(TAB_LIST),
  '组件 list 与 TAB_LIST 不一致'
)
check(
  'utils/tabbar.js 不再自定义 key 数组（改用 TAB_INDEX）',
  !/TAB_ORDER\s*=\s*\[/.test(read('utils/tabbar.js')),
  'utils/tabbar.js 里仍有 TAB_ORDER 字面量'
)
check('TAB_INDEX 由 TAB_ORDER 正确推导', TAB_ORDER.every((k, i) => TAB_INDEX[k] === i))

// ------------------------------------------------- C. AI 居中 + 凸起圆形

console.log('\nC. AI 居中凸起圆形（②）')

const raised = list.filter((i) => i.raised === true)
check('恰好 1 项标记 raised', raised.length === 1, `实际 ${raised.length}`)
check('raised 项位于正中（index 2 of 5）', list[2] && list[2].raised === true)
check('raised 项就是 AI 助手', !!(list[2] && list[2].text === 'AI助手' && list[2].pagePath === '/pages/chat/chat'))

const wxml = read('custom-tab-bar/index.wxml')
const wxss = read('custom-tab-bar/index.wxss')
const fabBlock = cssBlock(stripComments(wxss), '.tabbar-fab')
check('wxml 渲染凸起圆形（.tabbar-fab + tabbar-item--raised）', /tabbar-fab/.test(wxml) && /tabbar-item--raised/.test(wxml))
// 上浮有两种等价机制：绝对定位的 top 取负（本实现的机制，H 段再断言精确值 -20rpx），
// 或流式布局的 margin-top 取负。这里只要求「圆形 + 存在负向偏移」，不锁死机制 ——
// 否则会把合法的 `top: -20rpx` 判成失败（H 段同时要求 top，两条断言会自相矛盾）。
check(
  'wxss 定义圆形（border-radius: 50%）且 .tabbar-fab 表达上浮（负 top 或负 margin-top）',
  /border-radius:\s*50%/.test(fabBlock) && /(?:margin-top|top):\s*-\d/.test(fabBlock),
  `FAB 规则体=${fabBlock.trim().replace(/\s+/g, ' ') || '未匹配到 .tabbar-fab'}`
)
// 验收点①「真机凸起位置正确」里**唯一可自动化**的部分：圆形必须水平居中。
// 缺了这条，`.tabbar-fab { left: 0 }` 也能让整套断言通过（阴性对照已补该变异）。
check(
  '凸起圆形水平居中（left: 50% + translateX(-50%)），而非只"落在中间那一项里"',
  /left:\s*50%/.test(fabBlock) && /translateX\(\s*-50%\s*\)/.test(fabBlock),
  `FAB 规则体=${fabBlock.trim().replace(/\s+/g, ' ') || '未匹配到 .tabbar-fab'}`
)
// 与 glass.wxss 的 box-sizing 对齐：圆形尺寸按 96rpx 含描边算，否则实际直径会变成 100rpx
check('凸起圆形按 border-box 计算尺寸（96rpx 含 2rpx 描边）', /box-sizing:\s*border-box/.test(fabBlock))
check('wxss 复用 F10 玻璃样式库', /@import\s+"\.\.\/styles\/glass\.wxss"/.test(wxss), '未 @import glass.wxss')
check('组件按 F10 能力探测挂载降级类 is-glass-fallback', /is-glass-fallback/.test(wxml) && /glassSupported/.test(read('custom-tab-bar/index.js')))

// ---- F10 令牌在自定义组件内的可用性 ----
// tokens.wxss 把变量定义在 `page` 上（其文件头亦注明「自定义组件需结合 styleIsolation 处理」），
// 组件内 var() 能否取到令牌取决于隔离与继承，静态无法证实。因此本组件的硬性要求是：
// **每个 var() 都必须带 literal fallback** —— 即使令牌完全失效，样式也不会丢失。
const varUsages = (stripComments(wxss).match(/var\(/g) || []).length
const varWithFallback = (stripComments(wxss).match(/var\(--[A-Za-z0-9-]+\s*,\s*[^)]+\)/g) || []).length
check(
  '组件 wxss 的每个 var() 都带 literal fallback（令牌失效也不掉样式）',
  varUsages > 0 && varUsages === varWithFallback,
  `var() 共 ${varUsages} 处，带 fallback ${varWithFallback} 处`
)

// 降级类必须真的作用到凸起圆形：glass.wxss 需有对应选择器，且 FAB 需带 xj-glass-strong
const glassWxss = fs.readFileSync(path.join(MP, 'styles', 'glass.wxss'), 'utf8')
check(
  'glass.wxss 提供 .is-glass-fallback .xj-glass-strong 降级规则',
  /\.is-glass-fallback\s+\.xj-glass-strong/.test(glassWxss)
)
// 类名按 token 匹配：写死 `class="tabbar-fab xj-glass-strong"` 会因属性顺序 / 追加类而误报
check(
  'FAB 带有被降级规则命中的 xj-glass-strong 类',
  /class="[^"]*\btabbar-fab\b[^"]*\bxj-glass-strong\b[^"]*"/.test(wxml)
)

// 降级规则会重置 border-color / box-shadow（同权重靠声明顺序取胜），
// 故蓝色描边必须在 @import 之后用更高权重再声明一次，否则降级态下凸起圆形会退化成一片白。
const importAt = wxss.indexOf('@import')
const ringAt = wxss.indexOf('.tabbar-item--raised .tabbar-fab')
check(
  '凸起圆形的蓝色描边在 @import 之后重声明（降级态仍可辨识）',
  ringAt > importAt && ringAt > -1,
  `import@${importAt} ring@${ringAt}`
)
// 光有"声明顺序"还不够：§1.4 要的是**蓝色**描边。
// 只断言顺序的话，把描边改成灰色也照样通过。
const raisedRing = cssBlock(stripComments(wxss), '.tabbar-item--raised .tabbar-fab')
check(
  '凸起圆形的描边确实是「蓝色」（§1.4 蓝色描边），而非任意颜色',
  /border-color:\s*(?:#4A90D9|rgba\(\s*74\s*,\s*144\s*,\s*217|var\(--xj-color-primary)/i.test(raisedRing),
  `描边规则体=${raisedRing.trim().replace(/\s+/g, ' ') || '未匹配到 .tabbar-item--raised .tabbar-fab'}`
)

// 行为断言：能力探测「不支持」时必须真的降级，「支持」时必须不降级（反向对照）
const noGlass = loadComponent('custom-tab-bar/index.js', false)
const yesGlass = loadComponent('custom-tab-bar/index.js', true)
check(
  'glassSupported=false → glassFallback=true（走实心降级）',
  noGlass && noGlass.inst.data.glassFallback === true,
  noGlass ? String(noGlass.inst.data.glassFallback) : '加载失败'
)
check(
  '反向对照：glassSupported=true → glassFallback=false（保留毛玻璃）',
  yesGlass && yesGlass.inst.data.glassFallback === false,
  yesGlass ? String(yesGlass.inst.data.glassFallback) : '加载失败'
)

// ------------------------------------------------- D. 长按语音链路 + 震动/弹窗

console.log('\nD. 长按语音触发链路（③④）：震动 → 弹窗')

check('wxml 绑定 bindlongpress', /bindlongpress="onItemLongPress"/.test(wxml))
check('wxml 绑定 bindtap', /bindtap="onItemTap"/.test(wxml))

// 真实调用：长按 AI 项 → 必须产生 1 次震动 + 1 次弹窗
wxCalls.vibrate = 0
wxCalls.modal = 0
if (inst) inst.onItemLongPress(tapEvent({ raised: true }))
check('长按 AI 项触发震动', wxCalls.vibrate === 1, `vibrateShort 调用 ${wxCalls.vibrate} 次`)
check('长按 AI 项弹出语音入口', wxCalls.modal === 1, `showModal 调用 ${wxCalls.modal} 次`)

// 反向对照：长按非 AI 项不得触发（避免「所有 tab 都弹语音」）
wxCalls.vibrate = 0
wxCalls.modal = 0
if (inst) inst.onItemLongPress(tapEvent({ raised: false }))
check('反向对照：长按非 AI 项不震动不弹窗', wxCalls.vibrate === 0 && wxCalls.modal === 0)

// 点击切换：真实调用 switchTab
wxCalls.switchTab = []
if (inst) inst.onItemTap(tapEvent({ path: '/pages/forum/forum' }))
check('点击项调用 wx.switchTab 且目标正确', wxCalls.switchTab[0] === '/pages/forum/forum', String(wxCalls.switchTab[0]))

// 切换失败必须给可见反馈：底栏是这个页面上唯一的导航出口，
// 只 console.error 的话用户看到的就是"点了没反应"（与 pages/map 的既有做法一致）。
wxCalls.switchTab = []
wxCalls.toast = 0
loaded.sandbox.__switchTabShouldFail = true
if (inst) inst.onItemTap(tapEvent({ path: '/pages/user/user' }))
loaded.sandbox.__switchTabShouldFail = false
check('切换失败时给出 toast 反馈（不静默）', wxCalls.toast === 1, `showToast 调用 ${wxCalls.toast} 次`)

// 录音自检（POC 的上传替代物）：确认真的调了 recorder.start
wxCalls.recorderStart = 0
if (inst) inst.runRecorderSelfCheck()
check('录音自检会真正启动录音器（不上传）', wxCalls.recorderStart === 1, `start 调用 ${wxCalls.recorderStart} 次`)

// 录音**失败**路径：README §2 承诺「拒绝授权后应弹出录音失败而非静默无反应」。
// 这条承诺必须真的驱动 onError 回调才算验过（空函数桩会让它成为零覆盖的空话）。
wxCalls.modal = 0
if (inst) inst.runRecorderSelfCheck()
if (loaded.sandbox.__recorderOnError) loaded.sandbox.__recorderOnError({ errMsg: 'record:fail auth deny' })
check('录音失败时弹出「录音失败」而非静默无反应', wxCalls.modal === 1, `showModal 调用 ${wxCalls.modal} 次`)

// 反向对照：非本次自检的迟到回调必须被丢弃，否则上一次的结果会串到下一次
wxCalls.modal = 0
if (loaded.sandbox.__recorderOnStop) loaded.sandbox.__recorderOnStop({ duration: 2500, fileSize: 1024 })
check('反向对照：非本次自检的迟到回调不弹窗', wxCalls.modal === 0, `showModal 调用 ${wxCalls.modal} 次`)

// 成功路径同样要能弹出结果（不是只有失败才会响）
wxCalls.modal = 0
if (inst) inst.runRecorderSelfCheck()
if (loaded.sandbox.__recorderOnStop) loaded.sandbox.__recorderOnStop({ duration: 2500, fileSize: 1024 })
check('录音成功时弹出录音结果', wxCalls.modal === 1, `showModal 调用 ${wxCalls.modal} 次`)

// ------------------------------------------------- E. 全屏隐藏机制

console.log('\nE. 全屏页隐藏机制（⑤）')

check('wxml 用 hidden 控制 tabbar--hidden 类', /hidden\s*\?\s*'tabbar--hidden'/.test(wxml))
// 只断言"类存在"是空的：`.tabbar--hidden {}`（什么都没写）也能通过。
// 必须断言规则体**真的会隐藏**（下移 + 透明），否则"全屏页正确隐藏"无从谈起。
const hiddenBlock = cssBlock(stripComments(wxss), '.tabbar--hidden')
check(
  '.tabbar--hidden 规则体真的会隐藏（translateY 下移 + opacity: 0），不是空类',
  /translateY\(/.test(hiddenBlock) && /opacity:\s*0(?:\D|$)/.test(hiddenBlock),
  `隐藏规则体=${hiddenBlock.trim().replace(/\s+/g, ' ') || '未匹配到规则体'}`
)

if (inst) {
  inst.setHidden(true)
  const hiddenOn = inst.data.hidden === true
  inst.setHidden(false)
  check('setHidden(true/false) 正确写入 data.hidden', hiddenOn && inst.data.hidden === false)
}

// utils/tabbar.js：选中态 + 显隐 的容错与转发
let selectedSeen = null
let hiddenSeen = null
const fakeBar = {
  setSelected: (n) => {
    selectedSeen = n
  },
  setHidden: (h) => {
    hiddenSeen = h
  },
}
const fakePage = { getTabBar: () => fakeBar }

check("syncTabBar(page,'forum') 传入选中的序号 3", syncTabBar(fakePage, 'forum') === true && selectedSeen === 3, String(selectedSeen))
check('setTabBarHidden(page,true) 转发到组件', setTabBarHidden(fakePage, true) === true && hiddenSeen === true)
check('未知 key 被拒绝（不写入）', syncTabBar(fakePage, 'nope') === false)
check('无 getTabBar 的页面不抛异常（返回 false）', syncTabBar({}, 'index') === false && setTabBarHidden({}, true) === false)

// 页面侧接线：5 个 Tab 页必须各自同步选中项
const TAB_PAGES = {
  'pages/index/index.js': 'index',
  'pages/chat/chat.js': 'chat',
  'pages/service/service.js': 'service',
  'pages/forum/forum.js': 'forum',
  'pages/user/user.js': 'user',
}
for (const [rel, key] of Object.entries(TAB_PAGES)) {
  const src = read(rel)
  check(
    `${rel} 在 onShow 同步选中项 '${key}'`,
    /require\(['"][^'"]*utils\/tabbar['"]\)/.test(src) && new RegExp(`syncTabBar\\(this,\\s*'${key}'\\)`).test(src)
  )
}

// 关键顺序：chat 的 onShow 有多处提前 return，同步必须在其之前，否则会漏同步
const chatSrc = read('pages/chat/chat.js')
const onShowAt = chatSrc.indexOf('onShow()')
const syncAt = chatSrc.indexOf("syncTabBar(this, 'chat')")
const firstReturnAt = chatSrc.indexOf('return', syncAt > -1 ? syncAt : onShowAt)
check(
  'chat.js 的 syncTabBar 位于 onShow 的所有提前 return 之前',
  onShowAt > -1 && syncAt > onShowAt && (firstReturnAt === -1 || syncAt < firstReturnAt)
)

// 全屏隐藏必须有可被真机验证的触发入口（否则机制永远不触发）
check('chat 页提供全屏显隐触发入口（POC 按钮）', /onToggleFullscreenPoc/.test(chatSrc) && /onToggleFullscreenPoc/.test(read('pages/chat/chat.wxml')))
check('全屏入口调用 setTabBarHidden 并如实反馈失败', /setTabBarHidden\(this,/.test(chatSrc) && /showToast/.test(chatSrc))

// ------------------------------------------- F. POC 边界（不叠 F11 / 不假装完成）

console.log('\nF. POC 边界（不依赖未合并的 F11、不冒充正式实现）')

const barJs = read('custom-tab-bar/index.js')
check(
  '自定义 TabBar 未引用 /static/icons（不依赖尚未合入 dev 的 F11 图标集）',
  !/static\/icons/.test(barJs) && !/static\/icons/.test(wxml)
)
check(
  '组件与助手在**文件头**声明 POC 边界（不是"文件里随便哪儿出现过 POC"）',
  /POC/.test(headerComment(barJs)) && /POC/.test(headerComment(read('utils/tabbar.js')))
)
// 「不假装完成转写」拆成两条**各自能失败**的断言。
// （旧写法 `!有转写调用 || 提到未就绪` 里第二项被文件自己的注释满足 → 永远为真，等于没测。）
check(
  '组件不发起任何网络请求（转写未就绪，不假装能转写）',
  !/wx\.request\s*\(/.test(barJs) && !/services\/request/.test(barJs),
  '组件内出现了网络调用，POC 不应上传任何东西'
)
check('面向用户的文案如实说明转写未就绪', /未就绪|待 B28/.test(barJs))

// ------------------------------------ G. 底栏不遮挡页面（遮挡回归）

console.log('\nG. 底栏不遮挡页面内容（fixed 浮层不会自动为页面预留高度）')

// 底栏占用高度写死在组件 wxss 里；页面留白必须 ≥ 该值（+ 安全区）。
// 用 cssBlock 取规则体（而非"从 `.tabbar {` 一路非贪婪找 height"）：
// 后者会被规则体里的注释带偏，也会受后续同名选择器影响。
const tabbarBlock = cssBlock(stripComments(wxss), '.tabbar')
const barHeight = Number((tabbarBlock.match(/height:\s*(\d+)rpx/) || [])[1])
check('可从组件 wxss 解析出底栏高度', Number.isFinite(barHeight) && barHeight > 0, String(barHeight))
// 上面推出的「占用高度 = barHeight + 安全区」成立的前提：底栏自己带**现代写法**的安全区内边距
// （box-sizing: content-box + padding-bottom: env(...)）。少了这条，页面留白就无从对齐。
check(
  '.tabbar 自身带底部安全区内边距 env(safe-area-inset-bottom)（页面留白 = 高度 + 安全区 的前提）',
  SAFE_AREA.test(tabbarBlock),
  '仅声明 constant() 不算：现代 WebView 只认 env()'
)
// 高度与内边距必须相加（content-box）才是真实占用高度；border-box 下 110rpx 会把安全区吃进去
check(
  '.tabbar 用 content-box，使 height 与安全区内边距相加（而非互相挤占）',
  /box-sizing:\s*content-box/.test(tabbarBlock),
  `规则体=${tabbarBlock.trim().replace(/\s+/g, ' ')}`
)

const CLEARANCE = {
  'pages/index/index.wxss': '.page',
  'pages/service/service.wxss': '.xj-page',
  'pages/user/user.wxss': '.xj-page',
  'pages/chat/chat.wxss': '.chat-page',
  'pages/forum/forum.wxss': '.forum-page',
}
for (const [rel, selector] of Object.entries(CLEARANCE)) {
  const src = stripComments(read(rel))
  // 取该选择器最后一次声明（同权重后声明生效），检查 padding-bottom 是否含底栏高度
  const rules = [...src.matchAll(new RegExp(`\\${selector}\\s*\\{([^}]*)\\}`, 'g'))].map((m) => m[1])
  const last = rules[rules.length - 1] || ''
  const pb = (last.match(/padding-bottom:\s*([^;]+);/) || [])[1] || ''
  // 只要求「实际留白 ≥ 底栏占用高度」且「显式叠加安全区」：
  // 留白可以在底栏高度之上再加呼吸位（134/250rpx 都合法），故按数值比较，不比对字面量。
  const base = rpxBase(pb)
  const hasSafeArea = SAFE_AREA.test(pb)
  const enough = Number.isFinite(base) && base >= barHeight && hasSafeArea
  check(
    `${rel} 的 ${selector} 底部留白 ≥ 底栏高度(${barHeight}rpx) + 安全区`,
    enough,
    `padding-bottom=${pb.trim() || '未声明'}（解析 ${base}rpx，安全区=${hasSafeArea}）`
  )
}

// 论坛悬浮按钮必须抬到底栏之上，否则被 z-index:100 的底栏盖住（原生 tabBar 时代 80rpx 够用）
const forumWxss = stripComments(read('pages/forum/forum.wxss'))
const fabBottom = (forumWxss.match(/\.fab\s*\{[\s\S]*?bottom:\s*([^;]+);/) || [])[1] || ''
const fabBase = rpxBase(fabBottom)
check(
  `论坛 .fab 已抬到底栏之上（bottom ≥ ${barHeight}rpx + 安全区）`,
  Number.isFinite(fabBase) && fabBase >= barHeight && SAFE_AREA.test(fabBottom),
  `bottom=${fabBottom.trim() || '未声明'}（解析 ${fabBase}rpx）`
)

// 全屏态必须把底栏留白**还回去**：否则"全屏"只藏了底栏、没让出空间，
// 输入条仍悬在离视口底部 110rpx 处 —— 全屏语义没有真正生效。
const chatFullscreen = cssBlock(stripComments(read('pages/chat/chat.wxss')), '.chat-page--fullscreen')
check(
  '全屏态把底栏留白还回去（.chat-page--fullscreen padding-bottom: 0）',
  /padding-bottom:\s*0(?:rpx)?\s*;/.test(chatFullscreen),
  `全屏规则体=${chatFullscreen.trim().replace(/\s+/g, ' ') || '未匹配到 .chat-page--fullscreen'}`
)
check(
  'chat.wxml 在全屏态真的挂上 chat-page--fullscreen 类',
  /tabBarHidden\s*\?\s*'chat-page--fullscreen'/.test(read('pages/chat/chat.wxml'))
)

// ------------------------------------------- H. 规格对齐与"不夸大结论"

console.log('\nH. 规格对齐（方案 §1.4/§1.5）与结论口径')

// §1.5：TabBar 图标 48rpx —— POC 先占位，避免 F11 接入图标后每项高度变化、使真机结论失效
const slot = (wxss.match(/\.tabbar-icon-slot\s*\{[\s\S]*?width:\s*(\d+)rpx[\s\S]*?height:\s*(\d+)rpx/) || [])
check('已为图标预留 48rpx 槽位（方案 §1.5），F11 接入后不改每项高度', slot[1] === '48' && slot[2] === '48', `slot=${slot[1]}x${slot[2]}`)

// §1.4：AI 圆形按钮「上浮 20rpx」—— 用绝对定位的 top 精确表达
const fabTop = (cssBlock(stripComments(wxss), '.tabbar-fab').match(/top:\s*(-?\d+)rpx/) || [])[1]
check('凸起量符合方案 §1.4「上浮 20rpx」', fabTop === '-20', `top=${fabTop}rpx`)

// 隐藏状态必须在本页 onShow 复位：否则 F14 删掉 POC 按钮后，隐藏过的用户永久无法切页
check(
  'chat 页 onShow 会复位隐藏状态（消除 F14 删除按钮后的死锁）',
  /onShow\(\)[\s\S]{0,600}?setTabBarHidden\(this,\s*false\)/.test(chatSrc)
)
// 按钮不得"说谎"：必须先调用成功再改 data
const toggleBody = (chatSrc.match(/onToggleFullscreenPoc\(\)\s*\{([\s\S]*?)\n  \},/) || [])[1] || ''
check(
  '全屏按钮先调用成功后才改 data（不出现"显示底栏但没隐藏"的假状态）',
  toggleBody.indexOf('setTabBarHidden') > -1 && toggleBody.indexOf('setTabBarHidden') < toggleBody.indexOf('setData')
)

// README 的 ✅ 表不能被读成"真机已通过"。
// 要求写的是**那句承重的话**（代码级断言不能替代真机）+ 显式的 MANUAL CHECK REQUIRED 标记；
// 只匹配「代码级|非真机」的话，README 里任何一处提到这两个词的句子都会让它通过。
const barReadme = read('custom-tab-bar/README.md')
check(
  'README 明确标注断言为代码级、不能替代真机结论',
  /代码级断言\s*\*\*不能\*\*替代/.test(barReadme) && /MANUAL CHECK REQUIRED/.test(barReadme),
  'README 缺少"代码级断言不能替代真机检查"这句承重声明'
)

// README §3.1 声称「当前 N/N 处 var() 全部带 fallback」—— 与断言条数是同一类会漂移的手写数字，
// 同样绑定到实际值（改了 wxss 的 var() 数量而忘了改文档 → 这里失败）。
const varClaim = (barReadme.match(/(\d+)\s*\/\s*(\d+)\s*处/) || []).slice(1).map(Number)
check(
  `README 声明的 var() fallback 数与 wxss 实际一致（${varUsages}/${varUsages}）`,
  varClaim.length === 2 && varClaim[0] === varUsages && varClaim[1] === varUsages,
  `README=[${varClaim.join('/') || '未声明'}] 实际=${varUsages}/${varUsages}`
)

// README 里写死的断言条数会随本脚本演进而过期（修订前写着 54，实际 66）。
// 让文档与脚本强绑定，杜绝再次漂移。
// ⚠️ 本条必须是**最后一条断言**：`passed + failures.length + 1` 只有在它是最后一条时
// 才恰好等于总数（若它之后再失败一条，总数就少算 1 —— 这里曾因此差一位）。
const declaredCounts = [...barReadme.matchAll(/(\d+)\s*项[^|\n]*?断言/g)].map((m) => Number(m[1]))
const totalAssertions = passed + failures.length + 1
check(
  `README 声明的断言条数与脚本实际一致（${totalAssertions} 项）`,
  declaredCounts.length > 0 && declaredCounts.every((n) => n === totalAssertions),
  `README=[${declaredCounts.join(', ')}] 实际=${totalAssertions}`
)

// ---------------------------------------------------------------- 汇总

console.log(`\n${'-'.repeat(60)}`)
if (failures.length === 0) {
  console.log(`[PASS] F12 TabBar POC 校验全部通过（${passed} 项）`)
  console.log('⚠️ 真机凸起位置 / 真实震动 / 弹窗观感 / 隐藏动画 = MANUAL CHECK REQUIRED')
  process.exit(0)
} else {
  console.log(`[FAIL] ${failures.length} 项未通过 / 共 ${passed + failures.length} 项：`)
  for (const f of failures) console.log(`  - ${f}`)
  process.exit(1)
}
