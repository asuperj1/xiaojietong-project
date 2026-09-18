#!/usr/bin/env node
/**
 * F22 · 小程序端静态检查（6 条规则）
 *
 * 目的
 * ----
 * 把本仓**已经实际踩过**的小程序坑类固化成提交/CI 前可跑的静态自检，
 * 让这些坑不再回归。默认只读扫描 `miniprogram/`，不依赖微信开发者工具、
 * MySQL、后端与网络。
 *
 * 六条规则与来源（规则不是凭记忆发明的，逐条给出仓内证据）
 * ------------------------------------------------------
 *  R1_TAP_DETAIL_VALUE      tap/长按处理器不得读 `e.detail.value`
 *     来源：`docs/项目审计报告20260912序1.md:182`（FRONT-02 · P0：5 个页面分类切换
 *     整体失效，点击无反应）；`docs/验收测试/00-第一阶段验收测试方案.md:243`；
 *     `docs/成员任务单-二阶段整改260912.md:324`（F22 规则①）。
 *     正解：`Number(e.currentTarget.dataset.index)`；`picker` 的 `bindchange`
 *     才用 `e.detail.value`（`docs/项目审计报告20260912序1.md:197`）。
 *
 *  R2_MISSING_LOWER_TRIGGER 分页列表页必须有触底加载钩子
 *     来源：`docs/项目审计报告20260912序1.md:826`（FRONT-08 · P1：`life/index`、
 *     `life/notices` 仍缺 `onReachBottom`，20 条后无法加载更多）；
 *     `docs/验收测试/A-执行报告-前端体验走查260912.md:21,65,103`（12 个列表页）；
 *     `docs/二阶段整改方案-前端UI重构与后端支撑.md:395`。
 *
 *  R3_DATETIME_STRING_PARSE  不得用 `new Date('Y-m-d H:i:s')` 解析时间字符串
 *     来源：`docs/PR39-审查报告260912.md:173`（iOS `Invalid Date` 经典跨端坑）；
 *     `docs/验收测试/A-执行报告-前端体验走查260912.md:92,106`
 *     （`pages/library/seat.js` 一处被点名）；任务单 F22 规则③。
 *     注：`new Date()`（无参）与 ISO `T` 形式**不算违规**，见下方 self-test 的合法样例。
 *
 *  R4_LIST_FIELD_NO_FALLBACK 后端列表字段必须先兜底再使用
 *     来源：`docs/成员1任务单-20260918.md` §2.3 规则④（`(res && res.items) || []`，
 *     后端字段缺失时页面白屏）；本仓 `miniprogram/pages/**` 已有 30+ 处该写法，
 *     本脚本的「集合字段口径」正是从仓内既有写法自校准出来的。
 *
 *  R5_HARDCODED_BASE_URL    除 `config/env.js` 外不得出现写死的接口地址
 *     来源：`FRONT-01`（P0）`docs/项目审计报告20260912序1.md:170`；
 *     `docs/前端上线-域名与HTTPS方案.md:80`（改为导出 `getBaseUrl` 函数）；
 *     `docs/PR53-审查报告260913.md:143,181,190`（不再导出静态 `BASE_URL`）。
 *
 *  R6_PAGE_REGISTRATION     新页面必须注册进 `app.json`，TabBar 与跳转目标必须一致
 *     来源：`docs/成员1任务单-20260918.md` §2.3 规则⑥（漏注册 = 跳转失败）；
 *     任务单 §5 第 8 项（`git grep "\"pages/" miniprogram/app.json`）。
 *
 * 六条规则出自 `origin/fix/task-sheet-0918:docs/成员1任务单-20260918.md` §2.3
 * （该单尚未进 `dev`，故此处引用分支内原文位置）。
 *
 * 与其他 verifier 的边界（不重复造轮子）
 * ------------------------------------
 *  `tools/verify_frontend_base_url_guard.js` 验证的是 **运行期行为**
 *  （stub `wx` 后实测 release/develop 解析、异常是否同步逃逸）；
 *  本脚本的 R5 是**静态字面量扫描**（除 `config/env.js` 外不得出现写死的地址 /
 *  静态 `BASE_URL`）。两者互补：前者证明"解析逻辑对"，后者防止"新页面又写死一个"。
 *  同理 R1/R2/R3/R4/R6 在 `tools/` 下均无既有覆盖（`verify_f11_icon_set.js` /
 *  `verify_glass_probe.js` / `verify_f16_forum_search.js` / F12 的 POC 脚本
 *  都是单页/单特性行为验证）。
 *
 * 用法
 * ----
 *     node tools/verify_miniprogram_static_rules.js              # 只读扫描 miniprogram/
 *     node tools/verify_miniprogram_static_rules.js --self-test   # 六条规则的阴性对照
 *     node tools/verify_miniprogram_static_rules.js --src <repo-root>
 *
 *  退出码：0 = 六条规则全部通过；1 = 至少一条违规（或脚本/自检自身异常）。
 *
 * 跨平台
 * ------
 *  读取文本时统一做 `CRLF/CR → LF` 归一化并去 BOM（本仓 `core.autocrlf=true`，
 *  Windows 工作区是 CRLF、blob 是 LF —— F16 verifier 曾在这里踩过坑）。
 *
 * 本脚本覆盖不到什么（**不要外推**）
 * --------------------------------
 *  ✗ 视觉呈现 / 动画 / 真机性能 / `@supports` 类语法（WXSS 不在官方列举内）→ 真机抽测；
 *  ✗ 运行期行为（请求是否真的发出、状态机是否正确）→ 见各特性 `verify_*.js`；
 *  ✗ 后端接口语义与字段是否真的存在；
 *  ✗ 事件处理器写在 `behaviors`/混入对象里的情况（R1 只解析页面自身 JS）；
 *  ✗ `scroll-view` 自带 `bindscrolltolower` 之外的自定义分页触发（如按钮"加载更多"）；
 *  ✗ 后端时间字段实际类型为数字时间戳时，R3 的"字段直解"分面可能误报（见 R3 说明）；
 *  ✗ 列表字段不在本仓既有集合口径内的新命名（R4 口径自校准，见 `collectCollectionNames`）。
 */

'use strict'

const fs = require('fs')
const os = require('os')
const path = require('path')

// ============================================================== 规则清单 ====

const RULES = [
  {
    id: 'R1_TAP_DETAIL_VALUE',
    title: 'tap/长按处理器不得读 e.detail.value（索引须取 e.currentTarget.dataset.*）',
    source: 'FRONT-02(P0) docs/项目审计报告20260912序1.md:182 · 任务单 F22 规则①',
    fix: '改为 Number(e.currentTarget.dataset.index)；只有 picker 的 bindchange 才用 e.detail.value',
  },
  {
    id: 'R2_MISSING_LOWER_TRIGGER',
    title: '分页列表页必须有触底加载钩子（onReachBottom 或 bindscrolltolower）',
    source: 'FRONT-08(P1) docs/项目审计报告20260912序1.md:826 · 走查报告:103 · 任务单 F22 规则②',
    fix: '补 onReachBottom() { this.fetch(this.data.page + 1) }，或在 scroll-view 上加 bindscrolltolower',
  },
  {
    id: 'R3_DATETIME_STRING_PARSE',
    title: "不得用 new Date('Y-m-d H:i:s') 解析时间字符串（iOS → Invalid Date）",
    source: 'docs/PR39-审查报告260912.md:173 · 走查报告:92,106 · 任务单 F22 规则③',
    fix: '按仓内既有做法做字符串截断格式化（如 formatTime），或改用 ISO 形式 / 数字时间戳',
  },
  {
    id: 'R4_LIST_FIELD_NO_FALLBACK',
    title: '后端列表字段必须先兜底再使用（(res && res.items) || []），不得直接当数组用',
    source: 'docs/成员1任务单-20260918.md §2.3 规则④ · 仓内既有写法（30+ 处）',
    fix: '写成 ((res && res.items) || []) 后再 .map/.length；整块赋值时也要带 || []',
  },
  {
    id: 'R5_HARDCODED_BASE_URL',
    title: '除 config/env.js 外不得出现写死的接口地址 / 静态 BASE_URL',
    source: 'FRONT-01(P0) docs/项目审计报告20260912序1.md:170 · docs/PR53-审查报告260913.md:181',
    fix: "统一走 services/request.js（内部 require('../config/env').getBaseUrl()）",
  },
  {
    id: 'R6_PAGE_REGISTRATION',
    title: '页面必须注册进 app.json，TabBar 项与跳转目标必须与 pages 一致',
    source: 'docs/成员1任务单-20260918.md §2.3 规则⑥、§5 第 8 项',
    fix: '在 app.json#pages 补登记（TabBar 页同时补 tabBar.list）；删除或修正无效跳转目标',
  },
]

const RULE_BY_ID = {}
RULES.forEach((r) => {
  RULE_BY_ID[r.id] = r
})

// R4 集合字段口径的种子（其余从仓内既有写法自校准，见 collectCollectionNames）
const COLLECTION_SEED = ['items', 'list', 'rows', 'records']
// R3 后端时间字段名（字符串时间直解的典型）
const BACKEND_TIME_FIELDS = [
  'created_at',
  'updated_at',
  'publish_time',
  'remind_at',
  'deadline',
  'expire_at',
  'scheduled_at',
]
// R5 豁免清单：确实需要写死的外部链接（当前为空；新增须在此说明理由）
const URL_LITERAL_ALLOWLIST = []
// R6 允许的「非页面」目录（WXML 模板片段等）
const TAP_EVENTS = ['tap', 'longtap', 'longpress']

// ============================================================ 文本工具 ====

/**
 * 读取文本：去 BOM + 行尾归一化（CRLF/CR → LF）。
 * F16 verifier 的教训：Windows 检出是 CRLF，含 `\n` 的锚点若不归一化会静默失配。
 */
function readText(file) {
  let s = fs.readFileSync(file, 'utf8')
  if (s.charCodeAt(0) === 0xfeff) s = s.slice(1)
  return s.replace(/\r\n?/g, '\n')
}

/** 行尾归一化（对外暴露，self-test 复用） */
function normalizeEol(s) {
  return String(s).replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n')
}

/**
 * 去掉 JS 注释（行注释 / 块注释），**保持字符长度与换行不变**，
 * 这样行号、列位置仍与原文一一对应。
 *
 * 为什么必须去注释：本仓多处用注释记录"曾经的坑"（如
 * `miniprogram/pages/life/index.js` 里写着 `e.detail.value` 的错误写法），
 * 若不去注释，注释本身就会污染断言（既可能误报，也可能"让断言以为已修复"）。
 *
 * 字符串/模板串与正则字面量内的 `//` 不算注释；正则字面量用
 * "上一有效字符"启发式识别（`= ( , : [ ! & | ? { } ;` 之后才是正则）。
 */
function stripComments(src) {
  const out = src.split('')
  const n = src.length
  const blank = (i) => {
    if (src[i] !== '\n') out[i] = ' '
  }
  let i = 0
  let prev = '' // 上一个非空白有效字符（用于区分正则 / 除号）
  while (i < n) {
    const c = src[i]
    const d = src[i + 1]
    if (c === '/' && d === '/') {
      blank(i)
      blank(i + 1)
      i += 2
      while (i < n && src[i] !== '\n') {
        blank(i)
        i += 1
      }
      continue
    }
    if (c === '/' && d === '*') {
      blank(i)
      blank(i + 1)
      i += 2
      while (i < n && !(src[i] === '*' && src[i + 1] === '/')) {
        blank(i)
        i += 1
      }
      blank(i)
      blank(i + 1)
      i += 2
      continue
    }
    if (c === '"' || c === "'" || c === '`') {
      const quote = c
      i += 1
      while (i < n) {
        if (src[i] === '\\') {
          i += 2
          continue
        }
        if (src[i] === quote) {
          i += 1
          break
        }
        i += 1
      }
      prev = quote
      continue
    }
    if (c === '/' && (prev === '' || '(,=:[!&|?{};+-*%~^<>'.indexOf(prev) !== -1)) {
      // 正则字面量：跳到未转义的结束 '/'
      i += 1
      while (i < n) {
        if (src[i] === '\\') {
          i += 2
          continue
        }
        if (src[i] === '/') {
          i += 1
          break
        }
        if (src[i] === '\n') break
        i += 1
      }
      prev = '/'
      continue
    }
    if (!/\s/.test(c)) prev = c
    i += 1
  }
  return out.join('')
}

/** 去掉 WXML 注释 `<!-- ... -->`（同样保持长度与换行） */
function stripWxmlComments(src) {
  return src.replace(/<!--[\s\S]*?-->/g, (m) => m.replace(/[^\n]/g, ' '))
}

/** 1-based 行号 */
function lineAt(src, index) {
  let line = 1
  for (let i = 0; i < index && i < src.length; i += 1) if (src[i] === '\n') line += 1
  return line
}

/** 取 index 所在行的整行文本（用于证据展示） */
function lineTextAt(src, index) {
  const start = src.lastIndexOf('\n', Math.max(0, index - 1)) + 1
  let end = src.indexOf('\n', index)
  if (end === -1) end = src.length
  return src.slice(start, end).trim()
}

function clip(s, max = 160) {
  const t = String(s).replace(/\s+/g, ' ').trim()
  return t.length > max ? t.slice(0, max - 1) + '…' : t
}

/** 收集正则全部匹配（带 index） */
function findAll(src, re) {
  const out = []
  let m
  re.lastIndex = 0
  while ((m = re.exec(src)) !== null) {
    out.push(m)
    if (m.index === re.lastIndex) re.lastIndex += 1
  }
  return out
}

/** 跳过空白与换行 */
function skipWs(src, i) {
  while (i < src.length && /\s/.test(src[i])) i += 1
  return i
}

/** 从 `(` 找到配对的 `)`，返回其下标，失败返回 -1 */
function matchParen(src, open) {
  let depth = 0
  for (let i = open; i < src.length; i += 1) {
    const c = src[i]
    if (c === '(') depth += 1
    else if (c === ')') {
      depth -= 1
      if (depth === 0) return i
    }
  }
  return -1
}

/** 从 `{` 找到配对的 `}`，返回其下标，失败返回 -1 */
function matchBrace(src, open) {
  let depth = 0
  for (let i = open; i < src.length; i += 1) {
    const c = src[i]
    if (c === '{') depth += 1
    else if (c === '}') {
      depth -= 1
      if (depth === 0) return i
    }
  }
  return -1
}

/** 找到名字为 name 的 `foo(...) {` / `foo: function (...) {` 的函数体区间 */
function extractFunctionBody(src, name) {
  const re = new RegExp('(?:^|[^\\w$.])' + name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\b', 'g')
  let m
  while ((m = re.exec(src)) !== null) {
    let i = skipWs(src, m.index + m[0].length)
    if (src[i] === ':') {
      i = skipWs(src, i + 1)
      if (src.startsWith('async', i)) i = skipWs(src, i + 5)
      if (src.startsWith('function', i)) i = skipWs(src, i + 8)
    } else if (src.startsWith('async', i)) {
      i = skipWs(src, i + 5)
    }
    if (src[i] !== '(') continue
    const close = matchParen(src, i)
    if (close === -1) continue
    const brace = skipWs(src, close + 1)
    if (src[brace] !== '{') continue
    const end = matchBrace(src, brace)
    if (end === -1) continue
    return { start: brace, end, body: src.slice(brace, end + 1) }
  }
  return null
}

/** 找到所有 `name(...)` 调用的实参区间（跳过 `.name(` 这类成员调用） */
function findCalls(src, name) {
  const out = []
  const re = new RegExp('(?:^|[^\\w$.])' + name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*\\(', 'g')
  for (const m of findAll(src, re)) {
    const open = src.indexOf('(', m.index + m[0].length - 1)
    const close = matchParen(src, open)
    if (close === -1) continue
    out.push({ index: m.index, args: src.slice(open + 1, close), text: src.slice(m.index, close + 1) })
  }
  return out
}

// ============================================================ 文件遍历 ====

/** 递归列出文件：{ abs, rel }（rel 用 `/` 分隔，跨平台一致） */
function listFiles(root, filter, dir = root, acc = []) {
  let entries
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true })
  } catch (e) {
    return acc
  }
  for (const e of entries) {
    const abs = path.join(dir, e.name)
    if (e.isDirectory()) listFiles(root, filter, abs, acc)
    else if (!filter || filter(abs)) {
      acc.push({ abs, rel: path.relative(root, abs).split(path.sep).join('/') })
    }
  }
  return acc
}

// ============================================================== 扫描器 ====

/**
 * 扫描一个 `miniprogram/` 目录，返回 { violations, stats }。
 *
 * ⚠️ `--self-test` 走的是**同一个函数**（只是把根目录指向临时 fixture），
 * 这样自检证明的就是生产扫描逻辑本身，而不是另写一套复刻版。
 */
function scan(mpDir) {
  codeCache.clear()
  if (!fs.existsSync(mpDir)) {
    throw new Error(`扫描根目录不存在：${mpDir}`)
  }
  const jsFiles = listFiles(mpDir, (f) => f.endsWith('.js'))
  const wxmlFiles = listFiles(mpDir, (f) => f.endsWith('.wxml'))
  const jsonFiles = listFiles(mpDir, (f) => f.endsWith('.json'))
  const byRel = {}
  jsFiles.concat(wxmlFiles, jsonFiles).forEach((f) => {
    byRel[f.rel] = f
  })
  const pages = jsFiles.filter((f) => f.rel.startsWith('pages/') && !!byRel[f.rel.replace(/\.js$/, '.wxml')])

  const ctx = { mpDir, jsFiles, wxmlFiles, byRel, pages }
  ctx.collectionNames = collectCollectionNames(jsFiles)
  ctx.responseBindings = {}
  jsFiles.forEach((f) => {
    ctx.responseBindings[f.rel] = collectResponseBindings(code(f.abs))
  })

  const violations = []
  rule1TapDetailValue(ctx, violations)
  rule2MissingLowerTrigger(ctx, violations)
  rule3DatetimeStringParse(ctx, violations)
  rule4ListFieldNoFallback(ctx, violations)
  rule5HardcodedBaseUrl(ctx, violations)
  rule6PageRegistration(ctx, violations)

  violations.sort((a, b) => (a.rule === b.rule ? a.file.localeCompare(b.file) || a.line - b.line : a.rule.localeCompare(b.rule)))
  return {
    violations,
    stats: {
      js: jsFiles.length,
      wxml: wxmlFiles.length,
      pages: pages.length,
      collectionNames: Array.from(ctx.collectionNames).sort(),
    },
  }
}

const codeCache = new Map()
function code(file) {
  if (!codeCache.has(file)) codeCache.set(file, stripComments(readText(file)))
  return codeCache.get(file)
}

function violation(rule, file, line, evidence, kind) {
  return { rule, file, line, evidence: clip(evidence), kind, fix: RULE_BY_ID[rule].fix }
}

// ---------------------------------------------------------------- R1 ----

/** 从 WXML 里取 tap/长按类事件绑定的处理器名（WXML 注释已剔除） */
function tapHandlers(wxmlSrc) {
  const names = new Set()
  const re = new RegExp('(?:^|\\s)(?:bind|catch)[:]?(?:' + TAP_EVENTS.join('|') + ')\\s*=\\s*"([^"{}]+)"', 'g')
  for (const m of findAll(wxmlSrc, re)) {
    const name = m[1].trim()
    if (/^[\w$]+$/.test(name)) names.add(name)
  }
  return Array.from(names)
}

function rule1TapDetailValue(ctx, out) {
  for (const page of ctx.pages) {
    const wxmlFile = ctx.byRel[page.rel.replace(/\.js$/, '.wxml')]
    if (!wxmlFile) continue
    const handlers = tapHandlers(stripWxmlComments(readText(wxmlFile.abs)))
    if (!handlers.length) continue
    const src = code(page.abs)
    for (const name of handlers) {
      const fn = extractFunctionBody(src, name)
      if (!fn) continue // 处理器不在页面 JS 里（behaviors/混入）→ 静态盲区，不判
      const hit = /\bdetail\s*\.\s*value\b/.exec(fn.body)
      if (!hit) continue
      const at = fn.start + hit.index
      out.push(
        violation('R1_TAP_DETAIL_VALUE', page.rel, lineAt(src, at), `${name} 内：${lineTextAt(src, at)}`, 'tap-detail-value')
      )
    }
  }
}

// ---------------------------------------------------------------- R2 ----

function rule2MissingLowerTrigger(ctx, out) {
  for (const page of ctx.pages) {
    const src = code(page.abs)
    const paginated = findCalls(src, 'request').find((c) => /\bpage\b/.test(c.args))
    if (!paginated) continue
    if (/(?:^|[\s,{])onReachBottom\s*[:(]/.test(src)) continue
    const wxmlFile = ctx.byRel[page.rel.replace(/\.js$/, '.wxml')]
    const wxmlSrc = wxmlFile ? stripWxmlComments(readText(wxmlFile.abs)) : ''
    if (/(?:bind|catch)[:]?scrolltolower\s*=/.test(wxmlSrc)) continue
    out.push(
      violation(
        'R2_MISSING_LOWER_TRIGGER',
        page.rel,
        lineAt(src, paginated.index),
        `分页拉取但无触底钩子：${clip(paginated.text, 120)}`,
        'missing-hook'
      )
    )
  }
}

// ---------------------------------------------------------------- R3 ----

function rule3DatetimeStringParse(ctx, out) {
  const patterns = [
    { re: /\bnew\s+Date\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)/g, kind: 'new-date' },
    { re: /\bDate\s*\.\s*parse\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)/g, kind: 'date-parse' },
  ]
  for (const file of ctx.jsFiles) {
    const src = code(file.abs)
    for (const p of patterns) {
      for (const m of findAll(src, p.re)) {
        const arg = m[1].trim()
        const line = lineAt(src, m.index)
        const evidenceOf = () => lineTextAt(src, m.index)
        if (!arg) continue // new Date()：合法（本仓 pages/library/seat.js 同款）
        const strLit = /^(['"])([\s\S]*)\1$/.exec(arg)
        if (strLit) {
          const value = strLit[2]
          // 只判「日期与时间用空格分隔」的经典 iOS 失败形式；ISO 的 `T` 形式是安全的
          if (/^\d{4}-\d{1,2}-\d{1,2}[ ]+\d{1,2}:\d{2}/.test(value)) {
            out.push(
              violation('R3_DATETIME_STRING_PARSE', file.rel, line, `字符串时间直解：${evidenceOf()}`, 'date-literal')
            )
          }
          continue
        }
        const last = arg.split('.').pop().trim()
        if (/^[\w$]+$/.test(arg) || /^[\w$]+(\s*\.\s*[\w$]+)+$/.test(arg)) {
          if (BACKEND_TIME_FIELDS.indexOf(last) !== -1) {
            out.push(
              violation('R3_DATETIME_STRING_PARSE', file.rel, line, `后端时间字段直解：${evidenceOf()}`, 'date-field')
            )
          }
        }
      }
    }
  }
}

// ---------------------------------------------------------------- R4 ----

/**
 * 「集合字段」口径自校准：凡在本仓以 `X.f || []` / `X.f.map(...)` 这类**数组用法**
 * 出现过的字段名，都算列表字段（接收者限定为顶层标识符，页面态 `this.data.f`
 * 不算 —— 它已经是兜底过的本地状态）。
 */
function collectCollectionNames(jsFiles) {
  const names = new Set(COLLECTION_SEED)
  const chain = '(?:^|[^\\w$.])([A-Za-z_$][\\w$]*)\\s*\\.\\s*([A-Za-z_$][\\w$]*)'
  const re1 = new RegExp(chain + '\\s*\\)*\\s*\\|\\|\\s*\\[\\s*\\]', 'g')
  const re2 = new RegExp(
    chain + '\\s*\\.\\s*(?:map|forEach|filter|concat|slice|find|findIndex|some|every|reduce|join)\\b',
    'g'
  )
  for (const f of jsFiles) {
    const src = code(f.abs)
    findAll(src, re1).forEach((m) => names.add(m[2]))
    findAll(src, re2).forEach((m) => names.add(m[2]))
  }
  return names
}

/** 从 `request(...)` 回调参数 / `await request(...)` 里推断"响应对象"变量名 */
function collectResponseBindings(src) {
  const names = new Set(['res', 'resp', 'response'])
  findAll(src, /\.then\s*\(\s*(?:async\s+)?(?:function\s*)?\(?\s*([A-Za-z_$][\w$]*)\s*[,)]/g).forEach((m) => names.add(m[1]))
  findAll(src, /(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:await\s+)?request\s*\(/g).forEach((m) => names.add(m[1]))
  return names
}

const ARRAY_USE_RE = /^(?:\.\s*(?:map|forEach|filter|concat|slice|find|findIndex|some|every|reduce|reduceRight|join|pop|push|shift|unshift|sort|reverse|indexOf|includes)\s*\(|\.\s*length\b|\[)/

function rule4ListFieldNoFallback(ctx, out) {
  const colls = Array.from(ctx.collectionNames)
  for (const file of ctx.jsFiles) {
    const src = code(file.abs)
    const binds = Array.from(ctx.responseBindings[file.rel] || [])
    if (!binds.length || !colls.length) continue
    for (const bind of binds) {
      const re = new RegExp(
        '(^|[^\\w$.\'"])' + bind.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*(?:\\.\\s*data)?\\s*\\.\\s*([A-Za-z_$][\\w$]*)',
        'g'
      )
      for (const m of findAll(src, re)) {
        const field = m[2]
        if (colls.indexOf(field) === -1) continue
        const start = m.index + m[1].length
        const end = m.index + m[0].length
        const before = src.slice(Math.max(0, start - 40), start)
        const after = src.slice(end, end + 40)
        const line = lineAt(src, start)
        const evidence = lineTextAt(src, start)

        // (0) 已有兜底：`X.items || []`（含 `(res && res.items) || []` 里的 `) || []`）
        if (/^\s*\)*\s*\|\|/.test(after)) continue
        // (1) 直接当数组用：`X.items.map(` / `X.items[0]` / `X.items.length`
        //     （`(res && res.items).map(...)` 也算 —— res 为 null 时同样抛错）
        if (ARRAY_USE_RE.test(after.replace(/^\s*\)*\s*/, ''))) {
          out.push(violation('R4_LIST_FIELD_NO_FALLBACK', file.rel, line, `直接当数组用：${evidence}`, 'list-array-use'))
          continue
        }
        // (2) `X && X.items` 形式的前置守卫（非数组用法，如 if/三元）→ 放行
        if (/&&\s*$/.test(before)) continue
        // (3) 整块赋值无兜底：`setData({ items: res.items })` / `const x = res.items`
        if (/[:=]\s*$/.test(before)) {
          out.push(violation('R4_LIST_FIELD_NO_FALLBACK', file.rel, line, `整块赋值未兜底：${evidence}`, 'list-assign'))
        }
      }
    }
  }
}

// ---------------------------------------------------------------- R5 ----

function rule5HardcodedBaseUrl(ctx, out) {
  const ENV_REL = 'config/env.js'
  if (!ctx.byRel[ENV_REL]) {
    out.push(violation('R5_HARDCODED_BASE_URL', ENV_REL, 0, '缺少统一地址解析模块 config/env.js', 'missing-env'))
  }
  for (const file of ctx.jsFiles) {
    if (file.rel === ENV_REL) continue // env.js 是地址的唯一事实来源（http://127.0.0.1 为 develop 默认值）
    const src = code(file.abs)
    for (const m of findAll(src, /\bBASE_URL\b/g)) {
      out.push(
        violation('R5_HARDCODED_BASE_URL', file.rel, lineAt(src, m.index), `静态 BASE_URL：${lineTextAt(src, m.index)}`, 'base-url-ident')
      )
    }
    for (const m of findAll(src, /(['"])(https?:\/\/[^'"\s]*)\1/g)) {
      const url = m[2]
      if (URL_LITERAL_ALLOWLIST.indexOf(url) !== -1) continue
      out.push(
        violation('R5_HARDCODED_BASE_URL', file.rel, lineAt(src, m.index), `写死的地址字面量：${url}`, 'url-literal')
      )
    }
  }
}

// ---------------------------------------------------------------- R6 ----

function rule6PageRegistration(ctx, out) {
  const appFile = ctx.byRel['app.json']
  if (!appFile) {
    out.push(violation('R6_PAGE_REGISTRATION', 'app.json', 0, '缺少 app.json（无法校验页面注册）', 'missing-app-json'))
    return
  }
  const appSrc = readText(appFile.abs)
  let cfg
  try {
    cfg = JSON.parse(appSrc)
  } catch (e) {
    out.push(violation('R6_PAGE_REGISTRATION', 'app.json', 0, `app.json 不是合法 JSON：${e.message}`, 'app-json-parse'))
    return
  }

  const registered = new Set(Array.isArray(cfg.pages) ? cfg.pages : [])
  const subPackages = cfg.subPackages || cfg.subpackages || []
  for (const sp of subPackages) {
    const root = String(sp.root || '').replace(/\/?$/, '/')
    for (const p of sp.pages || []) registered.add(root + p)
  }

  // (a) 页面文件存在但没注册
  for (const page of ctx.pages) {
    const key = page.rel.replace(/\.js$/, '')
    if (!registered.has(key)) {
      out.push(
        violation('R6_PAGE_REGISTRATION', page.rel, 1, `页面未在 app.json#pages 注册：${key}`, 'page-unregistered')
      )
    }
  }
  // (b) 注册了但文件不存在
  for (const key of registered) {
    const js = ctx.byRel[key + '.js']
    const wxml = ctx.byRel[key + '.wxml']
    if (!js || !wxml) {
      out.push(
        violation('R6_PAGE_REGISTRATION', 'app.json', 0, `app.json 注册了不存在的页面：${key}（缺 ${!js ? '.js ' : ''}${!wxml ? '.wxml' : ''}）`, 'page-missing-file')
      )
    }
  }
  // (c) TabBar 项必须在 pages 内
  const tabList = (cfg.tabBar && cfg.tabBar.list) || []
  for (const item of tabList) {
    if (!item || !item.pagePath) continue
    if (!registered.has(item.pagePath)) {
      out.push(
        violation('R6_PAGE_REGISTRATION', 'app.json', 0, `tabBar 项未在 pages 注册：${item.pagePath}`, 'tabbar-unregistered')
      )
    }
  }
  // (d) 跳转目标必须是已注册页面（字符串字面量形式 /pages/x/y）
  const targetRe = /(['"])(\/pages\/[A-Za-z0-9_-]+\/[A-Za-z0-9_-]+)\1/g
  for (const file of ctx.jsFiles.concat(ctx.wxmlFiles)) {
    const src = file.rel.endsWith('.wxml') ? stripWxmlComments(readText(file.abs)) : code(file.abs)
    for (const m of findAll(src, targetRe)) {
      const target = m[2].slice(1)
      if (registered.has(target)) continue
      out.push(
        violation('R6_PAGE_REGISTRATION', file.rel, lineAt(src, m.index), `跳转目标未注册：${m[2]}`, 'nav-target-unregistered')
      )
    }
  }
}

// ============================================================== 报告 ====

const WIDTH = 88

function bar(title) {
  console.log('\n' + '='.repeat(WIDTH))
  if (title) {
    console.log(title)
    console.log('='.repeat(WIDTH))
  }
}

function printRuleInventory() {
  console.log('六条规则（来源见脚本头部注释与 tools/README.md）：')
  for (const r of RULES) {
    console.log(`  ${r.id}`)
    console.log(`      ${r.title}`)
    console.log(`      来源：${r.source}`)
  }
}

function printReport(result, mpLabel) {
  const { violations, stats } = result
  bar(`F22 小程序端静态检查 · 只读扫描 ${mpLabel}`)
  console.log(`文件：${stats.js} js / ${stats.wxml} wxml / 页面入口 ${stats.pages}`)
  console.log(`行尾已归一化（CRLF→LF）、BOM 已去；注释不参与断言`)
  console.log(`R4 集合字段口径（自校准）：${stats.collectionNames.join(', ') || '（空）'}`)

  printRuleInventory()

  if (!violations.length) {
    bar()
    console.log('[PASS] 六条规则全部通过：6/6 无违规')
    bar()
    return 0
  }

  bar(`违规明细（${violations.length} 处）`)
  violations.forEach((v, i) => {
    console.log(`\n[违规 ${i + 1}/${violations.length}] ${v.rule}`)
    console.log(`  文件: ${mpLabel}/${v.file}${v.line ? ':' + v.line : ''}`)
    console.log(`  证据: ${v.evidence}`)
    console.log(`  建议: ${v.fix}`)
  })

  const counts = {}
  violations.forEach((v) => {
    counts[v.rule] = (counts[v.rule] || 0) + 1
  })
  bar('汇总')
  RULES.forEach((r) => {
    const n = counts[r.id] || 0
    console.log(`  ${n ? '[NG]' : '[OK]'} ${r.id}${n ? '  → ' + n + ' 处' : ''}`)
  })
  console.log(`\n[FAIL] ${violations.length} 处违规，涉及 ${Object.keys(counts).length}/${RULES.length} 条规则`)
  console.log('⚠️ 六条规则只覆盖"已知坑类"；视觉/真机/运行期行为仍需人工与各特性 verifier。')
  return 1
}

// ============================================================ self-test ====
//
// 阴性对照：先造一份**合法**的最小 miniprogram 样例（必须 0 违规），
// 再对每条规则各注入一处**最小违规**，要求：
//   ① 对应规则必须命中；
//   ② 其余规则**不得**跟着命中（否则等于"碰巧抓到"，不算数）；
// 全部跑的是上面那个 scan() —— 即生产扫描逻辑本身。
//
// 诱饵：基线样例里刻意放了 6 组"把修复/违规写进注释"的诱饵，
// 基线 0 违规即证明**注释不会满足断言、也不会触发违规**。

const FIXTURE = {
  'app.json': `{
  "pages": [
    "pages/demo/demo",
    "pages/demo/extra"
  ],
  "tabBar": {
    "list": [
      { "pagePath": "pages/demo/demo", "text": "演示" }
    ]
  }
}
`,
  // env.js 是唯一允许出现绝对地址的文件（R5 必须豁免它）
  'config/env.js': `const DEFAULT_BASE_URL = 'http://127.0.0.1:8000/api/v1'
function getBaseUrl() {
  return DEFAULT_BASE_URL
}
module.exports = { getBaseUrl }
`,
  'services/request.js': `const { getBaseUrl } = require('../config/env')
function request(path) {
  return new Promise((resolve, reject) => {
    wx.request({ url: getBaseUrl() + path, success: resolve, fail: reject })
  })
}
module.exports = { request }
`,
  'pages/demo/demo.js': `const { request } = require('../../services/request')

// —— 诱饵注释（全在注释里：既不得满足断言，也不得触发违规）——
// onReachBottom() { this.fetch(this.data.page + 1) }
// 错误写法：this.setData({ catIndex: Number(e.detail.value) })
// const stamp = new Date('2026-09-12 10:00:00')
// const BASE_URL = 'http://127.0.0.1:8000/api/v1'
// const items = res.items

function today() {
  const d = new Date() // 合法样例：无参取当前时间（与 pages/library/seat.js 同款）
  return d.getFullYear() + '-01-01'
}

Page({
  data: { items: [], cats: ['全部'], catIndex: 0, page: 1, today: '' },
  onLoad() {
    this.setData({ today: today() })
    this.fetch(1)
  },
  onReachBottom() {
    this.fetch(this.data.page + 1)
  },
  onCatTap(e) {
    this.setData({ catIndex: Number(e.currentTarget.dataset.index) }, () => this.fetch(1))
  },
  onPickerChange(e) {
    // 合法样例：picker 的 bindchange 才用 e.detail.value
    this.setData({ catIndex: Number(e.detail.value) })
  },
  onGoExtra() {
    wx.navigateTo({ url: '/pages/demo/extra' })
  },
  fetch(page) {
    request('/demo/items', { data: { page, size: 20 } })
      .then((res) => {
        const items = ((res && res.items) || []).map((it) => ({ ...it }))
        this.setData({ items, page })
      })
      .catch(() => {})
  },
})
`,
  'pages/demo/demo.wxml': `<view class="page">
  <!-- 诱饵注释：<view bindtap="onCatTap">Number(e.detail.value)</view> -->
  <picker range="{{cats}}" value="{{catIndex}}" bindchange="onPickerChange">
    <view>{{cats[catIndex]}}</view>
  </picker>
  <view wx:for="{{items}}" wx:key="id" data-index="{{index}}" bindtap="onCatTap">{{item.name}}</view>
  <view bindtap="onGoExtra">去详情</view>
</view>
`,
  'pages/demo/extra.js': `Page({
  data: {},
  onLoad() {},
})
`,
  'pages/demo/extra.wxml': '<view>extra</view>\n',
}

function makeFixtureDir(files) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'xjt-f22-'))
  for (const rel of Object.keys(files)) {
    const abs = path.join(dir, 'miniprogram', rel)
    fs.mkdirSync(path.dirname(abs), { recursive: true })
    fs.writeFileSync(abs, normalizeEol(files[rel]), 'utf8')
  }
  return dir
}

function cloneFixture() {
  const out = {}
  Object.keys(FIXTURE).forEach((k) => {
    out[k] = FIXTURE[k]
  })
  return out
}

/** 应用一处突变，返回 { files, applied, note } */
function mutate(rel, from, to) {
  const files = cloneFixture()
  const src = files[rel]
  if (src === undefined) return { files, applied: false, note: `fixture 缺文件 ${rel}` }
  if (src.indexOf(from) === -1) return { files, applied: false, note: `锚点未命中（${rel}）：${clip(from, 60)}` }
  files[rel] = src.replace(from, to)
  return { files, applied: files[rel] !== src, note: '' }
}

function runOnFixture(files) {
  const dir = makeFixtureDir(files)
  try {
    return scan(path.join(dir, 'miniprogram'))
  } finally {
    try {
      fs.rmSync(dir, { recursive: true, force: true })
    } catch (e) {
      /* 清理失败不影响结论 */
    }
  }
}

let stPass = 0
let stFail = 0

function stCheck(name, ok, detail) {
  if (ok) {
    stPass += 1
    console.log(`  [OK] ${name}`)
  } else {
    stFail += 1
    console.log(`  [NG] ${name}${detail ? `\n         <- ${detail}` : ''}`)
  }
  return ok
}

/** 断言：指定规则命中 >= 1，且**没有其它规则**跟着命中 */
function expectSingleRule(label, files, ruleId, kinds) {
  const result = runOnFixture(files)
  const mine = result.violations.filter((v) => v.rule === ruleId)
  const others = result.violations.filter((v) => v.rule !== ruleId)
  const kindOk = !kinds || kinds.every((k) => mine.some((v) => v.kind === k))
  const otherText = others.map((v) => `${v.rule}@${v.file}:${v.line} ${v.evidence}`).join(' | ')
  return stCheck(
    `${label} → ${ruleId} 命中（且无其它规则误报）`,
    mine.length > 0 && others.length === 0 && kindOk,
    !mine.length
      ? '未被对应规则抓到（空跑）'
      : others.length
        ? `其它规则跟着命中（碰巧抓到，不算数）：${otherText}`
        : `命中分面不符：期望 ${JSON.stringify(kinds)}，实际 ${JSON.stringify(mine.map((v) => v.kind))}`
  )
}

function runSelfTest() {
  bar('F22 --self-test · 阴性对照（每条规则的最小违规样例必须被**本规则**抓到）')
  console.log('临时 fixture 建于系统 TEMP 目录，跑完即删；不触碰真实仓库文件。\n')

  // ---------------------------------------------- 基线（合法样例）--------
  console.log('[基线] 合法样例：6 条规则都必须 0 违规（含注释诱饵）')
  const base = runOnFixture(cloneFixture())
  stCheck(
    `基线扫描 0 违规（实际 ${base.violations.length}）`,
    base.violations.length === 0,
    base.violations.map((v) => `${v.rule}@${v.file}:${v.line} ${v.evidence}`).join(' | ')
  )
  // 证明基线里"确实含有"这些合法/诱饵样例（否则阴性对照是空的）
  const demoJs = FIXTURE['pages/demo/demo.js']
  const demoWxml = FIXTURE['pages/demo/demo.wxml']
  stCheck('基线含合法样例 new Date()（不得被 R3 误报）', /const d = new Date\(\)/.test(demoJs))
  stCheck('基线含合法样例 picker bindchange + e.detail.value（不得被 R1 误报）', /bindchange="onPickerChange"/.test(demoWxml) && /Number\(e\.detail\.value\)/.test(demoJs))
  stCheck('基线含合法样例 ((res && res.items) || []).map（不得被 R4 误报）', /\(\(res && res\.items\) \|\| \[\]\)\.map/.test(demoJs))
  stCheck('基线含 6 组注释诱饵（onReachBottom/detail.value/new Date/BASE_URL/res.items）',
    /\/\/ onReachBottom\(\)/.test(demoJs) &&
      /\/\/ 错误写法：.*e\.detail\.value/.test(demoJs) &&
      /\/\/ const stamp = new Date\('2026-09-12 10:00:00'\)/.test(demoJs) &&
      /\/\/ const BASE_URL = 'http:\/\/127\.0\.0\.1:8000\/api\/v1'/.test(demoJs) &&
      /\/\/ const items = res\.items/.test(demoJs) &&
      /<!-- 诱饵注释：.*e\.detail\.value/.test(demoWxml))
  stCheck('基线含 config/env.js 内的绝对地址（R5 必须豁免 env.js）', /http:\/\/127\.0\.0\.1:8000\/api\/v1/.test(FIXTURE['config/env.js']))

  // ---------------------------------------------- 六条规则的阴性对照 -----
  console.log('\n[阴性对照] 每条规则注入一处最小违规（跑的是同一个 scan()）')
  const controls = []

  // R1：tap 处理器改读 e.detail.value（dataset 索引 → detail.value）
  controls.push([
    'R1',
    () => {
      const m = mutate('pages/demo/demo.js', 'Number(e.currentTarget.dataset.index)', 'Number(e.detail.value)')
      return m
    },
    'R1_TAP_DETAIL_VALUE',
    ['tap-detail-value'],
  ])
  // R2：把 onReachBottom 注释掉（修复只存在于注释 → 仍须被抓）
  controls.push([
    'R2',
    () => mutate('pages/demo/demo.js', '  onReachBottom() {\n    this.fetch(this.data.page + 1)\n  },', '  // onReachBottom() { this.fetch(this.data.page + 1) },'),
    'R2_MISSING_LOWER_TRIGGER',
    ['missing-hook'],
  ])
  // R3：注入 'Y-m-d H:i:s' 字符串直解
  controls.push([
    'R3',
    () => mutate('pages/demo/demo.js', "today: '' },", "today: '', stamp: new Date('2026-09-12 10:00:00') },"),
    'R3_DATETIME_STRING_PARSE',
    ['date-literal'],
  ])
  // R4：列表字段不兜底，直接当数组用
  controls.push([
    'R4',
    () => mutate('pages/demo/demo.js', '((res && res.items) || []).map', 'res.items.map'),
    'R4_LIST_FIELD_NO_FALLBACK',
    ['list-array-use'],
  ])
  // R5：页面里写死 BASE_URL + 绝对地址
  controls.push([
    'R5',
    () => mutate('pages/demo/demo.js', "const { request } = require('../../services/request')", "const { request } = require('../../services/request')\nconst BASE_URL = 'http://127.0.0.1:8000/api/v1'"),
    'R5_HARDCODED_BASE_URL',
    ['base-url-ident', 'url-literal'],
  ])
  // R6：新增未注册页面 + 跳转目标未注册
  controls.push([
    'R6',
    () => {
      const m = mutate('pages/demo/demo.js', "wx.navigateTo({ url: '/pages/demo/extra' })", "wx.navigateTo({ url: '/pages/ghost/ghost' })")
      if (!m.applied) return m
      m.files['pages/ghost/ghost.js'] = 'Page({ data: {}, onLoad() {} })\n'
      m.files['pages/ghost/ghost.wxml'] = '<view>ghost</view>\n'
      return m
    },
    'R6_PAGE_REGISTRATION',
    ['page-unregistered', 'nav-target-unregistered'],
  ])

  let controlsPassed = 0
  for (const [label, make, ruleId, kinds] of controls) {
    const m = make()
    if (!stCheck(`${label} 突变可用（fixture 确实被改写）`, m.applied, m.note)) continue
    if (expectSingleRule(`${label} 反证`, m.files, ruleId, kinds)) controlsPassed += 1
  }

  console.log(`\n${controlsPassed}/${controls.length} negative controls PASS`)

  // ---------------------------------------------- 附加对照（分面/误报）----
  console.log('\n[附加对照] 规则分面与误报控制')
  // R2 替代触发：scroll-view 的 bindscrolltolower 应被接受（不得误报）
  {
    const m = mutate('pages/demo/demo.js', '  onReachBottom() {\n    this.fetch(this.data.page + 1)\n  },', '')
    if (stCheck('R2b 突变可用', m.applied, m.note)) {
      m.files['pages/demo/demo.wxml'] = m.files['pages/demo/demo.wxml'].replace(
        '<view class="page">',
        '<scroll-view class="page" scroll-y bindscrolltolower="onReachBottom">'
      )
      const r = runOnFixture(m.files)
      stCheck('R2b 改用 scroll-view bindscrolltolower 时 **不**误报', r.violations.length === 0, r.violations.map((v) => v.rule + ' ' + v.evidence).join(' | '))
    }
  }
  // R3 分面：后端时间字段直解
  {
    const m = mutate('pages/demo/demo.js', 'this.setData({ today: today() })', 'this.setData({ today: today(), stamp: new Date(item.publish_time) })')
    if (stCheck('R3b 突变可用', m.applied, m.note)) {
      const r = runOnFixture(m.files)
      const mine = r.violations.filter((v) => v.rule === 'R3_DATETIME_STRING_PARSE' && v.kind === 'date-field')
      stCheck('R3b 后端时间字段直解被抓（kind=date-field）', mine.length === 1 && r.violations.length === 1, r.violations.map((v) => v.rule + '/' + v.kind).join(' | '))
    }
  }
  // R4 分面：整块赋值未兜底
  {
    const m = mutate('pages/demo/demo.js', 'this.setData({ items, page })', 'this.setData({ items: res.items, page })')
    if (stCheck('R4b 突变可用', m.applied, m.note)) {
      const r = runOnFixture(m.files)
      const mine = r.violations.filter((v) => v.rule === 'R4_LIST_FIELD_NO_FALLBACK' && v.kind === 'list-assign')
      stCheck('R4b 整块赋值未兜底被抓（kind=list-assign）', mine.length === 1 && r.violations.length === 1, r.violations.map((v) => v.rule + '/' + v.kind).join(' | '))
    }
  }
  // R5 豁免：env.js 内的绝对地址不得误报（基线已覆盖，这里显式断言分面）
  {
    const r = runOnFixture(cloneFixture())
    stCheck('R5b config/env.js 的 develop 默认地址不被误报', r.violations.filter((v) => v.rule === 'R5_HARDCODED_BASE_URL').length === 0)
  }
  // 注释诱饵：把 base URL 地址写字面量放进注释 → 不得触发 R5
  {
    const m = mutate('pages/demo/demo.js', '// const BASE_URL', "// const REAL = 'http://10.0.0.9:8000/api/v1'\n// const BASE_URL")
    if (stCheck('R5c 突变可用', m.applied, m.note)) {
      const r = runOnFixture(m.files)
      stCheck('R5c 注释里的地址字面量不触发违规（注释不参与断言）', r.violations.length === 0, r.violations.map((v) => v.rule + ' ' + v.evidence).join(' | '))
    }
  }

  bar('self-test 汇总')
  const controlsOk = controlsPassed === controls.length
  if (stFail === 0 && controlsOk) {
    console.log(`[PASS] ${controls.length}/${controls.length} negative controls PASS（合计 ${stPass} 项断言全过）`)
    console.log('说明：六条规则各自的最小违规样例都被**对应规则**抓到，且无其它规则跟着命中；')
    console.log('      基线合法样例（含注释诱饵、合法 picker / new Date() / env.js 地址）0 违规。')
    return 0
  }
  console.log(`[FAIL] ${stFail} 项未通过 / negative controls ${controlsPassed}/${controls.length}`)
  return 1
}

// ================================================================ main ====

function argValue(name, fallback) {
  const i = process.argv.indexOf(name)
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback
}

function printHelp() {
  console.log('F22 小程序端静态检查（6 条规则）')
  console.log('')
  console.log('  node tools/verify_miniprogram_static_rules.js              只读扫描 miniprogram/')
  console.log('  node tools/verify_miniprogram_static_rules.js --self-test   六条规则的阴性对照（TEMP fixture）')
  console.log('  node tools/verify_miniprogram_static_rules.js --src <root>  指定仓库根目录')
  console.log('  node tools/verify_miniprogram_static_rules.js --help')
  console.log('')
  console.log('退出码：0 = 六条规则全部通过；1 = 至少一条违规 / 脚本异常')
  printRuleInventory()
}

function main() {
  const argv = process.argv.slice(2)
  if (argv.includes('--help') || argv.includes('-h')) {
    printHelp()
    return 0
  }
  if (argv.includes('--self-test')) {
    return runSelfTest()
  }
  const root = path.resolve(argValue('--src', path.join(__dirname, '..')))
  const mpDir = path.join(root, 'miniprogram')
  if (!fs.existsSync(mpDir)) {
    console.error(`[FAIL] 找不到 miniprogram/ 目录：${mpDir}`)
    console.error('       用 --src <repo-root> 指定仓库根目录。')
    return 1
  }
  const result = scan(mpDir)
  return printReport(result, 'miniprogram')
}

try {
  process.exit(main())
} catch (e) {
  console.error('[FAIL] 脚本异常：', e && e.stack ? e.stack : e)
  process.exit(1)
}
