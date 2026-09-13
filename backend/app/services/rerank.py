"""`C16` 检索重排（rerank）模块：可插拔策略。

背景
----
向量检索（bge-m3）召回的是「语义相近」，但**不保证最该被引用的那篇排在第一位**。
`C14` 基线实测：`hit@3 = 100%` 而 `hit@1 = 92%` —— 说明**正确文档几乎都在候选里，
只是排序不总是第一**。这正是重排该解决的问题。

设计
----
`RerankStrategy` 抽象接口 + 注册表，与 `C15` 的切片策略同一套形状：

- `NoopStrategy`（`none`，**默认**）：不改变顺序。默认必须是它 —— 否则「加了个
  可选模块」会变成「悄悄改变了线上检索结果」。
- `LexicalStrategy`（`lexical`）：IDF 加权的词元重合度（BM25 形状）+ 标题加权。
  零依赖、纯 CPU、可离线跑，不依赖 Ollama。

⚠️ 关于 IDF 的语料口径
--------------------
真正的 BM25 需要全库 `df`。这里只拿**本批候选集**当语料算 `df`（"候选内 IDF"），
好处是零额外查询、结果可复现；代价是候选集越小 IDF 越不稳定（候选只有 3 篇时
区分度会退化）。所以 `settings.rag_rerank_candidates` 默认给 20 —— 先多召，
再重排。要更准就得上交叉编码器（bge-reranker），那是 `C33/三阶段` 的事。

⚠️ 关于分词：**故意不用 `zh_tokenizer.terms()`**
-------------------------------------------------
`terms()` 的粒度取决于 run 的**长度**（≤3 字返回整词、>3 字切 2-gram）：

    terms("图书馆 研讨间")      -> ['图书馆', '研讨间']          # 两个 3 字 run
    terms("图书馆的规则。图书馆借阅。") -> ['图书','书馆','规则','馆借','借阅']  # >3 字

于是同一个「图书馆」在**问句**里是整词、在**正文**里变成「图书/书馆」，
**两侧词元永不相交**，任何按词元加权的打分（IDF / BM25 / 重合度）全部归零。
对 LIKE 匹配无所谓（子串能命中），但这里是打分，必须粒度一致。
→ 本模块自建 `_tokens()`：归一化后对所有汉字串统一切重叠 2-gram（MySQL ngram 默认值）。
→ `terms()` 这个不一致已登记 **`CAC-28`**（`terms()` 只能用于 LIKE，不可用于打分）。

作者：成员3 · C16
"""

from __future__ import annotations

import logging
import math
import re
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Optional

from app.services.zh_tokenizer import normalize

logger = logging.getLogger(__name__)

__all__ = [
    "RerankStrategy",
    "NoopStrategy",
    "LexicalStrategy",
    "DEFAULT_RERANK",
    "register_reranker",
    "unregister_reranker",
    "available_rerankers",
    "get_reranker",
    "resolve_reranker",
]

# BM25 形状参数：k1 控制词频饱和，b 控制长度归一化强度（业界默认值）
_BM25_K1 = 1.2
_BM25_B = 0.75

# 标题命中比正文命中重要得多：「图书馆开放时间」这篇标题里就有「图书馆」。
# 实测调参见 tools/eval_rerank.py（改了要重跑，别凭感觉调）。
TITLE_BOOST = 3.0

# 同质段：汉字串 / 拉丁数字串（与 zh_tokenizer 的分段规则一致）
_SEG_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+|[0-9A-Za-z]+")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _tokens(text: str) -> list[str]:
    """统一的字符 2-gram 分词（问句与文档必须走**同一个**函数）。

    汉字串：长度 1 保留原字，否则切**重叠** 2-gram（与 MySQL `WITH PARSER ngram`
    默认 `ngram_token_size=2` 一致）；拉丁/数字整段保留。

    选重叠 2-gram 而不是 `zh_tokenizer.terms()` 的原因见模块 docstring（`CAC-28`）。
    """
    norm = normalize(text)
    if not norm:
        return []
    out: list[str] = []
    for seg in _SEG_RE.findall(norm):
        if _CJK_RE.match(seg):
            if len(seg) == 1:
                out.append(seg)
            else:
                out.extend(seg[i : i + 2] for i in range(len(seg) - 1))
        else:
            out.append(seg)
    return out


def _doc_text(doc: Any, key: str) -> str:
    if isinstance(doc, dict):
        return str(doc.get(key) or "")
    return str(getattr(doc, key, "") or "")


class RerankStrategy(ABC):
    """重排策略基类。"""

    name: ClassVar[str] = ""
    label: ClassVar[str] = ""

    @abstractmethod
    def score(self, question: str, doc: Any) -> float:
        """给单个候选打分（越大越该排前面）。必须**不依赖全局状态**、可重复调用。"""

    def rerank(self, question: str, docs: list[Any], top_k: Optional[int] = None) -> list[Any]:
        """按分数降序返回。

        **稳定性是硬要求**：分数相同必须保持原顺序（`sorted` 本身稳定，这里显式
        只按 -score 排序）。否则同一问题两次请求可能给出不同顺序，
        `CAC-04` 的检索缓存就会返回「看起来随机」的结果。
        """
        if not docs:
            return []
        scored = [(self.score(question, d), i, d) for i, d in enumerate(docs)]
        scored.sort(key=lambda x: (-x[0], x[1]))
        out = [d for _, _, d in scored]
        return out[:top_k] if top_k else out

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<{type(self).__name__} name={self.name!r}>"


class NoopStrategy(RerankStrategy):
    """不重排：原样返回（默认，保证开启本模块**零行为变化**）。"""

    name = "none"
    label = "不重排"

    def score(self, question: str, doc: Any) -> float:
        return 0.0

    def rerank(self, question: str, docs: list[Any], top_k: Optional[int] = None) -> list[Any]:
        # 显式 override：Noop 必须**原样**返回，不能走基类的排序（虽然全 0 分也稳定，
        # 但直接返回能保证 future 改动基类时不会意外影响它）
        return docs[:top_k] if top_k else list(docs)


class LexicalStrategy(RerankStrategy):
    """IDF 加权的词元重合度 + 标题加权（零依赖、可离线评测）。

    ⚠️ **实测为负收益，因此不得作为默认开启**（`tools/eval_rerank.py`）
    ----------------------------------------------------------------
    在 `C14` 基线的 25 道正样本上，于已召回 top-3 内重排：

    | 指标 | baseline（向量序） | 词法重排 | Δ |
    |---|---|---|---|
    | hit@1 | 0.9200 | **0.8800** | **−0.0400** |
    | hit@3 | 1.0000 | 1.0000 | +0.0000 |
    | MRR | 0.9533 | **0.9400** | **−0.0133** |

    典型失败：`Q13`「宿舍水管漏了找谁修，怎么报修？」（预期《宿舍报修与后勤服务》）
    从 rank 1 被降到 rank 2 —— 另一篇标题也含「宿舍」且正文重叠词元更多的文档被顶上去了。

    **根因**：这是**覆盖式**（用词法分完全取代向量序）造成的。词法重合度偏爱
    「字面重叠多」的文档，而不是「语义上是答案」的文档 —— 与 `C20` 的实测结论一致
    （同一批数据上，正样本 top-1 词法分 min=0.000 反而低于负样本 max=2.522）。
    向量序（bge-m3）本身就带语义信号，直接丢掉它是自损。

    **正确做法是分数融合**：`final = w·向量分 + (1−w)·归一化词法分`，而不是覆盖。
    但融合权重 `w` 需要**逐命中项的向量分**才能标定，而 `C14` 基线没存 `score`
    → 与 `C20` 同一个依赖：`CAC-27`。在补上之前，本策略**保持默认关闭**，
    只作为「机制已就绪 + 反面证据已记录」交付。
    """

    name = "lexical"
    label = "词法重排（IDF + 标题加权）"

    def score(self, question: str, doc: Any) -> float:
        return self.score_all(question, [doc])[0]

    def score_all(self, question: str, docs: list[Any]) -> list[float]:
        """批量打分：IDF 的 `df` 需要看整批候选，所以批量算才对。

        单文档调用 `score()` 时退化为「一篇文章当语料」，此时 IDF 全是常数，
        退化成纯重合度 —— 记录在案，别在单文档场景指望 IDF 起作用。
        """
        q_terms = _tokens(question)
        if not q_terms or not docs:
            return [0.0] * len(docs)

        body_terms = [_tokens(_doc_text(d, "content")) for d in docs]
        title_terms = [_tokens(_doc_text(d, "title")) for d in docs]

        n_docs = len(docs)
        avg_len = sum(len(t) for t in body_terms) / n_docs or 1.0

        # 候选内 df：query 词元在多少篇候选的标题或正文里出现过
        df: dict[str, int] = {}
        for t in set(q_terms):
            df[t] = sum(
                1 for i in range(n_docs) if t in title_terms[i] or t in body_terms[i]
            )

        out: list[float] = []
        for i in range(n_docs):
            body_len = len(body_terms[i]) or 1
            score = 0.0
            for t in set(q_terms):
                idf = math.log(1.0 + (n_docs - df[t] + 0.5) / (df[t] + 0.5))
                tf = body_terms[i].count(t)
                if tf:
                    denom = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * body_len / avg_len)
                    score += idf * tf * (_BM25_K1 + 1) / denom
                if t in title_terms[i]:
                    score += TITLE_BOOST * idf
            out.append(score)
        return out

    def rerank(self, question: str, docs: list[Any], top_k: Optional[int] = None) -> list[Any]:
        if not docs:
            return []
        scores = self.score_all(question, docs)
        order = sorted(range(len(docs)), key=lambda i: (-scores[i], i))
        out = [docs[i] for i in order]
        return out[:top_k] if top_k else out


# ---------------------------------------------------------------- 注册表 ----

DEFAULT_RERANK = NoopStrategy.name

_REGISTRY: dict[str, RerankStrategy] = {}


def register_reranker(strategy: RerankStrategy, *, replace: bool = False) -> RerankStrategy:
    """注册策略。重名默认抛错（防止两个人各写一个 `lexical` 互相覆盖）。"""
    if not isinstance(strategy, RerankStrategy):  # type: ignore[unreachable]
        raise TypeError("必须传入 RerankStrategy 实例")
    if not strategy.name:
        raise ValueError("策略必须定义 ClassVar name")
    if strategy.name in _REGISTRY and not replace:
        raise ValueError(f"重排策略名重复：{strategy.name!r}")
    _REGISTRY[strategy.name] = strategy
    return strategy


def unregister_reranker(name: str) -> None:
    if name == DEFAULT_RERANK:
        raise ValueError(f"不能注销默认策略 {DEFAULT_RERANK!r}")
    _REGISTRY.pop(name, None)


def available_rerankers() -> list[str]:
    return sorted(_REGISTRY)


def get_reranker(name: str) -> RerankStrategy:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"未注册的重排策略 {name!r}；可用：{', '.join(available_rerankers())}"
        ) from None


def resolve_reranker(name: Optional[str]) -> RerankStrategy:
    """按名字取策略；未知名字**回退默认并打 WARNING**（不抛异常）。

    与 `resolve_strategy`（C15）同一策略：配置写错不应该把对话打挂，
    但必须留下warning，否则「以为开了重排其实没开」会静默很久。
    """
    if not name:
        return get_reranker(DEFAULT_RERANK)
    try:
        return get_reranker(name)
    except KeyError:
        logger.warning("未知的重排策略 %r，回退 %r；可用：%s",
                       name, DEFAULT_RERANK, ", ".join(available_rerankers()))
        return get_reranker(DEFAULT_RERANK)


register_reranker(NoopStrategy())
register_reranker(LexicalStrategy())
