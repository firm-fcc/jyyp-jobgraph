# -*- coding: utf-8 -*-
"""Stage S 降采样 fixture：分配数学（cap/floor/权重还原）+ 确定性 + 嵌套性（零 LLM）。"""
import os
import sys

import ut

ut.setup("graph", "builder")
ut.isolate()

from jd_sample import stratified_sample


def _cls_map(strata):
    """{code: n} → 仿真归类结果 {key: {job_code, it_related}}。"""
    out = {}
    for code, n in strata.items():
        for i in range(n):
            out[f"{code}-{i}"] = {"job_code": code, "it_related": True}
    return out


def test_full_retention_under_cap():
    """总量未超 cap：全保、权重恒 1.0。"""
    # 总 IT=45 < cap=100 → 全保，keys 全 1.0
    cm = _cls_map({"DEV-01": 40, "OPS-01": 5})
    n_it, keys, per_job = stratified_sample(cm, cap=100, floor=30, salt="s")
    assert n_it == 45 and len(keys) == 45
    assert all(w == 1.0 for w in keys.values())
    assert per_job["DEV-01"] == {"n": 40, "k": 40, "weight": 1.0}


def test_floor_protects_rare_strata():
    """稀疏层保护与逆概率权重：≤floor 全保、大层按 rate 取整、k×w=n 还原。"""
    # cap=100, 总体=1000+20 → rate=0.098；大层 1000→98(>floor)，稀疏层 20 全保
    cm = _cls_map({"DEV-01": 1000, "SEC-01": 20})
    n_it, keys, per_job = stratified_sample(cm, cap=100, floor=30, salt="s")
    assert per_job["SEC-01"]["k"] == 20            # ≤floor 全保
    assert per_job["DEV-01"]["k"] == 98            # round(rate*1000)，rate=100/1020
    assert abs(per_job["DEV-01"]["weight"] - 1000 / 98) < 1e-3   # per_job 权重展示值 4 位小数
    # 逆概率权重还原层总体：k×w = n
    for code, d in per_job.items():
        assert abs(d["k"] * d["weight"] - d["n"]) < 0.5


def test_determinism_and_nesting():
    """同盐可复现、换盐换样、cap 扩大单调扩展（缓存衔接）、k 单调不减。"""
    cm = _cls_map({"DEV-01": 500})
    _, keys_1, _ = stratified_sample(cm, cap=100, floor=30, salt="seed-x")
    _, keys_2, _ = stratified_sample(cm, cap=100, floor=30, salt="seed-x")
    _, keys_3, _ = stratified_sample(cm, cap=100, floor=30, salt="seed-y")
    assert set(keys_1) == set(keys_2)              # 同盐可复现
    assert set(keys_1) != set(keys_3)              # 换盐换样
    # 嵌套性：cap 扩大（rate 提高）时，原采样键全部保留（扩展只补增量，缓存衔接）
    _, keys_big, _ = stratified_sample(cm, cap=200, floor=30, salt="seed-x")
    assert set(keys_1) <= set(keys_big)
    # k 随 cap 单调不减
    k_small = len(keys_1)
    assert len(keys_big) >= k_small
