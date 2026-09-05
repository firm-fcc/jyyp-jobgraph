# -*- coding: utf-8 -*-
"""JD 第三源自测：delta_store jd 权重/confirm_named / snapshot 三分支判源 /
三源 merge（跨源同名合并、graduated 跳过、participates）/ participation 可见性门控。

运行：cd codes/graph && python fixtures/test_jd_source.py
（全部手算断言，零 LLM；输出到临时目录，不触碰正式产物。）
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

import ut

ut.setup("graph", "builder")
ut.isolate()

from delta_store import DeltaStore, _noisy_or       # noqa: E402
from snapshot_builder import _contrib, merge_delta  # noqa: E402
from participation import participating_items, overlay_labels_text  # noqa: E402
import graph_config as config                      # noqa: E402

TODAY = date(2026, 8, 17)


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ✓ {msg}")


def _approx(a, b, tol=1e-4):
    return abs(a - b) <= tol


class _Rec:
    def __init__(self, doc_id, pub_date):
        self.doc_id, self.arxiv_id, self.pub_date, self.tier = doc_id, "", pub_date, ""


def test_delta_store_jd(tmp):
    """JD 确证写入：src=jd 标记、doc_id 幂等、跨文档 noisy-or 增强、source_kinds 记账。"""
    print("== delta_store jd 源：权重 / src 标记 / confirm_named ==")
    path = os.path.join(tmp, "jd_delta.json")
    delta = DeltaStore(path, source_desc="t", source_kind="jd",
                       source_weight=1.0, half_life_days=365, now=TODAY)
    _assert(delta.source_kind == "jd", "source_kind=jd 注册生效（不回退 papers）")

    ev = {"date": "2026-08-07", "confidence": "high", "src": "jd"}
    expect = 1.0 * 1.0 * (0.5 ** (10 / 365.0))     # 10 天前，半衰期 365
    _assert(_approx(delta._contrib(ev), expect), f"contrib = 1.0×1.0×0.5^(10/365) = {expect:.4f}")

    rec = _Rec("job-1001", "2026-08-17")
    entry, created = delta.confirm_named("new_tasks", "智能体安全评估", rec,
                                         ["需要智能体安全评估经验"], "high", grade="require")
    _assert(created and entry["id"] == "PT-001", "confirm_named 新建（jd 文件内 PT-001）")
    _assert(entry["evidence"]["job-1001"]["src"] == "jd", "证据带 src=jd 标记")
    _assert("jd_confirm" in entry["source_kinds"], "source_kinds 记 jd_confirm")
    entry2, created2 = delta.confirm_named("new_tasks", "智能体安全评估", rec, ["需要智能体安全评估经验"], "high", grade="require")
    _assert(not created2 and len(entry2["evidence"]) == 1, "同 doc_id 幂等（不重复证据）")
    delta.confirm_named("new_tasks", "智能体安全评估", _Rec("job-1002", "2026-08-16"),
                        ["负责智能体安全评估"], "high", grade="require")
    _assert(len(delta.data["new_tasks"][0]["evidence"]) == 2, "第二文档确证 → 证据累积（增强）")
    _assert(_approx(delta.data["new_tasks"][0]["strength"],
                    _noisy_or([1.0 * 1.0 * 1.0, 1.0 * 1.0 * (0.5 ** (1 / 365.0))])),
            "强度 = noisy-or(两日 JD 确证)")


def test_snapshot_contrib_branches():
    """强度三分支判源：src=jd→JD 权重/365、有 tier→论文/730、无标记→新闻/180。"""
    print("== snapshot _contrib 三分支判源 ==")
    ev_jd = {"date": "2026-08-07", "confidence": "high", "src": "jd"}
    ev_pp = {"date": "2026-08-07", "confidence": "high", "tier": "S"}
    ev_nw = {"date": "2026-08-07", "confidence": "high"}  # 旧数据无 src/tier → 新闻兜底
    _assert(_approx(_contrib(ev_jd, TODAY), 1.0 * 1.0 * 0.5 ** (10 / 365.0)), "src=jd → JD 权重/365")
    _assert(_approx(_contrib(ev_pp, TODAY), 1.0 * 1.0 * 0.5 ** (10 / 730.0)), "tier → 论文权重/730")
    _assert(_approx(_contrib(ev_nw, TODAY), 0.4 * 1.0 * 0.5 ** (10 / 180.0)), "无标记 → 新闻兜底/180")


def test_merge_three_sources():
    """三源 merge：norm 同名合并、证据并集、分别衰减后 noisy-or、参与标记、graduated 跳过。"""
    print("== 三源 merge：跨源同名 / graduated 跳过 / participates ==")
    papers = {"new_tasks": [
        {"id": "PT-001", "name_zh": "智能体安全评估", "name_en": "", "description": "定义。",
         "evidence": {"2401.00001": {"date": "2026-08-10", "sentences": ["论文句"], "confidence": "high", "tier": "S"}}}],
        "new_skills": [], "new_jobs": [], "skillpoints": [], "strengthenings": []}
    jd = {"new_tasks": [
        {"id": "PT-001", "name_zh": "智能体安全评估", "name_en": "", "description": "",
         "evidence": {"job-1": {"date": "2026-08-16", "sentences": ["JD句1"], "confidence": "high", "src": "jd"},
                      "job-2": {"date": "2026-08-15", "sentences": ["JD句2"], "confidence": "high", "src": "jd"}}}],
        "new_skills": [{"id": "PS-001", "name_zh": "已转正技能", "status": "graduated", "promoted_to": "T-DG-01",
                        "evidence": {"job-9": {"date": "2026-08-16", "sentences": ["x"], "confidence": "high", "src": "jd"}}}],
        "new_jobs": [], "skillpoints": [], "strengthenings": []}
    merged, stats = merge_delta(papers, {}, jd, TODAY)
    _assert(len(merged["new_tasks"]) == 1, "跨源同名合并为一条")
    t = merged["new_tasks"][0]
    _assert(set(t["sources"]) == {"papers", "jd"}, f"sources 并集 {t['sources']}")
    _assert(len(t["evidence"]) == 3, "证据并集（论文 1 + JD 2）")
    p_contrib = 1.0 * 1.0 * 0.5 ** (7 / 730.0)
    j1 = 1.0 * 1.0 * 0.5 ** (1 / 365.0)
    j2 = 1.0 * 1.0 * 0.5 ** (2 / 365.0)
    _assert(_approx(t["strength"], _noisy_or([p_contrib, j1, j2])), "强度按判源分别计算后 noisy-or")
    _assert(t["participates"] is True, "strength ≥ 参与门槛 → participates=True")
    _assert(len(merged["new_skills"]) == 0 and stats["n_graduated_skipped"] == 1,
            "graduated 条目跳过（stats 计数）")
    # 休眠（遗忘）验证：老证据衰减后跌破参与门槛 → participates=False 但仍在视图（未删除）
    old = {"new_tasks": [{"id": "PT-009", "name_zh": "古老信号", "name_en": "", "description": "",
                          "evidence": {"old-1": {"date": "2023-06-01", "sentences": ["x"],
                                                 "confidence": "high", "src": "jd"}}}],
           "new_skills": [], "new_jobs": [], "skillpoints": [], "strengthenings": []}
    merged2, _ = merge_delta({}, old, {}, TODAY)
    item = merged2["new_tasks"][0]
    _assert(item["participates"] is False and item["strength"] > 0,
            f"半衰期衰减后休眠（strength={item['strength']} < {config.OVERLAY_PARTICIPATE_MIN}，未删除）")


def test_participation(tmp):
    """参与门控 exclude_src：指定源的独有条目剔除、他源保留；清单文本可注入提示词。"""
    print("== participation 可见性门控 ==")
    strong = {"id": "PT-001", "name_zh": "智能体安全评估", "description": "定义。",
              "evidence": {"p1": {"date": "2026-08-16", "sentences": ["x"], "confidence": "high", "tier": "S"}}}
    weak_news_only = {"id": "PT-002", "name_zh": "弱信号", "description": "",
                      "evidence": {"n1": {"date": "2024-01-01", "sentences": ["x"], "confidence": "low"}}}
    both = {"id": "PT-003", "name_zh": "双源信号", "description": "",
            "evidence": {"n2": {"date": "2026-08-16", "sentences": ["x"], "confidence": "high"}}}
    files = {"papers": os.path.join(tmp, "p.json"), "news": os.path.join(tmp, "n.json"),
             "jd": os.path.join(tmp, "j.json")}
    json.dump({"new_tasks": [strong], "new_skills": [], "new_jobs": [], "skillpoints": [], "strengthenings": []},
              open(files["papers"], "w", encoding="utf-8"))
    json.dump({"new_tasks": [weak_news_only, both], "new_skills": [], "new_jobs": [], "skillpoints": [], "strengthenings": []},
              open(files["news"], "w", encoding="utf-8"))
    items = participating_items(delta_files=files, now=TODAY)
    names = [it["name_zh"] for it in items]
    _assert(names == ["智能体安全评估", "双源信号"], f"仅强度达标者参与（{names}）")
    excl = participating_items(delta_files=files, now=TODAY, exclude_src="news")
    _assert([it["name_zh"] for it in excl] == ["智能体安全评估"],
            "exclude_src=news：仅存于 news 的条目被剔除（papers 源保留）")
    excl_p = participating_items(delta_files=files, now=TODAY, exclude_src="papers")
    _assert([it["name_zh"] for it in excl_p] == ["双源信号"],
            "exclude_src=papers：仅存于 papers 的条目被剔除")
    txt = overlay_labels_text(items)
    _assert("PT-001" in txt and "智能体安全评估" in txt, "overlay 清单文本可用于提示词注入")


def test_confirm_anchor(tmp):
    """确证锚定规范名：锚定后跨源合并与 jd_docs 统计正确；锚定变体名则合并失败（反例对照）。"""
    print("== 确证锚定规范名（跨源合并前提）==")
    papers = {"new_tasks": [
        {"id": "PT-001", "name_zh": "智能体安全评估", "name_en": "", "description": "定义。",
         "evidence": {"2401.00002": {"date": "2026-08-15", "sentences": ["论文句"], "confidence": "high", "tier": "S"}}}],
        "new_skills": [], "new_jobs": [], "skillpoints": [], "strengthenings": []}

    def _jd_file(name):
        path = os.path.join(tmp, f"jd_{name}.json")
        d = DeltaStore(path, source_desc="t", source_kind="jd",
                       source_weight=1.0, half_life_days=365, now=TODAY)
        d.confirm_named("new_tasks", name, _Rec("job-77", "2026-08-16"), ["JD 原文句"], "high",
                        grade="require")
        return {"new_tasks": d.data["new_tasks"], "new_skills": [], "new_jobs": [],
                "skillpoints": [], "strengthenings": []}

    from promotion import _jd_doc_count
    # 修复后行为：锚定规范名 → 快照合并、jd_docs 统计到论文条目上（转正判据可用）
    merged, _ = merge_delta(papers, {}, _jd_file("智能体安全评估"), TODAY)
    _assert(len(merged["new_tasks"]) == 1 and set(merged["new_tasks"][0]["sources"]) == {"papers", "jd"},
            "锚定规范名 → 跨源合并为一条")
    _assert(_jd_doc_count(merged["new_tasks"][0]) == 1, "jd_docs 正确统计（确证未丢失）")
    # 修复前缺陷对照：锚定提及变体名 → norm 不等 → 合并失败、确证丢失
    merged2, _ = merge_delta(papers, {}, _jd_file("智能体安全性评估"), TODAY)
    _assert(len(merged2["new_tasks"]) == 2 and _jd_doc_count(merged2["new_tasks"][0]) == 0,
            "对照：锚定变体名则合并失败、jd_docs 丢失（为何必须锚定规范名）")


def main():
    tmp = tempfile.mkdtemp(prefix="jdsrc_fixture_")
    try:
        test_delta_store_jd(tmp)
        test_snapshot_contrib_branches()
        test_merge_three_sources()
        test_participation(tmp)
        test_confirm_anchor(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n全部通过 ✓")


if __name__ == "__main__":
    main()
