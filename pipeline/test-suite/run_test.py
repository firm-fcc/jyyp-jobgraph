# -*- coding: utf-8 -*-
"""JD 解析测试套件 · 测试代码（模拟"下一窗口正常运行"并对照 GT 报告准确率）。

过程 = 生产代码路径原样运行（不复制逻辑，只换数据源）：
  1. Stage A 岗位归类：classify_job.merged_classification 对 jd_corpus.csv 在线归类
     （规则快路 + LLM 兜底走生产指纹缓存，与正式窗口同引擎同提示词）；
  2. Stage B 句级抽取：run_jd_extract.make_extractors（merged 一句一次出技能+技能点+
     任务，句级缓存与生产共享）+ 技术栈规则 + 确定性技术名词层；
  3. 对照 ground_truth.jsonl，按官方口径（2026-09-03 用户裁定）报告：
     JD 解析准确率 = 岗位归类 / 任务归类 / 技能归类 三维度用例通过率平均
     （评分集 = GT 有体系内归类的用例；IT/非IT 拒判不评分）。
  叠层参与（prev_window/overlay_participants.json）只影响叠层实体确证通道的独立
  overlays 输出，不改变基线体系 code 空间的岗位/任务/技能判定，故不注入评测路径。

用法：python test-suite/run_test.py           # 仓库根运行；exit 0 = ≥90% 达标
成本：首跑约 70 次 A 兜底 + 数百次句级调用（≈5-10 元）；生产缓存命中后近乎零成本。
"""
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "codes", "graph"))
sys.path.insert(0, os.path.join(REPO, "codes", "jd_annotate"))

import classify_job                                  # noqa: E402
import run_jd_extract as rje                         # noqa: E402
import common as ann_common                          # noqa: E402
import annotate_jd                                   # noqa: E402
from eval_jd_parse import _norm_sp                   # noqa: E402  粒度变体软匹配同源

CORPUS = os.path.join(HERE, "jd_corpus.csv")
GT = os.path.join(HERE, "ground_truth.jsonl")
RESULT = os.path.join(HERE, "results.json")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    gt = {json.loads(l)["jd_key"]: json.loads(l) for l in open(GT, encoding="utf-8")}
    print(f"[test] 载入 GT {len(gt)} 条；语料 {CORPUS}")

    # ---- Stage A：生产归类引擎（在线重算，指纹缓存共享）----
    print("[A] 岗位归类（classify_job 严格门，规则+LLM 兜底走生产缓存）...", flush=True)
    cls_raw, st = classify_job.merged_classification(CORPUS, None, strict=True)
    cls_map, _ = rje.load_full_classification(CORPUS, strict=True)
    print(f"[A] 行 {st['rows']} → 去重 {st['unique']}（送LLM {st['miss']}，"
          f"断点命中后增量运行）")

    # ---- Stage B：生产句级抽取 + 技术栈规则 + 确定性名词层 ----
    print("[B] 句级抽取（merged，生产句级缓存共享）...", flush=True)
    ext, text_split = rje.make_extractors()
    stack_matchers = ann_common.StackMatchers(ann_common.load_taxonomy())
    rows = []
    with open(CORPUS, encoding="utf-8-sig", errors="replace", newline="") as f:
        rd = csv.DictReader(f)
        for row in rd:
            key = ann_common.jd_text_key((row.get("job") or "").strip(),
                                         (row.get("job_information") or "").strip())
            if key in {r["jd_key"] for r in rows} or key not in gt:
                continue
            rows.append({"jd_key": key, "row": row,
                         "title": (row.get("job") or "").strip(),
                         "text": (row.get("job_information") or "").strip()})
    sys_out = {}
    for i, r in enumerate(rows, 1):
        cls = cls_map.get(r["jd_key"]) or {}
        kept = rje._kept_text(r["text"])
        sentences = text_split.split_sentences(kept)
        _, agg = ext._classify_units(sentences, None)
        skills = sorted(agg.get("skill_counts", {}))
        tasks = sorted(agg.get("task_counts", {}))
        stacks, _ = ann_common.rule_stacks(stack_matchers, r["title"], r["text"])
        it_related = bool(cls.get("it_related"))
        if not skills and not tasks and not stacks:
            it_related = False                        # 生产"无技术信号降级"同口径
        sys_out[r["jd_key"]] = {
            "it_related": it_related, "job_code": cls.get("job_code") or "",
            "skills": skills, "tasks": tasks, "techstacks": sorted(stacks),
            "skillpoints": sorted(annotate_jd.extract_tech_mentions(
                r["title"], r["text"]))}
        if i % 30 == 0:
            print(f"    B {i}/{len(rows)}（LLM {ext.llm.stats()}）", flush=True)

    # ---- 对照 GT：官方口径三维度（评分集 = GT 有体系内归类）----
    detail, _ = classify_job.load_jobs_v2()
    code2cat = {c: d.get("category", "") for c, d in detail.items()}
    scored = [k for k, g in gt.items() if g["it_related"]]
    n_job = n_tk = n_sk = 0
    fails = []
    for k in scored:
        g, s = gt[k], sys_out[k]
        gcat = g.get("job_category") or code2cat.get(g["job_code"], "?")
        scat = code2cat.get(s["job_code"], "?") if s["it_related"] else None
        jok = gcat == scat
        ct = (len(set(s["tasks"]) & set(g["tasks"])) / len(g["tasks"])) if g["tasks"] else 1.0
        cs = (len(set(s["skills"]) & set(g["skills"])) / len(g["skills"])) if g["skills"] else 1.0
        tok, sok = ct >= 0.5, cs >= 0.5
        n_job += jok
        n_tk += tok
        n_sk += sok
        if not (jok and tok and sok):
            fails.append({"case_id": g["case_id"], "title": g["title"][:30],
                          "job": f"{g['job_code']}→{s['job_code'] or '非IT'}",
                          "task_cov": round(ct, 2), "skill_cov": round(cs, 2)})
    job_r, tk_r, sk_r = n_job / len(scored), n_tk / len(scored), n_sk / len(scored)
    acc = (job_r + tk_r + sk_r) / 3

    rep = {"n_cases": len(gt), "n_scored": len(scored),
           "jd_parse_accuracy": round(acc, 4),
           "job_category_pass": round(job_r, 4),
           "task_coverage_pass": round(tk_r, 4),
           "skill_coverage_pass": round(sk_r, 4),
           "metric": "三维度用例通过率平均（岗位大类 / 任务覆盖≥50% / 技能覆盖≥50%；"
                     "评分集 = GT 有体系内归类的用例，IT 拒判不评分——2026-09-03 裁定）",
           "threshold": 0.90, "passed": acc >= 0.90,
           "fail_cases": fails}
    json.dump(rep, open(RESULT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n===== JD 解析测试报告 =====")
    print(f"评分集 {len(scored)}/{len(gt)}（IT 拒判不评分）")
    print(f"① 岗位归类（大类）  {n_job}/{len(scored)} = {job_r:.1%}")
    print(f"② 任务归类（覆盖≥50%）{n_tk}/{len(scored)} = {tk_r:.1%}")
    print(f"③ 技能归类（覆盖≥50%）{n_sk}/{len(scored)} = {sk_r:.1%}")
    print(f"JD 解析准确率（三维平均）= {acc:.1%}  目标 ≥90% → "
          + ("达标 ✓" if acc >= 0.90 else "未达标 ✗"))
    print(f"失败用例 {len(fails)} 条（明细见 results.json）")
    sys.exit(0 if acc >= 0.90 else 1)


if __name__ == "__main__":
    main()
