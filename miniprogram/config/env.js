// API Base URL 统一解析（FRONT-01）
// 职责：为 services/request.js 的普通请求与 SSE 提供同一份 Base URL。
//
// 取值优先级：
//   1) develop（开发者工具）/ trial（体验版）：允许用微信 storage 覆盖 → 便于真机联调局域网后端
//   2) release（正式版）：固定使用下方 RELEASE_BASE_URL 配置项（不允许 storage 覆盖；
//      未配置则直接抛出配置错误，不回退开发地址）
//
// 约定：
// - storage key 统一为 xjt_api_base_url
// - 仓库内不得提交个人局域网 IP，真机地址只在开发者工具里临时写入 storage
// - 微信小程序要求 https，正式地址须为已备案域名

// 开发/体验环境默认地址：本机后端（开发者工具「不校验合法域名」时可用）
const DEFAULT_BASE_URL = 'http://127.0.0.1:8000/api/v1'

// release（正式环境）API 地址配置位
// ⚠️ 上线前必须在此填入正式 https 域名，例如：'https://api.你的域名.edu.cn/api/v1'
// 留空 = 未配置：release 下 getBaseUrl() 会直接抛错（fail fast），不会回退到本机地址
const RELEASE_BASE_URL = ''

// 允许通过 storage 覆盖 Base URL 的 storage key
const BASE_URL_STORAGE_KEY = 'xjt_api_base_url'

// 读取当前运行环境：'develop'（开发者工具）/ 'trial'（体验版）/ 'release'（正式版）
function getEnvVersion() {
  try {
    const info = typeof wx.getAccountInfoSync === 'function' ? wx.getAccountInfoSync() : null
    const env = info && info.miniProgram && info.miniProgram.envVersion
    return env || 'develop'
  } catch (e) {
    return 'develop'
  }
}

// 最小规范化：去首尾空白、去掉末尾多余的 '/'（保留 "https://" 等前缀原样）
function normalizeBaseUrl(url) {
  const s = String(url || '').trim()
  return s ? s.replace(/\/+$/, '') : ''
}

// 仅开发 / 体验环境允许 storage 覆盖；正式版固定走配置项
function allowStorageOverride(envVersion) {
  return envVersion === 'develop' || envVersion === 'trial'
}

/**
 * 解析当前应使用的 API Base URL（末尾无多余 '/'）
 * @returns {string} 例如 "http://127.0.0.1:8000/api/v1"
 * @throws {Error} release 环境未配置 RELEASE_BASE_URL 时抛出（fail fast，不回退开发地址）
 */
function getBaseUrl() {
  const envVersion = getEnvVersion()

  if (allowStorageOverride(envVersion)) {
    const override = normalizeBaseUrl(wx.getStorageSync(BASE_URL_STORAGE_KEY))
    if (override) return override
    return DEFAULT_BASE_URL
  }

  // release：只用配置项，不读 storage、不伪造域名、不回退开发地址
  if (RELEASE_BASE_URL) {
    const url = normalizeBaseUrl(RELEASE_BASE_URL)
    // 微信正式版强制 https。不提前拦，真机上只会得到很难懂的
    // `url not in domain list` 类报错；顺带拦住 normalizeBaseUrl('https://')
    // 会产出 "https:" 这种非法值的情况。
    if (!/^https:\/\/[^/]/i.test(url)) {
      throw new Error(
        '[env] RELEASE_BASE_URL 必须是 https:// 开头的已备案域名，当前值：' + url
      )
    }
    return url
  }

  // fail fast：正式包缺少生产地址属发布配置错误，直接抛错，
  // 避免静默连上 127.0.0.1（真机必然失败）或空地址造成更难排查的问题
  throw new Error(
    '[env] release 环境未配置正式 API 地址：请在 miniprogram/config/env.js 的 RELEASE_BASE_URL 中填写'
  )
}

module.exports = {
  getBaseUrl,
  getEnvVersion,
  BASE_URL_STORAGE_KEY,
  DEFAULT_BASE_URL,
  RELEASE_BASE_URL,
}
