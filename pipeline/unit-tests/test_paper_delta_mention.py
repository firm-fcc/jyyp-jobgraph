# -*- coding: utf-8 -*-
"""论文 ΔG Stage C（基线提及 → strengthenings）接线自测。

运行：cd codes/graph && python fixtures/test_paper_delta_mention.py
（mock 提及识别器注入，零 LLM；输出到临时目录，不触碰正式产物。）
覆盖：双模式并入 / 规范名回填 / tier 权重与置信口径 / 同 paper 幂等 /
跨论文 noisy-OR / 证据截断。
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import date

import ut

ut.setup("graph", "builder")
ut.isolate()
# 不预置 extractor 路径：config 须先绑定 builder 版（paper_delta 自会在 import config
# 之后补挂 extractor 目录，提及识别器在本测试中为 mock，无需真实模块）

from delta_store import DeltaStore                             # noqa: E402
from paper_delta import MENTION_EVIDENCE_CAP, strengthen_paper_mentions  # noqa: E402


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ✓ {msg}")


class FakePaper:
    def __init__(self, arxiv_id, tier="S", pub_date="2026-01-15"):
        self.arxiv_id = arxiv_id
        self.doc_id = None            # delta_store._doc_id 回退 arxiv_id
        self.tier = tier
        self.pub_date = pub_date
        self.title, self.keywords, self.abstract = "t", [], ""


class FakeMentionExt:
    """鸭子类型提及识别器：按 paper 文本驱动返回固定 code→证据。"""

    def __init__(self, mapping):
        self.mapping = mapping      # {arxiv_id: {code: [units...]}}

    def extract_paper(self, paper, taxonomy):
        return {"mentions": {c: len(u) for c, u in self.mapping.get(paper.arxiv_id, {}).items()},
                "evidence": self.mapping.get(paper.arxiv_id, {}),
                "skillpoints": {}}


class FakeTax:
    def __init__(self, code_to_name):
        self.code_to_name = code_to_name


SKILL, TASK = "T-AI-01", "T-04"
TAXS = {"skill": FakeTax({SKILL: "机器学习与深度学习"}),
        "task": FakeTax({TASK: "应用软件开发"})}


def _decay(pub_date):
    """与 delta_store._recency_decay 同式（半衰期 730 天）。"""
    from datetime import datetime
    age = (date(2026, 8, 22) - datetime.strptime(pub_date, "%Y-%m-%d").date()).days
    return 0.5 ** (age / 730)


def _mk_delta(tmp, name):
    return DeltaStore(os.path.join(tmp, name), source_desc="测试",
                      source_kind="papers", now=date(2026, 8, 22))


def test_strengthen_and_idempotent(tmp):
    """提及并入：code 回填体系规范名、证据判源（papers+tier+confidence）、同 paper 重跑幂等。"""
    print("== 双模式并入 + tier 权重 + 幂等 ==")
    delta = _mk_delta(tmp, "d1.json")
    exts = {"skill": FakeMentionExt({"p1": {SKILL: ["标题：机器学习研究", "摘要句一"]}}),
            "task": FakeMentionExt({"p1": {TASK: ["摘要句二"]}})}
    paper = FakePaper("p1", tier="S")
    n = strengthen_paper_mentions(delta, [paper], exts, TAXS)
    _assert(n == 2, "skill+task 各 1 条并入")
    by_tax = {s["taxonomy"]: s for s in delta.data["strengthenings"]}
    s_skill = by_tax["skills"]
    _assert(s_skill["code"] == SKILL and s_skill["name_zh"] == "机器学习与深度学习",
            "技能条目：code + 体系规范名回填")
    _assert("mention" in s_skill["source_kinds"], "source_kinds 标记 mention")
    ev = s_skill["evidence"]["p1"]
    _assert(ev["src"] == "papers" and ev["tier"] == "S" and ev["confidence"] == "medium",
            "证据判源：src=papers + tier=S + confidence=medium（与新闻侧口径一致）")
    single = s_skill["strength"]
    expect = 1.0 * 0.6 * _decay("2026-01-15")   # tier S=1.0 × medium=0.6 × 半衰期 730 天衰减
    _assert(abs(single - expect) < 1e-4, f"strength = tier×conf×decay = {single:.4f}（存储舍入）")
    _assert(by_tax["tasks"]["code"] == TASK, "任务条目并入 taxonomy=tasks")

    # 同论文重跑：doc_id 幂等（强度不重复累计）
    strengthen_paper_mentions(delta, [paper], exts, TAXS)
    _assert(by_tax["skills"]["strength"] == single, "同 paper 重跑幂等（强度不变）")
    _assert(len(delta.data["strengthenings"]) == 2, "条目不重复创建")


def test_cross_paper_noisy_or(tmp):
    """跨论文证据独立累积（noisy-or 分格）。"""
    print("== 跨论文 noisy-OR ==")
    delta = _mk_delta(tmp, "d2.json")
    exts = {"skill": FakeMentionExt({"p1": {SKILL: ["u1"]}, "p2": {SKILL: ["u2"]}}),
            "task": FakeMentionExt({})}
    papers = [FakePaper("p1", tier="S"), FakePaper("p2", tier="A")]
    strengthen_paper_mentions(delta, papers, exts, TAXS)
    s = delta.data["strengthenings"][0]
    one_s = 1.0 * 0.6 * _decay("2026-01-15")     # tier S
    one_a = 0.7 * 0.6 * _decay("2026-01-15")     # tier A
    expect = 1 - (1 - one_s) * (1 - one_a)
    _assert(abs(s["strength"] - expect) < 1e-4,
            f"noisy-OR(S,A) = {s['strength']:.4f} ≈ {expect:.4f}")
    _assert(set(s["evidence"]) == {"p1", "p2"}, "两篇论文证据各占一格")


def test_evidence_cap(tmp):
    """单条目证据句封顶（防长尾刷屏）。"""
    print("== 证据句截断 ==")
    delta = _mk_delta(tmp, "d3.json")
    units = [f"证据句{i}" for i in range(10)]
    exts = {"skill": FakeMentionExt({"p1": {SKILL: units}}), "task": FakeMentionExt({})}
    strengthen_paper_mentions(delta, [FakePaper("p1")], exts, TAXS)
    ev = delta.data["strengthenings"][0]["evidence"]["p1"]
    _assert(len(ev["sentences"]) == MENTION_EVIDENCE_CAP,
            f"证据句封顶 {MENTION_EVIDENCE_CAP}（实际 {len(ev['sentences'])}）")


def test_empty_mention(tmp):
    """零提及 → 零条目（不产生空增强）。"""
    print("== 无提及不建条目 ==")
    delta = _mk_delta(tmp, "d4.json")
    exts = {"skill": FakeMentionExt({}), "task": FakeMentionExt({})}
    n = strengthen_paper_mentions(delta, [FakePaper("p1")], exts, TAXS)
    _assert(n == 0 and delta.data["strengthenings"] == [], "零提及零条目")


def main():
    tmp = tempfile.mkdtemp(prefix="pmention_fixture_")
    try:
        test_strengthen_and_idempotent(tmp)
        test_cross_paper_noisy_or(tmp)
        test_evidence_cap(tmp)
        test_empty_mention(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n全部通过 ✓")


if __name__ == "__main__":
    main()
