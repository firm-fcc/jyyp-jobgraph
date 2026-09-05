# -*- coding: utf-8 -*-
"""jd_delta_v2 单测：确定性扫描（token/n-gram 差集、df 带宽、子串归约、证据补全）、
确证预筛、HotUpdater 裁决接线（mock LLM 零真实调用）、裁决缓存幂等。"""
import json
import os
import sys
import types

import pytest

import ut

ut.setup("graph", "builder")
ut.isolate()
import jd_delta_v2 as v2  # noqa: E402


def _doc(i, sents, opentime="2022-06-01 00:00:00"):
    return {"jobid": f"J{i}", "opentime": opentime, "job_code": "DEV-33",
            "funtype": "开发", "jd_key": f"k{i}", "sents": sents}


# ---------------- 确定性扫描 ----------------
def test_doc_tokens():
    """英文 token 抽取：驼峰/版本号/缩写归一，纯数字与域名噪音剔除。"""
    toks = v2._doc_tokens(["熟悉 K8s 与 docker-compose 部署，掌握 5G 基站协议", "待遇面议123"])
    assert "k8s" in toks and "docker" in toks and "5g" in toks
    assert "123" not in toks                      # 纯数字剔除
    assert "com" not in toks or "compose" in toks  # 断词组件至少保留完整形态


def test_chinese_apriori_mining():
    """df≥5 的 5-gram 新词被逐级上卷捕获；df<5 的搭配不进结果。"""
    docs = []
    for i in range(6):                             # 提示词工程 ×6 文档
        docs.append(_doc(i, ["要求熟悉提示词工程与模型调优，本科及以上学历"]))
    for i in range(6, 9):                          # 低频搭配 ×3 → 不入池
        docs.append(_doc(i, ["负责奇偶校验收发器相关工作"]))
    levels = v2.count_chinese_df(docs, min_docs=5)
    grams5 = levels.get(5, {})
    assert grams5.get("提示词工程") == 6
    all_grams = {g for lv in levels.values() for g in lv}
    assert "校验收发" not in all_grams              # df=3 低于门槛


def test_trim_context():
    """语境词修剪：'熟悉/掌握/相关经验' 等外壳词剥除，专名保留。"""
    assert v2._trim_context("熟悉提示词工程") == "提示词工程"
    assert v2._trim_context("大模型调优经验") == "大模型调优"
    assert v2._trim_context("相关经验") == ""          # 纯语境词 → 弃
    assert v2._trim_context("鸿蒙") == "鸿蒙"


def test_pool_band_and_vocab_diff():
    """带宽下限砍低频、上限砍通用搭配；已知词表命中被差集吸收。"""
    docs = [_doc(i, ["熟悉提示词工程，掌握Java开发，本科相关专业"]) for i in range(4)]
    docs += [_doc(10 + i, ["学提示词工程并做调优，掌握Java开发，本科相关专业"]) for i in range(3)]
    docs += [_doc(20 + i, ["懂提示词工程者优先，掌握Java开发，本科相关专业"]) for i in range(3)]
    docs += [_doc(30 + i, ["掌握Java开发，本科相关专业"]) for i in range(9)]
    known = {"java", "开发", "掌握", "本科", "专业"}
    # 提示词工程 df=10/19≈53%（带内）；本科相关专业 df=19=100% 超上限被砍；java 命中词表
    pool, stats = v2.pool_candidates(docs, known, {}, min_docs=5, max_df_ratio=0.6)
    names = {c["name"] for c in pool}
    assert any("提示词工程" in n for n in names)
    assert "相关专业" not in names                     # 全文档搭配被带宽上限砍
    assert not any(c["name"].lower() == "java" for c in pool)
    assert stats["n_docs"] == 19
    # 低频不进：单文档出现的生词 df=1 < 5
    docs2 = docs + [_doc(99, ["了解某某专有名词XYZ技术"])]
    pool2, _ = v2.pool_candidates(docs2, known, {}, min_docs=5, max_df_ratio=0.6)
    assert not any("某某专有" in c["name"] for c in pool2)


def test_substring_reduction():
    """碎片（提示词）并入更长真名（提示词工程），独立高频短词保留。"""
    docs = ([_doc(i, ["熟悉提示词工程"]) for i in range(3)]
            + [_doc(10 + i, ["学提示词工程"]) for i in range(1)]
            + [_doc(20 + i, ["懂提示词工程"]) for i in range(1)])   # 提示词 df=6（含嵌套）
    known, cache = set(), {}
    pool, _ = v2.pool_candidates(docs, known, cache, min_docs=5, max_df_ratio=1.0)
    names = {c["name"] for c in pool}
    assert any("提示词工程" in n for n in names)     # 真名（或其轻微越界扩展）在池
    assert "提示词" not in names                      # 6 ≤ 1.2×5 → 碎片被并
    # 反例：短词独立高频且上下文多样（无刚性长串能覆盖其出现）→ 保留
    varied = ["写好提示词与口令", "调整提示词参数", "维护提示词模板", "优化提示词表达",
              "设计提示词流程", "重构提示词结构", "评审提示词规范", "迭代提示词风格"]
    docs3 = ([_doc(i, ["熟悉提示词工程"]) for i in range(3)]
             + [_doc(10 + i, [varied[i % len(varied)]]) for i in range(20)])
    pool3, _ = v2.pool_candidates(docs3, known, cache, min_docs=5, max_df_ratio=1.0)
    assert "提示词" in {c["name"] for c in pool3}    # df=23，无长串覆盖 ≥83%


def test_trim_resurrection_ceiling():
    """中频长变体修剪出的超限短词不得复活进池——带宽按修剪后终名的真实 df。"""
    docs = [_doc(i, ["5年以上经验。"]) for i in range(6)]             # "年以上经验" df=6（带内）
    docs += [_doc(20 + i, ["3年以上工作经历。"]) for i in range(30)]   # 三种续延分散出现，
    docs += [_doc(50 + i, ["3年以上开发经验。"]) for i in range(30)]   # "年以上" 真实 df=100
    docs += [_doc(80 + i, ["3年以上运维经验。"]) for i in range(34)]   # 超上限且无长串覆盖
    docs += [_doc(120 + i, ["技能:向量数据库。"]) for i in range(7)]   # 带内真词对照组
    pool, stats = v2.pool_candidates(docs, set(), {}, min_docs=5, max_df_ratio=0.10)
    names = {c["name"] for c in pool}
    assert "年以上" not in names                      # 终名 df=100 > ceil=10 → 封死
    assert stats["ceil_filtered"] >= 1
    assert "向量数据库" in names                      # 带内真词不受影响


def test_task_skill_novelty_gate(monkeypatch, tmp):
    """task/skill 判定过新颖性守门：被涵盖→改 alias；确无涵盖→新实体（nearest/why_not 审计）；
    复核未决→不缓存不应用留待下窗；幻觉 alias code 压非技术缓存。"""
    monkeypatch.setattr(v2, "ADJ_CACHE_PATH", os.path.join(tmp, "adj_cache_novelty.jsonl"))
    base_labels = {"skills": [{"code": "S-AI-01", "name_zh": "机器学习", "name_en": ""}],
                   "tasks": [{"code": "T-01", "name_zh": "应用软件开发", "name_en": ""},
                             {"code": "T-08", "name_zh": "系统运维", "name_en": ""}], "jobs": []}
    v2._BASE_LABELS_CACHE.clear()
    v2._BASE_LABELS_CACHE.update(base_labels)
    verdicts = [
        {"key": "前端开发", "is_tech": True, "canonical": "前端开发", "name_en": "",
         "kind": "task", "alias_to": None, "rationale": "初判职责级表述"},
        {"key": "联邦学习平台运维", "is_tech": True, "canonical": "联邦学习平台运维", "name_en": "",
         "kind": "task", "alias_to": None, "rationale": "体系外新职责域"},
        {"key": "未决任务", "is_tech": True, "canonical": "未决任务", "name_en": "",
         "kind": "task", "alias_to": None, "rationale": "复核不会返回它"},
        {"key": "halias", "is_tech": True, "canonical": "机器学习", "name_en": "",
         "kind": "alias", "alias_to": {"taxonomy": "skills", "code": "S-XX-99"},   # 幻觉 code
         "rationale": "映射失败"},
        {"key": "skp", "is_tech": True, "canonical": "Kafka", "name_en": "",
         "kind": "skillpoint", "alias_to": None, "rationale": "流式平台"},
    ]
    recheck = [
        {"key": "前端开发", "covered": True, "taxonomy": "tasks", "code": "T-01"},
        {"key": "联邦学习平台运维", "covered": False, "nearest": "T-08",
         "why_not": "基线无隐私计算集群运维职责"},
    ]   # "未决任务" 复核不返回 → 未决
    llm = _MockLLM(verdicts, recheck=recheck)
    keys = [r["key"] for r in verdicts]
    docs = [_doc(i, [f"要求{k}能力"]) for i, k in enumerate(keys)]
    pool = [{"key": k, "name": k, "channel": "zh" if k != "skp" else "en",
             "df": 5, "evidence": [(i, f"要求{k}")]} for i, k in enumerate(keys)]
    from delta_store import DeltaStore
    delta = DeltaStore(os.path.join(tmp, "jd_delta_novelty.json"), source_kind="jd",
                       now=v2.window_end_date("2022-06"))
    adj_cache = {}
    v2.adjudicate("2022-06", docs, pool, llm, adj_cache, base_labels, delta, dry_run=False)
    # 被涵盖的 task → 改 alias（只入缓存排水：市场存在已由基图统计，不写叠层/增强）
    assert adj_cache["前端开发"]["kind"] == "alias"
    assert adj_cache["前端开发"]["alias_to"] == {"taxonomy": "tasks", "code": "T-01"}
    assert all(not s.get("code") == "T-01" for s in delta.data["strengthenings"])
    # 确无涵盖的 task → 新实体落地，nearest/why_not 入缓存行留审计
    assert [e["name_zh"] for e in delta.data["new_tasks"]] == ["联邦学习平台运维"]
    assert adj_cache["联邦学习平台运维"]["nearest"] == "T-08"
    assert adj_cache["联邦学习平台运维"]["why_not"]
    nt = delta.data["new_tasks"][0]
    assert nt["evidence"] and all(ev.get("grade") == "scan" for ev in nt["evidence"].values())
    assert nt.get("born_window") == "2022-06"        # 入场窗戳：确证滞后语义的判定基准
    # 复核未决 → 不缓存、不应用（留待下窗，不给体系加没把握的新条目）
    assert "未决任务" not in adj_cache
    assert all(e["name_zh"] != "未决任务" for e in delta.data["new_tasks"])
    # 幻觉 alias code → 压非技术缓存（coerced_from 留审计）；skillpoint 只排水不写 ΔG
    assert adj_cache["halias"]["is_tech"] is False and adj_cache["halias"]["coerced_from"] == "alias"
    assert adj_cache["skp"]["kind"] == "skillpoint"
    assert not delta.data["skillpoints"] and not delta.data["new_skills"]
    # 提示词契约：alias 优先 + task/skill 须证无涵盖 + 复核宁严勿宽
    assert "无法涵盖" in v2._ADJ_PROMPT and "nearest" in v2._ADJ_PROMPT
    assert "宁严勿宽" in v2._NOVELTY_PROMPT


def test_collect_evidence():
    """证据收集：候选词带 (doc_id, 原句) 证据且句子以'熟悉/掌握'类要求语义开头。"""
    docs = [_doc(0, ["熟悉提示词工程与RAG"]), _doc(1, ["掌握RAG检索增强"])]
    sel = [{"key": "提示词工程", "name": "提示词工程", "channel": "zh", "df": 1},
           {"key": "rag", "name": "RAG", "channel": "en", "df": 2}]
    v2.collect_evidence(docs, sel, cap=2)
    assert sel[0]["evidence"][0][1].startswith("熟悉提示词工程")
    assert sel[1]["evidence"][0][1].startswith("熟悉") or sel[1]["evidence"][0][1].startswith("掌握")


def test_window_end_date():
    """窗末日期：大小月与闰年。"""
    assert str(v2.window_end_date("2022-06")) == "2022-06-30"
    assert str(v2.window_end_date("2024-02")) == "2024-02-29"


# ---------------- 确证通道退役（2026-08-30 迁移至 Stage B 叠层分类参与） ----------------
def test_confirm_channel_removed():
    """子串预筛确证通道已从 v2 退役；发现通道不再产出 confirm 统计。"""
    assert not hasattr(v2, "confirm_channel")
    assert not hasattr(v2, "_CONFIRM_PROMPT")


def test_load_overlay_items_born_window_gate(monkeypatch):
    """B 侧叠层参与清单：同窗出生的实体不接受确证（出生=入场窗，至少滞后一窗）。"""
    import run_jd_extract as rje
    items = [{"id": "PS-001", "name_zh": "提示词工程", "array": "new_skills",
              "strength": 0.5, "definition": "x", "name_en": "", "sources": ["papers"],
              "born_window": "2022-05"},
             {"id": "PS-002", "name_zh": "同窗出生", "array": "new_skills",
              "strength": 0.9, "definition": "x", "name_en": "", "sources": ["papers"],
              "born_window": "2022-06"}]        # 与窗口同月出生 → 不参与
    captured = {}

    def _fake_participating(**kw):
        captured.update(kw)
        return items

    import participation
    monkeypatch.setattr(participation, "participating_items", _fake_participating)
    got = rje._load_overlay_items("2022-06")
    assert [it["name_zh"] for it in got] == ["提示词工程"], "同窗出生者被排除"
    from datetime import date as _date
    assert captured.get("now") == _date(2022, 6, 1), "参与强度按窗初口径"
    assert not captured.get("exclude_src"), "确证目标不分出生源"


# ---------------- 裁决接线（HotUpdater 注入 + mock LLM） ----------------
class _MockLLM:
    """裁决 mock；recheck 提供"新颖性复核"调用的应答（按提示词标记分发）。"""

    def __init__(self, verdicts, recheck=None):
        self.verdicts = verdicts
        self.recheck = recheck
        self.calls = 0

    def _post(self, prompt):
        self.calls += 1
        if "守门员" in prompt and self.recheck is not None:   # 仅 _NOVELTY_PROMPT 含此标记
            return self.recheck
        return self.verdicts


def test_adjudication_hotupdate_wiring(monkeypatch, tmp):
    """残差 → HotUpdater 裁决 → 只入缓存（skillpoint/alias/非技术均排水，不写 ΔG）。"""
    monkeypatch.setattr(v2, "ADJ_CACHE_PATH", os.path.join(tmp, "adj_cache_wiring.jsonl"))
    cache_path = os.path.join(tmp, "adj_cache_wiring.jsonl")

    base_labels = {
        "skills": [{"code": "S-AI-01", "name_zh": "机器学习", "name_en": "ML"}],
        "tasks": [{"code": "T-DA-01", "name_zh": "数据建模", "name_en": ""}],
        "jobs": [],
    }
    v2._BASE_LABELS_CACHE.clear()
    v2._BASE_LABELS_CACHE.update(base_labels)

    verdicts = [
        {"key": "提示词工程", "is_tech": True, "canonical": "提示词工程",
         "name_en": "Prompt Engineering", "kind": "skillpoint", "alias_to": None,
         "rationale": "新兴技术载体"},
        {"key": "sklearn", "is_tech": True, "canonical": "机器学习", "name_en": "",
         "kind": "alias", "alias_to": {"taxonomy": "skills", "code": "S-AI-01"},
         "rationale": "体系别名"},
        {"key": "相关专业", "is_tech": False, "canonical": "", "name_en": "",
         "kind": "", "alias_to": None, "rationale": "通用语言搭配"},
    ]
    llm = _MockLLM(verdicts)
    docs = [_doc(i, ["熟悉提示词工程、sklearn 与相关专业要求"]) for i in range(3)]
    pool = [
        {"key": "提示词工程", "name": "提示词工程", "channel": "zh", "df": 3,
         "evidence": [(0, "熟悉提示词工程")]},
        {"key": "sklearn", "name": "sklearn", "channel": "en", "df": 3,
         "evidence": [(1, "熟悉sklearn")]},
        {"key": "相关专业", "name": "相关专业", "channel": "zh", "df": 3,
         "evidence": [(2, "相关专业要求")]},
    ]
    out = os.path.join(tmp, "jd_delta_wiring.json")
    from delta_store import DeltaStore
    delta = DeltaStore(out, source_kind="jd", now=v2.window_end_date("2022-06"))
    adj_cache = {}
    sel, stats = v2.adjudicate("2022-06", docs, pool, llm, adj_cache,
                               base_labels, delta, dry_run=False)
    assert stats["selected"] == 3 and llm.calls >= 1
    # skillpoint/alias 只入裁决缓存排水（发现权威在 B 阶段三层归一；被涵盖短语的市场
    # 存在已由基图统计）——不写 ΔG 任何数组
    assert adj_cache["提示词工程"]["kind"] == "skillpoint"
    assert adj_cache["sklearn"]["kind"] == "alias"
    assert all(not delta.data[k] for k in
               ("skillpoints", "strengthenings", "new_tasks", "new_skills"))
    # 非技术入缓存（跨窗背景）
    assert adj_cache["相关专业"]["is_tech"] is False

    # 幂等：同窗重跑（缓存全命中）零 LLM 调用
    calls_before = llm.calls
    sel2, _ = v2.adjudicate("2022-06", docs, pool, llm, adj_cache,
                            base_labels, delta, dry_run=False)
    assert llm.calls == calls_before
    # 缓存落盘可加载
    assert v2.load_adjudication_cache().get("相关专业", {}).get("is_tech") is False


# ---------------- 参数指纹 ----------------
def test_assembly_fingerprint():
    """组装参数指纹：同参同指纹、16 位短哈希。"""
    import graph_config as gc
    fp1, fp2 = gc.assembly_params_fingerprint(), gc.assembly_params_fingerprint()
    assert fp1 == fp2 and len(fp1) == 16
