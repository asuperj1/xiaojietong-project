#!/usr/bin/env node
/**
 * PR #59（F10 玻璃能力探测）行为验证工具
 *
 * 背景
 * ----
 * `miniprogram/utils/glass.js::detectGlass()` 靠 `info.system` 判断机型/系统版本：
 *
 *     const info =
 *       typeof wx.getAppBaseInfo === 'function' ? wx.getAppBaseInfo() : wx.getSystemInfoSync()
 *     const system = (info && info.system) || ''
 *
 * ⚠️ 但按微信官方文档，`wx.getAppBaseInfo()` 的返回字段是
 *    `SDKVersion / enableDebug / host / language / version / PCKernelVersion / theme /
 *     fontSizeScaleFactor / fontSizeSetting` —— **没有 `system`**。
 *    `system` 在 `wx.getSystemInfoSync()` 与 `wx.getDeviceInfo()` 里。
 *    → 基础库 ≥ 2.20.1 时走 `getAppBaseInfo()` 分支 ⇒ `system` 恒为 `''`
 *      ⇒ 所有正则都不命中 ⇒ **`detectGlass()` 恒返回 false**。
 *
 * 本工具不依赖微信开发者工具：在 Node 里 stub `global.wx`，直接加载**真实**的
 * `miniprogram/utils/glass.js` 调用它，断言各场景的返回值。
 *
 * 用法
 * ----
 *     node tools/verify_glass_probe.js                     # 测当前仓库
 *     node tools/verify_glass_probe.js --src <worktree路径>  # 测某个工作树（如 PR 分支）
 *
 * 退出码：0 = 全部场景符合预期（= 缺陷已修）；1 = 有场景不符合（= 缺陷仍在）。
 *
 * 文档依据：https://developers.weixin.qq.com/miniprogram/dev/api/base/system/wx.getAppBaseInfo.html
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
const GLASS_REL = path.join('miniprogram', 'utils', 'glass.js')
const SRC_FILE = path.join(SRC_ROOT, GLASS_REL)

if (!fs.existsSync(SRC_FILE)) {
  console.log(`[FAIL] 找不到 ${SRC_FILE}`)
  process.exit(1)
}

// ---------------------------------------------------------------- 断言 ----

let pass = 0
let fail = 0

function check(name, actual, expected, note) {
  const ok = actual === expected
  ok ? pass++ : fail++
  console.log(
    `  [${ok ? 'PASS' : 'FAIL'}] ${name}\n` +
      `         期望 detectGlass()=${expected}  实际=${actual}` +
      (note ? `\n         ${note}` : '')
  )
  return ok
}

function bar(title) {
  console.log('\n' + '='.repeat(84))
  console.log(title)
  console.log('='.repeat(84))
}

// ------------------------------------------------------------ 场景用桩 ----

/** 按**官方文档字段**构造 getAppBaseInfo 返回值（故意不含 system） */
const APP_BASE_DOC = {
  SDKVersion: '3.17.3',
  enableDebug: false,
  host: { appId: 'wx6ccc5c4c02b31455' },
  language: 'zh_CN',
  version: '8.0.50',
  theme: 'light',
}

/** 老接口 getSystemInfoSync：**有** system */
const SYS_INFO_IOS = { platform: 'ios', system: 'iOS 15.4', model: 'iPhone 13' }
const SYS_INFO_ANDROID12 = { platform: 'android', system: 'Android 12', model: 'PIXEL 5' }
const SYS_INFO_ANDROID8 = { platform: 'android', system: 'Android 8.1.0', model: 'MI 5' }
const SYS_INFO_DEVTOOL_WIN = { platform: 'windows', system: 'Windows 10 x64', model: 'devtools' }

/** 新接口 getDeviceInfo：**有** system */
const DEVICE_INFO_IOS = { brand: 'apple', model: 'iPhone 13', platform: 'ios', system: 'iOS 15.4' }
const DEVICE_INFO_ANDROID12 = { brand: 'google', model: 'PIXEL 5', platform: 'android', system: 'Android 12' }
const DEVICE_INFO_ANDROID8 = { brand: 'xiaomi', model: 'MI 5', platform: 'android', system: 'Android 8.1.0' }

function loadModule(file) {
  delete require.cache[require.resolve(file)]
  return require(file)
}

/**
 * 用给定的 wx 桩调用 detectGlass()
 * @param {string} file  glass.js 路径（沙箱内）
 * @param {object} wxStub
 */
function run(file, wxStub) {
  global.wx = wxStub
  return loadModule(file).detectGlass()
}

// ---------------------------------------------------------------- 沙箱 ----

const SANDBOX = fs.mkdtempSync(path.join(os.tmpdir(), 'xjt-glass-'))
const REAL_FILE = path.join(SANDBOX, 'glass.js')
fs.copyFileSync(SRC_FILE, REAL_FILE)

// 「修复版」：只改一行 —— 取 system 的字段来源
//   原：typeof wx.getAppBaseInfo === 'function' ? wx.getAppBaseInfo() : wx.getSystemInfoSync()
//   新：typeof wx.getDeviceInfo   === 'function' ? wx.getDeviceInfo()   : wx.getSystemInfoSync()
const FIXED_FILE = path.join(SANDBOX, 'glass.fixed.js')
const raw = fs.readFileSync(REAL_FILE, 'utf8')
const NEEDLE =
  "typeof wx.getAppBaseInfo === 'function' ? wx.getAppBaseInfo() : wx.getSystemInfoSync()"
const REPLACEMENT =
  "typeof wx.getDeviceInfo === 'function' ? wx.getDeviceInfo() : wx.getSystemInfoSync()"
const fixedSrc = raw.replace(NEEDLE, REPLACEMENT)
const fixApplicable = fixedSrc !== raw
fs.writeFileSync(FIXED_FILE, fixedSrc, 'utf8')

// ================================================================ 开始 ====

console.log(`被测文件：${SRC_FILE}`)
console.log(`沙箱：${SANDBOX}`)

// ------------------------------------------------- 一、复现：真实源码 ----

bar('一、当前实现（真实源码）在各机型上的返回')

// 基础库 ≥ 2.20.1：getAppBaseInfo 存在（文档字段，无 system）→ 走新分支
const s1 = run(REAL_FILE, {
  getAppBaseInfo: () => ({ ...APP_BASE_DOC }),
  getSystemInfoSync: () => ({ ...SYS_INFO_IOS }),
})
check(
  'S1 iOS 15.4 + 基础库 3.17.3（getAppBaseInfo 存在）→ 按设计应启用毛玻璃',
  s1,
  true,
  '这是**决定性反例**：iOS 本该默认允许，实际恒为 false'
)

const s2 = run(REAL_FILE, {
  getAppBaseInfo: () => ({ ...APP_BASE_DOC }),
  getSystemInfoSync: () => ({ ...SYS_INFO_ANDROID12 }),
})
check('S2 Android 12 + 基础库 3.17.3 → 按设计应启用（系统版本 ≥ 9）', s2, true)

const s3 = run(REAL_FILE, {
  getAppBaseInfo: () => ({ ...APP_BASE_DOC }),
  getSystemInfoSync: () => ({ ...SYS_INFO_DEVTOOL_WIN }),
})
check('S3 开发者工具（Windows 宿主）→ 按注释应允许，便于预览', s3, true)

// 反向对照：如果 getAppBaseInfo **恰好带上了** system（未文档化的字段），行为立刻不同
const s4 = run(REAL_FILE, {
  getAppBaseInfo: () => ({ ...APP_BASE_DOC, system: 'iOS 15.4' }),
  getSystemInfoSync: () => ({ ...SYS_INFO_IOS }),
})
check(
  'S4 反向对照：若 getAppBaseInfo 返回里**带 system** → true',
  s4,
  true,
  '证明返回值**完全取决于 `system` 字段是否存在**，而非机型判断逻辑'
)

// 基础库 < 2.20.1：没有 getAppBaseInfo → 走 getSystemInfoSync（有 system）→ 行为正确
const s5 = run(REAL_FILE, { getSystemInfoSync: () => ({ ...SYS_INFO_IOS }) })
check('S5 老基础库（无 getAppBaseInfo，走 getSystemInfoSync）iOS → true', s5, true,
  '⇒ 同一台设备「基础库新」反而判成不支持，是**升级即退化**，不易被察觉')

const s6 = run(REAL_FILE, {
  getAppBaseInfo: () => ({ ...APP_BASE_DOC }),
  getSystemInfoSync: () => ({ ...SYS_INFO_ANDROID8 }),
})
check('S6 Android 8.1 + 基础库 3.17.3 → 应保守返回 false', s6, false)
check(
  'S7 Android 8.1 + 老基础库 → 应保守返回 false',
  run(REAL_FILE, { getSystemInfoSync: () => ({ ...SYS_INFO_ANDROID8 }) }),
  false
)

const s8 = run(REAL_FILE, {
  getAppBaseInfo: () => {
    throw new Error('boom')
  },
  getSystemInfoSync: () => {
    throw new Error('boom')
  },
})
check('S8 探测接口全部抛异常 → 必须降级 false（不能崩）', s8, false)

// ------------------------------------------------- 二、验证修法有效 ----

bar('二、修法验证：把取 system 的接口换成 getDeviceInfo（等价一行改动）')

if (!fixApplicable) {
  console.log('  [FAIL] 无法在源码里定位到那一行（实现已变化？），请人工确认')
  fail++
} else {
  const f1 = run(FIXED_FILE, {
    getDeviceInfo: () => ({ ...DEVICE_INFO_IOS }),
    getAppBaseInfo: () => ({ ...APP_BASE_DOC }),
    getSystemInfoSync: () => ({ ...SYS_INFO_IOS }),
  })
  check('F1 修复后：iOS 15.4 → true', f1, true)

  const f2 = run(FIXED_FILE, {
    getDeviceInfo: () => ({ ...DEVICE_INFO_ANDROID12 }),
    getAppBaseInfo: () => ({ ...APP_BASE_DOC }),
    getSystemInfoSync: () => ({ ...SYS_INFO_ANDROID12 }),
  })
  check('F2 修复后：Android 12 → true', f2, true)

  const f3 = run(FIXED_FILE, {
    getDeviceInfo: () => ({ ...DEVICE_INFO_ANDROID8 }),
    getAppBaseInfo: () => ({ ...APP_BASE_DOC }),
    getSystemInfoSync: () => ({ ...SYS_INFO_ANDROID8 }),
  })
  check('F3 修复后：Android 8.1 → 仍为 false（没有过度开启）', f3, false)

  const f4 = run(FIXED_FILE, { getSystemInfoSync: () => ({ ...SYS_INFO_IOS }) })
  check('F4 修复后：无 getDeviceInfo（老基础库）→ 走 getSystemInfoSync，iOS → true', f4, true)
}

// ---------------------------------------------------------------- 结论 ----

bar('结论')
console.log(`  通过 ${pass} 项 / 失败 ${fail} 项`)
console.log('  ⚠️ 本工具**不复现**视觉问题，只验证 `detectGlass()` 的返回值契约。')
console.log('  ⚠️ 另需人工确认：`.is-glass-fallback` / `.is-glass-reduced` 是**祖先选择器**')
console.log('     （`.is-glass-fallback .xj-glass`），而小程序无法给 `page` 加 class，')
console.log('     必须由各页面在根节点包一层；本 PR 内暂无任何页面消费。')
console.log('='.repeat(84))

process.exit(fail === 0 ? 0 : 1)
