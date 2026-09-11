// AI 对话页（Tab）—— F4：SSE 流式多轮对话
const { sseRequest } = require('../../services/request')

const app = getApp()

Page({
  data: {
    messages: [],        // 消息列表：{ localId, role, content, sources, messageId }
    inputValue: '',      // 输入框内容
    sending: false,      // 是否正在生成回答
    conversationId: null, // 当前会话 id（首条消息前为 null）
    scrollIntoView: '',  // 滚动锚点 id
  },

  // 非渲染态字段
  _task: null,    // 当前 SSE RequestTask，页面卸载时 abort
  _localSeq: 0,   // localId 自增序列

  onShow() {
    // 首页搜索衔接：读取 pendingSearch 后立即清空，避免重复发送
    const keyword = app && app.globalData ? app.globalData.pendingSearch : ''
    if (keyword) {
      app.globalData.pendingSearch = ''
      if (!this.data.sending) {
        this.sendMessage(keyword)
      }
    }
  },

  onUnload() {
    // 页面卸载时中止仍在进行的流式请求，避免悬空
    if (this._task && typeof this._task.abort === 'function') {
      this._task.abort()
    }
    this._task = null
  },

  onInput(e) {
    this.setData({ inputValue: e.detail.value })
  },

  onSend() {
    const content = (this.data.inputValue || '').trim()
    if (!content) return
    this.setData({ inputValue: '' })
    this.sendMessage(content)
  },

  // 发送消息：先插入用户气泡 + 空 AI 气泡，再走 SSE
  sendMessage(content) {
    if (this.data.sending) return

    const userMsg = this.makeMessage('user', content)
    const aiMsg = this.makeMessage('assistant', '')
    const aiLocalId = aiMsg.localId

    this.setData({ sending: true })
    this.appendMessage(userMsg)
    this.appendMessage(aiMsg)
    this.setData({ scrollIntoView: 'msg-' + aiLocalId })

    this._task = sseRequest(
      '/chat/send',
      {
        conversation_id: this.data.conversationId,
        content,
        quick: '',
      },
      {
        // RAG sources 数组：归一化为可安全渲染的字段
        onSources: (sources) => {
          const normalized = (sources || []).map((s, i) => ({
            id: i,
            title: (s && s.title) || '参考资料',
            category: (s && s.category) || '',
            content: (s && s.content) || '',
          }))
          this.updateMessage(aiLocalId, { sources: normalized })
        },
        // 流式追加：不覆盖已有内容
        onChunk: (delta) => {
          const msg = this.findMessage(aiLocalId)
          const next = (msg ? msg.content : '') + (delta || '')
          this.updateMessage(aiLocalId, { content: next })
          this.setData({ scrollIntoView: 'msg-' + aiLocalId })
        },
        // 完成：保存会话/消息 id
        onDone: (data) => {
          if (data && data.conversation_id != null) {
            this.setData({ conversationId: data.conversation_id })
          }
          if (data && data.message_id != null) {
            this.updateMessage(aiLocalId, { messageId: data.message_id })
          }
          const msg = this.findMessage(aiLocalId)
          if (msg && !msg.content) {
            this.updateMessage(aiLocalId, { content: '（本次没有返回内容）' })
          }
          this.setData({ sending: false, scrollIntoView: 'msg-' + aiLocalId })
          this._task = null
        },
        // 失败：通用 toast / 鉴权已由 request.js 处理，这里只兜底空正文
        onError: () => {
          const msg = this.findMessage(aiLocalId)
          if (msg && !msg.content) {
            this.updateMessage(aiLocalId, { content: '抱歉，回答生成失败，请稍后重试。' })
          }
          this.setData({ sending: false, scrollIntoView: 'msg-' + aiLocalId })
          this._task = null
        },
      }
    )
  },

  makeMessage(role, content) {
    this._localSeq += 1
    return {
      localId: this._localSeq,
      role,
      content,
      sources: [],
      messageId: null,
    }
  },

  appendMessage(msg) {
    this.setData({ messages: this.data.messages.concat([msg]) })
  },

  findMessage(localId) {
    return this.data.messages.find((m) => m.localId === localId)
  },

  updateMessage(localId, patch) {
    const idx = this.data.messages.findIndex((m) => m.localId === localId)
    if (idx === -1) return
    this.setData({
      ['messages[' + idx + ']']: Object.assign({}, this.data.messages[idx], patch),
    })
  },
})
