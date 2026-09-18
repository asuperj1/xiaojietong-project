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
 *     node tools/verify_miniprogram_static_rules.js                  # 门禁模式（默认读 baseline）
 *     node tools/verify_miniprogram_static_rules.js --strict         # 严格模式：忽略 baseline，任何违规都失败
 *     node tools/verify_miniprogram_static_rules.js --baseline <f>   # 指定 baseline 文件
 *     node tools/verify_miniprogram_static_rules.js --list-candidates # R2 候选清单（人工核对）
 *     node tools/verify_miniprogram_static_rules.js --print-baseline  # 打印 baseline 片段（只打印，不写文件）
 *     node tools/verify_miniprogram_static_rules.js --self-test       # 六条规则 + baseline 的阴性对照
 *     node tools/verify_miniprogram_static_rules.js --src <repo-root>
 *
 * 门禁与 baseline（评审 P2）
 * ------------------------
 *  `dev` 上本来就有历史违规（`FRONT-08` 的 3 处 R2）。为了让 CI 门禁"红得有意义"，
 *  脚本把结果分成三类，**只有后两类决定退出码**：
 *
 *    KNOWN  已登记在 `tools/miniprogram_static_rules_baseline.json` 的历史违规 → 打印，不阻塞；
 *    NEW    本次扫描新出现的违规                                        → **阻塞**；
 *    STALE  baseline 里登记、但现在已经不存在的条目                     → **阻塞**（强制清理）。
 *
 *  STALE 也阻塞是刻意的：baseline 一旦"只进不出"就会退化成永久白名单，
 *  修好页面后没人删条目，下一个人还会以为这些页面仍然有问题。
 *  让 STALE 失败 = 强制"修页面"与"删条目"发生在同一个 PR 里（代价是删 1 个 JSON 块）。
 *
 *  匹配身份 = `rule + file + identity`，**不含行号**（行号会随无关改动漂移）。
 *  `identity` 是每条 finding 的稳定身份（如 R1 的 `handler:onCatTap`、
 *  R4 的 `list-array-use:res.items`）；R2 一个页面最多一条，故 identity 为空串。
 *  条目必须写明 `reason`（格式校验会拦住"无理由白名单"）。
 *
 * 退出码：0 = 无违规，或门禁模式下 NEW=0 且 STALE=0；
 *         1 = 有 NEW / 有 STALE / 严格模式下有违规 / **扫描面无覆盖** / baseline 格式错误 / 脚本异常。
 *         （扫不到页面入口时拒绝报 PASS，见 docs/CI.md「宁可红灯，不要假绿」）
 * 输出三档：**违规（KNOWN/NEW/STALE）**、**覆盖提示 INFO**（说明某处为什么没被判违规）、
 *           **R2 候选清单**（`--list-candidates`，与历史走查报告的"12 个列表页"不是同一口径，
 *           见 tools/README.md §「12 vs 17」）。
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
  // 每条规则的"检查计数"：只有规则函数真的走过扫描面才会 > 0 ——
  // 用来堵住"规则被删/没被调用 → 0 命中 → 假绿"（评审实测过的假绿路径）
  ctx.checks = {}
  ctx.collectionNames = collectCollectionNames(jsFiles)
  ctx.responseBindings = {}
  jsFiles.forEach((f) => {
    ctx.responseBindings[f.rel] = collectResponseBindings(code(f.abs))
  })

  const violations = []
  const notes = [] // 覆盖提示：不计入退出码，只暴露静态分析的边界（见 README「盲区」）
  const r2Candidates = [] // R2 的"渲染列表但请求未带分页参数"候选（人工核对清单，不判违规）
  rule1TapDetailValue(ctx, violations, notes)
  rule2MissingLowerTrigger(ctx, violations, notes, r2Candidates)
  rule3DatetimeStringParse(ctx, violations)
  rule4ListFieldNoFallback(ctx, violations, notes)
  rule5HardcodedBaseUrl(ctx, violations)
  rule6PageRegistration(ctx, violations)

  violations.sort((a, b) => (a.rule === b.rule ? a.file.localeCompare(b.file) || a.line - b.line : a.rule.localeCompare(b.rule)))
  return {
    // 同一 rule/file/line 的多个证据合并成一条，避免"违规处数"被重复计数
    violations: mergeFindings(disambiguateIdentities(violations)),
    notes,
    r2Candidates,
    checks: ctx.checks,
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

/**
 * 违规项（决定退出码 / 进入 baseline 分类）。
 *
 * - `line`：1-based；`0` 表示"不属于某一行"（app.json 级问题、聚合项）
 * - `identity`：**稳定身份**（跨行号漂移不变），baseline 用它精确登记某一条 finding；
 *   同一文件同一规则可能有多条（如两个 tap 处理器），只按 `(rule, file)` 登记会互相掩盖
 */
function violation(rule, file, line, evidence, kind, identity) {
  return {
    rule,
    file,
    line,
    evidence: clip(evidence),
    kind,
    identity: identity === undefined || identity === null ? '' : String(identity),
    fix: RULE_BY_ID[rule].fix,
  }
}

/** 覆盖提示（INFO）：说明某处为什么没被判违规 —— 不参与退出码（line = 0 同上） */
function note(rule, file, line, text, kind) {
  return { rule, file, line, text: clip(text, 220), kind }
}

/**
 * 同一 `rule + file + identity` 出现多条时（例如同一文件里两行写了同一个地址字面量），
 * 给第 2 条起追加 `#2`/`#3`… —— 否则两条 finding 会共用同一个 baseline key，
 * 一条条目只能豁免其中一条，另一条永远 NEW（且 `--print-baseline` 会打印重复条目）。
 *
 * ⚠️ 顺序口径：按 `rule/file/行号` 排序后的出现次序。同一文件里插入一条**同身份**的
 * finding 会让后续编号位移（此时 baseline 条目会报 STALE，提示重新登记）。
 */
function disambiguateIdentities(violations) {
  const seen = new Map()
  for (const v of violations) {
    const base = v.identity || ''
    const key = `${v.rule}|${v.file}|${base}`
    const n = (seen.get(key) || 0) + 1
    seen.set(key, n)
    if (n > 1) v.identity = base ? `${base}#${n}` : `#${n}`
  }
  return violations
}

/**
 * 合并"同一 rule + file + line"的多条证据（评审 P3-1）。
 * 典型场景：一行里同时有静态 `BASE_URL` 与写死的地址字面量 —— 那是**一处**缺陷，
 * 不该在"违规处数"里算两次。合并后 evidence 并列展示，identities/kinds 保留全部。
 */
function mergeFindings(violations) {
  const byKey = new Map()
  const merged = []
  for (const v of violations) {
    const key = v.line > 0 ? `${v.rule}|${v.file}|${v.line}` : `${v.rule}|${v.file}|#${v.evidence}`
    const hit = byKey.get(key)
    if (!hit) {
      const copy = Object.assign({}, v, { evidenceAll: [v.evidence], identities: [v.identity], kinds: [v.kind] })
      byKey.set(key, copy)
      merged.push(copy)
      continue
    }
    if (hit.evidenceAll.indexOf(v.evidence) === -1) {
      hit.evidenceAll.push(v.evidence)
      hit.evidence = hit.evidenceAll.join(' ;; ')
    }
    if (hit.identities.indexOf(v.identity) === -1) hit.identities.push(v.identity)
    if (hit.kinds.indexOf(v.kind) === -1) hit.kinds.push(v.kind)
  }
  return merged
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
  let examined = 0
  for (const page of ctx.pages) {
    examined += 1 // 工作单元 = 页面（与是否命中无关，保证"规则真的跑过扫描面"可验证）
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
        violation(
          'R1_TAP_DETAIL_VALUE',
          page.rel,
          lineAt(src, at),
          `${name} 内：${lineTextAt(src, at)}`,
          'tap-detail-value',
          `handler:${name}` // 稳定身份：同一页可能有多个 tap 处理器
        )
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
  ctx.checks.R1_TAP_DETAIL_VALUE = examined
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

/** 从 request 调用实参里取第一个字符串字面量路径（用于 R2 候选清单展示） */
function requestPathOf(callText) {
  const m = /(['"])([^'"]{1,80})\1/.exec(callText)
  return m ? m[2] : ''
}

function rule2MissingLowerTrigger(ctx, out, notes, candidates) {
  const unverifiable = []
  let examined = 0
  for (const page of ctx.pages) {
    examined += 1
    const src = code(page.abs)
    const calls = findCalls(src, 'request')
    const hasRequest = calls.length > 0
    const paginated = calls.find((c) => hasPaginationParam(c.args))
    const wxmlFile = ctx.byRel[page.rel.replace(/\.js$/, '.wxml')]
    const wxmlSrc = wxmlFile ? stripWxmlComments(readText(wxmlFile.abs)) : ''
    const hasHook =
      /(?:^|[\s,{])onReachBottom\s*[:(]/.test(src) || /(?:bind|catch)[:]?scrolltolower\s*=/.test(wxmlSrc)
    if (!paginated) {
      // 不传分页参数的列表页无法静态判定"能不能加载更多"（可能后端根本不支持分页）→ 只登记盲区
      if (hasRequest && rendersList(ctx, page)) {
        unverifiable.push(page.rel.replace(/^pages\//, '').replace(/\.js$/, ''))
        candidates.push({
          file: page.rel,
          hasHook,
          paths: calls.map((c) => requestPathOf(c.text)).filter(Boolean).slice(0, 3),
        })
      }
      continue
    }
    if (hasHook) continue
    out.push(
      violation(
        'R2_MISSING_LOWER_TRIGGER',
        page.rel,
        lineAt(src, paginated.index),
        `分页拉取但无触底钩子：${clip(paginated.text, 120)}`,
        'missing-hook',
        '' // 一个页面最多一条 R2：身份就是 (rule, file)
      )
    )
  }
  if (unverifiable.length) {
    notes.push(
      note(
        'R2_MISSING_LOWER_TRIGGER',
        '(多页)',
        0,
        `另有 ${unverifiable.length} 个页面渲染列表但请求未带分页参数，R2 不判定（属静态盲区，需人工确认后端是否支持分页）：${unverifiable.join(', ')}。可跑 --list-candidates 看逐页清单`,
        'r2-unverifiable'
      )
    )
  }
  ctx.checks.R2_MISSING_LOWER_TRIGGER = examined
}

// ---------------------------------------------------------------- R3 ----

function rule3DatetimeStringParse(ctx, out) {
  const patterns = [
    { re: /\bnew\s+Date\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)/g, kind: 'new-date' },
    { re: /\bDate\s*\.\s*parse\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)/g, kind: 'date-parse' },
  ]
  let examined = 0
  for (const file of ctx.jsFiles) {
    examined += 1
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
              violation(
                'R3_DATETIME_STRING_PARSE',
                file.rel,
                line,
                `字符串时间直解：${evidenceOf()}`,
                'date-literal',
                `date-literal:${clip(value, 40)}#${shortHash(value)}`
              )
            )
          }
          continue
        }
        const last = arg.split('.').pop().trim()
        if (/^[\w$]+$/.test(arg) || /^[\w$]+(\s*\.\s*[\w$]+)+$/.test(arg)) {
          if (BACKEND_TIME_FIELDS.indexOf(last) !== -1) {
            out.push(
              violation(
                'R3_DATETIME_STRING_PARSE',
                file.rel,
                line,
                `后端时间字段直解：${evidenceOf()}`,
                'date-field',
                `date-field:${clip(arg, 60)}#${shortHash(arg)}`
              )
            )
          }
        }
      }
    }
  }
  ctx.checks.R3_DATETIME_STRING_PARSE = examined
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

/**
 * 32 位 FNV-1a（8 位十六进制）。用途：长字面量被 `clip` 截断后仍要能区分彼此 ——
 * 两条 >80 字符、前 80 字符相同的地址字面量若只按截断值做身份，会塌成同一个 key，
 * 一条 baseline 条目就能把两条都豁免掉（评审实测）。
 */
function shortHash(s) {
  let h = 0x811c9dc5
  const str = String(s)
  for (let i = 0; i < str.length; i += 1) {
    h ^= str.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h.toString(16).padStart(8, '0')
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
  let examined = 0
  for (const file of ctx.jsFiles) {
    examined += 1
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
          out.push(
            violation(
              'R4_LIST_FIELD_NO_FALLBACK',
              file.rel,
              line,
              `直接当数组用：${evidence}`,
              'list-array-use',
              `list-array-use:${bind}.${field}`
            )
          )
          continue
        }
        if (parenUse) {
          out.push(
            violation(
              'R4_LIST_FIELD_NO_FALLBACK',
              file.rel,
              line,
              `守卫后仍当数组用：${evidence}`,
              'list-array-use',
              `list-array-use:${bind}.${field}`
            )
          )
          continue
        }
        if (guarded) continue // ④
        const endsValue =
          /^[,;)\]}]/.test(afterS) || afterS === '' || (/^\r?\n/.test(afterS) && !/^\r?\n[ \t]*(?:\.|\|\||&&|\?)/.test(afterS))
        if (endsValue && /[:=]\s*$/.test(before)) {
          // ⑤ 整块赋值：若被 `if (X && X.f ...) {` 这种单一守卫块包着，块内取值是安全的
          if (enclosingGuardText(src, start, (cond) => guardRe.test(cond))) continue
          out.push(
            violation(
              'R4_LIST_FIELD_NO_FALLBACK',
              file.rel,
              line,
              `整块赋值未兜底：${evidence}`,
              'list-assign',
              `list-assign:${bind}.${field}`
            )
          )
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
  ctx.checks.R4_LIST_FIELD_NO_FALLBACK = examined
}

// ---------------------------------------------------------------- R5 ----

function rule5HardcodedBaseUrl(ctx, out) {
  const ENV_REL = 'config/env.js'
  if (!ctx.byRel[ENV_REL]) {
    out.push(violation('R5_HARDCODED_BASE_URL', ENV_REL, 0, '缺少统一地址解析模块 config/env.js', 'missing-env', 'missing-env'))
  }
  let examined = 0
  for (const file of ctx.jsFiles) {
    examined += 1
    if (file.rel === ENV_REL) continue // env.js 是地址的唯一事实来源（http://127.0.0.1 为 develop 默认值）
    const src = code(file.abs)
    for (const m of findAll(src, /\bBASE_URL\b/g)) {
      out.push(
        violation(
          'R5_HARDCODED_BASE_URL',
          file.rel,
          lineAt(src, m.index),
          `静态 BASE_URL：${lineTextAt(src, m.index)}`,
          'base-url-ident',
          'base-url-ident:BASE_URL'
        )
      )
    }
    for (const m of findAll(src, /(['"])(https?:\/\/[^'"\s]*)\1/g)) {
      const url = m[2]
      if (URL_LITERAL_ALLOWLIST.indexOf(url) !== -1) continue
      out.push(
        violation(
          'R5_HARDCODED_BASE_URL',
          file.rel,
          lineAt(src, m.index),
          `写死的地址字面量：${url}`,
          'url-literal',
          `url-literal:${clip(url, 60)}#${shortHash(url)}`
        )
      )
    }
  }
  ctx.checks.R5_HARDCODED_BASE_URL = examined
}

// ---------------------------------------------------------------- R6 ----

function rule6PageRegistration(ctx, out) {
  const appFile = ctx.byRel['app.json']
  if (!appFile) {
    ctx.checks.R6_PAGE_REGISTRATION = ctx.jsFiles.length + ctx.wxmlFiles.length
    out.push(
      violation('R6_PAGE_REGISTRATION', 'app.json', 0, '缺少 app.json（无法校验页面注册）', 'missing-app-json', 'missing-app-json')
    )
    return
  }
  let examined = 0
  const appSrc = readText(appFile.abs)
  let cfg
  try {
    cfg = JSON.parse(appSrc)
  } catch (e) {
    // 解析失败已经是一条 R6 违规 —— 规则确实跑过，检查计数照记，避免被误报成"规则未执行"
    ctx.checks.R6_PAGE_REGISTRATION = (ctx.jsFiles.length + ctx.wxmlFiles.length) || 1
    out.push(
      violation('R6_PAGE_REGISTRATION', 'app.json', 0, `app.json 不是合法 JSON：${e.message}`, 'app-json-parse', 'app-json-parse')
    )
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
    examined += 1
    const key = page.rel.replace(/\.js$/, '')
    if (!registered.has(key)) {
      out.push(
        violation(
          'R6_PAGE_REGISTRATION',
          page.rel,
          1,
          `页面未在 app.json#pages 注册：${key}`,
          'page-unregistered',
          `page-unregistered:${key}`
        )
      )
    }
  }
  // (b) 注册了但文件不存在
  for (const key of registered) {
    const js = ctx.byRel[key + '.js']
    const wxml = ctx.byRel[key + '.wxml']
    if (!js || !wxml) {
      out.push(
        violation(
          'R6_PAGE_REGISTRATION',
          'app.json',
          0,
          `app.json 注册了不存在的页面：${key}（缺 ${!js ? '.js ' : ''}${!wxml ? '.wxml' : ''}）`,
          'page-missing-file',
          `page-missing-file:${key}`
        )
      )
    }
  }
  // (c) TabBar 项必须在 pages 内
  const tabList = (cfg.tabBar && cfg.tabBar.list) || []
  for (const item of tabList) {
    if (!item || !item.pagePath) continue
    if (!registered.has(item.pagePath)) {
      out.push(
        violation(
          'R6_PAGE_REGISTRATION',
          'app.json',
          0,
          `tabBar 项未在 pages 注册：${item.pagePath}`,
          'tabbar-unregistered',
          `tabbar-unregistered:${item.pagePath}`
        )
      )
    }
  }
  // (d) 跳转目标必须是已注册页面（字符串字面量形式 `/pages/x/y`，允许后面跟 ?query 或继续拼接）
  const targetRe = /(['"])(\/pages\/[A-Za-z0-9_-]+\/[A-Za-z0-9_/-]*)/g
  for (const file of ctx.jsFiles.concat(ctx.wxmlFiles)) {
    examined += 1
    const src = file.rel.endsWith('.wxml') ? stripWxmlComments(readText(file.abs)) : code(file.abs)
    for (const m of findAll(src, targetRe)) {
      const target = m[2].slice(1).replace(/\/$/, '')
      if (registered.has(target)) continue
      out.push(
        violation(
          'R6_PAGE_REGISTRATION',
          file.rel,
          lineAt(src, m.index),
          `跳转目标未注册：${m[2]}`,
          'nav-target-unregistered',
          `nav-target-unregistered:${m[2]}`
        )
      )
    }
  }
  ctx.checks.R6_PAGE_REGISTRATION = examined + registered.size
}

/** 六条规则是否**真的执行过**（每条规则的工作单元 > 0）；返回未执行的规则 ID 列表 */
function rulesNotExecuted(checks) {
  const c = checks || {}
  return RULES.filter((r) => !(c[r.id] > 0)).map((r) => r.id)
}

// ============================================================== 报告 ====

const WIDTH = 88

// -------------------------------------------------------------- baseline ----
//
// 门禁语义（评审 P2）：
//   KNOWN（已登记）→ 打印，不阻塞；NEW（新增）→ 阻塞；STALE（基线条目已失效）→ **也阻塞**。
//
// 为什么 STALE 也阻塞（而不是只 warn）：baseline 一旦"只进不出"，就会退化成永久白名单，
// 历史欠账修好了也没人删条目，下一个人还会以为这些页面仍然有问题。
// 让 STALE 失败，等于强制"修好页面"和"删掉条目"发生在同一个 PR 里 —— 代价只是删 1 个 JSON 块。

const BASELINE_DEFAULT_REL = 'tools/miniprogram_static_rules_baseline.json'

/** finding 的稳定身份：rule + file + identity（**不含行号**，行号会随无关改动漂移） */
function findingKey(rule, file, identity) {
  return `${rule}|${file}|${identity || ''}`
}

/**
 * 解析 baseline 文本（纯函数，便于 self-test 直接喂 CRLF 文本做对照）。
 * 返回 { entries, errors }：格式错误一律进 errors（由调用方决定是否硬失败），不静默忽略。
 */
function parseBaselineText(text) {
  const errors = []
  let data = null
  try {
    data = JSON.parse(normalizeEol(text))
  } catch (e) {
    return { entries: [], errors: [`baseline 不是合法 JSON：${e.message}`] }
  }
  const list = data && Array.isArray(data.entries) ? data.entries : null
  if (!list) return { entries: [], errors: ['baseline 缺少 entries 数组'] }
  const entries = []
  list.forEach((raw, i) => {
    const at = `entries[${i}]`
    if (!raw || typeof raw !== 'object') return errors.push(`${at} 不是对象`)
    if (!raw.rule || !RULE_BY_ID[raw.rule]) return errors.push(`${at}.rule 缺失或不是已知规则：${raw.rule}`)
    if (!raw.file || typeof raw.file !== 'string') return errors.push(`${at}.file 缺失`)
    if (typeof raw.identity !== 'string') return errors.push(`${at}.identity 必须是字符串（无稳定身份时写空串 ""）`)
    if (!raw.reason || String(raw.reason).trim().length < 4) return errors.push(`${at}.reason 必须写明登记原因`)
    entries.push({
      rule: raw.rule,
      file: raw.file,
      identity: raw.identity,
      reason: String(raw.reason),
      evidence: raw.evidence ? String(raw.evidence) : '', // 可选：登记时的证据，用于提醒"证据已变化"
      registered: raw.registered ? String(raw.registered) : '',
      ref: raw.ref ? String(raw.ref) : '',
      owner: raw.owner ? String(raw.owner) : '',
      key: findingKey(raw.rule, raw.file, raw.identity),
    })
  })
  const seen = new Set()
  entries.forEach((e) => {
    if (seen.has(e.key)) errors.push(`重复条目：${e.key}`)
    seen.add(e.key)
  })
  return { entries, errors }
}

/**
 * 门禁判定（纯函数，self-test 直接调用 —— 这就是生产用的判定逻辑）。
 * 匹配规则：finding 的 `rule + file + identity` 命中一条**未被占用**的 baseline 条目 → KNOWN；
 * 否则 NEW。未被任何 finding 命中的条目 → STALE。
 */
function evaluateGate(violations, entries) {
  const pool = new Map()
  entries.forEach((e) => {
    if (!pool.has(e.key)) pool.set(e.key, [])
    pool.get(e.key).push(e)
  })
  const known = []
  const fresh = []
  violations.forEach((v) => {
    const ids = v.identities && v.identities.length ? v.identities : [v.identity || '']
    const matched = []
    let allMatched = true
    for (const id of ids) {
      const bucket = pool.get(findingKey(v.rule, v.file, id))
      if (bucket && bucket.length) matched.push(bucket.shift())
      else allMatched = false
    }
    // 合并了多条证据的 finding 必须**全部身份**都已登记才算 KNOWN：
    // 否则同一行新增的另一条缺陷会被已登记的那条顺带掩盖（实测过的洗白路径）。
    // 已命中的条目照常消费掉（不再报 STALE），剩余的按 NEW 处理。
    if (allMatched && matched.length) {
      known.push({
        violation: v,
        entry: matched[0],
        entries: matched,
        evidenceChanged: !!matched[0].evidence && matched[0].evidence !== v.evidence,
      })
    } else {
      fresh.push(v)
    }
  })
  const stale = []
  pool.forEach((bucket) => bucket.forEach((e) => stale.push(e)))
  return { known, fresh, stale, exitCode: fresh.length > 0 || stale.length > 0 ? 1 : 0 }
}

function baselineStatusLabel(gate) {
  return `NEW=${gate.fresh.length} STALE=${gate.stale.length} KNOWN=${gate.known.length}`
}

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

function printReport(result, mpLabel, opts) {
  const { violations, notes, stats, checks } = result
  const options = opts || {}
  const gate = options.gate || null // 有 baseline 时才做 KNOWN/NEW/STALE 分类
  const modeLabel = gate ? `门禁模式（baseline: ${options.baselineLabel}）` : options.strict ? '严格模式（忽略 baseline）' : '只读扫描'
  bar(`F22 小程序端静态检查 · ${modeLabel} · ${mpLabel}`)
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

  // 每条规则必须真的跑过扫描面：规则函数被删/没被调用时，命中数会是 0 —— 那不是"干净"，是"没跑"
  const notRun = rulesNotExecuted(checks)
  if (notRun.length) {
    bar()
    console.log(`[FAIL] 规则未执行（检查计数为 0）：${notRun.join(', ')}`)
    console.log('       —— 命中 0 不等于没有问题；请检查 scan() 是否仍在调用这些规则。')
    bar()
    return 1
  }

  if (options.baselineWarning) {
    console.log(`\n⚠️ ${options.baselineWarning}`)
  }

  if (!violations.length) {
    printRuleSummary({}, gate)
    // 0 违规但 baseline 还有残留条目 = 页面已修好、条目没删 → 必须 STALE 阻塞，不能静默通过
    if (gate && gate.stale.length) {
      printStale(gate, mpLabel)
      printNotes(notes, mpLabel)
      bar()
      console.log(`[FAIL] 基线条目过期（STALE）${gate.stale.length} 条 —— 对应违规已修复，请从 ${options.baselineLabel} 删除这些条目`)
      bar()
      return 1
    }
    printNotes(notes, mpLabel)
    bar()
    console.log(`[PASS] 六条规则全部通过：6/6 无违规${notes.length ? `（另有 ${notes.length} 条覆盖提示，见上，不计入退出码）` : ''}`)
    bar()
    return 0
  }

  const counts = {}
  const newCounts = {}
  violations.forEach((v) => {
    counts[v.rule] = (counts[v.rule] || 0) + 1
  })
  if (gate) gate.fresh.forEach((v) => { newCounts[v.rule] = (newCounts[v.rule] || 0) + 1 })

  if (gate) {
    printKnown(gate, mpLabel)
    printNew(gate, mpLabel)
    printStale(gate, mpLabel)
  } else {
    bar(`违规明细（${violations.length} 处）`)
    violations.forEach((v, i) => {
      console.log(`\n[违规 ${i + 1}/${violations.length}] ${v.rule}`)
      console.log(`  文件: ${mpLabel}/${v.file}${v.line ? ':' + v.line : ''}`)
      console.log(`  身份: ${v.identity || '(rule+file)'}`)
      console.log(`  证据: ${v.evidence}`)
      console.log(`  建议: ${v.fix}`)
    })
  }

  bar('汇总')
  printRuleSummary(counts, gate)
  if (gate) {
    console.log(`\n  基线状态：${baselineStatusLabel(gate)}`)
    if (gate.fresh.length === 0 && gate.stale.length === 0) {
      console.log(`\n[PASS] 门禁通过：无新增违规（KNOWN ${gate.known.length} 处已登记，不阻塞）`)
      printNotes(notes, mpLabel)
      bar()
      return 0
    }
    if (gate.fresh.length) console.log(`\n[FAIL] 新增（NEW）${gate.fresh.length} 处违规 —— 必须修复，或在同一 PR 里登记进 baseline 并写明原因`)
    if (gate.stale.length) {
      console.log(`\n[FAIL] 过期基线条目（STALE）${gate.stale.length} 条 —— 对应违规已不存在，请在 ${options.baselineLabel} 里删除这些条目`)
    }
    printNotes(notes, mpLabel)
    console.log('\n⚠️ 六条规则只覆盖"已知坑类"；视觉/真机/运行期行为仍需人工与各特性 verifier。')
    return 1
  }

  const ruleCount = Object.keys(counts).length
  console.log(`\n[FAIL] ${violations.length} 处违规，涉及 ${ruleCount}/${RULES.length} 条规则`)
  printNotes(notes, mpLabel)
  console.log('\n⚠️ 六条规则只覆盖"已知坑类"；视觉/真机/运行期行为仍需人工与各特性 verifier。')
  return 1
}

function formatFinding(v, mpLabel) {
  return `${v.rule}  ${mpLabel}/${v.file}${v.line ? ':' + v.line : ''}${v.identity ? `  [${v.identity}]` : ''}`
}

/** 已登记的历史违规（KNOWN）：打印且不阻塞门禁 */
function printKnown(gate, mpLabel) {
  if (!gate.known.length) return
  bar(`已知违规 KNOWN（${gate.known.length} 处 · 已登记 · 不阻塞门禁）`)
  gate.known.forEach((k, i) => {
    console.log(`\n[已知 ${i + 1}/${gate.known.length}] ${formatFinding(k.violation, mpLabel)}`)
    console.log(`  登记原因: ${k.entry.reason}`)
    if (k.entry.ref) console.log(`  依据: ${k.entry.ref}${k.entry.owner ? ` · 责任: ${k.entry.owner}` : ''}`)
    console.log(`  证据: ${k.violation.evidence}`)
    if (k.evidenceChanged) {
      console.log(`  ⚠️ 证据已变化（登记时：${clip(k.entry.evidence, 120)}）—— 请复核登记是否仍然准确`)
    }
  })
  const changed = gate.known.filter((k) => k.evidenceChanged).length
  if (changed) console.log(`\n  ⚠️ 其中 ${changed} 条的证据文本与登记时不同（不阻塞，但建议在 PR 里复核）`)
}

/** 新增违规（NEW）：必须阻塞 */
function printNew(gate, mpLabel) {
  bar(`新增违规 NEW（${gate.fresh.length} 处 · 阻塞门禁）`)
  if (!gate.fresh.length) {
    console.log('  （无）')
    return
  }
  gate.fresh.forEach((v, i) => {
    console.log(`\n[新增 ${i + 1}/${gate.fresh.length}] ${formatFinding(v, mpLabel)}`)
    console.log(`  证据: ${v.evidence}`)
    console.log(`  建议: ${v.fix}`)
    if (v.identities && v.identities.length > 1) {
      console.log(`  说明: 该处由同一行 ${v.identities.length} 条证据合并（身份 ${v.identities.join(' / ')}）—— 全部登记后才算 KNOWN`)
    }
    console.log(`  （如确属历史遗留：在 baseline 里登记 rule+file+identity 并写明 reason）`)
  })
}

/** 过期基线条目（STALE）：对应违规已消失，必须清理，否则 baseline 会退化成永久白名单 */
function printStale(gate, mpLabel) {
  bar(`过期基线条目 STALE（${gate.stale.length} 条 · 阻塞门禁 · 请清理）`)
  if (!gate.stale.length) {
    console.log('  （无）')
    return
  }
  gate.stale.forEach((e, i) => {
    console.log(`\n[过期 ${i + 1}/${gate.stale.length}] ${e.rule}  ${mpLabel}/${e.file}${e.identity ? `  [${e.identity}]` : ''}`)
    console.log(`  原登记原因: ${e.reason}`)
    console.log(`  → 该违规已不存在（多半是已修好）：请从 baseline 删除此条目`)
  })
}

/** 每条规则一行结论（PASS / FAIL 两条路径都打，口径与其它 verify_*.js 一致） */
function printRuleSummary(counts, gate) {
  RULES.forEach((r) => {
    const n = counts[r.id] || 0
    const fresh = gate ? gate.fresh.filter((v) => v.rule === r.id).length : n
    const known = gate ? gate.known.filter((k) => k.violation.rule === r.id).length : 0
    const tag = fresh ? '[NG]' : n ? '[OK*]' : '[OK]'
    const detail = gate ? `  → 新增 ${fresh} / 已知 ${known}` : n ? `  → ${n} 处` : ''
    console.log(`  ${tag} ${r.id}${detail}`)
  })
  if (gate) console.log('  （[OK*] = 该规则只有已登记的历史违规，不阻塞门禁）')
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

/**
 * R2 候选清单（评审 P3-2）：渲染了 `wx:for` 列表、但请求里没有分页参数 → R2 **不判定**。
 * 这类页面"要不要加载更多"取决于后端是否分页，属人工核对范围（`F13`/`F17` 的活），
 * 与历史走查报告说的"12 个列表页"不是同一口径 —— 见 tools/README.md §「12 vs 17」。
 */
function printR2Candidates(result, mpLabel) {
  const list = result.r2Candidates || []
  bar(`R2 候选清单（INFO · ${list.length} 个 · 渲染列表但请求未带分页参数 · R2 不判定）`)
  console.log('  口径：静态启发式（WXML 有 wx:for + 有 request 调用 + 请求参数无 page/offset 类字段）')
  console.log('  ⚠️ 与历史走查报告的"12 个列表页"**不是同一口径**，不可直接对号入座（见 tools/README.md）\n')
  list.forEach((c, i) => {
    console.log(`  ${String(i + 1).padStart(2)}. ${mpLabel}/${c.file}`)
    console.log(`      触底钩子: ${c.hasHook ? '已有' : '无'}    请求: ${c.paths.length ? c.paths.join(', ') : '(动态路径)'}`)
  })
  console.log('\n  → 需人工确认后端是否分页；确认需要的，另行补 onReachBottom（属页面任务，不在 F22 工具范围内）')
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

/** 静音跑一段会打印的函数（只用于直接断言生产报告路径的退出码） */
function withSilentConsole(fn) {
  const orig = console.log
  const lines = []
  console.log = (...a) => lines.push(a.map((x) => String(x)).join(' '))
  try {
    return { code: fn(), lines }
  } finally {
    console.log = orig
  }
}

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
  // 合并后的 finding 可能带多个 kind（同一行多条证据），按 kinds 集合判断
  const kindOk = !kinds || kinds.every((k) => mine.some((v) => (v.kinds || [v.kind]).indexOf(k) !== -1))
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

  // ---------------------------------------------- P3-1：同一行不重复计数 ----
  console.log('\n[P3-1 对照] 同一 rule/file/line 的多条证据必须合并成 1 处')
  {
    const m = mutate(
      'pages/demo/demo.js',
      "const { request } = require('../../services/request')",
      "const { request } = require('../../services/request')\nconst BASE_URL = 'http://127.0.0.1:8000/api/v1'"
    )
    if (stCheck('D1 突变可用', m.applied, m.note)) {
      const r = runOnFixture(m.files)
      const r5 = r.violations.filter((v) => v.rule === 'R5_HARDCODED_BASE_URL')
      stCheck(
        'D1 同一行的「静态 BASE_URL + 地址字面量」只算 1 处违规',
        r5.length === 1,
        `R5 处数=${r5.length}（${r5.map((v) => v.kind).join(',')}）`
      )
      stCheck(
        'D1 证据已合并（两条都在同一处里展示）',
        !!r5[0] && /静态 BASE_URL/.test(r5[0].evidence) && /写死的地址字面量/.test(r5[0].evidence),
        r5[0] && r5[0].evidence
      )
      stCheck('D1 两条稳定身份都被保留（baseline 仍可分别登记）', !!r5[0] && r5[0].identities.length === 2, r5[0] && JSON.stringify(r5[0].identities))
    }
  }

  // ---------------------------------------------- baseline（评审 P2）----
  console.log('\n[baseline 对照] KNOWN 不阻塞 / NEW 阻塞 / STALE 被发现 / 不掩盖其它规则')
  const R2_RULE = 'R2_MISSING_LOWER_TRIGGER'
  const entryOf = (rule, file, identity, reason) => ({
    rule,
    file,
    identity,
    reason: reason || '对照用登记原因',
    registered: '2026-09-18',
    ref: '',
    owner: '',
    key: findingKey(rule, file, identity),
  })
  {
    const dropHook = mutate('pages/demo/demo.js', '  onReachBottom() {\n    this.fetch(this.data.page + 1)\n  },', '')
    const r2only = runOnFixture(dropHook.files)
    const r2hits = r2only.violations.filter((v) => v.rule === R2_RULE)
    stCheck('B0 对照夹具产出 1 条 R2（作为 KNOWN 样本）', dropHook.applied && r2hits.length === 1, r2only.violations.map((v) => v.rule).join(' | '))

    // B1 已登记 → 不阻塞
    const g1 = evaluateGate(r2only.violations, [entryOf(R2_RULE, 'pages/demo/demo.js', '')])
    stCheck(
      'B1 已登记的历史违规不阻塞门禁（KNOWN=1 → exit 0）',
      g1.exitCode === 0 && g1.known.length === 1 && g1.fresh.length === 0 && g1.stale.length === 0,
      `NEW=${g1.fresh.length} STALE=${g1.stale.length} exit=${g1.exitCode}`
    )

    // B2/B4 新违规（同一文件里的另一条规则）必须阻塞，baseline 不得掩盖它
    const mixed = Object.assign({}, dropHook.files)
    mixed['pages/demo/demo.js'] = dropHook.files['pages/demo/demo.js'].replace(
      'Number(e.currentTarget.dataset.index)',
      'Number(e.detail.value)'
    )
    const rmixed = runOnFixture(mixed)
    const g2 = evaluateGate(rmixed.violations, [entryOf(R2_RULE, 'pages/demo/demo.js', '')])
    stCheck(
      'B2 同文件里的新增违规（R1）必须阻塞（NEW>=1 → exit 1）',
      g2.exitCode === 1 && g2.fresh.some((v) => v.rule === 'R1_TAP_DETAIL_VALUE'),
      `NEW=${g2.fresh.map((v) => v.rule).join(',')} exit=${g2.exitCode}`
    )
    stCheck(
      'B4 baseline 只豁免登记过的那一条，不掩盖同文件其它规则',
      g2.known.length === 1 && g2.known[0].violation.rule === R2_RULE && g2.fresh.length === 1,
      `KNOWN=${g2.known.map((k) => k.violation.rule).join(',')} NEW=${g2.fresh.map((v) => v.rule).join(',')}`
    )

    // B5 同一规则、同一文件、不同 identity：只登记 A 时 B 仍然是新增
    const twoHandlers = Object.assign({}, mixed)
    twoHandlers['pages/demo/demo.js'] = mixed['pages/demo/demo.js'].replace(
      "onGoExtra() {\n    wx.navigateTo({ url: '/pages/demo/extra' })\n  },",
      'onGoExtra(e) {\n    this.setData({ other: Number(e.detail.value) })\n  },'
    )
    const rtwo = runOnFixture(twoHandlers)
    const r1hits = rtwo.violations.filter((v) => v.rule === 'R1_TAP_DETAIL_VALUE')
    stCheck('B5 对照夹具产出 2 条 R1（不同 handler 身份）', r1hits.length === 2, r1hits.map((v) => v.identity).join(' | '))
    const g5 = evaluateGate(rtwo.violations, [
      entryOf(R2_RULE, 'pages/demo/demo.js', ''),
      entryOf('R1_TAP_DETAIL_VALUE', 'pages/demo/demo.js', 'handler:onCatTap'),
    ])
    stCheck(
      'B5 同一文件同一规则的**另一条** finding 仍是新增（identity 生效）',
      g5.exitCode === 1 && g5.fresh.length === 1 && g5.fresh[0].identity === 'handler:onGoExtra',
      `NEW=${g5.fresh.map((v) => v.identity).join(',')} exit=${g5.exitCode}`
    )

    // B3 基线条目已过期 → STALE 且阻塞
    const g3 = evaluateGate(r2only.violations, [
      entryOf(R2_RULE, 'pages/demo/demo.js', ''),
      entryOf('R6_PAGE_REGISTRATION', 'app.json', 'page-missing-file:pages/demo/ghost'),
    ])
    stCheck(
      'B3 已消失的基线条目报 STALE 且阻塞（防止 baseline 退化成永久白名单）',
      g3.exitCode === 1 && g3.stale.length === 1 && g3.fresh.length === 0,
      `STALE=${g3.stale.length} NEW=${g3.fresh.length} exit=${g3.exitCode}`
    )

    // B6 CRLF：同一份 baseline 用 CRLF 写，结论必须一致（本仓 Windows 检出是 CRLF）
    const baselineText = JSON.stringify({ schema: 1, entries: [entryOf(R2_RULE, 'pages/demo/demo.js', '')] }, null, 2)
    const lfParse = parseBaselineText(baselineText)
    const crlfParse = parseBaselineText(baselineText.replace(/\n/g, '\r\n'))
    stCheck(
      'B6 baseline 文本 CRLF/LF 解析一致（含 BOM）',
      lfParse.errors.length === 0 &&
        crlfParse.errors.length === 0 &&
        parseBaselineText('\uFEFF' + baselineText.replace(/\n/g, '\r\n')).entries.length === 1 &&
        crlfParse.entries.length === lfParse.entries.length &&
        crlfParse.entries[0].key === lfParse.entries[0].key
    )
    const g6 = evaluateGate(r2only.violations, crlfParse.entries)
    stCheck('B6 CRLF baseline 的判定与 LF 相同（KNOWN=1 → exit 0）', g6.exitCode === 0 && g6.known.length === 1, baselineStatusLabel(g6))

    // B7 baseline 必须可审计：格式错误一律报错，不能变成"无理由白名单"
    const badReason = parseBaselineText(JSON.stringify({ entries: [{ rule: R2_RULE, file: 'pages/demo/demo.js', identity: '' }] }))
    const badRule = parseBaselineText(JSON.stringify({ entries: [{ rule: 'R9_NOPE', file: 'a.js', identity: '', reason: '随便写写' }] }))
    const dup = parseBaselineText(
      JSON.stringify({
        entries: [
          { rule: R2_RULE, file: 'pages/demo/demo.js', identity: '', reason: '第一条原因' },
          { rule: R2_RULE, file: 'pages/demo/demo.js', identity: '', reason: '第二条原因' },
        ],
      })
    )
    const broken = parseBaselineText('{ not json')
    stCheck(
      'B7 缺 reason / 未知规则 / 重复条目 / 非法 JSON 都报错',
      badReason.errors.length === 1 && badRule.errors.length === 1 && dup.errors.length === 1 && broken.errors.length === 1,
      JSON.stringify({ badReason: badReason.errors, badRule: badRule.errors, dup: dup.errors, broken: broken.errors })
    )

    // B8 页面都修好了但 baseline 没删条目 → 必须 STALE 阻塞（直接断言生产报告路径的退出码）
    const cleanStats = { js: 2, wxml: 1, pages: 1, collectionNames: [] }
    const cleanResult = { violations: [], notes: [], r2Candidates: [], checks: r2only.checks, stats: cleanStats }
    const staleGate = evaluateGate([], [entryOf(R2_RULE, 'pages/demo/demo.js', '')])
    const cleanRun = withSilentConsole(() =>
      printReport(cleanResult, 'miniprogram', { gate: staleGate, baselineLabel: 'baseline.json' })
    )
    stCheck(
      'B8 零违规但 baseline 有残留条目 → 报 STALE 且退出码 1（不允许静默通过）',
      cleanRun.code === 1 && cleanRun.lines.join('\n').indexOf('STALE') !== -1,
      `exit=${cleanRun.code}`
    )
    const pristineGate = evaluateGate([], [])
    const pristineRun = withSilentConsole(() =>
      printReport(cleanResult, 'miniprogram', { gate: pristineGate, baselineLabel: 'baseline.json' })
    )
    stCheck('B8b 零违规且 baseline 为空 → 正常 PASS（exit 0）', pristineRun.code === 0, `exit=${pristineRun.code}`)

    // C 规则"真的跑过"的不变量：删掉某条规则的调用 → 检查计数为 0 → 必须红（评审实测过的假绿路径）
    stCheck(
      'C1 六条规则在 fixture 上都有检查计数（工作单元 > 0）',
      rulesNotExecuted(r2only.checks).length === 0,
      JSON.stringify(r2only.checks)
    )
    const brokenChecks = Object.assign({}, r2only.checks)
    delete brokenChecks[R2_RULE]
    const notRun = withSilentConsole(() =>
      printReport(Object.assign({}, cleanResult, { checks: brokenChecks }), 'miniprogram', {
        gate: pristineGate,
        baselineLabel: 'baseline.json',
      })
    )
    stCheck(
      'C2 某条规则检查计数为 0（例如调用被删）→ 门禁失败，绝不报 PASS',
      notRun.code === 1 && notRun.lines.join('\n').indexOf('规则未执行') !== -1,
      `exit=${notRun.code}`
    )
    // C3 app.json 坏掉时：R6 既报违规、又必须记为"执行过"（否则会被误报成"规则未执行"）
    const brokenJson = cloneFixture()
    brokenJson['app.json'] = '{ "pages": [ oops\n'
    const rBroken = runOnFixture(brokenJson)
    stCheck(
      'C3 app.json 非法 JSON → 报 R6 违规，且 R6 仍计为已执行',
      rBroken.violations.some((v) => v.kind === 'app-json-parse') && rulesNotExecuted(rBroken.checks).length === 0,
      JSON.stringify(rBroken.checks)
    )

    // B9 登记证据变化 → 只提醒（不阻塞），便于复核"条目还准不准"
    const changedEntry = Object.assign(entryOf(R2_RULE, 'pages/demo/demo.js', ''), { evidence: '登记时的旧证据文本' })
    const g9 = evaluateGate(r2only.violations, [changedEntry])
    stCheck(
      'B9 登记证据与当前不同 → 提示 evidenceChanged 但仍不阻塞',
      g9.exitCode === 0 && g9.known.length === 1 && g9.known[0].evidenceChanged === true,
      `exit=${g9.exitCode} changed=${g9.known[0] && g9.known[0].evidenceChanged}`
    )
  }

  // ---------------------------------------------- 合并/身份：不得互相掩盖 ----
  console.log('\n[合并身份对照] 同一行多证据 / 同身份跨行 不得互相掩盖')
  {
    // D2：同一行两条证据，baseline 只登记其中一条 → 整体仍判 NEW（登记的条目被消费，不额外报 STALE）
    const m = mutate(
      'pages/demo/demo.js',
      "const { request } = require('../../services/request')",
      "const { request } = require('../../services/request')\nconst BASE_URL = 'http://127.0.0.1:8000/api/v1'"
    )
    const r = runOnFixture(m.files)
    const r5 = r.violations.filter((v) => v.rule === 'R5_HARDCODED_BASE_URL')
    if (stCheck('D2 突变可用（同一行两条 R5 证据）', m.applied && r5.length === 1 && r5[0].identities.length === 2, `R5=${r5.length}`)) {
      const partial = evaluateGate(r.violations, [
        Object.assign(entryOf('R5_HARDCODED_BASE_URL', 'pages/demo/demo.js', 'base-url-ident:BASE_URL'), {
          key: findingKey('R5_HARDCODED_BASE_URL', 'pages/demo/demo.js', 'base-url-ident:BASE_URL'),
        }),
      ])
      stCheck(
        'D2 只登记部分身份时，该处仍判 NEW（另一条不得被掩盖），且不额外报 STALE',
        partial.exitCode === 1 && partial.fresh.length === 1 && partial.stale.length === 0,
        `NEW=${partial.fresh.length} STALE=${partial.stale.length} exit=${partial.exitCode}`
      )
      const full = evaluateGate(r.violations, r5[0].identities.map((id) => entryOf('R5_HARDCODED_BASE_URL', 'pages/demo/demo.js', id)))
      stCheck('D2b 两条身份都登记后 → KNOWN（exit 0）', full.exitCode === 0 && full.known.length === 1, baselineStatusLabel(full))
    }
  }
  {
    // D3：同一文件两行写了同一个地址字面量 → 身份必须区分（否则一条 baseline 只能豁免一条）
    const m = mutate(
      'pages/demo/demo.js',
      "const { request } = require('../../services/request')",
      "const { request } = require('../../services/request')\nconst A = 'http://10.0.0.9:8000/api/v1'\nconst B = 'http://10.0.0.9:8000/api/v1'"
    )
    const r = runOnFixture(m.files)
    const r5 = r.violations.filter((v) => v.rule === 'R5_HARDCODED_BASE_URL')
    const ids = r5.map((v) => v.identity)
    if (stCheck('D3 突变可用（两行同一地址字面量）', m.applied && r5.length === 2, `R5=${r5.length}`)) {
      stCheck('D3 同名身份自动消歧（`…#2`），不再互相占用', new Set(ids).size === 2 && ids.some((x) => /#2$/.test(x)), ids.join(' | '))
      const g3 = evaluateGate(r.violations, ids.map((id) => entryOf('R5_HARDCODED_BASE_URL', 'pages/demo/demo.js', id)))
      stCheck('D3 两条都登记后一次通过（exit 0）', g3.exitCode === 0 && g3.known.length === 2, baselineStatusLabel(g3))
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
  console.log('  node tools/verify_miniprogram_static_rules.js                 门禁模式（有 baseline 时判 KNOWN/NEW/STALE）')
  console.log('  node tools/verify_miniprogram_static_rules.js --strict        严格模式：忽略 baseline，任何违规都失败')
  console.log('  node tools/verify_miniprogram_static_rules.js --baseline <f>  指定 baseline 文件（默认 tools/miniprogram_static_rules_baseline.json）')
  console.log('  node tools/verify_miniprogram_static_rules.js --list-candidates 打印 R2 候选清单（渲染列表但无分页参数）')
  console.log('  node tools/verify_miniprogram_static_rules.js --print-baseline  打印当前违规对应的 baseline 片段（只打印，不写文件）')
  console.log('  node tools/verify_miniprogram_static_rules.js --self-test     六条规则 + baseline 的阴性对照（TEMP fixture）')
  console.log('  node tools/verify_miniprogram_static_rules.js --src <root>    指定仓库根目录')
  console.log('  node tools/verify_miniprogram_static_rules.js --help')
  console.log('')
  console.log('退出码：')
  console.log('  0 = 无任何违规；或门禁模式下 NEW=0 且 STALE=0（已知的历史违规不阻塞）')
  console.log('  1 = 有新增违规（NEW）/ 基线条目过期（STALE）/ 严格模式下有违规 / 扫描面无覆盖 / 脚本异常')
  console.log('')
  console.log('baseline 匹配口径：rule + file + identity（**不含行号**）。条目格式见 tools/README.md。')
  printRuleInventory()
}

/** 打印当前违规对应的 baseline 片段（供人工粘贴；**不写文件**，避免自动把新违规洗白） */
function printBaselineSnippet(result, baselineLabel) {
  const today = new Date().toISOString().slice(0, 10)
  // ⚠️ 一个 finding 可能带多条身份（同一行多条证据被合并）→ **逐身份**各给一条条目，
  //    否则漏掉的那条身份在粘贴后仍会判 NEW（评审实测踩到过）
  const entries = []
  result.violations.forEach((v) => {
    const ids = v.identities && v.identities.length ? v.identities : [v.identity || '']
    ids.forEach((id) => {
      entries.push({
        rule: v.rule,
        file: v.file,
        identity: id || '',
        reason: 'TODO：写明为什么这条历史违规先登记、谁负责修（少于 4 字会被判格式错误）',
        evidence: v.evidence,
        registered: today,
        ref: '',
        owner: '',
      })
    })
  })
  bar(`baseline 片段（${entries.length} 条 · 当前扫描到的全部违规与身份 · 只打印不写文件）`)
  console.log('  ⚠️ 登记 = 承认这是**既有**债务；请先确认它不是本次改动引入的，再补 reason/负责人。')
  console.log('  ⚠️ 目标文件：' + baselineLabel + '\n')
  console.log(JSON.stringify({ schema: 1, entries }, null, 2))
  return 0
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

  // 互斥的 flag 组合必须硬失败：`--list-candidates` 是**信息模式**（恒 0），
  // 与它一起写 `--strict` 会得到"有 3 处违规却 exit 0"的假绿（评审实测过）
  const infoMode = argv.includes('--list-candidates')
  if (infoMode && (argv.includes('--strict') || argv.includes('--print-baseline') || argv.includes('--baseline'))) {
    console.error('[FAIL] --list-candidates 是信息模式（恒退出 0），不能与 --strict / --baseline / --print-baseline 同时使用。')
    console.error('       CI 门禁请直接用：node tools/verify_miniprogram_static_rules.js')
    return 1
  }

  if (infoMode) {
    printR2Candidates(result, 'miniprogram')
    console.log('\n（信息模式：不参与门禁判定；CI 请使用默认门禁模式）')
    return 0
  }
  const strict = argv.includes('--strict')
  const baselineArg = argValue('--baseline', '')
  // 相对路径按**被扫描的仓库根目录**（--src）解析 —— baseline 属于它描述的那棵树
  const baselineRel = baselineArg || BASELINE_DEFAULT_REL
  const baselineAbs = path.isAbsolute(baselineRel) ? baselineRel : path.join(root, baselineRel.replace(/^[\\/]+/, ''))

  if (argv.includes('--print-baseline')) {
    return printBaselineSnippet(result, baselineRel)
  }

  if (strict) {
    return printReport(result, 'miniprogram', { strict: true })
  }

  let baselineLabel = baselineRel
  let entries = []
  if (fs.existsSync(baselineAbs)) {
    const parsed = parseBaselineText(readText(baselineAbs))
    if (parsed.errors.length) {
      console.error(`[FAIL] baseline 文件格式错误（${baselineRel}）：`)
      parsed.errors.forEach((e) => console.error('   - ' + e))
      return 1
    }
    entries = parsed.entries
  } else if (baselineArg) {
    console.error(`[FAIL] 指定的 baseline 文件不存在：${baselineAbs}`)
    return 1
  } else {
    baselineLabel = `${BASELINE_DEFAULT_REL}（不存在）`
  }

  // baseline 缺失/为空本身不能"藏住"违规（未登记的违规一定是 NEW → 红），但会让 KNOWN/NEW 失去意义
  // —— 这会削弱门禁的可读性，所以要**大声**说出来，而不是只体现在标题行里
  let baselineWarning = ''
  if (!fs.existsSync(baselineAbs)) {
    baselineWarning =
      `未找到 baseline 文件：${baselineRel}（相对 ${root}）—— 历史违规无法区分，全部按 NEW 处理；` +
      `若这是误删，请从 git 恢复（它应当随仓库一起提交）`
  } else if (entries.length === 0) {
    baselineWarning = `baseline 文件为空（entries: []）：所有违规都会按 NEW 处理；历史欠账未被登记`
  }

  const gate = evaluateGate(result.violations, entries)
  return printReport(result, 'miniprogram', { gate, baselineLabel, baselineWarning })
}

try {
  process.exit(main())
} catch (e) {
  console.error('[FAIL] 脚本异常：', e && e.stack ? e.stack : e)
  process.exit(1)
}
