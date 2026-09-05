# -*- coding: utf-8 -*-
"""基图边计算（Graph Builder 简版）：JD 月度 CSV → G_base 四种边。

公式（docs/algorithm-design-v2.md §1，参数 settings.yaml → graph_base）：
- J-T / J-S:  base_weight = W(J,X) / W(J)           文档级 presence、薪资加权
- T-S:        ts_w1 · W(T,S)/W(T) + ts_w2 · I(explicit)   （显式关联项无数据源恒 0，预留）
- S-SP:       W(S,SP) / W(S)                        （多对多，分母按各 S 独立）
- 薪资加权:   weight(jd) = log(1 + salary_monthly / median)
- 跨窗口累积: freq_new + alpha · freq_historical（读上一窗口 base/freq.json；freq.json
  存"截至本窗口末"的累积值，下窗口整体乘一次 α，符合 §1 的链式衰减语义）

频次均为**加权文档数**（W）：一条 JD 以 presence 语义对每个出现的实体贡献一次 weight，
共现对（T,S）在同一文档内也只贡献一次。

输出写 `data/graph/{window}/base/`（非空边文件需 --force 覆盖，与快照约定一致）：
- job_task / job_skill / task_skill / skill_skillpoint.json（边，schema 同 snapshot_builder）
- skillpoints.json（基图技能点节点，回填发现的 SP）
- freq.json（累积加权频次，下窗口 α 累积链）
- entity_freq.json（实体文档频率 E_jd，供图谱合成 gap 计算）
- skill_prof.json（**技能熟练度要求分布**：extractor/jd_proficiency 逐 (JD×技能) 量规评估
  P1-P4/U 后按窗口聚合，供演化分析；评估器缺席则不写该文件）
- build_info.json（抽样/参数/统计记录）

岗位映射：JD.funtype 按 " or " 拆分 → jobs0806.json detail 的 funtypes/名称 → 岗位 code
（2026-05 实测覆盖率 100%；未命中行计数丢弃并打印）。
"""
import csv
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime

import graph_config as config
from snapshot_builder import parse_window, _edge_file  # noqa: F401  _edge_file 复用同一 schema

# ---------------- 薪资解析 ----------------
_NUM = r"(\d+(?:\.\d+)?)"
_RE_DAY = re.compile(rf"^{_NUM}(?:\s*-\s*{_NUM})?\s*元?\s*/\s*天")
_RE_YEAR = re.compile(rf"^{_NUM}(?:\s*-\s*{_NUM})?\s*(万|千)?\s*/\s*年")
_RE_RANGE = re.compile(rf"^{_NUM}\s*(万|千)?\s*-\s*{_NUM}\s*(万|千)?")
_RE_BELOW = re.compile(rf"^{_NUM}\s*(万|千)\s*及以下")
_RE_ABOVE = re.compile(rf"^{_NUM}\s*(万|千)\s*以上")
_RE_MONTHS = re.compile(r"[·・]\s*(\d+)\s*薪")
_UNIT = {"万": 10000.0, "千": 1000.0}
_DAYS_PER_MONTH = 22  # 日薪折月的工作日数


def parse_salary_monthly(s):
    """薪资字符串 → 月薪等价值（元）。面议/空/无法解析 → None。

    覆盖 2026-05 全量格式（按模式归纳）：
    "1.5-2万" / "8千-1.2万"（两侧单位独立，左侧缺省继承右侧）/ "6-8千"
    "·14薪"（年薪 N 个月 → 月值×N/12）/ "20-30万/年"（÷12）/ "200元/天"（×22）
    / "3千及以下" / "2万以上"（单值直接取）
    """
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    if not s or "面议" in s:
        return None

    def _to_yuan(v, unit):
        return float(v) * _UNIT.get(unit or "", 1.0)

    def _mid(lo, hi, u_lo, u_hi):
        u_lo = u_lo or u_hi  # 左侧无单位继承右侧（"5-7千"）
        return (_to_yuan(lo, u_lo) + _to_yuan(hi, u_hi)) / 2

    monthly = None
    m = _RE_DAY.match(s)
    if m:
        vals = [float(m.group(i)) for i in (1, 2) if m.group(i)]
        monthly = (sum(vals) / len(vals)) * _DAYS_PER_MONTH
    else:
        m = _RE_YEAR.match(s)
        if m:
            unit = m.group(3)  # 两侧同单位（"20-30万/年"）
            lo, hi = float(m.group(1)), float(m.group(2) or m.group(1))
            monthly = ((_to_yuan(lo, unit) + _to_yuan(hi, unit)) / 2) / 12
        else:
            m = _RE_RANGE.match(s)
            if m:
                monthly = _mid(m.group(1), m.group(3), m.group(2), m.group(4))
            else:
                m = _RE_BELOW.match(s) or _RE_ABOVE.match(s)
                if m:
                    monthly = _to_yuan(m.group(1), m.group(2))
    if monthly is None:
        return None
    m = _RE_MONTHS.search(s)
    if m:  # "·14薪"：年薪 N 个月发放 → 月薪等价 × N/12
        monthly *= int(m.group(1)) / 12
    return round(monthly, 2)


# ---------------- 岗位映射与抽样 ----------------
def load_funtype_map(jobs_json=None):
    """funtype 片段（含岗位名）→ 岗位 code。来自 jobs0806.json detail 的 funtypes 数组。"""
    path = jobs_json or config.BASE_NODE_FILES["jobs"]
    detail = json.load(open(path, encoding="utf-8")).get("detail", {})
    mapping = {}
    for code, nd in detail.items():
        if not isinstance(nd, dict):
            continue
        mapping[nd.get("name_zh", "")] = code
        for f in nd.get("funtypes", []) or []:
            mapping[f] = code
    mapping.pop("", None)
    return mapping


def match_job_code(funtype, mapping):
    """复合 funtype（"A or B or C"）按片段依次匹配，返回首个命中的岗位 code。"""
    for part in re.split(r"\s+or\s+", funtype or ""):
        code = mapping.get(part.strip())
        if code:
            return code
    return None


def sample_jds(csv_path, mapping, sample_total=None, per_job=None, min_text_chars=None):
    """流式扫描月度 CSV，按岗位 code 分层抽样。返回 (rows, scan_stats)。

    rows: [{job_code, funtype, salary, text}]；每岗位至多 per_job 条、总量至多 sample_total，
    文本短于 min_text_chars 或与已抽样完全重复（md5）的行跳过。
    """
    sample_total = sample_total if sample_total is not None else config.GB_SAMPLE_TOTAL
    per_job = per_job if per_job is not None else config.GB_PER_JOB
    min_text_chars = min_text_chars if min_text_chars is not None else config.GB_MIN_TEXT_CHARS
    rows, per_code, seen_text = [], {}, set()
    n_scanned = n_unmatched = n_short = n_dup = 0
    with open(csv_path, encoding="utf-8-sig", newline='') as fh:
        for row in csv.DictReader(fh):
            n_scanned += 1
            code = match_job_code(row.get("funtype", ""), mapping)
            if not code:
                n_unmatched += 1
                continue
            if per_code.get(code, 0) >= per_job:
                continue
            text = (row.get("job_information") or row.get("job") or "").strip()
            if len(text) < min_text_chars:
                n_short += 1
                continue
            key = hashlib.md5(text.encode("utf-8")).hexdigest()
            if key in seen_text:
                n_dup += 1
                continue
            seen_text.add(key)
            per_code[code] = per_code.get(code, 0) + 1
            rows.append({"job_code": code, "funtype": row.get("funtype", ""),
                         "salary": row.get("salary", ""), "text": text,
                         "title": row.get("job", ""), "work_year": row.get("work_year", "")})
            if len(rows) >= sample_total:
                break
    stats = {"n_scanned": n_scanned, "n_sampled": len(rows), "n_jobs": len(per_code),
             "n_unmatched": n_unmatched, "n_skip_short": n_short, "n_skip_dup": n_dup}
    return rows, stats


# ---------------- 抽取层（LLM，可注入 mock 供测试） ----------------
def make_extractors():
    """构造真实抽取器：{"merged": callable(text), "prof": evaluator}。

    merged 模式一句一次出 skill+task+skillpoint（替代 skill/task 两次分离调用，句级调用减半、
    不损穷举性）。跨模块约定（同 builder 各 run_*）：graph_config 已把 builder 版 config
    缓存进 sys.modules，而 extractor 子包的 `import config` 需命中 extractor 版（CACHE_DIR 等）——
    这里在导入期间临时换出 builder 版、导入完恢复，两边互不污染。
    "prof"（技能熟练度评估器）也必须在本窗口内 preload()：其惰性初始化若发生在
    换出后会绑定 builder 版 config（CACHE_DIR 缺失直接报错）。
    """
    ext_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "extractor"))
    if ext_dir not in sys.path:
        sys.path.insert(0, ext_dir)
    saved = sys.modules.pop("config", None)
    try:
        from extractor import Extractor
        from jd_proficiency import JDProficiencyEvaluator
        out = {}
        ext = Extractor(mode="merged")
        out["merged"] = lambda text, _ext=ext: _ext.extract(text)
        out["prof"] = JDProficiencyEvaluator().preload()
        return out
    finally:
        if saved is not None:
            sys.modules["config"] = saved
        else:
            sys.modules.pop("config", None)


def _node_name_maps():
    """code → name_zh（岗位/任务/技能），直接读体系 JSON，不依赖 extractor。"""
    jobs = json.load(open(config.BASE_NODE_FILES["jobs"], encoding="utf-8"))
    job_names = {c: nd.get("name_zh", "") for c, nd in jobs.get("detail", {}).items()}
    tasks = json.load(open(config.BASE_NODE_FILES["tasks"], encoding="utf-8"))
    task_names = {t["code"]: t.get("name_zh", "") for t in tasks.get("tasks", [])}
    skills = json.load(open(config.BASE_NODE_FILES["skills"], encoding="utf-8"))
    skill_names = {c: nd.get("name_zh", "") for c, nd in skills.get("detail", {}).items()}
    return job_names, task_names, skill_names


# ---------------- 频次聚合 ----------------
def _new_freq():
    return {"total": 0.0, "jobs": {}, "tasks": {}, "skills": {}, "skillpoints": {},
            "task_skill": {}, "skill_skillpoint": {}}


def _job_entry(freq, code):
    return freq["jobs"].setdefault(code, {"w": 0.0, "tasks": {}, "skills": {}})


def accumulate(freq, job_code, weight, task_set, skill_set, sp_map):
    """单条 JD 的加权 presence 累积。sp_map: {skill_code: set(技能点名)}。"""
    freq["total"] += weight
    je = _job_entry(freq, job_code)
    je["w"] += weight
    for t in task_set:
        je["tasks"][t] = je["tasks"].get(t, 0.0) + weight
        freq["tasks"][t] = freq["tasks"].get(t, 0.0) + weight
    for sk in skill_set:
        je["skills"][sk] = je["skills"].get(sk, 0.0) + weight
        freq["skills"][sk] = freq["skills"].get(sk, 0.0) + weight
        for sp in sp_map.get(sk, ()):  # S-SP 共现（同一文档一次）
            key = f"{sk}|{sp}"
            freq["skill_skillpoint"][key] = freq["skill_skillpoint"].get(key, 0.0) + weight
            freq["skillpoints"][sp] = freq["skillpoints"].get(sp, 0.0) + weight
    for t in task_set:  # T-S 共现（笛卡尔积，同一文档每对一次）
        for sk in skill_set:
            key = f"{t}|{sk}"
            freq["task_skill"][key] = freq["task_skill"].get(key, 0.0) + weight


def merge_history(new_freq, prev_freq, alpha):
    """freq = freq_new + alpha · freq_historical（prev_freq 为上一窗口末的累积值）。"""
    if not prev_freq:
        return new_freq
    out = _new_freq()

    def add(dst, key, val):
        dst[key] = dst.get(key, 0.0) + val

    a_old = alpha * prev_freq.get("total", 0.0)
    out["total"] = new_freq["total"] + a_old
    for t, w in prev_freq.get("tasks", {}).items():
        add(out["tasks"], t, alpha * w)
    for sk, w in prev_freq.get("skills", {}).items():
        add(out["skills"], sk, alpha * w)
    for sp, w in prev_freq.get("skillpoints", {}).items():
        add(out["skillpoints"], sp, alpha * w)
    for k, w in prev_freq.get("task_skill", {}).items():
        add(out["task_skill"], k, alpha * w)
    for k, w in prev_freq.get("skill_skillpoint", {}).items():
        add(out["skill_skillpoint"], k, alpha * w)
    for code, je in prev_freq.get("jobs", {}).items():
        dj = _job_entry(out, code)
        dj["w"] += alpha * je.get("w", 0.0)
        for t, w in je.get("tasks", {}).items():
            add(dj["tasks"], t, alpha * w)
        for sk, w in je.get("skills", {}).items():
            add(dj["skills"], sk, alpha * w)
    # 新频次叠加
    for t, w in new_freq["tasks"].items():
        add(out["tasks"], t, w)
    for sk, w in new_freq["skills"].items():
        add(out["skills"], sk, w)
    for sp, w in new_freq["skillpoints"].items():
        add(out["skillpoints"], sp, w)
    for k, w in new_freq["task_skill"].items():
        add(out["task_skill"], k, w)
    for k, w in new_freq["skill_skillpoint"].items():
        add(out["skill_skillpoint"], k, w)
    for code, je in new_freq["jobs"].items():
        dj = _job_entry(out, code)
        dj["w"] += je.get("w", 0.0)
        for t, w in je.get("tasks", {}).items():
            add(dj["tasks"], t, w)
        for sk, w in je.get("skills", {}).items():
            add(dj["skills"], sk, w)
    return out


# ---------------- 边生成 ----------------
def build_edges(freq, ts_w1=None, ts_w2=0.0):
    """从累积频次生成四种基图边。返回 {kind: [edge dict]}（按 src,dst 排序）。"""
    ts_w1 = ts_w1 if ts_w1 is not None else config.GB_TS_W1
    edges = {"job_task": [], "job_skill": [], "task_skill": [], "skill_skillpoint": []}
    for code, je in freq["jobs"].items():
        w_j = je.get("w", 0.0)
        if w_j <= 0:
            continue
        for t, w in je.get("tasks", {}).items():
            if w > 0:
                edges["job_task"].append({"src": code, "dst": t, "relation": "job_task",
                                          "weight": round(w / w_j, 4)})
        for sk, w in je.get("skills", {}).items():
            if w > 0:
                edges["job_skill"].append({"src": code, "dst": sk, "relation": "job_skill",
                                           "weight": round(w / w_j, 4)})
    for key, w in freq["task_skill"].items():
        t, sk = key.split("|", 1)
        w_t = freq["tasks"].get(t, 0.0)
        if w_t <= 0 or w <= 0:
            continue
        weight = ts_w1 * (w / w_t) + ts_w2 * 0  # 显式关联项无数据源，恒 0（预留）
        edges["task_skill"].append({"src": t, "dst": sk, "relation": "task_skill",
                                    "weight": round(weight, 4)})
    for key, w in freq["skill_skillpoint"].items():
        sk, sp = key.split("|", 1)
        w_s = freq["skills"].get(sk, 0.0)
        if w_s <= 0 or w <= 0:
            continue
        edges["skill_skillpoint"].append({"src": sk, "dst": sp, "relation": "skill_skillpoint",
                                          "weight": round(w / w_s, 4)})
    for kind in edges:
        edges[kind].sort(key=lambda e: (e["src"], e["dst"]))
    return edges


# ---------------- 主入口 ----------------
_EDGE_NAMES = {
    "job_task": "关系：岗位→任务（基图）",
    "job_skill": "关系：岗位→技能（基图）",
    "task_skill": "关系：任务→技能（基图）",
    "skill_skillpoint": "关系：技能→技能点（基图）",
}


def prev_window_label(window):
    """上一窗口标签（month → 上月；quarter → 上季）。"""
    kind, start, _, _ = parse_window(window)
    if kind == "month":
        y, m = start.year, start.month - 1
        if m == 0:
            y, m = y - 1, 12
        return f"{y:04d}-{m:02d}"
    y = start.year
    n = (start.month - 1) // 3 + 1  # 起始月 → 季度序号
    n -= 1
    if n == 0:
        y, n = y - 1, 4  # Q1 的上一季是去年 Q4
    return f"{y:04d}-Q{n}"


def _edge_path(base_dir, kind):
    return os.path.join(base_dir, config.EDGE_FILENAMES[kind])


def _edges_nonempty(base_dir):
    """已非空的基图边文件清单（force 守卫用）。"""
    out = []
    for kind in config.BASE_EDGE_KINDS:
        p = _edge_path(base_dir, kind)
        if os.path.exists(p):
            try:
                if json.load(open(p, encoding="utf-8")).get("total", 0) > 0:
                    out.append(p)
            except (OSError, ValueError):
                pass
    return out


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def load_jd_vectors(path):
    """读 jd_vectors.jsonl → [record, ...]（每 JD 一条 Stage A+B 分类向量）。"""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _aggregate_skill_prof(prof_records):
    """从 skill_vec_prof 重建的 prof_records 聚合 → {code: {name_zh, n, levels, unset}}。

    消费模式（Stage D）用：Stage C 已把每 JD 的 {code: level} 写入 skill_vec_prof，本函数
    按窗口聚合等级分布（levels 计数 + unset）。旗标/review 留在 Stage C 诊断产物，不进图聚合。
    记录可带 "w"（Stage S 逆概率权重 × 薪资权重）：降采样窗口按权重计数，分布对总体无偏。
    """
    out = {}
    for rec in prof_records:
        w = rec.get("w", 1.0)
        for code, s in (rec.get("skills") or {}).items():
            lvl = s.get("requirement_level")
            d = out.setdefault(code, {"name_zh": code, "n": 0,
                                      "levels": {l: 0 for l in ("P1", "P2", "P3", "P4", "U")},
                                      "unset": 0})
            d["n"] += w
            if lvl in d["levels"]:
                d["levels"][lvl] += w
            else:
                d["unset"] += w
    return out


def _jd_vectors_meta(window):
    """读 jd_vectors.meta.json → dict（缺则 {}）。供 Stage D 取 rubric_version 等。"""
    p = os.path.join(config.JD_DERIVED_DIR, config.JD_VECTORS_META_FILENAME.format(window=window))
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {}


def build_base(window, jd_csv=None, sample_total=None, per_job=None,
               salary_weight=None, prev_window="auto", dry_run=False, force=False,
               extractors=None, out_root=None):
    """构建一个窗口的基图边。返回 stats dict。

    - jd_csv 默认 data/timeline/jd/{window}.csv；prev_window="auto" 自动取上一月/季。
    - extractors 可注入 {task: fn, skill: fn}（测试 mock）；缺省构造真实 LLM 抽取器。
    - 边文件已非空且未 --force → FileExistsError（与快照覆盖约定一致）。
    """
    kind, _, _, _ = parse_window(window)
    out_root = out_root or config.GRAPH_ROOT
    base_dir = os.path.join(out_root, window, config.BASE_SUBDIR)
    jd_csv = jd_csv or os.path.join(config.PROJECT_ROOT, "data", "timeline", "jd", f"{window}.csv")
    # ---- Stage D 消费模式：jd_vectors 源文件存在则读其聚合（跳过字符串门/抽样/LLM 抽取/jd_csv 依赖）----
    jd_vectors_path = os.path.join(config.JD_DERIVED_DIR,
                                   config.JD_VECTORS_FILENAME.format(window=window))
    use_vectors = os.path.exists(jd_vectors_path)
    if not use_vectors and not os.path.exists(jd_csv):
        raise FileNotFoundError(f"JD 月度文件不存在: {jd_csv}（先运行 timeline 编排，或 Stage B 产 jd_vectors）")
    if not dry_run and not force:
        busy = _edges_nonempty(base_dir)
        if busy:
            raise FileExistsError(f"基图边已存在（用 --force 覆盖）: {', '.join(busy)}")

    salary_weight = config.GB_SALARY_WEIGHT if salary_weight is None else salary_weight

    # 上一窗口累积频次（α 链）——两路径共用
    prev_label = prev_window_label(window) if prev_window == "auto" else prev_window
    prev_path = os.path.join(out_root, prev_label or "", config.BASE_SUBDIR,
                             config.BASE_AUX_FILENAMES["freq"]) if prev_label else None
    prev_freq = None
    if prev_path and os.path.exists(prev_path):
        try:
            prev_freq = json.load(open(prev_path, encoding="utf-8")).get("freq")
        except (OSError, ValueError):
            pass
    if prev_freq:
        print(f"[base] 历史衰减：读 {prev_label} freq（α={config.GB_ALPHA}）")
    elif prev_label:
        print(f"[base] 无上一窗口频次（{prev_label}），本次为首批累积")

    prof_records = []
    freq = _new_freq()

    if use_vectors:
        # 消费模式：Stage A+B 已在 jd_vectors 源文件给出每 JD 分类向量，直接聚合
        records = load_jd_vectors(jd_vectors_path)
        n_dropped = sum(1 for r in records if not r.get("it_related", True))
        records = [r for r in records if r.get("it_related", True)]  # 无技术信号/范围外降级记录不进图
        # Stage D0 近重复（抄袭）过滤：变体记录不进聚合（存量窗经 replay 追溯生效；
        # 注意 sample_weight 的分母是去重前口径，属可接受的轻度保守偏差）
        try:
            import jd_dedup
            near_dup = jd_dedup.load_variants(window)
        except Exception:
            near_dup = {}
        if near_dup:
            n_dup = sum(1 for r in records if r.get("jd_key") in near_dup)
            records = [r for r in records if r.get("jd_key") not in near_dup]
            print(f"[base] 近重复过滤：剔除抄袭变体记录 {n_dup} 条（{window}.dedup.json）")
        else:
            n_dup = 0
        scan = {"n_scanned": len(records), "n_sampled": len(records),
                "n_jobs": len({r.get("job_code") for r in records if r.get("job_code")}),
                "n_unmatched": 0, "n_skip_short": 0, "n_skip_dup": 0,
                "n_dropped_non_it": n_dropped, "n_dropped_near_dup": n_dup,
                "source": "jd_vectors"}
        n_salary_ok = sum(1 for r in records if r.get("salary_monthly"))
        # 薪资加权组装期现算（重放友好）：Stage B 恒写 salary_weight=1.0（中性），开启加权时
        # 按本窗 salary_monthly 中位数在此处计算 log(1+s/median)——与旧 CSV 路径同公式，
        # 改参数后重放 Stage D 即可生效，无需重跑 B/C。
        if salary_weight:
            parsed = sorted(v for v in (r.get("salary_monthly") or 0 for r in records) if v)
            median = parsed[len(parsed) // 2] if parsed else None
        else:
            median = None
        print(f"[base] {window}（{kind}）：消费 jd_vectors 源文件 {len(records)} 条 JD"
              f"（已剔除非IT/范围外降级 {n_dropped}；{scan['n_jobs']} 岗位；薪资可解析 {n_salary_ok}"
              + (f"；salary_weight=on median={median}" if median else "") + "）")
        if dry_run:
            return {"window": window, "scan": scan, "n_salary_ok": n_salary_ok,
                    "median": median, "source": "jd_vectors"}
        for i, rec in enumerate(records, 1):
            job_code = rec.get("job_code")
            if not job_code:
                continue
            # Stage S 降采样的逆概率权重 × 薪资加权（组装期现算，见上）
            sw = 1.0
            if salary_weight and median:
                s = rec.get("salary_monthly") or 0
                if s:
                    sw = math.log(1 + s / median)
            weight = (rec.get("sample_weight") or 1.0) * sw
            task_set = set(rec.get("task_vec_01") or [])
            skill_set = set(rec.get("skill_vec_01") or [])
            sp_map = {sk: set(sps) for sk, sps in (rec.get("skillpoint_map") or {}).items()}
            accumulate(freq, job_code, weight, task_set, skill_set, sp_map)
            svp = rec.get("skill_vec_prof") or {}
            if svp:
                prof_records.append({"w": weight, "skills": {c: {"requirement_level": lvl}
                                                             for c, lvl in svp.items()}})
            if i % 500 == 0 or i == len(records):
                print(f"[base] 消费进度 {i}/{len(records)}")
    else:
        # 旧路径：字符串 funtype 门 + 分层抽样 + LLM 句级抽取
        if not os.path.exists(jd_csv):
            raise FileNotFoundError(f"JD 月度文件不存在: {jd_csv}（先运行 timeline 编排，或 Stage B 产 jd_vectors）")
        mapping = load_funtype_map()
        rows, scan = sample_jds(jd_csv, mapping, sample_total, per_job)
        parsed = [v for v in (parse_salary_monthly(r["salary"]) for r in rows) if v]
        median = sorted(parsed)[len(parsed) // 2] if parsed else None
        n_salary_ok = len(parsed)
        print(f"[base] {window}（{kind}）：扫描 {scan['n_scanned']} 行 → 抽样 {len(rows)} 条 / "
              f"{scan['n_jobs']} 岗位（unmatched={scan['n_unmatched']} 短文本={scan['n_skip_short']} "
              f"重复={scan['n_skip_dup']}；薪资可解析 {n_salary_ok}/{len(rows)}"
              f"{'，median=' + str(median) if median else ''}）")
        if dry_run:
            return {"window": window, "scan": scan, "n_salary_ok": n_salary_ok, "median": median}
        if extractors is None:
            extractors = make_extractors()
        prof_eval = extractors.get("prof")
        for i, r in enumerate(rows, 1):
            salary = parse_salary_monthly(r["salary"])
            if salary_weight and salary and median:
                weight = math.log(1 + salary / median)
            else:
                weight = 1.0
            merged_res = extractors["merged"](r["text"]) or {}
            task_set = set(merged_res.get("task_counts", {}))
            skill_set = set(merged_res.get("skill_counts", {}))
            sp_map = {sk: set(sps) for sk, sps in (merged_res.get("skill_skillpoint_map") or {}).items()}
            accumulate(freq, r["job_code"], weight, task_set, skill_set, sp_map)
            if prof_eval is not None:
                try:
                    prof_records.append(prof_eval.evaluate_jd(
                        r["text"], {"title": r.get("title", ""), "funtype": r["funtype"],
                                    "work_year": r.get("work_year", "")}))
                except Exception as e:            # 单条熟练度失败不阻断基图
                    print(f"[base] 熟练度评估失败（跳过该条）：{e}")
            if i % 20 == 0 or i == len(rows):
                print(f"[base] 抽取进度 {i}/{len(rows)}")

    freq = merge_history(freq, prev_freq, config.GB_ALPHA)
    edges = build_edges(freq)
    job_names, task_names, skill_names = _node_name_maps()
    for e in edges["job_task"]:
        e["src_name"], e["dst_name"] = job_names.get(e["src"], ""), task_names.get(e["dst"], "")
    for e in edges["job_skill"]:
        e["src_name"], e["dst_name"] = job_names.get(e["src"], ""), skill_names.get(e["dst"], "")
    for e in edges["task_skill"]:
        e["src_name"], e["dst_name"] = task_names.get(e["src"], ""), skill_names.get(e["dst"], "")
    for e in edges["skill_skillpoint"]:
        e["src_name"], e["dst_name"] = skill_names.get(e["src"], ""), e["dst"]

    # E_jd 实体文档频率（合成 gap 用）
    total = freq["total"] or 1.0
    entity_freq = {
        "schema_version": "0.1", "window": window, "total_weight": round(freq["total"], 4),
        "tasks": {k: round(w / total, 6) for k, w in sorted(freq["tasks"].items())},
        "skills": {k: round(w / total, 6) for k, w in sorted(freq["skills"].items())},
        "skillpoints": {k: round(w / total, 6) for k, w in sorted(freq["skillpoints"].items())},
    }

    # 技能熟练度要求分布（"prof" 评估器在场才有；计数为不加权对数，与 freq 的
    # 薪资加权口径解耦——熟练度分布是要求侧统计，不随薪资伸缩）
    skill_prof_stats = {"n_prof_jds": len(prof_records),
                        "n_prof_pairs": sum(len(r.get("skills") or {}) for r in prof_records)}
    if prof_records:
        if use_vectors:
            # 消费模式：Stage C 已在源文件给出 skill_vec_prof，就地聚合（不依赖 jd_proficiency import）
            vmeta = _jd_vectors_meta(window)
            _write_json(os.path.join(base_dir, config.BASE_AUX_FILENAMES["skill_prof"]),
                        {"schema_version": "0.1", "window": window,
                         "rubric_version": vmeta.get("rubric_version", "jd_proficiency_rubric_v0.1"),
                         "n_jds": len(prof_records),
                         "skills": _aggregate_skill_prof(prof_records),
                         "created": datetime.now().isoformat(timespec="seconds")})
        else:
            try:
                from jd_proficiency import aggregate_proficiency
                from jd_proficiency_prompts import RUBRIC_VERSION
                _write_json(os.path.join(base_dir, config.BASE_AUX_FILENAMES["skill_prof"]),
                            {"schema_version": "0.1", "window": window,
                             "rubric_version": RUBRIC_VERSION,
                             "n_jds": len(prof_records),
                             "skills": aggregate_proficiency(prof_records),
                             "created": datetime.now().isoformat(timespec="seconds")})
            except ImportError:
                print("[base] jd_proficiency 不可用，跳过 skill_prof.json（熟练度记录丢弃）")
                skill_prof_stats = {"n_prof_jds": 0, "n_prof_pairs": 0}

    # 技能点节点（沿用快照 schema；windows 合并上一窗口的记录）
    sp_nodes = {"system_name": "技能点体系（基图）", "schema_version": "0.1",
                "window": window, "total": len(freq["skillpoints"]), "skillpoints": {}}
    prev_sp = {}
    if prev_path and os.path.exists(os.path.join(os.path.dirname(prev_path),
                                                 config.BASE_NODE_FILENAMES["skillpoints"])):
        try:
            prev_sp = json.load(open(os.path.join(
                os.path.dirname(prev_path), config.BASE_NODE_FILENAMES["skillpoints"]),
                encoding="utf-8")).get("skillpoints", {})
        except (OSError, ValueError):
            pass
    for sp, w in sorted(freq["skillpoints"].items()):
        wins = sorted(set(prev_sp.get(sp, {}).get("windows", []) + [window]))
        sp_nodes["skillpoints"][sp] = {"weight": round(w, 4), "windows": wins}

    # 写文件（边 schema 与 snapshot_builder 一致）
    for ek, arr in edges.items():
        _write_json(_edge_path(base_dir, ek),
                    _edge_file(_EDGE_NAMES[ek], ek, window, arr))
    _write_json(os.path.join(base_dir, config.BASE_NODE_FILENAMES["skillpoints"]), sp_nodes)
    _write_json(os.path.join(base_dir, config.BASE_AUX_FILENAMES["freq"]),
                {"schema_version": "0.1", "window": window, "alpha": config.GB_ALPHA,
                 "prev_window": prev_label if prev_freq else None, "freq": freq})
    _write_json(os.path.join(base_dir, config.BASE_AUX_FILENAMES["entity_freq"]), entity_freq)
    stats = {
        "window": window, "scan": scan, "n_salary_ok": n_salary_ok, "median": median,
        "n_edges": {k: len(v) for k, v in edges.items()},
        "n_tasks_seen": len(freq["tasks"]), "n_skills_seen": len(freq["skills"]),
        "n_skillpoints": len(freq["skillpoints"]),
        "total_weight": round(freq["total"], 4),
        "alpha": config.GB_ALPHA, "prev_window": prev_label if prev_freq else None,
        **skill_prof_stats,
    }
    _write_json(os.path.join(base_dir, config.BASE_AUX_FILENAMES["build_info"]),
                {**stats, "jd_csv": jd_csv, "created": datetime.now().isoformat(timespec="seconds"),
                 "params": {"sample_total": sample_total or config.GB_SAMPLE_TOTAL,
                            "per_job": per_job or config.GB_PER_JOB,
                            "salary_weight": salary_weight,
                            "salary_mode": "log1p_median" if salary_weight else "off",
                            "ts_w1": config.GB_TS_W1, "ts_w2": config.GB_TS_W2},
                 "params_fingerprint": config.assembly_params_fingerprint()})
    # 消费模式：自动生成 JD 多维汇总 CSV（每 JD 一行，含技能/任务向量/技能点/熟练度）
    if use_vectors:
        try:
            from jd_summary import write_summary_csv
            csv_path, n_csv = write_summary_csv(window)
            print(f"[base] JD 多维汇总 CSV：{csv_path}（{n_csv} 行）")
        except Exception as e:
            print(f"[base] 汇总 CSV 生成失败（不阻断）：{e}")
    print(f"[base] 边统计：{stats['n_edges']}（E_jd 实体：任务{stats['n_tasks_seen']}/"
          f"技能{stats['n_skills_seen']}/技能点{stats['n_skillpoints']}）→ {base_dir}")
    return stats
