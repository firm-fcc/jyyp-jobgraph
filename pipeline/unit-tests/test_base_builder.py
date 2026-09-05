# -*- coding: utf-8 -*-
"""基图边计算自测：薪资解析 / 分层抽样 / 四种边手算 / α 衰减累积 / force 守卫。

运行：cd codes/graph && python fixtures/test_base_builder.py
（mock 抽取器注入，零 LLM；输出到临时目录，不触碰正式产物。）
"""
import csv
import json
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager

import ut

ut.setup("graph", "builder")
ut.isolate()

from base_builder import (parse_salary_monthly, load_funtype_map, build_base,  # noqa: E402
                          merge_history, prev_window_label, _new_freq, accumulate)
import graph_config as config                      # noqa: E402


@contextmanager
def _no_vectors():
    """JD_DERIVED_DIR 指向空临时目录：屏蔽真实 {window}.jd_vectors.jsonl 消费模式
    （该模式会绕过 jd_csv 与 mock 抽取器、直读全量历史聚合），保证用例走
    CSV 注入 + mock 抽取路径，不触碰正式产物。"""
    old = config.JD_DERIVED_DIR
    config.JD_DERIVED_DIR = tempfile.mkdtemp(prefix="jd_derived_ut_")
    try:
        yield
    finally:
        config.JD_DERIVED_DIR = old
        shutil.rmtree(config.JD_DERIVED_DIR, ignore_errors=True)


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ✓ {msg}")


def _approx(a, b, tol=1e-3):
    return abs(a - b) <= tol


def test_salary():
    """薪资字符串 → 月薪中值：万/千/·N薪/万年/元每天/及以下/以上全格式。"""
    print("== 薪资解析 ==")
    cases = {
        "1.5-2万": 17500.0,
        "8千-1.2万": 10000.0,
        "6-8千": 7000.0,
        "1-1.5万·13薪": 12500.0 * 13 / 12,
        "20-30万/年": 250000.0 / 12,
        "200元/天": 200.0 * 22,
        "300-500元/天": 400.0 * 22,
        "3千及以下": 3000.0,
        "2万以上": 20000.0,
    }
    for s, expect in cases.items():
        v = parse_salary_monthly(s)
        _assert(v is not None and abs(v - expect) <= 0.01, f"{s} → {v} ≈ {expect}")
    for s in ("面议", "", None, "薪资不限"):
        _assert(parse_salary_monthly(s) is None, f"{s!r} → None")


def _write_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["jobid", "job", "funtype", "salary", "place",
                                          "work_year", "degree", "company", "opentime",
                                          "job_information", "_table"])
        w.writeheader()
        for i, r in enumerate(rows):
            w.writerow({"jobid": i, "job": r["funtype"], "funtype": r["funtype"],
                        "salary": r.get("salary", ""), "opentime": r.get("opentime", ""),
                        "job_information": r["text"], "_table": "t"})


def _mock_extractors(table):
    """table: {marker_in_text: (task_set, skill_set, sp_map)} → 文本驱动 mock（merged 一调用出全）。"""
    def fn(text):
        for marker, (tasks, skills, sp_map) in table.items():
            if marker in text:
                return {
                    "skill_counts": {s: 1 for s in skills},
                    "task_counts": {t: 1 for t in tasks},
                    "skillpoint_counts": {sp: 1 for sps in sp_map.values() for sp in sps},
                    "skill_skillpoint_map": {s: {sp: 1 for sp in sps}
                                             for s, sps in sp_map.items()},
                }
        return {"skill_counts": {}, "task_counts": {},
                "skillpoint_counts": {}, "skill_skillpoint_map": {}}
    return {"merged": fn}


# 两个真实岗位（funtype 命中 jobs0806 映射；复合名走 " or " 拆分）
_FT_MAP = load_funtype_map()
JOB_A = _FT_MAP["软件工程师"]
JOB_B = _FT_MAP["运维工程师"]
_PAD = "岗位职责说明。" * 16   # 填充至 min_text_chars(100) 以上（7 字 × 16 = 112）

_TABLE_W1 = {  # 窗口 1 的抽取结果表（按文本 marker）
    "JD-A": ({"T-01"}, {"T-AI-01", "T-SW-01"}, {"T-AI-01": {"Python"}}),
    "JD-B": ({"T-01", "T-02"}, {"T-SW-01"}, {}),
}


def _rows_window1():
    return ([{"funtype": "软件工程师", "salary": "1-2万", "text": f"JD-A-{i} {_PAD}"} for i in range(3)]
            + [{"funtype": "运维工程师", "salary": "6-8千", "text": f"JD-B-{i} {_PAD}"} for i in range(1)])


def test_edges_math(tmp):
    """四种边（J-T/J-S/T-S/S-SP）与实体频次 E_jd 按手算分数断言。"""
    print("== 四种边手算（salary_weight=False，权重全 1）==")
    out_root = os.path.join(tmp, "graph")
    csv_path = os.path.join(tmp, "2025-01.csv")
    _write_csv(csv_path, _rows_window1())
    with _no_vectors():
        stats = build_base("2025-01", jd_csv=csv_path, salary_weight=False,
                           extractors=_mock_extractors(_TABLE_W1), out_root=out_root)

    base_dir = os.path.join(out_root, "2025-01", "base")
    edges = {k: json.load(open(os.path.join(base_dir, f"{k}.json"), encoding="utf-8"))["edges"]
             for k in config.BASE_EDGE_KINDS}
    jt = {(e["src"], e["dst"]): e["weight"] for e in edges["job_task"]}
    js = {(e["src"], e["dst"]): e["weight"] for e in edges["job_skill"]}
    ts = {(e["src"], e["dst"]): e["weight"] for e in edges["task_skill"]}
    ssp = {(e["src"], e["dst"]): e["weight"] for e in edges["skill_skillpoint"]}

    _assert(_approx(jt[(JOB_A, "T-01")], 1.0), f"J-T: {JOB_A}→T-01 = 3/3 = 1.0")
    _assert(_approx(jt[(JOB_B, "T-01")], 1.0) and _approx(jt[(JOB_B, "T-02")], 1.0),
            "J-T: B→T-01/T-02 = 1/1")
    _assert(_approx(js[(JOB_A, "T-AI-01")], 1.0) and _approx(js[(JOB_B, "T-SW-01")], 1.0),
            "J-S presence 比例正确")
    _assert(_approx(ts[("T-01", "T-AI-01")], 0.75), "T-S: T-01→T-AI-01 共现 3/W(T-01)=4 → 0.75")
    _assert(_approx(ts[("T-01", "T-SW-01")], 1.0) and _approx(ts[("T-02", "T-SW-01")], 1.0),
            "T-S: 全共现对 = 1.0")
    _assert(_approx(ssp[("T-AI-01", "Python")], 1.0), "S-SP: T-AI-01→Python = 3/3")
    _assert(("T-AI-01", "Java") not in ssp, "未提及的技能点不生成边")

    ef = json.load(open(os.path.join(base_dir, config.BASE_AUX_FILENAMES["entity_freq"]),
                        encoding="utf-8"))
    _assert(_approx(ef["tasks"]["T-01"], 1.0) and _approx(ef["tasks"]["T-02"], 0.25),
            "E_jd: T-01=4/4、T-02=1/4")
    _assert(_approx(ef["skills"]["T-AI-01"], 0.75), "E_jd: T-AI-01=3/4")
    _assert(_approx(ef["skillpoints"]["Python"], 0.75), "E_jd: Python=3/4")
    _assert(all(e.get("src_name") and e.get("dst_name") for arr in edges.values() for e in arr),
            "边端点名称回填完整")
    _assert(stats["scan"]["n_sampled"] == 4, f"抽样 4 条（实际 {stats['scan']['n_sampled']}）")


def test_force_guard(tmp):
    """边文件已非空且未 --force → FileExistsError（与快照覆盖约定一致）。"""
    print("== force 守卫 ==")
    # 自包含：独立 out_root 首建成功 → 同窗二次构建（无 --force）应拒绝。
    # （原版隐式依赖 test_edges_math 在共享 out_root 留下的边文件，跨用例耦合）
    out_root = os.path.join(tmp, "graph_guard")
    csv_path = os.path.join(tmp, "2025-01g.csv")
    _write_csv(csv_path, _rows_window1())
    ext = _mock_extractors(_TABLE_W1)
    with _no_vectors():
        build_base("2025-01", jd_csv=csv_path, salary_weight=False, extractors=ext,
                   out_root=out_root)
    try:
        with _no_vectors():
            build_base("2025-01", jd_csv=csv_path, salary_weight=False, extractors=ext,
                       out_root=out_root)
        _assert(False, "应拒绝覆盖")
    except FileExistsError:
        _assert(True, "边已非空时拒绝覆盖（FileExistsError）")


def test_decay_chain(tmp):
    """α 衰减链：freq = 新证据 + α×历史；J-T 权重分子分母同比不变；技能点 windows 跨窗合并。"""
    print("== α 衰减跨窗口累积（2025-01 → 2025-02）==")
    out_root = os.path.join(tmp, "graph2")
    csv1 = os.path.join(tmp, "d1.csv")
    _write_csv(csv1, _rows_window1())
    with _no_vectors():
        build_base("2025-01", jd_csv=csv1, salary_weight=False,
                   extractors=_mock_extractors(_TABLE_W1), out_root=out_root)
    freq1 = json.load(open(os.path.join(out_root, "2025-01", "base",
                                        config.BASE_AUX_FILENAMES["freq"]), encoding="utf-8"))["freq"]

    # 窗口 2：只有 1 条 B 类 JD（T-01/T-02 + T-SW-01），历史自动读 2025-01
    csv2 = os.path.join(tmp, "d2.csv")
    _write_csv(csv2, [{"funtype": "运维工程师", "salary": "6-8千", "text": f"JD-B-x {_PAD}"}])
    with _no_vectors():
        build_base("2025-02", jd_csv=csv2, salary_weight=False,
                   extractors=_mock_extractors(_TABLE_W1), out_root=out_root)
    freq2 = json.load(open(os.path.join(out_root, "2025-02", "base",
                                        config.BASE_AUX_FILENAMES["freq"]), encoding="utf-8"))["freq"]

    a = config.GB_ALPHA
    _assert(_approx(freq2["total"], 1 + a * freq1["total"]), f"total = 1 + α·{freq1['total']}")
    _assert(_approx(freq2["tasks"]["T-01"], 1 + a * 4.0), "W(T-01) = 1 + α·4")
    _assert(_approx(freq2["skills"]["T-AI-01"], a * 3.0), "W(T-AI-01) = 0 + α·3（本窗无新证据）")
    _assert(_approx(freq2["jobs"][JOB_A]["w"], a * 3.0), "W(J_A) 纯历史衰减")

    jt2 = {(e["src"], e["dst"]): e["weight"] for e in json.load(open(
        os.path.join(out_root, "2025-02", "base", "job_task.json"), encoding="utf-8"))["edges"]}
    _assert(_approx(jt2[(JOB_A, "T-01")], (a * 3.0) / (a * 3.0)), "J-T 权重对衰减不变（分子分母同比）")

    sp = json.load(open(os.path.join(out_root, "2025-02", "base", "skillpoints.json"),
                        encoding="utf-8"))["skillpoints"]
    _assert(sp["Python"]["windows"] == ["2025-01", "2025-02"], "技能点 windows 跨窗口合并")


def test_merge_history_unit():
    """merge_history 纯函数：总量/任务/岗位分别按新+α×旧合成。"""
    print("== merge_history 单元 ==")
    new = _new_freq()
    accumulate(new, "J1", 2.0, {"T1"}, {"S1"}, {"S1": {"sp1"}})
    prev = {"total": 10.0, "jobs": {"J2": {"w": 4.0, "tasks": {"T1": 2.0}, "skills": {}}},
            "tasks": {"T1": 2.0}, "skills": {}, "skillpoints": {}, "task_skill": {},
            "skill_skillpoint": {}}
    out = merge_history(new, prev, 0.5)
    _assert(_approx(out["total"], 2 + 5.0), "total = 2 + 0.5·10")
    _assert(_approx(out["tasks"]["T1"], 2 + 1.0), "T1 = 2 + 0.5·2")
    _assert(_approx(out["jobs"]["J2"]["w"], 2.0), "历史岗位 J2 衰减保留")
    _assert(_approx(out["jobs"]["J1"]["w"], 2.0), "新岗位 J1 原值")
    _assert(prev_window_label("2026-01") == "2025-12" and prev_window_label("2026-Q1") == "2025-Q4",
            "prev_window_label 月/季推导")


def test_skill_prof(tmp):
    """技能熟练度分布聚合（skill_prof.json）：评估器按 JD 出级别，分布按 JD×技能计数。"""
    print("== 技能熟练度分布（skill_prof.json，真实聚合 + mock 评估器）==")
    ut.setup("extractor")
    import jd_proficiency  # noqa: F401  预加载（聚合为纯函数，不触 config/LLM）
    out_root = os.path.join(tmp, "graph3")
    csv_path = os.path.join(tmp, "2025-01p.csv")
    _write_csv(csv_path, _rows_window1())

    def _s(code, name, level, suff):
        return {"team_skill_id": code, "name_zh": name, "requirement_level": level,
                "evidence_sufficiency": suff, "dimensions": {}, "reason": "mock",
                "uncertainty": [], "evidence": ["句"], "markers": [], "years_hints": [],
                "flags": [], "review_required": False}

    class _MockProf:
        """评估器契约 mock：evaluate_jd(text, profile) -> 记录 dict。"""
        def evaluate_jd(self, text, profile=None):
            if "JD-A" in text:
                skills = {"T-AI-01": _s("T-AI-01", "机器学习与深度学习", "P3", "sufficient"),
                          "T-SW-01": _s("T-SW-01", "程序设计与软件工程", "U", "insufficient")}
            else:
                skills = {"T-SW-01": _s("T-SW-01", "程序设计与软件工程", "P2", "sufficient")}
            return {"key": "mock", "rubric_version": "test", "skills": skills, "n_calls": 0}

    ext = _mock_extractors(_TABLE_W1)
    ext["prof"] = _MockProf()
    with _no_vectors():
        stats = build_base("2025-01", jd_csv=csv_path, salary_weight=False, extractors=ext,
                           out_root=out_root)
    sp = json.load(open(os.path.join(out_root, "2025-01", "base",
                                     config.BASE_AUX_FILENAMES["skill_prof"]), encoding="utf-8"))
    d = sp["skills"]["T-AI-01"]
    _assert(d["n"] == 3 and d["levels"]["P3"] == 3, "T-AI-01: 3×P3")
    sw = sp["skills"]["T-SW-01"]
    _assert(sw["levels"]["U"] == 3 and sw["levels"]["P2"] == 1, "T-SW-01: 3×U + 1×P2")
    _assert(sp["n_jds"] == 4, "n_jds = 4（全部抽样 JD）")
    _assert(stats["n_prof_jds"] == 4 and stats["n_prof_pairs"] == 7,
            "build_info 统计：4 JD / 7 对")
    _assert(sp["rubric_version"] and sp["window"] == "2025-01", "meta（版本/窗口）回填")


def main():
    tmp = tempfile.mkdtemp(prefix="gbase_fixture_")
    try:
        test_salary()
        test_edges_math(tmp)
        test_force_guard(tmp)
        test_decay_chain(tmp)
        test_merge_history_unit()
        test_skill_prof(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n全部通过 ✓")


if __name__ == "__main__":
    main()
