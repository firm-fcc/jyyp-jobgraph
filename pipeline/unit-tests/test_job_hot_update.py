# -*- coding: utf-8 -*-
"""builder 岗位热更新（关联分析）助手单测：来源推断、岗位证据聚合（doc_id 分块 /
长度上限 / 最早日期 / 最高档）、关联候选校验（定义与证据必填、超长名降级）、
关联链接去重。零 LLM。"""
import ut

ut.setup("builder")
ut.isolate()

from job_hot_update import (infer_source_kind, _build_job_evidence_text, _job_pub_date,
                            _job_tier, _validate_candidate, _dedup_links, _JobAssocRecord)

_JOB = {
    "id": "GJ-TEST", "name_zh": "隐私计算工程师",
    "evidence": {
        "doc-a": {"date": "2026-02-01", "tier": "S", "sentences": ["负责隐私计算平台建设", "熟悉联邦学习框架"]},
        "doc-b": {"date": "2026-01-15", "tier": "A", "sentences": ["参与多方安全计算研发"]},
    },
}


def test_infer_source_kind():
    """按文件名判源：news/jd/papers/缺省回退。"""
    assert infer_source_kind("classify/DeltaG/news_delta.json") == "news"
    assert infer_source_kind("a/b/jd_delta.json") == "jd"
    assert infer_source_kind("x/papers_delta.json") == "papers"
    assert infer_source_kind("x/other.json") == "papers"       # 缺省回退
    assert infer_source_kind("x/other.json", fallback="news") == "news"


def test_evidence_aggregation():
    """证据聚合：按 doc_id 分块排序、长度上限截断、畸形证据容错；最早日期/最高档。"""
    text = _build_job_evidence_text(_JOB, max_chars=1000)
    assert "[doc-a] 负责隐私计算平台建设" in text and "[doc-b] 参与多方安全计算研发" in text
    # doc_id 排序聚合 + 上限截断（不会超长爆炸）
    assert len(text) <= 1000 + 60
    assert _build_job_evidence_text({"evidence": {}}, 100) == ""
    assert _build_job_evidence_text({"evidence": "bad-shape"}, 100) == ""   # 容错
    assert _job_pub_date(_JOB) == "2026-01-15"                  # 最早证据日期
    assert _job_tier(_JOB) == "S"                               # 最高档


def test_assoc_record_doc_id():
    """合成记录稳定主键：job_assoc:{job_id}（证据幂等合并锚）。"""
    r = _JobAssocRecord("GJ-01", "2026-01-01", "A")
    assert r.doc_id == "job_assoc:GJ-01"                        # 幂等合并的稳定主键


def test_validate_candidate_rules():
    """关联候选校验：名称长度/定义/证据三防线、非法置信度降级、非 dict 拒收。"""
    rec = _JobAssocRecord("GJ-01")
    ok = {"name_zh": "联邦学习", "definition": "跨机构联合建模", "evidence": ["句1"], "confidence": "high"}
    c = _validate_candidate(ok, rec, "new_skill", 0)
    assert c is not None and c.record.doc_id == "job_assoc:GJ-01"
    # 定义 / 证据 / 名称长度三道防线
    for bad in (dict(ok, definition=""), dict(ok, evidence=[]), dict(ok, name_zh="短"),
                "not-a-dict"):
        assert _validate_candidate(bad, rec, "new_skill", 0) is None
    # 非法置信度降级不丢弃
    low = _validate_candidate(dict(ok, confidence="ultra"), rec, "new_task", 1)
    assert low is not None and low.confidence == "low"


def test_dedup_links():
    """关联链接去重：(taxonomy, code) 键保序去重。"""
    a = {"taxonomy": "tasks", "code": "T-01", "name": "任务A"}
    a2 = dict(a)
    b = {"taxonomy": "tasks", "code": "T-02", "name": "任务B"}
    assert _dedup_links([a, a2, b]) == [a, b]              # (taxonomy, code) 去重保序
    assert _dedup_links([]) == []
