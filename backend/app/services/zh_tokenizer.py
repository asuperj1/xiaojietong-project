"""中文友好的轻量分词（无第三方依赖）。

为什么需要这个模块
------------------
审计 **CAC-25**：关键词兜底检索（`rag.py::_keyword_retrieve`）原实现用

    _TERM_SPLIT_RE = re.compile(r"[\\s,，、;；/]+")

按空白与标点切词。**中文问句没有空格**，于是「图书馆几点关门？」整串被当作
**一个词**去执行 `LIKE '%图书馆几点关门？%'` —— 必然 0 命中；
且全角问号 `？` 不在字符类内，还会污染匹配串。
实测表现：「检索恒为空 → 全站问答降级失败」。

方案选型
--------
| 方案 | 取舍 |
|---|---|
| jieba 分词 | 效果最好，但**新增依赖 + 词典体积**（约 5MB+），且引入构建复杂度 |
| **中文 2-gram（本方案）** | **零依赖**、确定性、可离线测试；中文绝大多数双字词能被 bigram 覆盖 |
| ngram FULLTEXT 索引 | 更彻底（`C33` 负责），但需改表 + 重建索引，属独立批次 |

因此本模块先把 **Python 侧切词**修好（`CAC-25`），索引优化留待 `C33` 同批处理。

设计要点
--------
1. **归一化**：`NFKC` 把全角转半角（`？`→`?`），再用 `[\\s\\W_]` 去掉标点与空白；
   中日韩汉字属「word 字符」，不会被误删。
2. **拉丁/数字**：整词保留（长度 ≥ 2），如 `GPA`、`CET6`、`2026`。
3. **汉字**：切 **2-gram**；长度 ≤ 3 的短串**保留原串**（「图书馆」比「图书/书馆」更精准）。
4. **去噪**：剔除含虚词（的/了/呢/吗…）的 bigram —— 它们是高频噪声词。
5. **确定性**：同样输入必得同样输出（便于测试与缓存命中）。
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["normalize", "terms", "MAX_TERMS_DEFAULT"]

MAX_TERMS_DEFAULT = 8

# 虚词：出现它们的 bigram 通常是噪声（如「点的」「了吗」）
_STOP_CHARS = frozenset("的了吗呢吧啊呀哦嗯嘛么之乎者也")

# 汉字区间（含扩展 A 与兼容区），用于区分「汉字串」与「拉丁串」
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# 分段：把「2026校历」「GPA怎么算」这类**混排串**再切成连续同质段
# （否则整串会被当成一个词去 LIKE，等于没切）
_SEG_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+|[0-9A-Za-z]+"
)
# \w 在 Python3 str 模式下包含汉字，故 \W 只会命中标点/符号
_NON_WORD_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize(text: str) -> str:
    """归一化：全角→半角、去标点、压缩空白，只保留「汉字 / 字母 / 数字 + 空格」。

    >>> normalize("  图书馆几点关门？ ")
    '图书馆几点关门'
    >>> normalize("GPA 怎么算？（急）")
    'GPA 怎么算 急'
    """
    if not text:
        return ""
    # NFKC：全角 ？→?　Ｇ→G；随后 _NON_WORD_RE 把标点替换为空格
    s = unicodedata.normalize("NFKC", text)
    s = _NON_WORD_RE.sub(" ", s)
    return " ".join(s.split())


def _is_cjk_run(run: str) -> bool:
    return bool(_CJK_RE.match(run))


def _bigrams(run: str) -> list[str]:
    """把汉字串切成 2-gram；长度 ≤ 3 时保留原串（短词更精准）。"""
    if len(run) <= 3:
        return [run]
    return [run[i : i + 2] for i in range(len(run) - 1)]


def terms(text: str, max_terms: int = MAX_TERMS_DEFAULT) -> list[str]:
    """把查询串切成可用于 LIKE / FULLTEXT 的词元列表（保持出现顺序、去重）。

    >>> terms("图书馆几点关门")
    ['图书', '书馆', '馆几', '几点', '点关', '关门']
    >>> terms("怎么算")
    ['怎么算']
    >>> terms("GPA怎么算")
    ['GPA']
    >>> terms("一卡通补办流程")
    ['一卡', '卡通', '通补', '补办', '办流', '流程']
    >>> terms("2026校历")
    ['2026', '校历']
    >>> terms("的的的")
    []
    >>> terms("")
    []
    """
    norm = normalize(text)
    if not norm:
        return []

    # 双桶策略：
    #   clean —— 不含虚词的词元（首选，检索信噪比高）
    #   noisy —— 含虚词但**非全虚词**的词元（如「怎么算」「门怎」）
    # 只有当 clean 为空时才退回 noisy，保证「怎么算」这类提问仍有检索输入，
    # 同时避免虚词噪声污染正常提问。
    clean: list[str] = []
    noisy: list[str] = []
    seen: set[str] = set()

    def add(bucket: list[str], tok: str) -> None:
        if tok and tok not in seen:
            seen.add(tok)
            bucket.append(tok)

    for run in norm.split(" "):
        if not run:
            continue
        # 混排串（2026校历 / GPA怎么算）需再切为连续同质段，否则整串当一个词
        for seg in _SEG_RE.findall(run):
            if _is_cjk_run(seg):
                for bg in _bigrams(seg):
                    if len(bg) < 2:
                        continue  # 单字汉字：LIKE '%我%' 会命中全表，纯噪声
                    if all(ch in _STOP_CHARS for ch in bg):
                        continue  # 全虚词（「的的」「了吗」）零信息量
                    if any(ch in _STOP_CHARS for ch in bg):
                        add(noisy, bg)
                    else:
                        add(clean, bg)
            else:
                # 拉丁/数字：≥2 位整词保留；纯数字任意长度保留（2026 / 3 号楼）
                if len(seg) >= 2 or seg.isdigit():
                    add(clean, seg)

    return (clean or noisy)[:max_terms]


def build_like_params(term_list: list[str]) -> list[str]:
    """把词元展开为 LIKE 参数（每个词元出现两次：title、content）。"""
    params: list[str] = []
    for t in term_list:
        params += [f"%{t}%", f"%{t}%"]
    return params


def like_condition(term_list: list[str], title_col: str = "title",
                   content_col: str = "content") -> str:
    """生成 `(title LIKE ? OR content LIKE ?) OR ...` 条件（占位符与 build_like_params 对齐）。"""
    return " OR ".join(
        f"({title_col} LIKE ? OR {content_col} LIKE ?)" for _ in term_list
    )


def match_score_expr(term_list: list[str], title_col: str = "title",
                     content_col: str = "content") -> str:
    """生成命中数打分表达式（MySQL 布尔表达式求和 → 命中词元数）。

    用于 ORDER BY：命中越多排越前，避免「只沾一个泛词」的文档挤掉真正相关的。
    """
    parts = [
        f"(({title_col} LIKE ?) + ({content_col} LIKE ?))" for _ in term_list
    ]
    return " + ".join(parts) if parts else "0"
