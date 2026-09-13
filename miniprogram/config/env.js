// API Base URL 统一解析（FRONT-01）
// 职责：为 services/request.js 的普通请求与 SSE 提供同一份 Base URL。
//
// 取值优先级：
//   1) develop（开发者工具 / 预览 / 真机调试）：允许用微信 storage 覆盖 xjt_api_base_url
//      → 便于真机联调局域网后端
//   2) trial（体验版）：不允许 storage 覆盖，固定读 TRIAL_BASE_URL（须为合法 https）
//   3) release（正式版）：不允许 storage 覆盖，固定读 RELEASE_BASE_URL（须为合法 https）；
//      缺失或非法一律抛错，不回退开发地址
//
// 约定：
// - storage key 统一为 xjt_api_base_url（仅 develop 生效）
// - 仓库内不得提交个人局域网 IP，真机地址只在开发者工具里临时写入 storage
// - 微信小程序要求 https，trial / release 地址须为已备案域名

// 开发（develop）环境默认地址：本机后端（开发者工具「不校验合法域名」时可用）
const DEFAULT_BASE_URL = 'http://127.0.0.1:8000/api/v1'

// trial（体验版）API 地址配置位
// ⚠️ 需要体验版真机验证时填入合法 https 地址（如内网穿透临时域名/测试服）；留空 = 未配置 → 抛错
const TRIAL_BASE_URL = ''

// release（正式环境）API 地址配置位
// ⚠️ 上线前必须在此填入正式 https 域名，例如：'https://api.你的域名.edu.cn/api/v1'
// 留空或非法 → release 下 getBaseUrl() 抛错（fail fast），不会回退到本机地址
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

// 校验是否为「有效的 https 地址」（不依赖第三方库）
// 通过：'https://api.x.edu.cn/api/v1'、'https://api.x.edu.cn'
// 拒绝：''、'https://'、'http://'、'api.x.edu.cn/api/v1'、'http://api.x.edu.cn/api/v1'、纯 IP
function isValidHttpsUrl(url) {
  const s = normalizeBaseUrl(url)
  if (!s) return false
  const m = /^https:\/\/([^/?#\s]+)(\/[^\s]*)?$/.exec(s)
  if (!m) return false
  const host = m[1]
  // 主机名须是域名（含 '.'）；微信不接受 IP 地址
  if (host.indexOf('.') === -1) return false
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(host.split(':')[0])) return false
  return true
}

// 仅开发者工具 / 预览 / 真机调试（develop）允许 storage 覆盖；
// trial（体验版）与 release（正式版）固定走配置项，避免本地随意改线上地址
function allowStorageOverride(envVersion) {
  return envVersion === 'develop'
}

/**
 * 解析当前应使用的 API Base URL（末尾无多余 '/'）
 * @returns {string} 例如 "http://127.0.0.1:8000/api/v1"
 * @throws {Error} trial / release 环境未配置或配置非法（非 https）时抛出（fail fast）
 */
function getBaseUrl() {
  const envVersion = getEnvVersion()

  // develop：允许 storage 覆盖（真机联调用）；读取异常降级为默认地址，不影响开发
  if (allowStorageOverride(envVersion)) {
    let override = ''
    try {
      override = normalizeBaseUrl(wx.getStorageSync(BASE_URL_STORAGE_KEY))
    } catch (e) {
      // storage 读取异常（如被禁用）不得让请求同步炸掉：降级 + 告警
      console.warn('[env] 读取 storage 覆盖地址失败，已回退默认开发地址：', e)
      override = ''
    }
    if (override) return override
    return DEFAULT_BASE_URL
  }

  // trial（体验版）：固定配置项，必须是合法 https
  if (envVersion === 'trial') {
    if (isValidHttpsUrl(TRIAL_BASE_URL)) return normalizeBaseUrl(TRIAL_BASE_URL)
    throw new Error(
      'trial 环境 API 地址必须是有效的 https 地址：请在 miniprogram/config/env.js 的 TRIAL_BASE_URL 中填写'
    )
  }

  // release（正式版）：固定配置项，必须是合法 https；缺失/非法一律抛错
  if (isValidHttpsUrl(RELEASE_BASE_URL)) return normalizeBaseUrl(RELEASE_BASE_URL)
  throw new Error('release 环境 API 地址必须是有效的 https 地址')
}

module.exports = {
  getBaseUrl,
  getEnvVersion,
  isValidHttpsUrl,
  BASE_URL_STORAGE_KEY,
  DEFAULT_BASE_URL,
  TRIAL_BASE_URL,
  RELEASE_BASE_URL,
}
