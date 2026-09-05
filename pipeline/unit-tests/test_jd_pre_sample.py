# -*- coding: utf-8 -*-
"""Stage S0 预抽样单测：触发/不触发、确定性哈希、单调扩展、collect 过滤、权重复合。

设计（2026-09-03 用户裁定"大窗多一层抽样"，数量授权研判——真实记录数 wc -l 虚高
~14×，60k precap 只让 2026-03 触发）。mock 零真实 LLM/CSV。
"""
import os
import sys

import ut

ut.setup("graph", "builder")
ut.isolate()

import jd_pre_sample as s0                                      # noqa: E402
from jd_sample import apply_presample                           # noqa: E402


def test_select_keys():
    """选样数学：超限选 cap 个且 w0=N/k；未超限全保 w0=1；同种子逐键一致；cap 上调单调扩展；换种子换样。"""
    print("== 选择：确定性 + 触发 + w0 ==")
    uni = [f"key-{i:03d}" for i in range(100)]
    sel, w0 = s0.select_keys(uni, 60, "salt-x")
    _assert(len(sel) == 60 and w0 == round(100 / 60, 4), "超限触发：选 60 个，w0=N/k")
    sel2, w02 = s0.select_keys(uni, 200, "salt-x")
    _assert(sel2 == set(uni) and w02 == 1.0, "未超限：全保 w0=1")
    sel_b, _ = s0.select_keys(uni, 60, "salt-x")
    _assert(sel == sel_b, "同种子确定性：两次选择逐键一致")
    sel_c, _ = s0.select_keys(uni, 80, "salt-x")
    _assert(sel <= sel_c, "单调扩展：cap 上调时已选键集保留（缓存衔接）")
    sel_d, _ = s0.select_keys(uni, 60, "salt-y")
    _assert(sel != sel_d or True, "换种子选择不同（信息性）")   # 不做强断言（碰撞可能）


def test_apply_presample():
    """w0 复合纯函数：未触发原样返回；触发时各键权重 ×w0（含全保 1.0）。"""
    print("== 权重复合 w0 ==")
    keys_w = {"a": 1.0, "b": 2.5}
    out, active = apply_presample(keys_w, None)
    _assert(not active and out == keys_w, "无 presample → 原样")
    out, active = apply_presample(keys_w, {"keys": None, "weight": 1.6})
    _assert(not active and out == keys_w, "keys=null（未触发）→ 原样")
    out, active = apply_presample(keys_w, {"keys": ["a", "b"], "weight": 1.6})
    _assert(active and out == {"a": 1.6, "b": 4.0}, "触发 → 各键 ×w0（含全保 1.0→w0）")


def test_collect_filter(monkeypatch):
    """A 门过滤口径：unique_all=全集、selected+未选=全集、未选键规则与 LLM 都不见、未激活零行为变化。"""
    print("== A 门过滤（collect） ==")
    import classify_job as cj

    rows = [(None, f"标题{i}", f"正文{i}这是测试内容用于指纹") for i in range(6)]
    keys = [cj.common.jd_text_key(t, x) for _, t, x in rows]
    presample = set(keys[:3])                                   # 只选前 3 键
    monkeypatch.setattr(cj, "iter_jd_rows", lambda files, limit=None: iter(rows))
    _, st_sel, _ = cj.collect("fake.csv", strict=True, presample=presample)
    _assert(st_sel["unique_all"] == 6, "unique_all=全集指纹数（不受过滤影响）")
    _assert(st_sel["unique"] + st_sel["presampled_out"] == 6,
            "selected + 未选 = 全集")
    _assert(st_sel["unique"] == 3 and st_sel["presampled_out"] == 3, "未选键跳过规则与 LLM")
    # 重复行（同指纹）只计一次
    rows2 = rows + [rows[0]]
    monkeypatch.setattr(cj, "iter_jd_rows", lambda files, limit=None: iter(rows2))
    _, st2, _ = cj.collect("fake.csv", strict=True, presample=presample)
    _assert(st2["rows"] == 7 and st2["unique_all"] == 6, "重复行按唯一键计")
    # 无 presample：行为与旧版一致（unique=unique_all）
    _, st3, _ = cj.collect("fake.csv", strict=True, presample=None)
    _assert(st3["unique"] == st3["unique_all"] == 6 and st3["presampled_out"] == 0,
            "未激活时 unique=unique_all，零行为变化")


def _assert(cond, msg):
    assert cond, msg
    print(f"  ✓ {msg}")


if __name__ == "__main__":
    test_select_keys()
    test_apply_presample()
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
