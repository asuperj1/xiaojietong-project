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
 * ⚠️ v2（2026-09-14，二次审查时修正）
 * --------------------------------
 * v1 有两个问题，都会**误导**使用者：
 *  ① 「修法验证」写死了逐字把 `getAppBaseInfo` 换成 `getDeviceInfo`。缺陷修好之后
 *     源码里已找不到旧那一行 → 工具报 `[FAIL] …实现已变化？` 并返回 **EXIT=1**，
 *     而实际上缺陷已修 —— 让作者以为没修好（真实踩到）。
 *  ② 场景桩里**没有 `getDeviceInfo`** → 修复后的**新代码分支根本没被执行**：
 *     旧写法在这里靠 `getSystemInfoSync()` 兜底也能“通过”，覆盖是假的。
 * v2 改为**状态自适应**：识别源码是新/旧写法，另建一份“**另一种实现**”做反向对照；
 *   两种状态下用**同一组场景**跑两份实现，**行为必须不同**（R3）—— 这是断言非空的保证。
 *
 * 用法
 * ----
 *     node tools/verify_glass_probe.js                     # 测当前仓库
 *     node tools/verify_glass_probe.js --src <worktree路径>  # 测某个工作树（如 PR 分支）
 *
 * 退出码：0 = 当前实现的**行为契约全部满足**；1 = 有场景不满足（缺陷仍在 / 行为退化）。
 *
 * v3（PR #109 评审 P2-1，2026-09-16）
 * --------------------------------
 * 补 Android 阈值**边界对** S4b（`Android 9` → 期望 true）/ S4c（`Android 8.9` → 期望 false）：
 * v2 的 Android 采样点只有 `Android 12` 与 `Android 8.1.0`，导致 `glass.js` 的
 * 「Android ≥ 9」规则**边界没被锁住** —— 独立评审实测把阈值改成 10 / 11 / 12 时脚本仍 exit 0。
 *
 * ⚠️ 验证范围（PR #109 评审 P2-2 补写；引用本脚本时请按此口径，不要外推）
 * --------------------------------------------------------------------
 *   ✅ **只**验证 `miniprogram/utils/glass.js::detectGlass()` 的**返回值契约**
 *      （场景 S1~S9 + 边界对 S4b/S4c + 反向对照 R1~R3）。
 *   ❌ **不**读取、**不**校验 `miniprogram/styles/glass.wxss` 与 `miniprogram/styles/tokens.wxss`：
 *      实测改动这两个 WXSS 后本脚本**仍然 exit 0**。
 *   ⇒ F10 的三个产物里，目前只有 `utils/glass.js` 有自动化校验；两个 WXSS
 *     （令牌值、`.xj-glass` 声明、`.is-glass-fallback` / `.is-glass-reduced` 三级降级）
 *     **仍属人工检查范围**：需目视 + 真机 / 开发者工具确认。
 *   ⇒ 不要把「本脚本 PASS」读成「F10 整体已被验证」。
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
/** 阈值下界：glass.js 规则是「Android ≥ 9」，9 本身必须为 true */
const DEVICE_INFO_ANDROID9 = { brand: 'google', model: 'PIXEL 3', platform: 'android', system: 'Android 9' }
/** 紧邻下界**之下**的合成版本号（8.9 非真实版本），只用于把阈值从下方夹死 */
const DEVICE_INFO_ANDROID89 = { brand: 'synthetic', model: 'SYNTHETIC', platform: 'android', system: 'Android 8.9' }

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

// 「另一种实现」：只改一行 —— 取 system 的字段来源（新老两种写法，双向可逆）
//   旧：typeof wx.getAppBaseInfo === 'function' ? wx.getAppBaseInfo() : wx.getSystemInfoSync()
//   新：typeof wx.getDeviceInfo   === 'function' ? wx.getDeviceInfo()   : wx.getSystemInfoSync()
const OLD_LINE =
  "typeof wx.getAppBaseInfo === 'function' ? wx.getAppBaseInfo() : wx.getSystemInfoSync()"
const NEW_LINE =
  "typeof wx.getDeviceInfo === 'function' ? wx.getDeviceInfo() : wx.getSystemInfoSync()"
const raw = fs.readFileSync(REAL_FILE, 'utf8')
const isFixed = raw.includes(NEW_LINE)
const ALT_FILE = path.join(SANDBOX, 'glass.alt.js')
const altSrc = isFixed ? raw.replace(NEW_LINE, OLD_LINE) : raw.replace(OLD_LINE, NEW_LINE)
const altApplicable = altSrc !== raw
fs.writeFileSync(ALT_FILE, altSrc, 'utf8')
const ALT_LABEL = isFixed ? '回退到原始写法（getAppBaseInfo）' : '改为修复写法（getDeviceInfo）'

// ================================================================ 开始 ====

console.log(`被测文件：${SRC_FILE}`)
console.log(
  `当前实现：${isFixed ? '✅ 修复后写法（getDeviceInfo）' : '⚠️  原始写法（getAppBaseInfo）'}`
)
console.log(`沙箱：${SANDBOX}`)

// ------------------------------------------------- 一、复现：真实源码 ----

bar('一、当前实现（真实源码）在各机型上的返回（期望值 = 设计契约）')

// 基础库 ≥ 2.20.1 的**真实**环境：三个接口都在。
//   `getAppBaseInfo` 按官方文档**不含 system**；`system` 在 getDeviceInfo / getSystemInfoSync 里。
const baseLibNew = (device) => ({
  getAppBaseInfo: () => ({ ...APP_BASE_DOC }),
  getDeviceInfo: () => ({ ...device }),
  getSystemInfoSync: () => ({ ...device }),
})

check(
  'S1 iOS 15.4 + 基础库 3.17.3 → 应启用毛玻璃',
  run(REAL_FILE, baseLibNew(DEVICE_INFO_IOS)),
  true,
  'iOS 按设计默认允许；恒为 false 即为缺陷'
)
check(
  'S2 Android 12 + 基础库 3.17.3 → 应启用（系统 ≥ 9）',
  run(REAL_FILE, baseLibNew(DEVICE_INFO_ANDROID12)),
  true
)
check(
  'S3 开发者工具（Windows 宿主）→ 应允许，便于预览',
  run(REAL_FILE, baseLibNew(SYS_INFO_DEVTOOL_WIN)),
  true
)
check(
  'S4 Android 8.1 + 基础库 3.17.3 → 应保守 false',
  run(REAL_FILE, baseLibNew(DEVICE_INFO_ANDROID8)),
  false,
  '反向对照：修复也不该“过度开启”低版本 Android'
)

// ⭐ S4b / S4c：Android 阈值**边界对** —— 锁死 glass.js 的「Android 系统版本 ≥ 9」规则。
//   加这对场景的原因（PR #109 独立评审 P2-1）：原脚本的 Android 采样点只有
//   `Android 12` 与 `Android 8.1.0` 两个，于是**任何落在 (8.1, 12] 的阈值都能蒙混过关** ——
//   实测把 `>= 9` 改成 `>= 10 / 11 / 12` 时，脚本仍 exit 0（假绿）。
//   边界对把规则夹死：下界含（9 → true）+ 下界之下（8.9 → false）。
check(
  'S4b Android 9（阈值下界，**含**）+ 基础库 3.17.3 → 必须 true',
  run(REAL_FILE, baseLibNew(DEVICE_INFO_ANDROID9)),
  true,
  '⭐ 边界锁：阈值 9→10 / 11 / 12 会在此 FAIL（否则 Android 9/10/11 被静默降级、CI 仍全绿）'
)
check(
  'S4c Android 8.9（阈值下界**之下**，合成版本号）+ 基础库 3.17.3 → 必须 false',
  run(REAL_FILE, baseLibNew(DEVICE_INFO_ANDROID89)),
  false,
  '⭐ 边界锁：防止阈值被下调（如 9→8.5）后低版本 Android 被过度开启'
)

// ⭐ S5 / S6：**只有 getDeviceInfo 可用** —— 新写法的“唯一通路”。
//   旧写法在这里会走到 `wx.getSystemInfoSync()`（undefined）→ TypeError → false。
//   ⇒ 这条专治 v1 的假通过（桩里没给 getDeviceInfo，靠老接口兜底）。
const onlyDevice = (device) => ({ getDeviceInfo: () => ({ ...device }) })
check(
  'S5 iOS 15.4，仅有 getDeviceInfo（无 getAppBaseInfo / getSystemInfoSync）→ true',
  run(REAL_FILE, onlyDevice(DEVICE_INFO_IOS)),
  true,
  '⭐ 这条**只**能被 getDeviceInfo 满足'
)
check(
  'S6 Android 8.1，仅有 getDeviceInfo → 仍应 false',
  run(REAL_FILE, onlyDevice(DEVICE_INFO_ANDROID8)),
  false
)

// 老基础库（< 2.20.1）：既没 getAppBaseInfo 也没 getDeviceInfo，只有 getSystemInfoSync
const legacy = (device) => ({ getSystemInfoSync: () => ({ ...device }) })
check(
  'S7 老基础库仅有 getSystemInfoSync（iOS）→ true',
  run(REAL_FILE, legacy(SYS_INFO_IOS)),
  true
)

// 兜底：探测接口全抛异常 → 必须降级 false 且不崩
check(
  'S8 探测接口全部抛异常 → 必须降级 false（不能崩）',
  run(REAL_FILE, {
    getAppBaseInfo: () => {
      throw new Error('boom')
    },
    getDeviceInfo: () => {
      throw new Error('boom')
    },
    getSystemInfoSync: () => {
      throw new Error('boom')
    },
  }),
  false
)

// 极端：只有 getAppBaseInfo（文档字段、无 system），无其它接口 → false 且不崩
check(
  'S9 仅有 getAppBaseInfo（无 system）→ 必须 false 且不崩',
  run(REAL_FILE, { getAppBaseInfo: () => ({ ...APP_BASE_DOC }) }),
  false
)

// ------------------------------------------------- 二、验证修法有效 ----

bar(`二、反向对照：加跑一份「${ALT_LABEL}」，同一组场景行为**必须不同**`)

if (!altApplicable) {
  console.log('  [FAIL] 源码里既找不到新写法也找不到旧写法 → 无法构造另一种实现，请人工确认')
  fail++
} else {
  const realS1 = run(REAL_FILE, baseLibNew(DEVICE_INFO_IOS))
  const realS5 = run(REAL_FILE, onlyDevice(DEVICE_INFO_IOS))
  const altS1 = run(ALT_FILE, baseLibNew(DEVICE_INFO_IOS))
  const altS5 = run(ALT_FILE, onlyDevice(DEVICE_INFO_IOS))

  if (isFixed) {
    check(
      'R1 把接口回退成 getAppBaseInfo 后，iOS（基础库 3.17.3）→ 应**复现缺陷** false',
      altS1,
      false,
      '同一组桩、同一台“设备”，唯一差别就是取 system 的接口 —— 行为相反'
    )
    check('R2 回退版：仅有 getDeviceInfo 时 → false（TypeError 被兜底）', altS5, false)
  } else {
    check('R1 改成 getDeviceInfo 后，iOS（基础库 3.17.3）→ 缺陷应被消除 true', altS1, true)
    check('R2 修复版：仅有 getDeviceInfo 时 → true', altS5, true)
  }

  check(
    'R3 两种实现在 S1 上行为**必须不同**（证明这组场景有区分度，不是空跑）',
    realS1 === altS1,
    false,
    `当前实现 S1=${realS1} / S5=${realS5}；${ALT_LABEL} S1=${altS1} / S5=${altS5}`
  )
}

// ---------------------------------------------------------------- 结论 ----

bar('结论')
console.log(
  `  被测实现：${isFixed ? '修复后写法（getDeviceInfo）' : '原始写法（getAppBaseInfo）'}`
)
console.log(`  通过 ${pass} 项 / 失败 ${fail} 项`)
console.log('  ⚠️ 验证范围：**仅** `miniprogram/utils/glass.js` 的 `detectGlass()` 返回值契约。')
console.log('     本脚本**不读** `styles/glass.wxss`、`styles/tokens.wxss` —— 改动这两个 WXSS')
console.log('     后本脚本**仍会 exit 0**；WXSS 相关内容（令牌值 / `.xj-glass` 声明 / 三级降级）')
console.log('     **仍属人工检查范围**（目视 + 真机 / 开发者工具），不要用本脚本的 PASS 代替。')
console.log('  ⚠️ 本工具**不复现**视觉问题，只验证 `detectGlass()` 的返回值契约。')
console.log('  ⚠️ 另需人工确认：`.is-glass-fallback` / `.is-glass-reduced` 是**祖先选择器**')
console.log('     （`.is-glass-fallback .xj-glass`），而小程序无法给 `page` 加 class，')
console.log('     必须由各页面在根节点包一层；本 PR 内暂无任何页面消费。')
console.log('='.repeat(84))

process.exit(fail === 0 ? 0 : 1)
