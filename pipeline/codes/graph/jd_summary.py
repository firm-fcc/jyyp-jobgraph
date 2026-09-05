# -*- coding: utf-8 -*-
"""JD 多维分类汇总 CSV：每 JD 一行，**各项用中文名称而非代号**。

读 data/timeline/jd_derived/{window}.jd_vectors.jsonl（Stage B+C 产物，skill_vec_prof 已回填）
→ 各 code 经体系 name_zh 映射为中文名 → 写 data/graph/data/jd_summary_{window}.csv。

接入管线：Stage D（base_builder 消费模式）构建基图后自动调用 write_summary_csv；
也可独立运行 `python run_jd_summary.py --window 2025-10` 重生成。
代号仍保留在 jd_vectors.jsonl（程序化 join 用），本 CSV 供人工查阅。
"""
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
TIMELINE_JD_DIR = os.path.join(REPO, "data", "timeline", "jd_derived")
SUMMARY_DIR = os.path.join(REPO, "data", "graph", "data")

_ANN = os.path.join(REPO, "codes", "jd_annotate")
if _ANN not in sys.path:
    sys.path.insert(0, _ANN)
import common as ann_common          # 技术栈体系路径/加载
import graph_config as gconfig       # jobs/tasks/skills 基准路径（经 taxonomy_base.json 切换）

# 岗位级别 code → 中文名
LEVEL_NAMES = {"L0": "实习/应届", "L1": "初级(0-2年)", "L2": "中级(3-4年)",
               "L3": "高级(5-9年)", "L4": "专家(10年+)"}

COLUMNS = [
    "jd_key", "jobid", "opentime", "title", "funtype", "std_job", "it_related", "tier",
    "techstack", "level", "level_source",
    "salary", "salary_monthly", "salary_weight", "sample_weight", "work_year",
    "skill_vec_01",          # |-joined 技能中文名（0/1 present 向量）
    "skill_vec_prof",        # 技能名:等级;...（P1-P4/U）
    "task_vec_01",           # |-joined 任务中文名
    "skillpoint_map",        # 技能名:技能点1,技能点2;...
    "n_skills", "n_tasks", "n_skillpoints", "n_prof",
]


def _load_name_maps():
    """加载 jobs/skills/tasks/techstacks 的 code→name_zh 映射（经基准切换）。"""
    nm = {}
    for key, p in (("job", gconfig.BASE_NODE_FILES["jobs"]),
                   ("skill", gconfig.BASE_NODE_FILES["skills"]),
                   ("task", gconfig.BASE_NODE_FILES["tasks"])):
        try:
            data = json.load(open(p, encoding="utf-8"))
        except OSError:
            nm[key] = {}
            continue
        if key == "task":
            nm[key] = {t["code"]: t.get("name_zh", t["code"]) for t in data.get("tasks", [])}
        else:
            nm[key] = {c: d.get("name_zh", c) for c, d in (data.get("detail") or {}).items()}
    # 技术栈体系（common.TAXONOMY_PATH）
    try:
        ts = ann_common.load_taxonomy()
        nm["techstack"] = {c: d.get("name_zh", c) for c, d in ts.items()}
    except Exception:
        nm["techstack"] = {}
    return nm


def _row(rec, nm):
    sk = nm.get("skill", {})
    job_name = nm.get("job", {}).get(rec.get("job_code") or "", rec.get("job_code") or "")
    svp = rec.get("skill_vec_prof") or {}
    sp_map = rec.get("skillpoint_map") or {}
    return {
        "jd_key": rec.get("jd_key", ""),
        "jobid": rec.get("jobid", ""),
        "opentime": rec.get("opentime", ""),
        "title": rec.get("title", ""),
        "funtype": rec.get("funtype", ""),
        "std_job": job_name,          # 标准岗位分类名（jobs_v2 name_zh）
        "it_related": rec.get("it_related", ""),
        "tier": rec.get("tier", ""),
        "techstack": "|".join(nm.get("techstack", {}).get(c, c) for c in (rec.get("techstack") or [])),
        "level": LEVEL_NAMES.get(rec.get("level") or "", rec.get("level") or ""),
        "level_source": rec.get("level_source", "") or "",
        "salary": rec.get("salary", "") or "",
        "salary_monthly": rec.get("salary_monthly", "") or "",
        "salary_weight": rec.get("salary_weight", 1.0),
        "sample_weight": rec.get("sample_weight", 1.0),   # Stage S 逆概率权重（未降采样恒 1）
        "work_year": rec.get("work_year", "") or "",
        "skill_vec_01": "|".join(sk.get(c, c) for c in (rec.get("skill_vec_01") or [])),
        "skill_vec_prof": ";".join(f"{sk.get(c, c)}:{l}" for c, l in svp.items()),
        "task_vec_01": "|".join(nm.get("task", {}).get(c, c) for c in (rec.get("task_vec_01") or [])),
        "skillpoint_map": ";".join(f"{sk.get(s, s)}:{','.join(sps)}" for s, sps in sp_map.items()),
        "n_skills": len(rec.get("skill_vec_01") or []),
        "n_tasks": len(rec.get("task_vec_01") or []),
        "n_skillpoints": sum(len(sps) for sps in sp_map.values()),
        "n_prof": len(svp),
    }


def write_summary_csv(window, out_dir=SUMMARY_DIR, name_maps=None):
    """读 {window}.jd_vectors.jsonl → 写 jd_summary_{window}.csv（中文名）。返回 (path, n_rows)。

    只写 it_related=True 的记录（非IT/范围外/无技术信号降级记录留在源文件备查，不进汇总）；
    近重复抄袭变体（{窗口}.dedup.json）同口径剔除。
    """
    out_dir = out_dir or SUMMARY_DIR
    src = os.path.join(TIMELINE_JD_DIR, f"{window}.jd_vectors.jsonl")
    if not os.path.exists(src):
        raise FileNotFoundError(f"jd_vectors 源文件不存在: {src}（先跑 Stage B+C）")
    nm = name_maps if name_maps is not None else _load_name_maps()
    import jd_dedup
    near_dup_variants = jd_dedup.load_variants(window)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"jd_summary_{window}.csv")
    n = 0
    with open(src, encoding="utf-8") as fin, \
         open(path, "w", encoding="utf-8-sig", newline="") as fout:
        w = csv.DictWriter(fout, fieldnames=COLUMNS)
        w.writeheader()
        for line in fin:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("jd_key") in near_dup_variants:
                continue                      # Stage D0 近重复：抄袭变体不进汇总（与 D 聚合同口径）
            if not rec.get("it_related", True):
                continue
            w.writerow(_row(rec, nm))
            n += 1
    return path, n
