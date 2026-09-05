# -*- coding: utf-8 -*-
"""taxonomy_mapper 字面近邻复核（near_recheck，第三道门）单测。

背景（2026-09-02 用户裁定"增强监督"）：LLM 守门混批对近同义漏放（AI幻觉识别与纠偏能力
vs 基线 AI幻觉识别与校验）。第三道门 = 确定性相似度预筛（字符重合率 ≥0.5 或公共子串 ≥4）
→ 小批逐对 LLM 终裁（same→强制映射；diff→维持；全部对照对均 same 才映射）。
mock 零真实 LLM（按"复核员/守门员"标记分发）。
"""
import os
import sys
import types

import ut

ut.setup("graph", "builder", "extractor")
from delta_store import DeltaStore                             # noqa: E402,F401
ut.setup("extractor")

import taxonomy_mapper as tm                                   # noqa: E402
from signal_extractor import Candidate                         # noqa: E402


_LABELS = {"tasks": [{"code": "T-28", "name_zh": "多模态数据融合建模", "name_en": ""}],
           "skills": [{"code": "T-AI-08", "name_zh": "AI幻觉识别与校验", "name_en": ""}],
           "jobs": [{"code": "AID-01", "name_zh": "算法工程师", "name_en": ""}]}
_DELTA = [{"id": "PT-001", "name_zh": "联邦学习运维平台", "array": "new_tasks"}]

_MAP_RESP = {"decisions": [
    {"index": 0, "final_kind": "new_skill", "name_zh": "AI幻觉识别与纠偏能力", "name_en": "",
     "status": "keep", "map_to": None, "merge_into": None, "reject_reason": "", "reason": "初判keep"},
    {"index": 1, "final_kind": "new_job", "name_zh": "生成式AI算法工程师", "name_en": "",
     "status": "keep", "map_to": None, "merge_into": None, "reject_reason": "", "reason": "初判keep"},
    {"index": 2, "final_kind": "new_task", "name_zh": "模型压缩", "name_en": "",
     "status": "keep", "map_to": None, "merge_into": None, "reject_reason": "", "reason": "初判keep"},
]}
_JOB_GATE_RESP = [
    {"name": "生成式AI算法工程师", "action": "keep", "nearest": "AID-01",
     "why_not": "生成式AI细分头衔，市场独立招聘"},
]
_NEAR_RESP = [
    {"pair": "AI幻觉识别与纠偏能力", "action": "same", "reason": "纠偏≈校验，同义"},
    {"pair": "生成式AI算法工程师", "action": "diff", "reason": "不同市场头衔"},
]


class _FakeLLM:
    def __init__(self):
        self.calls = []

    def __call__(self, prompt, parse_json=True, max_tokens=None, api_key=None):
        if "复核员" in prompt:
            self.calls.append("near")
            return _NEAR_RESP
        if "岗位体系守门员" in prompt:
            self.calls.append("job_gate")
            return _JOB_GATE_RESP
        if "守门员" in prompt:
            self.calls.append("recheck")
            return []
        self.calls.append("map")
        return _MAP_RESP


def _cand(i, kind, name):
    rec = types.SimpleNamespace(doc_id=f"d{i}", pub_date="2025-04-01")
    return Candidate(i, rec, kind, name, "", "", "依据", [], "high")


def test_near_recheck_same_forces_map(monkeypatch):
    """近邻 same → 强制映射基线（含理由），合法远邻维持 keep。"""
    fake = _FakeLLM()
    monkeypatch.setattr(tm, "call_llm", fake)
    cands = [_cand(0, "new_skill", "AI幻觉识别与纠偏能力"),
             _cand(1, "new_job", "生成式AI算法工程师"),
             _cand(2, "new_task", "模型压缩")]
    decisions = tm.map_signals(cands, _LABELS, _DELTA)
    by_name = {d.name_zh: d for d in decisions}
    assert fake.calls.count("near") == 1, "近邻复核单次小批调用"
    # 字面近同义 → same → 强制映射基线技能（跨 kind 概念覆盖）
    dup = by_name["AI幻觉识别与纠偏能力"]
    assert dup.map_to == {"taxonomy": "skills", "code": "T-AI-08"}
    assert "近邻复核 same" in dup.reason
    # 字面相似但内涵不同 → diff → 维持新条目
    legit = by_name["生成式AI算法工程师"]
    assert legit.status == "keep" and not legit.map_to and not legit.merge_into
    # 无近邻（模型压缩）→ 不进对照批
    assert by_name["模型压缩"].status == "keep" and not by_name["模型压缩"].map_to


def test_near_recheck_llm_failure_keeps(monkeypatch):
    """近邻复核 LLM 失败 → 维持初判（信号不丢）。"""

    class _Boom:
        def __call__(self, prompt, parse_json=True, max_tokens=None, api_key=None):
            if "复核员" in prompt:
                raise RuntimeError("网络瞬断")
            if "岗位体系守门员" in prompt:
                return _JOB_GATE_RESP
            if "守门员" in prompt:
                return []
            return _MAP_RESP

    monkeypatch.setattr(tm, "call_llm", _Boom())
    cands = [_cand(0, "new_skill", "AI幻觉识别与纠偏能力")]
    decisions = tm.map_signals(cands, _LABELS, _DELTA)
    d = decisions[0]
    assert d.status == "keep" and not d.map_to, "LLM 失败不推翻初判"
