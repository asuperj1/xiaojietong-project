#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建指令微调数据集（C1/C2）。

流程：多源采集 → 清洗 → 组装 Qwen2.5 对话格式 → 输出 train.jsonl（≥500 条）。

数据源（离线可用，无需数据库/第三方库）：
  1. 内置种子问答  ai/finetune/data/seed_qa.jsonl
  2. RAG 知识库 FAQ db/sql/99b_knowledge_faq.sql（按句拆分生成问答）
  3. 问法模板扩增（同一问题多种口语化问法 + 多轮追问）

用法：
  python build_dataset.py                     # 默认输出 ai/finetune/data/train.jsonl
  python build_dataset.py --min 500 --out data/train.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

SYSTEM_PROMPT = "你是校捷通校园助手，服务吉林大学学生，回答学习、生活、办事等校园问题，简明准确。"

# 问法模板：{q} 为原始问题文本
QUESTION_TEMPLATES = [
    "{q}",
    "请问{q}",
    "我想知道{q}",
    "{q}？麻烦说一下",
    "同学你好，{q}",
    "帮我看下：{q}",
    "{q}，谢谢",
]

# 多轮追问模板（基于同一主题，考察上下文理解）
FOLLOWUPS = [
    ("那还有别的注意事项吗？", "请结合上面的回答补充 1~2 条关键注意事项，不必重复已有内容。"),
    ("如果遇到问题怎么办？", "请给出遇到异常时的处理办法或咨询渠道。"),
    ("能给我一句话总结吗？", "请用一句话总结上面的要点。"),
]


def load_seed(path: Path) -> list[dict]:
    """读取内置种子问答（每行一个 JSON：{question, answer}）。"""
    items: list[dict] = []
    if not path.exists():
        return items
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        q, a = obj.get("question", "").strip(), obj.get("answer", "").strip()
        if q and a:
            items.append({"topic": obj.get("topic", "校园"), "question": q, "answer": a})
    return items


def load_sql_faq(path: Path) -> list[dict]:
    """从 99b_knowledge_faq.sql 解析知识文档，按句拆成问答对。"""
    items: list[dict] = []
    if not path.exists():
        return items
    text = path.read_text(encoding="utf-8", errors="ignore")
    # 匹配 ('title', 'category', 'content'...)
    for m in re.finditer(r"\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*((?:'(?:[^']|'')*'\s*)+)", text):
        title, category, raw = m.group(1), m.group(2), m.group(3)
        # 拼接被切成多段的字符串字面量
        segs = re.findall(r"'((?:[^']|'')*)'", raw)
        content = "".join(segs).replace("''", "'")
        for sent in re.split(r"[。；;]", content):
            sent = sent.strip()
            if len(sent) < 8:
                continue
            items.append({"topic": category or title, "question": f"{title}：{sent[:18]}…", "answer": sent + "。"})
    return items


def to_chat(question: str, answer: str) -> dict:
    """组装 Qwen2.5 对话格式（messages）。"""
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ]
    }


def build(seed_path: Path, sql_path: Path, out: Path, minimum: int, seed: int = 42) -> int:
    base = load_seed(seed_path) + load_sql_faq(sql_path)
    if not base:
        raise SystemExit("未采集到任何基础问答，请检查种子文件与 99b_knowledge_faq.sql")

    rng = random.Random(seed)
    samples: list[dict] = []

    # 1) 单轮：同问题多问法
    for item in base:
        q, a = item["question"], item["answer"]
        for tpl in QUESTION_TEMPLATES:
            samples.append(to_chat(tpl.format(q=q.rstrip("。？?")), a))

    # 2) 多轮追问（上下文理解）
    for item in base:
        q, a = item["question"], item["answer"]
        for follow_q, follow_hint in FOLLOWUPS:
            samples.append({
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": q},
                    {"role": "assistant", "content": a},
                    {"role": "user", "content": follow_q},
                    {"role": "assistant", "content": f"{follow_hint}（主题：{item['topic']}）"},
                ]
            })

    # 3) 不足则用后缀模板继续扩增（保证 ≥ minimum）
    suffixes = ["请用同学能听懂的话说", "请给要点式回答", "请补充常见误区", "请给出办理渠道"]
    i = 0
    while len(samples) < minimum:
        item = base[i % len(base)]
        samples.append(to_chat(
            f"{item['question'].rstrip('。？?')}，{suffixes[i % len(suffixes)]}",
            item["answer"],
        ))
        i += 1

    rng.shuffle(samples)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"基础问答 {len(base)} 条 → 输出样本 {len(samples)} 条 → {out}")
    return len(samples)


def main() -> None:
    ap = argparse.ArgumentParser(description="构建校捷通指令微调数据集")
    ap.add_argument("--seed", default=str(HERE / "data" / "seed_qa.jsonl"), help="内置种子问答路径")
    ap.add_argument("--sql", default=str(REPO / "db" / "sql" / "99b_knowledge_faq.sql"), help="FAQ 知识库 SQL 路径")
    ap.add_argument("--out", default=str(HERE / "data" / "train.jsonl"), help="输出 train.jsonl 路径")
    ap.add_argument("--min", type=int, default=500, help="最少样本条数（默认 500）")
    args = ap.parse_args()
    build(Path(args.seed), Path(args.sql), Path(args.out), args.min)


if __name__ == "__main__":
    main()
