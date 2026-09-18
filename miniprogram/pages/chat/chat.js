// AI 对话页（Tab）—— F4：SSE 流式多轮对话 + 停止 / 新建会话 / 历史
// F14-A：AI 助手侧边栏（从左侧滑出：新建会话 + 会话列表 + 长按删除；遮罩点击 / 右滑手势关闭）
//        语音（长按说话 → 转写）不在本页，属 F14-B，另行分支交付。
const { request, sseRequest } = require('../../services/request')
const { formatTime } = require('../../utils/format')

// ==================== F14-A 侧边栏常量 ====================
// 向右拖拽超过该距离即关闭侧边栏。touch 事件的 clientX 单位是 px（不是 rpx）：
// 60px 在 375pt 宽的机型上约为面板宽度（560rpx ≈ 280px）的 1/5，
// 与 iOS 抽屉「拖过一小段就关闭、拖不到就回弹」的手感一致，避免误触关掉。
const SIDEBAR_CLOSE_DRAG_PX = 60
// 拖拽死区：位移小于该值视为抖动；且只有「横向位移大于纵向位移」才进入拖拽态，
// 否则会把会话列表的纵向滚动误判成关闭手势。
const SIDEBAR_DRAG_DEAD_ZONE_PX = 6
// 「清空历史」轮数上限：后端只有单条 DELETE /chat/conversations/{id}（无批量接口），
// 而 GET /chat/conversations 每次最多返回 50 条 —— 故实现为「取一批 → 删一批」循环。
// 封顶 10 轮（≈500 条），避免后端始终删不干净时无限循环；超出则提示「部分未清空」。
const CLEAR_HISTORY_MAX_ROUNDS = 10

Page({
  data: {
    messages: [],        // 消息列表：{ localId, role, content, sources, messageId, stopped }
    inputValue: '',      // 输入框内容
    sending: false,      // 是否正在生成回答
    conversationId: null, // 当前会话 id（首条消息前为 null）
    scrollIntoView: '',  // 滚动锚点 id
    glassClass: '',      // F10 运行时玻璃降级类（.is-glass-fallback）

    // ---- F14-A 侧边栏 ----
    sidebarOpen: false,     // 侧边栏是否展开（含遮罩）
    sidebarDragging: false, // 是否正在跟手拖拽（拖拽期间关掉过渡动画）
    sidebarStyle: '',       // 拖拽位移（空串 = 交给 WXSS 的开/合位置）
    conversations: [],      // 会话列表：{ id, title, time }（后端已按 updated_at 倒序）
    convLoading: false,     // 会话列表加载中
    convError: '',          // 会话列表加载失败文案（面板内可重试）
  },

  // 非渲染态字段
  _task: null,        // 当前 SSE RequestTask，停止/卸载时 abort
  _localSeq: 0,       // localId 自增序列
  _activeAiId: null,  // 正在流式填充的 AI 气泡 localId（用于丢弃停止后的迟到回调）
  _app: null,         // getApp() 句柄，在 onLoad 内安全获取
  _dragStartX: null,  // 侧边栏手势起点（null = 当前没有进行中的手势）
  _dragStartY: null,
  _dragX: 0,          // 当前跟手位移（px）
  _dragging: false,   // 是否已越过死区进入拖拽态
  _createSeq: 0,      // 「新建会话」请求序号（丢弃迟到响应，避免覆盖已开始的对话）
  _convLoading: false, // 会话消息加载中（防止连点重复拉取）

  onLoad() {
    // 官方建议：getApp() 在页面生命周期内调用，而非模块顶层
    this._app = getApp()
    // F10 玻璃运行时降级：能力探测不支持时给根节点加 .is-glass-fallback，
    // 让 styles/glass.wxss 的实心底规则命中（它们是祖先选择器，页面需自己包一层）。
    const app = this._app
    const supported = !!(app && app.globalData && app.globalData.glassSupported)
    this.setData({ glassClass: supported ? '' : 'is-glass-fallback' })
  },

  onShow() {
    // F14-A：Tab 切走再回来时侧边栏不应保持展开 —— 遮罩会盖住整页、看起来像卡死；
    // 拖拽中间态同理必须复位。放在所有提前 return 之前，保证任何进入路径都会归一化。
    this.closeSidebarState()

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
    this.resetDrag()
  },

  // ==================== F14-A 侧边栏 ====================

  // 【菜单】按钮：展开侧边栏并拉取会话列表（每次展开都刷新，删除/新建后不会看到旧列表）
  onOpenSidebar() {
    this.closeSidebarState()
    this.setData({ sidebarOpen: true })
    this.fetchConversations()
  },

  // 收起侧边栏（遮罩点击 / 右滑手势 / 新建会话 / 选中会话都走这里）
  onCloseSidebar() {
    this.closeSidebarState()
  },

  // 关闭并复位拖拽中间态（幂等：未展开时调用不会产生多余渲染）
  closeSidebarState() {
    this.resetDrag()
    if (!this.data.sidebarOpen && !this.data.sidebarDragging && !this.data.sidebarStyle) return
    this.setData({ sidebarOpen: false, sidebarDragging: false, sidebarStyle: '' })
  },

  onMaskTap() {
    this.onCloseSidebar()
  },

  // 会话列表：GET /chat/conversations（后端按 updated_at DESC，最多 50 条）
  fetchConversations() {
    this.setData({ convLoading: true, convError: '' })
    request('/chat/conversations')
      .then((res) => {
        const items = ((res && res.items) || []).map((c) => ({
          id: c.id,
          title: c.title || '未命名会话',
          time: formatTime(c.updated_at),
        }))
        this.setData({ conversations: items, convLoading: false })
      })
      .catch(() => {
        // 网络/业务错误提示已由 services/request.js 统一处理，这里只落到面板内的可重试态
        this.setData({ convLoading: false, convError: '加载失败，请稍后重试' })
      })
  },

  // 点选会话：拉历史消息 → 还原到对话区并收起侧栏（与历史页同一套数据口径）
  onTapConversation(e) {
    const id = Number(e.currentTarget.dataset.id)
    if (!id) return
    if (this.data.sending) {
      wx.showToast({ title: '正在回答中，请稍后再试', icon: 'none' })
      return
    }
    if (this._convLoading) return
    this._convLoading = true
    wx.showLoading({ title: '加载中…', mask: true })

    request('/chat/conversations/' + id + '/messages')
      .then((res) => {
        const messages = (res && res.items) || []
        this._convLoading = false
        wx.hideLoading()
        this.closeSidebarState()
        this.restoreConversation({ conversationId: id, messages })
      })
      .catch(() => {
        // 错误提示已由 services/request.js 统一处理
        this._convLoading = false
        wx.hideLoading()
      })
  },

  // 长按会话卡片 → 确认后删除（需求：会话可删）
  onConvLongPress(e) {
    const id = Number(e.currentTarget.dataset.id)
    if (!id) return
    // 正在流式回答的会话不删：删了之后 SSE 回调仍会往 messages 里写，状态会打架
    if (this.data.sending && this.data.conversationId === id) {
      wx.showToast({ title: '正在回答中，请稍后再试', icon: 'none' })
      return
    }
    const target = this.data.conversations.find((c) => c.id === id)
    wx.showModal({
      title: '删除会话',
      content: '删除后不可恢复，确定删除「' + this.shortTitle(target && target.title) + '」？',
      confirmText: '删除',
      confirmColor: '#E64340',
      success: (r) => {
        if (r.confirm) this.deleteConversation(id)
      },
    })
  },

  // 会话标题进弹窗前的截断：标题最长 100 字，整条塞进 modal 会被截断得很难看
  shortTitle(title) {
    const text = title || '该会话'
    return text.length > 12 ? text.slice(0, 12) + '…' : text
  },

  deleteConversation(id) {
    request('/chat/conversations/' + id, { method: 'DELETE' })
      .then(() => {
        this.setData({ conversations: this.data.conversations.filter((c) => c.id !== id) })
        // 删掉的正是当前会话 → 对话区一并清空，避免继续往已删除的会话里发消息
        if (this.data.conversationId === id) this.resetConversation()
        wx.showToast({ title: '已删除', icon: 'none' })
      })
      .catch(() => {
        // 错误提示已由 services/request.js 统一处理
      })
  },

  // 【清空历史】：确认后删除全部会话
  onClearHistory() {
    if (this.data.sending) {
      wx.showToast({ title: '正在回答中，请稍后再试', icon: 'none' })
      return
    }
    if (!this.data.conversations.length) {
      wx.showToast({ title: '暂无历史会话', icon: 'none' })
      return
    }
    wx.showModal({
      title: '清空历史',
      content: '将删除全部会话记录，删除后不可恢复。',
      confirmText: '清空',
      confirmColor: '#E64340',
      success: (r) => {
        if (r.confirm) this.clearAllConversations()
      },
    })
  },

  // 逐条删除全部会话。
  // 串行而非 Promise.all：① 失败即停，不会连打 50 个失败 toast；② 小程序并发请求上限 10，
  // 并发反而更慢且难以给出确定的失败语义。
  clearAllConversations() {
    wx.showLoading({ title: '正在清空…', mask: true })
    this.clearRound(CLEAR_HISTORY_MAX_ROUNDS)
  },

  clearRound(roundsLeft) {
    request('/chat/conversations')
      .then((res) => {
        const items = (res && res.items) || []
        if (!items.length) return this.finishClear(true)
        if (roundsLeft <= 0) return this.finishClear(false)
        return this.deleteSequential(items, 0).then((ok) => {
          if (!ok) return this.finishClear(false)
          return this.clearRound(roundsLeft - 1)
        })
      })
      .catch(() => this.finishClear(false))
  },

  // 串行删除一批会话；返回是否全部成功
  deleteSequential(items, i) {
    if (i >= items.length) return Promise.resolve(true)
    return request('/chat/conversations/' + items[i].id, { method: 'DELETE' })
      .then(() => this.deleteSequential(items, i + 1))
      .catch(() => false)
  },

  finishClear(done) {
    wx.hideLoading()
    // 全部会话都没了 → 当前对话区也必须清空，否则会停在一个已不存在的会话上
    this.resetConversation()
    this.fetchConversations()
    wx.showToast({
      title: done ? '已清空历史' : '部分会话未清空，请重试',
      icon: 'none',
    })
  },

  // 侧边栏底部「设置」入口。
  // 设置页（头像/昵称/学号）属 F17，当前 dev 上不存在该页面 —— 故刻意不做跳转
  // （跳到未注册页面 = 白屏），只给出明确提示，不伪装成已完成。
  onSettingsEntry() {
    wx.showToast({ title: '设置页尚未开放', icon: 'none' })
  },

  // ---- 侧边栏手势：右滑关闭 ----

  onSidebarTouchStart(e) {
    if (!this.data.sidebarOpen) return
    const t = (e.touches && e.touches[0]) || (e.changedTouches && e.changedTouches[0])
    if (!t) return
    this._dragStartX = t.clientX
    this._dragStartY = t.clientY
    this._dragX = 0
    this._dragging = false
  },

  onSidebarTouchMove(e) {
    if (this._dragStartX == null) return
    const t = (e.touches && e.touches[0]) || (e.changedTouches && e.changedTouches[0])
    if (!t) return
    const dx = t.clientX - this._dragStartX
    const dy = t.clientY - this._dragStartY

    if (!this._dragging) {
      if (Math.abs(dx) < SIDEBAR_DRAG_DEAD_ZONE_PX) return // 抖动死区
      // 只认「向右且比纵向更明显」的位移：其余情况（左拖 / 上下滚动列表）不接管
      if (dx <= 0 || Math.abs(dx) <= Math.abs(dy)) return
      this._dragging = true
    }

    // 只跟随向右的位移：向左拖不把面板推出屏幕（面板最左就是 0）
    this._dragX = dx > 0 ? dx : 0
    this.setData({
      sidebarDragging: true,
      sidebarStyle: 'transform: translateX(' + this._dragX + 'px)',
    })
  },

  onSidebarTouchEnd() {
    if (this._dragStartX == null) return
    const dragged = this._dragX
    const wasDragging = this._dragging
    this.resetDrag()
    if (wasDragging && dragged >= SIDEBAR_CLOSE_DRAG_PX) {
      this.setData({ sidebarOpen: false, sidebarDragging: false, sidebarStyle: '' })
      return
    }
    // 没拖够 → 回弹（清空内联位移，位置交回 WXSS 的展开态）
    this.setData({ sidebarDragging: false, sidebarStyle: '' })
  },

  // touchcancel（来电、系统手势等打断）**不当作一次完成的拖拽**：
  // 回到拖拽前的展开位，避免手指被系统收走后面板停在半路、或误判为「关闭」。
  onSidebarTouchCancel() {
    if (this._dragStartX == null) return
    this.resetDrag()
    this.setData({ sidebarDragging: false, sidebarStyle: '' })
  },

  resetDrag() {
    this._dragStartX = null
    this._dragStartY = null
    this._dragX = 0
    this._dragging = false
  },

  // ==================== F4 对话 ====================

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

  // 【+ 新会话】（顶部操作条）与侧边栏【新建会话】共用：
  // 生成中先确认停止，避免两个 SSE 并发
  onNewConversation() {
    if (this.data.sending) {
      wx.showModal({
        title: '正在回答中',
        content: '是否停止当前回答并开启新会话？',
        success: (r) => {
          if (!r.confirm) return
          this.onStop()
          this.startNewConversation()
        },
      })
      return
    }
    this.startNewConversation()
  },

  startNewConversation() {
    this.resetConversation()
    this.closeSidebarState()
    this.createConversation()
  },

  // 显式新建会话（B24）：点「新建」即拿到 conversation_id，空会话可直接对话。
  // 失败不阻断：conversationId 保持 null 时，首条消息仍由 /chat/send 懒建会话
  // （即 F4 的原有路径），用户不会因为这一次请求失败而无法开始新对话。
  createConversation() {
    this._createSeq += 1
    const seq = this._createSeq
    request('/chat/conversations', { method: 'POST', data: {} })
      .then((res) => {
        // 迟到响应丢弃：期间又点过新建，或用户已经开口（此时会话 id 归 /chat/send 所有，
        // 直接 setData 会把正在进行的对话指到一个空会话上）
        if (seq !== this._createSeq) return
        if (this.data.messages.length || this.data.sending) return
        const id = res && res.conversation_id
        if (id == null) return
        this.setData({ conversationId: id })
      })
      .catch(() => {
        // 错误提示已由 services/request.js 统一处理（含登录失效跳转）
      })
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

    // 本条消息由 /chat/send 负责建会话（conversationId 为 null 时后端会新建），
    // 故让仍在飞的「新建会话」响应作废，避免它稍后把 id 改成另一个空会话。
    this._createSeq += 1

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
        // `C20` 拒答：检索结果不足以回答，后端没调模型，直接展示结论文案
        onRefused: (data) => {
          if (this._activeAiId !== aiLocalId) return
          this.updateMessage(aiLocalId, { content: (data && data.delta) || '', refused: true })
          this.setData({ scrollIntoView: 'msg-' + aiLocalId })
        },
        // `C20` 引用清洗：正文已流过，用清洗后的全文覆盖，并提示剔除了伪造引用
        onCitations: (data) => {
          if (this._activeAiId !== aiLocalId) return
          if (data && typeof data.final === 'string' && data.final) {
            this.updateMessage(aiLocalId, { content: data.final })
          }
          const n = (data && data.fabricated && data.fabricated.length) || 0
          if (n > 0) {
            wx.showToast({ title: '已剔除非知识库引用', icon: 'none' })
          }
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
