# -*- coding: utf-8 -*-
"""graph 出口与重放编排单测：JD 多维汇总行构造（中文名映射 / 非 IT 与抄袭变体
剔除）、参数重放计划校验（α 链完整性 / 窗口洞检测 / dry-run 零执行）。零 LLM。"""
import json
import os

import pytest

import ut

ut.setup("graph", "builder")
ut.isolate()   # graph_config 顶层 import config（builder 的 DELTA_OUTPUT 等），须先弹出冲突绑定

import jd_summary
from jd_summary import _row
import replay


# ---------------- JD 多维汇总 ----------------

def test_row_maps_codes_to_names():
    """行构造：code→中文名（岗位/任务/技能/技术栈/级别）、向量列拼接、计数列。"""
    nm = {"job": {"DEV-01": "算法工程师"}, "skill": {"S-01": "机器学习"},
          "task": {"T-01": "模型训练"}, "techstack": {"TS-01": "AI应用"}}
    rec = {"jd_key": "k", "jobid": "J1", "opentime": "2026-05-01", "title": "算法工程师",
           "funtype": "技术类", "job_code": "DEV-01", "it_related": True, "tier": 1,
           "techstack": ["TS-01"], "level": "L2", "level_source": "work_year",
           "salary": "1-2万", "salary_monthly": 15000, "salary_weight": 1.0,
           "sample_weight": 1.0,
           "skill_vec_01": ["S-01"], "task_vec_01": ["T-01"],
           "skillpoint_map": {"S-01": ["Python"]},
           "skill_vec_prof": {"S-01": "P3"}}
    row = _row(rec, nm)
    assert row["std_job"] == "算法工程师"                       # code → 中文名
    assert row["task_vec_01"] == "模型训练" and row["skill_vec_01"] == "机器学习"
    assert row["techstack"] == "AI应用"
    assert row["level"] == "中级(3-4年)"                       # 级别 code → 展示名
    assert row["skill_vec_prof"] == "机器学习:P3"
    assert row["skillpoint_map"] == "机器学习:Python"
    assert row["n_skills"] == 1 and row["n_skillpoints"] == 1 and row["n_prof"] == 1


def test_write_summary_csv_filters(tmp_path, monkeypatch):
    """仅写 it_related=True；近重复变体（dedup.json）同口径剔除。"""
    derived = tmp_path / "jd_derived"
    derived.mkdir()
    win = "2099-99"                                            # 不存在的窗口：真实 dedup 必为空
    vecs = [
        {"jd_key": "keep", "jobid": "1", "title": "算法工程师", "std_job": "DEV-01",
         "it_related": True, "tier": 1, "salary_monthly": 15000, "tasks": [], "skills": []},
        {"jd_key": "drop_it", "jobid": "2", "it_related": False},
    ]
    with open(derived / f"{win}.jd_vectors.jsonl", "w", encoding="utf-8") as f:
        for v in vecs:
            f.write(json.dumps(v, ensure_ascii=False) + "\n")
    monkeypatch.setattr(jd_summary, "TIMELINE_JD_DIR", str(derived))
    import graph_config
    monkeypatch.setattr(graph_config, "JD_DERIVED_DIR", str(derived))  # load_variants 的 dedup 目录
    # 构造近重复变体：keep2 是 keep 的抄袭变体
    with open(derived / f"{win}.dedup.json", "w", encoding="utf-8") as f:
        json.dump({"variants": {"keep2": "keep"}}, f)
    with open(derived / f"{win}.jd_vectors.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"jd_key": "keep2", "jobid": "3", "it_related": True}, ensure_ascii=False) + "\n")

    nm = {"job": {"DEV-01": "算法工程师"}, "skill": {}, "task": {}, "techstack": {}}
    out_dir = tmp_path / "out"
    path, n = jd_summary.write_summary_csv(win, out_dir=str(out_dir), name_maps=nm)
    assert n == 1                                              # 非 IT 与抄袭变体均剔除
    import csv
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    assert len(rows) == 1 and rows[0]["jd_key"] == "keep"

    # 源文件缺失 → 明确报错（先跑 Stage B+C）
    try:
        jd_summary.write_summary_csv("1999-01", out_dir=str(out_dir), name_maps=nm)
        raise SystemExit("应抛 FileNotFoundError")
    except FileNotFoundError:
        pass


# ---------------- 参数重放编排 ----------------

def _mk_replay_tree(tmp_path, monkeypatch):
    derived = tmp_path / "jd_derived"
    graph = tmp_path / "graph"
    derived.mkdir()
    graph.mkdir()
    for w in ("2022-05", "2022-06", "2022-07"):
        (derived / f"{w}.jd_vectors.jsonl").write_text("{}", encoding="utf-8")
    (graph / "2022-06").mkdir()
    (graph / "2022-06" / "meta.json").write_text("{}", encoding="utf-8")  # 仅 06 建过快照
    monkeypatch.setattr(replay.config, "JD_DERIVED_DIR", str(derived))
    monkeypatch.setattr(replay, "GRAPH_ROOT", str(graph))
    monkeypatch.setattr(replay.config, "GRAPH_ROOT", str(graph))
    return derived, graph


def test_replay_plan_and_dry_run(tmp_path, monkeypatch, capsys):
    """输入：临时窗树（3 窗 jd_vectors、仅 2022-06 有快照 meta）replay(dry_run=True)。期望输出：stats={"windows":3,"dry_run":True}；计划文本区分"仅 base"与"+ 快照/合成"；含"零 LLM"标记。"""
    _mk_replay_tree(tmp_path, monkeypatch)
    stats = replay.replay(["2022-05", "2022-06", "2022-07"], dry_run=True)
    assert stats == {"windows": 3, "dry_run": True}
    out = capsys.readouterr().out
    assert "2022-05：prev=none （仅 base，未建快照）" in out
    assert "2022-06：prev=2022-05 + 快照/合成" in out      # 有快照窗追加快照/合成步
    assert "零 LLM" in out


def test_replay_validation_guards(tmp_path, monkeypatch):
    """输入：三种非法窗口列表：未知窗 / 非最早窗起跑 / 中间有洞。期望输出：三种均 SystemExit（α 链完整性守卫）。"""
    _mk_replay_tree(tmp_path, monkeypatch)
    # 未知窗口
    with pytest.raises(SystemExit):
        replay.replay(["2021-01"], dry_run=True)
    # α 链：必须从最早窗口起整链重建
    with pytest.raises(SystemExit):
        replay.replay(["2022-06", "2022-07"], dry_run=True)
    # 列表中间有洞
    with pytest.raises(SystemExit):
        replay.replay(["2022-05", "2022-07"], dry_run=True)
