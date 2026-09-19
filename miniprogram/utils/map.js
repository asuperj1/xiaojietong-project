// ============================================================
// F18 校园地图：数据变换层（纯函数，无 wx 依赖、无副作用）
//
// 职责边界：
//   - 本模块只做「后端契约 → 微信 <map> 组件契约」的**纯映射**，
//     不发起请求、不 setData、不弹窗、不读取全局状态；
//   - 页面（pages/map/index.js）负责网络、权限、setData 与用户反馈。
//   这样拆分是为了让坐标/字段映射这类**易错且不可真机调试**的逻辑，
//   能在 Node 里被逐条断言（tools/verify_f18_campus_map.js）。
//
// 后端契约（已与 origin/dev 的 backend/app/routers/map_api.py、docs/api.md §9 逐行对账）：
//   GET  /map/pois           -> data.items[] = {id,name,category,latitude,longitude,floor}
//   GET  /map/nearby         -> data.items[] = {id,name,category,latitude,longitude} + distance(米)
//                               （**没有 floor**；按 distance 升序；radius 默认 500 / 取值 1~5000）
//   POST /map/navigate       -> data = {distance,straight_distance,duration,
//                                      path:[{lat,lng}],algorithm,start_source,target}
//   GET  /map/building/{id}  -> data = {id,name,campus,address,floor_count,
//                                      latitude,longitude,floor_plan[],services[]}
//
// ⚠️ 关键字段名差异（本模块存在的首要理由）：
//   后端 navigate 的 path 用 {lat, lng}；微信 <map> 的 polyline 用 {latitude, longitude}。
//   二者混用会静默画出一条错误路线（北纬 43.88 与东经 125.32 互换后落点完全不同），
//   因此所有坐标转换只允许走本模块，不允许在页面内手写字段名。
//
// ⚠️ 已知后端缺口（不得在前端伪造）：`poi` 表**有** `building_id` 列，
//   但 `GET /map/pois` 的 SELECT 不含该列（也不含 `description`），
//   因此接口**不返回** building_id，也就没有「POI → 建筑」的可信链路。
//   见 normalizePoi() 与 toMarkers() 的注释。
//
// ⚠️ 类型口径：所有 DB 值经 cpp_bridge / jt_db 统一按**字符串**回传
//   （db/cpp_driver/include/jt_db/types.h: Row = std::map<std::string,std::string>；
//     pybind_wrapper.cpp: d[py::str(k)] = py::str(v)）。
//   因此 id="1"、latitude="43.880000"、floor="0" 都是常态，
//   页面里任何直接比较（如 markerId === poi.id）都会静默失败 ——
//   本模块负责把字符串统一转成 Number，页面只消费归一化后的结果。
// ============================================================

// 校园默认中心（长春·前卫南区示意坐标，与 db/sql/99e_poi_seed.sql 一致）。
// 仅在「后端未返回任何可用 POI」时兜底，不作为业务数据来源。
const DEFAULT_CENTER = { latitude: 43.88, longitude: 125.32 }

// 默认缩放级别：能看清单个校园的范围（微信 map scale 取值 3~20，数值越大越近）
const DEFAULT_SCALE = 16

// 定位/导航视图使用的缩放级别（比默认更近，便于看清单点周边）
const LOCATED_SCALE = 17

/**
 * 合法坐标校验：纬度 ±90、经度 ±180，且任一轴不得为 0。
 *
 * 为什么连单轴 0 也拒绝：本项目 POI 的经纬度是长春市区（纬度 43.8x、经度 125.3x），
 * 纬度 0（赤道）或经度 0（本初子午线）在校区内都不可能成立。
 * 而 `DECIMAL NOT NULL DEFAULT 0` 意味着「字段没写」会落成 0 ——
 * 单轴 0 正是「后端漏填了一列」的典型脏值，与 (0,0) 同一性质。
 * 放行它会在几内亚湾/印尼画出一个假点位，比丢掉一行数据更糟。
 *
 * @returns {boolean}
 */
function isValidLatLng(latitude, longitude) {
  const lat = Number(latitude)
  const lng = Number(longitude)
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return false
  if (lat < -90 || lat > 90) return false
  if (lng < -180 || lng > 180) return false
  if (lat === 0 || lng === 0) return false
  return true
}

/**
 * 归一化单个 POI。
 *
 * 后端 id 可能为字符串、坐标可能为字符串型 DECIMAL（cpp_bridge 直出 DB 行），
 * 微信 <map> 的 marker.id 必须是 Number，故统一在此转换。
 *
 * @param {Object} raw 后端 POI 行
 * @returns {Object|null} 字段不可用时返回 null，由调用方过滤（不产出脏 marker）
 */
function normalizePoi(raw) {
  if (!raw || typeof raw !== 'object') return null
  const id = Number(raw.id)
  if (!Number.isFinite(id)) return null
  if (!isValidLatLng(raw.latitude, raw.longitude)) return null

  const floorNum = Number(raw.floor)
  const buildingNum = Number(raw.building_id)
  return {
    id,
    name: String(raw.name == null ? '' : raw.name),
    category: String(raw.category == null ? '' : raw.category),
    latitude: Number(raw.latitude),
    longitude: Number(raw.longitude),
    // floor 仅在有意义（> 0，室内点位）时保留，避免列表里出现「· 0 楼」
    floor: Number.isFinite(floorNum) && floorNum > 0 ? floorNum : 0,
    // nearby 接口独有；pois 接口无此字段
    distance:
      raw.distance != null && Number.isFinite(Number(raw.distance)) ? Number(raw.distance) : null,
    // 当前 GET /map/pois **不返回** building_id（SELECT 未含该列），故此值恒为 null。
    // 页面据此隐藏「建筑详情」入口；后端补出该列后无需改动本模块即可自动生效。
    // 只有 > 0 才算真实建筑：0 / NULL / 空串都表示「无关联建筑」，统一归一为 null，
    // 避免页面侧出现「guard 通过但跳转 id=0」的不一致。
    buildingId: raw.building_id != null && Number.isFinite(buildingNum) && buildingNum > 0 ? buildingNum : null,
  }
}

/**
 * 归一化列表并丢弃不可用项（保证 markers 与列表同源、且无脏点）。
 *
 * @param {Array} list
 * @returns {Array} 归一化后的 POI 数组
 */
function normalizePois(list) {
  if (!Array.isArray(list)) return []
  const out = []
  for (let i = 0; i < list.length; i++) {
    const poi = normalizePoi(list[i])
    if (poi) out.push(poi)
  }
  return out
}

// 分类筛选用的稳定顺序（依据 miniprogram/前端页面规格.md §9 的分类口径，
// 与 db/sql/99e_poi_seed.sql 的实际 category 取值一致）。
// ⚠️ 这里只定义「排序优先级」，不定义「必须存在哪些分类」——
//     chips 的实际成员一律由真实 POI 数据推导（见 toCategoryList）。
const BASE_CATS = ['教学楼', '图书馆', '食堂', '宿舍', '校医院', '体育馆', '超市', '校车点']

/**
 * 由**全量** POI 列表推导分类 chips。
 *
 * 为什么必须传全量：`GET /map/pois?category=x` 只返回该分类的点，
 * 若用筛选后的结果推导 chips，点一次分类后其余分类 chip 就会消失
 * （用户再想换分类只能先点回「全部」）。所以 chips 只在拿到全量数据时更新。
 *
 * 为什么不用固定分类表：写死的分类会产生「有 chip 但永远筛不出点」的假入口，
 * 也会漏掉后端未来新增的分类。因此 known 只做排序、extra 兜底完整性。
 *
 * @param {Array} fullList 全量 POI（category 为空的那次请求结果）
 * @returns {string[]} ['全部', ...实际存在的已知分类（按固定顺序）, ...其他分类（字典序）]
 */
function toCategoryList(fullList) {
  const pois = normalizePois(fullList)
  const present = {}
  for (let i = 0; i < pois.length; i++) {
    if (pois[i].category) present[pois[i].category] = true
  }
  const known = BASE_CATS.filter((c) => present[c])
  // 后端若新增了固定表以外的分类，按字典序补在末尾，保证这些点仍可被筛选
  const extra = Object.keys(present)
    .filter((c) => BASE_CATS.indexOf(c) === -1)
    .sort()
  return ['全部'].concat(known, extra)
}

// 分类配色（每类不同颜色 marker，依据 前端页面规格.md §9）。
// 色值取自 F10 语义色域：主色沿用 --xj-color-primary(#4A90D9)，
// 其余为同明度、同饱和倾向的低饱和色，避免地图上出现刺眼纯色块。
const CATEGORY_COLORS = {
  教学楼: '#4A90D9',
  图书馆: '#7C6BD6',
  食堂: '#E08A5F',
  宿舍: '#5AAE9B',
  校医院: '#DE6B7C',
  体育馆: '#C79A4A',
  超市: '#6B9BD1',
  校车点: '#8A9099',
}
const CATEGORY_COLOR_FALLBACK = '#4A90D9'

/** 分类 → marker 主色；未知/空分类回退主色（不抛异常、不产生 undefined 色值） */
function colorOfCategory(category) {
  const key = String(category == null ? '' : category)
  return CATEGORY_COLORS[key] || CATEGORY_COLOR_FALLBACK
}

/** 分类首字（"教学楼" → "教"）；空分类回退为定位符 */
function categoryInitial(category) {
  const key = String(category == null ? '' : category).trim()
  if (!key) return '·'
  return key.slice(0, 1)
}

/**
 * POI 列表 → 微信 <map> markers
 *
 * marker 契约（微信官方）：
 *   { id:Number, latitude, longitude, iconPath, width, height, callout:{...}, label:{...} }
 *
 * 关于图标：本仓 origin/dev 的 `static/icons/` 只有 F11 交付的位图/线性图标，
 * 没有可用于 marker 的专用图钉资源。因此这里使用**微信原生 marker 形态 +
 * label 分类着色**，而不是临时新增一套位图资源：
 * 既满足「每类不同颜色 marker」，又不引入与 F11 冲突的重复资产。
 * iconPath 留空即使用系统默认图钉。
 *
 * ⚠️ 不产出任何「建筑」字段：后端 POI 接口不返回 building_id，
 *    marker 上挂 building_id 会诱导调用方写出假入口。
 *
 * @param {Array} list 已归一化或原始 POI 列表
 * @param {Object} [options]
 * @param {Number|null} [options.selectedId] 当前选中 POI：callout 常显 + zIndex 置顶
 * @returns {Array} markers
 */
function toMarkers(list, options) {
  const opts = options || {}
  const selectedId =
    opts.selectedId != null && Number.isFinite(Number(opts.selectedId))
      ? Number(opts.selectedId)
      : null

  const pois = normalizePois(list)
  return pois.map((poi, index) => {
    const selected = selectedId !== null && poi.id === selectedId
    const color = colorOfCategory(poi.category)
    return {
      id: poi.id,
      latitude: poi.latitude,
      longitude: poi.longitude,
      // iconPath 缺省 → 系统默认图钉
      width: 24,
      height: 34,
      // 点击 marker 时 callout 自动展开（配合 bindmarkertap 打开信息卡）
      callout: {
        content: poi.name,
        color: '#1F2329',
        fontSize: 12,
        borderRadius: 8,
        borderWidth: 0,
        bgColor: '#FFFFFF',
        padding: 6,
        display: selected ? 'ALWAYS' : 'BYCLICK',
        textAlign: 'center',
        anchorY: 0,
      },
      // 分类标签：胶囊底色 + 白色首字，替代新增位图资源
      label: {
        content: categoryInitial(poi.category),
        color: '#FFFFFF',
        fontSize: 11,
        bgColor: color,
        borderColor: selected ? '#1F2329' : color,
        borderWidth: selected ? 2 : 0,
        borderRadius: 10,
        padding: 5,
        textAlign: 'center',
        anchorX: -14,
        anchorY: -46,
      },
      // 选中项最后渲染（覆盖在上层），未选中按原序
      zIndex: selected ? 100 + index : index,
    }
  })
}

/**
 * 后端导航 path → 微信 <map> polyline
 *
 * 后端：path: [{lat, lng}, ...]（services/route.py 输出，端点用真实坐标，至少 2 点）
 * 微信：polyline: [{points:[{latitude, longitude}], color, width, arrowLine, borderColor}]
 *
 * @param {Array} path 后端返回的折线点
 * @param {Object} [options] color / width 可覆盖
 * @returns {Array} polyline 数组；路径不足 2 点或全部非法时返回 []（不画半条路线）
 */
function toPolyline(path, options) {
  const opts = options || {}
  if (!Array.isArray(path)) return []

  const points = []
  for (let i = 0; i < path.length; i++) {
    const p = path[i]
    if (!p || typeof p !== 'object') continue
    // 显式映射 lat→latitude、lng→longitude（禁止透传对象，避免字段名错位）
    if (!isValidLatLng(p.lat, p.lng)) continue
    points.push({ latitude: Number(p.lat), longitude: Number(p.lng) })
  }
  if (points.length < 2) return []

  return [
    {
      points,
      color: opts.color || '#4A90D9CC',
      width: opts.width || 6,
      arrowLine: true,
      borderColor: '#FFFFFFCC',
      borderWidth: 2,
    },
  ]
}

/**
 * 距离文案：<1000m 用「米」，否则用「公里」。
 *
 * null / undefined / '' 一律返回空串：语义是「距离未知」，
 * 绝不能因为 `Number(null) === 0` 而渲染成「0 米」（那是编造出来的结论）。
 * 只有真正的数值 0 才显示「0 米」。
 */
function formatDistance(meters) {
  if (meters === null || meters === undefined || meters === '') return ''
  const m = Number(meters)
  if (!Number.isFinite(m) || m < 0) return ''
  if (m < 1000) return Math.round(m) + ' 米'
  return (m / 1000).toFixed(1) + ' 公里'
}

/**
 * 导航响应的展示视图模型（只取页面要用的字段，避免整包 setData）
 *
 * @param {Object} res POST /map/navigate 的 data
 * @returns {Object|null} path 不足 2 点时返回 null（调用方据此报错，不激活导航）
 */
function buildNavViewModel(res) {
  if (!res || typeof res !== 'object') return null
  const path = Array.isArray(res.path) ? res.path : []
  if (path.length < 2) return null
  const target = res.target && typeof res.target === 'object' ? res.target : null
  const straight = Number(res.straight_distance)
  const distance = Number(res.distance)
  const durationNum = Number(res.duration)
  return {
    distanceText: formatDistance(distance),
    // duration 缺失时返回空串，而不是渲染「0 分钟」——
    // 那和「距离未知却显示 0 米」是同一种编造（formatDistance 已为此专门处理 null）。
    // WXML 用 wx:if="{{nav.durationText}}" 控制，空串即不渲染。
    durationText: Number.isFinite(durationNum) ? Math.round(durationNum) + ' 分钟' : '',
    // 绕行增量：仅当路网距离明显大于直线距离时才提示（阈值 20m，避免噪声）
    detourText:
      Number.isFinite(straight) && Number.isFinite(distance) && distance - straight > 20
        ? '较直线多走 ' + formatDistance(distance - straight)
        : '',
    targetName: target ? String(target.name == null ? '' : target.name) : '',
    // 起点来源如实透出：nearest_poi 时并非用户真实位置，
    // UI 不得声称「已按你的位置规划」
    fromUserLocation: res.start_source === 'user_location',
    // 算法口径透出（astar-grid / straight-fallback），供页面在回退直线时如实提示
    algorithm: String(res.algorithm == null ? '' : res.algorithm),
  }
}

/** 全部 POI 的中心点（用于首屏 center）；无可用点时回退校园默认中心 */
function centerOf(list) {
  const pois = normalizePois(list)
  if (pois.length === 0) {
    return { latitude: DEFAULT_CENTER.latitude, longitude: DEFAULT_CENTER.longitude }
  }
  let sumLat = 0
  let sumLng = 0
  for (let i = 0; i < pois.length; i++) {
    sumLat += pois[i].latitude
    sumLng += pois[i].longitude
  }
  return {
    latitude: Number((sumLat / pois.length).toFixed(6)),
    longitude: Number((sumLng / pois.length).toFixed(6)),
  }
}

/**
 * 把 nearby 返回的 distance 合并进已有 POI 列表（按 id 匹配，不改原数组）。
 *
 * 为什么需要合并而不是直接用 nearby 结果：`GET /map/nearby` 只返回**半径内**
 * 的点，若直接替换列表，半径外的 POI 会从地图上消失（用户以为点位不存在）。
 * 因此这里保留原列表，只把已知的真实距离覆盖上去；未知距离保持 null。
 *
 * @param {Array} list 主列表（当前分类的 POI）
 * @param {Array} nearbyList /map/nearby 返回的 POI（含 distance）
 * @returns {Array} 新数组（distance 为 null 表示半径外/未查到，不得编造）
 */
function mergeDistance(list, nearbyList) {
  const pois = normalizePois(list)
  const near = normalizePois(nearbyList)
  const distanceById = {}
  for (let i = 0; i < near.length; i++) {
    if (near[i].distance != null) distanceById[near[i].id] = near[i].distance
  }
  return pois.map((poi) => {
    const d = distanceById[poi.id]
    return { ...poi, distance: d == null ? poi.distance : d }
  })
}

/**
 * 按真实距离升序排序（近的在前）。
 *
 * 无 distance 的项（半径外/未查到）排到末尾，**不给未知项编造距离**；
 * 距离相同时按 id 升序收尾，避免同距离顺序抖动。
 *
 * @param {Array} list 含 distance 字段的 POI 数组
 * @returns {Array} 新数组
 */
function sortByDistance(list) {
  const arr = Array.isArray(list) ? list.slice() : []
  arr.sort((a, b) => {
    const da = a && a.distance != null ? Number(a.distance) : Infinity
    const db = b && b.distance != null ? Number(b.distance) : Infinity
    if (da === db) return Number(a && a.id) - Number(b && b.id)
    return da - db
  })
  return arr
}

module.exports = {
  DEFAULT_CENTER,
  DEFAULT_SCALE,
  LOCATED_SCALE,
  BASE_CATS,
  CATEGORY_COLORS,
  isValidLatLng,
  normalizePoi,
  normalizePois,
  colorOfCategory,
  categoryInitial,
  toCategoryList,
  toMarkers,
  toPolyline,
  formatDistance,
  buildNavViewModel,
  centerOf,
  mergeDistance,
  sortByDistance,
}
