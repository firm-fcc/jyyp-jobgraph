# -*- coding: utf-8 -*-
"""taxonomy_mapper 岗位守门（recheck_job_keeps）单测：new_job keep 的第二道独立 LLM。

背景（2026-09-02 用户裁定）：论文侧岗位此前无第二道门——近同义（数据标注员 vs 基线
数据标注师）与场景限定角色直接出生。守门四路：map 基线同义/子岗 / merge 叠层同岗 /
reject 非普适 / keep 普适新岗位；另含确定性基线同名强制映射（改名碰撞，零 LLM）。
mock 零真实 LLM（按提示词"岗位体系守门员"标记分发三阶段调用）。
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


_LABELS = {"tasks": [{"code": "T-01", "name_zh": "应用软件开发", "name_en": ""}],
           "skills": [{"code": "S-01", "name_zh": "机器学习", "name_en": ""}],
           "jobs": [{"code": "AID-18", "name_zh": "数据标注师", "name_en": ""},
                    {"code": "DEV-15", "name_zh": "GIS工程师", "name_en": ""}]}
_DELTA = [{"id": "GJX-01", "name_zh": "前向部署工程师", "array": "new_jobs"},
          {"id": "PT-001", "name_zh": "联邦学习运维平台", "array": "new_tasks"}]

# 初判（PROMPT_MAP 视角）：五条岗位候选全 keep（任务/技能守门与岗位守门均跳过非本类）
_MAP_RESP = {"decisions": [
    {"index": 0, "final_kind": "new_job", "name_zh": "数据标注员", "name_en": "",
     "status": "keep", "map_to": None, "merge_into": None, "reject_reason": "", "reason": "初判keep"},
    {"index": 1, "final_kind": "new_job", "name_zh": "GIS开发者", "name_en": "",
     "status": "keep", "map_to": None, "merge_into": None, "reject_reason": "", "reason": "初判keep"},
    {"index": 2, "final_kind": "new_job", "name_zh": "学生软件解决方案中心运营者", "name_en": "",
     "status": "keep", "map_to": None, "merge_into": None, "reject_reason": "", "reason": "初判keep"},
    {"index": 3, "final_kind": "new_job", "name_zh": "GIS工程师", "name_en": "",
     "status": "keep", "map_to": None, "merge_into": None, "reject_reason": "",
     "reason": "映射改名归一（原名 地理信息系统开发者）"},
    {"index": 4, "final_kind": "new_job", "name_zh": "前向部署工程师", "name_en": "",
     "status": "keep", "map_to": None, "merge_into": None, "reject_reason": "", "reason": "初判keep"},
    {"index": 5, "final_kind": "new_job", "name_zh": "幻觉code岗位", "name_en": "",
     "status": "keep", "map_to": None, "merge_into": None, "reject_reason": "", "reason": "初判keep"},
    {"index": 6, "final_kind": "new_job", "name_zh": "生成式AI算法工程师", "name_en": "",
     "status": "keep", "map_to": None, "merge_into": None, "reject_reason": "", "reason": "初判keep"},
]}

# 岗位守门（守门员视角）：近同义 map / 场景限定 reject / 幻觉 code 拒 / 普适 keep；
# "前向部署工程师"→merge 叠层同岗；"GIS工程师"确定性同名映射不进 LLM 批
_JOB_GATE_RESP = [
    {"name": "数据标注员", "action": "map", "taxonomy": "jobs", "code": "AID-18"},
    {"name": "GIS开发者", "action": "map", "taxonomy": "jobs", "code": "DEV-15"},
    {"name": "学生软件解决方案中心运营者", "action": "reject",
     "reject_reason": "校园场景限定角色，非普适市场岗位"},
    {"name": "前向部署工程师", "action": "merge", "target": "GJX-01"},
    {"name": "幻觉code岗位", "action": "map", "taxonomy": "jobs", "code": "AID-99"},   # code 无效
    {"name": "生成式AI算法工程师", "action": "keep", "nearest": "AID-01", "why_not": "基线无生成式AI岗"},
]

# 任务/技能守门（recheck_keeps 视角）——本批无 new_task/new_skill keep，返回空
_RECHECK_RESP = []


class _FakeLLM:
    def __init__(self):
        self.calls = []

    def __call__(self, prompt, parse_json=True, max_tokens=None, api_key=None):
        if "岗位体系守门员" in prompt:
            self.calls.append("job_gate")
            return _JOB_GATE_RESP
        if "守门员" in prompt:
            self.calls.append("recheck")
            return _RECHECK_RESP
        self.calls.append("map")
        return _MAP_RESP


def _cand(i, kind, name):
    rec = types.SimpleNamespace(doc_id=f"d{i}", pub_date="2025-02-01")
    return Candidate(i, rec, kind, name, "", "", "依据", [], "high")


def test_job_gate_verdicts(monkeypatch):
    """岗位守门裁决：近同义岗位被映射回基线（数据标注员→AID-18、GIS开发者→DEV-15），理由含'岗位守门'。"""
    fake = _FakeLLM()
    monkeypatch.setattr(tm, "call_llm", fake)
    cands = [_cand(0, "new_job", "数据标注员"), _cand(1, "new_job", "GIS开发者"),
             _cand(2, "new_job", "学生软件解决方案中心运营者"),
             _cand(3, "new_job", "地理信息系统开发者"),   # 映射 LLM 改名 → "GIS工程师"
             _cand(4, "new_job", "前向部署工程师"), _cand(5, "new_job", "幻觉code岗位"),
             _cand(6, "new_job", "生成式AI算法工程师")]
    decisions = tm.map_signals(cands, _LABELS, _DELTA)
    by_name = {d.name_zh: d for d in decisions}
    assert fake.calls.count("job_gate") == 1, "岗位守门单次调用"
    # 近同义 → map 基线岗位
    assert by_name["数据标注员"].map_to == {"taxonomy": "jobs", "code": "AID-18"}
    assert "岗位守门" in by_name["数据标注员"].reason
    assert by_name["GIS开发者"].map_to == {"taxonomy": "jobs", "code": "DEV-15"}
    # 改名撞基线岗位名（"GIS工程师"）→ 守门确定性同名映射，零 LLM
    assert by_name["GIS工程师"].map_to == {"taxonomy": "jobs", "code": "DEV-15"}
    assert "同名复检" in by_name["GIS工程师"].reason
    # 场景限定角色 → reject
    rej = by_name["学生软件解决方案中心运营者"]
    assert rej.status == "reject" and "非普适" in rej.reject_reason
    # 叠层同岗 → merge
    assert by_name["前向部署工程师"].merge_into == "GJX-01"
    # 终审无效（幻觉 code）→ 拒绝（宁缺毋滥）
    assert by_name["幻觉code岗位"].status == "reject"
    # 普适新岗位 → keep 并记审计
    kept = by_name["生成式AI算法工程师"]
    assert kept.status == "keep" and "岗位守门通过" in kept.reason and "AID-01" in kept.reason


def test_job_gate_llm_failure_keeps(monkeypatch):
    """守门 LLM 整体失败 → 岗位按初判保留（信号不丢）；确定性同名映射仍生效。"""

    class _Boom:
        def __call__(self, prompt, parse_json=True, max_tokens=None, api_key=None):
            if "岗位体系守门员" in prompt:
                raise RuntimeError("网络瞬断")
            return _MAP_RESP

    monkeypatch.setattr(tm, "call_llm", _Boom())
    cands = [_cand(0, "new_job", "数据标注员"), _cand(1, "new_job", "GIS工程师")]
    decisions = tm.map_signals(cands, _LABELS, _DELTA)
    by_name = {d.name_zh: d for d in decisions}
    # LLM 失败不推翻初判
    assert by_name["数据标注员"].status == "keep" and not by_name["数据标注员"].map_to
    # 确定性同名映射与 LLM 无关，仍然生效
    assert by_name["GIS工程师"].map_to == {"taxonomy": "jobs", "code": "DEV-15"}
