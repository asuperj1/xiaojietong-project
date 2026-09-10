// 统一 HTTP 请求封装（F1B：普通请求 + SSE 流式请求）
// 依据：miniprogram/前端页面规格.md §0、docs/api.md §0、backend/app/routers/chat.py
// - base URL: http://127.0.0.1:8000/api/v1
// - 统一响应 {code, message, data}；code=0 成功
// - 登录后请求头携带 Authorization: Bearer <token>
// - SSE：POST /chat/send，事件 sources / chunk / done（error 为文档预留）

const BASE_URL = 'http://127.0.0.1:8000/api/v1'

// token 在本地存储中的键名（与 app.js 及规格书 §0 登录页保持一致）
const TOKEN_KEY = 'token'

// 登录失效统一处理：清理无效登录态并跳转登录页（request 与 sseRequest 共用，保证语义一致）
function handleAuthFailure(message) {
  wx.removeStorageSync(TOKEN_KEY)
  wx.removeStorageSync('refresh_token')
  wx.removeStorageSync('user')
  wx.showToast({ title: message || '登录已过期，请重新登录', icon: 'none' })
  wx.reLaunch({ url: '/pages/auth/login' })
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
  // 路径兼容：确保以 "/" 开头
  const url = BASE_URL + (path.startsWith('/') ? path : '/' + path)

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
        const body = res.data

        // 兜底：非标准响应体（如网关错误、非 JSON）
        if (!body || typeof body !== 'object' || typeof body.code === 'undefined') {
          wx.showToast({ title: '服务异常，请稍后重试', icon: 'none' })
          reject(new Error('响应格式错误'))
          return
        }

        const { code, message, data: payload } = body

        // 成功：resolve 业务数据
        if (code === 0) {
          resolve(payload)
          return
        }

        const err = new Error(message || '请求失败')
        err.code = code
        err.message = message || '请求失败'

        // 登录失效（未登录 / token 过期）：复用统一登录失效处理
        if (code === 2001 || code === 2002) {
          handleAuthFailure(message)
          reject(err)
          return
        }

        // 其他业务错误：统一 toast 提示后 reject
        wx.showToast({ title: message || '操作失败', icon: 'none' })
        reject(err)
      },
      fail() {
        // 网络层错误（断网、超时、域名不合法等）
        wx.showToast({ title: '网络异常，请稍后重试', icon: 'none' })
        reject(new Error('网络异常，请稍后重试'))
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
 * @param {Function} [handlers.onDone] done 事件（{ conversation_id, message_id }）
 * @param {Function} [handlers.onError] 出错回调（网络/解析/业务错误/登录失效）
 * @returns {RequestTask} 调用方可 task.abort() 主动终止
 */
function sseRequest(path, data = {}, { onSources, onChunk, onDone, onError } = {}) {
  const url = BASE_URL + (path.startsWith('/') ? path : '/' + path)
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
      case 'done':
        finished = true
        if (typeof onDone === 'function') onDone(payload || {})
        break
      case 'error': {
        // 文档定义的 error 事件：后端当前不一定发送，收到则转 onError
        const e = new Error((payload && payload.message) || '服务返回错误')
        if (payload && payload.code) e.code = payload.code
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
    if (code === 2001 || code === 2002) {
      // handleAuthFailure 内部已含 toast，避免重复提示
      handleAuthFailure(message)
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

module.exports = { request, sseRequest, BASE_URL }