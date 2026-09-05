# -*- coding: utf-8 -*-
"""转正（promotion）自测：候选门槛 / 三体系写入（T-续号、T-DG 组、GJ- 岗位）/
ΔG 源 graduated 标记 / 备份 / dry-run 不写 / 二次运行收敛。

运行：cd codes/graph && python fixtures/test_promotion.py
（临时基准副本 + 临时三源 delta，零 LLM，不触碰正式产物。）
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import date

import ut

ut.setup("graph", "builder")
ut.isolate()

from promotion import run_promotion, evaluate_candidates  # noqa: E402
import graph_config as gconfig                     # noqa: E402
import config                                       # noqa: E402  builder config

TODAY = date(2026, 8, 17)


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ✓ {msg}")


def _entry(id_, name, ev, extra=None):
    e = {"id": id_, "name_zh": name, "name_en": "", "evidence": ev}
    if extra:
        e.update(extra)
    return e


def _jd_ev(*doc_ids, grade="require"):
    """确证通道级 JD 证据（grade=require 计入转正确证；scan=发现级不计）。"""
    return {d: {"date": "2026-08-16", "sentences": [f"{d} 原文"], "confidence": "high",
                "src": "jd", "grade": grade}
            for d in doc_ids}


def _pp_ev():
    return {"2401.00001": {"date": "2026-08-15", "sentences": ["论文句"], "confidence": "high", "tier": "S"}}


def _mk_fixture(tmp):
    """临时三源 delta + 临时基准副本（真实文件拷贝，写坏也不影响正式产物）。"""
    papers = {"new_tasks": [_entry("PT-001", "智能体安全评估", _pp_ev(), {"description": "对智能体系统进行安全评估。"}),
                            _entry("PT-004", "无JD确证任务", _pp_ev(), {"description": "仅论文信号。"})],
              "new_skills": [_entry("PS-001", "边缘智能部署", _pp_ev(),
                                    {"definition": "在边缘设备上部署智能应用。", "skill_type": "hard"})],
              "new_jobs": [_entry("PJ-001", "提示词工程师", _pp_ev(), {"definition": "设计提示词的工程师。", "status": "pending"})],
              "skillpoints": [], "strengthenings": []}
    jd = {"new_tasks": [_entry("PT-001", "智能体安全评估", _jd_ev("job-1", "job-2"), {"description": ""}),
                        _entry("PT-002", "单文档任务", _jd_ev("job-3"), {"description": "JD 侧仅 1 篇。"})],
          "new_skills": [_entry("PS-001", "边缘智能部署", _jd_ev("job-4", "job-5"), {"definition": "", "skill_type": "hard"})],
          "new_jobs": [_entry("PJ-001", "提示词工程师", _jd_ev("job-6", "job-7", "job-8"), {"definition": "", "status": "pending"}),
                       _entry("PJ-002", "弱确证岗位", _jd_ev("job-9", "job-10"), {"definition": "", "status": "pending"})],
          "skillpoints": [], "strengthenings": []}
    files = {"papers": os.path.join(tmp, "papers_delta.json"),
             "news": os.path.join(tmp, "news_delta.json"),
             "jd": os.path.join(tmp, "jd_delta.json")}
    json.dump(papers, open(files["papers"], "w", encoding="utf-8"), ensure_ascii=False)
    json.dump({"new_tasks": [], "new_skills": [], "new_jobs": [], "skillpoints": [], "strengthenings": []},
              open(files["news"], "w", encoding="utf-8"))
    json.dump(jd, open(files["jd"], "w", encoding="utf-8"), ensure_ascii=False)
    tax = {"tasks": os.path.join(tmp, "tasks.json"),
           "skills": os.path.join(tmp, "skills0805.json"),
           "jobs": os.path.join(tmp, "jobs_v2.json")}
    for key, src in (("tasks", gconfig.BASE_NODE_FILES["tasks"]),
                     ("skills", gconfig.BASE_NODE_FILES["skills"]),
                     ("jobs", gconfig.BASE_NODE_FILES["jobs"])):
        shutil.copy(src, tax[key])
    return files, tax


def test_dry_run(tmp):
    """dry-run：不写任何文件；门槛不足者（JD 文档数<2/3 或无确证）不转正。"""
    print("== dry-run：只评估不写 ==")
    files, tax = _mk_fixture(tmp)
    md5_before = {p: open(p, "rb").read() for p in list(files.values()) + list(tax.values())}
    report = run_promotion(delta_files=files, tax_paths=tax, dry_run=True, now=TODAY,
                           backup_root=tmp, log_path=os.path.join(tmp, "promotion_log.md"))
    names = sorted(c["name_zh"] for c in report["candidates"])
    _assert(names == ["提示词工程师", "智能体安全评估", "边缘智能部署"],
            f"候选 = 强度达标且 JD 确证达标的三条（{names}）")
    _assert(all(open(p, "rb").read() == c for p, c in md5_before.items()),
            "dry-run 未写任何文件")
    cands, _ = evaluate_candidates(files, TODAY)
    by_name = {c["name_zh"]: c for c in cands}
    _assert(by_name["智能体安全评估"]["jd_docs"] == 2 and by_name["提示词工程师"]["jd_docs"] == 3,
            "jd_docs 只统计 require 级确证证据数")
    _assert("单文档任务" not in by_name and "无JD确证任务" not in by_name
            and "弱确证岗位" not in by_name,
            "门槛拒绝：JD 文档数不足（任务<2 / 岗位<3）与无 JD 确证者不转正")


def test_scan_grade_not_counted(tmp):
    """发现通道证据（grade=scan，"词在 JD 中出现过"）不计确证——防 v2 当窗发现"出生即转正"。"""
    print("== scan 级证据不计确证 ==")
    files, tax = _mk_fixture(tmp)
    jd = json.load(open(files["jd"], encoding="utf-8"))
    # 智能体安全评估改为 5 篇 scan 级证据（v2 发现路径的真实形态：强度拉满、非确证）
    jd["new_tasks"][0]["evidence"] = _jd_ev("job-1", "job-2", "job-3", "job-4", "job-5",
                                            grade="scan")
    json.dump(jd, open(files["jd"], "w", encoding="utf-8"), ensure_ascii=False)
    report = run_promotion(delta_files=files, tax_paths=tax, dry_run=True, now=TODAY,
                           backup_root=tmp, log_path=os.path.join(tmp, "promotion_log.md"))
    names = [c["name_zh"] for c in report["candidates"]]
    _assert("智能体安全评估" not in names,
            f"scan 级不充当确证（{names}）——require 级（确证通道）才计入 jd_docs")
    # 对照：同条目换成 2 篇 require 级即恢复候选资格
    jd["new_tasks"][0]["evidence"] = _jd_ev("job-1", "job-2", grade="require")
    json.dump(jd, open(files["jd"], "w", encoding="utf-8"), ensure_ascii=False)
    report = run_promotion(delta_files=files, tax_paths=tax, dry_run=True, now=TODAY,
                           backup_root=tmp, log_path=os.path.join(tmp, "promotion_log.md"))
    _assert("智能体安全评估" in [c["name_zh"] for c in report["candidates"]],
            "require 级确证 2 篇即达标")


def _next_task_code(tasks_path):
    """按基准现有任务 code 推导续号（promotion 的 T-{max+1} 同口径，勿写死——基准会迭代）。"""
    tasks = json.load(open(tasks_path, encoding="utf-8"))["tasks"]
    nums = [int(t["code"].split("-")[1]) for t in tasks if t["code"].startswith("T-")]
    return f"T-{(max(nums) + 1) if nums else 1:02d}"


def test_promote_and_mark(tmp):
    """执行转正：任务 T-续号+定义、技能 T-DG 组、岗位 GJ-（funtypes=名称）、版本/total 提升、graduated 标记。"""
    print("== 转正写入 + ΔG 标记 + 备份 ==")
    files, tax = _mk_fixture(tmp)
    t_new = _next_task_code(gconfig.BASE_NODE_FILES["tasks"])
    _jobs0 = json.load(open(tax["jobs"], encoding="utf-8"))
    _gj_nums = [int(k[3:]) for k in _jobs0.get("detail", {}) if k.startswith("GJ-") and k[3:].isdigit()]
    j_new = f"GJ-{(max(_gj_nums) + 1) if _gj_nums else 1:03d}"
    n_jobs0 = _jobs0.get("meta", {}).get("n_jobs", len(_jobs0["detail"]))
    _sk0 = json.load(open(tax["skills"], encoding="utf-8"))
    _dg_nums = [int(k[5:]) for k in _sk0.get("detail", {}) if k.startswith("T-DG-") and k[5:].isdigit()]
    s_new = f"T-DG-{(max(_dg_nums) + 1) if _dg_nums else 1:02d}"
    n_sk0 = _sk0.get("total", len(_sk0.get("detail", {})))
    report = run_promotion(delta_files=files, tax_paths=tax, dry_run=False, now=TODAY,
                           backup_root=tmp, log_path=os.path.join(tmp, "promotion_log.md"))
    promoted = {p["name_zh"]: p["new_code"] for p in report["promoted"]}
    _assert(promoted == {"智能体安全评估": t_new, "边缘智能部署": s_new, "提示词工程师": j_new},
            f"code 分配：任务续号 {t_new}、技能续组 {s_new}、岗位 {j_new}（{promoted}）")

    tasks = json.load(open(tax["tasks"], encoding="utf-8"))
    t36 = next(t for t in tasks["tasks"] if t["code"] == t_new)
    _assert(t36["name_zh"] == "智能体安全评估" and t36["description"], "tasks.json 追加含定义")
    _assert(tasks["date"] == "2026-08-17" and float(tasks["version"]) > 0.1, "版本/日期提升")

    skills = json.load(open(tax["skills"], encoding="utf-8"))
    sd = skills["detail"][s_new]
    _assert(sd["name_zh"] == "边缘智能部署" and sd["skill_type"] == "hard", "skills detail 追加")
    grp = skills["简明体系"]["T"]["groups"]["T-DG"]
    _assert(grp["skills"][-1] == "边缘智能部署" and "边缘智能部署" in grp["skills"],
            "简明体系 T-DG 组追加")
    _assert(skills["total"] == n_sk0 + 1, "total +1")

    jobs = json.load(open(tax["jobs"], encoding="utf-8"))
    jd1 = jobs["detail"][j_new]
    _assert(jd1["name_zh"] == "提示词工程师" and jd1["funtypes"] == ["提示词工程师"],
            "jobs detail 追加（funtypes=名称，供 funtype 映射命中）")
    _assert(jd1["category"] == "" and jd1["graduated"] == "2026-08-17",
            "v2 结构条目（categories 纯组织维度，GJ- 不挂类别；记录转正日期）")
    _assert(jobs["meta"]["n_jobs"] == n_jobs0 + 1, "meta.n_jobs +1")

    pd = json.load(open(files["papers"], encoding="utf-8"))
    jdt = json.load(open(files["jd"], encoding="utf-8"))
    for data, id_ in ((pd, "PT-001"), (pd, "PS-001"), (pd, "PJ-001"), (jdt, "PT-001")):
        it = next(x for x in data["new_tasks"] + data["new_skills"] + data["new_jobs"] if x["id"] == id_)
        _assert(it["status"] == "graduated" and it["promoted_to"] and it["promoted_date"] == "2026-08-17",
                f"{id_} 标记 graduated（promoted_to={it['promoted_to']}）")
    _assert(all(x.get("status") != "graduated" for x in jdt["new_tasks"] if x["id"] == "PT-002"),
            "未达标条目不被标记")

    bdir = report["backup_dir"]
    backed = sorted(os.listdir(bdir))
    _assert(set(backed) == {"tasks.json", "skills0805.json", "jobs_v2.json"},
            f"写入前已备份三基准文件 → {bdir}")
    _assert(json.load(open(os.path.join(bdir, "tasks.json"), encoding="utf-8"))["tasks"]
            == json.load(open(gconfig.BASE_NODE_FILES["tasks"], encoding="utf-8"))["tasks"],
            "备份内容 = 写入前状态（可回滚）")

    # 二次运行收敛：graduated 已跳过，无新候选
    report2 = run_promotion(delta_files=files, tax_paths=tax, dry_run=False, now=TODAY,
                            backup_root=tmp, log_path=os.path.join(tmp, "promotion_log.md"))
    _assert(report2["n_candidates"] == 0, "二次运行无候选（graduated 跳过，收敛）")
    tasks2 = json.load(open(tax["tasks"], encoding="utf-8"))
    _assert(len([t for t in tasks2["tasks"] if t["code"] == t_new]) == 1, "不重复追加")


def main():
    tmp = tempfile.mkdtemp(prefix="promo_fixture_")
    try:
        test_dry_run(tmp)
        test_promote_and_mark(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n全部通过 ✓")


if __name__ == "__main__":
    main()
