"""`C20` 引用反向校验 —— 单元测试（**纯离线**，不需 DB / C++ 扩展 / Ollama）。

覆盖三件事：
1. 两道闸门：**来源支撑**（`should_refuse`）与**引用真实性**（`check_citations`）。
2. 反向对照：**合规答案不能被误判**（否则这个功能上线就是灾难）。
3. `clean_answer` 只删伪造引用、不碰有效引用与正文。

阈值标定不在这里做（那要用真实数据），见 `tools/verify_citation_check.py`。
"""

from __future__ import annotations

import pytest

from app.services.citation_check import (
    DEFAULT_COVERAGE_THRESHOLD,
    DEFAULT_SCORE_THRESHOLD,
    Citation,
    check_citations,
    clean_answer,
    extract_citations,
    ngram_coverage,
    should_refuse,
    text_support_ratio,
)

LIB = {
    "title": "图书馆开放时间",
    "category": "图书馆",
    "content": "图书馆开放时间为每天 8:00-22:00。周末照常开放。",
    "source_url": "https://example.edu.cn/lib",
}
MANUAL = {
    "title": "学生手册",
    "category": "教务",
    "content": "学生手册由教务处发放，新生入学时统一领取。",
}


# ==================================================== extract_citations ----


def test_extract_index_citation() -> None:
    got = extract_citations("开放时间见[1]。")
    assert len(got) == 1
    assert got[0].kind == "index" and got[0].value == "1"


def test_extract_title_citation_both_brackets() -> None:
    got = extract_citations("见[图书馆开放时间]，另有【学生手册】。")
    assert [c.kind for c in got] == ["title", "title"]
    assert [c.value for c in got] == ["图书馆开放时间", "学生手册"]


def test_extract_citations_multiple_and_order() -> None:
    got = extract_citations("[1] 与 [2] 都提到了。")
    assert [c.value for c in got] == ["1", "2"]
    assert got[0].start < got[1].start


def test_extract_citations_none() -> None:
    assert extract_citations("没有任何引用标记。") == []
    assert extract_citations("") == []


def test_extract_citations_ignores_empty_brackets() -> None:
    assert extract_citations("空[]与空【】") == []


# ---------- 评审 P2-1：不是“凡方括号即引用” ----------
# 这四个都是**肯定不是引用**的结构；若被当成引用，`clean_answer` 会删掉它们
# → 正文被改、Markdown 链接被破、代码片段被破。


def test_markdown_link_is_not_a_citation() -> None:
    """评审 P2-1 场景①：`[文字](url)` 是链接，不是引用。"""
    assert extract_citations("详见[图书馆开放时间](https://lib.example.com)。") == []
    # 反向对照：去掉 `(url)` 后**同一个方括号就是**引用 ——
    # 证明上面的空结果来自“链接判定”，而不是正则整体失效
    assert [c.value for c in extract_citations("详见[图书馆开放时间]。")] == ["图书馆开放时间"]


def test_reference_style_link_is_a_known_residual_risk() -> None:
    """⚠️ **已知残留风险（显式断言，不藏着）**：引用式链接 `[文字][ref]` **不过滤**。

    只拦**行内**链接 `[文字](url)`（评审 P2-1 列举的场景①）。原因：`[a][b]` 到底是
    “链接”还是“**连续引用**”在字面上不可区分，而 `[图书馆规则][学生手册]` 这种
    连续标题引用是真实存在、**必须保留**的（`test_chat_c20_gate` 就依赖它）。

    两害相权：宁可漏过滤一个小概率的链接形态，也不误伤真实的连续引用。
    （之前曾加过“`[` 紧跟在 `]` 之后 → 拒”的规则，正是它误伤了连续引用，已回退。）
    """
    got = extract_citations("详见[图书馆开放时间][ref]。")
    assert [c.value for c in got] == ["图书馆开放时间", "ref"]  # 均被提取（已知）
    # 反向对照：**行内**链接形态确实被拦住了
    assert extract_citations("详见[图书馆开放时间](https://x.example.com)。") == []


def test_adjacent_title_citations_are_kept() -> None:
    """**连续标题引用必须保留** —— 这是回退“紧随收尾方括号”规则的原因，锁死防再犯。"""
    got = extract_citations("图书馆开放时间为每天 8:00-22:00。[图书馆规则][学生手册]")
    assert [c.value for c in got] == ["图书馆规则", "学生手册"]
    # 连续序号引用同理
    assert [c.value for c in extract_citations("见[1][2]。")] == ["1", "2"]


def test_code_subscript_is_not_a_citation() -> None:
    """评审 P2-1 场景③：`arr[0]` / `x[1]` 是代码下标，不是引用。"""
    assert extract_citations("取 arr[0] 与 x[1] 的值。") == []
    # 反向对照：**CJK 前缀**后的方括号仍是引用，不能被误杀
    # （这是本规则只判 ASCII 单词字符的原因）
    assert [c.value for c in extract_citations("见[1]。")] == ["1"]
    assert [c.value for c in extract_citations("参考（[2]）")] == ["2"]


def test_number_list_is_not_a_citation() -> None:
    """评审 P2-1 场景③：`[1,2]` 是列表/区间，不是引用。"""
    assert extract_citations("见 [1,2] 两节。") == []
    assert extract_citations("见 [1，2] 两节。") == []
    # 反向对照：单个数字仍是引用
    assert [c.value for c in extract_citations("见 [1] 节。")] == ["1"]


def test_url_fragment_is_not_a_citation() -> None:
    assert extract_citations("见 [https://lib.example.com] 。") == []


def test_structural_filters_do_not_break_real_citations() -> None:
    """**集中反向对照**：各类**真实**引用形式必须全部保留。"""
    text = "见[1]，【2】与[图书馆开放时间]都提到了。"
    assert [c.value for c in extract_citations(text)] == ["1", "2", "图书馆开放时间"]


# ===================================================== ngram_coverage ----


def test_ngram_coverage_full() -> None:
    assert ngram_coverage("图书馆开放时间", "图书馆开放时间为每天八点") == 1.0


def test_ngram_coverage_zero() -> None:
    assert ngram_coverage("院长办公室电话", "图书馆开放时间") == 0.0


def test_ngram_coverage_partial_is_between() -> None:
    r = ngram_coverage("图书馆开放时间", "图书馆开放时间为每天八点")
    r2 = ngram_coverage("图书馆开放时间电话", "图书馆开放时间")
    assert 0.0 < r2 < r == 1.0


def test_ngram_coverage_ignores_whitespace_and_punct() -> None:
    a = ngram_coverage("图书馆 开放时间。", "图书馆开放时间为每天八点")
    b = ngram_coverage("图书馆开放时间", "图书馆开放时间为每天八点")
    assert a == b == 1.0


def test_ngram_coverage_empty_inputs() -> None:
    assert ngram_coverage("", "任何内容") == 0.0
    assert ngram_coverage("任何内容", "") == 0.0


def test_ngram_coverage_unigram_mode() -> None:
    """n=1（单字）对短问句更宽容，用于标定时的对照。"""
    r1 = ngram_coverage("图书馆几点开门", "图书馆开放时间", n=1)
    r2 = ngram_coverage("图书馆几点开门", "图书馆开放时间", n=2)
    assert r1 > r2


# ====================================================== should_refuse ----


def test_refuse_when_no_sources() -> None:
    refused, reason = should_refuse("图书馆几点开门", [])
    assert refused is True
    assert reason


def test_refuse_when_top_score_below_threshold() -> None:
    refused, reason = should_refuse(
        "图书馆几点开门", [{"title": "x", "content": "y", "score": 0.10}]
    )
    assert refused is True
    assert "0.100" in reason or "相似度" in reason


def test_pass_when_top_score_above_threshold() -> None:
    refused, _ = should_refuse(
        "图书馆几点开门", [{"title": "x", "content": "y", "score": 0.88}]
    )
    assert refused is False


def test_score_gate_uses_max_not_first() -> None:
    """多条来源时要看**最高分**，不能因为第一条低分就误拒。"""
    refused, _ = should_refuse(
        "图书馆几点开门",
        [{"title": "a", "content": "b", "score": 0.05}, {"title": "c", "content": "d", "score": 0.9}],
    )
    assert refused is False


def test_no_score_does_not_refuse() -> None:
    """无 score（关键词降级路径）**不拒答** —— 覆盖率闸门已被实测否掉。"""
    refused, reason = should_refuse("计算机学院院长办公室电话", [LIB])
    assert refused is False
    assert "无相似度分数" in reason


def test_coverage_accepts_plain_string_sources() -> None:
    """容忍传字符串列表（测试与降级路径更方便）。"""
    refused, _ = should_refuse("图书馆开放时间", ["图书馆开放时间为 8:00-22:00"])
    assert refused is False


def test_lexical_coverage_would_falsely_reject_a_legit_question() -> None:
    """把「覆盖率闸门被否掉」的证据固化在测试里，防止有人再把它加回门禁。

    真实数据（`C14` 基线 + 知识库正文）里，正样本 Q23「东西丢了去哪里找？」
    的 bigram 覆盖率是 **0.000** —— 比两条负样本都低。这里用同样形状本地复现：
    文档确实回答了问题，但字面几乎不重合。（**提问口语化 / 文档书面化**）
    """
    doc = {"title": "失物招领", "content": "失物招领：图书馆一楼服务台负责失物登记与认领。"}
    q = "东西丢了去哪里找？"
    assert ngram_coverage(q, doc["content"]) < DEFAULT_COVERAGE_THRESHOLD
    # 结论：覆盖率低 ≠ 不相关 → 绝不能拿它拒答
    assert should_refuse(q, [doc])[0] is False


def test_refuse_only_depends_on_score() -> None:
    """同一问题，加不加 score 决定是否拒答；文本内容不参与判定。"""
    q = "图书馆开放时间是什么"
    assert should_refuse(q, [LIB])[0] is False  # 无 score
    with_low = [dict(LIB, score=0.05)]
    assert should_refuse(q, with_low)[0] is True  # 有 score 且低


# ===================================================== check_citations ----


def test_valid_index_citation() -> None:
    r = check_citations("图书馆开放时间为每天 8:00-22:00。[1]", [LIB])
    assert len(r.citations) == 1
    assert len(r.valid) == 1 and r.fabricated == []
    assert r.ok is True


def test_out_of_range_index_is_fabricated() -> None:
    r = check_citations("图书馆开放时间为每天 8:00-22:00。[3]", [LIB])
    assert len(r.fabricated) == 1
    assert r.fabricated[0].value == "3"
    assert r.ok is False


def test_title_citation_matching_source() -> None:
    r = check_citations("开放时间见[图书馆开放时间]。", [LIB])
    assert len(r.valid) == 1 and r.fabricated == []


def test_title_citation_partial_match_is_accepted() -> None:
    """模型常只写标题的一部分，归一化后包含即算命中。"""
    r = check_citations("见[图书馆开放时间（含节假日）]。", [LIB])
    assert len(r.valid) == 1 and r.fabricated == []


def test_title_citation_not_in_sources_is_fabricated() -> None:
    r = check_citations("见[学生手册]。", [LIB])
    assert len(r.fabricated) == 1
    assert r.fabricated[0].value == "学生手册"


def test_mixed_valid_and_fabricated() -> None:
    r = check_citations("见[图书馆开放时间]与[不存在的文档]。", [LIB])
    assert len(r.valid) == 1 and len(r.fabricated) == 1


def test_unsupported_sentence_is_flagged() -> None:
    answer = (
        "图书馆开放时间为每天 8:00-22:00。"
        "计算机学院院长办公室电话是 010-12345678，可随时拨打咨询。"
    )
    r = check_citations(answer, [LIB], check_sentences=True)
    assert len(r.unsupported_sentences) == 1
    assert "院长办公室电话" in r.unsupported_sentences[0]
    assert r.ok is False


def test_grounded_answer_has_no_unsupported_sentences() -> None:
    """反向对照：完全有依据的答案（逐字重合）不能被误判。"""
    answer = "图书馆开放时间为每天 8:00-22:00，周末照常开放。"
    r = check_citations(answer, [LIB], check_sentences=True)
    assert r.unsupported_sentences == []
    assert r.ok is True


def test_boilerplate_sentences_are_exempt() -> None:
    """短句与引导语不判（否则每句"建议咨询教务处"都会被标成幻觉）。"""
    answer = "好的。建议咨询教务处。根据以上资料，图书馆开放时间为每天 8:00-22:00。"
    r = check_citations(answer, [LIB], check_sentences=True)
    assert r.unsupported_sentences == []


def test_sentence_check_is_opt_in() -> None:
    """句子级依据判定**默认关闭**：n-gram 区分不了「改写」与「编造」，
    默认开启会误报（实测正样本 Q23 覆盖率 0.000），因此只能当诊断信号。"""
    answer = "计算机学院院长办公室电话是 010-12345678，可随时拨打咨询。"
    assert check_citations(answer, [LIB]).unsupported_sentences == []
    assert check_citations(answer, [LIB], check_sentences=True).unsupported_sentences


def test_report_refused_propagates_when_question_given() -> None:
    low = [
        {
            "title": "图书馆开放时间",
            "content": "图书馆开放时间为每天 8:00-22:00。",
            "score": 0.10,
        }
    ]
    r = check_citations(
        "图书馆开放时间为每天 8:00-22:00。", low, question="计算机学院院长办公室电话"
    )
    assert r.refused is True
    assert r.reason


def test_report_not_refused_for_on_topic_question() -> None:
    r = check_citations(
        "图书馆开放时间为每天 8:00-22:00。", [dict(LIB, score=0.9)], question="图书馆开放时间是什么"
    )
    assert r.refused is False
    assert r.reason == ""


def test_report_explains_why_it_did_not_refuse_without_score() -> None:
    """无 score 时不拒答，但必须给出原因（不能静默）。"""
    r = check_citations("图书馆开放时间为每天 8:00-22:00。", [LIB], question="图书馆开放时间是什么")
    assert r.refused is False
    assert "无相似度分数" in r.reason


def test_report_coverage_is_populated() -> None:
    r = check_citations("图书馆开放时间", [LIB], question="图书馆开放时间")
    assert r.coverage > 0.5


def test_report_summary_is_human_readable() -> None:
    r = check_citations("见[不存在的文档]。", [LIB])
    s = r.summary()
    assert "伪造 1" in s


def test_empty_sources_makes_everything_fabricated() -> None:
    r = check_citations("见[1]。", [])
    assert len(r.fabricated) == 1


def test_string_sources_supported() -> None:
    r = check_citations("见[图书馆开放时间为 8:00-22:00]。", ["图书馆开放时间为 8:00-22:00"])
    assert len(r.valid) == 1


# ======================================================== clean_answer ----


def test_clean_answer_removes_only_fabricated() -> None:
    answer = "开放时间见[图书馆开放时间]，另见[不存在的文档]。"
    r = check_citations(answer, [LIB])
    cleaned = clean_answer(answer, r)
    assert "[图书馆开放时间]" in cleaned
    assert "不存在的文档" not in cleaned


def test_clean_answer_no_fabricated_is_noop() -> None:
    answer = "开放时间见[图书馆开放时间]。"
    r = check_citations(answer, [LIB])
    assert clean_answer(answer, r) == answer


def test_clean_answer_handles_empty_report() -> None:
    assert clean_answer("原文", None) == "原文"  # type: ignore[arg-type]


def test_clean_answer_collapses_leftover_spaces() -> None:
    answer = "开放时间  [假文档]  。"
    r = check_citations(answer, [LIB])
    cleaned = clean_answer(answer, r)
    assert "假文档" not in cleaned
    assert "  " not in cleaned


def test_clean_answer_preserves_markdown_link() -> None:
    """评审 P2-1 **端到端**：含 Markdown 链接的答案经 check+clean 后**逐字节不变**。

    光断言 `extract_citations` 为空不够 —— 要证明**最终落库/发给前端的文本**没被改动，
    因为“误伤”的危害正体现在这一步。
    """
    answer = "开放时间详见[图书馆开放时间](https://lib.example.com)，或见[1]。"
    r = check_citations(answer, [LIB])
    assert r.fabricated == []
    assert clean_answer(answer, r) == answer


def test_clean_answer_preserves_code_snippet() -> None:
    """评审 P2-1 端到端：代码下标不得被删（`arr[0]` 被删属“改用户看到的正文”）。"""
    answer = "数组取值用 arr[0]，详见 [1] 节。"
    r = check_citations(answer, [LIB])
    assert r.fabricated == []
    assert clean_answer(answer, r) == answer


# ==================================================== 辅助与常量校验 ----


def test_sentence_support_ratio_orders_correctly() -> None:
    on_topic = text_support_ratio("图书馆开放时间为每天 8:00-22:00", LIB["content"])
    off_topic = text_support_ratio("计算机学院院长办公室电话是 010-12345678", LIB["content"])
    assert on_topic > off_topic
    assert off_topic < 0.2


def test_citation_dataclass_is_frozen() -> None:
    c = Citation("[1]", "index", "1", 0, 3)
    with pytest.raises(Exception):
        c.value = "2"  # type: ignore[misc]


def test_default_thresholds_are_sane() -> None:
    assert 0.0 < DEFAULT_COVERAGE_THRESHOLD < 1.0
    assert 0.0 < DEFAULT_SCORE_THRESHOLD < 1.0


# ============================================ 评分闸门与 store 阈值的关系 ----


def test_score_gate_threshold_relationship_is_pinned() -> None:
    """把闸门阈值与 store 过滤阈值的**关系**钉住（评审 P2）。

    原断言是 `assert 0.0 < DEFAULT_SCORE_THRESHOLD < 1.0` —— **对任何合理常数恒真**：
    实测把它改成 0.11 ~ 0.88 之间任意值，全部 463 条用例仍然全绿。

    这两个值**不是各自独立的**：上游 `rag.py` 让向量库按 `settings.rag_score_threshold`
    先筛一遍，筛掉的候选进不到 `should_refuse`。所以：

        gate 阈值 == store 阈值  ⇒  `top < gate` 恒为假 ⇒ 闸门只在「检索无结果」时触发
        gate 阈值 >  store 阈值  ⇒  闸门能拦住「有来源但都不够像」的情况

    **改 store 阈值而不动这个常量，闸门就会静默失效** —— 这正是本 PR 之前的状态。
    """
    from app.core.config import settings

    assert DEFAULT_SCORE_THRESHOLD >= settings.rag_score_threshold, (
        f"闸门阈值 {DEFAULT_SCORE_THRESHOLD} 低于 store 过滤阈值 "
        f"{settings.rag_score_threshold} ⇒ should_refuse 永不可达（死代码）。"
        f"两者要么相等（此时闸门只拦「检索无结果」），要么闸门更高。"
    )


def test_score_gate_fires_when_store_threshold_is_lower() -> None:
    """反向对照：把 store 阈值降到闸门之下，闸门**必须真的能拦**。

    没有这条，上面那条断言只能证明"常量之间的大小关系"，证明不了闸门本身可用。
    """
    weak = [{"title": "某文档", "content": "...", "score": 0.20}]
    refused, reason = should_refuse("随便问点什么", weak, score_threshold=0.35)
    assert refused is True
    assert "0.200" in reason or "0.2" in reason

    strong = [{"title": "某文档", "content": "...", "score": 0.60}]
    ok, _ = should_refuse("随便问点什么", strong, score_threshold=0.35)
    assert ok is False


# ============================ 评审 P2-2 / P3-2：不要把整块闸门搞挂 ============


def test_superscript_does_not_skip_the_whole_citation_check() -> None:
    """一个 `[²]` 不得让**整条答案**的引用校验被静默跳过（评审 P2-2）。

    `'²'.isdigit()` 为 `True`，但 `int('²')` 抛 `ValueError`。旧实现在
    `check_citations` 里直接 `int(c.value)`，异常会被 `chat.py` 的
    `except Exception` 记成 warning 吞掉 ⇒ 这条回答里**所有**伪造引用都不会被剔除。
    """
    answer = "面积为 [²] 平方米，另见[编造的标题]。"
    report = check_citations(answer, [LIB])          # 不得抛异常
    # 重点不是 `[²]` 被判成什么，而是**校验跑完了** —— 它后面的伪造引用必须被抓住。
    assert "编造的标题" in [c.value for c in report.fabricated], (
        "上标不应让同一条答案里的伪造引用漏网"
    )
    # `[²]` 自身既不是合法序号、也不匹配任何标题 ⇒ 归伪造（宁可放过方向的反面，
    # 但至少是**确定的行为**且能被日志看见，而不是静默跳过整条校验）。
    assert "²" in [c.value for c in report.fabricated]


def test_fullwidth_digits_still_count_as_index() -> None:
    """反向对照：全角数字 `int()` 是**可以**解的，不能一并降级成标题类。

    提醒后来者：`isascii()` 是错的判据（它会把 `'１２３'` 也排除掉），
    正确做法是用 `int()` 本身的接受域。
    """
    assert [c.kind for c in extract_citations("见[１２３]。")] == ["index"]


def test_markdown_indent_and_hard_break_survive_clean() -> None:
    """`clean_answer` 只清**删除点**的空白，不得改正文其余部分（评审 P3-2）。

    旧实现是全篇 `re.sub(r"[ \\t]{2,}", " ")` + `re.sub(r"[ \\t]+([，。；：！？])", ...)`，
    实测会把 Markdown 的 4 空格缩进压成 1 个、并删掉行尾两个空格（硬换行）。
    """
    answer = "步骤：\n    1. 打开系统  \n    2. 输入学号[编造的标题]"
    report = check_citations(answer, [LIB])
    assert [c.value for c in report.fabricated] == ["编造的标题"]
    cleaned = clean_answer(answer, report)
    assert "\n    1." in cleaned, f"缩进被压平：{cleaned!r}"
    assert "打开系统  \n" in cleaned, f"行尾硬换行被删：{cleaned!r}"


def test_clean_still_tidies_whitespace_left_by_deletion() -> None:
    """反向对照：删除点**该**清理的空白仍要清 —— 别为了不动正文就什么都不做。"""
    answer = "见 [编造的标题]。"
    report = check_citations(answer, [LIB])
    assert clean_answer(answer, report) == "见。"

    mid = "详见 [编造的标题] 的说明。"
    report2 = check_citations(mid, [LIB])
    assert clean_answer(mid, report2) == "详见 的说明。"
