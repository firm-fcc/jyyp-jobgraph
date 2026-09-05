# -*- coding: utf-8 -*-
"""taxonomy_mapper 守门终审（recheck_keeps）单测：keep 判定的第二道独立 LLM 修订。

初判在粒度边界不稳定（实测同一候选两批次判出相反结果）——终审按不同视角提示词复核：
被涵盖→map_to / 兄弟环节→merge_into 或同批归簇改名 / 确无涵盖→保留记审计 / 无效→拒绝。
mock 零真实 LLM（按提示词"守门员"标记分发两阶段调用）。
"""
import os
import sys
import types

import ut

ut.setup("graph", "builder", "extractor")
# config 先绑定 builder 版（与 test_paper_delta_mention 同款导入舞步）
from delta_store import DeltaStore                             # noqa: E402,F401
ut.setup("extractor")

import taxonomy_mapper as tm                                   # noqa: E402
from signal_extractor import Candidate                         # noqa: E402


_LABELS = {"tasks": [{"code": "T-13", "name_zh": "数据分析", "name_en": ""},
                     {"code": "T-01", "name_zh": "应用软件开发", "name_en": ""}],
           "skills": [{"code": "S-01", "name_zh": "机器学习", "name_en": ""}],
           "jobs": []}
_DELTA = [{"id": "PT-001", "name_zh": "联邦学习运维平台", "array": "new_tasks"}]

# 初判（PROMPT_MAP 视角）：四条 task 全 keep + 一条 skillpoint keep（不进终审）
_MAP_RESP = {"decisions": [
    {"index": 0, "final_kind": "new_task", "name_zh": "数据准备优化", "name_en": "",
     "status": "keep", "map_to": None, "merge_into": None, "reject_reason": "", "reason": "初判keep"},
    {"index": 1, "final_kind": "new_task", "name_zh": "脑波数据采集", "name_en": "",
     "status": "keep", "map_to": None, "merge_into": None, "reject_reason": "", "reason": "初判keep"},
    {"index": 2, "final_kind": "new_task", "name_zh": "脑波标注数据集构建", "name_en": "",
     "status": "keep", "map_to": None, "merge_into": None, "reject_reason": "", "reason": "初判keep"},
    {"index": 3, "final_kind": "new_task", "name_zh": "联邦学习平台运维", "name_en": "",
     "status": "keep", "map_to": None, "merge_into": None, "reject_reason": "", "reason": "初判keep"},
    {"index": 4, "final_kind": "new_task", "name_zh": "幻觉code候选", "name_en": "",
     "status": "keep", "map_to": None, "merge_into": None, "reject_reason": "", "reason": "初判keep"},
    {"index": 5, "final_kind": "skillpoint", "name_zh": "Kafka", "name_en": "",
     "status": "keep", "map_to": None, "merge_into": None, "reject_reason": "", "reason": "工具"},
]}

# 终审（守门员视角）：map / 同批归簇 / keep / 幻觉 code；"脑波标注数据集构建"由归簇联动改名
_RECHECK_RESP = [
    {"name": "数据准备优化", "action": "map", "taxonomy": "tasks", "code": "T-13"},
    {"name": "脑波数据采集", "action": "merge", "target": "脑波标注数据集构建",
     "cluster_name": "脑波数据处理"},
    {"name": "联邦学习平台运维", "action": "keep", "nearest": "T-01", "why_not": "基线无隐私计算运维"},
    {"name": "幻觉code候选", "action": "map", "taxonomy": "tasks", "code": "T-99"},   # code 无效
]


class _FakeLLM:
    def __init__(self):
        self.calls = []

    def __call__(self, prompt, parse_json=True, max_tokens=None, api_key=None):
        self.calls.append("recheck" if "守门员" in prompt else "map")
        return _RECHECK_RESP if "守门员" in prompt else _MAP_RESP


def _cand(i, kind, name):
    rec = types.SimpleNamespace(doc_id=f"d{i}", pub_date="2022-07-01")
    return Candidate(i, rec, kind, name, "", "", "依据", [], "high")


def test_recheck_keeps_revisions(monkeypatch):
    """守门修订：数据准备优化被映射 T-13；同批兄弟条目归簇改名；真新条目保留并附 nearest/理由。"""
    fake = _FakeLLM()
    monkeypatch.setattr(tm, "call_llm", fake)
    cands = [_cand(0, "new_task", "数据准备优化"), _cand(1, "new_task", "脑波数据采集"),
             _cand(2, "new_task", "脑波标注数据集构建"), _cand(3, "new_task", "联邦学习平台运维"),
             _cand(4, "new_task", "幻觉code候选"), _cand(5, "skillpoint", "Kafka")]
    decisions = tm.map_signals(cands, _LABELS, _DELTA)
    by_name = {d.name_zh: d for d in decisions}
    assert fake.calls.count("recheck") == 1, "终审单次调用"
    # 被涵盖 → map_to 既有任务
    assert by_name["数据准备优化"].map_to == {"taxonomy": "tasks", "code": "T-13"}
    # 兄弟环节 → 同批归簇：两条都被改名为簇名（store 按名去重自然合并）
    assert "脑波数据处理" in by_name and "脑波数据采集" not in by_name \
        and "脑波标注数据集构建" not in by_name
    # 确无涵盖 → 保留并记审计
    kept = by_name["联邦学习平台运维"]
    assert kept.status == "keep" and "守门通过" in kept.reason and "T-01" in kept.reason
    # 终审无效（幻觉 code）→ 拒绝（宁缺毋滥）
    assert by_name["幻觉code候选"].status == "reject"
    # skillpoint 不进终审、判定不变
    assert by_name["Kafka"].status == "keep" and by_name["Kafka"].final_kind == "skillpoint"
