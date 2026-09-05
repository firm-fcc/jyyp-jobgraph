# -*- coding: utf-8 -*-
"""句级分类链与生产端技术栈门补测：Extractor._classify_units（去重 / 缓存 /
LLM 兜底 / 按句频聚合三联结构）、classify_stacks.collect_misses（词库快路 +
必空排除 + 指纹去重的生产端口径）。LLM client 注入桩，零网络。"""
import csv
import json
from collections import Counter

import ut

ut.setup("jd_annotate")
ut.setup("extractor")
ut.isolate()

import config as ext_config
from extractor import Extractor
import classify_stacks


class _StubClient:
    """分类桩：按子串返回匹配（供聚合口径验证）。"""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def classify_sentences(self, units, taxonomy):
        self.calls.append(list(units))
        return {u: self.table.get(u, []) for u in units}


def test_classify_units_dedupe_and_aggregate(tmp_path):
    """单元去重后送 LLM、按原频次聚合三联结构（技能/技能点/映射）。"""
    stub = _StubClient({
        "熟悉Python开发": [{"code": "S-PY", "skillpoints": ["NumPy"]}],
        "负责数据分析": [{"code": "S-ML", "skillpoints": []}],
    })
    ext = Extractor(mode="skill", llm_client=stub, use_cache=False)
    units = ["熟悉Python开发", "负责数据分析", "熟悉Python开发"]   # 第三个重复
    results, agg = ext._classify_units(units, taxonomy=None)
    assert results["负责数据分析"] == [{"code": "S-ML", "skillpoints": []}]
    assert stub.calls == [["熟悉Python开发", "负责数据分析"]]     # 去重后送 LLM
    assert agg["skill_counts"] == {"S-PY": 2, "S-ML": 1}          # 按句频（含重复单元）
    assert agg["skillpoint_counts"] == {"NumPy": 2}
    assert agg["skill_skillpoint_map"] == {"S-PY": {"NumPy": 2}}


def test_classify_units_cache_hit(tmp_path, monkeypatch):
    """句级缓存跨实例复用：同句第二次零 LLM 调用（成本防线）。"""
    monkeypatch.setattr(ext_config, "CACHE_DIR", str(tmp_path))
    stub = _StubClient({"新句子": [{"code": "S-9", "skillpoints": []}]})
    ext1 = Extractor(mode="skill", llm_client=stub, use_cache=True)
    _, agg1 = ext1._classify_units(["新句子"], taxonomy=None)
    assert agg1["skill_counts"] == {"S-9": 1}
    # 第二个实例：命中缓存 → LLM 零调用（同句跨运行只判一次的成本防线）
    stub2 = _StubClient({})
    ext2 = Extractor(mode="skill", llm_client=stub2, use_cache=True)
    _, agg2 = ext2._classify_units(["新句子"], taxonomy=None)
    assert stub2.calls == [] and agg2["skill_counts"] == {"S-9": 1}


def _write_stack_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["job", "job_information"])
        w.writerows(rows)


def test_collect_misses_production_gate(tmp_path, monkeypatch):
    """生产端 collect_misses：标题/正文词库、必空排除表、指纹去重、miss 收集。"""
    import common
    rows = [
        ("数据分析工程师", "负责日常报表"),                    # 标题词库 tier1
        ("综合专员", "要求掌握数据分析工具"),                  # 正文词库 tier2
        ("电镀工艺员", "负责电镀线参数记录"),                  # 排除表（必空域）
        ("综合事务专员", "处理行政综合事务"),                  # miss → 送 LLM
        ("综合事务专员", "处理行政综合事务"),                  # 指纹去重
    ]
    _write_stack_csv(str(tmp_path / "stack.csv"), rows)
    misses, st = classify_stacks.collect_misses(str(tmp_path / "stack.csv"))
    assert st["rows"] == 5 and st["unique"] == 4
    assert st["tier1"] == 1 and st["tier2"] == 1 and st["excluded"] == 1
    assert st["miss"] == 1 and len(misses) == 1
    key = common.jd_text_key(*rows[3])
    assert misses[key]["title"] == "综合事务专员"
    # 断点进度：仅保留 valid_keys 内的键
    prog = tmp_path / "prog.jsonl"
    with open(prog, "w", encoding="utf-8") as f:
        f.write(json.dumps({"k1": ["TS-1"], "k2": ["TS-2"]}) + "\n")
    monkeypatch.setattr(classify_stacks, "PROGRESS", str(prog))
    assert classify_stacks.load_progress({"k1"}) == {"k1": ["TS-1"]}
