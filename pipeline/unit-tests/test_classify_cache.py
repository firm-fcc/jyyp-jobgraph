# -*- coding: utf-8 -*-
"""Stage A 归类缓存层单测：jobcls 窗口缓存写读回环与新鲜度守卫（CSV 指纹 / 体系
sha / strict 口径任一变更即失效）、LLM 判定缓存合并（tier3 / unclassified）、
LLM 输出 JSON 数组提取、断点进度键过滤。零 LLM（只读写临时文件）。"""
import json
import os
import time

import ut

ut.setup("jd_annotate")

import common
import classify_job as cj
from classify_job import (merged_classification, parse_array, load_progress,
                          write_jobcls_cache, read_jobcls_cache)


def _write_csv(path, rows):
    import csv
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["job", "job_information"])
        w.writerows(rows)


# ---------------- jobcls 窗口缓存 ----------------

def test_jobcls_cache_roundtrip(tmp_path, monkeypatch):
    """缓存写读回环 + 三重新鲜度守卫：CSV mtime/size、体系 sha256、strict 口径任一变更即失效。"""
    csv_path = tmp_path / "2099-12.csv"
    _write_csv(str(csv_path), [("Java开发工程师", "负责服务端开发")])
    cache_path = tmp_path / "2099-12.jobcls.json"
    monkeypatch.setattr(cj, "_jobcls_path", lambda p: str(cache_path))
    cls = {common.jd_text_key("Java开发工程师", "负责服务端开发"):
           {"jobs": ["DEV-01"], "tier": 1, "non_it": False}}
    st = {"rows": 1, "unique": 1}
    write_jobcls_cache(str(csv_path), cls, st, strict=False)
    assert cache_path.exists()

    got, got_st = read_jobcls_cache(str(csv_path), strict=False)
    assert got == cls and got_st["rows"] == 1
    # 口径变更（strict）→ 缓存失效
    assert read_jobcls_cache(str(csv_path), strict=True) == (None, None)
    # 源 CSV 变更（mtime/size）→ 失效
    time.sleep(0.02)
    _write_csv(str(csv_path), [("Java开发工程师", "负责服务端开发（更新职责）"),
                               ("数据分析师", "负责经营分析")])
    assert read_jobcls_cache(str(csv_path), strict=False) == (None, None)
    # 体系 sha 变更 → 失效
    write_jobcls_cache(str(csv_path), cls, st, strict=False)
    monkeypatch.setattr(cj, "_jobs_v2_sha", lambda: "deadbeef")
    assert read_jobcls_cache(str(csv_path), strict=False) == (None, None)


# ---------------- LLM 判定合并 ----------------

def test_merged_classification_with_llm_cache(tmp_path, monkeypatch):
    """规则层 + jd_job_cache LLM 判定合并：命中缓存 tier3、无缓存 unclassified 标记。"""
    csv_path = tmp_path / "2099-11.csv"
    rows = [("Java开发工程师", "负责服务端开发"),                    # 名称层直收
            ("项目经理", "负责厂房建设进度跟进与施工方日常对接"),      # 泛词 → LLM（有缓存）
            ("综合专员", "处理日常综合事务，无技术内容描述")]          # LLM 无缓存 → unclassified
    _write_csv(str(csv_path), rows)
    k_ambig = common.jd_text_key(rows[1][0], rows[1][1])
    k_miss = common.jd_text_key(rows[2][0], rows[2][1])
    llm_cache = tmp_path / "jd_job_cache.jsonl"
    with open(llm_cache, "w", encoding="utf-8") as f:
        f.write(json.dumps({"key": k_ambig, "jobs": [], "non_it": True}, ensure_ascii=False) + "\n")
        f.write(json.dumps({"key": "无关键", "jobs": ["DEV-01"]}, ensure_ascii=False) + "\n")
    monkeypatch.setattr(cj, "JD_JOB_CACHE", str(llm_cache))

    cls_raw, st = merged_classification(str(csv_path))
    tiers = {v["tier"] for v in cls_raw.values()}
    assert 1 in tiers and 3 in tiers                            # 规则层 + LLM 层并存
    assert cls_raw[k_ambig] == {"jobs": [], "tier": 3, "non_it": True}
    assert cls_raw[k_miss].get("unclassified") is True          # 无缓存 → 待跑标记
    assert st["miss"] == 2

    # 缓存文件不存在 → 全部 miss 记 unclassified（A 未跑满态）
    monkeypatch.setattr(cj, "JD_JOB_CACHE", str(tmp_path / "none.jsonl"))
    cls_raw2, _ = merged_classification(str(csv_path))
    assert cls_raw2[k_ambig].get("unclassified") is True


# ---------------- 杂项工具 ----------------

def test_parse_array_tolerant():
    """LLM 原文提取 JSON 数组：markdown 围栏与前后杂讯容忍，无数组报错。"""
    assert parse_array('```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert parse_array('结论：[{"jobs": ["DEV-01"]}] 以上') == [{"jobs": ["DEV-01"]}]
    try:
        parse_array("没有数组")
        raise SystemExit("应抛 ValueError")
    except ValueError:
        pass


def test_load_progress_filters_unknown_keys(tmp_path):
    """断点进度加载：多键行/空行容错，仅保留 valid_keys 内的键。"""
    p = tmp_path / "progress.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"k1": {"jobs": ["DEV-01"]}}) + "\n")
        f.write(json.dumps({"k2": {"jobs": []}, "k3": 1}) + "\n")   # 同行多键
        f.write("\n")                                               # 空行容错
    done = load_progress(str(p), valid_keys={"k1", "k3"})
    assert set(done) == {"k1", "k3"}
    assert load_progress(str(tmp_path / "none.jsonl"), {"k1"}) == {}
