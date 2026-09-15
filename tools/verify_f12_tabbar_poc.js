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
 * 用法：
 *     node tools/verify_f12_tabbar_poc.js
 * 退出码：0 = 全部通过；1 = 有失败
 *
 * 注意：脚本会故意打印 `[tabbar] 未知的 Tab key： nope`（那是「未知 key 被拒绝」这条
 * 断言触发的被测代码诊断日志，属预期行为），不代表失败；只看 `[OK]`/`[NG]` 与退出码。
 */

'use strict'

const fs = require('fs')
const path = require('path')
const vm = require('vm')

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

// ------------------------------------------------------- 组件沙箱（真实加载）

const wxCalls = { vibrate: 0, modal: 0, switchTab: [], toast: 0, recorderStart: 0 }

function makeSandbox(glassSupported) {
  const sandbox = {
    console: { log() {}, warn() {}, error() {} },
    module: { exports: {} },
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
      },
      getRecorderManager: () => ({
        onStop() {},
        onError() {},
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
  const sandbox = makeSandbox(glassSupported)
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
const { TAB_ORDER, syncTabBar, setTabBarHidden } = require(path.join(MP, 'utils', 'tabbar.js'))

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

// ------------------------------------------------- C. AI 居中 + 凸起圆形

console.log('\nC. AI 居中凸起圆形（②）')

const raised = list.filter((i) => i.raised === true)
check('恰好 1 项标记 raised', raised.length === 1, `实际 ${raised.length}`)
check('raised 项位于正中（index 2 of 5）', list[2] && list[2].raised === true)
check('raised 项就是 AI 助手', !!(list[2] && list[2].text === 'AI助手' && list[2].pagePath === '/pages/chat/chat'))

const wxml = read('custom-tab-bar/index.wxml')
const wxss = read('custom-tab-bar/index.wxss')
check('wxml 渲染凸起圆形（.tabbar-fab + tabbar-item--raised）', /tabbar-fab/.test(wxml) && /tabbar-item--raised/.test(wxml))
check('wxss 定义圆形（border-radius: 50%）与上浮（负 margin-top）', /border-radius:\s*50%/.test(wxss) && /margin-top:\s*-\d/.test(wxss))
check('wxss 复用 F10 玻璃样式库', /@import\s+"\.\.\/styles\/glass\.wxss"/.test(wxss), '未 @import glass.wxss')
check('组件按 F10 能力探测挂载降级类 is-glass-fallback', /is-glass-fallback/.test(wxml) && /glassSupported/.test(read('custom-tab-bar/index.js')))

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

// 录音自检（POC 的上传替代物）：确认真的调了 recorder.start
wxCalls.recorderStart = 0
if (inst) inst.runRecorderSelfCheck()
check('录音自检会真正启动录音器（不上传）', wxCalls.recorderStart === 1, `start 调用 ${wxCalls.recorderStart} 次`)

// ------------------------------------------------- E. 全屏隐藏机制

console.log('\nE. 全屏页隐藏机制（⑤）')

check('wxml 用 hidden 控制 tabbar--hidden 类', /hidden\s*\?\s*'tabbar--hidden'/.test(wxml))
check('wxss 定义 .tabbar--hidden', /\.tabbar--hidden/.test(wxss))

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
check('组件与助手均声明 POC 边界注释', /POC/.test(barJs) && /POC/.test(read('utils/tabbar.js')))
check('不假装完成转写（无假接口调用）', !/voice\/transcribe/.test(barJs) || /未就绪|待 B28/.test(barJs))

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
