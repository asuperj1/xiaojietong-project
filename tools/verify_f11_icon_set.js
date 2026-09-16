#!/usr/bin/env node
/**
 * 验证 F11「苹果线性图标集」的资产与接入（二阶段任务单 §3.3 F11）
 *
 * 背景：`miniprogram/static/icons/` 原有 10 个 192×192 **彩色插画 PNG**（首页/服务页宫格用），
 * 与 F10 起的「苹果风 + 液态玻璃」规范（图标统一线宽 1.5、圆角端点、**禁用粗重填充**）冲突。
 * F11 要求把成员4 交付的线性 SVG 图标集接入 `miniprogram/static/icons/`，
 * 并**补齐缺失的 `user-solid.svg`**，验收标准是**全站无彩色 / 粗重图标**。
 *
 * 本脚本静态校验四件事（无需微信开发者工具、无需网络）：
 *   A. 每个 SVG 都符合 F11 线性样式契约（viewBox 24、currentColor、线宽 1.5、圆角、fill:none、
 *      无外链、无硬编码颜色）；
 *   B. 页面引用的每个图标都真实存在（防「改了路径忘了加文件」）；
 *   C. 全站不再引用 `static/icons/*.png`（彩色插画），且 PNG 资产**未被删除**；
 *   D. 首页 / 服务页宫格的**业务行为未变**（条目名 / 跳转 url / tab 标志逐条比对）——
 *      F11 只换图标资产，不改业务。
 *
 * 另有 E 段 **emoji 字形盘点**：只统计、不断言。F11 验收口径（「全站无彩色 / 粗重图标」）
 * 只覆盖**图标资产**，emoji 字形的口径与归属见 `miniprogram/static/icons/README.md` §5；
 * E 段存在的意义是让该口径**可复现**，而不是让 emoji 参与 PASS/FAIL。
 *
 * 用法：
 *     node tools/verify_f11_icon_set.js
 * 退出码：0 = 全部通过；1 = 有失败
 * 输出：A~D 段是断言（决定退出码）；E 段是 `[INFO]` 统计（不影响退出码）。
 */

'use strict'

const fs = require('fs')
const path = require('path')

const ROOT = path.resolve(__dirname, '..')
const MP = path.join(ROOT, 'miniprogram')
const ICON_DIR = path.join(MP, 'static', 'icons')

let passed = 0
const failures = []

function check(name, ok, detail) {
  if (ok) {
    passed += 1
    console.log(`  [OK] ${name}`)
  } else {
    failures.push(name + (detail ? `  <- ${detail}` : ''))
    console.log(`  [NG] ${name}${detail ? '  <- ' + detail : ''}`)
  }
}

function walk(dir, filter, out = []) {
  if (!fs.existsSync(dir)) return out
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) walk(full, filter, out)
    else if (filter(full)) out.push(full)
  }
  return out
}

// ---------------------------------------------------------------- A. 样式契约

const SVG_CONTRACT = [
  ['viewBox="0 0 24 24"', /viewBox="0 0 24 24"/],
  ['stroke="currentColor"', /stroke="currentColor"/],
  ['stroke-width="1.5"', /stroke-width="1\.5"/],
  ['stroke-linecap="round"', /stroke-linecap="round"/],
  ['stroke-linejoin="round"', /stroke-linejoin="round"/],
  ['fill="none"', /fill="none"/],
]

// 硬编码颜色：hex / rgb() / hsl() / CSS 颜色关键字
const COLOR_RE = /#[0-9a-fA-F]{3,8}\b|\brgba?\s*\(|\bhsla?\s*\(|\b(?:red|blue|green|black|white|gray|grey|orange|yellow|purple|pink)\b/

console.log('\nA. 图标资产样式契约（F11：苹果线性 / stroke-width 1.5 / fill:none）')

const svgFiles = fs.existsSync(ICON_DIR)
  ? fs.readdirSync(ICON_DIR).filter((f) => f.endsWith('.svg')).sort()
  : []

check('miniprogram/static/icons/ 存在且含 SVG', svgFiles.length > 0, `找到 ${svgFiles.length} 个`)

// F11 验收要求「`static/icons/*.svg` 数量正确」—— 数量真源是 README §2 的清单表，
// 不是脚本里的魔数。锁「README 清单 == 磁盘实际」：漏交会失败，**多交一个野图标也会失败**
// （新增图标必须同步 README，否则口径与资产会悄悄漂移）。
{
  const readmePath = path.join(ICON_DIR, 'README.md')
  let listed = []
  const lines = fs.existsSync(readmePath)
    ? fs.readFileSync(readmePath, 'utf8').split(/\r?\n/)
    : []
  const start = lines.findIndex((l) => /^##\s*2\..*清单/.test(l))
  if (start !== -1) {
    for (let i = start + 1; i < lines.length && !/^##\s/.test(lines[i]); i += 1) {
      const m = /^\|\s*`([^`]+\.svg)`\s*\|/.exec(lines[i])
      if (m) listed.push(m[1])
    }
  }
  listed = listed.sort()
  const onDisk = svgFiles.slice()
  check(
    `README §2 图标清单与磁盘一致（数量 = ${listed.length}，F11 验收口径）`,
    listed.length > 0 && listed.join('|') === onDisk.join('|'),
    `README=[${listed.join(', ')}] 磁盘=[${onDisk.join(', ')}]`
  )
}

for (const file of svgFiles) {
  const raw = fs.readFileSync(path.join(ICON_DIR, file), 'utf8')
  const missing = SVG_CONTRACT.filter(([, re]) => !re.test(raw)).map(([label]) => label)
  check(`${file} 符合线性样式契约`, missing.length === 0, missing.length ? `缺 ${missing.join(' / ')}` : '')

  // 颜色检查前先剥掉 path 的 d 属性：路径数据里可能偶然出现颜色单词，避免误报
  const withoutPathData = raw.replace(/\sd="[^"]*"/g, '')
  check(
    `${file} 无硬编码颜色`,
    !COLOR_RE.test(withoutPathData),
    (withoutPathData.match(COLOR_RE) || []).join(',')
  )
  check(`${file} 无外链 / CDN 引用`, !/href=|<image|url\(|@import|<script/i.test(raw))

  // 官方 image 组件对 SVG 的两条限制（见 static/icons/README.md §1）
  check(`${file} 无 <style> 元素（官方 SVG 限制）`, !/<style/i.test(raw))
  check(`${file} 无百分比单位（官方 SVG 限制）`, !/%/.test(raw))

  // 路径数据非空且以 moveto 起笔（防截断 / 空图标）
  const d = ((raw.match(/\sd="([^"]*)"/) || [])[1] || '').trim()
  check(`${file} 路径数据非空且以 moveto 起笔`, /^[Mm]/.test(d) && d.length > 8, `d="${d.slice(0, 24)}"`)
}

check('user-solid.svg 已补齐（F11 明确要求）', svgFiles.includes('user-solid.svg'))

// 评审（PR #77）疑问：「user-solid.svg 与 user.svg 逐字节相同、且零引用」。
// 处置：文件名 `user-solid.svg` 是任务单 §3.3 F11 **点名要求的产出**，不能改名、不能删除；
// 内容与 `user.svg` 同形是**刻意**的 —— 纯线性体系里选中态由颜色表达、不由填充表达（README §4.2）。
// 因此这里把「两者等值」**锁定为断言**：一旦有人只改其中一个，本脚本立刻报错，
// 防止两个文件在后续 F12~F17 中悄悄漂移成不同形态。
const solidPath = path.join(ICON_DIR, 'user-solid.svg')
const plainPath = path.join(ICON_DIR, 'user.svg')
const missingOf = [solidPath, plainPath].filter((p) => !fs.existsSync(p)).map((p) => path.basename(p))
const solidBuf = missingOf.length ? Buffer.alloc(0) : fs.readFileSync(solidPath)
const plainBuf = missingOf.length ? Buffer.alloc(0) : fs.readFileSync(plainPath)
check(
  'user-solid.svg 与 user.svg 逐字节等值（刻意的线性同形槽位，锁死防漂移）',
  missingOf.length === 0 && solidBuf.equals(plainBuf),
  missingOf.length
    ? `文件缺失：${missingOf.join(' / ')}`
    : `user-solid=${solidBuf.length}B / user=${plainBuf.length}B，内容已不一致`
)

// ------------------------------------------------- B. 引用完整性（全 miniprogram）

console.log('\nB. 页面引用的图标均真实存在')

const sourceFiles = walk(MP, (f) => /\.(js|wxml|wxss|json)$/.test(f))
const refRe = /\/static\/icons\/([A-Za-z0-9._-]+\.(?:svg|png))/g
const referenced = new Map() // 文件名 -> 引用处集合

for (const file of sourceFiles) {
  const raw = fs.readFileSync(file, 'utf8')
  let m
  while ((m = refRe.exec(raw))) {
    const rel = path.relative(ROOT, file).replace(/\\/g, '/')
    if (!referenced.has(m[1])) referenced.set(m[1], new Set())
    referenced.get(m[1]).add(rel)
  }
}

check('至少存在一处图标引用（防误删全部引用）', referenced.size > 0, `${referenced.size} 个`)

for (const [name, where] of [...referenced.entries()].sort()) {
  check(
    `引用存在：${name}`,
    fs.existsSync(path.join(ICON_DIR, name)),
    `被 ${[...where].join(', ')} 引用但文件缺失`
  )
}

// ------------------------------------------------- C. 全站无彩色 PNG 图标

console.log('\nC. 全站无彩色 / 粗重图标（验收标准）')

const pngRefs = [...referenced.entries()].filter(([name]) => name.endsWith('.png'))
check(
  '无任何页面引用 static/icons/*.png',
  pngRefs.length === 0,
  pngRefs.map(([n, w]) => `${n} <- ${[...w].join(',')}`).join('; ')
)

// 旧 PNG 资产必须保留（任务约束：不要为完成 F11 删除既有 PNG）
const legacyPngs = [
  'ai-assistant.png', 'food.png', 'forum.png', 'free-room.png', 'job.png',
  'library.png', 'map.png', 'notice.png', 'secondhand.png', 'task.png',
]
const missingPng = legacyPngs.filter((p) => !fs.existsSync(path.join(ICON_DIR, p)))
check('原有 10 个 PNG 资产未被删除', missingPng.length === 0, missingPng.join(', '))

// 官方 image 组件注意：svg 且 mode=scaleToFill 时 WebView 会居中 → 宫格统一用 aspectFit
for (const rel of ['pages/index/index.wxml', 'pages/service/service.wxml']) {
  const raw = fs.readFileSync(path.join(MP, rel), 'utf8')
  const tag = (raw.match(/<image[^>]*class="grid-icon"[^>]*>/) || [])[0]
  check(
    `${rel} 宫格 <image> 使用 mode="aspectFit"`,
    !!tag && /mode="aspectFit"/.test(tag),
    tag || '未找到 <image class="grid-icon">'
  )
}

// ------------------------------------------------- D. 业务行为未变（回归守卫）

console.log('\nD. 宫格业务行为未变（只换图标，不改业务）')

const EXPECTED = {
  'pages/index/index.js': {
    label: '首页 10 宫格',
    items: [
      [1, 'AI 助手', '/pages/chat/chat', true],
      [2, '图书馆预约', '/pages/library/index', false],
      [3, '查空教室', '/pages/library/freeRoom', false],
      [4, '二手集市', '/pages/secondhand/index', false],
      [5, '兼职实习', '/pages/job/index', false],
      [6, '校园地图', '/pages/map/index', false],
      [7, '外卖点餐', '/pages/life/index', false],
      [8, '通知公告', '/pages/life/notices', false],
      [9, '校园论坛', '/pages/forum/forum', true],
      [10, '任务中心', '/pages/agent/index', false],
    ],
  },
  'pages/service/service.js': {
    label: '服务页 8 宫格',
    items: [
      [1, '图书馆预约', '/pages/library/index', false],
      [2, '查空教室', '/pages/library/freeRoom', false],
      [3, '二手集市', '/pages/secondhand/index', false],
      [4, '兼职实习', '/pages/job/index', false],
      [5, '校园地图', '/pages/map/index', false],
      [6, '外卖点餐', '/pages/life/index', false],
      [7, '通知公告', '/pages/life/notices', false],
      [8, '任务中心', '/pages/agent/index', false],
    ],
  },
}

const ITEM_RE = /\{\s*id:\s*(\d+),\s*name:\s*'([^']*)',\s*icon:\s*'([^']*)',\s*url:\s*'([^']*)',\s*tab:\s*(true|false)\s*\}/g

for (const [rel, spec] of Object.entries(EXPECTED)) {
  const raw = fs.readFileSync(path.join(MP, rel), 'utf8')
  const block = raw.match(/gridItems:\s*\[([\s\S]*?)\n\s*\],/)
  if (!block) {
    check(`${spec.label} 可解析`, false, `未找到 gridItems 数组（${rel}）`)
    continue
  }

  const items = []
  let m
  while ((m = ITEM_RE.exec(block[1]))) {
    items.push({ id: +m[1], name: m[2], icon: m[3], url: m[4], tab: m[5] === 'true' })
  }

  check(`${spec.label} 条目数 = ${spec.items.length}`, items.length === spec.items.length, `实际 ${items.length}`)

  spec.items.forEach(([id, name, url, tab], i) => {
    const it = items[i]
    const ok = !!it && it.id === id && it.name === name && it.url === url && it.tab === tab
    check(
      `${spec.label} #${id} ${name}`,
      ok,
      it ? `实际 id=${it.id} name=${it.name} url=${it.url} tab=${it.tab}` : '缺失'
    )
    if (it) {
      check(
        `${spec.label} #${id} ${name} 使用本地线性 SVG`,
        /^\/static\/icons\/[A-Za-z0-9._-]+\.svg$/.test(it.icon),
        `实际 icon=${it.icon}`
      )
    }
  })
}

// ------------------------------------------- E. emoji 字形盘点（仅统计，不断言）

console.log('\nE. emoji 字形盘点（[INFO] 仅统计，不影响退出码 —— 不在 F11 验收口径内，见 static/icons/README.md §5）')

// 三种口径（详细解释与「评审报告 109 处」的对账见 README §5.2）：
//   A（采用）: Extended_Pictographic 码点 + 独立 VS16(U+FE0F) —— 与 README §5 历史数字同口径
//   BASE     : 只数 Extended_Pictographic 基字形（A 与 BASE 的差 = 独立 VS16 数）
//   BROAD    : A ∪ 箭头(→ ← ↑ ↓) ∪ 带圈字母数字(①②…) —— 非 pictographic 的排版符号，仅供参考对账
const EMOJI_RE = /\p{Extended_Pictographic}|\uFE0F/gu
const EMOJI_BASE_RE = /\p{Extended_Pictographic}/gu
const EMOJI_BROAD_RE = /\p{Extended_Pictographic}|\uFE0F|[\u2190-\u21FF]|[\u2460-\u24FF]/gu
const CENSUS_EXTS = ['.js', '.wxml', '.wxss', '.json']

const census = new Map(CENSUS_EXTS.map((ext) => [ext, { files: 0, count: 0, base: 0, broad: 0, broadFiles: 0 }]))
const shapes = { error: 0, empty: 0, inline: 0 }
const baseGlyphs = new Map() // 基字形 -> 处数（用于 README §5.2 的对账）

// 单遍扫描：每个源文件只读一次，同时累计三种口径与 wxml 形态分布。
for (const file of sourceFiles) {
  const raw = fs.readFileSync(file, 'utf8')
  const ext = path.extname(file)
  const row = census.get(ext)
  if (!row) continue

  const count = (raw.match(EMOJI_RE) || []).length
  if (count > 0) {
    row.files += 1
    row.count += count
    const baseHits = raw.match(EMOJI_BASE_RE) || []
    row.base += baseHits.length
    for (const g of baseHits) baseGlyphs.set(g, (baseGlyphs.get(g) || 0) + 1)
  }
  const broad = (raw.match(EMOJI_BROAD_RE) || []).length
  row.broad += broad
  if (broad > 0) row.broadFiles += 1

  if (ext !== '.wxml' || count === 0) continue
  // 形态分布按**行**归类：行内含 xj-empty-icon 且含 ⚠ = 错误态；含 xj-empty-icon = 空态；其余 = 行内文本。
  for (const line of raw.split(/\r?\n/)) {
    const n = (line.match(EMOJI_RE) || []).length
    if (!n) continue
    const hasEmptyIcon = /xj-empty-icon/.test(line)
    const bucket = hasEmptyIcon ? (/⚠/.test(line) ? 'error' : 'empty') : 'inline'
    shapes[bucket] += n
  }
}

const rows = [...census.entries()]
const total = (pick) => rows.reduce((a, [, r]) => a + pick(r), 0)
const byExt = (pick) => rows.map(([ext, r]) => `${ext} ${pick(r)}`).join(' · ')

console.log('  [INFO] 口径A（采用）= Extended_Pictographic + 独立 VS16；范围 = miniprogram/ 下 .js/.wxml/.wxss/.json')
console.log(`  [INFO] 合计 ${total((r) => r.count)} 处 / ${total((r) => r.files)} 个文件（含 emoji 的文件数）`)
console.log(`  [INFO] 分扩展名（处/文件）：${byExt((r) => `${r.count}/${r.files}`)}`)
console.log(`  [INFO] 只数基字形 = ${total((r) => r.base)} 处（${byExt((r) => r.base)}）；独立 VS16 = ${total((r) => r.count) - total((r) => r.base)} 处`)
const topGlyphs = [...baseGlyphs.entries()]
  .sort((a, b) => b[1] - a[1])
  .slice(0, 5)
  .map(([g, n]) => `${g} ${n}`)
  .join(' · ')
console.log(`  [INFO] 基字形 Top5：${topGlyphs}`)
console.log(`  [INFO] wxml 形态分布：错误态 \`.xj-empty-icon\` ⚠️ ${shapes.error} · 空态 ${shapes.empty} · 行内文本 ${shapes.inline}`)
console.log(`  [INFO] 参考口径（并计 →/①② 等非 pictographic 符号）= ${total((r) => r.broad)} 处 / ${total((r) => r.broadFiles)} 个文件（${byExt((r) => r.broad)}）`)
console.log('  [INFO] F11 验收只覆盖「图标资产」：彩色 PNG 引用 = 0（见 C 段）；emoji 归属见 README §5')

// ---------------------------------------------------------------- 汇总

console.log(`\n${'-'.repeat(60)}`)
if (failures.length === 0) {
  console.log(`[PASS] F11 图标集校验全部通过（${passed} 项）`)
  process.exit(0)
} else {
  console.log(`[FAIL] ${failures.length} 项未通过 / 共 ${passed + failures.length} 项：`)
  for (const f of failures) console.log(`  - ${f}`)
  process.exit(1)
}
