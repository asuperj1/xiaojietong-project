// 论坛页（Tab）—— F7：帖子列表（最新/分类/热点）+ 发帖入口
// F16：搜索框 + 7 标签栏（全部/学习/生活/闲置/活动/热点/我的帖子）+ 帖子卡片玻璃化
const { request } = require('../../services/request')
const { formatTime } = require('../../utils/format')

// 标签栏（F16）：7 项。
// `kind` 决定请求口径；`value` 是**复用既有 forum category 参数**的取值（空串 = 全部），
// **仅 category 类标签有意义** —— hot / mine 走后端专用接口、不发 category 参数，
// 故这两项刻意不写 `value`，避免出现「带了一个永远不会被发送的值」的假契约：
//   category → GET /topics?category=（后端已支持，见 docs/api.md §8）
//   hot      → GET /topics/hot（热点榜；后端**无** category / keyword 参数）
//   mine     → GET /topics/mine（我的帖子，额外返回 audit_status）
const CATS = [
  { label: '全部', value: '', kind: 'category' },
  { label: '学习', value: '学习', kind: 'category' },
  { label: '生活', value: '生活', kind: 'category' },
  { label: '闲置', value: '闲置', kind: 'category' },
  { label: '活动', value: '活动', kind: 'category' },
  { label: '热点', kind: 'hot' },
  { label: '我的帖子', kind: 'mine' },
]

const PAGE_SIZE = 20

// 审核状态文案。与 pages/forum/mine 的映射**取值刻意不同**：
// 这里未返回 audit_status 的列表（全部/分类/热点）统一映射成空串 = 「不适用，不渲染」，
// 而 mine 页用「未知」= 「拿到了预期外的值」。两者语义不同，故不抽成一个共享 mapper。
const AUDIT_TEXT = { 0: '待审核', 1: '已通过', 2: '未通过' }

// 列表条目归一化：三种接口（/topics、/topics/hot、/topics/mine）字段不完全一致，
// 统一在这里映射，页面只消费映射后的字段。
function mapItem(t) {
  const auditStatus = Number(t.audit_status)
  return {
    id: t.id,
    title: t.title,
    category: t.category,
    summary: t.ai_summary || (t.content || '').slice(0, 60),
    likeCount: t.like_count || 0,
    commentCount: t.comment_count || 0,
    viewCount: t.view_count || 0,
    authorName: t.author_name || '',
    isHot: !!t.is_hot,
    time: formatTime(t.created_at),
    // 只有 /topics/mine 返回 audit_status；其余接口为 undefined → auditText 为空即不渲染
    auditText: AUDIT_TEXT[auditStatus] || '',
    auditOk: auditStatus === 1,
    auditBad: auditStatus === 2,
  }
}

// 依据「当前标签 + 已提交关键词」解析请求（F16 的核心分派）。
function buildRequest(cat, keyword, page, size) {
  if (keyword) {
    // 关键词检索走后端既有能力（B29）：GET /topics?keyword=&category=
    // ⚠️ /topics/hot 与 /topics/mine 都**不接受 keyword**（后端契约如此，见 docs/api.md §8）。
    // 这两栏下不伪造后端：降级为「全部」分类检索，并在页面上明确提示口径。
    const scoped = cat.kind === 'category'
    return {
      path: '/topics',
      data: { category: scoped ? cat.value : '', keyword, page, size },
      hint: scoped ? '' : '「' + cat.label + '」暂不支持关键词筛选，已为你搜索全部帖子',
    }
  }
  if (cat.kind === 'hot') return { path: '/topics/hot', data: {}, hint: '' }
  if (cat.kind === 'mine') return { path: '/topics/mine', data: { page, size }, hint: '' }
  // 以下仅 category 类标签可达（hot / mine 已在上面返回），故 cat.value 必有值
  return { path: '/topics', data: { category: cat.value, page, size }, hint: '' }
}

Page({
  data: {
    cats: CATS.map((c) => c.label),
    catIndex: 0,
    keyword: '', // 搜索框输入值（受控）
    searchText: '', // 已提交的关键词；空 = 普通列表
    searchHint: '', // 当前标签不支持关键词筛选时的口径说明
    glassClass: '', // F10 运行时玻璃降级类（.is-glass-fallback）
    items: [],
    page: 1,
    loading: false, // 初始必须为 false：fetchList() 用 loading 防重复追加，若初始为 true 则首次进入不请求
    error: '',
    finished: false,
  },

  onLoad() {
    // F10 玻璃运行时降级：能力探测不支持时给根节点加 .is-glass-fallback，
    // 让 styles/glass.wxss 的实心底规则命中（它们是祖先选择器，页面需自己包一层）。
    const app = getApp()
    const supported = !!(app && app.globalData && app.globalData.glassSupported)
    this.setData({ glassClass: supported ? '' : 'is-glass-fallback' })
  },

  onShow() {
    // Tab 每次进入刷新列表（发帖/点赞返回后保持最新）
    this.refresh()
  },

  onPullDownRefresh() {
    this.refresh(() => wx.stopPullDownRefresh())
  },

  onReachBottom() {
    if (this.data.finished || this.data.loading) return
    // 热点榜一次性返回，后端 /topics/hot 无分页参数：显式挡住，避免请求失败
    // （finished 仍为 false）时触底把同一批数据重复追加一遍。
    if (CATS[this.data.catIndex].kind === 'hot') return
    this.fetchList(false)
  },

  // ---------------- 标签栏（F16）----------------

  onCatChange(e) {
    // 标签是 bindtap（不是 picker）：索引来自 data-index，e.detail 中没有 value
    const index = Number(e.currentTarget.dataset.index)
    if (!Number.isInteger(index) || index < 0 || index >= CATS.length) return
    // 切标签时一并提交搜索框里已输入但未回车的关键词，
    // 避免「输入框有字、列表却没按它筛选」的不一致状态。
    this.setData({ catIndex: index, searchText: (this.data.keyword || '').trim() }, () => this.refresh())
  },

  // ---------------- 搜索（F16）----------------

  onSearchInput(e) {
    this.setData({ keyword: e.detail.value })
  },

  onSearchConfirm() {
    const keyword = (this.data.keyword || '').trim()
    // 与上次提交一致且当前无错误 → 不重复请求（回车/再次确认的常见误触）。
    // ⚠️ 上次请求失败（error 非空，如后端 keyword 检索失败关闭 500+5001）时**不早退**：
    // 否则用户再按一次回车无法重试，只能去点「重试」按钮。
    if (keyword === this.data.searchText && !this.data.error) return
    this.setData({ keyword, searchText: keyword }, () => this.refresh())
  },

  onSearchClear() {
    if (!this.data.keyword && !this.data.searchText) return
    const hadSearch = !!this.data.searchText
    this.setData({ keyword: '', searchText: '' }, () => {
      // 仅当此前确有已提交的搜索时才需要重新拉列表
      if (hadSearch) this.refresh()
    })
  },

  onPublish() {
    wx.navigateTo({ url: '/pages/forum/create' })
  },

  onItemTap(e) {
    wx.navigateTo({ url: '/pages/forum/detail?id=' + e.currentTarget.dataset.id })
  },

  refresh(done) {
    this.setData({ page: 1, items: [], finished: false })
    this.fetchList(true, done)
  },

  fetchList(reset, done) {
    // 追加分页：已有请求在途时不重复发起（reset 不受此限 —— 否则切标签/搜索会被在途
    // 请求吞掉，表现为「列表已清空但永不加载」）。
    if (!reset && this.data.loading) {
      if (typeof done === 'function') done()
      return
    }
    const cat = CATS[this.data.catIndex]
    const page = reset ? 1 : this.data.page
    const req = buildRequest(cat, this.data.searchText, page, PAGE_SIZE)
    // 请求序号：切标签/搜索可能在上一次请求在途时发起，只允许最后一次的结果落地，
    // 避免旧响应覆盖新标签的数据。
    const seq = (this.reqSeq = (this.reqSeq || 0) + 1)
    this.setData({ loading: true, error: '' })

    request(req.path, { data: req.data })
      .then((res) => {
        if (seq !== this.reqSeq) {
          if (typeof done === 'function') done()
          return
        }
        const raw = (res && res.items) || []
        const items = raw.map(mapItem)
        this.setData({
          items: reset ? items : this.data.items.concat(items),
          page: page + 1,
          loading: false,
          // 热点榜一次性返回（后端不分页）；其余按「本页不足一页」判定到底
          finished: cat.kind === 'hot' ? true : raw.length < PAGE_SIZE,
          searchHint: req.hint,
        })
        if (typeof done === 'function') done()
      })
      .catch((err) => {
        if (seq !== this.reqSeq) {
          if (typeof done === 'function') done()
          return
        }
        // 关键词检索在后端缺 FULLTEXT 索引时失败关闭（500 + 5001，见 services/topic_search.py）；
        // 用后端原文提示，避免误报成「加载失败」。
        const msg = err && err.code === 5001 ? err.message : ''
        this.setData({
          loading: false,
          error: msg || '加载失败，请稍后重试',
          searchHint: req.hint,
        })
        if (typeof done === 'function') done()
      })
  },
})
