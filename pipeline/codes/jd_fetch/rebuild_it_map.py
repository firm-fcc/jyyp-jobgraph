# -*- coding: utf-8 -*-
"""
为 JD-Origin 老数据（本地 51job 库）重建 funtype → IT 判定映射。

背景：原判定的 funtype_it_map.json / 51job_it_jobs_classified.json 生成于原机器，
未随仓库同步（gitignore），本机缺失。本脚本用本地仍存在的资源重建等效映射：

  1. gather  本地库全部 job 表 distinct funtype（按 " or " 拆 part）
  2. 种子判定（零成本，来源可追溯）：
       - blacklist  summary.json correction_2026_08_06 移除的 6 个误判 funtype
                    （与 job_classification.json mapping_log.llm_direct_it 完全一致，人工修正优先）
       - csv_seed   data/jd_dataset/*.csv 的 funtype 列并集（实际通过 2025-26 过滤的 IT 串）
       - system_seed  jobs0806.json 的 name_zh/funtypes + job_classification.json hierarchy.funtypes
       - merge_log  job_classification.json mapping_log.rule_merges / llm_merges（part→IT 分类）
  3. rule_merge  复用 merge_classify_funtypes.rule_merge：去括号/去后缀后命中
       IT 体系名 → IT；命中非 IT 体系名 → 非 IT
  4. LLM 兜底  剩余未知 part 送 deepseek-v4-flash（prompt/重试复用 merge_classify_funtypes）

输出 output/funtype_it_map_origin.json（schema 与原 funtype_it_map.json 一致）：
  {"total_parts": N, "parts": {part: {it_related, confidence, reason, matched_to, source}}}

用法（jd_fetch 目录下）：
  python rebuild_it_map.py --skip-llm    # 先看种子+规则覆盖率（不调 API）
  python rebuild_it_map.py               # 全流程（需 codes/api-key.txt）
"""
import argparse
import csv
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "job_classify_51job")))
import config
import classify_jobs as cj
from merge_classify_funtypes import rule_merge, classify_batch, BATCH

CONFIG_ORIGIN = os.path.join(HERE, "config_origin.yaml")
OUT = os.path.join(HERE, "output", "funtype_it_map_origin.json")
PARTS_OUT = os.path.join(HERE, "output", "all_funtype_parts_origin.json")
PROJECT = config.PROJECT_ROOT

JD_DIR = os.path.join(PROJECT, "data", "jd_dataset")
SUMMARY = os.path.join(JD_DIR, "summary.json")
JOBS0806 = os.path.join(PROJECT, "classify", "Jobs", "jobs0806.json")
JOB_CLASS = os.path.join(PROJECT, "docs", "job_classification.json")
DD = os.path.join(PROJECT, "classify", "docs", "51job_classify", "dd_funtype_translation.json")

SPLIT_RE = re.compile(r"\s+or\s+")

# 负证据覆盖：LLM 误判纠正（判 IT，但该 part 在 2025-26 现有 IT 数据 funtype 列从未出现，
# 即原 funtype_it_map.json 判定为非 IT —— 以原过滤实际行为为准）
FORCE_NONIT = {
    "质检员/测试员(QC)",  # 原过滤从未导出；LLM 因"测试员"联想到软件测试属误判
}


def split_parts(ft):
    return [p.strip() for p in SPLIT_RE.split(ft) if p.strip()]


# ---------- Phase 1: gather 本地库 funtype parts ----------

def gather_parts(section):
    conn = config.connect(section)
    cur = conn.cursor()
    tables = config.get_tables(section)
    part_info = {}
    for tbl in tables:
        try:
            cur.execute(f"SELECT funtype, COUNT(*) FROM `{tbl}` "
                        "WHERE funtype IS NOT NULL AND funtype<>'' GROUP BY funtype")
            rows = cur.fetchall()
        except Exception as e:
            print(f"  [warn] {tbl}: {e}", flush=True)
            continue
        for ft, cnt in rows:
            for p in split_parts(ft):
                ent = part_info.setdefault(p, {"tables": set(), "count": 0})
                ent["tables"].add(tbl)
                ent["count"] += cnt
        print(f"  {tbl}: 累计 {len(part_info)} parts", flush=True)
    conn.close()
    return part_info


# ---------- Phase 2: 本地种子资源 ----------

def load_blacklist():
    """2026-08-06 人工修正移除的误判 funtype（最强优先级：非 IT）。"""
    s = json.load(open(SUMMARY, encoding="utf-8"))
    removed = s["meta"]["correction_2026_08_06"]["removed_funtypes"]
    jc = json.load(open(JOB_CLASS, encoding="utf-8"))
    ldi = [e["part"] for e in jc["mapping_log"]["llm_direct_it"]]
    return sorted(set(removed) | set(ldi))


def load_csv_seed():
    """现有 22 个 CSV 的 funtype 列并集（修正后实际导出的 IT 串）→ part 集合。"""
    parts = set()
    files = sorted(glob.glob(os.path.join(JD_DIR, "*.csv")))
    for path in files:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or "funtype" not in reader.fieldnames:
                continue
            for row in reader:
                ft = row.get("funtype") or ""
                parts.update(split_parts(ft))
    print(f"  csv_seed: {len(files)} 个 CSV → {len(parts)} 个 IT parts", flush=True)
    return parts


def load_system_seed():
    """IT 体系名（jobs0806 name_zh + 各节点 funtypes + hierarchy funtypes）。"""
    jobs = json.load(open(JOBS0806, encoding="utf-8"))
    names = set()
    for v in jobs["detail"].values():
        names.add(v["name_zh"])
        names.update(v.get("funtypes", []))
    jc = json.load(open(JOB_CLASS, encoding="utf-8"))
    tree_names = set()

    def walk(nodes):
        for n in nodes:
            tree_names.add(n["name"])
            tree_names.update(n.get("funtypes", []))
            walk(n.get("children", []))

    walk(jc["hierarchy"])
    return names, tree_names


def load_merge_log():
    jc = json.load(open(JOB_CLASS, encoding="utf-8"))
    m = jc["mapping_log"]
    entries = {}
    for e in m["rule_merges"] + m["llm_merges"]:
        entries[e["part"]] = e["matched_to"]
    return entries


def load_dd_names():
    dd = json.load(open(DD, encoding="utf-8"))
    return set(v["value"] for v in dd.values())


# ---------- 主流程 ----------

def build_map(part_info, skip_llm=False):
    blacklist = set(load_blacklist())
    csv_seed = load_csv_seed()
    sys_names, tree_names = load_system_seed()
    system_seed = sys_names | tree_names
    merge_log = load_merge_log()
    dd_names = load_dd_names()

    all_parts = sorted(part_info.keys())
    final_map = {}

    # 1. 黑名单（人工修正，最强）
    for p in all_parts:
        if p in blacklist:
            final_map[p] = {"it_related": False, "confidence": 1.0, "reason": "2026-08-06修正移除",
                            "matched_to": None, "source": "blacklist"}
    # 2. csv_seed（实际通过过滤的证据）
    for p in all_parts:
        if p in final_map:
            continue
        if p in csv_seed:
            final_map[p] = {"it_related": True, "confidence": 1.0, "reason": "现有数据集IT串",
                            "matched_to": p, "source": "csv_seed"}
    # 3. system_seed（IT 体系名精确命中）
    for p in all_parts:
        if p in final_map:
            continue
        if p in system_seed:
            final_map[p] = {"it_related": True, "confidence": 0.95, "reason": "IT体系名命中",
                            "matched_to": p, "source": "system_seed"}
    # 4. merge_log（原 LLM/规则归类记录：part → IT 分类名）
    for p in all_parts:
        if p in final_map:
            continue
        if p in merge_log:
            final_map[p] = {"it_related": True, "confidence": 0.9, "reason": "原映射记录",
                            "matched_to": merge_log[p], "source": "merge_log"}
    # 5. rule_merge（去括号/去后缀命中体系名；IT 名与非 IT 名两侧）
    it_side = system_seed
    nonit_side = dd_names - system_seed
    for p in all_parts:
        if p in final_map:
            continue
        m = rule_merge(p, dd_names)
        if m:
            if m in it_side:
                final_map[p] = {"it_related": True, "confidence": 0.95, "reason": f"规则合并自{m}",
                                "matched_to": m, "source": "rule_merge"}
            elif m in nonit_side:
                final_map[p] = {"it_related": False, "confidence": 0.95, "reason": f"规则合并自{m}",
                                "matched_to": m, "source": "rule_merge"}
    # 6. LLM 兜底
    need_llm = [p for p in all_parts if p not in final_map]
    src_cnt = lambda: {s: sum(1 for v in final_map.values() if v["source"] == s)
                       for s in {"blacklist", "csv_seed", "system_seed", "merge_log", "rule_merge"}}
    print(f"总 parts: {len(all_parts)} | 种子+规则: {len(final_map)} | 需LLM: {len(need_llm)} | {src_cnt()}",
          flush=True)
    if need_llm and not skip_llm:
        api_key = cj.load_api_key("")
        model = os.environ.get("DEEPSEEK_MODEL", cj.DEFAULT_MODEL)
        it_names = sorted(it_side | csv_seed)
        print(f"LLM 处理 {len(need_llm)} 个（batch={BATCH}，IT参照 {len(it_names)} 个）...", flush=True)
        llm_res = {}
        for i in range(0, len(need_llm), BATCH):
            chunk = need_llm[i:i + BATCH]
            llm_res.update(classify_batch(api_key, model, chunk, it_names))
            print(f"  进度 {len(llm_res)}/{len(need_llm)}", flush=True)
        final_map.update(llm_res)
    elif need_llm:
        print("（--skip-llm：未知 parts 未判定，输出中 source=unknown）", flush=True)

    # 7. 负证据覆盖（最高优先级，在 LLM 之后应用）
    for p, ent in final_map.items():
        if p in FORCE_NONIT and ent["it_related"]:
            ent.update({"it_related": False, "confidence": 1.0,
                        "reason": "原2025-26过滤从未导出（负证据对齐）", "matched_to": None,
                        "source": "force_nonit"})

    result = {"total_parts": len(all_parts),
              "parts": {p: final_map.get(p, {"it_related": False, "confidence": 0, "reason": "未判定",
                                              "matched_to": None, "source": "unknown"})
                        for p in all_parts}}
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-llm", action="store_true", help="不调 LLM，只做种子+规则（覆盖率预览）")
    ap.add_argument("--config", default=CONFIG_ORIGIN)
    ap.add_argument("--no-gather", action="store_true", help="复用已有 all_funtype_parts_origin.json")
    args = ap.parse_args()

    section = config.load_config(args.config)

    parts_file = PARTS_OUT
    if args.no_gather and os.path.exists(parts_file):
        data = json.load(open(parts_file, encoding="utf-8"))
        part_info = {p: {"tables": set(v["tables"]), "count": v["count"]}
                     for p, v in data["parts"].items()}
        print(f"复用已有 parts: {len(part_info)}", flush=True)
    else:
        print("Phase 1: gather 本地库 funtype parts ...", flush=True)
        part_info = gather_parts(section)
        os.makedirs(os.path.dirname(parts_file), exist_ok=True)
        with open(parts_file, "w", encoding="utf-8") as f:
            json.dump({"total_parts": len(part_info),
                       "parts": {p: {"tables": sorted(v["tables"]), "count": v["count"]}
                                 for p, v in sorted(part_info.items())}},
                      f, ensure_ascii=False, indent=2)
        print(f"parts 输出: {parts_file}", flush=True)

    print("Phase 2: 种子 + 规则 + LLM 判定 ...", flush=True)
    result = build_map(part_info, skip_llm=args.skip_llm)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    it_n = sum(1 for v in result["parts"].values() if v["it_related"])
    src = {}
    for v in result["parts"].values():
        src[v["source"]] = src.get(v["source"], 0) + 1
    it_rows = sum(part_info[p]["count"] for p, v in result["parts"].items() if v["it_related"])
    total_rows = sum(v["count"] for v in part_info.values())
    print(f"\n完成！IT parts: {it_n}/{len(result['parts'])} | 来源分布: {src}")
    print(f"IT 行数覆盖: {it_rows:,}/{total_rows:,}（{it_rows/total_rows*100:.1f}%）")
    print(f"输出: {OUT}")


if __name__ == "__main__":
    main()
