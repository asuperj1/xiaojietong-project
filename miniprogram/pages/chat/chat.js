// AI 对话页（Tab）—— F4：SSE 流式多轮对话 + 停止 / 新建会话 / 历史
const { sseRequest } = require('../../services/request')

Page({
  data: {
    messages: [],        // 消息列表：{ localId, role, content, sources, messageId, stopped }
    inputValue: '',      // 输入框内容
    sending: false,      // 是否正在生成回答
    conversationId: null, // 当前会话 id（首条消息前为 null）
    scrollIntoView: '',  // 滚动锚点 id
  },

  // 非渲染态字段
  _task: null,        // 当前 SSE RequestTask，停止/卸载时 abort
  _localSeq: 0,       // localId 自增序列
  _activeAiId: null,  // 正在流式填充的 AI 气泡 localId（用于丢弃停止后的迟到回调）
  _app: null,         // getApp() 句柄，在 onLoad 内安全获取

  onLoad() {
    // 官方建议：getApp() 在页面生命周期内调用，而非模块顶层
    this._app = getApp()
  },

  onShow() {
    const app = this._app
    if (!app || !app.globalData) return

    // 1) 历史页带回的会话：直接渲染，不再请求
    const restore = app.globalData.chatRestore
    if (restore) {
      // 生成中不打断当前回答，保留标记待下次 onShow 消费
      if (this.data.sending) return
      app.globalData.chatRestore = null
      this.restoreConversation(restore)
      return
    }

    // 2) 首页搜索关键词：仅在真正消费时才清空，避免生成中被丢弃
    const keyword = app.globalData.pendingSearch
    if (!keyword) return
    if (this.data.sending) {
      // 关键词保留在 globalData，不重复自动发送
      wx.showToast({ title: '正在回答中，请稍后再试', icon: 'none' })
      return
    }
    app.globalData.pendingSearch = ''
    this.sendMessage(keyword)
  },

  onUnload() {
    // 页面卸载时中止仍在进行的流式请求，避免悬空回调写 setData
    this.abortTask()
    this._activeAiId = null
  },

  // 中止当前流式请求（主动停止属正常操作，不弹错误提示）
  abortTask() {
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

  // 【停止】：中止 SSE，保留已生成内容并标记「已停止」
  onStop() {
    if (!this.data.sending) return
    const aiLocalId = this._activeAiId
    this.abortTask()
    this._activeAiId = null
    this.finalizeAssistant(aiLocalId, { stopped: true })
    this.setData({ sending: false })
  },

  // 【+ 新会话】：清空当前对话；生成中先确认停止，避免两个 SSE 并发
  onNewConversation() {
    if (this.data.sending) {
      wx.showModal({
        title: '正在回答中',
        content: '是否停止当前回答并开启新会话？',
        success: (r) => {
          if (!r.confirm) return
          this.onStop()
          this.resetConversation()
        },
      })
      return
    }
    this.resetConversation()
  },

  resetConversation() {
    this._activeAiId = null
    this.setData({
      messages: [],
      conversationId: null,
      inputValue: '',
      scrollIntoView: '',
    })
  },

  // 【历史】：进入会话列表页（生成中不跳转，避免状态互相干扰）
  onHistory() {
    if (this.data.sending) {
      wx.showToast({ title: '正在回答中，请稍后再试', icon: 'none' })
      return
    }
    wx.navigateTo({ url: '/pages/chat/history' })
  },

  // 用历史页带回的数据恢复会话（ai_message 只存 role/content，无 sources）
  restoreConversation(restore) {
    const messages = ((restore && restore.messages) || []).map((m) => {
      const msg = this.makeMessage(m.role === 'user' ? 'user' : 'assistant', m.content || '')
      msg.messageId = m.id != null ? m.id : null
      return msg
    })
    const last = messages[messages.length - 1]
    this._activeAiId = null
    this.setData({
      messages,
      conversationId: restore.conversationId,
      inputValue: '',
      sending: false,
      scrollIntoView: last ? 'msg-' + last.localId : '',
    })
  },

  // 发送消息：先插入用户气泡 + 空 AI 气泡，再走 SSE
  sendMessage(content) {
    if (this.data.sending) return

    const userMsg = this.makeMessage('user', content)
    const aiMsg = this.makeMessage('assistant', '')
    const aiLocalId = aiMsg.localId
    // 记录本次流式目标气泡，回调中据此丢弃停止/切会话后的迟到数据
    this._activeAiId = aiLocalId

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
          if (this._activeAiId !== aiLocalId) return
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
          if (this._activeAiId !== aiLocalId) return
          const msg = this.findMessage(aiLocalId)
          const next = (msg ? msg.content : '') + (delta || '')
          this.updateMessage(aiLocalId, { content: next })
          this.setData({ scrollIntoView: 'msg-' + aiLocalId })
        },
        // 完成：保存会话/消息 id
        onDone: (data) => {
          if (this._activeAiId !== aiLocalId) return
          if (data && data.conversation_id != null) {
            this.setData({ conversationId: data.conversation_id })
          }
          if (data && data.message_id != null) {
            this.updateMessage(aiLocalId, { messageId: data.message_id })
          }
          this.finalizeAssistant(aiLocalId)
          this._activeAiId = null
          this.setData({ sending: false, scrollIntoView: 'msg-' + aiLocalId })
          this._task = null
        },
        // 失败：通用 toast / 鉴权已由 request.js 处理，这里只兜底空正文
        onError: () => {
          if (this._activeAiId !== aiLocalId) return
          this.finalizeAssistant(aiLocalId, { errorText: '抱歉，回答生成失败，请稍后重试。' })
          this._activeAiId = null
          this.setData({ sending: false, scrollIntoView: 'msg-' + aiLocalId })
          this._task = null
        },
      }
    )
  },

  // 收尾 AI 气泡：空正文兜底；stopped 时标记已停止（有内容则保留内容）
  finalizeAssistant(localId, { stopped = false, errorText = '' } = {}) {
    if (localId == null) return
    const msg = this.findMessage(localId)
    if (!msg) return
    const patch = {}
    if (!msg.content) {
      patch.content = errorText || (stopped ? '（已停止生成）' : '（本次没有返回内容）')
    }
    if (stopped) patch.stopped = true
    if (Object.keys(patch).length) this.updateMessage(localId, patch)
  },

  makeMessage(role, content) {
    this._localSeq += 1
    return {
      localId: this._localSeq,
      role,
      content,
      sources: [],
      messageId: null,
      stopped: false,
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
