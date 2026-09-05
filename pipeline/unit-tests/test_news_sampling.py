# -*- coding: utf-8 -*-
"""新闻月度降采样（2026-08-30 用户裁定）：先抽样再筛选。

- 确定性：random.Random(窗口种子)，同窗重跑抽样一致（可重演）；
- 上限内月份不受影响；抽样记录落 data/timeline/news_derived/{window}.sample.json。
"""
import json
import os
import sys

import ut

_HERE = os.path.dirname(os.path.abspath(__file__))
_BUILDER_DIR = ut.path("builder")
_NEWS_DIR = ut.path("news_signal")


class _R:
    def __init__(self, i):
        self.doc_id = f"d{i}"
        self.pub_date = "2023-07-15"
        self.title = f"新闻{i}"
        self.text = "正文"


def _news_delta():
    for m in ("config", "llm"):
        sys.modules.pop(m, None)   # 跨包冲突名按本助手的路径序重新解析
    for d in (_BUILDER_DIR, _NEWS_DIR):
        if d in sys.path:
            sys.path.remove(d)
        sys.path.insert(0, d)
    return __import__("news_delta", fromlist=["news_delta"])


def test_news_sample_cap_deterministic(tmp_path, monkeypatch):
    """输入：500 条池 cap=100 同种子两次抽样。期望输出：两次 doc_id 序列一致；sample.json 记 pool_size/cap/doc_ids 与实抽一致。"""
    nd = _news_delta()
    monkeypatch.setattr(nd.config, "NEWS_SAMPLE_CAP", 100)
    monkeypatch.setattr(nd.config, "NEWS_DERIVED_DIR", str(tmp_path))
    records = [_R(i) for i in range(500)]
    s1 = nd._apply_sample_cap("2099-01", records)
    s2 = nd._apply_sample_cap("2099-01", records)
    assert len(s1) == 100
    assert [r.doc_id for r in s1] == [r.doc_id for r in s2], "同窗种子重跑一致（可重演）"
    rec = json.load(open(tmp_path / "2099-01.sample.json", encoding="utf-8"))
    assert rec["pool_size"] == 500 and rec["cap"] == 100 and rec["n_sampled"] == 100
    assert rec["doc_ids"] == [r.doc_id for r in s1]
    # 不同窗口种子 → 不同抽样（可区分）
    s3 = nd._apply_sample_cap("2099-02", records)
    assert [r.doc_id for r in s3] != [r.doc_id for r in s1]


def test_news_sample_cap_noop_within_cap(tmp_path, monkeypatch):
    """输入：50 条池 cap=100。期望输出：原列表原样返回（同一对象）；不落 sample.json。"""
    nd = _news_delta()
    monkeypatch.setattr(nd.config, "NEWS_SAMPLE_CAP", 100)
    monkeypatch.setattr(nd.config, "NEWS_DERIVED_DIR", str(tmp_path))
    records = [_R(i) for i in range(50)]
    out = nd._apply_sample_cap("2099-01", records)
    assert out is records and len(out) == 50, "上限内月份不抽样、原序返回"
    assert not (tmp_path / "2099-01.sample.json").exists(), "未抽样不写记录"
