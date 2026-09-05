# -*- coding: utf-8 -*-
"""builder 热更新基础设施单测：分层抽样器（配额策略 / 去重 / 消费断点 / 可复现）、
体系存储（编码续号 / 增改并 / 读写回环 / 模式推断）、监督 Agent 类型规整与越界
防御（注入 mock LLM，零网络）、提案应用（同名防重 / merge / modify / 异常隔离）。"""
import json

import ut

ut.setup("builder")
ut.isolate()

from sampler import StratifiedSampler
from taxonomy_store import TaxonomyStore
import supervisor
from apply import apply_updates


def _items(n_per=5, strata=("开发", "运维", "数据"), prefix="文档内容足够长用于过滤阈值"):
    return [(s, f"{prefix}-{s}-{i} 数据库与接口开发职责描述") for s in strata for i in range(n_per)]


# ---------------- 分层抽样器 ----------------

def test_sampler_filter_and_dedup():
    """追加条目：md5 文本去重 + <20 字过滤。"""
    s = StratifiedSampler(items=_items(n_per=3), seed=1)
    assert len(s.docs) == 9
    s.add_items([("开发", "文档内容足够长用于过滤阈值-开发-0 数据库与接口开发职责描述")])  # 重复文本
    assert len(s.docs) == 9                                   # md5 去重
    s.add_items([("开发", "太短")])                            # <20 字过滤
    assert len(s.docs) == 9


def test_sampler_strategies_and_cap():
    """三种配额策略与防御裁剪：min_coverage 保底层、_cap_targets 从最小层裁起、uniform 极端预算。"""
    s = StratifiedSampler(items=_items(n_per=6, strata=("A", "B", "C")), seed=1)
    # min_coverage：预算充足时每层至少 min_per
    got = s.sample(9, strategy="min_coverage", min_per=3)
    assert len(got) == 9
    # _cap_targets：总量超预算时从最小层裁剪
    capped = StratifiedSampler._cap_targets({"A": 6, "B": 6, "C": 6}, 10)
    assert capped == {"B": 4, "C": 6}                          # 从配额最小的层（同值按名序 A 先）裁起
    # uniform：n < 层数的极端情况（防御裁剪保证 ≤ n）
    few = StratifiedSampler(items=_items(n_per=2, strata=("A", "B", "C", "D")), seed=1)
    assert len(few.sample(2, strategy="uniform")) == 2


def test_sampler_next_batch_consumption():
    """热更新消费语义：next_batch 标记消费不重复，remaining 递减，断点哈希可持久化。"""
    s = StratifiedSampler(items=_items(n_per=4, strata=("A", "B")), seed=7)
    assert s.remaining() == 8
    b1 = set(s.next_batch(4))
    assert len(b1) == 4 and s.remaining() == 4
    b2 = set(s.next_batch(4))
    assert not (b1 & b2)                                      # 批间不重复
    assert s.remaining() == 0
    h1 = s.consumed_hashes()
    assert len(h1) == 8 and len(set(h1)) == 8
    # sample（冷启动）不标记消费
    s2 = StratifiedSampler(items=_items(n_per=2, strata=("A",)), seed=3)
    s2.sample(2)
    assert s2.remaining() == 2


def test_sampler_reproducible():
    """同种子 + 同调用序可复现（构造即重置随机种子，热更新断点续跑的前提）。"""
    a = StratifiedSampler(items=_items(), seed=42).sample(6, strategy="proportional")
    b = StratifiedSampler(items=_items(), seed=42).sample(6, strategy="proportional")
    assert a == b


# ---------------- 体系存储 ----------------

def test_taxonomy_store_task_mode(tmp_path):
    """task 模式存储：T-NN 续号、标签文本、modify 改写、merge 并条目移除被并方。"""
    store = TaxonomyStore(path=str(tmp_path / "tasks.json"), mode="task")
    t1 = store.add_task("数据管道开发", "Data Pipeline", "离线/实时管道构建")
    t2 = store.add_task("可观测性建设")
    assert (t1["code"], t2["code"]) == ("T-01", "T-02")        # 编码续号
    assert store.to_labels() == "T-01:数据管道开发\nT-02:可观测性建设"
    assert store.modify_task("T-02", description="指标与告警体系")
    assert store.tasks()[1]["description"] == "指标与告警体系"
    assert not store.modify_task("T-99")                       # 未知 code 拒改
    assert store.merge_tasks("T-01", "T-02")
    assert [t["code"] for t in store.tasks()] == ["T-01"]
    assert "可观测性建设" not in json.dumps(store.data, ensure_ascii=False)  # b 已移除


def test_taxonomy_store_skill_mode_and_roundtrip(tmp_path):
    """skill 模式：S-NN 编码 + definition 字段；save/load 回环按内容推断 mode；total 回填。"""
    store = TaxonomyStore(path=str(tmp_path / "skills.json"), mode="skill")
    s1 = store.add_task("大模型应用开发", skill_type="hard")
    assert s1["code"] == "S-01" and "definition" in s1
    store.save()
    # 回环：load 后按内容推断 mode（detail 无 tasks → skill）
    reloaded = TaxonomyStore(path=str(tmp_path / "skills.json"), mode="task").load()
    assert reloaded.mode == "skill" and reloaded.tasks()[0]["code"] == "S-01"
    assert reloaded.data["total"] == 1


# ---------------- 监督 Agent（注入 mock LLM） ----------------

def _fake_llm_decision(monkeypatch, decisions):
    def fake_call(prompt, parse_json=True):
        return {"decisions": decisions}
    monkeypatch.setattr(supervisor, "call_llm", fake_call)


def test_supervise_type_normalization(monkeypatch, tmp_path):
    """LLM 返回 index 为字符串 / approved 为 'true' 字符串时不得误拒（历史缺陷防线）。"""
    store = TaxonomyStore(path=str(tmp_path / "t.json"), mode="task")
    store.add_task("已有任务")
    proposal = {"updates": [{"action": "add", "task": {"name_zh": "新任务甲"}},
                            {"action": "add", "task": {"name_zh": "新任务乙"}},
                            {"action": "merge", "merge_codes": ["T-01", "T-02"]}]}
    _fake_llm_decision(monkeypatch, [
        {"index": "0", "approved": "true"},                    # 字符串 index + 字符串 bool
        {"index": 1, "approved": False, "reason": "与现有体系重复"},
        {"index": 99, "approved": True},                       # 越界 → 跳过
        "not-a-dict",                                          # 非法结构 → 跳过
    ])
    approved, rejected = supervisor.supervise(proposal, store)
    assert approved == [proposal["updates"][0]]
    assert rejected == [{"index": 1, "reason": "与现有体系重复", "map_to": None}]


def test_supervise_empty_updates(monkeypatch, tmp_path):
    """空提案短路：不触 LLM 直接返回空。"""
    store = TaxonomyStore(path=str(tmp_path / "t.json"), mode="task")
    monkeypatch.setattr(supervisor, "call_llm",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应调用 LLM")))
    assert supervisor.supervise({"updates": []}, store) == ([], [])


# ---------------- 提案应用 ----------------

def test_apply_updates_full_actions(tmp_path):
    """提案应用四动作：add（同名防重跳过）/merge/modify/未知动作异常隔离，落盘回读一致。"""
    store = TaxonomyStore(path=str(tmp_path / "t.json"), mode="task")
    store.add_task("数据管道开发")
    log = apply_updates(store, [
        {"action": "add", "task": {"name_zh": "实时数仓建设", "definition": "流式入仓"}},
        {"action": "add", "task": {"name_zh": "数据管道开发"}},        # 同名防重
        {"action": "merge", "merge_codes": ["T-01", "T-02"]},
        {"action": "modify", "target_code": "T-01", "task": {"name_zh": "数据管道开发与治理"}},
        {"action": "modify", "target_code": "T-77", "task": {}},        # 未知 code 静默
        {"action": "unknown-action"},                                   # 未知动作不抛错
    ])
    assert any("add T-02:实时数仓建设" in x for x in log)
    assert any("skip add" in x for x in log)
    assert any("merge T-02 -> T-01" in x for x in log)
    assert any("modify T-01" in x for x in log)
    names = [t["name_zh"] for t in store.tasks()]
    assert names == ["数据管道开发与治理"]
    # 落盘回读一致（apply 内部 save）
    assert TaxonomyStore(path=str(tmp_path / "t.json"), mode="task").load().tasks() == store.tasks()
