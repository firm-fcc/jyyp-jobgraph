# -*- coding: utf-8 -*-
"""管线阶段助手单测（全离线）：Stage S0 预抽样构建（build_universe / make_presample
触发与未触发 / load_presample 容错）、Stage S 采样文件生成（make_sample 全链：
A 门结果消费 / 近重复过滤 / 预抽样复合）、Stage D0 近重复聚类产物（build_variants
星型聚类保最早 + 变体表落盘）、行级标注迭代（iter_annotated 三列补齐）。"""
import csv
import json
import os
import sys

import pytest

import ut

ut.setup("graph", "builder")
ut.setup("jd_annotate")   # 行级标注引擎位于 jd_annotate 包
ut.isolate()

import graph_config as gconfig
import jd_sample
import jd_pre_sample as s0
import jd_dedup as dd
import annotate_jd as aj
import common as ann_common


def _write_timeline(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["jobid", "job", "funtype", "opentime", "job_information"])
        w.writeheader()
        for i, (title, text, ot) in enumerate(rows):
            w.writerow({"jobid": f"J{i}", "job": title, "funtype": "技术类",
                        "opentime": ot, "job_information": text})


@pytest.fixture()
def derived_tree(tmp_path, monkeypatch):
    jd_dir = tmp_path / "jd"
    derived = tmp_path / "jd_derived"
    jd_dir.mkdir()
    derived.mkdir()
    monkeypatch.setattr(gconfig, "TIMELINE_JD_DIR", str(jd_dir))
    monkeypatch.setattr(gconfig, "JD_DERIVED_DIR", str(derived))
    # jd_dedup / jd_pre_sample 侧的同名路径常量（两模块各持一份引用）
    monkeypatch.setattr(dd.gconfig, "TIMELINE_JD_DIR", str(jd_dir))
    monkeypatch.setattr(dd.gconfig, "JD_DERIVED_DIR", str(derived))
    monkeypatch.setattr(s0.gconfig, "TIMELINE_JD_DIR", str(jd_dir))
    monkeypatch.setattr(jd_sample, "TIMELINE_JD_DIR", str(jd_dir))  # jd_sample 持有本地副本
    return jd_dir, derived


# ---------------- Stage S0 预抽样 ----------------

def test_build_universe_dedup(derived_tree):
    """输入：3 行 CSV（1 条同文重复）。期望输出：记录数 3、指纹唯一 2（两口径分计）。"""
    jd_dir, _ = derived_tree
    rows = [("Java工程师", "负责服务端开发" + "。", "2026-05-01 00:00:00"),
            ("Java工程师", "负责服务端开发" + "。", "2026-05-02 00:00:00"),   # 同文重复
            ("数据分析师", "负责经营分析", "2026-05-03 00:00:00")]
    _write_timeline(str(jd_dir / "2099-01.csv"), rows)
    n, universe = s0.build_universe(str(jd_dir / "2099-01.csv"))
    assert n == 3 and len(universe) == 2                     # 记录数 / 指纹唯一数分口径


def test_make_presample_trigger_and_not(derived_tree, capsys):
    """输入：10 指纹窗口：cap=5 与 cap=100 两轮 + 已存在再跑。期望输出：触发：5 键 w0=2.0 population 记 10/5；未触发：keys=null selected=10；已存在打印"已存在，跳过"。"""
    jd_dir, derived = derived_tree
    _write_timeline(str(jd_dir / "2099-02.csv"),
                    [(f"岗位{i}", f"职责描述{i}" * 5, "2026-05-01 00:00:00") for i in range(10)])
    # cap=5 < unique=10 → 触发：选中恰 5 键，w0 = N/k = 2
    out = s0.make_presample("2099-02", cap=5, salt="s", force=True)
    rec = json.load(open(out, encoding="utf-8"))
    assert len(rec["keys"]) == 5 and abs(rec["weight"] - 2.0) < 1e-9
    assert rec["population"]["unique"] == 10 and rec["population"]["selected"] == 5
    # 未触发（cap ≥ unique）→ keys=None，幂等标记
    out2 = s0.make_presample("2099-02", cap=100, salt="s", force=True)
    rec2 = json.load(open(out2, encoding="utf-8"))
    assert rec2["keys"] is None and rec2["population"]["selected"] == 10
    # 已存在且未 force → 跳过
    s0.make_presample("2099-02", cap=5, salt="s")
    assert "已存在，跳过" in capsys.readouterr().out
    # load_presample 容错
    assert s0.load_presample("1999-01") is None


# ---------------- Stage S 采样文件 ----------------

def test_make_sample_end_to_end(derived_tree, monkeypatch, capsys):
    """输入：4 键 A 门桩结果（3+1 分层）：cap=2/floor=1、cap=1000、含 unclassified 三轮。期望输出：DEV 层 3→2 weight=1.5、keys 3 条带权重；全保 keys=null n=4；A 未跑满 SystemExit。"""
    jd_dir, derived = derived_tree
    rows = [("Java开发工程师", f"负责服务端开发-{i}", "2026-05-01 00:00:00") for i in range(3)] \
        + [("数据分析师", "负责经营分析", "2026-05-02 00:00:00")]
    _write_timeline(str(jd_dir / "2099-03.csv"), rows)
    # A 门结果桩：名称层直收 + 一条未归类（应中止）
    keys = {}
    for title, text, _ in rows:
        keys[ann_common.jd_text_key(title, text)] = {
            "jobs": ["DEV-01"] if "Java" in title else ["DAT-01"],
            "job_code": "DEV-01" if "Java" in title else "DAT-01",
            "tier": 1, "it_related": True}
    stub_cls = dict(keys)
    stub_st = {"rows": 4, "unique": 4, "unique_all": 4, "out_of_scope": 0,
               "excluded": 0, "it_scope_version": "test"}

    def fake_load(csv_path, strict=True):
        return dict(stub_cls), dict(stub_st)

    monkeypatch.setattr(jd_sample.rje, "load_full_classification", fake_load)
    # cap=2 < IT 4 且 DEV 层(3)>floor=1 → 触发降采样：DEV 3→2、DAT 1→1，共 3 键带权重
    out = jd_sample.make_sample("2099-03", cap=2, floor=1, salt="s", force=True)
    rec = json.load(open(out, encoding="utf-8"))
    assert rec["stage"] == "S_sample" and rec["sample"]["sampled"] is True
    assert rec["population"]["it_in_scope"] == 4
    assert set(rec["keys"]) <= set(stub_cls)                  # 键集来自 A 门结果
    assert rec["sample"]["n_sampled"] == 3
    assert rec["sample"]["per_job"]["DEV-01"] == {"k": 2, "weight": 1.5}
    assert all(w == 1.5 for k, w in rec["keys"].items()
               if stub_cls[k]["job_code"] == "DEV-01")        # 逆概率权重入键表
    # 未触 cap 全保：keys=null（Stage B 不过滤）
    out2 = jd_sample.make_sample("2099-03", cap=1000, force=True)
    rec2 = json.load(open(out2, encoding="utf-8"))
    assert rec2["keys"] is None and rec2["sample"]["n_sampled"] == 4
    # A 门未跑满 → 中止
    monkeypatch.setattr(jd_sample.rje, "load_full_classification",
                        lambda p, strict=True: ({"k": {"jobs": [], "tier": None,
                                                       "unclassified": True}},
                                                dict(stub_st)))
    with pytest.raises(SystemExit):
        jd_sample.make_sample("2099-03", force=True)


# ---------------- Stage D0 近重复聚类 ----------------

def test_build_variants_star_cluster(derived_tree, monkeypatch):
    """输入：3 文档（最早代表/最小编辑变体/无关）+ A 门桩。期望输出：变体指向最早代表、代表不在表；load_variants 回读同口径。"""
    jd_dir, derived = derived_tree
    base = "负责" + "数据平台开发与维护，" * 12 + "要求熟悉大数据组件。"
    rows = [
        ("数据开发工程师", base, "2026-05-01 00:00:00"),               # 最早：代表
        ("数据开发工程师", base[:-1], "2026-05-10 00:00:00"),            # 变体（最小编辑）
        ("前端工程师", "负责" + "界面切图与样式还原，" * 14, "2026-05-02 00:00:00"),  # 无关
    ]
    _write_timeline(str(jd_dir / "2099-04.csv"), rows)
    keys = {ann_common.jd_text_key(t, x): {"jobs": ["DAT-01" if "数据" in t else "DEV-01"],
                                           "tier": 1, "it_related": True}
            for t, x, _ in rows}
    monkeypatch.setattr(dd.rje, "load_full_classification",
                        lambda p, strict=True: (dict(keys), {"rows": 3, "unique": 3,
                                                             "excluded": 0, "out_of_scope": 0,
                                                             "it_scope_version": "t"}))
    st = dd.build_variants("2099-04", force=True)
    rec = json.load(open(derived / "2099-04.dedup.json", encoding="utf-8"))
    variants = rec["variants"]
    rep_key = ann_common.jd_text_key(*rows[0][:2])
    var_key = ann_common.jd_text_key(*rows[1][:2])
    assert variants.get(var_key) == rep_key                    # 变体指向最早代表
    assert rep_key not in variants                              # 代表自身不是变体
    # 消费方回读（load_variants 与产物同口径）
    assert dd.load_variants("2099-04") == variants


# ---------------- 行级标注迭代 ----------------

def test_iter_annotated(monkeypatch, tmp_path):
    """输入：2 行原始行（含 work_year=3-5年 / 全空）+ StackAnnotator。期望输出：首行补齐 techstack/tier=1/level=L2/source=work_year；次行空标注且判级为空。"""
    monkeypatch.setattr(ann_common, "JD_STACK_CACHE", str(tmp_path / "none.jsonl"))
    ann = aj.StackAnnotator()
    rows = [{"job": "数据分析工程师", "job_information": "负责数据报表",
             "work_year": "3-5年", "funtype": "技术类"},
            {"job": "综合专员", "job_information": "处理日常事务", "work_year": "", "funtype": ""}]
    out = list(aj.iter_annotated(rows, ann))
    assert len(out) == 2
    new, tier, level, source, wy_lv, text_lv = out[0]
    assert new["techstack"] and tier == 1
    assert level == "L2" and source == "work_year" and wy_lv == "L2"
    new2, tier2, level2, _, wy2, text2 = out[1]
    assert new2["techstack"] == "" and tier2 == 0 and level2 == "" and wy2 is None
