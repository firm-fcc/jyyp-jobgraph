# -*- coding: utf-8 -*-
"""jd_proficiency 单元测试（独立可运行：python fixtures/test_jd_proficiency.py）。

注入 mock llm_call / classifier，不触网、不读真实缓存。
覆盖：严格契约（重复键/多余字段/非法枚举）、旗标触发、分块、缓存命中、
聚合技能剔除、聚合统计。
"""
import json
import os
import sys
import tempfile

import ut

ut.setup("extractor")
ut.isolate()

import jd_proficiency as jp  # noqa: E402

DIMENSIONS = ("D1", "D2", "D3", "D4")


def make_item(code, level, suff="sufficient", reason="测试理由"):
    return {"team_skill_id": code, "evidence_sufficiency": suff,
            "dimensions": {d: {"level": level, "reason": "引用原文"} for d in DIMENSIONS},
            "requirement_level": level, "reason": reason, "uncertainty": []}


class MockLLM:
    """按 prompt 中【输入】段的技能对返回结果；可注入首 N 次坏输出。"""

    def __init__(self, per_code=None, fail_first=0, bad_text=""):
        self.calls = 0
        self.per_code = per_code or {}
        self.fail_first = fail_first
        self.bad_text = bad_text or '{"results": [], "results": []}'  # 重复键

    def __call__(self, prompt):
        self.calls += 1
        if self.fail_first and self.calls <= self.fail_first:
            return self.bad_text
        inp = json.loads(prompt.split("【输入】\n", 1)[1])
        items = []
        for p in inp["skills"]:
            code = p["team_skill_id"]
            it = self.per_code.get(code)
            if it is None:  # 缺省按词面锚点给保守 P2
                it = make_item(code, "P2")
            items.append(it if it != "OMIT" else None)
        return json.dumps({"results": [i for i in items if i]}, ensure_ascii=False)


def make_evaluator(mock, evidence, chunk=6, use_cache=False):
    classifier = lambda text: {c: list(s) for c, s in evidence.items()}
    return jp.JDProficiencyEvaluator(llm_call=mock, classifier=classifier,
                                     chunk_skills=chunk, use_cache=use_cache)


def test_strict_contract():
    """严格契约：重复键/多余字段/非法枚举的重试与拒收；重试耗尽标记 llm_no_valid_result。"""
    # 重复键：首调坏、重试好
    mock = MockLLM(per_code={"T-SW-01": make_item("T-SW-01", "P2")}, fail_first=1)
    ev = make_evaluator(mock, {"T-SW-01": ["熟悉Java，能独立完成模块开发"]})
    res = ev.evaluate_jd("JD文本A" * 10)
    assert res["skills"]["T-SW-01"]["requirement_level"] == "P2"
    assert ev.n_retries == 1 and ev.n_invalid == 1
    # 模型输出多余字段：条目被拒 → 重试仍坏 → llm_no_valid_result
    bad = make_item("T-SW-01", "P2")
    bad["extra"] = 1
    mock2 = MockLLM(per_code={"T-SW-01": bad})
    ev2 = make_evaluator(mock2, {"T-SW-01": ["熟悉Java"]})
    res2 = ev2.evaluate_jd("JD文本B" * 10)
    s = res2["skills"]["T-SW-01"]
    assert s["requirement_level"] is None and "llm_no_valid_result" in s["flags"]
    # 非法枚举直接抛错
    try:
        jp._validate_result_item(make_item("X", "P5"))
        raise AssertionError("P5 应被拒绝")
    except jp.ProficiencyParseError:
        pass
    # 重复键 JSON 直解拒绝
    try:
        jp._strict_load_object('{"a": 1, "a": 2}')
        raise AssertionError("重复键应被拒绝")
    except jp.ProficiencyParseError:
        pass
    print("ok strict_contract")


def test_flags():
    """确定性旗标四类：锚点词×等级冲突/P4 无高信号/insufficient 冲突/年限冲突 → review_required。"""
    # 词面冲突：精通 + P2
    ev = make_evaluator(MockLLM({"T-SW-01": make_item("T-SW-01", "P2")}),
                        {"T-SW-01": ["精通Java，有大型系统开发经验"]})
    s = ev.evaluate_jd("JD文本C" * 10)["skills"]["T-SW-01"]
    assert "marker_level_conflict" in s["flags"] and s["review_required"]
    # P4 无高信号词
    ev = make_evaluator(MockLLM({"T-SW-01": make_item("T-SW-01", "P4")}),
                        {"T-SW-01": ["熟悉Java，能独立完成开发"]})
    s = ev.evaluate_jd("JD文本D" * 10)["skills"]["T-SW-01"]
    assert "p4_without_high_signals" in s["flags"]
    # 充分性冲突：insufficient + P2
    ev = make_evaluator(MockLLM({"T-SW-01": make_item("T-SW-01", "P2", suff="insufficient")}),
                        {"T-SW-01": ["技术栈：Java/Go/Python"]})
    s = ev.evaluate_jd("JD文本E" * 10)["skills"]["T-SW-01"]
    assert "insufficient_level_conflict" in s["flags"]
    # 年限冲突：5年以上 + P1
    ev = make_evaluator(MockLLM({"T-SW-01": make_item("T-SW-01", "P1")}),
                        {"T-SW-01": ["5年以上Java开发经验"]})
    s = ev.evaluate_jd("JD文本F" * 10)["skills"]["T-SW-01"]
    assert "years_level_conflict" in s["flags"]
    assert s["years_hints"] and s["markers"] == []
    # 无冲突：熟悉 + P2
    ev = make_evaluator(MockLLM({"T-SW-01": make_item("T-SW-01", "P2")}),
                        {"T-SW-01": ["熟悉Java语言，具备良好编码习惯"]})
    s = ev.evaluate_jd("JD文本G" * 10)["skills"]["T-SW-01"]
    assert s["flags"] == [] and not s["review_required"]
    assert s["markers"] == ["熟悉"]
    # 跨度歧义：精通+了解 混合 → marker_span_ambiguous（而非冲突）
    ev = make_evaluator(MockLLM({"T-SW-01": make_item("T-SW-01", "P2")}),
                        {"T-SW-01": ["精通Python编程，了解自动化标注技术"]})
    s = ev.evaluate_jd("JD文本G2" * 10)["skills"]["T-SW-01"]
    assert s["flags"] == ["marker_span_ambiguous"]
    print("ok flags")


def test_chunking():
    """技能对分块：超批拆多批送 LLM，结果合并。"""
    codes = [f"T-AI-{i:02d}" for i in range(1, 9)]  # 8 个技能
    evidence = {c: ["熟悉相关技术，能独立开发"] for c in codes}
    mock = MockLLM()
    ev = make_evaluator(mock, evidence, chunk=3)
    res = ev.evaluate_jd("JD文本H" * 10)
    assert mock.calls == 3  # 3+3+2
    assert len(res["skills"]) == 8
    assert all(s["requirement_level"] == "P2" for s in res["skills"].values())
    print("ok chunking")


def test_cache():
    """证据级缓存：同 JD 文本第二次评估命中缓存零调用、结果一致；不同文本才再调。"""
    with tempfile.TemporaryDirectory() as td:
        jp.CACHE_PATH = os.path.join(td, "prof_cache.jsonl")
        evidence = {"T-SW-01": ["熟悉Java，能独立完成开发"]}
        mock = MockLLM({"T-SW-01": make_item("T-SW-01", "P2")})
        ev = make_evaluator(mock, evidence, use_cache=True)
        text = "JD文本I" * 10
        r1 = ev.evaluate_jd(text)
        assert mock.calls == 1 and ev.n_cache_hits == 0
        # 同文本二次评估：全缓存命中，0 次调用
        r2 = ev.evaluate_jd(text)
        assert mock.calls == 1 and ev.n_cache_hits == 1
        assert r2["skills"] == r1["skills"]
        # rubric_version 不符的旧缓存文件被忽略重算
        stale = dict(r1)
        stale["rubric_version"] = "obsolete"
        with open(jp.CACHE_PATH, "w", encoding="utf-8") as f:
            f.write(json.dumps(stale, ensure_ascii=False) + "\n")
        ev2 = make_evaluator(mock, evidence, use_cache=True)
        ev2.evaluate_jd(text)
        assert mock.calls == 2
    print("ok cache")


def test_aggregate_skills_skipped():
    """聚合剔除：skipped 技能不进分布。"""
    ev = make_evaluator(MockLLM(), {"F-1-01": ["具备主动学习能力"],
                                    "T-SW-01": ["熟悉Java"]})
    res = ev.evaluate_jd("JD文本J" * 10)
    assert "F-1-01" not in res["skills"] and "T-SW-01" in res["skills"]
    print("ok aggregate_skills_skipped")


def test_aggregate_proficiency():
    """聚合统计：n/levels 分布、review 与 flag 计数、unset 单列。"""
    records = [
        {"skills": {"T-SW-01": {"name_zh": "程序设计与软件工程", "requirement_level": "P2",
                                "flags": [], "review_required": False},
                    "T-AI-01": {"name_zh": "机器学习与深度学习", "requirement_level": None,
                                "flags": ["llm_no_valid_result"], "review_required": True}}},
        {"skills": {"T-SW-01": {"name_zh": "程序设计与软件工程", "requirement_level": "P3",
                                "flags": ["marker_level_conflict"], "review_required": True}}},
    ]
    agg = jp.aggregate_proficiency(records)
    d = agg["T-SW-01"]
    assert d["n"] == 2 and d["levels"]["P2"] == 1 and d["levels"]["P3"] == 1
    assert d["review"] == 1 and d["flags"]["marker_level_conflict"] == 1
    assert agg["T-AI-01"]["unset"] == 1
    print("ok aggregate_proficiency")


if __name__ == "__main__":
    test_strict_contract()
    test_flags()
    test_chunking()
    test_cache()
    test_aggregate_skills_skipped()
    test_aggregate_proficiency()
    print("\n全部通过 ✓")
