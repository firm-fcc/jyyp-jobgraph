# -*- coding: utf-8 -*-
"""图谱合成自测：gap/Δw/effective 手算 / 合成新 T-S 边与上限 / 空叠层容错 /
合成不修改 base 与 delta（md5 独立性断言）。

运行：cd codes/graph && python fixtures/test_synthesis.py
（单测用 FakeSnap 鸭子类型直控输入；集成用 fixtures 两源 delta 建真快照，
 输出到临时目录，不触碰正式产物。）
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile

import ut

ut.setup("graph", "builder")
ut.isolate()

from synthesis import synthesize, synthesize_edges, compute_gaps, validate_effective  # noqa: E402
from snapshot_builder import build_snapshot         # noqa: E402
import graph_config as config                      # noqa: E402

FIXTURES = {"papers": ut.fixture("papers_delta.json"),
            "news": ut.fixture("news_delta.json")}

# 岗位码/名称动态取自当前基准首岗（勿写死——基准已切 v2，v1 旧码 0107 不在节点表）
_FIRST_JOB = next(iter(json.load(open(config.BASE_NODE_FILES["jobs"], encoding="utf-8"))
                       ["detail"].values()))
JOB_CODE, JOB_NAME = _FIRST_JOB["code"], _FIRST_JOB["name_zh"]


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ✓ {msg}")


def _approx(a, b, tol=1e-4):
    return abs(a - b) <= tol


class FakeSnap:
    """鸭子类型快照：直控 base 边 / strengthenings / job_links / 节点。"""

    def __init__(self, base_edges, strengthenings, job_links, nodes):
        self._edges, self._s, self._jl, self._nodes = base_edges, strengthenings, job_links, nodes

    def edges(self, kind, layer="base"):
        return self._edges.get(kind, [])

    def strengthenings(self):
        return self._s

    def job_links(self):
        return self._jl

    def nodes(self, layer, kind):
        return self._nodes.get(kind, [])

    def node_index(self):
        idx = {}
        for kind, arr in self._nodes.items():
            for n in arr:
                idx[n["id"]] = {"layer": "fake", "kind": kind, "node": n}
        return idx


def _fake_inputs():
    """手算场景：3 gap 实体（1 任务 2 技能）+ 1 被压平实体 + 1 jobs 增强跳过。"""
    base_edges = {
        "job_task": [{"src": JOB_CODE, "src_name": JOB_NAME, "dst": "T-01",
                      "dst_name": "任务一", "relation": "job_task", "weight": 0.5}],
        "job_skill": [{"src": JOB_CODE, "src_name": JOB_NAME, "dst": "T-AI-01",
                       "dst_name": "技能一", "relation": "job_skill", "weight": 0.7}],
        "task_skill": [
            {"src": "T-01", "src_name": "任务一", "dst": "T-AI-01", "dst_name": "技能一",
             "relation": "task_skill", "weight": 0.6},
            {"src": "T-01", "src_name": "任务一", "dst": "T-SW-01", "dst_name": "技能二",
             "relation": "task_skill", "weight": 0.2}],
        "skill_skillpoint": [
            {"src": "T-AI-01", "src_name": "技能一", "dst": "Python", "dst_name": "Python",
             "relation": "skill_skillpoint", "weight": 0.55},
            {"src": "T-SW-01", "src_name": "技能二", "dst": "Git", "dst_name": "Git",
             "relation": "skill_skillpoint", "weight": 0.3}],
    }
    strengthenings = [
        {"taxonomy": "tasks", "code": "T-01", "strength": 0.8},
        {"taxonomy": "tasks", "code": "T-02", "strength": 0.9},          # 无基图 T-S 边 → 合成新边
        {"taxonomy": "skills", "code": "T-AI-01", "strength": 0.6},
        {"taxonomy": "skills", "code": "T-SW-01", "strength": 0.1},
        {"taxonomy": "skills", "code": "T-DA-01", "strength": 0.1},      # E_jd 更高 → gap=0
        {"taxonomy": "jobs", "code": JOB_CODE, "strength": 0.9},           # jobs → 跳过
    ]
    job_links = [
        {"src": "PJ-001", "src_name": "新岗位", "dst": "T-01", "dst_name": "任务一",
         "relation": "job_task", "taxonomy": "tasks", "weight": 0.9},
        {"src": "PJ-001", "src_name": "新岗位", "dst": "T-AI-01", "dst_name": "技能一",
         "relation": "job_skill", "taxonomy": "skills", "weight": 0.9},
    ]
    nodes = {
        "jobs": [{"id": JOB_CODE, "name_zh": JOB_NAME}],
        "tasks": [{"id": "T-01", "name_zh": "任务一"}, {"id": "T-02", "name_zh": "任务二"}],
        "skills": [{"id": "T-AI-01", "name_zh": "技能一"}, {"id": "T-SW-01", "name_zh": "技能二"},
                   {"id": "T-DA-01", "name_zh": "技能三"}],
        "skillpoints": [{"id": "Python", "name_zh": "Python"}, {"id": "Git", "name_zh": "Git"}],
        "new_jobs": [{"id": "PJ-001", "name_zh": "新岗位", "strength": 0.9, "status": "pending"}],
    }
    entity_freq = {"tasks": {"T-01": 0.3, "T-02": 0.1},
                   "skills": {"T-AI-01": 0.2, "T-SW-01": 0.05, "T-DA-01": 0.3}}
    return FakeSnap(base_edges, strengthenings, job_links, nodes), entity_freq


def test_gaps():
    """gap=max(0, strength−E_jd)：任务/技能分表、E_jd 已足者归零、岗位类跳过。"""
    print("== gap 手算 ==")
    snap, ef = _fake_inputs()
    gt, gs, skipped = compute_gaps(snap, ef)
    _assert(_approx(gt["T-01"], 0.5) and _approx(gt["T-02"], 0.8), "gap(任务)=0.5/0.8")
    _assert(_approx(gs["T-AI-01"], 0.4) and _approx(gs["T-SW-01"], 0.05), "gap(技能)=0.4/0.05")
    _assert("T-DA-01" not in gs, "E_jd ≥ strength → gap=0 不修正")
    _assert(skipped == 1, "jobs 类增强跳过计数 1")


def test_synthesis_math():
    """λ 修正四式手算：J-S=λ·gap(S)、T-S=λ·gap(T)·gap(S)、S-SP 父技能降级、job_links→PJ- 新边；双端 gap 合成新 T-S 边。"""
    print("== 四种边 Δw/effective 手算 ==")
    snap, ef = _fake_inputs()
    params = dict(config.SYN_WEIGHTS)
    edges, stats = synthesize_edges(snap, ef, params)
    lj, lts, lsp = params["lambda_j"], params["lambda_ts"], params["lambda_sp"]

    jt = {(e["src"], e["dst"]): e for e in edges["job_task"]}
    _assert(_approx(jt[(JOB_CODE, "T-01")]["delta_weight"], lj * 0.5)
            and _approx(jt[(JOB_CODE, "T-01")]["effective_weight"], 0.5 + lj * 0.5),
            f"J-T: Δw=λ·0.5, eff=0.5+Δw")
    _assert(_approx(jt[("PJ-001", "T-01")]["effective_weight"], 0.9)
            and jt[("PJ-001", "T-01")]["origin"] == "delta",
            "job_links → PJ- 新边（base=0，eff=link weight）")

    js = {(e["src"], e["dst"]): e for e in edges["job_skill"]}
    _assert(_approx(js[(JOB_CODE, "T-AI-01")]["delta_weight"], lj * 0.4),
            "J-S: Δw=λ·gap(S)")

    ts = {(e["src"], e["dst"]): e for e in edges["task_skill"]}
    _assert(_approx(ts[("T-01", "T-AI-01")]["delta_weight"], lts * 0.5 * 0.4)
            and _approx(ts[("T-01", "T-AI-01")]["effective_weight"], 0.6 + lts * 0.2),
            "T-S: Δw=λ·gap(T)·gap(S)")
    _assert(_approx(ts[("T-01", "T-SW-01")]["delta_weight"], lts * 0.5 * 0.05),
            "T-S 弱 gap 也可修正")
    _assert(ts[("T-02", "T-AI-01")]["origin"] == "synthesized"
            and _approx(ts[("T-02", "T-AI-01")]["effective_weight"], lts * 0.8 * 0.4),
            "合成新 T-S 边（T-02×T-AI-01，双端有 gap 无基图边）")
    _assert(ts[("T-02", "T-SW-01")]["origin"] == "synthesized", "第二组合成新边")
    _assert(stats["n_new_ts_edges"] == 2, "新 T-S 边计数 2")

    ssp = {(e["src"], e["dst"]): e for e in edges["skill_skillpoint"]}
    _assert(_approx(ssp[("T-AI-01", "Python")]["delta_weight"], lsp * 0.4)
            and _approx(ssp[("T-SW-01", "Git")]["delta_weight"], lsp * 0.05),
            "S-SP: 按父技能 gap 修正")
    _assert(stats["n_skipped_job_strengthenings"] == 1, "stats 记录 jobs 跳过")


def test_ts_cap():
    """新 T-S 边上限：仅保留最强者。"""
    print("== 合成新 T-S 边上限 ==")
    snap, ef = _fake_inputs()
    params = dict(config.SYN_WEIGHTS)
    params["max_new_ts_edges"] = 1
    edges, stats = synthesize_edges(snap, ef, params)
    synth = [e for e in edges["task_skill"] if e["origin"] == "synthesized"]
    _assert(len(synth) == 1 and synth[0]["dst"] == "T-AI-01",
            "上限 1 → 仅保留最强的 (T-02, T-AI-01)")


def test_empty_delta():
    """空叠层 → effective=base 原样、stats 全零。"""
    print("== 空叠层容错（无 strengthenings/job_links）==")
    snap, _ = _fake_inputs()
    snap._s, snap._jl = [], []
    edges, stats = synthesize_edges(snap, {"tasks": {}, "skills": {}}, dict(config.SYN_WEIGHTS))
    _assert(all(e["delta_weight"] == 0 and e["origin"] == "base"
                for arr in edges.values() for e in arr),
            "无叠层信号 → effective = base 原样")
    _assert(stats["n_gaps"] == 0 and stats["n_new_ts_edges"] == 0, "stats 全零")


def _md5_tree(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for fn in sorted(files):
            if fn.endswith(".json"):
                p = os.path.join(dirpath, fn)
                out[os.path.relpath(p, root)] = hashlib.md5(open(p, "rb").read()).hexdigest()
    return out


def test_integration_independent(tmp):
    """合成层独立性：合成前后 base/ 与 delta/ 全文件 md5 不变；effective 加法合成；重复合成幂等。"""
    print("== 集成：真快照合成 + base/delta 独立性（md5）==")
    out_root = os.path.join(tmp, "graph")
    build_snapshot("2026-05", out_root=out_root, delta_files=FIXTURES)
    base_dir = os.path.join(out_root, "2026-05", "base")

    # 注入 base_builder 产物：基图边 + entity_freq（strength 从快照叠层读取，避免手算 noisy-OR）
    for kind, ek in (("job_task", "job_task"), ("job_skill", "job_skill")):
        with open(os.path.join(base_dir, f"{ek}.json"), "w", encoding="utf-8") as f:
            json.dump({"system_name": "t", "schema_version": "0.1", "window": "2026-05",
                       "relation": ek, "total": 1,
                       "edges": [{"src": JOB_CODE, "src_name": JOB_NAME,
                                  "dst": "T-01" if ek == "job_task" else "T-AI-01",
                                  "dst_name": "x", "relation": ek, "weight": 0.5}]}, f,
                      ensure_ascii=False)
    stg = json.load(open(os.path.join(out_root, "2026-05", "delta", "strengthenings.json"),
                         encoding="utf-8"))["items"]
    t01_strength = next(s["strength"] for s in stg if s["code"] == "T-01")
    with open(os.path.join(base_dir, config.BASE_AUX_FILENAMES["entity_freq"]), "w",
              encoding="utf-8") as f:
        json.dump({"tasks": {"T-01": 0.1}, "skills": {}}, f)

    before = _md5_tree(os.path.join(out_root, "2026-05", "base"))
    before_delta = _md5_tree(os.path.join(out_root, "2026-05", "delta"))
    stats = synthesize("2026-05", out_root=out_root)
    _assert(_md5_tree(os.path.join(out_root, "2026-05", "base")) == before,
            "合成后 base/ 全部文件 md5 不变（独立性）")
    _assert(_md5_tree(os.path.join(out_root, "2026-05", "delta")) == before_delta,
            "合成后 delta/ 全部文件 md5 不变（独立性）")

    eff_dir = os.path.join(out_root, "2026-05", config.EFFECTIVE_SUBDIR)
    _assert(os.path.isdir(eff_dir), "effective/ 已创建")
    errs = validate_effective("2026-05", out_root)
    _assert(not errs, f"validate_effective 通过（错误 {errs}）")

    jt = json.load(open(os.path.join(eff_dir, "job_task.json"), encoding="utf-8"))["edges"]
    got = next(e for e in jt if e["dst"] == "T-01" and e["src"] == JOB_CODE)
    expect_dw = round(config.SYN_LAMBDA_J * (t01_strength - 0.1), 4)
    _assert(_approx(got["delta_weight"], expect_dw),
            f"J-T Δw = λ·(strength−E_jd) = λ·({t01_strength}−0.1)")
    _assert(_approx(got["effective_weight"], round(0.5 + expect_dw, 4)), "effective=base+Δw")
    pj = [e for e in jt if e["src"] == "PJ-001"]
    _assert(len(pj) >= 1 and all(e["origin"] == "delta" for e in pj),
            "job_links 进入 effective J-T（origin=delta）")
    ne = json.load(open(os.path.join(eff_dir, "new_entities.json"), encoding="utf-8"))
    _assert(ne["total"] == stats["n_new_entities"] > 0, f"new_entities 清单 {ne['total']} 条")
    # 重算幂等（内容一致，忽略 created）
    def _strip(o):
        if isinstance(o, dict):
            return {k: _strip(v) for k, v in o.items() if k != "created"}
        if isinstance(o, list):
            return [_strip(v) for v in o]
        return o
    first = _strip(json.load(open(os.path.join(eff_dir, "meta.json"), encoding="utf-8")))
    synthesize("2026-05", out_root=out_root)
    second = _strip(json.load(open(os.path.join(eff_dir, "meta.json"), encoding="utf-8")))
    _assert(first == second, "重复合成幂等（忽略 created）")


def main():
    tmp = tempfile.mkdtemp(prefix="synth_fixture_")
    try:
        test_gaps()
        test_synthesis_math()
        test_ts_cap()
        test_empty_delta()
        test_integration_independent(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n全部通过 ✓")


if __name__ == "__main__":
    main()
