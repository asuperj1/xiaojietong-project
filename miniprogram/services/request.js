// 统一 HTTP 请求封装（F1B：普通请求 + SSE 流式请求）
// 依据：miniprogram/前端页面规格.md §0、docs/api.md §0、backend/app/routers/chat.py
// - base URL: 由 config/env.js 的 getBaseUrl() 统一解析（开发默认本机后端，见该文件说明）
//   ⚠️ 解析可能失败（release 未配置 `RELEASE_BASE_URL` 会 throw）。两个入口都已捕获，
//   并转为各自的**异步**错误通道（request → reject，sseRequest → onError），
//   严禁让它在 Promise 创建前同步逃逸（详见 request() 内注释）。
// - 统一响应 {code, message, data}；code=0 成功
// - 登录后请求头携带 Authorization: Bearer <token>
// - SSE：POST /chat/send，事件 sources / chunk / done（error 为文档预留）

const { getBaseUrl } = require('../config/env')

// token 在本地存储中的键名（与 app.js 及规格书 §0 登录页保持一致）
const TOKEN_KEY = 'token'

// 登录态失效的错误码：2001 未登录 / 2002 token 失效 / 2003 无权限
// 后端 2003（HTTP 403）当前由 core/deps.py 的 get_current_user 用于「用户不存在」「账号已禁用」，
// 小程序端只调用用户接口，因此 2003 同属登录态失效，需与 2001/2002 一并处理；
// （get_current_admin 的「需要管理员权限」小程序不会触发）
const AUTH_FAILURE_CODES = [2001, 2002, 2003]

// ---------------------------------------------------------------- 环境地址 ----
// 环境解析失败（典型：release 未配置 `RELEASE_BASE_URL`）时的统一提示。
// 提示语对用户可读；真实原因（含 env.js 抛出的详细文案）打在 console 便于定位。
const ENV_ERROR_TOAST = '接口地址未配置，请联系管理员'

// 统一的环境错误对象（带 code，便于调用方区分「配置错误」与业务错误）
function makeEnvError() {
  const err = new Error(ENV_ERROR_TOAST)
  err.code = 'ENV_BASE_URL_UNAVAILABLE'
  return err
}

// 按错误码生成提示文案：2003 场景给出「账号已被禁用」的明确指引
function authFailureMessage(code, message) {
  if (code === 2003) {
    const text = String(message || '')
    // 后端默认文案为「无权限」，对用户无意义；命中禁用时统一给可操作提示
    if (!text || text === '无权限' || text.indexOf('禁用') !== -1) {
      return '账号已被禁用，请联系管理员'
    }
    return text
  }
  return message || '登录已过期，请重新登录'
}

// 登录失效统一处理：清理无效登录态并跳转登录页（request 与 sseRequest 共用，保证语义一致）
function handleAuthFailure(code, message) {
  wx.removeStorageSync(TOKEN_KEY)
  wx.removeStorageSync('refresh_token')
  wx.removeStorageSync('user')
  // 同步清空 globalData，避免内存态与存储态不一致
  // （此处位于回调中，App 已初始化；仍做存在性判断以防异常环境）
  const app = typeof getApp === 'function' ? getApp() : null
  if (app && app.globalData) {
    app.globalData.token = ''
    app.globalData.userInfo = null
  }
  wx.showToast({ title: authFailureMessage(code, message), icon: 'none' })
  wx.reLaunch({ url: '/pages/auth/login' })
}

// ------------------------------------------------------- 响应收口（共用） ----

/** `request()` 的错误文案 */
const REQUEST_LABELS = {
  badBodyToast: '服务异常，请稍后重试',
  badBodyError: '响应格式错误',
  failMessage: '请求失败',
  failToast: '操作失败',
}

/** `uploadImage()` 的错误文案（与 request 分开：上传失败要让用户知道是「上传」这一步） */
const UPLOAD_LABELS = {
  badBodyToast: '上传失败，请稍后重试',
  badBodyError: '上传响应格式错误',
  failMessage: '上传失败',
  failToast: '上传失败',
}

/**
 * 统一响应体 `{code, message, data}` 的判定收口 —— `request()` 与 `uploadImage()` 共用。
 *
 * 为什么要收口：这两条链路（`wx.request` / `wx.uploadFile`）原本各抄一遍
 * 「非标准响应体 → code=0 → 登录态失效 → 业务错误」四段判断，
 * 漏改一处就会让某条链路悄悄偏离统一错误语义（上传尤其容易被漏）。
 * 文案随链路不同，故由调用方显式传入 `labels`，不在这里硬编码。
 *
 * @param {any} body 已解析的响应体（uploadFile 侧需先 JSON.parse）
 * @param {(data:any)=>void} resolve
 * @param {(err:Error)=>void} reject
 * @param {{badBodyToast:string, badBodyError:string, failMessage:string, failToast:string}} labels
 */
function settleBody(body, resolve, reject, labels) {
  // 兜底：非标准响应体（如网关错误、非 JSON）
  if (!body || typeof body !== 'object' || typeof body.code === 'undefined') {
    wx.showToast({ title: labels.badBodyToast, icon: 'none' })
    reject(new Error(labels.badBodyError))
    return
  }

  const { code, message, data: payload } = body

  // 成功：resolve 业务数据
  if (code === 0) {
    resolve(payload)
    return
  }

  const err = new Error(message || labels.failMessage)
  err.code = code

  // 登录态失效（未登录 / token 过期 / 账号禁用）：复用统一处理（内部已含 toast）
  if (AUTH_FAILURE_CODES.indexOf(code) !== -1) {
    handleAuthFailure(code, message)
    reject(err)
    return
  }

  // 其他业务错误：统一 toast 提示后 reject
  wx.showToast({ title: message || labels.failToast, icon: 'none' })
  reject(err)
}

/** 网络层错误（断网、超时、域名不合法等）的统一反馈 —— `request()` 与 `uploadImage()` 共用 */
function failNetwork(reject) {
  wx.showToast({ title: '网络异常，请稍后重试', icon: 'none' })
  reject(new Error('网络异常，请稍后重试'))
}

/**
 * 发起普通 HTTP 请求（Promise 化）
 * @param {string} path 接口路径，如 "/auth/wechat-login"
 * @param {Object} [options]
 * @param {string} [options.method='GET'] 请求方法
 * @param {Object} [options.data={}] 请求参数
 * @returns {Promise<any>} 成功 resolve 后端 data；失败 reject 携带 code 属性的 Error
 */
function request(path, { method = 'GET', data = {} } = {}) {
  // 路径兼容：确保以 "/" 开头（每次请求解析，真机调试改 storage 后可立即生效）
  //
  // ⚠️ 必须 try/catch：`getBaseUrl()` 在 release 未配置正式地址时会 throw，
  // 而此处位于 `return new Promise(...)` **之前** —— 异常会在 Promise 创建前
  // 同步逃逸，调用方写的 `.catch()` 接不到（实测：仓内 47 处 .catch 全部无效），
  // 表现为「按钮没反应 + 无任何提示」。故统一转为 rejected Promise，
  // 把错误送回调用方本就应该走的错误通道。
  let url
  try {
    url = getBaseUrl() + (path.startsWith('/') ? path : '/' + path)
  } catch (e) {
    console.error('[env] API 地址解析失败：', e)
    wx.showToast({ title: ENV_ERROR_TOAST, icon: 'none' })
    return Promise.reject(makeEnvError())
  }

  // 自动读取本地 token（无 token 则不注入 Authorization）
  const token = wx.getStorageSync(TOKEN_KEY) || ''

  return new Promise((resolve, reject) => {
    wx.request({
      url,
      method,
      data,
      header: token
        ? { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token }
        : { 'Content-Type': 'application/json' },
      success(res) {
        settleBody(res.data, resolve, reject, REQUEST_LABELS)
      },
      fail() {
        failNetwork(reject)
      },
    })
  })
}

// ==================== 文件上传（multipart） ====================

/** 图片上传接口路径（契约：docs/api.md §12 —— `POST /upload/image`） */
const UPLOAD_IMAGE_PATH = '/upload/image'

/**
 * 上传图片（multipart/form-data）—— F17 资料设置页上传头像使用。
 *
 * 与 `request()` 共用同一套语义，避免各页自己去拼 URL / token：
 * - base URL 走 `config/env.js` 的 `getBaseUrl()`，解析失败同样转成**异步**错误通道
 *   （不得同步逃逸，理由与 `request()` 完全一致）；
 * - 自动携带 `Authorization: Bearer <token>`；
 * - `code === 0` → resolve 后端 data（`{ url, size }`）；否则与 `request()` 一样，
 *   登录态失效走 `handleAuthFailure`，其余业务错误 toast 后 reject（`err.code` 可用）。
 *
 * ⚠️ 必须用 `wx.uploadFile`：multipart 的 boundary 由框架生成，**不能**手写
 * `Content-Type: application/json`，否则后端收不到文件（`upload.py` 只接受 multipart）。
 *
 * @param {string} filePath 本地临时文件路径（`wx.chooseMedia` 的 tempFilePath）
 * @param {Object} [options]
 * @param {string} [options.name='file'] 表单字段名（后端 `upload_image(file: UploadFile)`）
 * @returns {Promise<any>} 成功 resolve 后端 data；失败 reject 携带 code 属性的 Error
 */
function uploadImage(filePath, { name = 'file' } = {}) {
  let url
  try {
    url = getBaseUrl() + UPLOAD_IMAGE_PATH
  } catch (e) {
    console.error('[env] API 地址解析失败：', e)
    wx.showToast({ title: ENV_ERROR_TOAST, icon: 'none' })
    return Promise.reject(makeEnvError())
  }

  const token = wx.getStorageSync(TOKEN_KEY) || ''

  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url,
      filePath,
      name,
      // 刻意不设置 Content-Type：由框架补 multipart/form-data 与 boundary
      header: token ? { Authorization: 'Bearer ' + token } : {},
      success(res) {
        // 与 wx.request 不同：uploadFile 的 res.data 是**字符串**，需要自行解析
        let body = res.data
        if (typeof body === 'string') {
          try {
            body = JSON.parse(body)
          } catch (e) {
            body = null
          }
        }
        settleBody(body, resolve, reject, UPLOAD_LABELS)
      },
      fail() {
        failNetwork(reject)
      },
    })
  })
}

// ==================== SSE 流式请求 ====================

// UTF-8 部分解码：返回 { text, remaining }，remaining 为末尾不完整多字节序列的字节
function decodeUtf8Partial(bytes) {
  let i = 0
  let out = ''
  while (i < bytes.length) {
    const b = bytes[i]
    let len
    let codePoint
    if (b < 0x80) { len = 1; codePoint = b }
    else if ((b & 0xe0) === 0xc0) { len = 2; codePoint = b & 0x1f }
    else if ((b & 0xf0) === 0xe0) { len = 3; codePoint = b & 0x0f }
    else if ((b & 0xf8) === 0xf0) { len = 4; codePoint = b & 0x07 }
    else {
      // 续字节出现在起始位置：损坏数据，用替换符跳过
      out += '\ufffd'
      i += 1
      continue
    }
    // 不完整多字节序列：停止解码，剩余字节留待下一个网络 chunk
    if (i + len > bytes.length) break
    for (let j = 1; j < len; j++) {
      const cb = bytes[i + j]
      if ((cb & 0xc0) !== 0x80) {
        codePoint = -1
        break
      }
      codePoint = (codePoint << 6) | (cb & 0x3f)
    }
    if (codePoint === -1) {
      out += '\ufffd'
      i += 1
      continue
    }
    out += String.fromCodePoint(codePoint)
    i += len
  }
  return { text: out, remaining: bytes.slice(i) }
}

/**
 * SSE 流式请求（当前用于 POST /chat/send）
 * @param {string} path 接口路径，如 "/chat/send"
 * @param {Object} data 请求体
 * @param {Object} handlers 事件回调
 * @param {Function} [handlers.onSources] sources 事件（引用来源数组）
 * @param {Function} [handlers.onChunk] chunk 事件（每次一段 delta 文本）
 * @param {Function} [handlers.onRefused] refused 事件（C20 拒答，{ delta, reason }；未提供时降级走 onChunk）
 * @param {Function} [handlers.onCitations] citations 事件（C20 引用清洗，{ fabricated, final }）
 * @param {Function} [handlers.onDone] done 事件（{ conversation_id, message_id }）
 * @param {Function} [handlers.onError] 出错回调（网络/解析/业务错误/登录失效）
 * @returns {RequestTask} 调用方可 task.abort() 主动终止
 */
function sseRequest(path, data = {}, { onSources, onChunk, onRefused, onCitations, onDone, onError } = {}) {
  // 与 request() 使用同一套环境解析规则（getBaseUrl）
  //
  // ⚠️ 同 request()：环境解析失败必须走 onError。若直接同步抛出，
  // ① onError 永远不会被调用；② 调用方拿不到 RequestTask。
  // 而 chat.js 是「先 setData({sending:true}) 再调 sseRequest」的写法，
  // onError 不回 => sending 永远为 true => 对话页永久 loading、AI 气泡空白。
  let url
  try {
    url = getBaseUrl() + (path.startsWith('/') ? path : '/' + path)
  } catch (e) {
    console.error('[env] API 地址解析失败：', e)
    wx.showToast({ title: ENV_ERROR_TOAST, icon: 'none' })
    if (typeof onError === 'function') onError(makeEnvError())
    // 保持 RequestTask 形状，避免调用方 task.abort() 报错
    return { abort() {} }
  }
  const token = wx.getStorageSync(TOKEN_KEY) || ''

  let textBuffer = ''                 // 已解码、待按事件边界切分的文本
  let byteBuffer = new Uint8Array(0)  // 未解码的字节缓冲（仅保留末尾不完整序列）
  let finished = false                // 流是否已结束（done / error / 失败）

  // 只触发一次 onError
  const safeError = (err) => {
    if (finished) return
    finished = true
    if (typeof onError === 'function') onError(err)
  }

  // 解析并分发一个完整 SSE 事件
  function dispatchEvent(raw) {
    let eventName = ''
    const dataLines = []
    raw.split('\n').forEach((line) => {
      if (line.startsWith('event:')) eventName = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
    })
    if (!eventName) return

    const dataStr = dataLines.join('\n')
    let payload = null
    if (dataStr) {
      try {
        payload = JSON.parse(dataStr)
      } catch (e) {
        safeError(new Error('SSE 数据解析失败'))
        return
      }
    }

    switch (eventName) {
      case 'sources':
        if (typeof onSources === 'function') onSources(payload || [])
        break
      case 'chunk':
        if (payload && typeof payload.delta === 'string' && typeof onChunk === 'function') {
          onChunk(payload.delta)
        }
        break
      // `C20` 拒答：后端判定检索结果不足以回答，**没有调模型**，直接给结论文案。
      // 提供了 onRefused 就单独走它（调用方可标记气泡为“无依据”）；否则当普通正文。
      case 'refused':
        if (payload && typeof payload.delta === 'string') {
          if (typeof onRefused === 'function') onRefused(payload)
          else if (typeof onChunk === 'function') onChunk(payload.delta)
        }
        break
      // `C20` 引用清洗：正文已经流式展示过了，这里下发修正后的全文（final）
      // 与被剔除的伪造引用（fabricated），由调用方覆盖气泡内容。
      case 'citations':
        if (payload && typeof onCitations === 'function') onCitations(payload)
        break
      case 'done':
        finished = true
        if (typeof onDone === 'function') onDone(payload || {})
        break
      case 'error': {
        // 文档定义的 error 事件：后端当前不一定发送，收到则转 onError
        const e = new Error((payload && payload.message) || '服务返回错误')
        if (payload && payload.code) e.code = payload.code
        // 事件内携带登录态失效码时同样统一处理，保证与 request() 语义一致
        if (payload && AUTH_FAILURE_CODES.indexOf(payload.code) !== -1) {
          handleAuthFailure(payload.code, payload.message)
        }
        safeError(e)
        break
      }
      default:
        break
    }
  }

  // 从文本缓冲中按空行切出完整事件并分发
  function drain() {
    textBuffer = textBuffer.replace(/\r\n/g, '\n')
    let sep = textBuffer.indexOf('\n\n')
    while (sep !== -1) {
      const raw = textBuffer.slice(0, sep)
      textBuffer = textBuffer.slice(sep + 2)
      dispatchEvent(raw)
      // 解析出错或流结束（finished=true）后立即停止，丢弃后续未处理内容
      if (finished) {
        textBuffer = ''
        return
      }
      sep = textBuffer.indexOf('\n\n')
    }
  }

  // 追加网络字节块：合并字节缓冲 → 部分 UTF-8 解码 → 追加文本 → 切分事件
  function pushBytes(ab) {
    const incoming = new Uint8Array(ab)
    const merged = new Uint8Array(byteBuffer.length + incoming.length)
    merged.set(byteBuffer, 0)
    merged.set(incoming, byteBuffer.length)
    const { text, remaining } = decodeUtf8Partial(merged)
    byteBuffer = remaining
    if (text) {
      textBuffer += text
      drain()
    }
  }

  // 非 2xx：后端返回普通 JSON 错误（复用普通 request 的错误语义）
  function handleNonStreamError(body) {
    if (!body || typeof body !== 'object' || typeof body.code === 'undefined') {
      wx.showToast({ title: '服务异常，请稍后重试', icon: 'none' })
      safeError(new Error('服务异常，请稍后重试'))
      return
    }
    const { code, message } = body
    const err = new Error(message || '请求失败')
    err.code = code
    err.message = message || '请求失败'
    if (AUTH_FAILURE_CODES.indexOf(code) !== -1) {
      // handleAuthFailure 内部已含 toast，避免重复提示
      handleAuthFailure(code, message)
      safeError(err)
      return
    }
    // 普通业务错误：与 request() 一致，先 toast 再 onError
    wx.showToast({ title: message || '操作失败', icon: 'none' })
    safeError(err)
  }

  const task = wx.request({
    url,
    method: 'POST',
    data,
    enableChunked: true,
    header: token
      ? { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token }
      : { 'Content-Type': 'application/json' },
    success(res) {
      if (res.statusCode >= 200 && res.statusCode < 300) {
        // 2xx：流正常结束；若未收到 done 事件，视为异常中断
        if (!finished) safeError(new Error('连接中断'))
        return
      }
      handleNonStreamError(res.data)
    },
    fail(err) {
      // 主动 abort 属正常终止：静默结束，不当作网络错误、不触发 onError
      if (err && err.errMsg && err.errMsg.indexOf('abort') !== -1) {
        finished = true
        return
      }
      safeError(new Error('网络异常，请稍后重试'))
    },
  })

  // 接收流式分块（ArrayBuffer）
  if (task && typeof task.onChunkReceived === 'function') {
    task.onChunkReceived((res) => {
      if (finished) return
      const data = res.data
      if (data instanceof ArrayBuffer) {
        pushBytes(data)
      } else if (typeof data === 'string') {
        // 防御：个别实现可能返回字符串
        textBuffer += data
        drain()
      }
    })
  }

  // 返回 RequestTask，调用方可 task.abort() 主动终止
  return task
}

// 后端地址改为动态解析：导出 getBaseUrl 供调试/自检使用
// （原 BASE_URL 为静态字符串，仓内已无调用方，故不再导出，避免误用静态值）
module.exports = { request, sseRequest, uploadImage, getBaseUrl }