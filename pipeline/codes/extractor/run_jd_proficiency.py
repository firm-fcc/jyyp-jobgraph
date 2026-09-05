# -*- coding: utf-8 -*-
"""JD 技能熟练度要求判定 CLI：抽样 → 逐 JD 评估 → 校准报告。

抽样口径与基图生产路径（graph/base_builder.sample_jds）一致：funtype→jobs0806
岗位映射过滤非 IT、每岗位 ≤per_job 条、总量 ≤n、文本 ≥100 字、md5 去重——
保证校准结论可直接外推到生产线（M3 接线前的量规审阅依据）。

用法：
  python run_jd_proficiency.py --window 2026-05 --n 200            # 评估 + 报告
  python run_jd_proficiency.py --n 50 --no-cache                   # 默认取最新窗口，强制重评
  python run_jd_proficiency.py --window 2026-05 --n 0              # 只重出报告（用缓存，0 次 LLM）

产物（codes/extractor/output/，gitignored）：
  jd_prof_results_{window}.json     逐 JD 评估明细（复查用）
  jd_prof_calibration.md            校准报告（等级分布/旗标率/词面锚点×等级交叉表/抽样明细）
"""
import argparse
import csv
import glob
import hashlib
import json
import os
import re
from collections import Counter

import config
from jd_proficiency import (LEVELS, MARKER_RANK, JDProficiencyEvaluator,
                            aggregate_proficiency)

JD_DIR = os.path.join(config.PROJECT_ROOT, "data", "timeline", "jd")
JD_DERIVED_DIR = os.path.join(config.PROJECT_ROOT, "data", "timeline", "jd_derived")  # 管线产物（与源 CSV 分离）
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "settings.yaml")


def _settings_jd(key, default):
    """读 settings.yaml → jd_proficiency.{key}（缺省兜底）。"""
    try:
        import yaml
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            node = yaml.safe_load(f) or {}
        return node["jd_proficiency"][key]
    except (OSError, ValueError, KeyError, TypeError):
        return default

DEFAULT_N = 200
DEFAULT_PER_JOB = 5       # 与 graph_base.per_job 对齐
MIN_TEXT_CHARS = 100      # 与 graph_base.min_text_chars 对齐


# ---------------- 抽样（复刻 base_builder 口径，避免跨模块导入 graph） ----------------
def load_funtype_map():
    """funtype 片段（含岗位名）→ 岗位 code。与 base_builder.load_funtype_map 同源同构。"""
    with open(config.JOB_TAXONOMY, encoding="utf-8") as f:
        detail = json.load(f).get("detail", {})
    mapping = {}
    for code, nd in detail.items():
        if not isinstance(nd, dict):
            continue
        mapping[nd.get("name_zh", "")] = code
        for ft in nd.get("funtypes", []) or []:
            mapping[ft] = code
    mapping.pop("", None)
    return mapping


def match_job_code(funtype, mapping):
    for part in re.split(r"\s+or\s+", funtype or ""):
        code = mapping.get(part.strip())
        if code:
            return code
    return None


def sample_jds(csv_path, n, per_job):
    """流式扫描月度 CSV → 岗位分层抽样。返回 (rows, stats)。"""
    mapping = load_funtype_map()
    rows, per_code, seen = [], {}, set()
    n_scanned = n_unmatched = n_short = n_dup = 0
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            n_scanned += 1
            code = match_job_code(row.get("funtype", ""), mapping)
            if not code:
                n_unmatched += 1
                continue
            if per_code.get(code, 0) >= per_job:
                continue
            text = (row.get("job_information") or row.get("job") or "").strip()
            if len(text) < MIN_TEXT_CHARS:
                n_short += 1
                continue
            key = hashlib.md5(text.encode("utf-8")).hexdigest()
            if key in seen:
                n_dup += 1
                continue
            seen.add(key)
            per_code[code] = per_code.get(code, 0) + 1
            rows.append({"job_code": code, "jobid": row.get("jobid", ""),
                         "title": row.get("job", ""), "funtype": row.get("funtype", ""),
                         "work_year": row.get("work_year", ""),
                         "level": row.get("level", ""), "text": text})
            if len(rows) >= n:
                break
    stats = {"n_scanned": n_scanned, "n_sampled": len(rows), "n_jobs": len(per_code),
             "n_unmatched": n_unmatched, "n_skip_short": n_short, "n_skip_dup": n_dup}
    return rows, stats


# ---------------- 报告 ----------------
def _pct(n, total):
    return f"{n / total * 100:.1f}%" if total else "-"


def build_report(window, records, agg, stats, ev_stats, detail_rows=40):
    """records: [{_row: {jobid,title}, skills: {code: 技能记录}}]（CLI 已附加 _row）。"""
    n_pairs = sum(d["n"] for d in agg.values())
    level_total, suff_total = Counter(), Counter()
    flag_total, marker_level = Counter(), Counter()
    # 交叉表按最高锚点档归行（精通>熟练/深入理解/扎实>熟悉/掌握>了解>无）
    rank_name = {4: "精通", 3: "熟练/扎实/深入理解", 2: "熟悉/掌握", 1: "了解"}
    for rec in records:
        for s in rec["skills"].values():
            lv = s.get("requirement_level")
            if lv:
                level_total[lv] += 1
            suff_total[s.get("evidence_sufficiency") or "unset"] += 1
            for f in s.get("flags", []):
                flag_total[f] += 1
            if lv and lv != "U":
                r = max((MARKER_RANK[m] for m in (s.get("markers") or [])), default=0)
                marker_level[(rank_name.get(r, "无锚点"), lv)] += 1

    lines = [f"# JD 技能熟练度校准报告：{window}", ""]
    lines.append(f"- 抽样：扫描 {stats['n_scanned']} 行 → {stats['n_sampled']} 条 / "
                 f"{stats['n_jobs']} 岗位（非IT过滤 {stats['n_unmatched']}、短文本 {stats['n_skip_short']}、"
                 f"重复 {stats['n_skip_dup']}）")
    lines.append(f"- 评估对：{n_pairs}（JD×技能）；LLM 调用 {ev_stats['n_calls']}、"
                 f"缓存命中 {ev_stats['n_cache_hits']}、重试 {ev_stats['n_retries']}、"
                 f"契约失败 {ev_stats['n_invalid']}")
    lines.append(f"- 量规：{ev_stats['rubric_version']}（模型 {ev_stats['model']}）")
    lines.append("")

    lines.append("## 等级分布（要求侧 P1-P4/U）")
    for lv in LEVELS:
        lines.append(f"- {lv}: {level_total.get(lv, 0)}（{_pct(level_total.get(lv, 0), n_pairs)}）")
    lines.append(f"- 未定级（契约失败）: {n_pairs - sum(level_total.values())}")
    lines.append("")
    lines.append("## 证据充分性分布")
    for k in ("sufficient", "partial", "insufficient", "unset"):
        lines.append(f"- {k}: {suff_total.get(k, 0)}（{_pct(suff_total.get(k, 0), n_pairs)}）")
    lines.append("")

    lines.append("## 旗标（确定性复核，只标记不改正）")
    if flag_total:
        for f, c in flag_total.most_common():
            lines.append(f"- {f}: {c}（{_pct(c, n_pairs)}）")
    else:
        lines.append("- （无旗标）")
    lines.append("")

    lines.append("## 词面锚点 × LLM 等级交叉表（校准核心：偏离对角线过远=量规或锚点需调）")
    cols = [lv for lv in LEVELS if lv != "U"]
    lines.append("| 锚点档 | " + " | ".join(cols) + " | 合计 |")
    lines.append("|" + "---|" * (len(cols) + 2))
    for rname in ("精通", "熟练/扎实/深入理解", "熟悉/掌握", "了解", "无锚点"):
        cells = [marker_level.get((rname, lv), 0) for lv in cols]
        lines.append(f"| {rname} | " + " | ".join(str(c) for c in cells)
                     + f" | {sum(cells)} |")
    lines.append("")

    lines.append("## 技能维度（按评估对数降序，前 20）")
    lines.append("| code | 技能 | 对数 | P1 | P2 | P3 | P4 | U | 未定 | 复核 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for code, d in sorted(agg.items(), key=lambda kv: -kv[1]["n"])[:20]:
        lv = d["levels"]
        lines.append(f"| {code} | {d['name_zh']} | {d['n']} | {lv['P1']} | {lv['P2']} | "
                     f"{lv['P3']} | {lv['P4']} | {lv['U']} | {d['unset']} | {d['review']} |")
    lines.append("")

    lines.append(f"## 抽样明细（前 {detail_rows} 对；旗标项为复核建议，证据句截断展示）")
    lines.append("| 职位 | 技能 | 等级 | 充分性 | 锚点 | 旗标 | 证据句 |")
    lines.append("|---|---|---|---|---|---|---|")
    shown = 0
    for rec in records:
        for code, s in sorted(rec["skills"].items()):
            if shown >= detail_rows:
                break
            ev0 = (s.get("evidence") or [""])[0].replace("|", "／")[:60]
            lines.append(f"| {rec['_row']['title'][:12]} | {s.get('name_zh', code)} | "
                         f"{s.get('requirement_level') or '-'} | "
                         f"{s.get('evidence_sufficiency') or '-'} | "
                         f"{'/'.join(s.get('markers') or []) or '-'} | "
                         f"{'/'.join(s.get('flags') or []) or '-'} | {ev0} |")
            shown += 1
        if shown >= detail_rows:
            break
    lines.append("")
    return "\n".join(lines)


def run_from_vectors(window, marker_gated=False, no_cache=False, soft_gate=None):
    """Stage C：读 jd_vectors 源文件的 evidence_map → 跨 JD 证据去重定级 → 回填 skill_vec_prof。

    - 跳过 Stage B 已算好的 evidence_map（不再二次抽取，零额外 LLM 于证据组装）
    - it_related=False 的记录（无技术信号降级/范围外）跳过定级
    - 跨 JD 去重：相同 (技能, 归一化证据) 只判一次（证据级缓存 jd_prof_evidence_cache.jsonl）
    - marker_gated：无梯度词证据 → 确定性 U 免 LLM
    - soft_gate（默认 settings jd_proficiency.soft_gate=True）：软技能（F-）无梯度词 →
      确定性 U 免 LLM（下游约定 U 视作最低档要求参与匹配）
    - skill_vec_prof 回填到 jd_vectors.jsonl，meta 增 rubric_version + proficiency 统计
    """
    jd_vectors_path = os.path.join(JD_DERIVED_DIR, f"{window}.jd_vectors.jsonl")
    meta_path = os.path.join(JD_DERIVED_DIR, f"{window}.jd_vectors.meta.json")
    if not os.path.exists(jd_vectors_path):
        raise SystemExit(f"jd_vectors 源文件不存在: {jd_vectors_path}（先运行 Stage B run_jd_extract.py）")

    records = []
    with open(jd_vectors_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    n_active = sum(1 for r in records if r.get("it_related", True))
    n_pairs = sum(len(r.get("evidence_map") or {}) for r in records if r.get("it_related", True))
    print(f"[C] {window}：读 {len(records)} JD（it_related {n_active}）/ {n_pairs} (JD×技能) 证据对"
          f"（marker_gated={marker_gated}, soft_gate={soft_gate}）", flush=True)

    if soft_gate is None:
        soft_gate = _settings_jd("soft_gate", default=True)
    ev = JDProficiencyEvaluator(use_cache=False if no_cache else None)

    # ---- Pass 1：串行 prepare 全窗（缓存命中/门控 U/跨 JD 证据去重在此发生）----
    # 同 (技能,证据) 对只留首现——若并发 prepare，重复对在飞会双调且可能产生不一致判定，
    # 违反"同证据同判定"的设计意图，故去重必须发生在串行阶段。
    preps = []
    for rec in records:
        emap = rec.get("evidence_map") or {}
        if not emap or not rec.get("it_related", True):  # 无证据/非IT降级：skill_vec_prof 留空
            continue
        try:
            preps.append((rec, ev.prepare_jd_from_evidence(rec.get("jd_key", ""), emap,
                                                           marker_gated=marker_gated,
                                                           soft_gate=soft_gate)))
        except Exception as e:
            print(f"[C] JD {rec.get('jd_key', '')} prepare 失败: {e}")
    n_pending = sum(len(p["pairs"]) for _, p in preps)
    print(f"[C] Pass 1：待评 {n_pending} 对（缓存命中/门控 U 已定 {n_pairs - n_pending}），"
          f"chunk={ev.chunk_skills}，并行 {ev.concurrency}", flush=True)

    # ---- Pass 2：全窗 chunk 并行送 LLM（chunk 保持在 JD 内——profile 是该 JD 的定级上下文）----
    work = []          # [(prep_idx, profile, chunk)]
    for idx, (rec, prep) in enumerate(preps):
        if not prep["pairs"]:
            continue
        profile = {"title": rec.get("title", ""), "funtype": rec.get("funtype", ""),
                   "work_year": rec.get("work_year", "")}
        for i in range(0, len(prep["pairs"]), ev.chunk_skills):
            work.append((idx, profile, prep["pairs"][i:i + ev.chunk_skills]))

    results = [{} for _ in preps]

    def run_one(w):
        idx, profile, chunk = w
        return idx, ev._evaluate_chunk(chunk, profile)

    if ev.concurrency > 1 and len(work) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=ev.concurrency) as ex:
            for n_done, (idx, out) in enumerate(ex.map(run_one, work), 1):
                results[idx].update(out)
                if n_done % 200 == 0 or n_done == len(work):
                    print(f"[C] Pass 2 进度 {n_done}/{len(work)} chunk"
                          f"（调用 {ev.n_calls}，重试 {ev.n_retries}）", flush=True)
    else:
        for n_done, (idx, out) in enumerate(map(run_one, work), 1):
            results[idx].update(out)
            if n_done % 200 == 0 or n_done == len(work):
                print(f"[C] Pass 2 进度 {n_done}/{len(work)} chunk（调用 {ev.n_calls}）", flush=True)

    # ---- Pass 3：串行 finalize（合并 + 顺序写证据缓存）→ 回填 skill_vec_prof ----
    for (rec, prep), res in zip(preps, results):
        try:
            final = ev.finalize_jd_from_evidence(prep, res)
            rec["skill_vec_prof"] = {c: s.get("requirement_level")
                                     for c, s in final["skills"].items()
                                     if s.get("requirement_level")}
        except Exception as e:
            print(f"[C] JD {rec.get('jd_key', '')} 汇总失败: {e}")
            rec["skill_vec_prof"] = {}
    print(f"[C] Pass 3：已回填 {len(preps)} JD 的 skill_vec_prof", flush=True)

    # 回写 jd_vectors.jsonl（skill_vec_prof 已填）
    with open(jd_vectors_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # 更新 meta（增 rubric_version + proficiency 统计）
    meta = {}
    if os.path.exists(meta_path):
        try:
            meta = json.load(open(meta_path, encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
    meta["rubric_version"] = __import__("jd_proficiency_prompts").RUBRIC_VERSION
    meta["stage"] = "C_proficiency"
    meta["proficiency"] = {
        "marker_gated": marker_gated,
        "soft_gate": soft_gate,
        "n_calls": ev.n_calls, "n_ev_cache_hits": ev.n_cache_hits,
        "n_retries": ev.n_retries, "n_invalid": ev.n_invalid,
        "n_jds": len(records),
        "n_pairs_graded": sum(len(r.get("skill_vec_prof") or {}) for r in records),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    print(f"\n[C] 完成：skill_vec_prof 已回填 {jd_vectors_path}", flush=True)
    print(f"    LLM 调用 {ev.n_calls} | 证据缓存命中 {ev.n_cache_hits} | "
          f"重试 {ev.n_retries} | 契约失败 {ev.n_invalid}", flush=True)
    print(f"    定级对数 {meta['proficiency']['n_pairs_graded']} | meta: {meta_path}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="JD 技能熟练度要求判定（校准运行）")
    ap.add_argument("--window", default="", help="timeline 窗口（如 2026-05），默认最新月度 CSV")
    ap.add_argument("--n", type=int, default=DEFAULT_N, help="抽样 JD 条数（0=仅用缓存重出报告）")
    ap.add_argument("--per-job", type=int, default=DEFAULT_PER_JOB)
    ap.add_argument("--detail", type=int, default=40, help="报告明细行数")
    ap.add_argument("--no-cache", action="store_true", help="忽略缓存强制重评（仍会写回缓存）")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--from-vectors", action="store_true",
                    help="Stage C：读 jd_vectors 源文件的 evidence_map 跨 JD 证据去重定级，回填 skill_vec_prof")
    ap.add_argument("--marker-gated", action="store_true",
                    help="（--from-vectors 时）无梯度词证据 → 确定性 U 免 LLM（~35% 对免调用）")
    ap.add_argument("--no-soft-gate", dest="soft_gate", action="store_false", default=None,
                    help="（--from-vectors 时）关闭软技能无梯度词门控（默认开：软技能 U 免 LLM）")
    args = ap.parse_args()

    if args.from_vectors:
        window = args.window
        if not window:
            files = sorted(glob.glob(os.path.join(JD_DERIVED_DIR, "*.jd_vectors.jsonl")))
            if not files:
                raise SystemExit("data/timeline/jd_derived/ 下无 jd_vectors 源文件（先运行 Stage B run_jd_extract.py）")
            window = os.path.basename(files[-1]).rsplit(".jd_vectors.jsonl", 1)[0]
        run_from_vectors(window, marker_gated=args.marker_gated, no_cache=args.no_cache,
                         soft_gate=args.soft_gate)
        return

    window = args.window
    if not window:
        files = sorted(glob.glob(os.path.join(JD_DIR, "*.csv")))
        if not files:
            raise SystemExit("data/timeline/jd/ 下无月度 CSV（先运行 timeline 编排）")
        window = os.path.splitext(os.path.basename(files[-1]))[0]
    csv_path = os.path.join(JD_DIR, f"{window}.csv")
    if not os.path.exists(csv_path):
        raise SystemExit(f"JD 月度文件不存在: {csv_path}")

    rows, stats = sample_jds(csv_path, max(args.n, 0), args.per_job)
    print(f"[prof] {window}：扫描 {stats['n_scanned']} 行 → 抽样 {len(rows)} 条 / "
          f"{stats['n_jobs']} 岗位（非IT过滤 {stats['n_unmatched']}）")

    ev = JDProficiencyEvaluator(use_cache=False if args.no_cache else None,
                                api_key=args.api_key or None)
    records = []
    for i, r in enumerate(rows, 1):
        profile = {"title": r["title"], "funtype": r["funtype"],
                   "work_year": r["work_year"], "seniority": r["level"]}
        try:
            rec = ev.evaluate_jd(r["text"], profile)
            rec["_row"] = {"jobid": r["jobid"], "title": r["title"],
                           "job_code": r["job_code"]}
            records.append(rec)
        except Exception as e:                      # 单条失败不中断批次
            print(f"[prof] JD {r['jobid']} 评估失败：{e}")
        if i % 10 == 0 or i == len(rows):
            print(f"[prof] 进度 {i}/{len(rows)}（调用 {ev.n_calls}，缓存命中 {ev.n_cache_hits}）")

    agg = aggregate_proficiency(records)
    ev_stats = {"n_calls": ev.n_calls, "n_cache_hits": ev.n_cache_hits,
                "n_retries": ev.n_retries, "n_invalid": ev.n_invalid,
                "rubric_version": __import__("jd_proficiency_prompts").RUBRIC_VERSION,
                "model": config.DEFAULT_MODEL}
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, f"jd_prof_results_{window}.json"), "w", encoding="utf-8") as f:
        json.dump({"window": window, "stats": stats, "evaluator": ev_stats,
                   "rows": [{"jobid": r["jobid"], "title": r["title"],
                             "job_code": r["job_code"]} for r in rows],
                   "records": records, "aggregate": agg},
                  f, ensure_ascii=False, indent=1)

    report = build_report(window, records, agg, stats, ev_stats,
                          detail_rows=args.detail)
    out_md = os.path.join(OUT_DIR, "jd_prof_calibration.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n{report}\n\n明细: {OUT_DIR}{os.sep}jd_prof_results_{window}.json\n报告: {out_md}")


if __name__ == "__main__":
    main()
