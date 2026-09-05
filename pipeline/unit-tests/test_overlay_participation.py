# -*- coding: utf-8 -*-
"""叠层确证参与（原算法设计恢复，2026-08-30）：分类注入 + overlays 输出 + 处置生效窗。

- B 分类提示词注入叠层候选（与既有技能/任务并列），命中按语义（含同义表述）判定；
- overlays 回显按注入名称集过滤（未知名称丢弃）；
- 合并视图（merge_delta / participating_items）按 remapped_window 生效窗退役，
  历史窗口（生效窗前）逐字节保持。
"""
import sys
import os

import ut
from datetime import date

_HERE = os.path.dirname(os.path.abspath(__file__))
_GRAPH_DIR = ut.path("graph")
_EXT_DIR = ut.path("extractor")
_BUILDER_DIR = ut.path("builder")


def _ext_module(name):
    """导入 extractor 子包模块，config 换出/恢复（同 run_jd_extract.make_extractors）。

    extractor 与 builder 各有 config.py；sys.modules["config"] 须在导入后恢复，
    否则污染同进程其它测试（graph_config 读 builder 版 DELTA_OUTPUT）。
    """
    saved = sys.modules.pop("config", None)
    for d in (_EXT_DIR, _GRAPH_DIR, _BUILDER_DIR):
        if d in sys.path:
            sys.path.remove(d)
        sys.path.insert(0, d)
    sys.path.insert(0, _EXT_DIR)          # extractor 优先（import config 命中其 config.py）
    try:
        import importlib
        return importlib.import_module(name)
    finally:
        sys.path.remove(_EXT_DIR)
        if saved is not None:
            sys.modules["config"] = saved
        else:
            sys.modules.pop("config", None)


# ---------------- build_overlay_labels / overlay_label_names ----------------
def test_build_overlay_labels():
    """清单文本：'名称（类型）：定义' 行格式；overlay_label_names 反解名称集。"""
    extractor = _ext_module("extractor")
    items = [
        {"name_zh": "多技能模型融合调优", "array": "new_tasks",
         "definition": "将多个单一技能模型融合并调优的方法"},
        {"name_zh": "提示工程", "array": "new_skills", "definition": ""},
        {"name_zh": "", "array": "new_skills", "definition": "无名条目跳过"},
    ]
    text = extractor.build_overlay_labels(items)
    lines = text.splitlines()
    assert lines[0] == "- 多技能模型融合调优（任务）：将多个单一技能模型融合并调优的方法"
    assert lines[1] == "- 提示工程（技能）"
    assert len(lines) == 2

    lc = _ext_module("llm_client")
    assert lc.overlay_label_names(text) == {"多技能模型融合调优", "提示工程"}
    assert lc.overlay_label_names(None) == set()


# ---------------- classify_merged：overlays 解析与过滤（mock LLM） ----------------
def test_classify_merged_overlays(monkeypatch):
    """句级分类 overlays 输出：叠层命中句出 overlays 键，基图分类不受扰。"""
    lc = _ext_module("llm_client")

    class _Tax:
        code_to_name = {"T-SW-01": "程序设计与软件工程"}
        name_to_code = {"程序设计与软件工程": "T-SW-01"}

        def label_text(self):
            return "T-SW-01 程序设计与软件工程"

    def _fake_post(self, prompt):
        assert "叠层候选体系" in prompt and "- 提示工程（技能）" in prompt
        return [
            {"sentence": "具备良好的团队合作与沟通能力", "skills": [], "tasks": [],
             "overlays": ["提示工程", "未注入的名称"]},   # 未知名称应被过滤
            {"sentence": "负责Java服务端开发", "skills": [{"code": "T-SW-01"}],
             "tasks": [], "overlays": []},
        ]

    monkeypatch.setattr(lc.LLMClient, "_post", _fake_post)
    client = lc.LLMClient(api_key="test")
    overlay_text = "- 提示工程（技能）：设计提示词的方法"
    out = client.classify_merged(["具备良好的团队合作与沟通能力", "负责Java服务端开发"],
                                 _Tax(), _Tax(), overlay_labels=overlay_text)
    assert out["具备良好的团队合作与沟通能力"]["overlays"] == ["提示工程"]
    assert out["负责Java服务端开发"]["skills"][0]["code"] == "T-SW-01"


# ---------------- merge_delta：处置生效窗（历史不变 + 生效窗退役） ----------------
def _papers_store():
    return {
        "new_skills": [
            {"id": "PS-001", "name_zh": "团队协作与协调", "evidence": {
                "p1": {"date": "2022-09-29", "sentences": ["s"], "confidence": "medium"}},
                "strength": 0.8, "born_window": "2022-09", "status": "pending",
                "remapped_window": "2023-06", "remap_note": "与基图 F-2-03 同名"},
            {"id": "PS-002", "name_zh": "数据流漂移治理", "evidence": {
                "p2": {"date": "2022-11-20", "sentences": ["s"], "confidence": "medium"}},
                "strength": 0.7, "born_window": "2022-11", "status": "pending"},
        ]
    }


def test_merge_delta_remap_effective_window():
    """退役生效窗门：生效窗前条目保留、生效窗起移出（stats 计数）。"""
    saved = sys.modules.pop("config", None)
    if _BUILDER_DIR not in sys.path:
        sys.path.insert(0, _BUILDER_DIR)
    if _GRAPH_DIR not in sys.path:
        sys.path.insert(0, _GRAPH_DIR)
    try:
        from snapshot_builder import merge_delta
    finally:
        if saved is not None:
            sys.modules["config"] = saved
    papers = _papers_store()

    m_pre, st_pre = merge_delta(papers, {}, {}, date(2023, 5, 31))
    names_pre = [e["name_zh"] for e in m_pre["new_skills"]]
    assert names_pre == ["团队协作与协调", "数据流漂移治理"], "生效窗前不退役"
    assert all("remapped_window" not in e and "remap_note" not in e
               for e in m_pre["new_skills"]), "处置元数据不进快照产物"
    assert st_pre.get("n_remapped_skipped") == 0

    m_post, st_post = merge_delta(papers, {}, {}, date(2023, 6, 30))
    names_post = [e["name_zh"] for e in m_post["new_skills"]]
    assert names_post == ["数据流漂移治理"], "生效窗起退役"
    assert st_post.get("n_remapped_skipped") == 1


def test_merge_delta_rename_retroactive():
    """重命名回溯传播（2026-08-30 裁定）：store 单条新名条目自出生窗起在所有窗口渲染新名；
    改名审计链（rename_history）只留 ΔG store，不进快照产物。"""
    from snapshot_builder import merge_delta
    papers = {
        "new_tasks": [
            {"id": "PT-001", "name_zh": "机器人技能示教", "evidence": {
                "p1": {"date": "2022-09-29", "sentences": ["s"], "confidence": "high"}},
                "strength": 1.0, "born_window": "2022-09", "status": "pending",
                "definition": "人类通过演示/示教使机器人习得技能",
                "rename_history": ["机器人技能学习", "机器人技能获取与复用"]},
        ]
    }
    for we, label in [(date(2022, 9, 30), "出生窗"), (date(2023, 5, 31), "历史窗"),
                      (date(2023, 6, 30), "当前窗")]:
        m, _ = merge_delta(papers, {}, {}, we)
        names = [e["name_zh"] for e in m["new_tasks"]]
        assert names == ["机器人技能示教"], f"{label}应渲染新名"
        assert all("rename_history" not in e for e in m["new_tasks"]), "审计链不进产物"


def test_participating_items_remap_skip(monkeypatch):
    """参与清单同步跳过已退役条目（提示词注入面与快照面一致）。"""
    saved = sys.modules.pop("config", None)
    if _BUILDER_DIR not in sys.path:
        sys.path.insert(0, _BUILDER_DIR)
    if _GRAPH_DIR not in sys.path:
        sys.path.insert(0, _GRAPH_DIR)
    try:
        import participation
        import snapshot_builder
    finally:
        if saved is not None:
            sys.modules["config"] = saved
    papers = _papers_store()
    monkeypatch.setattr(participation, "merged_view",
                        lambda now=None, delta_files=None: (
                            snapshot_builder.merge_delta(papers, {}, {}, now)[0], []))
    monkeypatch.setattr(participation.config, "OVERLAY_PARTICIPATE_MIN", 0.0)
    items = participation.participating_items(now=date(2023, 5, 31))
    assert {it["name_zh"] for it in items} == {"团队协作与协调", "数据流漂移治理"}
    items6 = participation.participating_items(now=date(2023, 6, 1))
    assert {it["name_zh"] for it in items6} == {"数据流漂移治理"}

# ---------------- 叠层岗位确证：标题级通道（2026-08-30） ----------------
def test_job_overlay_prompt_and_split():
    """岗位走 JD 标题级批量分类（PROMPT_JOB_OVERLAY）；句级注入只承载任务/技能。"""
    saved = sys.modules.pop("config", None)
    for d in (_EXT_DIR, _GRAPH_DIR):
        if d in sys.path:
            sys.path.remove(d)
        sys.path.insert(0, d)
    sys.path.insert(0, _EXT_DIR)
    try:
        import prompts as ext_prompts
        import extractor as ext_mod
    finally:
        sys.path.remove(_EXT_DIR)
        if saved is not None:
            sys.modules["config"] = saved
        else:
            sys.modules.pop("config", None)
    t = ext_prompts.PROMPT_JOB_OVERLAY
    assert "{overlay_jobs}" in t and "{titles}" in t and "宁缺毋滥" in t
    # build_overlay_labels 仍可渲染岗位（供标题级通道复用文本块）
    items = [{"name_zh": "航天测试专家", "array": "new_jobs", "definition": "航天系统测试"}]
    assert "航天测试专家（岗位）：航天系统测试" in ext_mod.build_overlay_labels(items)

# ---------------- 转正生效窗（graduated_window 门，2026-08-30 回溯转正） ----------------
def test_merge_delta_graduated_window_gate():
    """转正条目：生效窗起退出叠层（基图侧自该窗含新实体）；生效窗前仍渲染为叠层。"""
    from snapshot_builder import merge_delta
    papers = {
        "new_tasks": [
            {"id": "PT-002", "name_zh": "多模态数据融合建模", "evidence": {
                "p1": {"date": "2022-07-25", "sentences": ["s"], "confidence": "high"}},
                "strength": 0.99, "born_window": "2022-07", "status": "graduated",
                "promoted_to": "T-28", "graduated_window": "2023-06"},
        ]
    }
    m_pre, st_pre = merge_delta(papers, {}, {}, date(2023, 5, 31))
    assert [e["name_zh"] for e in m_pre["new_tasks"]] == ["多模态数据融合建模"], "生效窗前仍叠层"
    assert st_pre.get("n_graduated_skipped") == 0
    m_post, st_post = merge_delta(papers, {}, {}, date(2023, 6, 30))
    assert m_post["new_tasks"] == [], "生效窗起退出叠层（已入基图）"
    assert st_post.get("n_graduated_skipped") == 1

