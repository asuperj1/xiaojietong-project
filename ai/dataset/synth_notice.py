#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校园通知合成语料生成器（C33 抽取微调的**数据来源**）。

【为什么需要它 —— 先把家底摆清楚（2026-09-17 实测）】
    · `campus_notice` 真实表里只有 **10 行**，正文**全部 < 60 字**；
      `deadline` / `importance` 列虽已建，但**全为 NULL**（那正是我们要标的目标）。
    · ⇒ **真实原料不足以训练**：连"模型预标注（C31 的 `prelabel`）"那条路也走不通 ——
      最多标出 10 条，且那 10 条太短。
    · 更要命的是：**预标注会把模型当前的错误学进去**（本任务恰恰是要修它在年份、
      importance、类目上的错），还得人工复核，质量说不清。
    · 因此改为**程序构造**：**标签由构造过程保证正确**（不需要人工），
      并且能按需覆盖整个标签空间 —— 这正是低资源场景下最可靠的做法。

【代价与边界（必须如实写进训练报告，不许含糊）】
    1. 训练集是**合成**的，而 C34 的评测集 `ai/eval/extract_cases.json` 是**人工编写**的：
       同域、不同源，但**泛化性未验证**（真实样本只有 10 条，只能做定性检查）。
    2. 本生成器**不产评测集**，也不许被评测集用作训练：评测集 24 条与 C34/C35 的 16 条示例池
       一律**排除在训练之外**（有测试守着"id 与文本都不重合"）。
    3. 相对时间全部按 `REFERENCE_DATE` 推算 ⇒ 与评测集的 `reference_date` 必须一致，
       否则训练与评测的口径会打架（有测试守着）。
       ⚠️ 副作用要在报告里注明：用固定基准日合成，可能让模型**过拟合到 2026 年**
       （表现为忽略 prompt 里的"今天"）。我们同时注入带**显式年份**的绝对时间样本
       （2026/2027/2028）来缓解，并在报告的局限里明写。

【覆盖矩阵（缺一项就会在训练集里留下盲区）】
    `category` ∈ 5 类 × `importance` ∈ {3,4,5}
    × 时间表达 ∈ {绝对(带年)/绝对(无年)/相对(本周/下周/即日起)/跨年/多时间/**无时间**}
    × 实体数 ∈ {0,1,2,3}
    另含三类**陷阱样本**（C34 已实证模型在这三处出错）：
      ① 整条通知**没有时间要求** → `deadline` 必须为 `null`（硬编日期即算错，会被判 FP）
      ② 一句话里**多个时间** → 要抽全；`deadline` 取该类通知的**约定那个**
      ③ **只有活动时间、没有报名截止** → `deadline` 取活动日

【`deadline` 的取法（类的约定，与评测集口径对齐）】
    · `cutoff_last`：报名/申请/提交类 ⇒ 取**最晚的那个时间**（截止）
    · `start_first`：放假/活动/讲座安排类 ⇒ 取**最早的那个时间**（事件开始）
    · `none`       ：纯通报/公示类 ⇒ `null`
    这三条不是随手定的：C34 的评测集就是这么标的（`E09` 报名窗口取关闭日；
    `E12` 放假安排取开始日；`E03/E11/E16/E23` 通报类为 `null`），
    训练集必须与评测集**同一套约定**，否则等于在教模型跟评测打架。

【用法】
    from ai.dataset.synth_notice import generate, to_records, validate_all
    samples = generate(count=800, seed=20260917)
    bad = validate_all(samples)          # 期望返回 []

    命令行自检（不发请求、不依赖任何第三方库）：
    python ai/dataset/synth_notice.py --count 800 --json ai/eval/out/synth_stats.json

作者：成员3（C++ 数据层 / 模型微调 / 数据库）· C33
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

DATASET_DIR = Path(__file__).resolve().parent
REPO_ROOT = DATASET_DIR.parent.parent

#: 基准日（周三）。**必须**与 `ai/eval/extract_cases.json` 的 `reference_date` 一致 ——
#: "本周五""下周三"这类相对时间的期望值都是按它推算的。一致性由测试守着。
REFERENCE_DATE = date(2026, 9, 16)

#: 受控类目。与 `ai/eval/prompt_variants.py::CATEGORIES` 由测试强制一致
#: （不直接 import：`ai/dataset` 不该依赖 `ai/eval`，那是评测侧的东西）。
CATEGORIES: tuple[str, ...] = ("奖学金", "活动", "讲座", "竞赛", "通知")

#: importance 只取 3/4/5 —— 与评测集一致（评测集里 1/2 从未出现过，
#: 训出 1/2 的模型在评测上只会制造 FP）。
IMPORTANCE_LEVELS: tuple[int, ...] = (3, 4, 5)

#: **实体数配比**（按桶分配，而不是让组合数自然决定）。
#: 为什么必须显式定：早先的实现是“一个 (类型×时间选项) 组合出一條”，
#: 于是时间选项多的单实体类天然占了 89%，而零实体/三实体只剩 2%/1%，
#: 而评测集里这两类各占 8% —— **陷阱样本训练不足**，模型到评测上必然在它们身上扣分。
#: 取值对齐评测集（0:/8% 1:71% 2:13% 3:8%）并略向难例（0/2/3）倾斜。
ENTITY_MIX: dict[int, float] = {0: 0.11, 1: 0.60, 2: 0.18, 3: 0.11}

#: 字段顺序：assistant 输出按它拼 JSON（与 C34 的 `SCHEMA_HINT` 同序，减少无谓差异）
FIELD_ORDER: tuple[str, ...] = ("category", "importance", "deadline", "entities")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ============================================================ 时间表达 ====

@dataclass(frozen=True)
class TimeSpec:
    """一条时间表达：原文片段 `snippet` + 归一化日期 `norm`。

    `snippet` 必须能**原样出现在通知正文里** —— 评测口径里实体是"原文片段"，
    生成时若做了改写，标签就不成立了（`validate` 会逐条查这一点）。
    """

    kind: str
    snippet: str
    norm: str

    @property
    def year(self) -> str:
        return self.norm[:4]


def _d(y: int, m: int, dd: int) -> str:
    return date(y, m, dd).isoformat()


#: 单时间表达池（kind 写入 tags 供覆盖统计；同一 kind 内不同措辞用于增加表层多样性）
SINGLE_TIMES: tuple[TimeSpec, ...] = (
    TimeSpec("absolute_year", "2026年10月15日", _d(2026, 10, 15)),
    TimeSpec("absolute_year", "2026年11月3日", _d(2026, 11, 3)),
    TimeSpec("absolute_year", "2026年10月28日", _d(2026, 10, 28)),
    TimeSpec("absolute_iso", "2026-11-03", _d(2026, 11, 3)),
    TimeSpec("no_year", "10月15日前", _d(2026, 10, 15)),
    TimeSpec("no_year", "9月3日前", _d(2026, 9, 3)),          # 单数字月份：考"补零"
    TimeSpec("no_year", "10月9日前", _d(2026, 10, 9)),
    TimeSpec("this_week_friday", "本周五", _d(2026, 9, 18)),
    TimeSpec("next_week_monday", "下周一", _d(2026, 9, 21)),
    TimeSpec("next_week_wednesday", "下周三", _d(2026, 9, 23)),
    TimeSpec("since_today_two_weeks", "即日起两周内", _d(2026, 9, 30)),
    TimeSpec("cross_year", "1月15日", _d(2027, 1, 15)),        # 无年份 + 早于今天 ⇒ 次年
    TimeSpec("cross_year", "2月23日", _d(2027, 2, 23)),
    TimeSpec("absolute_next_year", "2027年3月1日", _d(2027, 3, 1)),
    TimeSpec("absolute_next_year", "2028年4月6日", _d(2028, 4, 6)),
)

#: 多时间表达池：(片段列表, 归一化日期列表)。用于 n_entities ≥ 2。
MULTI_TIMES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("9月22日", "10月12日"), (_d(2026, 9, 22), _d(2026, 10, 12))),
    (("9月8日", "9月26日"), (_d(2026, 9, 8), _d(2026, 9, 26))),
    (("10月18日", "11月1日"), (_d(2026, 10, 18), _d(2026, 11, 1))),
    (("12月26日", "12月31日"), (_d(2026, 12, 26), _d(2026, 12, 31))),
    # 3 元素组合：放假安排类需要（评测集里 3 实体的两条都是放假/报名窗口）
    (("1月1日", "1月3日", "1月4日"), (_d(2027, 1, 1), _d(2027, 1, 3), _d(2027, 1, 4))),
    (("2月9日", "2月20日", "3月2日"), (_d(2027, 2, 9), _d(2027, 2, 20), _d(2027, 3, 2))),
    (("12月26日", "12月31日", "1月4日"), (_d(2026, 12, 26), _d(2026, 12, 31), _d(2027, 1, 4))),
    (("9月28日", "9月29日", "9月30日"), (_d(2026, 9, 28), _d(2026, 9, 29), _d(2026, 9, 30))),
    (("3月1日", "3月8日", "3月15日"), (_d(2027, 3, 1), _d(2027, 3, 8), _d(2027, 3, 15))),
)


# ============================================================ 槽位词表 ====
#
# ⚠️ 刻意**避开** C34 评测集用过的机构/地点/事项（逸夫楼、教务处、学工系统、
#    大学生活动中心、双创基地…），以降低与评测集的表层重合度；
#    重合度有量化检查（见 `similarity_report`）。

ORGS: tuple[str, ...] = (
    "材料科学与工程学院", "外国语学院", "计算机科学与技术学院", "研究生院",
    "校团委", "学生资助管理中心", "保卫处", "国际合作与交流处",
    "心理健康教育中心", "招生办公室", "校史馆", "体育运动委员会",
)

#: **按类别分的机构池**。为什么要分：全局乱配会造出"校史馆发奖学金名额"这种荒谬搭配，
#: 模型会把这些噪声当作分布的一部分学进去（语料越不真实，学到的越像模板）。
#: 注意每个池子都 ≥ 4 个机构 —— 机构是主要去重维之一，池子太小会撑不到目标条数。
ORGS_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "奖学金": ("学生资助管理中心", "研究生院", "材料科学与工程学院",
             "外国语学院", "计算机科学与技术学院", "招生办公室"),
    "活动": ("校团委", "体育运动委员会", "材料科学与工程学院",
           "心理健康教育中心", "外国语学院"),
    "讲座": ("研究生院", "国际合作与交流处", "计算机科学与技术学院",
           "外国语学院", "校团委"),
    "竞赛": ("计算机科学与技术学院", "材料科学与工程学院", "研究生院", "校团委"),
    "通知": ("保卫处", "学生资助管理中心", "研究生院", "国际合作与交流处",
           "心理健康教育中心", "招生办公室", "体育运动委员会", "校史馆"),
}

PLACES: tuple[str, ...] = (
    "鼎新楼一楼服务大厅", "敬信楼 305 室", "图书馆四楼研讨间",
    "中心校区体育馆东门", "大学生创新创业园 A 区", "净月校区行政楼 112 室",
    "文体中心放映厅", "校医院门诊一层",
)

ITEMS: tuple[str, ...] = (
    "申请材料", "推荐表", "报名信息表", "参赛作品说明书", "体检表",
    "个人陈述与成绩单", "纸质承诺书", "项目结题报告",
)

MATERIAL_CLAUSES: tuple[str, ...] = (
    "逾期不再受理", "逾期视为自动放弃", "材料不全者不予办理", "请按时办理，过期不补",
)


# ============================================================ 用例类型 ====
#
# 每个 `CaseKind` = 一条"生成规则"：类别、重要度、实体数、deadline 取法、文本模板族。
# 为什么用表驱动：覆盖矩阵是**可以肉眼核对**的（哪一类少了、哪一档缺了，看表就知道），
# 而随机拼字符串做不到这一点。

@dataclass(frozen=True)
class CaseKind:
    key: str
    category: str
    importance: int
    n_entities: int                 # 0 / 1 / 2 / 3
    deadline_rule: str              # cutoff_last | start_first | none
    forms: tuple[str, ...]          # 文本模板，占位：{org}{place}{item}{time}{clause}{year}
    allow_single: bool = True       # 是否允许用 SINGLE_TIMES（多时间类只能 false）
    allow_multi: bool = False       # 是否允许用 MULTI_TIMES


CASE_KINDS: tuple[CaseKind, ...] = (
    # ---------------- 奖学金 ----------------
    CaseKind(
        "scholarship_cutoff", "奖学金", 5, 1, "cutoff_last",
        ("{org}关于评选{year}年度企业冠名奖学金的通知：请符合条件的同学于{time}"
         "将{item}报送至{place}，{clause}。",
         "{org}关于开展{year}年度优秀学生奖学金评审工作的说明：{time}前须完成{item}提交，"
         "报送地点为{place}，{clause}。"),
    ),
    CaseKind(
        "scholarship_submit", "奖学金", 4, 1, "cutoff_last",
        ("{org}关于办理临时困难补助的通知：需申请的同学请于{time}到{place}提交{item}，"
         "{clause}。",
         "{org}家庭经济困难学生认定工作提示：{time}前将{item}交至{place}即可，"
         "逾期需下学期补办。"),
    ),
    CaseKind(
        "scholarship_notice_only", "奖学金", 3, 0, "none",
        ("{org}关于{year}年度各类奖学金名额分配情况的说明：名额已按各专业人数比例下达，"
         "具体分配结果见{place}公告栏。",
         "{org}{year}年度资助工作总结已通过，本学期资助工作按计划推进，同学们无需另行申请。"),
    ),
    # ---------------- 活动 ----------------
    CaseKind(
        "activity_event", "活动", 3, 1, "start_first",
        ("{org}主办的校园{year}年秋季趣味运动会将于{time}在{place}举行，欢迎同学前往观赛。",
         "{org}社团文化展演定于{time}在{place}开演，现场设互动区，无需报名。"),
    ),
    CaseKind(
        "activity_signup", "活动", 4, 1, "cutoff_last",
        ("{org}关于招募迎新志愿者的通知：报名截止时间为{time}，志愿者须参加岗前培训并在"
         "{place}集合，{clause}。",
         "{org}无偿献血活动报名：请于{time}前往{place}登记并提交{item}，"
         "献血前请保证休息。"),
    ),
    CaseKind(
        "activity_multi_window", "活动", 4, 2, "cutoff_last",
        ("{org}冬季长跑活动安排：{time}为报名期，请通过线上系统提交{item}，"
         "逾期不再补报。",),
        allow_single=False, allow_multi=True,
    ),
    # ---------------- 讲座 ----------------
    CaseKind(
        "lecture_preview", "讲座", 3, 1, "start_first",
        ("{org}学术讲坛第{year}期：主题为城市更新与公共空间，主讲人为外校教授，"
         "时间为{time}，地点在{place}，无需预约。",
         "{org}行业报告会通知：{time}在{place}举行，由企业工程师主讲，欢迎旁听。"),
    ),
    CaseKind(
        "lecture_required", "讲座", 4, 1, "start_first",
        ("{org}关于组织安全教育培训的通知：{time}在{place}开展消防与实验室安全专题培训，"
         "各课题组研究生须到场签到。",
         "{org}科研伦理专题培训安排：{time}在{place}进行，全体新入职研究生必须参加。"),
    ),
    # ---------------- 竞赛 ----------------
    CaseKind(
        "contest_signup", "竞赛", 5, 1, "cutoff_last",
        ("{org}{year}年程序设计挑战赛报名开始：报名系统将于{time}关闭，"
         "参赛队须提交{item}并完成线上注册。",
         "{org}关于举办数学应用能力竞赛的通知：报名通道{time}截止，"
         "请将{item}发送至指定邮箱，{clause}。"),
    ),
    CaseKind(
        "contest_round", "竞赛", 4, 2, "cutoff_last",
        ("{org}创新创业项目路演安排：{time}分别进行初评与终评，路演地点为{place}，"
         "请各团队提前 30 分钟到场调试设备。",),
        allow_single=False, allow_multi=True,
    ),
    CaseKind(
        "contest_result", "竞赛", 3, 0, "none",
        ("{org}{year}年英语演讲比赛校内选拔结果已产生，获奖名单与作品评分见{place}公示栏，"
         "证书领取时间另行通知。",
         "{org}关于公布专业能力测评结果的通知：本次测评各专业通过情况已汇总，"
         "具体分数请自行登录系统查询。"),
    ),
    # ---------------- 通知 ----------------
    CaseKind(
        "notice_hard_deadline", "通知", 5, 1, "cutoff_last",
        ("{org}关于本学期公共选修课补退选的通知：补退选将于{time}关闭，"
         "请同学们登录教务系统确认选课结果。",
         "{org}关于办理校园卡信息核验的通知：{time}前须到{place}完成核验，{clause}。"),
    ),
    CaseKind(
        "notice_service_window", "通知", 4, 1, "start_first",
        ("{org}关于校园网核心设备升级的通知：{time}期间宿舍区网络将短时中断，"
         "请提前保存资料。",
         "{org}关于调整浴室开放时间的说明：自{time}起开放时间调整为 16:00 至 22:30，"
         "请合理安排。"),
    ),
    CaseKind(
        "notice_multi_holiday", "通知", 5, 3, "start_first",
        ("{org}关于假期安排的通知：{time}依次为放假开始、返校报到与正式上课时间，"
         "请提前安排行程，具体说明见{place}公告栏。",
         "{org}假期与开学安排：{time}分别为放假首日、返校日与上课首日，"
         "相关手续在{place}办理。"),
        allow_single=False, allow_multi=True,
    ),
    CaseKind(
        "notice_info_only", "通知", 3, 0, "none",
        ("{org}关于校园快递服务站运行情况的通报：本月共处理包裹约 2.4 万件，"
         "站内自助取件设备已全部投入使用。",
         "{org}关于公共区域照明改造完成的说明：本次改造覆盖全部教学楼层，"
         "同学如遇故障可通过报修系统反馈。"),
    ),
)


# ============================================================ 生成 ====

@dataclass
class Sample:
    """一条训练样本：正文 + 标注 + 覆盖标签（tags 用于统计与覆盖校验）。"""

    id: str
    text: str
    expected: dict
    tags: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _pick_slots(rng: random.Random, category: str) -> dict:
    """挑一组槽位值。**只挑值、不做 format** —— 模板里还有 `{time}` 要由构造时间的那一步填。

    机构从**该类别专属池**里取（见 `ORGS_BY_CATEGORY` 的说明），其余槽位共用。
    """
    org_pool = ORGS_BY_CATEGORY.get(category) or ORGS
    return {
        "org": rng.choice(org_pool),
        "place": rng.choice(PLACES),
        "item": rng.choice(ITEMS),
        "clause": rng.choice(MATERIAL_CLAUSES),
        "year": str(rng.choice((2026, 2026, 2026, 2027))),
    }


def _build_sample(kind: CaseKind, times: tuple[TimeSpec, ...], seq: int,
                  rng: random.Random) -> Sample:
    """按类型 + 选定时间构造一条样本。**标签由构造过程直接得出**（不靠模型、不靠人工）。"""
    form = rng.choice(kind.forms)
    slots = _pick_slots(rng, kind.category)
    text = form.format(time="、".join(t.snippet for t in times), **slots)

    entities = [{"type": "time", "text": t.snippet, "norm": t.norm} for t in times]

    if kind.deadline_rule == "cutoff_last" and times:
        deadline = max(t.norm for t in times)
    elif kind.deadline_rule == "start_first" and times:
        deadline = min(t.norm for t in times)
    else:
        deadline = None

    expected = {
        "category": kind.category,
        "importance": kind.importance,
        "deadline": deadline,
        "entities": entities,
    }
    tags = {
        "kind": kind.key,
        "time_kind": ("+".join(t.kind for t in times) if times else "none"),
        "n_entities": len(entities),
        "deadline_rule": kind.deadline_rule,
    }
    return Sample(id=f"SN{seq:04d}", text=text, expected=expected, tags=tags)


def _time_options(kind: CaseKind) -> list[tuple[TimeSpec, ...]]:
    """该类型可用的时间选项。

    ⚠️ 多时间类型要**显式检查池子里有没有够长的组合** —— 早先没查，遇到
    `n_entities=3` 而多时间池里只有 2 元素的组合时就原地空转（`continue` 不推进游标），
    跑了 3 万次只产出 139 条。现在宁可当场报错，也不静默少给样本。
    """
    if kind.n_entities == 0:
        return [()]
    if kind.n_entities == 1:
        return [(spec,) for spec in SINGLE_TIMES]
    opts: list[tuple[TimeSpec, ...]] = []
    for snippets, norms in MULTI_TIMES:
        if len(snippets) >= kind.n_entities:
            picked = tuple(TimeSpec(f"multi{kind.n_entities}", s, n)
                           for s, n in zip(snippets, norms))[:kind.n_entities]
            opts.append(picked)
    if not opts:
        raise ValueError(
            f"类型 {kind.key!r} 声明 n_entities={kind.n_entities}，"
            f"但 MULTI_TIMES 里没有这长的组合 —— 请补数据或改声明"
        )
    return opts


def generate(count: int = 800, *, seed: int = 20260917,
             kinds: tuple[CaseKind, ...] = CASE_KINDS) -> list[Sample]:
    """生成 `count` 条去重样本。

    做法：把 **类型 × 可用时间表达** 先展开成组合池（保证覆盖矩阵每格都有机会被抽到），
    然后**轮转**：每轮每个组合出一条，轮数由目标条数决定。
    这样“某个格子抽不出新文本”最多浪费该格一次重试，不会把整个生成卡死。
    """
    if count <= 0:
        raise ValueError("count 必须为正")
    rng = random.Random(seed)
    combos: list[tuple[CaseKind, tuple[TimeSpec, ...]]] = []
    for kind in kinds:
        for times in _time_options(kind):
            combos.append((kind, times))
    if not combos:
        raise ValueError("没有任何可用组合（kinds 为空？）")

    # 按 `ENTITY_MIX` 分配名额：每个桶内轮转取组合，保证桶内覆盖均匀
    plan: list[tuple[CaseKind, tuple[TimeSpec, ...]]] = []
    for n, share in sorted(ENTITY_MIX.items()):
        bucket = [c for c in combos if c[0].n_entities == n]
        if not bucket:
            continue
        want = max(1, round(count * share))
        for i in range(want):
            plan.append(bucket[i % len(bucket)])

    out: list[Sample] = []
    seen: set[str] = set()
    seq = 0
    for attempt_round in range(6):        # 计划不够时多跑几轮（正常第二轮就停）
        for kind, times in plan:
            if len(out) >= count:
                break
            for _try in range(40):        # 同格内重试换槽位，直到文本不重复
                seq += 1
                sample = _build_sample(kind, times, seq, rng)
                if sample.text in seen:
                    continue
                seen.add(sample.text)
                out.append(sample)
                break
        if len(out) >= count:
            break

    if len(out) < count:
        raise RuntimeError(
            f"只生成了 {len(out)}/{count} 条（跑了 {attempt_round + 1} 轮）："
            f"槽位组合不够，请扩充 ORGS/PLACES/ITEMS/MATERIAL_CLAUSES 或模板族 —— "
            f"不许静默少给样本"
        )
    return out


# ============================================================ 校验 ====

def validate(sample: Sample) -> list[str]:
    """单条样本自检。返回问题列表（空 = 通过）。

    这些检查的目的是让"标签正确"这件事**可被机器验证**，而不是靠生成器作者的自觉：
    标签是训练信号，标签错一条就等于往模型里灌一条错答案。
    """
    problems: list[str] = []
    exp = sample.expected
    text = sample.text

    if exp.get("category") not in CATEGORIES:
        problems.append(f"{sample.id}: category={exp.get('category')!r} 不在受控词表内")
    if exp.get("importance") not in IMPORTANCE_LEVELS:
        problems.append(f"{sample.id}: importance={exp.get('importance')!r} 不在 {IMPORTANCE_LEVELS}")
    if set(exp.keys()) != set(FIELD_ORDER):
        problems.append(f"{sample.id}: 字段集合={sorted(exp.keys())} 与口径 {list(FIELD_ORDER)} 不符")

    deadline = exp.get("deadline")
    if deadline is not None and not _DATE_RE.match(str(deadline)):
        problems.append(f"{sample.id}: deadline={deadline!r} 不是 YYYY-MM-DD")

    entities = exp.get("entities") or []
    if not isinstance(entities, list):
        problems.append(f"{sample.id}: entities 不是列表")
        return problems
    norms: list[str] = []
    for ent in entities:
        if set(ent.keys()) != {"type", "text", "norm"}:
            problems.append(f"{sample.id}: 实体字段集合异常 {sorted(ent.keys())}")
            continue
        if ent["type"] != "time":
            problems.append(f"{sample.id}: 实体 type={ent['type']!r}（本任务只抽 time）")
        if not _DATE_RE.match(str(ent["norm"])):
            problems.append(f"{sample.id}: 实体 norm={ent['norm']!r} 不是 YYYY-MM-DD")
        if ent["text"] not in text:
            # 评测口径里实体是"原文片段"：片段必须真在正文里，否则这条标签无法被模型学会
            problems.append(f"{sample.id}: 实体 text={ent['text']!r} 不在正文中")
        norms.append(str(ent["norm"]))

    if len(entities) != sample.tags.get("n_entities"):
        problems.append(f"{sample.id}: 实体数 {len(entities)} != 声明 {sample.tags.get('n_entities')}")

    if not entities and deadline is not None:
        problems.append(f"{sample.id}: 没有任何时间却给了 deadline={deadline!r}（过度抽取）")
    if entities and deadline is not None and str(deadline) not in norms:
        problems.append(f"{sample.id}: deadline={deadline!r} 不在实体日期 {sorted(set(norms))} 中")

    if not text.strip():
        problems.append(f"{sample.id}: 正文为空")
    return problems


def validate_all(samples: list[Sample]) -> list[str]:
    """批量自检 + 全局不变量（id/正文唯一）。返回问题列表。"""
    problems: list[str] = []
    for s in samples:
        problems.extend(validate(s))
    ids = [s.id for s in samples]
    if len(ids) != len(set(ids)):
        problems.append("存在重复 id")
    texts = [s.text for s in samples]
    if len(texts) != len(set(texts)):
        dup = len(texts) - len(set(texts))
        problems.append(f"存在 {dup} 条重复正文（训练集里重复等于变相加权）")
    return problems


# ==================================================== 与评测集的隔离 ====

def _char_ngrams(text: str, n: int = 3) -> set[str]:
    s = re.sub(r"\s+", "", text)
    return {s[i:i + n] for i in range(max(0, len(s) - n + 1))}


def similarity_report(samples: list[Sample], holdout_texts: list[str], *,
                      n: int = 3, warn_at: float = 0.6) -> dict:
    """与"留出集"（评测集 + few-shot 示例池）的表层重合度检查。

    为什么要量化：训练集与评测集**同域不同源**这件事，光靠"我自己写的时候很小心"是没法服人的。
    用字符 n-gram 重合度给出一个数：**最大重合度**越低，说明"不是抄的"越有底气。
    注意这只是**表层**检查，不能证明分布不同 —— 分布差异只能靠报告里的诚实声明。
    """
    hold = [h for h in holdout_texts if h.strip()]
    worst = {"similarity": 0.0, "sample_id": "", "holdout_index": -1}
    for s in samples:
        grams = _char_ngrams(s.text, n)
        for i, h in enumerate(hold):
            hg = _char_ngrams(h, n)
            if not grams or not hg:
                continue
            j = len(grams & hg) / len(grams | hg)
            if j > worst["similarity"]:
                worst = {"similarity": round(j, 4), "sample_id": s.id, "holdout_index": i}
    return {
        "n_gram": n,
        "holdout_size": len(hold),
        "max_similarity": worst["similarity"],
        "worst_sample": worst["sample_id"],
        "warn_at": warn_at,
        "passed": worst["similarity"] < warn_at,
    }


# ==================================================== 统计与导出 ====

def summarize(samples: list[Sample]) -> dict:
    """覆盖矩阵统计：**缺哪一格一眼可见**（这是能用肉眼核对覆盖的唯一办法）。"""
    def count_by(fn) -> dict:
        out: dict[str, int] = {}
        for s in samples:
            k = str(fn(s))
            out[k] = out.get(k, 0) + 1
        return dict(sorted(out.items()))

    matrix: dict[str, int] = {}
    for s in samples:
        key = f"{s.expected['category']}|imp{s.expected['importance']}|e{s.tags['n_entities']}"
        matrix[key] = matrix.get(key, 0) + 1

    return {
        "count": len(samples),
        "distinct_texts": len({s.text for s in samples}),
        "by_category": count_by(lambda s: s.expected["category"]),
        "by_importance": count_by(lambda s: s.expected["importance"]),
        "by_time_kind": count_by(lambda s: s.tags["time_kind"]),
        "by_n_entities": count_by(lambda s: s.tags["n_entities"]),
        "by_kind": count_by(lambda s: s.tags["kind"]),
        "null_deadline_count": sum(1 for s in samples if s.expected["deadline"] is None),
        "coverage_matrix": dict(sorted(matrix.items())),
        "matrix_cells_covered": len(matrix),
    }


def load_holdout_texts() -> list[str]:
    """留出集正文：C34 评测集 + C34/C35 的 few-shot 示例池（都**不许进训练**）。"""
    texts: list[str] = []
    eval_path = REPO_ROOT / "ai" / "eval" / "extract_cases.json"
    if eval_path.exists():
        texts += [c["text"] for c in json.loads(eval_path.read_text(encoding="utf-8")).get("cases", [])]
    for rel in ("ai/eval/fixtures/few_shot_examples.json",
                "ai/eval/fixtures/prompt_opt_examples_ext.json"):
        p = REPO_ROOT / rel
        if p.exists():
            texts += [e["text"] for e in json.loads(p.read_text(encoding="utf-8")).get("examples", [])]
    return texts


def to_records(samples: list[Sample]) -> list[dict]:
    return [s.to_dict() for s in samples]


def to_messages(sample: Sample, system_prompt: str) -> dict:
    """转成训练用的 chat 格式（`ai/finetune/train.py` 只读 `messages`）。

    `system_prompt` **必须逐字节等于评测时使用的 prompt**（C34 的 `zero-shot`/`finetuned`
    对照要求如此，`extract_bench` 自带 `prompt_mismatch` 自检盯着这条）——
    所以这个字符串由调用方从 `ai/eval/extract_bench.py::SYSTEM_PROMPT` 取，**禁止手抄**。
    """
    payload = {k: sample.expected[k] for k in FIELD_ORDER}
    return {
        "id": sample.id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": sample.text},
            {"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

    ap = argparse.ArgumentParser(prog="python ai/dataset/synth_notice.py",
                                 description="C33 校园通知合成语料生成器（离线自检）")
    ap.add_argument("--count", type=int, default=800)
    ap.add_argument("--seed", type=int, default=20260917)
    ap.add_argument("--json", default="", help="把统计写入指定 JSON")
    args = ap.parse_args(argv)

    samples = generate(args.count, seed=args.seed)
    problems = validate_all(samples)
    stats = summarize(samples)
    stats["validation_problems"] = problems
    stats["similarity"] = similarity_report(samples, load_holdout_texts())

    print(f"[synth_notice] 生成 {stats['count']} 条（去重后正文 {stats['distinct_texts']} 种）")
    print(f"  类别分布 : {stats['by_category']}")
    print(f"  重要度   : {stats['by_importance']}（null deadline {stats['null_deadline_count']} 条）")
    print(f"  时间表达 : {stats['by_time_kind']}")
    print(f"  实体数   : {stats['by_n_entities']}")
    print(f"  覆盖格子 : {stats['matrix_cells_covered']} 格")
    print(f"  与留出集最大 {stats['similarity']['n_gram']}-gram 重合："
          f"{stats['similarity']['max_similarity']}（阈值 {stats['similarity']['warn_at']}，"
          f"{'通过' if stats['similarity']['passed'] else '⚠️ 过高'}）")
    print(f"  自检     : {'全部通过' if not problems else f'❌ {len(problems)} 个问题'}")
    for p in problems[:10]:
        print(f"    - {p}")

    if args.json:
        out = Path(args.json)
        if not out.is_absolute():
            out = REPO_ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  统计已写入 {out}")

    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
