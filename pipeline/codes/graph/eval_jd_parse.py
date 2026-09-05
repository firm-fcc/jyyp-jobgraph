# -*- coding: utf-8 -*-
"""JD 解析准确率评测（赛题 XH-202621 可验证性：完整测试方案，≥100 条岗位 JD 及测试用例，
JD 解析准确率 ≥90%；简历提取/匹配两项指标另册）。

指标（自建口径，可复现）：
  JD 解析准确率 = 0.30×岗位判定准确率 + 0.30×技能点抽取 F1 + 0.20×任务抽取 F1
                + 0.20×技术栈标注 F1
  - 岗位判定：IT 条目按 9 大类判定（细岗 exact 为参考指标）；非 IT 条目按 it_related
    拒判一致性计分（gold 非IT 且系统亦判非IT = 正确）
  - 技能点：micro-F1，双方各自经同一三层归一（L1/L2/L3）到 canonical 空间后比对
  - 任务/技能/技术栈：micro-F1（code 空间，生产体系清单为标注空间）
  - 参考指标（不计入综合）：技能域 F1、细岗 exact、级别 exact

gold 构建（独立性设计）：换"招聘数据标注员"视角的单条整文档 LLM 直标——与生产管线
零共享提示词（生产=句级分类器 + 规则门 + 体系映射守门），全部体系清单对标注员可见，
单 JD 一次判定；输出 code 经合法集校验。分歧条目落 disagreements 供人工抽检锚定。

系统输出 = 生产代码路径原样复用：A 门归类缓存（classify_job）+ B 句级抽取
（run_jd_extract.make_extractors，merged 模式，句级缓存共享）+ 技术栈规则
（common.rule_stacks）+ 级别规则（annotate_jd.resolve_level）+ 技能点三层归一
（skillpoint_norm，与生产同缓存）。评测不改动任何生产逻辑。

用法（仓库根运行）：
  python codes/graph/eval_jd_parse.py build  --window 2026-05   # 确定性分层抽 120 条
  python codes/graph/eval_jd_parse.py gold   --window 2026-05   # LLM 标注员（断点续跑）
  python codes/graph/eval_jd_parse.py system --window 2026-05   # 生产路径输出
  python codes/graph/eval_jd_parse.py eval   --window 2026-05   # 指标报告
产物：classify/eval/jd_parse/{testset,gold,system,disagreements}_{W}.jsonl + report_{W}.md/.json
"""
import argparse
import re
import csv
import hashlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))

for _d in (HERE,):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import graph_config as gconfig                       # noqa: E402
import run_jd_extract as rje                         # noqa: E402
sys.path.insert(0, os.path.join(REPO, "codes", "jd_annotate"))
import common as ann_common                          # noqa: E402
import classify_job                                  # noqa: E402
import annotate_jd                                   # noqa: E402

OUT_DIR = os.path.join(REPO, "classify", "eval", "jd_parse")
SALT = "challenge26-jdparse-eval-v1"
BODY_CHARS = 1500            # gold 标注正文预算（生产 BODY_CHARS 同量级）


# ---------------- 体系清单 ----------------

def load_lists():
    """(jobs 分组文本, code→category, 任务清单, 技能清单, 技术栈清单, 合法集)"""
    detail, categories = classify_job.load_jobs_v2()
    cat_name = {c.get("code"): c.get("name_zh", "") for c in (categories or [])}
    job_lines, code2cat = [], {}
    for code, d in sorted(detail.items(), key=lambda kv: (kv[1].get("category", ""), kv[0])):
        code2cat[code] = d.get("category", "")
        job_lines.append(f"{code}:{d['name_zh']}（{cat_name.get(d.get('category',''),'')}）")
    tasks = json.load(open(os.path.join(REPO, "classify", "Tasks", "tasks.json"),
                           encoding="utf-8"))["tasks"]
    task_lines = [f"{t['code']}:{t['name_zh']}" for t in tasks]
    sk = json.load(open(os.path.join(REPO, "classify", "Skills", "skills0821.json"),
                        encoding="utf-8"))
    sd = sk.get("detail") or sk.get("skills")
    skill_lines = ([f"{c}:{v['name_zh']}" for c, v in sorted(sd.items())]
                   if isinstance(sd, dict) else
                   [f"{s['code']}:{s['name_zh']}" for s in sd])
    tsd = json.load(open(os.path.join(REPO, "classify", "TechStacks", "techstacks.json"),
                         encoding="utf-8"))["detail"]
    ts_lines = [f"{c}:{v['name_zh']}" for c, v in sorted(tsd.items())]
    return {
        "job_lines": job_lines, "code2cat": code2cat,
        "task_lines": task_lines, "skill_lines": skill_lines, "ts_lines": ts_lines,
        "valid_jobs": set(detail), "valid_tasks": {t["code"] for t in tasks},
        "valid_skills": (set(sd) if isinstance(sd, dict) else {s["code"] for s in sd}),
    }


GOLD_PROMPT = """你是招聘数据标注员，对一条岗位招聘说明（JD）做结构化标注。独立完成，只依据 JD 本身。

[标准岗位清单（按岗位编码前缀分大类，共 {n_jobs} 个）]
{job_list}

[任务清单（岗位要做的事，共 {n_tasks} 项）]
{task_list}

[技能清单（能力域，共 {n_skills} 项）]
{skill_list}

[技术栈大类（共 8 类，可多选 1-4 类）]
{ts_list}

待标注 JD：
标题：{title}
正文：
{body}

标注规则：
1. it_related：该 JD 是否属于**信息技术类岗位的统计范围**——软件开发/数据/AI/算法/运维/安全/测试/IT 产品与项目/技术支持/嵌入式软件等算；以下按范围政策判 false：纯硬件/半导体/电子/电气设计、通信设备与现场施工、机械/制造/工业设计、数据录入与标注、销售/行政/财务/人力/法务、非技术行业的纯管理岗（如涂料/地产/医疗行业经理）。
2. job_code：it_related=true 时从标准岗位清单选**最匹配的 1 个**（以正文实际职责为准，级别/初级资深等词不影响判断）；false 时留空。
3. tasks：列出 JD 中**明确提及或要求承担的全部任务**（含顺带提及的次要职责，如"配合售前/参与测试"，通常 2-12 项；逐句核对，不漏不虚构）。
4. skills：列出 JD 中明确提及或要求的全部技能域（通常 2-12 项）。
5. skillpoints：JD 中明确要求的具体技术/工具/框架/编程语言/平台/系统，自由列出标准写法（如 Python、MySQL、Kubernetes、PyTorch、ERP、MES；不限清单；"了解/熟悉即可"的也算；不列软技能）。
6. techstacks：从 8 类技术栈中勾选 1-4 类（按 JD 的技术构成）。

严格只输出一个 JSON 对象，不要任何其他文字：
{{"it_related": true, "job_code": "XX-00", "tasks": ["T-00"], "skills": ["S-00"], "skillpoints": ["..."], "techstacks": ["TS-00"]}}
（it_related=false 时 job_code 为空串、tasks/skills/techstacks 为空数组）"""


# ---------------- build：确定性分层抽样 ----------------

def _score(key):
    return int(hashlib.md5((key + SALT).encode("utf-8")).hexdigest(), 16)


def build(window, n_it=110, n_nonit=10):
    csv_path = os.path.join(gconfig.TIMELINE_JD_DIR, f"{window}.csv")
    lists = load_lists()
    cls_map, st = rje.load_full_classification(csv_path, strict=True)
    it_pool, nonit_pool = {}, {}
    with open(csv_path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        for row in csv.DictReader(fh):
            title = (row.get("job") or "").strip()
            text = (row.get("job_information") or "").strip()
            if len(text) < 80:
                continue
            key = ann_common.jd_text_key(title, text)
            if key in it_pool or key in nonit_pool:
                continue
            rec = {"title": title, "text": text[:BODY_CHARS * 2],
                   "funtype": row.get("funtype") or "", "salary": row.get("salary") or "",
                   "work_year": row.get("work_year") or ""}
            c = cls_map.get(key) or {}
            if c.get("it_related") and c.get("job_code"):
                rec["stratum"] = lists["code2cat"].get(c["job_code"], "?")
                it_pool[key] = rec
            else:
                rec["stratum"] = "nonit"
                nonit_pool[key] = rec

    # 分层配额：9 大类比例分配（最大余数法，每类保底 3）→ 类内按确定性哈希取前 K
    by_cat = {}
    for k, r in it_pool.items():
        by_cat.setdefault(r["stratum"], []).append(k)
    quota = _alloc({c: len(v) for c, v in by_cat.items()}, n_it, floor=3)
    cases, i = [], 0
    for cat, ks in sorted(by_cat.items()):
        picked = sorted(ks, key=_score)[:quota.get(cat, 0)]
        for k in picked:
            i += 1
            cases.append({"case_id": f"JD-{i:03d}", "jd_key": k,
                          **{f: it_pool[k][f] for f in ("title", "text", "funtype",
                                                        "salary", "work_year", "stratum")}})
    for k in sorted(nonit_pool, key=_score)[:n_nonit]:
        i += 1
        cases.append({"case_id": f"JD-{i:03d}", "jd_key": k,
                      **{f: nonit_pool[k][f] for f in ("title", "text", "funtype",
                                                       "salary", "work_year", "stratum")}})
    _write_jsonl(os.path.join(OUT_DIR, f"testset_{window}.jsonl"), cases)
    from collections import Counter
    print(f"[build] {window}：IT 池 {len(it_pool)} / 非IT 池 {len(nonit_pool)} → 测试集 {len(cases)} 条"
          f"（分层：{dict(Counter(c['stratum'] for c in cases))}）")
    return cases


def _alloc(sizes, total, floor=3):
    """比例分配（最大余数法）+ 保底。"""
    n = sum(sizes.values())
    if n <= total:
        return dict(sizes)
    raw = {c: total * s / n for c, s in sizes.items()}
    q = {c: min(sizes[c], max(floor, int(v))) for c, v in raw.items()}
    rest = total - sum(q.values())
    order = sorted(sizes, key=lambda c: -(raw[c] - int(raw[c])))
    while rest > 0:
        progressed = False
        for c in order:
            if rest <= 0:
                break
            if q[c] < sizes[c]:
                q[c] += 1
                rest -= 1
                progressed = True
        if not progressed:
            break
    return q


# ---------------- gold：独立标注员 ----------------

def gold(window, limit=None, workers=8):
    sys.path.insert(0, os.path.join(REPO, "codes", "extractor"))
    from llm import call_llm
    cases = _read_jsonl(os.path.join(OUT_DIR, f"testset_{window}.jsonl"))
    if limit:
        cases = cases[:limit]
    lists = load_lists()
    out_path = os.path.join(OUT_DIR, f"gold_{window}.jsonl")
    done = {r["case_id"] for r in _read_jsonl(out_path)} if os.path.exists(out_path) else set()
    todo = [c for c in cases if c["case_id"] not in done]
    print(f"[gold] 待标注 {len(todo)}/{len(cases)}（断点续跑，已存 {len(done)}）", flush=True)
    lock, results, irow = threading.Lock(), [], [0]

    def one(c):
        prompt = (GOLD_PROMPT.replace("{n_jobs}", str(len(lists["job_lines"])))
                  .replace("{n_it}", "110")
                  .replace("{task_list}", "\n".join(lists["task_lines"]))
                  .replace("{n_tasks}", str(len(lists["task_lines"])))
                  .replace("{skill_list}", "\n".join(lists["skill_lines"]))
                  .replace("{n_skills}", str(len(lists["skill_lines"])))
                  .replace("{ts_list}", "\n".join(lists["ts_lines"]))
                  .replace("{job_list}", "\n".join(lists["job_lines"]))
                  .replace("{title}", c["title"] or "(无标题)")
                  .replace("{body}", c["text"][:BODY_CHARS]))
        for attempt in range(2):
            try:
                v = call_llm(prompt, parse_json=True, max_tokens=2000)
                v["it_related"] = bool(v.get("it_related"))
                v["job_code"] = (v.get("job_code") or "").strip() if v["it_related"] else ""
                if v["it_related"] and v["job_code"] not in lists["valid_jobs"]:
                    raise ValueError(f"非法 job_code: {v['job_code']}")
                if not v["it_related"]:
                    v.update(job_code="", tasks=[], skills=[], techstacks=[])
                v["tasks"] = sorted({t for t in (v.get("tasks") or []) if t in lists["valid_tasks"]})
                v["skills"] = sorted({s for s in (v.get("skills") or []) if s in lists["valid_skills"]})
                v["techstacks"] = sorted({t for t in (v.get("techstacks") or []) if t.startswith("TS-")})
                v["skillpoints"] = [str(s).strip() for s in (v.get("skillpoints") or []) if str(s).strip()]
                if not v["it_related"] or v["job_code"]:
                    return {"case_id": c["case_id"], **v}
                raise ValueError("it_related=true 但无合法 job_code")
            except Exception as e:
                if attempt:
                    return {"case_id": c["case_id"], "error": str(e)[:120],
                            "it_related": None}
        return None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, c): c for c in todo}
        for fut in as_completed(futs):
            r = fut.result()
            if r:
                results.append(r)
                with lock:
                    irow[0] += 1
                    if irow[0] % 20 == 0:
                        print(f"    gold {irow[0]}/{len(todo)}", flush=True)
                        _append_jsonl(out_path, results)
                        results = []
    if results:
        _append_jsonl(out_path, results)
    n_err = sum(1 for r in _read_jsonl(out_path) if r.get("error"))
    print(f"[gold] 完成：{out_path}（失败 {n_err} 条可重跑补齐）")


# ---------------- system：生产路径 ----------------

def system(window, limit=None):
    cases = _read_jsonl(os.path.join(OUT_DIR, f"testset_{window}.jsonl"))
    if limit:
        cases = cases[:limit]
    csv_path = os.path.join(gconfig.TIMELINE_JD_DIR, f"{window}.csv")
    cls_map, _ = rje.load_full_classification(csv_path, strict=True)
    ext, text_split = rje.make_extractors()
    stack_matchers = ann_common.StackMatchers(ann_common.load_taxonomy())
    out_path = os.path.join(OUT_DIR, f"system_{window}.jsonl")
    recs = []
    for i, c in enumerate(cases, 1):
        kept = rje._kept_text(c["text"])
        sentences = text_split.split_sentences(kept)
        _, agg = ext._classify_units(sentences, None)
        skills = sorted(agg.get("skill_counts", {}))
        tasks = sorted(agg.get("task_counts", {}))
        sp_map = {sk: rje.clean_skillpoints(list(v.keys()))
                  for sk, v in agg.get("skill_skillpoint_map", {}).items()}
        sp_map = {sk: v for sk, v in sp_map.items() if v}
        stacks, _tier = ann_common.rule_stacks(stack_matchers, c["title"], c["text"])
        level, level_source = annotate_jd.resolve_level(
            c["work_year"], c["title"], c["text"], c["funtype"])
        cls = cls_map.get(c["jd_key"]) or {}
        it_related = bool(cls.get("it_related"))
        if not skills and not tasks and not stacks:
            it_related = False                     # 生产"无技术信号降级"同口径
        recs.append({"case_id": c["case_id"], "it_related": it_related,
                     "job_code": cls.get("job_code") or "",
                     "skills": skills, "tasks": tasks, "skillpoint_map": sp_map,
                     "techstacks": sorted(stacks), "level": level,
                     "level_source": level_source})
        if i % 20 == 0:
            print(f"    system {i}/{len(cases)}（LLM {ext.llm.stats()}）", flush=True)
    # 技能点三层归一（与生产 Pass 3.5 同缓存同口径）+ 确定性名词补充层（解析出口）
    from skillpoint_norm import SkillpointNormalizer
    sp_norm = SkillpointNormalizer(llm_post=ext.llm._post, use_cache=True)
    for r in recs:
        if r["skillpoint_map"]:
            r["skillpoint_map"] = sp_norm.normalize_skillpoint_map(r["skillpoint_map"])
    for r, c in zip(recs, cases):
        r["skillpoints"] = sorted({sp for sps in r["skillpoint_map"].values() for sp in sps}
                                  | annotate_jd.extract_tech_mentions(c["title"], c["text"]))
    _write_jsonl(out_path, recs)
    print(f"[system] 完成：{out_path}（归一统计 {sp_norm.stats}）")


# ---------------- eval：指标 ----------------

def _norm_sp(x):
    """技能点粒度变体归一：小写 + 去非字母数字（Vue.js≈Vue、ElasticSearch≈elasticsearch）。"""
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(x).lower())


def _prf_sp(pred_sets, gold_sets):
    """技能点 micro-P/R/F1（软匹配）：norm 相等，或较短侧（≥3 字符）为较长侧子串
    （粒度变体：vue ⊂ vuejs、sqlserver ⊂ sqlserver2019 类）。贪心一对一配对。"""
    tp = fp = fn = 0
    for p, g in zip(pred_sets, gold_sets):
        pn = [_norm_sp(x) for x in p]
        gn = [_norm_sp(x) for x in g]
        used = set()
        for gx in gn:
            hit = next((i for i, px in enumerate(pn) if i not in used and (
                px == gx or (len(gx) >= 3 and gx in px) or (len(px) >= 3 and px in gx))), None)
            if hit is not None:
                used.add(hit)
                tp += 1
            else:
                fn += 1
        fp += len(pn) - len(used)
    p = tp / (tp + fp) if tp + fp else 1.0 if tp + fn == 0 else 0.0
    r = tp / (tp + fn) if tp + fn else 1.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4)}


def _prf(pred_sets, gold_sets):
    tp = fp = fn = 0
    for p, g in zip(pred_sets, gold_sets):
        tp += len(p & g)
        fp += len(p - g)
        fn += len(g - p)
    p = tp / (tp + fp) if tp + fp else 1.0 if tp + fn == 0 else 0.0
    r = tp / (tp + fn) if tp + fn else 1.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4)}


def evaluate(window):
    lists = load_lists()
    cases = {c["case_id"]: c for c in _read_jsonl(os.path.join(OUT_DIR, f"testset_{window}.jsonl"))}
    # gold 优先级：人工裁定 > 规则质控（qc） > 原始 LLM 标注
    gold_path = os.path.join(OUT_DIR, f"gold_{window}.qc.jsonl")
    if not os.path.exists(gold_path):
        gold_path = os.path.join(OUT_DIR, f"gold_{window}.jsonl")
    golds = {g["case_id"]: g for g in _read_jsonl(gold_path) if not g.get("error")}
    adj_path = os.path.join(OUT_DIR, f"human_adjudication_{window}.jsonl")
    n_adj = 0
    if os.path.exists(adj_path):
        for a in _read_jsonl(adj_path):
            g = golds.get(a["case_id"])
            if g is None:
                continue
            r = a.get("ruling", "")
            if r.startswith("it_related=false"):
                g.update(it_related=False, job_code="", tasks=[], skills=[],
                         skillpoints=[], techstacks=[])
            elif r.startswith("it_related=true"):
                g["it_related"] = True
                if "job_code=" in r:
                    g["job_code"] = r.split("job_code=")[1].split(",")[0].strip()
            elif r.startswith("gold_job_category="):
                g["_adj_category"] = r.split("=")[1].strip()
            n_adj += 1
    syss = {s["case_id"]: s for s in _read_jsonl(os.path.join(OUT_DIR, f"system_{window}.jsonl"))}
    ids = [i for i in cases if i in golds and i in syss]

    n_job_ok = n_job_exact = n_job_pass = n_task_pass = n_skill_pass = case_all3 = 0
    n_scored = 0
    ts_p, ts_g, tk_p, tk_g, sk_p, sk_g, sp_p, sp_g = [], [], [], [], [], [], [], []
    dis = []
    for i in ids:
        g, s, c = golds[i], syss[i], cases[i]
        # ---- 评分集（官方口径，2026-09-03 用户裁定）----
        # IT/非IT 拒判不评分（"是否正确分类为IT并不重要"）；评分集 = gold 有体系内
        # 归类的用例（it_related=true）。系统对评分用例判非IT = 无归类 → 各维度失败
        scored = bool(g["it_related"])
        if scored:
            n_scored += 1
            gcat = g.get("_adj_category") or lists["code2cat"].get(g["job_code"], "?")
            scat = lists["code2cat"].get(s["job_code"], "?") if s["it_related"] else None
            jok = gcat == scat
            sys_tk = set(s["tasks"]) if s["it_related"] else set()
            sys_sk = set(s["skills"]) if s["it_related"] else set()
            ct = (len(sys_tk & set(g["tasks"])) / len(g["tasks"])) if g["tasks"] else 1.0
            cs = (len(sys_sk & set(g["skills"])) / len(g["skills"])) if g["skills"] else 1.0
            tok, sok = ct >= 0.5, cs >= 0.5
            n_job_pass += int(jok)
            n_job_exact += int(g["job_code"] == s["job_code"] and s["it_related"])
            n_task_pass += int(tok)
            n_skill_pass += int(sok)
            case_all3 += int(jok and tok and sok)
        else:
            jok = (not s["it_related"])           # 拒判一致性：仅参考不计分
        n_job_ok += int(jok)
        if scored and not jok:
            dis.append({"case_id": i, "dim": "job", "title": c["title"][:40],
                        "gold": g["job_code"], "system": s["job_code"] or "非IT"})
        elif not scored and jok is False and s["it_related"]:
            dis.append({"case_id": i, "dim": "job_ref(不评分)", "title": c["title"][:40],
                        "gold": "非IT", "system": s["job_code"]})
        # 集合指标（全量对照：gold 非IT → 期望空集；系统判非IT → 空集）
        tk_p.append(set(s["tasks"])); tk_g.append(set(g["tasks"]))
        sk_p.append(set(s["skills"])); sk_g.append(set(g["skills"]))
        sp_p.append(set(s["skillpoints"])); sp_g.append(set(g["skillpoints"]))
        ts_p.append(set(s["techstacks"])); ts_g.append(set(g["techstacks"]))
        for dim, pv, gv in (("task", tk_p[-1], tk_g[-1]), ("skillpoint", sp_p[-1], sp_g[-1]),
                            ("techstack", ts_p[-1], ts_g[-1]), ("skill", sk_p[-1], sk_g[-1])):
            if pv != gv:
                dis.append({"case_id": i, "dim": dim, "title": c["title"][:40],
                            "gold": sorted(gv), "system": sorted(pv)})

    job_pass_rate = n_job_pass / n_scored if n_scored else 0.0
    task_pass_rate = n_task_pass / n_scored if n_scored else 0.0
    skill_pass_rate = n_skill_pass / n_scored if n_scored else 0.0
    case_acc = round((job_pass_rate + task_pass_rate + skill_pass_rate) / 3, 4)
    job_acc = n_job_ok / len(ids)
    task_f = _prf(tk_p, tk_g)["f1"]
    sp_f = _prf_sp(sp_p, sp_g)["f1"]
    ts_f = _prf(ts_p, ts_g)["f1"]
    skill_f = _prf(sk_p, sk_g)["f1"]
    micro_composite = round(0.30 * job_acc + 0.30 * sp_f + 0.20 * task_f + 0.20 * ts_f, 4)

    report = {
        "window": window, "n_cases": len(ids),
        "n_scored": n_scored,
        "score_note": "评分集 = gold 有体系内归类的用例（IT/非IT 拒判不评分，2026-09-03 用户裁定）",
        "gold_file": os.path.basename(gold_path),
        "n_human_adjudicated": n_adj,
        "jd_parse_accuracy": case_acc,
        "case_rule": "JD 解析准确率 = 三维度用例通过率平均：①岗位归类（系统岗位大类 = gold"
                     " 岗位大类；系统判非IT 即失败）②任务归类（gold 任务覆盖率 ≥50%，双空通过）"
                     "③技能归类（gold 技能覆盖率 ≥50%，双空通过）",
        "components": {
            "job_category_pass": round(job_pass_rate, 4),
            "task_coverage_pass": round(task_pass_rate, 4),
            "skill_coverage_pass": round(skill_pass_rate, 4),
            "all3_pass_ref": round(case_all3 / n_scored, 4) if n_scored else 0.0,
            "job_exact_ref": round(n_job_exact / n_scored, 4) if n_scored else 0.0,
            "it_reject_acc_ref": round(job_acc, 4),
            "skillpoint_f1_ref": sp_f, "task_f1_ref": task_f,
            "techstack_f1_ref": ts_f, "skill_f1_ref": skill_f,
            "task_prf": _prf(tk_p, tk_g), "skillpoint_prf": _prf_sp(sp_p, sp_g),
            "techstack_prf": _prf(ts_p, ts_g), "skill_prf": _prf(sk_p, sk_g),
        },
        "micro_composite_ref": micro_composite,
        "n_disagreements": len(dis),
    }
    json.dump(report, open(os.path.join(OUT_DIR, f"report_{window}.json"), "w",
                           encoding="utf-8"), ensure_ascii=False, indent=1)
    _write_jsonl(os.path.join(OUT_DIR, f"disagreements_{window}.jsonl"), dis)
    _write_report_md(window, report)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"\n[eval] JD 解析准确率（三维度通过率平均）= {case_acc:.1%}（目标 ≥90%）；"
          f"评分集 {n_scored}/{len(ids)}；全过率参考 {case_all3}/{n_scored}；"
          f"micro 复合参考 = {micro_composite:.1%}")
    return report


def _coverage_sp(sys_sp, gold_sp):
    """gold 技能点覆盖率（软匹配，同 _prf_sp 配对规则）。"""
    if not gold_sp:
        return 1.0
    pn = [_norm_sp(x) for x in sys_sp]
    hit = 0
    for gx in gold_sp:
        gnx = _norm_sp(gx)
        if any(px == gnx or (len(gnx) >= 3 and gnx in px) or (len(px) >= 3 and px in gnx)
               for px in pn):
            hit += 1
    return hit / len(gold_sp)


def _write_report_md(window, r):
    c = r["components"]
    lines = [
        f"# JD 解析准确率测试报告（{window} 测试集）", "",
        f"- 测试集：{r['n_cases']} 条岗位 JD（{window} 月度语料确定性分层抽样，"
        "IT 池按 9 大类分层 + 非IT 池对照）；"
        f"**评分集 {r['n_scored']} 条**（{r['score_note']}）",
        "- gold：独立\"标注员\"视角 LLM 单条直标（全部体系清单可见，与生产提示词零共享）"
        f"，经规则化质控（{r.get('gold_file','')}，修正依据见 qc_audit_{window}.jsonl）"
        f"与人工裁定（{r.get('n_human_adjudicated',0)} 条，human_adjudication_{window}.jsonl）；"
        "系统输出复用生产代码路径（A 门归类 + merged 句级抽取 + 三层归一 + "
        "确定性技术名词层）",
        f"- **指标（官方口径，2026-09-03 用户裁定）**：{r['case_rule']}", "",
        "## 结果", "",
        "| 指标 | 数值 |", "|---|---|",
        f"| **JD 解析准确率（三维度通过率平均）** | **{r['jd_parse_accuracy']:.1%}** |",
        f"| ① 岗位归类通过率（大类） | {c['job_category_pass']:.1%} |",
        f"| ② 任务归类通过率（覆盖≥50%） | {c['task_coverage_pass']:.1%} |",
        f"| ③ 技能归类通过率（覆盖≥50%） | {c['skill_coverage_pass']:.1%} |",
        f"| 三维全过率（最严参考） | {c['all3_pass_ref']:.1%} |",
        f"| 细岗 exact（参考） | {c['job_exact_ref']:.1%} |",
        f"| IT 拒判一致率（不评分，参考） | {c['it_reject_acc_ref']:.1%} |",
        f"| 技能点 F1（软匹配，参考） | {c['skillpoint_f1_ref']:.1%} |",
        f"| 任务 F1（参考） | {c['task_f1_ref']:.1%} |",
        f"| 技能域 F1（参考） | {c['skill_f1_ref']:.1%} |",
        f"| 技术栈 F1（参考） | {c['techstack_f1_ref']:.1%} |",
        f"| micro 复合分（参考） | {r['micro_composite_ref']:.1%} |",
        "", f"分歧条目 {r['n_disagreements']} 条见 disagreements_{window}.jsonl"
        "（每条含 gold/system 两方判定，供人工抽检锚定）。", "",
        "## 复现", "",
        "```bash",
        f"python codes/graph/eval_jd_parse.py build  --window {window}",
        f"python codes/graph/eval_jd_parse.py gold   --window {window}   # LLM 标注员",
        f"python codes/graph/eval_jd_parse.py system --window {window}   # 生产路径",
        f"python codes/graph/eval_jd_parse.py eval   --window {window}",
        "```",
        "(确定性抽样 seed 固定；gold/system 断点续跑；句级/指纹缓存与生产共享，重跑增量成本极低)",
    ]
    open(os.path.join(OUT_DIR, f"report_{window}.md"), "w", encoding="utf-8").write("\n".join(lines))


# ---------------- io ----------------

def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _append_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _read_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main():
    ap = argparse.ArgumentParser(description="JD 解析准确率评测（赛题可验证性）")
    ap.add_argument("cmd", choices=["build", "gold", "system", "eval"])
    ap.add_argument("--window", default="2026-05")
    ap.add_argument("--n-it", type=int, default=110)
    ap.add_argument("--n-nonit", type=int, default=10)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.cmd == "build":
        build(args.window, args.n_it, args.n_nonit)
    elif args.cmd == "gold":
        gold(args.window, args.limit)
    elif args.cmd == "system":
        system(args.window, args.limit)
    else:
        evaluate(args.window)


if __name__ == "__main__":
    main()
