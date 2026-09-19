#!/usr/bin/env node
/**
 * F18「校园地图容器」negative control / 语义突变测试
 *
 * 目的：反向证明 `tools/verify_f18_campus_map.js` **真的会失败**。
 * 一个只会 PASS 的校验脚本等于没有校验；因此这里对**最小源码副本**注入
 * 一组已知缺陷与语义突变，逐条运行 verifier，要求：
 *   1. verifier 退出码非 0；
 *   2. 且**指定的那一条断言**确实变红（不是"碰巧别的原因挂了"）。
 *
 * 与旧版的关键区别（旧版曾整体失效）：
 *   - 旧版 verifier 在 `pendingByCat.食堂` 处因动态 chips 与 unresolved mock
 *     冲突而 TypeError 崩溃，导致其后的大量断言与 negative control
 *     **根本没执行**，却仍可能被读成"通过"。
 *   - 本脚本因此**不信任 verifier 的自我报告**：它解析 verifier 的 stdout，
 *     逐条核对 [NG] 行，并在末尾统计
 *       · 突变组数（MUTATIONS.length）
 *       · 实际执行组数（run 真跑完的数量）
 *       · 命中组数（预期断言真的变红）
 *       · 漏检组数（预期断言没红 = 校验脚本有盲区）
 *       · 污染组数（预期之外的断言也红了 = 突变不最小，结论不可靠）
 *     任一组漏检/污染/异常 → 退出码非 0。
 *
 * 用法：
 *     node tools/negative_control_f18_campus_map.js
 *     node tools/negative_control_f18_campus_map.js --filter marker   # 只跑名字含 marker 的组
 *     node tools/negative_control_f18_campus_map.js --list            # 只列组名
 * 退出码：0 = 全部突变都被指定断言抓到；1 = 有漏检/污染/异常
 */

'use strict'

const fs = require('fs')
const os = require('os')
const path = require('path')
const { spawnSync } = require('child_process')

const ROOT = path.resolve(__dirname, '..')
const MP = path.join(ROOT, 'miniprogram')
const VERIFIER = path.join(__dirname, 'verify_f18_campus_map.js')

// 允许被突变的文件（= 本任务写范围）
const MUTABLE = [
  path.join('pages', 'map', 'index.js'),
  path.join('pages', 'map', 'index.wxml'),
  path.join('pages', 'map', 'index.wxss'),
  path.join('pages', 'map', 'index.json'),
  path.join('utils', 'map.js'),
  path.join('app.json'),
]

/**
 * 只替换一次；找不到（或找到多次）就抛错——避免"突变其实没生效"的假阴性。
 *
 * 行尾归一：仓库文件在 Windows 上可能是 CRLF，而突变字面量按 LF 书写。
 * 因此先把源码归一为 LF 再匹配，写回时保持 LF
 * （小程序对行尾不敏感，且 verifier 的断言同样在归一后的文本上匹配）。
 * 若不归一，「突变目标未找到」会被误读成"校验脚本漏检"，浪费一整轮排查。
 */
function mustReplaceOnce(src, needle, replacement, label) {
  const text = src.replace(/\r\n/g, '\n')
  const idx = text.indexOf(needle)
  if (idx === -1) throw new Error(`突变目标未找到：${label} :: ${JSON.stringify(needle.slice(0, 60))}`)
  if (text.indexOf(needle, idx + 1) !== -1) {
    throw new Error(`突变目标出现多次（不唯一，无法最小化）：${label} :: ${JSON.stringify(needle.slice(0, 60))}`)
  }
  return text.slice(0, idx) + replacement + text.slice(idx + needle.length)
}

function re(src, pattern, replacement, label) {
  const re1 = new RegExp(pattern)
  if (!re1.test(src)) throw new Error(`突变正则未命中：${label} :: /${pattern}/`)
  const out = src.replace(re1, replacement)
  if (out === src) throw new Error(`突变未改变源码：${label}`)
  return out
}

// ============================================================ 突变定义 ----
//
// 每条：{ name, why, file, expect: [必须变红的断言名（子串匹配）], apply(src) }
//   - expect 用**子串**匹配，避免因断言文案微调而失效；
//   - 尽量只让一条断言变红（最小突变），否则结论不可靠（会被记为"污染"）。

const MUTATIONS = [
  // ---------------- 坐标契约：最危险的一类（静默画错路线） ----------------
  {
    name: 'marker-latlng-swap',
    why: 'marker 把 latitude/longitude 写反 —— 地图上所有点会整体落到错误位置',
    file: path.join('utils', 'map.js'),
    expect: ['marker.latitude === 源 latitude', 'marker.longitude === 源 longitude', '字符串型 id/坐标可归一化', '负例：marker.latitude 不等于源 longitude'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '      latitude: poi.latitude,\n      longitude: poi.longitude,',
        '      latitude: poi.longitude,\n      longitude: poi.latitude,',
        'marker 坐标互换'
      ),
  },
  {
    name: 'marker-pass-through-raw',
    why: 'toMarkers 直接透传后端原始对象（lat/lng 字段名）→ 微信 map 收不到坐标',
    file: path.join('utils', 'map.js'),
    expect: ['marker.latitude === 源 latitude', 'marker.longitude === 源 longitude', '字符串型 id/坐标可归一化'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '      latitude: poi.latitude,\n      longitude: poi.longitude,',
        '      latitude: poi.lng,\n      longitude: poi.lat,',
        'marker 透传原始字段'
      ),
  },
  {
    name: 'polyline-latlng-swap',
    why: 'polyline 把后端 {lat,lng} 反过来写 → 路线画到别处',
    file: path.join('utils', 'map.js'),
    expect: ['polyline 首点坐标正确', 'polyline 末点坐标正确', 'polyline 点使用 latitude 字段', 'polyline 点使用 longitude 字段', 'includePoints 使用 polyline 的点'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '    points.push({ latitude: Number(p.lat), longitude: Number(p.lng) })',
        '    points.push({ latitude: Number(p.lng), longitude: Number(p.lat) })',
        'polyline 坐标互换'
      ),
  },
  {
    name: 'polyline-passthrough',
    why: 'polyline 点直接放后端对象（字段名仍是 lat/lng）→ 微信不认',
    file: path.join('utils', 'map.js'),
    expect: ['polyline 点使用 latitude 字段', 'polyline 点使用 longitude 字段', 'polyline 首点坐标正确', 'polyline 末点坐标正确', '负例：polyline 点不含后端原始字段名 lat', 'includePoints 使用 polyline 的点'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '    points.push({ latitude: Number(p.lat), longitude: Number(p.lng) })',
        '    points.push(p)',
        'polyline 透传'
      ),
  },
  // ---------------- 脏数据过滤 ----------------
  {
    name: 'allow-zero-island',
    why: '接受 (0,0) 脏坐标 —— 后端 DECIMAL 默认值会在地图上画出一个非洲的点',
    file: path.join('utils', 'map.js'),
    expect: ['isValidLatLng 拒绝 (0,0)', 'isValidLatLng 拒绝单轴为 0 的脏值', '脏坐标/缺 id 被过滤', '含脏点的 path 只保留合法点'],
    apply: (s) => mustReplaceOnce(s, '  if (lat === 0 || lng === 0) return false\n', '', '放行 0 值坐标'),
  },
  {
    name: 'drop-id-validation',
    why: '不再校验 id —— 缺 id 的行会产出 id=NaN 的 marker，点击永远无响应',
    file: path.join('utils', 'map.js'),
    expect: ['脏坐标/缺 id 被过滤'],
    apply: (s) =>
      mustReplaceOnce(s, '  if (!Number.isFinite(id)) return null\n', '', '去掉 id 校验'),
  },
  {
    name: 'skip-range-check',
    why: '不再校验经纬度范围 —— 越界值会被当成合法坐标',
    file: path.join('utils', 'map.js'),
    expect: ['isValidLatLng 拒绝越界与 NaN', '脏坐标/缺 id 被过滤', 'isValidLatLng 拒绝 ±90/±180 之外的边界外值'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '  if (lat < -90 || lat > 90) return false\n  if (lng < -180 || lng > 180) return false\n',
        '',
        '去掉范围校验'
      ),
  },
  // ---------------- 分类 chips ----------------
  {
    name: 'hardcoded-cats',
    why: 'chips 写死固定分类表 —— 会出现"有 chip 但永远筛不出点"的假入口',
    file: path.join('utils', 'map.js'),
    expect: ['chips 不含数据中不存在的分类', '空数据 → 只有「全部」', '非数组输入不抛异常'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '  return [\'全部\'].concat(known, extra)',
        '  return [\'全部\'].concat(BASE_CATS, extra)',
        '写死分类'
      ),
  },
  {
    name: 'drop-unknown-cats',
    why: '只保留固定分类表 —— 后端新增分类的点永远无法被筛选',
    file: path.join('utils', 'map.js'),
    expect: ['后端新增的未知分类也被纳入', '未知分类排在已知分类之后'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        "  const extra = Object.keys(present)\n    .filter((c) => BASE_CATS.indexOf(c) === -1)\n    .sort()",
        '  const extra = []',
        '丢弃未知分类'
      ),
  },
  {
    name: 'cats-from-filtered',
    why: 'chips 由**筛选后**结果推导 —— 点一次分类后其余 chip 全部消失',
    file: path.join('pages', 'map', 'index.js'),
    expect: ['chips 由页面在', '分类 chips 在 nearby 后未被冲掉'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        "          cats: cat === '' ? mapUtil.toCategoryList(items) : null,",
        '          cats: mapUtil.toCategoryList(items),',
        'chips 用筛选结果推导'
      ),
  },
  {
    name: 'danger-ignore-filter',
    why: '忽略后端 category 过滤结果（把筛选请求的结果当全量用）→ 分类筛选失效',
    file: path.join('pages', 'map', 'index.js'),
    expect: ['（前置）切换分类后列表只含该分类的点', '页面内未写死 POI 列表', 'nearby 不把半径内其他分类的点塞进当前列表'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '        const items = mapUtil.normalizePois((res && res.items) || [])',
        '        const items = mapUtil.normalizePois((res && res.items) || []).concat([])\n        if (cat === \'食堂\') items.push({ id: 99, name: \'假的\', category: \'图书馆\', latitude: 43.88, longitude: 125.32, floor: 0, distance: null, buildingId: null })',
        '分类结果混入其他分类'
      ),
  },
  // ---------------- 距离 / 排序 ----------------
  {
    name: 'known-distance-zero',
    why: '距离未知时编造 0 —— 用户会看到"就在脚下"',
    file: path.join('utils', 'map.js'),
    expect: ['mergeDistance：未命中项保持 null', 'nearby 结果按距离升序', 'nearby 半径外无距离的点排在末尾', '半径外 POI 选中卡片不显示距离'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '    return { ...poi, distance: d == null ? poi.distance : d }',
        '    return { ...poi, distance: d == null ? 0 : d }',
        '未知距离编造为 0'
      ),
  },
  {
    name: 'sort-descending',
    why: 'nearby 排序反过来 —— "附近"列表把最远的排在最前',
    file: path.join('utils', 'map.js'),
    expect: ['sortByDistance：按距离升序', 'sortByDistance：无距离项排末尾', 'sortByDistance：同距离按 id 升序稳定收尾', 'nearby 结果按距离升序', 'nearby 半径外无距离的点排在末尾'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '    if (da === db) return Number(a && a.id) - Number(b && b.id)\n    return da - db',
        '    if (da === db) return Number(a && a.id) - Number(b && b.id)\n    return db - da',
        '距离降序'
      ),
  },
  {
    name: 'distance-null-text',
    why: 'formatDistance 把 null 当成 0（Number(null)===0）→ 渲染"0 米"',
    file: path.join('utils', 'map.js'),
    expect: ['formatDistance：null/undefined/空串'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        "  if (meters === null || meters === undefined || meters === '') return ''\n",
        '',
        '去掉 null 拦截'
      ),
  },
  {
    name: 'merge-shortens-list',
    why: 'nearby 直接替换列表 —— 半径外的 POI 从地图上消失',
    file: path.join('pages', 'map', 'index.js'),
    expect: ['nearby 后 markers 未被破坏', 'nearby 半径外无距离的点排在末尾', '半径内无点位 → 有提示且不破坏列表', '半径外 POI 选中卡片不显示距离'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '          const merged = mapUtil.sortByDistance(mapUtil.mergeDistance(this.data.items, inCat))',
        '          const merged = mapUtil.sortByDistance(inCat)',
        'nearby 替换列表'
      ),
  },
  // ---------------- 导航 / 起点诚实 ----------------
  {
    name: 'lie-user-location',
    why: 'start_source=nearest_poi 时仍声称"已按你的位置规划"—— 诚实性缺陷',
    file: path.join('utils', 'map.js'),
    expect: ['start_source=nearest_poi → fromUserLocation=false', 'start_source=nearest_poi 时如实标注非用户位置'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        "    fromUserLocation: res.start_source === 'user_location',",
        '    fromUserLocation: true,',
        '谎称用户位置'
      ),
  },
  {
    name: 'keep-stale-polyline',
    why: '导航失败时不清掉上一次路线 —— 卡片说失败、地图上还留着旧路线',
    file: path.join('pages', 'map', 'index.js'),
    expect: ['导航失败 → 清除上一次残留的 polyline'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '        this.setData({ nav: { active: false, loading: false }, polyline: [] })',
        '        this.setData({ nav: { active: false, loading: false } })',
        '失败不清路线'
      ),
  },
  {
    name: 'activate-without-path',
    why: 'path 缺失仍激活导航 —— 出现"导航中但地图上没有路线"',
    file: path.join('pages', 'map', 'index.js'),
    expect: ['返回缺 path → 不激活导航且给出提示'],
    apply: (s) =>
      mustReplaceOnce(s, '        if (!vm || polyline.length === 0) {', '        if (false) {', '缺 path 仍激活'),
  },
  {
    name: 'navigate-missing-from',
    why: '有定位但不传起点坐标 —— 后端只能退回"最近地标"，起点被静默降级',
    file: path.join('pages', 'map', 'index.js'),
    expect: ['有定位时 navigate 传入 from_lat/from_lng', 'navigate 起点使用 from_lat/from_lng'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '      payload.from_lat = loc.latitude\n      payload.from_lng = loc.longitude',
        '      payload.from_lat = loc.latitude',
        '只传 from_lat'
      ),
  },
  {
    name: 'navigate-not-post',
    why: 'navigate 用 GET 调用 —— 后端 405，导航永远失败',
    file: path.join('pages', 'map', 'index.js'),
    expect: ['navigate 使用 POST', '调用 POST /map/navigate 且方法为 POST'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        "    request('/map/navigate', { method: 'POST', data: payload })",
        "    request('/map/navigate', { data: payload })",
        'navigate 非 POST'
      ),
  },
  {
    name: 'navigate-wrong-field',
    why: '请求体字段名写错（poi_id 而非 to_poi_id）—— 后端 422',
    file: path.join('pages', 'map', 'index.js'),
    expect: ['navigate 传 to_poi_id', 'navigate 请求体使用 to_poi_id'],
    apply: (s) => mustReplaceOnce(s, 'const payload = { to_poi_id: poi.id }', 'const payload = { poi_id: poi.id }', '字段名写错'),
  },
  // ---------------- 竞态守卫 ----------------
  {
    name: 'drop-fetchseq',
    why: '去掉 fetchSeq 守卫 —— 过期响应会覆盖当前分类结果',
    file: path.join('pages', 'map', 'index.js'),
    expect: ['过期的分类响应被丢弃', '过期响应后到时不得覆盖已生效的新结果', '更早的过期响应同样被丢弃'],
    apply: (s) =>
      mustReplaceOnce(s, '        if (seq !== this.fetchSeq) return // 过期响应（用户已切换分类）直接丢弃\n', '', '去掉 fetchSeq 守卫'),
  },
  {
    name: 'drop-navseq',
    why: '去掉 navSeq 守卫 —— 用户已结束导航，迟到的响应又把路线画回来',
    file: path.join('pages', 'map', 'index.js'),
    expect: ['过期导航响应不激活导航', '过期导航响应不画出路线'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '        if (seq !== this.navSeq) return\n        const vm = mapUtil.buildNavViewModel(res)',
        '        const vm = mapUtil.buildNavViewModel(res)',
        '去掉 navSeq 守卫'
      ),
  },
  {
    name: 'retry-resets-cat',
    why: 'retryFetch 静默跳回「全部」—— 用户点重试后分类被悄悄改掉',
    file: path.join('pages', 'map', 'index.js'),
    expect: ['retryFetch 保持当前分类'],
    apply: (s) =>
      mustReplaceOnce(s, '    this.fetch(this.currentCategory())', "    this.fetch('')", 'retry 重置分类'),
  },
  // ---------------- 定位诚实性 ----------------
  {
    name: 'fake-location-on-deny',
    why: '定位被拒时偷偷把地图移到硬编码坐标 —— 假装定位成功',
    file: path.join('pages', 'map', 'index.js'),
    expect: ['拒绝授权 → 不移动地图'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        "        done(null)\n      },\n    })",
        "        this.setData({ center: { latitude: 43.88, longitude: 125.32 } })\n        done(null)\n      },\n    })",
        '拒绝时移动地图'
      ),
  },
  {
    name: 'drop-locating-guard',
    why: '去掉 locating 防重入 —— 连点会发起多次定位',
    file: path.join('pages', 'map', 'index.js'),
    expect: ['定位进行中连点只发起 1 次定位请求'],
    apply: (s) =>
      mustReplaceOnce(s, '    if (this.data.locating) return // 防连点\n', '', '去掉防重入'),
  },
  {
    name: 'wrong-coord-system',
    why: '定位坐标系写成 wgs84 —— 与微信地图 gcj02 不一致，整体偏移数百米',
    file: path.join('pages', 'map', 'index.js'),
    expect: ['使用 wx.getLocation 且坐标系为 gcj02'],
    apply: (s) => mustReplaceOnce(s, "      type: 'gcj02',", "      type: 'wgs84',", '坐标系写错'),
  },
  {
    name: 'nearby-without-location',
    why: '没有定位也硬发 nearby 请求（编造坐标）',
    file: path.join('pages', 'map', 'index.js'),
    expect: ['无定位 → 不发起 /map/nearby 请求'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '    if (this.data.locating) return\n    const seq = ++this.nearbySeq\n    this.withLocation((loc) => {\n      if (!loc) return\n',
        '    if (this.data.locating) return\n    const seq = ++this.nearbySeq\n    this.withLocation((loc) => {\n      loc = loc || { latitude: 43.88, longitude: 125.32 }\n',
        '无定位硬发 nearby'
      ),
  },
  // ---------------- 建筑入口诚实性 ----------------
  {
    name: 'fake-building-id',
    why: '给每个 marker 伪挂 building_id=1 —— 伪造出接口并不返回的字段',
    file: path.join('utils', 'map.js'),
    expect: ['normalizePoi：后端不返回 building_id 时 buildingId 为 null', 'POI 无 building_id → 选中卡片 buildingId 为空', '无 buildingId 时点击建筑详情不跳转', '选中建筑后跳真实详情页'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '    buildingId: raw.building_id != null && Number.isFinite(buildingNum) && buildingNum > 0 ? buildingNum : null,',
        '    buildingId: raw.building_id != null && Number.isFinite(buildingNum) && buildingNum > 0 ? buildingNum : 1,',
        '伪造 building_id'
      ),
  },
  {
    name: 'always-show-building-btn',
    why: '建筑详情按钮不受 buildingId 守卫 —— 出现永远点不动的假入口',
    file: path.join('pages', 'map', 'index.wxml'),
    expect: ['WXML 中建筑详情按钮受 buildingId 守卫'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '<view wx:if="{{selectedPoi.buildingId}}" class="act act--ghost"',
        '<view class="act act--ghost"',
        '去掉建筑按钮守卫'
      ),
  },
  // ---------------- 安全区 / 胶囊 ----------------
  {
    name: 'no-capsule-reserve',
    why: '不预留胶囊宽度 —— 右上角「定位」按钮被微信胶囊盖住、点不动',
    file: path.join('pages', 'map', 'index.wxml'),
    expect: ['导航为胶囊按钮预留宽度', '预留宽度被注入到导航内层'],
    apply: (s) =>
      mustReplaceOnce(s, ' style="padding-right: {{capsuleReserve}}px"', '', '去掉胶囊预留'),
  },
  {
    name: 'no-safe-area',
    why: '信息卡不避让底部安全区 —— iPhone home indicator 压住操作按钮',
    file: path.join('pages', 'map', 'index.wxss'),
    expect: ['信息卡位置真实消费底部安全区'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '  bottom: calc(var(--xj-space-md, 24rpx) + var(--map-safe-bottom, 0px));',
        '  bottom: var(--xj-space-md, 24rpx);',
        '去掉底部安全区'
      ),
  },
  // ---------------- 容器尺寸 ----------------
  {
    name: 'shrink-map-rpx',
    why: '地图容器用固定 rpx 高度 —— 小于需求要求的 60% 屏',
    file: path.join('pages', 'map', 'index.wxss'),
    expect: ['≥ 60% 视口', '.map-root 未用固定 rpx 高度压缩地图'],
    apply: (s) =>
      mustReplaceOnce(s, '  height: 100vh;\n  overflow: hidden;', '  height: 600rpx;\n  overflow: hidden;', '地图高度被压缩'),
  },
  {
    name: 'nav-not-overlay',
    why: '顶部导航改为普通流式块 —— 会把地图往下挤，容器不再是全屏',
    file: path.join('pages', 'map', 'index.wxss'),
    expect: ['.nav-bar 为浮层'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '.nav-bar {\n  position: absolute;',
        '.nav-bar {\n  position: relative;',
        '导航不再浮层'
      ),
  },
  // ---------------- 加载 / 空 / 错误态 ----------------
  {
    name: 'no-retry-entry',
    why: '失败态没有重试入口 —— 用户只能退出页面重进',
    file: path.join('pages', 'map', 'index.wxml'),
    expect: ['失败态分支内部有自己的重试入口'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '<view class="overlay-retry" hover-class="is-pressed" bindtap="retryFetch">重试</view>',
        '',
        '去掉重试入口'
      ),
  },
  {
    name: 'error-clears-to-empty',
    why: '失败时把 error 与 empty 混在一起 —— 分不清"没数据"和"加载失败"',
    file: path.join('pages', 'map', 'index.js'),
    expect: ['接口失败 → error 非空'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        "          error: '加载失败',",
        "          error: '',",
        '失败伪装成空数据'
      ),
  },
  {
    name: 'keep-stale-markers-on-error',
    why: '加载失败仍留着上一次的 markers —— 用户以为数据是新的',
    file: path.join('pages', 'map', 'index.js'),
    expect: ['失败态清空 markers'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        "          markers: [],\n          polyline: [],\n          empty: false,",
        "          polyline: [],\n          empty: false,",
        '失败残留 markers'
      ),
  },
  // ---------------- 无障碍 / 结构 ----------------
  {
    name: 'drop-map-bindings',
    why: 'map 不再绑定 polyline —— 导航路线永远画不出来',
    file: path.join('pages', 'map', 'index.wxml'),
    expect: ['map 组件绑定 markers 与 polyline'],
    apply: (s) => mustReplaceOnce(s, '    polyline="{{polyline}}"\n', '', '去掉 polyline 绑定'),
  },
  {
    name: 'broken-handler-name',
    why: 'WXML 绑定的事件处理函数在 JS 中不存在 —— 点击无反应',
    file: path.join('pages', 'map', 'index.wxml'),
    expect: ['事件处理函数均在页面 JS 中实现', '存在定位按钮'],
    apply: (s) => mustReplaceOnce(s, 'bindtap="onLocate"', 'bindtap="onLocateTypo"', '改坏事件名'),
  },
  {
    name: 'unclosed-tag',
    why: 'WXML 标签未闭合 —— 开发者工具直接编译失败/整屏白',
    file: path.join('pages', 'map', 'index.wxml'),
    expect: ['WXML 标签全部配对闭合'],
    apply: (s) => mustReplaceOnce(s, '<view class="glyph-locate"></view>', '<view class="glyph-locate">', '去掉闭合标签'),
  },
  {
    name: 'missing-style-class',
    why: 'WXML 用了 WXSS 里不存在的 class —— 浮层没有定位样式，布局塌掉',
    file: path.join('pages', 'map', 'index.wxss'),
    expect: ['关键样式类均在 WXSS 中定义', '.poi-card 为浮层', '浮层使用 z-index 叠在地图之上', '信息卡位置真实消费底部安全区'],
    apply: (s) => mustReplaceOnce(s, '.poi-card {', '.poi-card-renamed {', '样式类失配'),
  },
  // ---------------- 配置 ----------------
  {
    name: 'drop-custom-nav',
    why: '未开启自定义导航 —— 顶部自绘导航与原生导航栏重叠',
    file: path.join('pages', 'map', 'index.json'),
    expect: ['navigationStyle: custom'],
    apply: (s) => mustReplaceOnce(s, '"navigationStyle": "custom",', '"navigationStyle": "default",', '导航样式未自定义'),
  },
  {
    name: 'drop-disable-scroll',
    why: '未禁用页面滚动 —— 地图拖动手势与页面滚动冲突',
    file: path.join('pages', 'map', 'index.json'),
    expect: ['禁用页面级滚动'],
    apply: (s) => mustReplaceOnce(s, '"disableScroll": true', '"disableScroll": false', '未禁用滚动'),
  },
  {
    name: 'drop-required-private-infos',
    why: 'app.json 缺 requiredPrivateInfos —— wx.getLocation 在正式版直接失败',
    file: path.join('app.json'),
    expect: ['requiredPrivateInfos 含 getLocation', 'app.json 权限声明为最小改动'],
    apply: (s) => mustReplaceOnce(s, '  "requiredPrivateInfos": ["getLocation"],\n', '', '去掉定位接口声明'),
  },
  {
    name: 'drop-location-permission-desc',
    why: 'app.json 缺 scope.userLocation 用途说明 —— 授权弹窗无法解释用途',
    file: path.join('app.json'),
    expect: ['scope.userLocation 用途说明', 'app.json 权限 desc 长度符合官方约束'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '  "permission": {\n    "scope.userLocation": {\n      "desc": "用于地图定位、查找周边地点与步行导航"\n    }\n  },\n',
        '',
        '去掉权限用途说明'
      ),
  },
  {
    name: 'register-as-tabbar',
    why: '把地图页注册为 TabBar 页 —— 底部 TabBar 遮挡信息卡',
    file: path.join('app.json'),
    expect: ['未被注册为 TabBar 页', '本页未新增 tabBar 配置'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        '{ "pagePath": "pages/user/user", "text": "我的" }',
        '{ "pagePath": "pages/user/user", "text": "我的" },\n      { "pagePath": "pages/map/index", "text": "地图" }',
        '注册为 TabBar 页'
      ),
  },
  // ---------------- 接口接入诚实性 ----------------
  {
    name: 'mock-poi-data',
    why: '页面内置假 POI 兜底数据 —— 接口挂了也"看起来正常"',
    file: path.join('pages', 'map', 'index.js'),
    expect: ['未使用本地 mock/兜底假数据', '页面内未写死 POI 列表'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        'const INITIAL_CATS = [\'全部\']',
        "const MOCK_POI = [{ name: '假地点' }]\nconst INITIAL_CATS = ['全部']",
        '内置假数据'
      ),
  },
  {
    name: 'hardcoded-host',
    why: '页面硬编码后端域名 —— 绕过 config/env 的地址治理（release 会打到本机）',
    file: path.join('pages', 'map', 'index.js'),
    expect: ['无硬编码 host'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        "const { request } = require('../../services/request')",
        "const { request } = require('../../services/request')\nconst HOST = 'http://127.0.0.1:8000/api/v1'",
        '硬编码 host'
      ),
  },
  {
    name: 'add-image-asset',
    why: 'marker 改用新增图片素材 —— 引入 F11 之外的重复资产依赖',
    file: path.join('utils', 'map.js'),
    expect: ['marker 不设置 iconPath'],
    // 注入点必须在 toMarkers **函数体内**：verifier 只扫描该函数体，
    // 若注到函数上方的注释里，verifier 根本看不见，会被误判成「校验脚本漏检」。
    apply: (s) =>
      mustReplaceOnce(
        s,
        '      width: 24,\n      height: 34,',
        "      iconPath: '/static/icons/pin.png',\n      width: 24,\n      height: 34,",
        '引入图片素材'
      ),
  },
  {
    name: 'third-party-map-sdk',
    why: '引入第三方地图 SDK —— 绕过自研 /map/navigate 契约',
    file: path.join('pages', 'map', 'index.js'),
    expect: ['未引入第三方地图 SDK'],
    apply: (s) =>
      mustReplaceOnce(
        s,
        "const { request } = require('../../services/request')",
        "const { request } = require('../../services/request')\nconst amap = require('../../utils/amap-sdk')",
        '引入第三方 SDK'
      ),
  },
]

// ============================================================ 运行框架 ----

function buildCopy() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'xjt-f18-nc-'))
  // 需要 miniprogram 全量（回归断言会读 app.wxss / custom-tab-bar 等）
  fs.cpSync(MP, path.join(dir, 'miniprogram'), { recursive: true })
  return dir
}

/** 解析 verifier stdout 中的 [NG] 行，返回失败断言名数组 */
function parseNg(stdout) {
  const names = []
  stdout.split(/\r?\n/).forEach((line) => {
    const m = /^\s*\[NG\]\s+(.*)$/.exec(line)
    if (m) {
      // 去掉 "  <- detail" 尾巴
      names.push(m[1].split(/\s+<-/)[0].trim())
    }
  })
  return names
}

/**
 * verifier 是否**跑到了结尾**。
 *
 * 两个条件缺一不可：
 *   ① 打印了汇总行（脚本执行到最后一节的标志）；
 *   ② 汇总行里没有 `[未跑完]` 标记（脚本在中途抛异常后仍会打印汇总，
 *      但会明确标注未跑完 —— 否则崩溃前打印的 [NG] 行会被误判成 HIT）。
 */
function ranToCompletion(stdout) {
  return (
    /断言统计：共 \d+ 项（通过 \d+ \/ 失败 \d+ \/ 异常 \d+）/.test(stdout) &&
    !/\[未跑完\]/.test(stdout)
  )
}

/**
 * 运行 verifier 子进程并取回退出码与输出。
 *
 * ⚠️ 刻意**不用 `encoding: 'utf8'` 的管道捕获**：在受限沙箱（Harness
 * file sandbox / 只读或 workspace-write 模式）下，Node 的 `child_process`
 * 默认 `stdio: 'pipe'` 会因无法创建管道而 `EPERM`，
 * 表现为 `status === null` + `res.error.code === 'EPERM'`，
 * 极易被误读成「verifier 崩溃」。
 * 这里改为把 stdout/stderr 重定向到**普通文件**，再读回来。
 */
function runVerifier(copyRoot, outDir) {
  const outFile = path.join(outDir, 'stdout.txt')
  const errFile = path.join(outDir, 'stderr.txt')
  const outFd = fs.openSync(outFile, 'w')
  const errFd = fs.openSync(errFile, 'w')
  let res
  try {
    res = spawnSync(process.execPath, [VERIFIER], {
      cwd: ROOT,
      env: Object.assign({}, process.env, { MAP_SRC_OVERRIDE: path.join(copyRoot, 'miniprogram') }),
      stdio: ['ignore', outFd, errFd],
    })
  } finally {
    fs.closeSync(outFd)
    fs.closeSync(errFd)
  }
  return {
    status: res.status,
    signal: res.signal,
    // spawnSync 自身失败（EPERM/ENOENT…）时 status 为 null 且 res.error 有值 ——
    // 必须显式暴露，否则会被误读成「verifier 挂了」而不是「子进程根本没起来」
    error: res.error ? `${res.error.code || ''} ${res.error.message || res.error}`.trim() : '',
    stdout: fs.readFileSync(outFile, 'utf8'),
    stderr: fs.readFileSync(errFile, 'utf8'),
  }
}

// ---------------------------------------------------------------- 主流程 ----

const argv = process.argv.slice(2)
if (argv.indexOf('--list') !== -1) {
  MUTATIONS.forEach((m, i) => console.log(`${String(i + 1).padStart(2)}. ${m.name}  → 期望失败：${m.expect.join(' | ')}`))
  console.log(`\n共 ${MUTATIONS.length} 组突变`)
  process.exit(0)
}

const filterIdx = argv.indexOf('--filter')
const filter = filterIdx !== -1 ? argv[filterIdx + 1] : null
const selected = filter ? MUTATIONS.filter((m) => m.name.indexOf(filter) !== -1) : MUTATIONS

// 过滤条件匹配不到任何突变时**必须失败**：
// 否则 `--filter zzz` 会打印「0/0 组全部被抓到」并 exit 0 —— 一个静默的假绿，
// 在排查 verifier 是否失效时尤其危险。
if (selected.length === 0) {
  console.error(`[FAIL] --filter "${filter}" 没有匹配到任何突变组（不构成一次有效运行）`)
  console.error('       可用组名见：node tools/negative_control_f18_campus_map.js --list')
  process.exit(1)
}

/** 基线运行：未突变的副本必须让 verifier 全绿，否则后面所有结论都不可信 */
function runBaseline() {
  const dir = buildCopy()
  try {
    const res = runVerifier(dir, dir)
    if (res.error) return { ok: false, why: `无法启动 verifier：${res.error}` }
    if (!ranToCompletion(res.stdout)) {
      return { ok: false, why: 'verifier 未跑完（无汇总行）—— 基线即红，突变结论不可信' }
    }
    if (res.status !== 0) {
      const ng = parseNg(res.stdout)
      return { ok: false, why: `未突变的副本 verifier 仍失败：${ng.slice(0, 5).join(' | ') || '见 stdout'}` }
    }
    return { ok: true }
  } finally {
    try {
      fs.rmSync(dir, { recursive: true, force: true })
    } catch (e) {
      /* 忽略 */
    }
  }
}

const copies = []
let executed = 0
let hit = 0
const missed = []
const polluted = []
const crashed = []
const incomplete = []

console.log(`F18 negative control：共 ${selected.length} 组突变${filter ? `（filter=${filter}）` : ''}`)
console.log('每组都要求 verifier 退出码非 0、**跑完整个脚本**，且**指定断言**确实变红。\n')

// 先跑基线：没有它，一次中途的文件改动或本来就红的仓库状态
// 会把所有组都变成 MISS/污染，而报告仍"看起来很详细"。
const baseline = runBaseline()
console.log(
  baseline.ok
    ? '  [BASE] 基线（未突变副本）verifier 全绿 —— 后续突变结论有效\n'
    : `  [BASE] 基线失败：${baseline.why}\n`
)

for (const mut of selected) {
  const label = mut.name
  let copy = null
  try {
    copy = buildCopy()
    copies.push(copy)

    // 应用突变（必须真正改变文件内容）
    const target = path.join(copy, 'miniprogram', mut.file)
    const before = fs.readFileSync(target, 'utf8')
    const after = mut.apply(before)
    if (after === before) throw new Error('突变未改变文件内容')
    fs.writeFileSync(target, after, 'utf8')

    const res = runVerifier(copy, copy)
    executed += 1

    if (res.error) {
      crashed.push(`${label}：无法启动 verifier 子进程 —— ${res.error}`)
      console.log(`  [ERR ] ${label} —— 无法启动 verifier 子进程：${res.error}`)
      continue
    }
    if (res.status === null || res.status > 1) {
      crashed.push(
        `${label}：verifier 异常退出 status=${res.status} signal=${res.signal || '-'}\n${(res.stderr || '').slice(0, 600)}`
      )
      console.log(`  [ERR ] ${label} —— verifier 异常退出 status=${res.status}`)
      continue
    }
    // 必须跑完：只看 [NG] 会把"打挂了静态断言随后崩溃"误判成 HIT
    if (!ranToCompletion(res.stdout)) {
      const tail = (res.stderr || '').split(/\r?\n/).filter(Boolean).slice(-6).join(' / ')
      incomplete.push(
        `${label}：verifier 未跑完（无汇总行），[NG]=${JSON.stringify(parseNg(res.stdout))}` +
          (tail ? `\n        stderr: ${tail.slice(0, 400)}` : '')
      )
      console.log(`  [ERR ] ${label} —— verifier 未跑完（断言未执行到最后）`)
      if (tail) console.log(`         stderr: ${tail.slice(0, 240)}`)
      continue
    }
    if (res.status === 0) {
      crashed.push(`${label}：verifier 仍然退出 0（突变未被察觉）`)
      console.log(`  [MISS] ${label} —— verifier 仍 PASS`)
      continue
    }

    const ng = parseNg(res.stdout)
    const hitAll = mut.expect.every((e) => ng.some((n) => n.indexOf(e) !== -1))
    const expectedMatched = ng.filter((n) => mut.expect.some((e) => n.indexOf(e) !== -1))
    const extra = ng.filter((n) => mut.expect.every((e) => n.indexOf(e) === -1))

    if (!hitAll) {
      missed.push(`${label}：期望 [${mut.expect.join(' | ')}] 未变红；实际失败 = ${JSON.stringify(ng)}`)
      console.log(`  [MISS] ${label}`)
      console.log(`         期望变红：${mut.expect.join(' | ')}`)
      console.log(`         实际变红：${ng.join(' | ') || '(无)'}`)
      continue
    }

    hit += 1
    if (extra.length > 0) {
      // 突变不够最小 → 结论不可靠，但已确认目标断言有效；如实记录并计入污染
      polluted.push(`${label}：额外变红 = ${JSON.stringify(extra)}`)
      console.log(`  [HIT+] ${label} —— 命中 ${expectedMatched.length} 条，但有 ${extra.length} 条额外变红`)
      console.log(`         额外：${extra.join(' | ')}`)
    } else {
      console.log(`  [HIT] ${label} —— 命中「${mut.expect.join(' | ')}」`)
    }
  } catch (e) {
    crashed.push(`${label}：注入/执行异常 ${e && e.message ? e.message : e}`)
    console.log(`  [ERR ] ${label} —— ${e && e.message ? e.message : e}`)
  } finally {
    if (copy) {
      try {
        fs.rmSync(copy, { recursive: true, force: true })
      } catch (e) {
        /* 忽略 */
      }
    }
  }
}

// ---------------------------------------------------------------- 汇总 ----

console.log('\n' + '─'.repeat(60))
console.log('negative control 统计：')
console.log(`  突变组数（定义）      : ${MUTATIONS.length}`)
console.log(`  本轮选中组数          : ${selected.length}`)
console.log(`  实际执行组数          : ${executed}`)
console.log(`  命中组数（指定断言红）: ${hit}`)
console.log(`  漏检组数              : ${missed.length}`)
console.log(`  污染组数（非最小突变）: ${polluted.length}`)
console.log(`  未跑完组数            : ${incomplete.length}`)
console.log(`  异常组数              : ${crashed.length}`)
console.log(`  未执行组数            : ${selected.length - executed}`)
console.log(`  基线（未突变副本）    : ${baseline.ok ? '全绿' : '失败 → ' + baseline.why}`)

if (missed.length) {
  console.log('\n漏检明细：')
  missed.forEach((m) => console.log('  - ' + m))
}
if (polluted.length) {
  console.log('\n污染明细（突变不够最小，结论仅供参考）：')
  polluted.forEach((m) => console.log('  - ' + m))
}
if (incomplete.length) {
  console.log('\n未跑完明细（断言未执行到最后，禁止判定为 HIT）：')
  incomplete.forEach((m) => console.log('  - ' + m))
}
if (crashed.length) {
  console.log('\n异常明细：')
  crashed.forEach((m) => console.log('  - ' + m))
}

const allSelectedExecuted = executed === selected.length
const ok =
  baseline.ok &&
  allSelectedExecuted &&
  missed.length === 0 &&
  crashed.length === 0 &&
  polluted.length === 0 &&
  incomplete.length === 0

if (ok) {
  console.log(`\n[PASS] F18 negative control：${hit}/${selected.length} 组语义突变全部被**指定断言**抓到`)
  console.log('（基线全绿 + 每组 verifier 均跑完 + 无漏检/污染/异常）')
  process.exit(0)
}
console.log(
  `\n[FAIL] F18 negative control：命中 ${hit}/${selected.length}，漏检 ${missed.length}，污染 ${polluted.length}，` +
    `未跑完 ${incomplete.length}，异常 ${crashed.length}，基线 ${baseline.ok ? 'ok' : 'FAIL'}`
)
process.exit(1)
