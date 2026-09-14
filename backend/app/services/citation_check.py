"""引用反向校验（任务 `C20`）：防止**编造引用**，并对「检索结果不支撑提问」**拒答**。

## 为什么需要它
`C14` 的实测基线暴露：**负样本误命中率 100%** —— 问"今年寒假从哪一天开始放假？"
（知识库根本没有这条），系统仍然硬塞 top-3 文档并据此作答。这不是"检索不准"，
而是**缺少两道闸门**：

1. **来源闸门**：检索结果根本不含该问题的依据时，应当**拒答**而不是编。
2. **引用闸门**：模型在答案里写出的引用，必须**真的来自**本次检索结果；
   不在其中的属**伪造引用**，要标记/剔除。

本模块只做这两件事，且**纯函数、无 IO**，便于离线单测。

## 设计取舍
- **不依赖 embedding 分数**：向量检索失败会降级到关键词路径，那条路径没有 `score`；
  因此除了分数闸门，还有一道**文本相关性闸门**（问题的字符 n-gram 在来源里的覆盖率），
  它对所有路径都生效，且**可用真实数据离线验证**（见 `tools/verify_citation_check.py`）。
- **句子级依据校验**：当前检索结果里还没有稳定 `chunk_id`（那是 `C19` 的产出），
  所以"引用的片段"以**句子**为单位，用 n-gram 覆盖率判定它是否被检索内容支撑。
- **宁可放过、不可错杀**：覆盖率阈值取得偏宽松，并把"套话/引导句"（长度短、或
  以"根据/综上/建议"开头）列为免检，避免把正常回答误判成幻觉。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "Citation",
    "CitationReport",
    "DEFAULT_COVERAGE_THRESHOLD",
    "DEFAULT_SCORE_THRESHOLD",
    "check_citations",
    "clean_answer",
    "extract_citations",
    "ngram_coverage",
    "should_refuse",
    "text_support_ratio",
]

# ---------------------------------------------------------------- 阈值 ----
# 分数闸门：向量检索最高分低于它 → 拒答（与 settings.rag_score_threshold 默认一致）。
# ⚠️ 这是**唯一**参与拒答判定的阈值。
DEFAULT_SCORE_THRESHOLD = 0.35
# ⚠️ 以下两个是**诊断用**阈值，**默认不参与任何拒绝判定**。
# 原因：「用字面重合度判有无依据」这个方案已被实测否掉 —— 详见 should_refuse 的
# 实测表格（正样本 Q23 覆盖率 0.000，比两条负样本都低）。保留它们是为了能
# **可解释地展示依据强弱**，而不是当门禁。
DEFAULT_COVERAGE_THRESHOLD = 0.34
DEFAULT_SENTENCE_SUPPORT = 0.55
# 免检句：太短的不判（"好的。"），或以这些词开头的属引导/套话
_BOILERPLATE_PREFIXES = (
    "根据以上", "根据上述", "根据检索", "综上", "总之", "另外", "此外", "建议",
    "如需", "如有", "如果需要", "以上", "回答如下", "如下", "参考资料", "来源",
)
MIN_SENTENCE_LEN = 10

# 引用标记：`[1]` / `[图书馆开放时间]` / `【图书馆开放时间】`
_CITE_RE = re.compile(r"[\[【]\s*([^\[\]【】]{1,60}?)\s*[\]】]")

# ⚠️ **不是“凡是方括号都算引用”** —— 下面几类可证实**肯定不是引用**，必须排除：
#   ① Markdown **行内**链接 `[文字](url)`：紧随 `]` 的字符是 `(`；
#   ② 代码下标 `arr[0]` / `x[1]`：`[` 紧跟在 **ASCII 单词字符**之后；
#      ⚠️ 只判 ASCII：`见[1]` 的“见”是 CJK 字符，属**正常引用**，不能误杀；
#   ③ 列表/区间 `[1,2]` / `[1,2,3]`：内容含逗号；
#   ④ URL 片段：内容含 `://`。
#
# 为什么必须过滤：`clean_answer` 会**删除**判定为“伪造”的标记。链接 / 下标若被当成引用，
# 一旦不匹配来源标题就会被删 → **正文被改、Markdown 链接被破**（评审 P2-1）。
#
# ⚠️ **已知残留风险（不处理，已写成测试）**：**引用式链接** `[文字][ref]` **不在过滤范围**。
#    因为 `[a][b]` 到底是“链接”还是“**连续引用**”在字面上不可区分，而
#    `[图书馆规则][学生手册]` 这种连续标题引用是真实存在、**必须保留**的
#    （`tests/test_chat_c20_gate.py` 就依赖它）。两害相权：宁可漏过滤一个小概率的链接形态。
_MD_LINK_FOLLOWERS = "("
_WORD_CHAR_RE = re.compile(r"[A-Za-z0-9_]")
# 归一化：去空白与常见标点，便于标题模糊比对
_NORM_RE = re.compile(r"[\s\u3000,，.。;；:：、!！?？'\"“”‘’()（）\[\]【】<>《》/\\|_-]+")
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？；…\n])")


# ============================================================ 基础工具 ----


def _norm(text: str) -> str:
    return _NORM_RE.sub("", text or "")


def _ngrams(text: str, n: int) -> set[str]:
    s = _norm(text)
    if len(s) < n:
        return {s} if s else set()
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def ngram_coverage(needle: str, haystack: str, *, n: int = 2) -> float:
    """`needle` 的字符 n-gram 有多少比例出现在 `haystack` 里（0~1）。

    >>> ngram_coverage("图书馆开放时间", "图书馆开放时间为每天八点")
    1.0
    >>> ngram_coverage("院长办公室电话", "图书馆开放时间")
    0.0
    """
    grams = _ngrams(needle, n)
    if not grams:
        return 0.0
    pool = _ngrams(haystack, n)
    if not pool:
        return 0.0
    return len(grams & pool) / len(grams)


def _sources_text(sources: list[dict]) -> str:
    parts: list[str] = []
    for s in sources or []:
        if not isinstance(s, dict):
            continue
        parts.append(str(s.get("title") or ""))
        parts.append(str(s.get("category") or ""))
        parts.append(str(s.get("content") or ""))
    return "\n".join(parts)


def _source_titles(sources: list[dict]) -> list[str]:
    out: list[str] = []
    for s in sources or []:
        if isinstance(s, dict) and s.get("title"):
            out.append(str(s["title"]))
    return out


def _plain_sources(sources) -> list[dict]:
    """容忍调用方传字符串列表（便于测试与降级路径）。"""
    out: list[dict] = []
    for s in sources or []:
        if isinstance(s, dict):
            out.append(s)
        elif isinstance(s, str):
            out.append({"title": s, "content": s})
    return out


# ========================================================== 引用标记 ----


@dataclass(frozen=True)
class Citation:
    """一个引用标记。"""

    raw: str  # 原始标记，如 "[1]"
    kind: str  # "index"（序号引用）| "title"（标题引用）
    value: str  # "1" 或 "图书馆开放时间"
    start: int
    end: int


def _looks_like_citation(text: str, match: "re.Match[str]", value: str) -> bool:
    """结构上排除“肯定不是引用”的方括号（判定规则见 `_CITE_RE` 上方注释）。

    这里**只做结构判断，不猜语义** —— 宁可让少数可疑标记留在正文里（最坏是漏剔
    一个伪造引用，只看日志），也不要错删模型正常输出的 Markdown 链接 / 代码下标
    （那会**直接改掉用户看到的正文**）。符合本模块“宁可放过、不可错杀”的取舍。

    ⚠️ **残留风险（已知、不做处理）**：模型模仿 prompt 格式输出 `[分类]`（如 `[图书馆]`）
    时，它不匹配任何来源标题 → 仍会被归为伪造并剔除。这是**有意保留**的：`[分类]`
    与真正的标题引用在字面上无法区分，宁严不宽；若要彻底消除，需让 prompt 不再
    用方括号包裹分类（改 `build_system_prompt`），不属于本模块职责。
    """
    if match.end() < len(text) and text[match.end()] in _MD_LINK_FOLLOWERS:
        return False  # ① 行内链接 `[x](`
    if match.start() > 0 and _WORD_CHAR_RE.match(text[match.start() - 1]):
        return False  # ② arr[0] / x[1]
    if "," in value or "，" in value:
        return False  # ③ [1,2]
    if "://" in value:
        return False  # ④ URL 片段
    return True


def extract_citations(answer: str) -> list[Citation]:
    """提取答案里的引用标记。

    - 纯数字（`[1]` / `【2】`）→ `kind="index"`（指向来源列表第 n 项，1-based）
    - 其它 → `kind="title"`（按标题引用）

    ⚠️ **不是“凡方括号即引用”**：Markdown 链接 `[文字](url)`、代码下标 `arr[0]`、
    列表 `[1,2]`、URL 片段会被**结构上**排除（见 `_looks_like_citation` 与
    `_CITE_RE` 上方注释）。否则 `clean_answer` 会把它们当“伪造引用”**删掉** ——
    后果是正文被改、链接被破（评审 P2-1 的回归测试已锁定：
    `test_markdown_link_is_not_a_citation` / `test_code_subscript_is_not_a_citation`）。

    >>> [c.value for c in extract_citations("开放时间见[1]，另有[图书馆开放时间]。")]
    ['1', '图书馆开放时间']
    >>> extract_citations("详见[图书馆开放时间](https://lib.example.com)")
    []
    >>> extract_citations("取 arr[0] 与 [1,2]")
    []
    """
    out: list[Citation] = []
    src = answer or ""
    for m in _CITE_RE.finditer(src):
        value = m.group(1).strip()
        if not value:
            continue
        if not _looks_like_citation(src, m, value):
            continue
        kind = "index" if value.isdigit() else "title"
        out.append(Citation(m.group(0), kind, value, m.start(), m.end()))
    return out


# ============================================== 闸门一：来源是否支撑 ----


def should_refuse(
    question: str,
    sources,
    *,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
) -> tuple[bool, str]:
    """判断是否应当**拒答**（检索结果不足以回答该问题）。

    **只按相似度分数判定**：

    - 有 `score` 时：最高分 < `score_threshold` → 拒答。
    - 没有 `score` 时（关键词降级路径）：**不拒答**，并在原因里说明。

    ⚠️ 为何不用「问题与来源的文本重合度」当闸门
    -------------------------------------------
    这个方案被**实测否掉**（`tools/verify_citation_check.py`：用 `C14` 基线 27 题
    ＋ 真实知识库正文逐题算，`n=2`）：

    | 样本 | bigram 覆盖率 |
    |---|---|
    | 正样本 Q23「东西丢了去哪里找？」 | **0.000**（比两条负样本都低） |
    | 正样本 Q06 / Q10 / Q11 | 0.100 / 0.091 / 0.111 |
    | 负样本 N01「今年寒假从哪一天开始放假？」 | **0.273** |
    | 负样本 N02「计算机学院院长的办公室电话是多少？」 | 0.067 |

    → 正/负区间**完全重叠，不存在可分阈值**（`unigram` 更糟，负样本反而更高）。
    根因：**提问是口语化的、文档是书面化的，字面重合度不是相关性的可靠代理** ——
    这恰恰是系统要用 embedding 的原因。所以拒答只能依赖**相似度分数**；
    而「阈值该定多少」需要用**记录了 score 的**评测重测（现有 `C14` 基线没存 score）。

    Returns:
        `(是否拒答, 原因)`；不拒答时原因可能仍非空（用于说明为何没判）。
    """
    items = _plain_sources(sources)
    if not items:
        return True, "检索无结果"

    scores = [s.get("score") for s in items if isinstance(s.get("score"), (int, float))]
    if not scores:
        return False, "无相似度分数（关键词降级路径）：文本覆盖率闸门经实测不可靠，不拒答"

    top = max(scores)
    if top < score_threshold:
        return True, f"最高相似度 {top:.3f} < 阈值 {score_threshold}"
    return False, ""


# ========================================== 闸门二：答案是否编造引用 ----


def _split_sentences(text: str) -> list[str]:
    return [p.strip() for p in _SENT_SPLIT_RE.split(text or "") if p and p.strip()]


def _is_boilerplate(sentence: str) -> bool:
    if len(sentence) < MIN_SENTENCE_LEN:
        return True
    return sentence.startswith(_BOILERPLATE_PREFIXES)


def text_support_ratio(sentence: str, sources_text: str, *, n: int = 4) -> float:
    """句子被来源文本支撑的比例（n-gram 覆盖率）。"""
    return ngram_coverage(sentence, sources_text, n=n)


@dataclass
class CitationReport:
    """引用校验结果。"""

    citations: list[Citation] = field(default_factory=list)
    valid: list[Citation] = field(default_factory=list)
    fabricated: list[Citation] = field(default_factory=list)
    unsupported_sentences: list[str] = field(default_factory=list)
    coverage: float = 0.0
    refused: bool = False
    reason: str = ""

    @property
    def ok(self) -> bool:
        """没有伪造引用、也没有无依据句子。"""
        return not self.fabricated and not self.unsupported_sentences

    def summary(self) -> str:
        bits = [f"引用 {len(self.citations)} 条（有效 {len(self.valid)} / 伪造 {len(self.fabricated)}）"]
        if self.unsupported_sentences:
            bits.append(f"无依据句 {len(self.unsupported_sentences)} 条")
        if self.refused:
            bits.append(f"拒答：{self.reason}")
        return "；".join(bits)


def _match_title(cited: str, titles: list[str]) -> bool:
    """标题匹配：归一化后「包含」即算命中（模型可能只写标题的一部分或加了书名号）。"""
    c = _norm(cited)
    if not c:
        return False
    for t in titles:
        nt = _norm(t)
        if nt and (c == nt or c in nt or nt in c):
            return True
    return False


def check_citations(
    answer: str,
    sources,
    *,
    question: str = "",
    sentence_support: float = DEFAULT_SENTENCE_SUPPORT,
    check_sentences: bool = False,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
) -> CitationReport:
    """核心：对「模型答案 + 本次检索结果」做反向校验。

    做三件事：

    1. **标记级（可靠，默认开启）**：`[n]` 必须落在来源列表范围内；`[标题]` 必须
       能匹配到某个来源标题（归一化后包含即算命中）。不满足的进 `fabricated`。
       这一层**不靠字面猜测**，是纯结构校验，可以放心当门禁。
    2. **句子级（启发式，默认关闭）**：拆句后逐句算 n-gram 覆盖率，低于
       `sentence_support` 且非套话的句子进 `unsupported_sentences`。
       ⚠️ **默认关闭**：n-gram 区分不了"改写"与"编造"—— 改写过的合规答案覆盖率
       同样会低（实测正样本 Q23 就是 0.000）。开启后会产生误报，**只适合当诊断信号**，
       不适合当门禁。需要真正的依据校验时应引入 NLI/交叉编码器，而不是 n-gram。
    3. **拒答判定**：透传 `should_refuse`（传了 `question` 才做；**只按分数判定**）。
    """
    items = _plain_sources(sources)
    titles = _source_titles(items)
    src_text = _sources_text(items)

    report = CitationReport()
    report.citations = extract_citations(answer)

    for c in report.citations:
        if c.kind == "index":
            idx = int(c.value)
            if 1 <= idx <= len(items):
                report.valid.append(c)
            else:
                report.fabricated.append(c)
        else:
            (report.valid if _match_title(c.value, titles) else report.fabricated).append(c)

    if check_sentences and src_text:
        for sent in _split_sentences(answer):
            if _is_boilerplate(sent):
                continue
            # 去掉引用标记后再判，避免标记文本干扰覆盖率
            pure = _CITE_RE.sub("", sent).strip()
            if len(pure) < MIN_SENTENCE_LEN:
                continue
            if text_support_ratio(pure, src_text) < sentence_support:
                report.unsupported_sentences.append(sent)

    if src_text:
        report.coverage = ngram_coverage(question or answer, src_text, n=2)

    if question:
        report.refused, report.reason = should_refuse(
            question, items, score_threshold=score_threshold
        )
    return report


def clean_answer(answer: str, report: CitationReport) -> str:
    """剔除**伪造引用标记**（保留正文），并清理删除处残留的空格。

    注意：
    - 只删“伪造”的那几条；有效引用**原样保留**。
    - 删除的是**整段标记（含方括号）**，因此不会留下空括号 —— 早期 docstring
      写的“去掉因此产生的空括号”与实现不符（评审 P3-1），已改成与实现一致的措辞。
    - 两个 `re.sub` 只处理**删除后的空格残留**：标点前空格、连续空格。
    - 哪些方括号**根本不会被当成引用**（Markdown 链接 / 代码下标 / 列表 / URL），
      见 `extract_citations` 及其上方注释。
    """
    if not report or not report.fabricated:
        return answer
    spans = sorted(((c.start, c.end) for c in report.fabricated), reverse=True)
    text = answer or ""
    for start, end in spans:
        text = text[:start] + text[end:]
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+([，。；：！？])", r"\1", text)
    return text.strip()
