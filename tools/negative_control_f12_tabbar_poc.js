#!/usr/bin/env node
/**
 * F12「自定义 TabBar POC」校验脚本的**阴性对照（negative control）**。
 *
 * 为什么需要它：一套只会打印 `[OK]` 的断言脚本没有任何证明力 —— 断言可能因为
 * 写错了正则、选错了文件、或比对字面量而**永远通过**。本脚本用变异测试证明
 * `tools/verify_f12_tabbar_poc.js` 真的会对「被改坏的产品代码」FAIL：
 *
 *   1. 在系统临时目录里复制一份 `miniprogram/` + 校验脚本（**绝不动工作区**）；
 *   2. 先跑基线，必须 PASS（否则说明副本都不干净，后面的结论无意义）；
 *   3. 逐个注入「已知缺陷」，每次都必须让**指定的那条断言**失败；
 *   4. 注入没生效（字符串没匹配上）也算失败 —— 否则会得到「假阴性通过」。
 *
 * 覆盖点特意包含历史上出过问题的地方：凸起量、页面底部留白（134/126rpx 这类
 * 「比 110rpx 大」的合法值）、安全区、README 里写死的断言条数。
 *
 * 用法：
 *     node tools/negative_control_f12_tabbar_poc.js
 * 退出码：0 = 基线通过且每个变异都被抓到；1 = 基线不通过 / 有变异漏检
 */

'use strict'

const fs = require('fs')
const os = require('os')
const path = require('path')
const { spawnSync } = require('child_process')

const ROOT = path.resolve(__dirname, '..')
const VERIFIER_REL = path.join('tools', 'verify_f12_tabbar_poc.js')

// ------------------------------------------------------------------ 变异清单
// mutate 收到的是「已把换行统一成 \n」的文件内容；返回改坏后的内容。
// expect 里的每个子串都必须出现在校验脚本的失败列表中 —— 只要求「退出码非 0」
// 会放过「因为别的原因恰好失败」这种假检出。
const MUTATIONS = [
  {
    id: 'fab-lift-removed',
    file: 'miniprogram/custom-tab-bar/index.wxss',
    why: '凸起圆形不再上浮（top: -20rpx → 0）',
    mutate: (t) => t.replace('  top: -20rpx;', '  top: 0rpx;'),
    expect: ['wxss 定义圆形', '凸起量符合方案 §1.4'],
  },
  {
    id: 'fab-not-a-circle',
    file: 'miniprogram/custom-tab-bar/index.wxss',
    why: '凸起按钮不再是圆形（border-radius 50% → 8rpx）',
    mutate: (t) => t.replace('  border-radius: 50%;', '  border-radius: 8rpx;'),
    expect: ['wxss 定义圆形'],
  },
  {
    id: 'fab-off-center',
    file: 'miniprogram/custom-tab-bar/index.wxss',
    why: '凸起圆形不再水平居中（left: 50% → 0）—— 验收点①「凸起位置正确」的可自动化部分',
    mutate: (t) => t.replace('  left: 50%;', '  left: 0;'),
    expect: ['凸起圆形水平居中'],
  },
  {
    id: 'fab-box-sizing-changed',
    file: 'miniprogram/custom-tab-bar/index.wxss',
    why: '凸起圆形改用 content-box（96rpx 不含描边，实际直径变成 100rpx）',
    mutate: (t) => t.replace('  border: 2rpx solid rgba(74, 144, 217, 0.55);\n  box-sizing: border-box;\n', '  border: 2rpx solid rgba(74, 144, 217, 0.55);\n'),
    expect: ['凸起圆形按 border-box 计算尺寸'],
  },
  {
    id: 'ring-colour-changed',
    file: 'miniprogram/custom-tab-bar/index.wxss',
    why: '凸起圆形的描边不再是蓝色（§1.4「蓝色描边」）',
    mutate: (t) => t.replace('  border-color: rgba(74, 144, 217, 0.55);', '  border-color: #cccccc;'),
    expect: ['描边确实是「蓝色」'],
  },
  {
    id: 'tabbar-height-grown',
    file: 'miniprogram/custom-tab-bar/index.wxss',
    why: '底栏长高到 160rpx，页面留白不再够（证明是「比大小」而非「比字面量」）',
    mutate: (t) => t.replace('  height: 110rpx;', '  height: 160rpx;'),
    expect: [
      'pages/index/index.wxss 的 .page 底部留白',
      'pages/chat/chat.wxss 的 .chat-page 底部留白',
      '论坛 .fab 已抬到底栏之上',
    ],
  },
  {
    id: 'tabbar-safe-area-dropped',
    file: 'miniprogram/custom-tab-bar/index.wxss',
    why: '底栏只剩旧写法 constant()、丢了 env()（页面留白的对齐前提被破坏）',
    mutate: (t) => t.replace('  padding-bottom: env(safe-area-inset-bottom);\n', ''),
    expect: ['.tabbar 自身带底部安全区内边距'],
  },
  {
    id: 'index-clearance-legacy-safe-area',
    file: 'miniprogram/pages/index/index.wxss',
    why: '首页留白退化成旧写法 constant()（现代 WebView 不认，等于没留安全区）',
    mutate: (t) => t.replace('env(safe-area-inset-bottom)', 'constant(safe-area-inset-bottom)'),
    expect: ['pages/index/index.wxss 的 .page 底部留白'],
  },
  {
    id: 'forum-fab-legacy-safe-area',
    file: 'miniprogram/pages/forum/forum.wxss',
    why: '论坛悬浮按钮退化成旧写法 constant()',
    mutate: (t) => t.replace('env(safe-area-inset-bottom)', 'constant(safe-area-inset-bottom)'),
    expect: ['论坛 .fab 已抬到底栏之上'],
  },
  {
    id: 'index-clearance-too-small',
    file: 'miniprogram/pages/index/index.wxss',
    why: '首页底部留白 134rpx → 80rpx（小于底栏 110rpx）',
    mutate: (t) => t.replace('calc(134rpx + env(safe-area-inset-bottom))', 'calc(80rpx + env(safe-area-inset-bottom))'),
    expect: ['pages/index/index.wxss 的 .page 底部留白'],
  },
  {
    id: 'service-safe-area-dropped',
    file: 'miniprogram/pages/service/service.wxss',
    why: '服务页留白不再叠加安全区（iPhone 上会被底栏压住）',
    mutate: (t) => t.replace('calc(134rpx + env(safe-area-inset-bottom))', '134rpx'),
    expect: ['pages/service/service.wxss 的 .xj-page 底部留白'],
  },
  {
    id: 'forum-fab-under-tabbar',
    file: 'miniprogram/pages/forum/forum.wxss',
    why: '论坛悬浮按钮退回 80rpx（重新被底栏盖住）',
    mutate: (t) => t.replace('calc(126rpx + env(safe-area-inset-bottom))', '80rpx'),
    expect: ['论坛 .fab 已抬到底栏之上'],
  },
  {
    id: 'ai-item-no-longer-raised',
    file: 'miniprogram/utils/tab-order.js',
    why: 'AI 项不再是凸起项',
    mutate: (t) => t.replace(", raised: true }", ' }'),
    expect: ['恰好 1 项标记 raised'],
  },
  {
    id: 'raised-moved-off-center',
    file: 'miniprogram/utils/tab-order.js',
    why: '凸起项从「正中」挪到第 2 项（凸起会偏左）',
    mutate: (t) =>
      t
        .replace("{ key: 'service', pagePath: '/pages/service/service', text: '服务' }", "{ key: 'service', pagePath: '/pages/service/service', text: '服务', raised: true }")
        .replace("{ key: 'chat', pagePath: '/pages/chat/chat', text: 'AI助手', raised: true }", "{ key: 'chat', pagePath: '/pages/chat/chat', text: 'AI助手' }"),
    expect: ['raised 项位于正中'],
  },
  {
    id: 'tab-order-diverged',
    file: 'miniprogram/utils/tab-order.js',
    why: 'TAB_ORDER 与 app.json 的 Tab 顺序不一致',
    mutate: (t) => t.replace('TAB_LIST.map((item) => item.key)', 'TAB_LIST.map((item) => item.key).reverse()'),
    expect: ['TAB_ORDER 与 app.json 的 Tab 顺序一致'],
  },
  {
    id: 'longpress-unbound',
    file: 'miniprogram/custom-tab-bar/index.wxml',
    why: '长按不再绑定处理函数',
    mutate: (t) => t.replace('    bindlongpress="onItemLongPress"\n', ''),
    expect: ['wxml 绑定 bindlongpress'],
  },
  {
    id: 'hidden-class-unbound',
    file: 'miniprogram/custom-tab-bar/index.wxml',
    why: '全屏态不再挂 tabbar--hidden 类',
    mutate: (t) => t.replace("{{hidden ? 'tabbar--hidden' : ''}}", "''"),
    expect: ['wxml 用 hidden 控制'],
  },
  {
    id: 'hidden-class-empty',
    file: 'miniprogram/custom-tab-bar/index.wxss',
    why: '.tabbar--hidden 变成空规则（类还在，但什么也不隐藏）',
    mutate: (t) => t.replace('  transform: translateY(110%);\n  opacity: 0;\n', ''),
    expect: ['.tabbar--hidden 规则体真的会隐藏'],
  },
  {
    id: 'fullscreen-padding-not-returned',
    file: 'miniprogram/pages/chat/chat.wxss',
    why: '全屏态不把底栏留白还回去（"全屏"只藏底栏、输入条仍悬空 110rpx）',
    mutate: (t) => t.replace('  padding-bottom: 0;', '  padding-bottom: 110rpx;'),
    expect: ['全屏态把底栏留白还回去'],
  },
  {
    id: 'fullscreen-class-unbound',
    file: 'miniprogram/pages/chat/chat.wxml',
    why: 'chat 根容器不再挂 chat-page--fullscreen 类',
    mutate: (t) => t.replace(" {{tabBarHidden ? 'chat-page--fullscreen' : ''}}", ''),
    expect: ['chat.wxml 在全屏态真的挂上'],
  },
  {
    id: 'longpress-fires-everywhere',
    file: 'miniprogram/custom-tab-bar/index.js',
    why: '去掉「仅 AI 项响应长按」的判断（所有 tab 都弹语音）',
    mutate: (t) => t.replace("      if (raised !== true && raised !== 'true') return\n", ''),
    expect: ['反向对照：长按非 AI 项不震动不弹窗'],
  },
  {
    id: 'vibrate-removed',
    file: 'miniprogram/custom-tab-bar/index.js',
    why: '长按不再震动（验收点④的一半缺失）',
    mutate: (t) => t.replace("wx.vibrateShort({ type: 'medium', fail: () => {} })", '/* 变异：去掉震动 */'),
    expect: ['长按 AI 项触发震动'],
  },
  {
    id: 'switchtab-silent',
    file: 'miniprogram/custom-tab-bar/index.js',
    why: '切换失败只 console.error、不给用户反馈（"点了没反应"）',
    mutate: (t) => t.replace("          wx.showToast({ title: '切换失败，请重试', icon: 'none' })\n", ''),
    expect: ['切换失败时给出 toast 反馈'],
  },
  {
    id: 'recorder-error-silent',
    file: 'miniprogram/custom-tab-bar/index.js',
    why: '录音回调被静默丢弃（README §2 承诺的"拒绝授权后弹录音失败"失效）',
    mutate: (t) => t.replace('if (!this._recorderPending) return', 'if (true) return // 变异：静默丢弃所有回调'),
    expect: ['录音失败时弹出「录音失败」'],
  },
  {
    id: 'fake-transcribe-call',
    file: 'miniprogram/custom-tab-bar/index.js',
    why: '组件偷偷发起网络请求（假装转写已就绪 / POC 上传了录音）',
    mutate: (t) => t.replace('const RECORDER_CHECK_MS = 2500', "const RECORDER_CHECK_MS = 2500\nconst _fakeTranscribe = () => wx.request({ url: 'https://x/voice/transcribe' })"),
    expect: ['组件不发起任何网络请求'],
  },
  {
    id: 'header-poc-claim-removed',
    file: 'miniprogram/utils/tabbar.js',
    why: '助手模块头不再声明 POC 边界（正文/其它注释里仍有 POC 字样 → 只有"认文件头"才抓得到）',
    mutate: (t) =>
      t
        .replace('// F12 POC：自定义 TabBar 助手', '// F12：自定义 TabBar 助手')
        .replace('POC 阶段只做', '本阶段只做'),
    expect: ['声明 POC 边界'],
  },
  {
    id: 'chat-onshow-no-sync',
    file: 'miniprogram/pages/chat/chat.js',
    why: 'AI 页 onShow 不再同步选中项',
    mutate: (t) => t.replace("    syncTabBar(this, 'chat')\n", ''),
    expect: ['pages/chat/chat.js 在 onShow 同步选中项'],
  },
  {
    id: 'fullscreen-button-lies',
    file: 'miniprogram/pages/chat/chat.js',
    why: '全屏按钮先改 data 再调用（会显示「显示底栏」但底栏没被隐藏）',
    mutate: (t) =>
      t.replace(
        "    // 先尝试，成功才改状态 —— 否则按钮会显示「显示底栏」但底栏其实没被隐藏（假反馈）\n    if (!setTabBarHidden(this, next)) {",
        '    this.setData({ tabBarHidden: next })\n    if (!setTabBarHidden(this, next)) {'
      ).replace('    this.setData({ tabBarHidden: next })\n  },', '  },'),
    expect: ['全屏按钮先调用成功后才改 data'],
  },
  {
    id: 'readme-count-stale',
    file: 'miniprogram/custom-tab-bar/README.md',
    why: 'README 里写死的断言条数与脚本不一致',
    mutate: (t) => t.replace('80 项断言', '99 项断言'),
    expect: ['README 声明的断言条数'],
  },
  {
    id: 'readme-var-count-stale',
    file: 'miniprogram/custom-tab-bar/README.md',
    why: 'README 里写死的 var() fallback 数与 wxss 实际不一致',
    mutate: (t) => t.replace('15/15 处', '7/7 处'),
    expect: ['README 声明的 var() fallback 数'],
  },
  {
    id: 'readme-disclaimer-softened',
    file: 'miniprogram/custom-tab-bar/README.md',
    why: 'README 把"代码级断言不能替代真机"弱化成一句含糊话（结论口径失真）',
    mutate: (t) => t.replace('代码级断言**不能**替代', '代码级断言不等同于'),
    expect: ['README 明确标注断言为代码级'],
  },
]

// ------------------------------------------------------------------ 沙箱准备

function copyTree(from, to) {
  fs.cpSync(from, to, { recursive: true })
}

function makeSandbox() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'f12-negctl-'))
  copyTree(path.join(ROOT, 'miniprogram'), path.join(dir, 'miniprogram'))
  fs.mkdirSync(path.join(dir, 'tools'), { recursive: true })
  fs.copyFileSync(path.join(ROOT, VERIFIER_REL), path.join(dir, VERIFIER_REL))
  return dir
}

/**
 * 在沙箱里**真实地**跑一次校验脚本（真子进程、真退出码）。
 * stdout/stderr 用文件描述符重定向而不是 pipe：受限沙箱下程序无法开管道，
 * 重定向到临时文件既兼容又拿得到完整输出。
 */
function runVerifier(sandbox) {
  const outFile = path.join(sandbox, 'verifier-output.txt')
  const fd = fs.openSync(outFile, 'w')
  let res
  try {
    res = spawnSync(process.execPath, [path.join(sandbox, VERIFIER_REL)], {
      cwd: sandbox,
      stdio: ['ignore', fd, fd],
    })
  } finally {
    fs.closeSync(fd)
  }
  const text = fs.readFileSync(outFile, 'utf8')
  if (res.error) return { status: -1, text: `${text}\n[spawn error] ${res.error.message}` }
  return { status: res.status, text }
}

// 读文件时把换行统一成 \n 再做替换，写回时还原原文件的换行风格。
function readNormalized(abs) {
  const raw = fs.readFileSync(abs, 'utf8')
  return { raw, eol: raw.includes('\r\n') ? '\r\n' : '\n', text: raw.replace(/\r\n/g, '\n') }
}

// ------------------------------------------------------------------ 主流程

let detected = 0
const problems = []
let sandbox = null

try {
  sandbox = makeSandbox()
  console.log(`沙箱：${sandbox}\n（工作区未被触碰；所有变异只作用于该临时副本）\n`)

  console.log('基线：未变异时必须 PASS')
  const base = runVerifier(sandbox)
  const basePass = base.status === 0 && /\[PASS\]/.test(base.text)
  console.log(`  ${basePass ? '[OK]' : '[NG]'} 基线退出码=${base.status}，输出 ${basePass ? '含 [PASS]' : '不含 [PASS]'}`)
  if (!basePass) {
    problems.push('基线未通过 —— 副本不干净或校验脚本本身有问题，后面的变异结论无意义')
  }

  // 校验脚本头部写死了变异数量；那份文档同样会漂移，这里绑定到实际值。
  const verifierSrc = fs.readFileSync(path.join(ROOT, VERIFIER_REL), 'utf8')
  const documented = Number((verifierSrc.match(/(\d+)\s*个已知缺陷/) || [])[1])
  const docOk = documented === MUTATIONS.length
  console.log(
    `\n  ${docOk ? '[OK]' : '[NG]'} 校验脚本头部声明的变异数与实际一致（声明 ${documented || '未声明'}，实际 ${MUTATIONS.length}）`
  )
  if (!docOk) problems.push(`校验脚本头部写的是 ${documented} 个缺陷，实际有 ${MUTATIONS.length} 个`)

  console.log(`\n注入 ${MUTATIONS.length} 个已知缺陷，逐个要求被「指定断言」抓到：`)
  for (const m of MUTATIONS) {
    const abs = path.join(sandbox, m.file)
    const pristine = readNormalized(abs)
    const mutated = m.mutate(pristine.text)

    if (mutated === pristine.text) {
      console.log(`  [NG] ${m.id}：变异未生效（源串没匹配上，等于没测）`)
      problems.push(`${m.id}：变异未匹配到目标代码`)
      continue
    }

    fs.writeFileSync(abs, mutated.replace(/\n/g, pristine.eol))
    let res
    try {
      res = runVerifier(sandbox)
    } finally {
      fs.writeFileSync(abs, pristine.raw) // 无论成败都还原副本
    }

    const missed = m.expect.filter((needle) => !res.text.includes(needle))
    const ok = res.status !== 0 && missed.length === 0
    if (ok) {
      detected += 1
      console.log(`  [OK] ${m.id} —— ${m.why}`)
    } else {
      console.log(`  [NG] ${m.id} —— ${m.why}`)
      console.log(`       退出码=${res.status}（应非 0）；未抓到的断言=${missed.length ? missed.join(' / ') : '无'}`)
      problems.push(`${m.id}：${res.status === 0 ? '缺陷未被检出（校验脚本 PASS 了）' : `未命中指定断言 ${missed.join(' / ')}`}`)
    }
  }
} finally {
  if (sandbox) fs.rmSync(sandbox, { recursive: true, force: true })
}

console.log(`\n${'-'.repeat(60)}`)
if (problems.length === 0) {
  console.log(`[PASS] 阴性对照通过：基线 PASS，且 ${detected}/${MUTATIONS.length} 个注入缺陷全部被指定断言抓到`)
  console.log('       结论：verify_f12_tabbar_poc.js 不是「只会 PASS」的脚本。')
  process.exit(0)
} else {
  console.log(`[FAIL] 阴性对照未通过（检出 ${detected}/${MUTATIONS.length}）：`)
  for (const p of problems) console.log(`  - ${p}`)
  process.exit(1)
}
