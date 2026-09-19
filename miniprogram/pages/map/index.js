// 校园地图（F18 · 需求 10 / 二阶段方案 §2.6）：全屏 <map> 容器 + POI / 周边 / 导航 / 建筑详情
//
// 数据来源（全部为 origin/dev 真实接口，无 mock、无本地兜底假数据）：
//   GET  /map/pois          分类 POI 列表 → markers
//   GET  /map/nearby        周边（需用户坐标）→ 各 POI 真实距离
//   POST /map/navigate      步行路线（网格 A* 折线）→ polyline
//   GET  /map/building/{id} 建筑详情（由 pages/map/building 承载，本页只负责可信跳转）
//
// ⚠️ 已知后端契约缺口（不在 F18 写范围内；F18 的 handoff 与 Draft PR 说明中会逐条登记）：
//   1) `poi` 表**有** `building_id` 列，但 `GET /map/pois` 的 SELECT 只含
//      `id,name,category,latitude,longitude,floor`，**不回传 building_id**。
//      因此前端无法从 POI 推知其所属建筑：marker 卡片默认**不显示**「建筑详情」
//      入口（避免给一个永远点不动的假按钮）。该按钮只在接口真的带回
//      building_id 时才出现（见 WXML 的 wx:if="selectedPoi.buildingId"），
//      后端补出该列即自动生效，无需再改前端。
//   2) 后端**没有建筑列表接口**（只有 GET /map/building/{id} 单点查询）。
//      顶部「建筑」入口因此只登记种子数据中确实存在的建筑 id
//      （db/sql/99_init_data.sql 的 building 表：1=中心图书馆、2=第二教学楼），
//      不猜 id、不伪造列表接口。后端新增列表接口后替换 KNOWN_BUILDINGS 即可。
//
// 性能约定（地图为性能敏感组件）：
//   - markers / polyline 只在「数据真正变化」时重算（fetch / 分类切换 / 选中变化 / 路线变化）；
//   - 不使用 setInterval / 不轮询定位；
//   - 点击 marker 造成的选中态变化，只重算 markers 一个字段；
//   - 不把整包接口响应塞进 data（只保留页面渲染需要的字段）。
//
// 坐标字段差异由 utils/map.js 统一处理：后端 {lat,lng} ↔ 微信 {latitude,longitude}。

const { request } = require('../../services/request')
const mapUtil = require('../../utils/map')

/** POI → 卡片视图模型（附加已格式化的距离文案，避免在 WXML 里做计算） */
function withDistanceText(poi) {
  if (!poi) return null
  const text = poi.distance == null ? '' : mapUtil.formatDistance(poi.distance)
  return { ...poi, distanceText: text }
}

// 定位结果缓存有效期：同一会话内避免每次点击都重新定位（不轮询、不高频请求）
const LOCATION_TTL_MS = 30000

// 周边查询半径（米）。后端约束 1~5000、默认 500；这里取 1000，
// 覆盖校园步行可达范围，又能排除过远点位。
const NEARBY_RADIUS = 1000

// 分类 chips 的初始值：真实分类由首次全量 /map/pois 结果推导（见 fetch），
// 推导前先用「全部」占位，避免首帧出现空 chips 或写死的假分类。
const INITIAL_CATS = ['全部']

// 已知可用的建筑（后端**没有**建筑列表接口，只有 GET /map/building/{id}）。
// id/名称取自 db/sql/99_init_data.sql 的 building 种子（1=中心图书馆、2=第二教学楼），
// 与 pages/library/index.js 既有的 GET /map/building/1 口径一致；
// 不在此处编造不存在的 id，也不伪造一个「建筑列表」接口。
const KNOWN_BUILDINGS = [
  { id: 1, name: '中心图书馆' },
  { id: 2, name: '第二教学楼' },
]

Page({
  data: {
    cats: INITIAL_CATS,
    catIndex: 0,

    // 地图
    center: {
      latitude: mapUtil.DEFAULT_CENTER.latitude,
      longitude: mapUtil.DEFAULT_CENTER.longitude,
    },
    scale: mapUtil.DEFAULT_SCALE,
    markers: [],
    polyline: [],
    showLocation: false,
    mapError: '',

    // 列表 / 状态
    items: [],
    loading: false,
    error: '',
    empty: false,

    // 选中与导航
    selectedPoi: null,
    nav: { active: false },

    // 定位
    locating: false,
    nearbyActive: false,
    nearbyCount: 0,

    // 安全区（onLoad 填充；默认 0 保证旧基础库下布局不塌）
    statusBarHeight: 0,
    capsuleReserve: 100,
    glassFallback: false, // F10 能力探测结果：不支持毛玻璃时走实心降级
  },

  onLoad() {
    this.mapCtx = null
    this.lastLocation = null
    this.locationAt = 0
    this.userLocated = false
    this.fetchSeq = 0
    this.navSeq = 0
    this.nearbySeq = 0
    // F10：把「能力探测结果」翻译成玻璃样式库的运行时降级类（progressive enhancement 第三层），
    // 与 custom-tab-bar / 其他已整改页面保持同一口径，避免各页各写一套
    const app = typeof getApp === 'function' ? getApp() : null
    const supported = !!(app && app.globalData && app.globalData.glassSupported)
    const inset = this.readTopInset()
    this.setData({
      statusBarHeight: inset.top,
      capsuleReserve: inset.capsuleReserve,
      glassFallback: !supported,
    })
    this.fetch()
  },

  // ---------------------------------------------------------------- 工具 ----

  /**
   * 自定义导航的顶部安全区：
   *   top            —— 状态栏高度（刘海/挖孔区），导航内容下移这么多
   *   capsuleReserve —— 为微信胶囊按钮（右上角「···⊙」）预留的宽度
   *
   * navigationStyle: custom 时，原生胶囊按钮仍会绘制在右上角。若不预留，
   * 右侧的「定位」按钮会被胶囊完全盖住、点不动。
   * 胶囊位置只能通过 wx.getMenuButtonBoundingClientRect() 取得；
   * 取不到（极旧基础库 / 非真机）时回退到经验值 100px，宁可多留白也不遮按钮。
   *
   * @returns {{top: number, capsuleReserve: number}}
   */
  readTopInset() {
    // 两个用途（状态栏高度、胶囊预留宽度）共用同一次同步桥调用，避免重复探测
    let info = null
    try {
      info = typeof wx.getWindowInfo === 'function' ? wx.getWindowInfo() : wx.getSystemInfoSync()
    } catch (e) {
      info = null
    }

    let statusBar = 0
    const h = info && Number(info.statusBarHeight)
    if (Number.isFinite(h) && h > 0) statusBar = h

    let capsuleReserve = 100 // 兜底：约 87px 胶囊 + 两侧间隙
    try {
      if (typeof wx.getMenuButtonBoundingClientRect === 'function') {
        const rect = wx.getMenuButtonBoundingClientRect()
        if (rect && Number(rect.width) > 0 && Number(rect.right) > 0 && Number(rect.top) > 0) {
          // 胶囊右边缘到屏幕右侧的间隙
          const winWidth = Number(info && info.windowWidth) || 0
          const rightGap = winWidth > 0 ? Math.max(0, winWidth - Number(rect.right)) : 8
          const reserve = rightGap * 2 + Number(rect.width)
          if (Number.isFinite(reserve) && reserve > 0) capsuleReserve = Math.ceil(reserve)
          // 状态栏高度取不到时（极旧基础库）：按业界常用的 20px 兜底。
          // 这里不再"用胶囊位置反推"—— rect.top 本身已包含状态栏高度，
          // 减去自身只会得到常数，注释与行为必须一致。
          if (!statusBar) statusBar = 20
        }
      }
    } catch (e) {
      /* 保持兜底值 */
    }
    return { top: statusBar, capsuleReserve }
  },

  // 当前分类（索引 0 = 全部 → 传空串）
  currentCategory() {
    const idx = Number(this.data.catIndex)
    if (!Number.isInteger(idx) || idx <= 0) return ''
    return this.data.cats[idx] || ''
  },

  /**
   * 写入列表相关状态。
   * @param {Array}  items 当前要展示的 POI（已归一化）
   * @param {Object} opts
   * @param {string[]|null} [opts.cats] chips 列表；**仅在拿到全量数据时传入**，
   *                                    否则筛选结果会把其余分类 chip 冲掉
   * @param {boolean} [opts.applyCatIndex] 是否按 opts.category 重算 catIndex
   * @param {string}  [opts.category] 本次请求的分类（配合 applyCatIndex）
   * @param {number|null} [opts.selectedId]
   */
  applyItems(items, opts) {
    const o = opts || {}
    const patch = {
      items,
      markers: mapUtil.toMarkers(items, { selectedId: o.selectedId || null }),
      loading: false,
      error: '',
      empty: items.length === 0,
    }
    if (o.cats) patch.cats = o.cats
    if (o.applyCatIndex) {
      const idx = o.category ? (o.cats || this.data.cats).indexOf(o.category) : 0
      patch.catIndex = idx > 0 ? idx : 0
    }
    this.setData(patch)
  },

  // ---------------------------------------------------------------- 拉取 ----

  /**
   * 拉取 POI 列表。
   * @param {string} [category] 缺省取当前分类；空串 = 全部
   * @returns {Promise<void>}
   */
  fetch(category) {
    const cat = category === undefined ? this.currentCategory() : category
    const seq = ++this.fetchSeq
    // 已有数据时不显示全屏 loading，避免地图被遮罩闪烁
    this.setData({ loading: true, error: '', mapError: '' })

    return request('/map/pois', { data: { category: cat } })
      .then((res) => {
        if (seq !== this.fetchSeq) return // 过期响应（用户已切换分类）直接丢弃
        const items = mapUtil.normalizePois((res && res.items) || [])
        // 选中项已不在当前分类结果中 → 清空选中与路线，避免卡片指向不存在的点
        const selected = this.data.selectedPoi
        const stillThere = selected ? items.filter((it) => it.id === selected.id)[0] : null
        if (!stillThere) {
          this.navSeq++ // 使进行中的导航请求失效
          this.setData({ selectedPoi: null, nav: { active: false }, polyline: [] })
        }
        this.applyItems(items, {
          // 只有全量请求才能定义 chips 集合（分类结果只含本类，会冲掉其他 chip）
          cats: cat === '' ? mapUtil.toCategoryList(items) : null,
          applyCatIndex: true,
          category: cat,
          selectedId: stillThere ? stillThere.id : null,
        })
        // 首次拿到全量数据时把地图中心对准校园（不覆盖用户主动定位后的视图）
        if (cat === '' && !this.userLocated) {
          this.setData({ center: mapUtil.centerOf(items) })
        }
      })
      .catch(() => {
        if (seq !== this.fetchSeq) return
        // 同时清空 items：只清 markers 会留下不一致状态 ——
        // 之后一次成功的「附近」会用 mergeDistance(this.data.items, ...) 把旧列表
        // 重新画出来并顺手清掉 error（applyItems 内部 error:''），
        // 用户明明没重试，错误态却消失了。
        this.setData({
          loading: false,
          error: '加载失败',
          items: [],
          markers: [],
          polyline: [],
          empty: false,
        })
      })
  },

  // 重试：保持当前分类（不静默跳回「全部」）
  retryFetch() {
    this.nearbySeq++ // 使进行中的周边查询失效（它属于上一次列表）
    this.setData({ nearbyActive: false, nearbyCount: 0 })
    this.fetch(this.currentCategory())
  },

  onCatChange(e) {
    const index = Number(e.currentTarget.dataset.index)
    if (!Number.isInteger(index) || index < 0 || index >= this.data.cats.length) return
    if (index === this.data.catIndex) return
    const cat = index === 0 ? '' : this.data.cats[index]
    // 切换分类 = 更换浏览范围：清空选中与路线，避免卡片/路线指向已被过滤掉的点；
    // 同时使进行中的周边查询失效（其距离属于旧分类）
    this.navSeq++
    this.nearbySeq++
    this.setData({
      catIndex: index,
      nearbyActive: false,
      nearbyCount: 0,
      selectedPoi: null,
      nav: { active: false },
      polyline: [],
    })
    this.fetch(cat)
  },

  // ---------------------------------------------------------------- 定位 ----

  /**
   * 取用户位置（带 TTL 缓存，避免重复高频定位）。
   * 三种结果如实区分：成功回调坐标；拒绝授权回调 null 并给出可操作指引；
   * 其他失败回调 null 并 toast。任何情况都不伪造坐标。
   *
   * @param {Function} done 回调 (latlng|null)
   */
  withLocation(done) {
    const now = Date.now()
    if (this.lastLocation && now - this.locationAt < LOCATION_TTL_MS) {
      done(this.lastLocation)
      return
    }
    this.setData({ locating: true })
    wx.getLocation({
      // gcj02：与微信地图组件坐标系一致，否则 marker/定位点会整体偏移
      type: 'gcj02',
      isHighAccuracy: true,
      highAccuracyExpireTime: 4000,
      success: (res) => {
        this.lastLocation = { latitude: res.latitude, longitude: res.longitude }
        this.locationAt = Date.now()
        this.setData({ locating: false, showLocation: true })
        done(this.lastLocation)
      },
      fail: (err) => {
        this.setData({ locating: false })
        const msg = String((err && err.errMsg) || '')
        if (msg.indexOf('auth deny') !== -1 || msg.indexOf('auth denied') !== -1 || msg.indexOf('authorize') !== -1) {
          // 明确拒绝：给出「去设置」入口。
          // ⚠️ 注意这是**本页自己的**引导弹窗，不是系统授权弹窗 —— 系统弹窗只会在
          // 首次调用时出现，之后由 scope.userLocation 的记忆决定；本页无法读取
          // scope 状态（wx.getSetting 是异步的），因此这里不缓存"已拒绝"，
          // 用户每次主动点「定位」都会看到一次引导。这是有意为之：
          // 用户主动点击 = 有明确意图，此时给可操作指引优于静默失败。
          wx.showModal({
            title: '需要位置权限',
            content: '用于定位到你的位置、查找周边地点并规划步行路线。可在设置中重新开启。',
            confirmText: '去设置',
            success: (r) => {
              if (r.confirm) wx.openSetting({})
            },
          })
        } else {
          wx.showToast({ title: '定位失败，请检查系统定位开关', icon: 'none' })
        }
        done(null)
      },
    })
  },

  // 顶部定位按钮：定位成功 → 地图切到用户位置；失败/拒绝 → 只提示，不改地图视图
  onLocate() {
    if (this.data.locating) return // 防连点
    this.withLocation((loc) => {
      if (!loc) return
      this.userLocated = true
      this.applyToUserLocation(loc)
    })
  },

  // 地图切到用户位置，并开启 show-location 显示定位蓝点
  applyToUserLocation(loc) {
    const ctx = this.getMapCtx()
    this.setData({
      center: { latitude: loc.latitude, longitude: loc.longitude },
      scale: mapUtil.LOCATED_SCALE,
      showLocation: true,
    })
    // moveToLocation 依赖 show-location 已开启，setData 后再调用；失败则退化为上面的 center 更新
    if (ctx && typeof ctx.moveToLocation === 'function') {
      setTimeout(() => {
        try {
          ctx.moveToLocation({ latitude: loc.latitude, longitude: loc.longitude })
        } catch (e) {
          // 忽略：center/scale 已更新，地图仍会移动到目标位置
        }
      }, 50)
    }
  },

  getMapCtx() {
    if (!this.mapCtx && typeof wx.createMapContext === 'function') {
      this.mapCtx = wx.createMapContext('campus-map', this)
    }
    return this.mapCtx
  },

  // ------------------------------------------------------------- 周边 ----

  /**
   * 【附近】：需用户位置，失败只提示、不破坏主地图。
   * 列表只保留「当前分类」内的点（保持分类筛选语义），距离按 id 合并真实值并升序排序。
   *
   * 竞态：与 fetch / navigate 一样用序号守卫。否则一条迟到的 nearby 响应会在
   * 用户已经切换分类（onCatChange 会清 nearbyActive/nearbyCount）之后
   * 把「周边 N 个」重新点亮，并把旧分类的距离贴到新列表上。
   */
  onNearby() {
    if (this.data.locating) return
    const seq = ++this.nearbySeq
    this.withLocation((loc) => {
      if (!loc) return
      if (seq !== this.nearbySeq) return // 定位期间用户已发起新一轮周边查询
      this.userLocated = true
      this.setData({ loading: true })
      request('/map/nearby', {
        data: { lat: loc.latitude, lng: loc.longitude, radius: NEARBY_RADIUS },
      })
        .then((res) => {
          if (seq !== this.nearbySeq) return // 过期响应（已切换分类 / 重新查询）
          const all = mapUtil.normalizePois((res && res.items) || [])
          // 主列表只接受「当前分类」内的点，保持分类筛选语义；
          // 距离按 id 合并（后端返回的是半径内的全部 POI，可能是别的分类），
          // 因此列表仍是纯真实数据，不混入其它分类的点。
          const cat = this.currentCategory()
          const inCat = cat ? all.filter((it) => it.category === cat) : all
          const merged = mapUtil.sortByDistance(mapUtil.mergeDistance(this.data.items, inCat))
          this.applyItems(merged, {
            selectedId: this.data.selectedPoi ? this.data.selectedPoi.id : null,
          })
          this.refreshSelectedFrom(merged)
          this.setData({ nearbyActive: true, nearbyCount: inCat.length, loading: false })
          if (inCat.length === 0) wx.showToast({ title: '附近暂无地点', icon: 'none' })
        })
        .catch(() => {
          if (seq !== this.nearbySeq) return
          // 周边失败不影响主地图：保留原 items / markers，只提示
          this.setData({ loading: false, nearbyActive: false, nearbyCount: 0 })
          wx.showToast({ title: '周边加载失败', icon: 'none' })
        })
    })
  },

  /** 列表被替换后，同步选中卡片的距离文案（选中对象不在地图重建范围内） */
  refreshSelectedFrom(items) {
    const selected = this.data.selectedPoi
    if (!selected) return
    const found = items.filter((it) => it.id === selected.id)[0]
    if (!found) return
    this.setData({ selectedPoi: withDistanceText(found) })
  },

  // ------------------------------------------------------------- 交互 ----

  onMarkerTap(e) {
    const rawId = e && e.detail ? e.detail.markerId : null
    const id = Number(rawId)
    if (!Number.isFinite(id)) return
    const poi = this.data.items.filter((it) => it.id === id)[0]
    if (!poi) return
    this.selectPoi(poi)
  },

  selectPoi(poi) {
    this.setData({
      selectedPoi: withDistanceText(poi),
      markers: mapUtil.toMarkers(this.data.items, { selectedId: poi.id }),
    })
  },

  // 点空白地图 → 收起卡片（保留路线）
  onMapTap() {
    if (!this.data.selectedPoi) return
    this.setData({
      selectedPoi: null,
      markers: mapUtil.toMarkers(this.data.items, { selectedId: null }),
    })
  },

  onCloseCard() {
    this.onMapTap()
  },

  onMapError(e) {
    const detail = e && e.detail ? e.detail : {}
    const msg = String(detail.errMsg || '')
    // 地图组件自身渲染失败（离线 / 无地图能力）如实提示，不静默
    this.setData({ mapError: '地图加载失败' + (msg ? '（' + msg + '）' : '') })
  },

  // ------------------------------------------------------------- 导航 ----

  // POST /map/navigate：有定位则用真实起点，否则由后端取最近地标作锚点（start_source 如实展示）
  onNavigate() {
    const poi = this.data.selectedPoi
    if (!poi || this.data.nav.loading) return

    const loc =
      this.lastLocation && Date.now() - this.locationAt < LOCATION_TTL_MS ? this.lastLocation : null
    const payload = { to_poi_id: poi.id }
    if (loc) {
      payload.from_lat = loc.latitude
      payload.from_lng = loc.longitude
    }

    const seq = ++this.navSeq
    this.setData({ nav: { active: false, loading: true, targetName: poi.name } })

    request('/map/navigate', { method: 'POST', data: payload })
      .then((res) => {
        if (seq !== this.navSeq) return
        const vm = mapUtil.buildNavViewModel(res)
        const polyline = mapUtil.toPolyline(res && res.path)
        if (!vm || polyline.length === 0) {
          // 返回结构异常：如实报错，不画半条路线
          this.setData({ nav: { active: false, loading: false } })
          wx.showToast({ title: '未获取到路线', icon: 'none' })
          return
        }
        this.setData({
          nav: {
            active: true,
            loading: false,
            distanceText: vm.distanceText,
            durationText: vm.durationText,
            detourText: vm.detourText,
            targetName: vm.targetName || poi.name,
            fromUserLocation: vm.fromUserLocation,
            // 后端在障碍封死时回退直线（algorithm=straight-fallback），如实标注
            isStraightFallback: vm.algorithm === 'straight-fallback',
          },
          polyline,
          markers: mapUtil.toMarkers(this.data.items, { selectedId: poi.id }),
        })
        this.fitRoute()
      })
      .catch(() => {
        if (seq !== this.navSeq) return
        // 失败时一并清掉上一次的路线，避免「卡片说导航失败、地图上还留着旧路线」
        this.setData({ nav: { active: false, loading: false }, polyline: [] })
      })
  },

  // 让路线整体进入视野（includePoints 失败不影响已绘制的 polyline）
  fitRoute() {
    const ctx = this.getMapCtx()
    if (!ctx || typeof ctx.includePoints !== 'function' || this.data.polyline.length === 0) return
    try {
      ctx.includePoints({
        points: this.data.polyline[0].points,
        padding: [120, 60, 220, 60],
      })
    } catch (e) {
      // 忽略：路线已绘制，用户仍可手动缩放
    }
  },

  onClearNav() {
    this.navSeq++
    this.setData({ nav: { active: false }, polyline: [] })
  },

  // --------------------------------------------------------- 建筑详情 ----

  /**
   * 从 marker 卡片进入建筑详情。
   *
   * ⚠️ `GET /map/pois` **不返回** building_id（后端 SELECT 未含该列），
   * 所以正常数据下 selectedPoi.buildingId 恒为 null，按钮也不会渲染
   * （WXML 用 wx:if="selectedPoi.buildingId" 控制）。此处仍保留一个
   * 显式守卫，保证即使按钮因任何原因被点到，也不会跳到
   * `/pages/map/building?id=null` 这种假入口。
   */
  onOpenBuilding() {
    const poi = this.data.selectedPoi
    const id = poi && poi.buildingId != null ? Number(poi.buildingId) : NaN
    if (!Number.isFinite(id) || id <= 0) {
      wx.showToast({ title: '该地点无建筑详情', icon: 'none' })
      return
    }
    wx.navigateTo({ url: '/pages/map/building?id=' + id })
  },

  /**
   * 顶部「建筑」入口。
   *
   * 后端**没有建筑列表接口**（只有 GET /map/building/{id} 单点查询），
   * 因此只登记种子数据中确实存在的建筑（见 KNOWN_BUILDINGS），
   * 选中后进入既有的 pages/map/building 详情页（真实 GET /map/building/{id}）。
   * 不做「猜 id」的假入口，也不伪造一个建筑列表接口。
   */
  onOpenBuildings() {
    const list = KNOWN_BUILDINGS.map((b) => b.name)
    wx.showActionSheet({
      itemList: list,
      success: (r) => {
        const picked = KNOWN_BUILDINGS[r.tapIndex]
        if (!picked) return
        wx.navigateTo({ url: '/pages/map/building?id=' + picked.id })
      },
      fail: () => {
        /* 用户取消：不处理 */
      },
    })
  },

  // ------------------------------------------------------------- 返回 ----

  onBack() {
    const pages = typeof getCurrentPages === 'function' ? getCurrentPages() : []
    if (pages.length > 1) {
      wx.navigateBack({ delta: 1 })
      return
    }
    // 直接进入本页（无返回栈）时退回首页，避免返回按钮无响应。
    // 首页是 Tab 页，本仓对 Tab 页 URL 统一用 switchTab（见 pages/auth/login.js、pages/index/index.js）
    wx.switchTab({ url: '/pages/index/index' })
  },
})
