// 玻璃（backdrop-filter）能力探测（F10）
// 依据：docs/二阶段整改方案-前端UI重构与后端支撑.md §1.2
// 职责：只做能力探测，返回布尔值；不操作页面、不写 DOM、不改 globalData。
// 结果由 app.js 在 onLaunch 存入 globalData，页面后续按需取用
// （配合 styles/glass.wxss 的 .is-glass-fallback / .is-glass-reduced）。

/**
 * 探测当前设备是否适合启用毛玻璃
 * 规则：
 * - iOS / iPadOS：默认允许（WKWebView 支持稳定）
 * - 开发者工具宿主（Windows / macOS）：允许，便于开发预览
 * - Android：系统版本 ≥ 9 视为可用；低版本 WebView 对 backdrop-filter 支持不稳定，保守降级
 * - 无法识别型号（含 HarmonyOS 等）+ 任何异常：返回 false（降级到实心底，保证可读）
 * @returns {boolean}
 */
function detectGlass() {
  try {
    const info =
      typeof wx.getAppBaseInfo === 'function' ? wx.getAppBaseInfo() : wx.getSystemInfoSync()
    const system = (info && info.system) || ''

    if (/iOS|iPadOS/i.test(system)) return true
    if (/Windows|macOS|Macintosh/i.test(system)) return true

    const matched = /Android\s+([0-9.]+)/i.exec(system)
    if (!matched) return false
    return parseFloat(matched[1]) >= 9
  } catch (e) {
    return false
  }
}

module.exports = { detectGlass }
