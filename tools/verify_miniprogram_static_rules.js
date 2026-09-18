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
 *     来源：`docs/项目审计报告20260912序1.md:826`（FRONT-08 · P2：`life/index`、
 *     `life/notices` 仍缺 `onReachBottom`，20 条后无法加载更多）；
 *     `docs/验收测试/A-执行报告-前端体验走查260912.md:21,65,103`（12 个列表页）；
 *     `docs/二阶段整改方案-前端UI重构与后端支撑.md:395`。
 *     口径：只认"页/偏移"类参数（`page`/`pageNo`/`offset`/`skip`…）；
 *     只有 `limit`/`size` 的请求算"只取前 N 条"的预览（本仓首页 `/topics/hot`），不判定。
 *
 *  R3_DATETIME_STRING_PARSE  不得用 `new Date('Y-m-d H:i:s')` 解析时间字符串
 *     来源：`docs/PR39-审查报告260912.md:173`（iOS `Invalid Date` 经典跨端坑）；
 *     任务单 F22 规则③（"本仓改用纯字符串截断"）。
 *     注：走查报告 `docs/验收测试/A-执行报告-前端体验走查260912.md:92,106` 点名的
 *     `pages/library/seat.js:5` 实为**无参** `new Date()`（取当前时间，安全），
 *     故本规则不把它当违规 —— 见 self-test 的合法样例。
 *     另：`new Date('2026-09-12T10:00:00')`（ISO `T` 形式）同样安全，不判。
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
 * 退出码：0 = 六条规则全部通过；1 = 至少一条违规（或脚本/自检自身异常、
 * 或**扫描面无覆盖** —— 扫不到页面入口时拒绝报 PASS，见 docs/CI.md「宁可红灯，不要假绿」）。
 * 输出两档：**违规**（决定退出码）与**覆盖提示 INFO**（说明某处为什么没被判违规，
 * 例如"有 `X.f || []` 兜底但没有 `X &&` 守卫""渲染列表但请求未带分页参数"）。
 * 文件名：任务单写的是 `verify_miniprogram_rules.js`，本仓实际文件是
 * `verify_miniprogram_static_rules.js`（接 CI 时用实际路径）。
 *
 * 跨平台
 * ------
 *  读取文本时统一做 `CRLF/CR → LF` 归一化并去 BOM（本仓 `core.autocrlf=true`，
 *  Windows 工作区是 CRLF、blob 是 LF —— F16 verifier 曾在这里踩过坑）。
 *
 * 本脚本覆盖不到什么（**不要外推**；完整清单见 tools/README.md）
 * ------------------------------------------------------------
 *  ✗ 视觉呈现 / 动画 / 真机性能 / `@supports` 类语法（WXSS 不在官方列举内）→ 真机抽测；
 *  ✗ 运行期行为（请求是否真的发出、状态机是否正确）→ 见各特性 `verify_*.js`；
 *  ✗ 后端接口语义与字段是否真的存在；
 *  ✗ 事件处理器写在 `behaviors`/混入对象/箭头函数属性里的情况（R1 只解析页面自身 JS，
 *    未定位到的会在覆盖提示里点名）；
 *  ✗ `scroll-view` 自带 `bindscrolltolower` 之外的自定义分页触发（如按钮"加载更多"）；
 *  ✗ 请求不带分页参数的列表页（R2 不判定，只在覆盖提示里列出）；
 *  ✗ 后端时间字段实际类型为数字时间戳时，R3 的"字段直解"分面可能误报（见 R3 说明）；
 *  ✗ 列表字段不在本仓既有集合口径内的新命名（R4 口径自校准，见 `collectCollectionNames`）；
 *  ✗ R5 只看 JS（WXML/WXSS 的外链不判定）；R6 只认字符串字面量跳转目标。
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
    source: 'FRONT-08 docs/项目审计报告20260912序1.md:826（审计表列 P2；总表 docs/成员任务单-二阶段整改260912.md:363 列为 P1）· 走查报告:103 · 任务单 F22 规则②',
    fix: '补 onReachBottom() { this.fetch(this.data.page + 1) }，或在 scroll-view 上加 bindscrolltolower',
  },
  {
    id: 'R3_DATETIME_STRING_PARSE',
    title: "不得用 new Date('Y-m-d H:i:s') 解析时间字符串（iOS → Invalid Date）",
    source: 'docs/PR39-审查报告260912.md:173 · 任务单 F22 规则③（"本仓改用纯字符串截断"）；见脚本头部对 seat.js 的说明',
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
// R1 认定的"点击类"事件（bindtap / catch:tap / bindlongpress …）
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

/** 行尾归一化（去 BOM + CRLF/CR → LF）；readText 与 self-test fixture 共用 */
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
 * 字符串/模板串与正则字面量内的 `//` 不算注释。正则字面量的起始位置用
 * "上一有效字符 / 上一关键字"启发式识别（`= ( , : [ ! & | ? { } ;` 之后，
 * 或 `return`/`typeof`/`case`/`in`/`of`/`void`/`delete`/`await` 等关键字之后才是正则）；
 * 正则**整体连同其内容**一并抹掉 —— 否则正则里的 `'` 会被当成字符串开头，
 * 导致后面的注释没被剥掉（实测踩过：注释里的 `BASE_URL` 被当成代码报违规）。
 */
function stripComments(src) {
  const out = src.split('')
  const n = src.length
  const blank = (i) => {
    if (src[i] !== '\n') out[i] = ' '
  }
  let i = 0
  let prev = '' // 上一个非空白有效字符（用于区分正则 / 除号）
  let prevWord = '' // 上一个标识符（关键字后可以跟正则字面量）
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
      prevWord = ''
      continue
    }
    if (c === '/' && (prev === '' || REGEX_PREFIX_CHARS.indexOf(prev) !== -1 || REGEX_PREFIX_WORDS.indexOf(prevWord) !== -1)) {
      // 正则字面量：整体抹掉（含内容），避免其中的引号把后续注释"吞掉"
      blank(i) // 开头的 '/'
      i += 1
      while (i < n && src[i] !== '\n') {
        if (src[i] === '\\') {
          blank(i)
          blank(i + 1)
          i += 2
          continue
        }
        if (src[i] === '/') {
          blank(i)
          i += 1
          break
        }
        blank(i)
        i += 1
      }
      prev = '/'
      prevWord = ''
      continue
    }
    if (/[A-Za-z_$]/.test(c)) {
      let j = i
      while (j < n && /[\w$]/.test(src[j])) j += 1
      prev = src[j - 1]
      prevWord = src.slice(i, j)
      i = j
      continue
    }
    if (!/\s/.test(c)) {
      prev = c
      prevWord = ''
    }
    i += 1
  }
  return out.join('')
}

// 正则字面量可以出现在这些字符 / 关键字之后（其余情况 `/` 是除号）
const REGEX_PREFIX_CHARS = '(,=:[!&|?{};+-*%~^<>'
const REGEX_PREFIX_WORDS = [
  'return',
  'typeof',
  'case',
  'in',
  'of',
  'delete',
  'void',
  'instanceof',
  'yield',
  'await',
  'do',
  'else',
]

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
  // 不吞目录读取错误：读不到就必须红脸报错，否则会出现"没检查却报 PASS"的假绿
  const entries = fs.readdirSync(dir, { withFileTypes: true })
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
  const notes = [] // 覆盖提示：不计入退出码，只暴露静态分析的边界（见 README「盲区」）
  rule1TapDetailValue(ctx, violations, notes)
  rule2MissingLowerTrigger(ctx, violations, notes)
  rule3DatetimeStringParse(ctx, violations)
  rule4ListFieldNoFallback(ctx, violations, notes)
  rule5HardcodedBaseUrl(ctx, violations)
  rule6PageRegistration(ctx, violations)

  violations.sort((a, b) => (a.rule === b.rule ? a.file.localeCompare(b.file) || a.line - b.line : a.rule.localeCompare(b.rule)))
  return {
    violations,
    notes,
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

/** 违规项（决定退出码）。line = 0 表示"该违规不属于某一行"（如 app.json 级别的注册问题、聚合项） */
function violation(rule, file, line, evidence, kind) {
  return { rule, file, line, evidence: clip(evidence), kind, fix: RULE_BY_ID[rule].fix }
}

/** 覆盖提示（INFO）：说明某处为什么没被判违规 —— 不参与退出码（line = 0 同上） */
function note(rule, file, line, text, kind) {
  return { rule, file, line, text: clip(text, 220), kind }
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

function rule1TapDetailValue(ctx, out, notes) {
  for (const page of ctx.pages) {
    const wxmlFile = ctx.byRel[page.rel.replace(/\.js$/, '.wxml')]
    if (!wxmlFile) continue
    const handlers = tapHandlers(stripWxmlComments(readText(wxmlFile.abs)))
    if (!handlers.length) continue
    const src = code(page.abs)
    const unresolved = []
    for (const name of handlers) {
      const fn = extractFunctionBody(src, name)
      if (!fn) {
        // 处理器不在页面 JS 的可解析形态里（behaviors / 混入 / 箭头函数属性）→ 静态盲区
        unresolved.push(name)
        continue
      }
      const hit = /\bdetail\s*\.\s*value\b/.exec(fn.body)
      if (!hit) continue
      const at = fn.start + hit.index
      out.push(
        violation('R1_TAP_DETAIL_VALUE', page.rel, lineAt(src, at), `${name} 内：${lineTextAt(src, at)}`, 'tap-detail-value')
      )
    }
    if (unresolved.length) {
      notes.push(
        note(
          'R1_TAP_DETAIL_VALUE',
          page.rel,
          0,
          `${unresolved.length} 个 tap 处理器未能在页面 JS 中定位（箭头函数属性 / behaviors / 混入），本规则未检查：${unresolved.join(', ')}`,
          'r1-unresolved-handler'
        )
      )
    }
  }
}

// ---------------------------------------------------------------- R2 ----

/** 页面是否渲染服务端列表（WXML 有 wx:for） */
function rendersList(ctx, page) {
  const wxmlFile = ctx.byRel[page.rel.replace(/\.js$/, '.wxml')]
  if (!wxmlFile) return false
  return /\bwx:for\s*=/.test(stripWxmlComments(readText(wxmlFile.abs)))
}

/** 把字符串/模板串内容抹成空格（保留长度），避免"字符串里出现 page 就算分页"这类误判 */
function blankStrings(src) {
  return src.replace(/(['"`])(?:\\.|(?!\1)[^\\])*\1/g, (m) => m.replace(/[^\n]/g, ' '))
}

/**
 * 请求实参里是否带**分页语义**的参数（只看代码，不看字符串内容）。
 *
 * 口径：只认"页/偏移"类参数（`page`/`pageNo`/`offset`/`skip`…）。
 * **刻意不含 `limit` / `size`** —— 单独一个 `limit: 5` 是"只取前 N 条"的预览
 * （本仓 `pages/index/index.js` 的 `/topics/hot` 就是），不是分页，判它会误报。
 */
function hasPaginationParam(args) {
  const codeOnly = blankStrings(args)
  return /\b(?:page|pageNo|pageNum|pageIndex|page_no|page_num|offset|skip)\b/.test(codeOnly)
}

function rule2MissingLowerTrigger(ctx, out, notes) {
  const unverifiable = []
  for (const page of ctx.pages) {
    const src = code(page.abs)
    const hasRequest = findCalls(src, 'request').length > 0
    const paginated = findCalls(src, 'request').find((c) => hasPaginationParam(c.args))
    if (!paginated) {
      // 不传分页参数的列表页无法静态判定"能不能加载更多"（可能后端根本不支持分页）→ 只登记盲区
      if (hasRequest && rendersList(ctx, page)) unverifiable.push(page.rel.replace(/^pages\//, '').replace(/\.js$/, ''))
      continue
    }
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
  if (unverifiable.length) {
    notes.push(
      note(
        'R2_MISSING_LOWER_TRIGGER',
        '(多页)',
        0,
        `另有 ${unverifiable.length} 个页面渲染列表但请求未带分页参数，R2 不判定（属静态盲区，需人工确认后端是否支持分页）：${unverifiable.join(', ')}`,
        'r2-unverifiable'
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
        const strLit = /^(['"`])([\s\S]*)\1$/.exec(arg)
        if (strLit) {
          const value = strLit[2]
          if (value.indexOf('${') !== -1) continue // 模板串插值：静态判不了
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
 * 「集合字段」口径自校准：凡在本仓以 `X.f || []` / `Y.f.map(...)` 这类**数组用法**
 * 出现过的字段名，都算列表字段。
 *
 * 两个约束（防止把一个随便什么对象的字段名污染成"列表字段"）：
 *   - `X.f || []`：接收者不限 —— 写 `|| []` 本身就说明"这里期望它是数组"；
 *   - `Y.f.map(...)`：接收者只认 `this.data`（页面态）或**本文件的响应对象变量**
 *     （否则 `cfg.plan.map(...)` 会把 `plan` 变成列表字段，进而误报 `res.plan.length`）。
 */
function collectCollectionNames(jsFiles) {
  const names = new Set(COLLECTION_SEED)
  const chain = '(?:^|[^\\w$.])([A-Za-z_$][\\w$]*)\\s*\\.\\s*([A-Za-z_$][\\w$]*)'
  const methods = '(?:map|forEach|filter|concat|slice|find|findIndex|some|every|reduce|join)'
  const re1 = new RegExp(chain + '\\s*\\)*\\s*\\|\\|\\s*\\[\\s*\\]', 'g')
  const re2 = new RegExp('this\\s*\\.\\s*data\\s*\\.\\s*([A-Za-z_$][\\w$]*)\\s*\\.\\s*' + methods + '\\b', 'g')
  const re3 = new RegExp(chain + '\\s*\\.\\s*' + methods + '\\b', 'g')
  for (const f of jsFiles) {
    const src = code(f.abs)
    const binds = collectResponseBindings(src)
    findAll(src, re1).forEach((m) => names.add(m[2]))
    findAll(src, re2).forEach((m) => names.add(m[1]))
    findAll(src, re3).forEach((m) => {
      if (binds.has(m[1])) names.add(m[2])
    })
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

/** 正则转义 */
function esc(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** 取 index 之前的**同一条语句**片段（以 `;` `{` `}` 或换行为界） */
function sameStatementBefore(src, index) {
  const window = src.slice(Math.max(0, index - 240), index)
  const cut = Math.max(
    window.lastIndexOf(';'),
    window.lastIndexOf('{'),
    window.lastIndexOf('}'),
    window.lastIndexOf('\n')
  )
  return cut === -1 ? window : window.slice(cut + 1)
}

/**
 * 该位置是否被 `if (X && X.f ...) {` / `if (X && X.f ...)` 这类**单一守卫块**包着。
 * 用于 `list-assign` 分面：块内取值不会再抛，不该判违规（实测曾误报）
 * 例：`if (res && res.items.length) { this.setData({ items: res.items }) }`
 *
 * 判据：向上找最近的 `if/while/for (...) {`，且它的 `{` 到当前位置**没有再闭合**
 * （说明这个块还开着）—— 这样"守卫块结束后另起一条语句"不会被误放行。
 */
function enclosingGuardText(src, index, guardTest) {
  const before = src.slice(Math.max(0, index - 400), index)
  const re = /(?:^|[;{}\n])\s*(?:else\s+)?(?:if|while|for)\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)\s*\{/g
  const opened = []
  let m
  while ((m = re.exec(before)) !== null) opened.push(m)
  for (let i = opened.length - 1; i >= 0; i -= 1) {
    const afterBrace = before.slice(opened[i].index + opened[i][0].length)
    if (afterBrace.indexOf('}') !== -1) return '' // 该块已闭合，不构成包裹
    return guardTest(opened[i][1]) ? opened[i][1] : ''
  }
  // 无大括号：`if (...)` 换行后直接跟语句
  const trimmed = before.replace(/[ \t]+$/, '')
  const bare = /(?:^|[;{}\n])\s*(?:else\s+)?(?:if|while|for)\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)\s*$/.exec(trimmed)
  if (bare && guardTest(bare[1])) return bare[1]
  return ''
}

function rule4ListFieldNoFallback(ctx, out, notes) {
  const colls = Array.from(ctx.collectionNames)
  const weakGuards = []
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

        // 分类口径（四条互斥分支，顺序不能换 —— 每条都有对应的合法写法做对照）：
        //
        //   ① 已有兜底        `X.f || []` / `(X && X.f) || []`            → 放行
        //   ② 未守卫 + 直接当数组用  `X.f.map(...)` / `X.f[0]` / `X.f.length` → 违规
        //   ③ 括号闭合后再当数组用   `(X && X.f).map(...)`                 → 违规
        //      （X 为 null 时 `X && X.f` 是 null，`.map` 照样抛）
        //   ④ 前置守卫 `X && X.f` 且非上述用法（`X && X.f.length`、
        //      `X && X.f.map(...)`）→ 放行（&& 短路，安全）
        //   ⑤ 整块赋值且值表达式到此结束（`,` `;` `)` `}` `]` 或换行）→ 违规
        const afterS = after.replace(/^[ \t]+/, '')
        const stmtBefore = sameStatementBefore(src, start)
        const B = esc(bind)
        const F = esc(field)
        // 同语句内的守卫：`X && X.f`，以及 `X.f && X.f.length` 这类"先判空再取长度"
        // （后者曾实测误报，故必须按语句作用域识别，而不是只看紧邻的 `&&`）
        const guardRe = new RegExp(
          '(?:^|[^\\w$.])' +
            '(?:' +
            B +
            '\\s*&&\\s*(?:' +
            B +
            '\\s*\\.\\s*data\\s*\\.\\s*|' +
            B +
            '\\s*\\.\\s*)' +
            F +
            '\\b' +
            '|' +
            B +
            '\\s*\\.\\s*' +
            F +
            '\\s*&&\\s*' +
            B +
            '\\s*\\.\\s*' +
            F +
            '\\s*\\.\\s*length\\b' +
            ')'
        )
        const guarded = /&&\s*$/.test(before) || guardRe.test(stmtBefore)
        if (/^\s*\)*\s*\|\|/.test(afterS)) {
          // ① / ①'：有兜底即放行；接收者未守卫的（`X.f || []`）登记覆盖提示
          if (!guarded) weakGuards.push(`${file.rel}:${line}`)
          continue
        }
        const directUse = ARRAY_USE_RE.test(afterS)
        const parenUse = /^\)/.test(afterS) && ARRAY_USE_RE.test(afterS.replace(/^\)+[ \t]*/, ''))
        if (directUse && !guarded) {
          out.push(violation('R4_LIST_FIELD_NO_FALLBACK', file.rel, line, `直接当数组用：${evidence}`, 'list-array-use'))
          continue
        }
        if (parenUse) {
          out.push(violation('R4_LIST_FIELD_NO_FALLBACK', file.rel, line, `守卫后仍当数组用：${evidence}`, 'list-array-use'))
          continue
        }
        if (guarded) continue // ④
        const endsValue =
          /^[,;)\]}]/.test(afterS) || afterS === '' || (/^\r?\n/.test(afterS) && !/^\r?\n[ \t]*(?:\.|\|\||&&|\?)/.test(afterS))
        if (endsValue && /[:=]\s*$/.test(before)) {
          // ⑤ 整块赋值：若被 `if (X && X.f ...) {` 这种单一守卫块包着，块内取值是安全的
          if (enclosingGuardText(src, start, (cond) => guardRe.test(cond))) continue
          out.push(violation('R4_LIST_FIELD_NO_FALLBACK', file.rel, line, `整块赋值未兜底：${evidence}`, 'list-assign'))
        }
      }
    }
  }
  if (weakGuards.length) {
    notes.push(
      note(
        'R4_LIST_FIELD_NO_FALLBACK',
        '(多文件)',
        0,
        `有 ${weakGuards.length} 处用 \`X.f || []\` 兜底但接收者未做 \`X &&\` 守卫（字段缺失已兜住；X 为 null 时仍会抛）。按"有兜底即放行"的口径**未判违规**，登记以便人工决定是否统一为 \`(X && X.f) || []\`：${weakGuards.join(', ')}`,
        'r4-weak-guard'
      )
    )
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
  // (d) 跳转目标必须是已注册页面（字符串字面量形式 `/pages/x/y`，允许后面跟 ?query 或继续拼接）
  const targetRe = /(['"])(\/pages\/[A-Za-z0-9_-]+\/[A-Za-z0-9_/-]*)/g
  for (const file of ctx.jsFiles.concat(ctx.wxmlFiles)) {
    const src = file.rel.endsWith('.wxml') ? stripWxmlComments(readText(file.abs)) : code(file.abs)
    for (const m of findAll(src, targetRe)) {
      const target = m[2].slice(1).replace(/\/$/, '')
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
  const { violations, notes, stats } = result
  bar(`F22 小程序端静态检查 · 只读扫描 ${mpLabel}`)
  console.log(`文件：${stats.js} js / ${stats.wxml} wxml / 页面入口 ${stats.pages}`)
  console.log(`行尾已归一化（CRLF→LF）、BOM 已去；注释不参与断言`)
  console.log(`R4 集合字段口径（自校准）：${stats.collectionNames.join(', ') || '（空）'}`)

  printRuleInventory()

  // 覆盖下限：什么都没扫到时**不能报 PASS**（docs/CI.md「宁可红灯，不要假绿」）
  if (stats.js === 0 || stats.pages === 0) {
    bar()
    console.log(`[FAIL] 扫描面无覆盖：${mpLabel} 下发现 ${stats.js} 个 js / ${stats.pages} 个页面入口（pages/**/*.js + 同名 .wxml）`)
    console.log('       —— 拒绝在"没检查"的情况下给出 PASS；请确认扫描根目录是否正确。')
    bar()
    return 1
  }

  if (!violations.length) {
    printRuleSummary({})
    printNotes(notes, mpLabel)
    bar()
    console.log(`[PASS] 六条规则全部通过：6/6 无违规${notes.length ? `（另有 ${notes.length} 条覆盖提示，见上，不计入退出码）` : ''}`)
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
  printRuleSummary(counts)
  console.log(`\n[FAIL] ${violations.length} 处违规，涉及 ${Object.keys(counts).length}/${RULES.length} 条规则`)
  printNotes(notes, mpLabel)
  console.log('\n⚠️ 六条规则只覆盖"已知坑类"；视觉/真机/运行期行为仍需人工与各特性 verifier。')
  return 1
}

/** 每条规则一行结论（PASS / FAIL 两条路径都打，口径与其它 verify_*.js 一致） */
function printRuleSummary(counts) {
  RULES.forEach((r) => {
    const n = counts[r.id] || 0
    console.log(`  ${n ? '[NG]' : '[OK]'} ${r.id}${n ? '  → ' + n + ' 处' : ''}`)
  })
}

/** 覆盖提示（INFO）：说明静态分析在哪里"看不见"，不参与退出码 */
function printNotes(notes, mpLabel) {
  if (!notes || !notes.length) return
  bar(`覆盖提示（INFO · ${notes.length} 条 · 不计入退出码）`)
  notes.forEach((n) => {
    console.log(`\n[提示] ${n.rule}`)
    console.log(`  位置: ${n.file === '(多页)' || n.file === '(多文件)' ? n.file : `${mpLabel}/${n.file}${n.line ? ':' + n.line : ''}`}`)
    console.log(`  说明: ${n.text}`)
  })
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

// 合法样例：正则里带单引号 —— 注释解析不得因此错位（曾把下面的诱饵注释当成代码报 R5）
function isQuoted(s) {
  return /'/.test(s)
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
        // 合法变体（都曾让 R4 误报，必须全部放行）：
        const total = res.items && res.items.length ? res.items.length : 0
        const fallback = res.items || []
        const safe = (res && res.total) || 0
        const guarded = res.items && res.items.map((it) => it.id)
        if (res && res.items.length) {
          // 守卫块内整块赋值是安全的（也曾误报）
          this.setData({ items: res.items })
        }
        this.setData({ items, page, total, fallbackCount: fallback.length, safe, guardedCount: guarded.length })
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

function makeFixtureDir(files, opts) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'xjt-f22-'))
  const eol = opts && opts.crlf ? '\r\n' : '\n'
  for (const rel of Object.keys(files)) {
    const abs = path.join(dir, 'miniprogram', rel)
    fs.mkdirSync(path.dirname(abs), { recursive: true })
    const content = normalizeEol(files[rel]).replace(/\n/g, eol)
    fs.writeFileSync(abs, content, 'utf8')
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

function runOnFixture(files, opts) {
  const dir = makeFixtureDir(files, opts)
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
  stCheck(
    '基线含 R4 三个"曾误报"的合法变体（先判空再取长度 / 无守卫兜底 / 非集合字段）',
    /res\.items && res\.items\.length \? res\.items\.length : 0/.test(demoJs) &&
      /const fallback = res\.items \|\| \[\]/.test(demoJs) &&
      /const safe = \(res && res\.total\) \|\| 0/.test(demoJs)
  )
  stCheck('基线的"无守卫兜底"只登记覆盖提示、不计入违规（notes 通道生效）', base.notes.length >= 1, `notes=${base.notes.length}`)
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
    const m = mutate('pages/demo/demo.js', 'this.setData({ items, page, total', 'this.setData({ items: res.items, page, total')
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
  // R5d 解析错位：正则字面量里的单引号不得让后面的注释被当成代码（实测踩过的假违规）
  {
    const m = mutate(
      'pages/demo/demo.js',
      "function isQuoted(s) {\n  return /'/.test(s)\n}",
      "function isQuoted(s) {\n  return /'/.test(s)\n}\n// const LEAK = 'http://10.0.0.9:8000/api/v1'"
    )
    if (stCheck('R5d 突变可用', m.applied, m.note)) {
      const r = runOnFixture(m.files)
      stCheck('R5d 正则后的注释不得被当成代码（不误报 R5）', r.violations.length === 0, r.violations.map((v) => v.rule + ' ' + v.evidence).join(' | '))
    }
  }
  // R2d 分页口径：只有 limit（预览）不算分页；pageNo（页号）必须算分页
  {
    const drop = mutate('pages/demo/demo.js', '  onReachBottom() {\n    this.fetch(this.data.page + 1)\n  },', '')
    const noHook = drop.files['pages/demo/demo.js']
    const withPageNo = noHook.replace('{ data: { page, size: 20 } }', '{ data: { pageNo: page, size: 20 } }')
    const withLimit = noHook.replace('{ data: { page, size: 20 } }', '{ data: { limit: 5 } }')
    const applied = drop.applied && withPageNo !== noHook && withLimit !== noHook
    if (stCheck('R2d 突变可用', applied, drop.note)) {
      const r1 = runOnFixture(drop.files)
      const r2 = runOnFixture(Object.assign({}, drop.files, { 'pages/demo/demo.js': withPageNo }))
      const r3 = runOnFixture(Object.assign({}, drop.files, { 'pages/demo/demo.js': withLimit }))
      stCheck('R2d 去掉触底钩子后 page/size 被抓（正对照）', r1.violations.length === 1 && r1.violations[0].rule === 'R2_MISSING_LOWER_TRIGGER', r1.violations.map((v) => v.rule).join(' | '))
      stCheck('R2d pageNo 同样算分页（被抓）', r2.violations.some((v) => v.rule === 'R2_MISSING_LOWER_TRIGGER'), r2.violations.map((v) => v.rule).join(' | '))
      stCheck('R2d 只有 limit:5（首页预览）不算分页，不得误报', r3.violations.filter((v) => v.rule === 'R2_MISSING_LOWER_TRIGGER').length === 0, r3.violations.map((v) => v.rule).join(' | '))
    }
  }
  // R3c 模板串时间字面量同样要被抓
  {
    const m = mutate("pages/demo/demo.js", 'const d = new Date()', 'const d = new Date(`2026-09-12 10:00:00`)')
    if (stCheck('R3c 突变可用', m.applied, m.note)) {
      const r = runOnFixture(m.files)
      const mine = r.violations.filter((v) => v.rule === 'R3_DATETIME_STRING_PARSE' && v.kind === 'date-literal')
      stCheck('R3c 模板串 `Y-m-d H:i:s` 被抓（kind=date-literal）', mine.length === 1 && r.violations.length === 1, r.violations.map((v) => v.rule + '/' + v.kind).join(' | '))
    }
  }
  // R4c 口径污染：与响应无关的对象字段名不得被当成"列表字段"
  {
    const files = cloneFixture()
    files['pages/demo/demo.js'] = FIXTURE['pages/demo/demo.js']
      .replace('function isQuoted(s) {', "const cfg = { plan: ['a'] }\nconst seededPlan = cfg.plan.map((x) => x)\nfunction isQuoted(s) {")
      .replace('const safe = (res && res.total) || 0', 'const safe = (res && res.total) || 0\n        const n = res.plan.length')
    const applied = /cfg\.plan\.map/.test(files['pages/demo/demo.js']) && /res\.plan\.length/.test(files['pages/demo/demo.js'])
    if (stCheck('R4c 突变可用', applied)) {
      const r = runOnFixture(files)
      stCheck('R4c cfg.plan.map 不得把 plan 变成列表字段（res.plan.length 不误报）', r.violations.filter((v) => v.rule === 'R4_LIST_FIELD_NO_FALLBACK').length === 0, r.violations.map((v) => v.rule + ' ' + v.evidence).join(' | '))
    }
  }
  // R6c 带 query 的跳转目标必须被抓（曾漏报）
  {
    const m = mutate('pages/demo/demo.js', "wx.navigateTo({ url: '/pages/demo/extra' })", "wx.navigateTo({ url: '/pages/ghost/ghost?id=1' })")
    if (stCheck('R6c 突变可用', m.applied, m.note)) {
      const r = runOnFixture(m.files)
      const mine = r.violations.filter((v) => v.kind === 'nav-target-unregistered')
      stCheck('R6c 带 query 的未注册跳转目标被抓', mine.length === 1 && r.violations.length === 1, r.violations.map((v) => v.rule + '/' + v.kind).join(' | '))
    }
  }
  // CRLF：整棵 fixture 换成 CRLF 行尾后，结论（含行号）必须完全一致（F16 踩过的坑）
  {
    const m = mutate('pages/demo/demo.js', 'Number(e.currentTarget.dataset.index)', 'Number(e.detail.value)')
    const lf = runOnFixture(m.files)
    const crlf = runOnFixture(m.files, { crlf: true })
    const lfHit = lf.violations.filter((v) => v.rule === 'R1_TAP_DETAIL_VALUE')
    const crlfHit = crlf.violations.filter((v) => v.rule === 'R1_TAP_DETAIL_VALUE')
    stCheck('CRLF 基线（整树 CRLF）仍然 0 违规', runOnFixture(cloneFixture(), { crlf: true }).violations.length === 0)
    stCheck(
      'CRLF 下 R1 注入仍被抓，且行号与 LF 一致',
      lfHit.length === 1 && crlfHit.length === 1 && crlfHit[0].line === lfHit[0].line,
      `LF=${lfHit.map((v) => v.line).join(',')} CRLF=${crlfHit.map((v) => v.line).join(',')}`
    )
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
