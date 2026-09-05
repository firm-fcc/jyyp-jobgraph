# -*- coding: utf-8 -*-
"""JD 解析评测指标核心（graph/eval_jd_parse.py）单测：分层抽样分配（最大余数法 +
保底）、技能点软匹配归一/配对/覆盖率——官方三维度口径（岗位/任务/技能通过率平均）
的底层函数，保证指标本身可复现、无口径漂移。零 LLM。"""
import ut

ut.setup("graph", "builder")
ut.isolate()

from eval_jd_parse import _alloc, _norm_sp, _prf_sp, _prf, _coverage_sp


# ---------------- 分层抽样分配 ----------------

def test_alloc_under_total_passthrough():
    """总量未超时按原规模全保。"""
    sizes = {"DEV": 5, "OPS": 3}
    assert _alloc(sizes, 100) == {"DEV": 5, "OPS": 3}     # 总量未超 → 全保


def test_alloc_largest_remainder_with_floor():
    """最大余数法分配 + floor 保底 + 层规模上限：四组手算。"""
    # 100 按 55:45 分 → 55/45；floor=3 不触发
    q = _alloc({"A": 55, "B": 45}, 100, floor=3)
    assert sum(q.values()) == 100 and q == {"A": 55, "B": 45}
    # 最大余数法：22 按 9:7:6 分 → raw 3.75/2.92/2.5，floor=3 各保 3，余 1 归余数最大的 B
    q2 = _alloc({"A": 9, "B": 7, "C": 6}, 22, floor=3)
    assert q2 == {"A": 9, "B": 7, "C": 6}                 # 未超总量全保
    q3 = _alloc({"A": 9, "B": 7, "C": 6}, 10, floor=3)
    assert q3 == {"A": 4, "B": 3, "C": 3} and sum(q3.values()) == 10
    #（raw=4.09/3.18/2.73 → floor 各 3 起步、int 部分给 A=4，恰满 10 无余量分配）
    # 层配额不被 floor 抬过其真实规模（min(sizes, …) 上限）
    q4 = _alloc({"A": 8, "B": 2}, 10, floor=3)
    assert q4 == {"A": 8, "B": 2}


# ---------------- 技能点软匹配 ----------------

def test_norm_sp():
    """技能点粒度归一：小写 + 去非字母数字（Vue.js≈VueJS、中英一致）。"""
    assert _norm_sp("Vue.js") == "vuejs"
    assert _norm_sp("ElasticSearch") == _norm_sp("elasticsearch") == "elasticsearch"
    assert _norm_sp("C++") == "c"                          # 非字母数字剔除（与 _prf_sp 配对规则配套）
    assert _norm_sp("机器 学习") == "机器学习"


def test_prf_sp_soft_pairing():
    """软匹配 micro-P/R/F1：粒度变体配对（vue↔vuejs）、错配、双空、pred 冗余。"""
    # 粒度变体：vue vs vuejs 软配对成功（≥3 字符子串包含）
    r = _prf_sp([["Vue.js"]], [["VueJS"]])
    assert r == {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    # 独立变体：sqlserver ⊂ sqlserver2019
    r2 = _prf_sp([["SQLServer2019"]], [["SQLServer"]])
    assert r2["f1"] == 1.0
    # 完全错配
    r3 = _prf_sp([["Python"]], [["Java"]])
    assert r3 == {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    # 双空 → 完美（无 gold 且无 pred 不应惩罚）
    r4 = _prf_sp([[]], [[]])
    assert r4["f1"] == 1.0
    # 多对贪心一对一：pred 多 → 精度下降
    r5 = _prf_sp([["Python", "Go"]], [["Python"]])
    assert r5["precision"] == 0.5 and r5["recall"] == 1.0


def test_prf_exact_sets():
    """精确集合 micro 指标：tp=2 fp=1 fn=1 → P=R=F=2/3。"""
    r = _prf([{"a", "b"}, {"c"}], [{"a"}, {"c", "d"}])    # micro：tp=2 fp=1 fn=1
    assert r["precision"] == 0.6667 and r["recall"] == 0.6667 and r["f1"] == 0.6667


def test_coverage_sp():
    """gold 覆盖率（软匹配）：逐条 gold 独立命中、空集边界。"""
    assert _coverage_sp(["Vue.js", "Go"], ["VueJS"]) == 1.0
    assert _coverage_sp([], ["Python", "Java"]) == 0.0
    assert _coverage_sp([], []) == 1.0                     # 无 gold 不惩罚
    # 每条 gold 独立可命中（非一一配对，允许一次 pred 命中多条 gold 的口径）
    assert _coverage_sp(["机器学习"], ["机器学习", "深度学习"]) == 0.5
