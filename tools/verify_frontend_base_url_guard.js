#!/usr/bin/env node
/**
 * 验证 miniprogram 的 API 地址解析守卫（PR #53 审查 P1）
 *
 * 背景：`services/request.js` 在 `return new Promise(...)` **之前**同步调用
 * `getBaseUrl()`。release 且未配置 `RELEASE_BASE_URL` 时它会 throw，
 * 异常在 Promise 创建前就逃逸 -> 调用方的 `.catch()` 接不到、
 * sseRequest 的 `onError` 不触发、`request.js` 内 6 处 `wx.showToast` 也不会执行。
 * 症状：正式包若忘填地址 -> 按钮没反应 / 对话页永久 loading / 无任何提示。
 *
 * 本脚本在 **Node 里 stub `wx`**，直接加载 `miniprogram/` 的真实源码，
 * 实测各场景行为，无需微信开发者工具、无需网络。
 *
 * 用法：
 *     node tools/verify_frontend_base_url_guard.js
 * 退出码：0 = 全部通过；1 = 有失败
 *
 * 注意：脚本会故意打印若干条 `console.error`（那是被测代码的诊断日志，属预期行为）。
 * PowerShell 会把它渲染成红色错误块 —— 那只是显示效果、不代表失败；
 * 只看最后的 `[PASS]` / `[FAIL]` 与退出码。
 */

'use strict'

const fs = require('fs')
const os = require('os')
const path = require('path')

const ROOT = path.resolve(__dirname, '..')
const MP = path.join(ROOT, 'miniprogram')

const sandboxes = []
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

/**
 * 建一个隔离沙箱：复制真实源码，并把 env.js 的 RELEASE_BASE_URL 换成指定值。
 * （RELEASE_BASE_URL 是模块级 const，只能靠改源码来模拟「已配置」的正式包）
 *
 * ⚠️ 沙箱必须镜像 app.js 的**完整** require 图，而不是只放本用例关心的 env/request：
 * `app.js` 还 require 了 `./utils/glass`（F10 玻璃能力探测，onLaunch 里跑 detectGlass）。
 * 漏拷 `utils/` 会让 F10 与「API 地址守卫」合流后 app.js 直接 MODULE_NOT_FOUND，
 * 整个脚本在 S1 就崩掉 —— 与本用例要测的地址解析行为无关的假失败。
 */
function makeSandbox(releaseUrl) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'xjt-env-'))
  sandboxes.push(dir)
  fs.mkdirSync(path.join(dir, 'config'), { recursive: true })
  fs.mkdirSync(path.join(dir, 'services'), { recursive: true })
  fs.mkdirSync(path.join(dir, 'utils'), { recursive: true })

  const envSrc = fs
    .readFileSync(path.join(MP, 'config', 'env.js'), 'utf8')
    .replace(/const RELEASE_BASE_URL = .*/, `const RELEASE_BASE_URL = ${JSON.stringify(releaseUrl)}`)
  fs.writeFileSync(path.join(dir, 'config', 'env.js'), envSrc)
  fs.copyFileSync(path.join(MP, 'services', 'request.js'), path.join(dir, 'services', 'request.js'))
  // F10：app.js 的 require('./utils/glass')。真实拷贝而非打桩 —— 探测失败时
  // detectGlass() 自带 try/catch 降级 false，不会影响本用例的任何断言。
  fs.copyFileSync(path.join(MP, 'utils', 'glass.js'), path.join(dir, 'utils', 'glass.js'))
  fs.copyFileSync(path.join(MP, 'app.js'), path.join(dir, 'app.js'))
  return dir
}

/** 在沙箱里 stub wx / App 并加载模块，返回 { requestMod, app, calls, sentUrls } */
function loadSandbox(releaseUrl, envVersion, storage) {
  const dir = makeSandbox(releaseUrl)
  const calls = { toast: 0, modal: 0, relaunch: 0 }
  const sentUrls = []

  global.wx = {
    getAccountInfoSync: () => ({ miniProgram: { envVersion } }),
    getStorageSync: (k) => (storage && storage[k]) || '',
    setStorageSync: () => {},
    removeStorageSync: () => {},
    showToast: () => {
      calls.toast += 1
    },
    showModal: () => {
      calls.modal += 1
    },
    reLaunch: () => {
      calls.relaunch += 1
    },
    request: (opts) => {
      sentUrls.push(opts.url)
    },
  }

  let appInstance = null
  global.App = (obj) => {
    appInstance = obj
  }
  global.getApp = () => appInstance

  // 清掉该沙箱的模块缓存，保证每次都是干净加载
  Object.keys(require.cache).forEach((k) => {
    if (k.startsWith(dir)) delete require.cache[k]
  })

  const requestMod = require(path.join(dir, 'services', 'request.js'))
  require(path.join(dir, 'app.js')) // 执行 App({...}) 捕获实例

  return { requestMod, app: appInstance, calls, sentUrls }
}

/** 断言「调用 f 不会同步抛错」 */
function noSyncThrow(fn) {
  try {
    return { threw: null, value: fn() }
  } catch (e) {
    return { threw: e, value: null }
  }
}

async function main() {
  console.log('='.repeat(78))
  console.log('miniprogram API 地址解析守卫验证（PR #53 审查 P1）')
  console.log('='.repeat(78))

  // ------------------------------------------------ S1 release 未配置（核心场景）
  console.log('\n[S1] release 正式版 + RELEASE_BASE_URL 为空（发布配置错误）')
  {
    const { requestMod, app, calls, sentUrls } = loadSandbox('', 'release', {})

    const r1 = noSyncThrow(() => requestMod.request('/topics/hot'))
    check('request() 不抛同步异常', r1.threw === null, r1.threw && r1.threw.message)
    check('request() 返回 Promise', !!r1.value && typeof r1.value.then === 'function')

    let caught = null
    if (r1.value) await r1.value.catch((e) => { caught = e })
    check('.catch() 能接住（修复前接不到）', caught !== null && caught.code === 'ENV_BASE_URL_UNAVAILABLE')

    let onErrorCalled = 0
    const r2 = noSyncThrow(() =>
      requestMod.sseRequest('/chat/send', {}, { onError: () => { onErrorCalled += 1 } })
    )
    check('sseRequest() 不抛同步异常', r2.threw === null, r2.threw && r2.threw.message)
    check('sseRequest() 调用 onError（修复前不调用）', onErrorCalled === 1, 'called=' + onErrorCalled)
    check('sseRequest() 返回可 abort 的 task', !!r2.value && typeof r2.value.abort === 'function')

    check('用户能收到 toast 提示（不再完全静默）', calls.toast >= 1, 'toast=' + calls.toast)
    check('未发出任何真实请求', sentUrls.length === 0)

    const r3 = noSyncThrow(() => app.checkApiEnv())
    check('app.checkApiEnv() 不抛异常', r3.threw === null)
    check('启动自检弹出 showModal 明确提示', calls.modal === 1, 'modal=' + calls.modal)
  }

  // ------------------------------------------------ S2 release 地址非法（P2-1）
  console.log('\n[S2] release + RELEASE_BASE_URL 非法（协议 / 空壳 / 缺 scheme）')
  for (const bad of ['http://api.x.edu.cn/api/v1', 'https://', 'api.x.edu.cn/api/v1']) {
    const { requestMod } = loadSandbox(bad, 'release', {})
    let envErr = null
    try {
      requestMod.getBaseUrl()
    } catch (e) {
      envErr = e
    }
    check(`${JSON.stringify(bad)} 被拦下且提示必须 https`, !!envErr && /https:\/\//.test(envErr.message),
      envErr && envErr.message)
    const r = noSyncThrow(() => requestMod.request('/x'))
    check(`${JSON.stringify(bad)} 场景下 request() 仍不抛同步异常`, r.threw === null)
  }

  // ------------------------------------------------ S3 release 合法
  console.log('\n[S3] release + RELEASE_BASE_URL 合法')
  {
    const url = 'https://api.x.edu.cn/api/v1'
    const { requestMod, sentUrls } = loadSandbox(url, 'release', {
      xjt_api_base_url: 'http://evil.local/api/v1', // 必须被忽略
    })
    check('解析结果正确', requestMod.getBaseUrl() === url)
    check('release 忽略 storage 覆盖（测试地址不会带上线）', requestMod.getBaseUrl() === url)

    requestMod.request('/topics/hot')
    check('请求 URL 拼接正确', sentUrls[0] === url + '/topics/hot', sentUrls[0])
    requestMod.request('topics/hot')
    check('缺少前导 "/" 时自动补上', sentUrls[1] === url + '/topics/hot', sentUrls[1])
  }

  // ------------------------------------------------ S4 develop
  console.log('\n[S4] develop 开发者工具（默认本机 + storage 覆盖）')
  {
    const s = loadSandbox('', 'develop', {})
    check('默认取本机地址', s.requestMod.getBaseUrl() === 'http://127.0.0.1:8000/api/v1')
    s.requestMod.request('/topics/hot')
    check('请求 URL 拼接正确', s.sentUrls[0] === 'http://127.0.0.1:8000/api/v1/topics/hot', s.sentUrls[0])

    const s2 = loadSandbox('', 'develop', { xjt_api_base_url: '  http://192.168.1.5:8000/api/v1/  ' })
    check('storage 覆盖生效且规范化（去空白 / 去末尾斜杠）',
      s2.requestMod.getBaseUrl() === 'http://192.168.1.5:8000/api/v1', s2.requestMod.getBaseUrl())
  }

  // -------------------------------- S5 反向对照：证明本用例真能发现原缺陷
  console.log('\n[S5] 反向对照（negative control）—— 复刻修复前的写法')
  {
    const { requestMod } = loadSandbox('', 'release', {})
    // 修复前就是这么写的：同步取 URL，再 return Promise
    function oldStyleRequest(p) {
      const url = requestMod.getBaseUrl() + p // 无 try/catch
      return new Promise(() => {})
    }
    let syncThrew = false
    let caughtByCatch = false
    try {
      oldStyleRequest('/x').catch(() => { caughtByCatch = true })
    } catch (e) {
      syncThrew = true
    }
    check('旧写法确实同步逃逸（异常绕过 Promise）', syncThrew === true)
    check('旧写法下 .catch() 确实接不到', caughtByCatch === false)
    check('=> 本用例具备发现该缺陷的能力（非空跑）', syncThrew === true && caughtByCatch === false)
  }

  // ------------------------------------------------ 汇总
  console.log('\n' + '='.repeat(78))
  if (failures.length === 0) {
    console.log(`[PASS] ${passed}/${passed} 项全部通过`)
  } else {
    console.log(`[FAIL] ${passed}/${passed + failures.length} 项通过，失败 ${failures.length} 项：`)
    failures.forEach((f) => console.log('   - ' + f))
  }
  console.log('='.repeat(78))
  return failures.length === 0 ? 0 : 1
}

main()
  .then((code) => {
    sandboxes.forEach((d) => {
      try {
        fs.rmSync(d, { recursive: true, force: true })
      } catch (e) {
        /* 清理失败不影响结论 */
      }
    })
    process.exit(code)
  })
  .catch((e) => {
    console.error('[FAIL] 脚本异常：', e)
    process.exit(1)
  })
