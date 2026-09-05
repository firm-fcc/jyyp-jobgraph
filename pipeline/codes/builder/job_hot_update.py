# -*- coding: utf-8 -*-
"""岗位热更新（关联分析）：消费 ΔG 增量层的 pending 新岗位，LLM 抽取任务/技能并映射关联。

输入：`classify/DeltaG/{papers,news}_delta.json`（由 run_paper_delta / run_news_delta 生成）。
对每个 `status=="pending"` 的新岗位：
  1. 聚合岗位证据文本（name/definition + evidence 各句，按 doc_id 聚合）
  2. Stage A：LLM 提取候选任务/技能（`PROMPT_JOB_ASSOC`，仅显式信号、定义+证据必填）
  3. Stage B：复用 `taxonomy_mapper.map_signals`（`prompt_template=PROMPT_JOB_MAP`）
     → 基础体系(map_to) / ΔG 已有(merge_into) / 新建(is_new)
  4. 经 `delta.apply` 落库（新任务/技能证据按合成 doc_id=`job_assoc:{job_id}` 幂等合并）
  5. 解析关联链接（去重）→ 回填 job.related_tasks / related_skills

本模块是 **ΔG 后处理**：不修改论文/新闻提取流水线；新岗位仍 pending（绝不写 jobs0806.json，
由未来图谱合成 / 人工审核消费）。
"""
import json
import os
import re
import sys
from datetime import date

import config
from delta_store import DeltaStore
from llm import call_llm
from paper_logger import RunLogger

# 跨模块导入：extractor 分类层加入 sys.path（复用 taxonomy_mapper / signal_extractor）。
# 唯一命名（job_prompts）避免与 builder 自身 prompts 冲突。
_HERE = os.path.dirname(os.path.abspath(__file__))
for _sub in ("..", "extractor"),:
    _p = os.path.abspath(os.path.join(_HERE, *_sub))
    if _p not in sys.path:
        sys.path.insert(0, _p)
from taxonomy_mapper import load_base_labels, map_signals, norm  # noqa: E402
from signal_extractor import Candidate                           # noqa: E402
from job_prompts import PROMPT_JOB_ASSOC, PROMPT_JOB_MAP         # noqa: E402

VALID_KINDS = {"new_task", "new_skill"}
VALID_CONF = {"high", "medium", "low"}
# 名称长度上限（提示词规范 4-12/14 字；此处为降级保留的截断上限，宽松于规范）
MAX_NAME_CHARS = 20
_ID_RE = re.compile(r"^[A-Z]{2}-\d+$")


def fit_name(name, max_chars=MAX_NAME_CHARS):
    """超长名降级：保留末尾核心词（中文名核心概念通常在尾部）截断到 max_chars，**不丢弃信号**。

    优先在「连接词/分隔符」后截断（避免切碎词）；无合适边界则保留末尾 max_chars。
    截断后的名字仍会交给映射层 LLM 做归一化（那里才是最终命名修正处）。
    """
    if len(name) <= max_chars:
        return name
    # 从窗口起点向后找第一个连接词，取其后的最长干净后缀（≤max_chars，且至少 4 字承载核心）
    start = len(name) - max_chars
    for i in range(start, len(name) - 4):
        if name[i] in "的与及和、，,；;/":
            return name[i + 1:]
    # 无合适边界：保留末尾 max_chars（去前导连接词）
    s = name[-max_chars:].lstrip("的与及和、，,；;/ ")
    return s or name[:max_chars]
_LINK_TAX_TO_SIDE = {"tasks": "task", "new_tasks": "task", "skills": "skill", "new_skills": "skill"}


class _JobAssocRecord:
    """岗位关联分析的合成记录：doc_id 稳定（job_assoc:{job_id}），新任务/技能证据按此幂等合并。"""

    def __init__(self, job_id, pub_date="", tier=""):
        self.doc_id = f"job_assoc:{job_id}"
        self.arxiv_id = ""          # DeltaStore._doc_id 回退用
        self.pub_date = pub_date
        self.tier = tier


def infer_source_kind(path, fallback="papers"):
    """按文件名推断 source_kind（news_delta.json → news，jd_delta.json → jd，其余 → papers）。"""
    base = os.path.basename(path).lower()
    if "news" in base:
        return "news"
    if "jd" in base:
        return "jd"
    if "paper" in base:
        return "papers"
    return fallback


# ---------------- 证据文本聚合 ----------------
def _build_job_evidence_text(job, max_chars):
    """聚合岗位证据句（按 doc_id 标注的原文句块），供 LLM 关联分析。名称/定义由提示词模板注入。"""
    ev = job.get("evidence") or {}
    if not isinstance(ev, dict):
        ev = {}
    parts, total = [], 0
    for doc_id in sorted(ev):
        if total >= max_chars:
            break
        e = ev[doc_id]
        sents = e.get("sentences") if isinstance(e, dict) else None
        for s in sents or []:
            line = f"[{doc_id}] {s}"
            total += len(line)
            if total > max_chars:
                break
            parts.append(line)
    return "\n".join(parts)


def _job_pub_date(job):
    dates = [e.get("date", "") for e in (job.get("evidence") or {}).values()
             if isinstance(e, dict) and e.get("date")]
    return min(dates) if dates else ""


def _job_tier(job):
    tiers = [e.get("tier", "") for e in (job.get("evidence") or {}).values()
             if isinstance(e, dict) and e.get("tier")]
    return max(tiers) if tiers else ""


# ---------------- Stage A：任务/技能提取 ----------------
def _validate_candidate(s, record, kind, index):
    """校验单条关联候选；非法返回 None。"""
    if not isinstance(s, dict):
        return None
    name_zh = str(s.get("name_zh", "")).strip()
    if len(name_zh) < 2:
        return None
    if len(name_zh) > MAX_NAME_CHARS:
        fitted = fit_name(name_zh)
        print(f"[validate] 名称超长降级截断（{len(name_zh)}→{len(fitted)}）：{name_zh} → {fitted}")
        name_zh = fitted
    definition = str(s.get("definition", "")).strip()
    evidence = s.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = [evidence] if isinstance(evidence, str) else []
    evidence = [str(e).strip() for e in evidence if str(e).strip()]
    # 定义与证据必填（关联产物须可溯源）
    if not definition or not evidence:
        return None
    name_en = str(s.get("name_en", "")).strip()
    conf = str(s.get("confidence", "low")).strip().lower()
    if conf not in VALID_CONF:
        conf = "low"
    rationale = evidence[0]
    return Candidate(index, record, kind, name_zh, name_en, definition,
                     rationale, evidence, conf)


def _extract_tasks_skills(job, evidence_text, api_key, max_tokens, logger):
    """Stage A：LLM 提取候选任务/技能 → list[Candidate]。失败返回空。"""
    prompt = (PROMPT_JOB_ASSOC
              .replace("{name_zh}", job.get("name_zh", ""))
              .replace("{name_en}", job.get("name_en", ""))
              .replace("{definition}", job.get("definition", ""))
              .replace("{evidence_text}", evidence_text))
    try:
        raw = call_llm(prompt, parse_json=True, max_tokens=max_tokens, api_key=api_key)
    except Exception as e:
        if logger:
            logger.error("job_assoc_extract", f"{job.get('id', '')} 关联提取 LLM 失败：{e}")
        return []
    if not isinstance(raw, dict):
        return []
    rec = _JobAssocRecord(job.get("id", ""), _job_pub_date(job), _job_tier(job))
    # 程序化防御：丢弃与岗位自身同名/近名的候选（岗位名不应被提取为任务或技能）
    self_norm = {norm(job.get("name_zh", "")), norm(job.get("name_en", ""))}
    self_norm.discard("")
    candidates = []
    for key, kind in (("tasks", "new_task"), ("skills", "new_skill")):
        for s in raw.get(key) or []:
            cand = _validate_candidate(s, rec, kind, len(candidates))
            if cand is None:
                continue
            if self_norm & {norm(cand.name_zh), norm(cand.name_en)}:
                continue
            candidates.append(cand)
    return candidates


# ---------------- Stage B：映射 + 关联回填 ----------------
def _apply_and_link(delta, cand, decision, code_to_name):
    """应用裁决（证据聚合/新建）并解析关联链接。返回 {taxonomy,code,name_zh} 或 None。

    - map_to → 基础体系（delta.apply 走 strengthen 路径）
    - merge_into → ΔG 已有条目（走 merge 路径）
    - is_new → ΔG 新建（走 new 路径；标记 assoc_from 防低强度剪枝致链接悬空）
    """
    if decision.status == "reject":
        return None
    arr = DeltaStore._target_array(decision.final_kind)
    if arr is None:
        return None
    action, detail = delta.apply(cand, decision, code_to_name=code_to_name)
    if action == "strengthen" and decision.map_to:
        tax, code = decision.map_to["taxonomy"], decision.map_to["code"]
        return {"taxonomy": tax, "code": code,
                "name_zh": (code_to_name.get(tax) or {}).get(code, cand.name_zh)}
    if action == "merge" and decision.merge_into:
        item = delta._find_by_id(decision.merge_into)
        name = item.get("name_zh", cand.name_zh) if item else cand.name_zh
        return {"taxonomy": arr, "code": decision.merge_into, "name_zh": name}
    if action == "new":
        eid = detail.rsplit("/", 1)[-1]
        if _ID_RE.match(eid):
            entry = delta._find_by_id(eid)
            if entry is not None:
                entry["assoc_from"] = cand.record.doc_id   # job_assoc:{job_id}
            return {"taxonomy": arr, "code": eid, "name_zh": cand.name_zh}
    return None


def _dedup_links(links):
    seen, out = set(), []
    for link in links:
        key = (link["taxonomy"], link["code"])
        if key in seen:
            continue
        seen.add(key)
        out.append(link)
    return out


# ---------------- 流水线 ----------------
def run_pipeline(delta_path, source_kind=None, output=None, limit=None, api_key=None,
                 max_tokens=None, log_prefix=None, dry_run=False, window=None):
    """对 ΔG 文件的 pending 新岗位做关联分析并回填。探索运行（limit）写 *_explore.json。

    window：窗口驱动运行传入（born_window 契约——关联分析新出生条目盖窗口月而非运行日）。
    """
    source_kind = source_kind or infer_source_kind(delta_path)
    load_path = delta_path
    out_path = output or delta_path
    if limit is not None:
        out_path = os.path.splitext(out_path)[0] + "_explore.json"

    _weights = {"papers": (1.0, config.HALF_LIFE_DAYS),
                "news": (config.NEWS_SOURCE_WEIGHT, config.NEWS_HALF_LIFE_DAYS),
                "jd": (config.JD_SOURCE_WEIGHT, config.JD_HALF_LIFE_DAYS)}
    sw, hl = _weights.get(source_kind, _weights["papers"])
    # born_window 契约（2026-08-29 裁定：出生=入场窗）：窗口驱动的运行必须传窗末，
    # 缺省 date.today() 会把关联分析产生的新任务/技能盖成运行日（2026-09 实证）
    now_date = None
    if window:
        import calendar
        y, m = int(window[:4]), int(window[5:7])
        now_date = date(y, m, calendar.monthrange(y, m)[1])
    delta = DeltaStore(load_path,
                       source_desc=f"岗位热更新（{os.path.basename(delta_path)}）",
                       source_kind=source_kind, source_weight=sw, half_life_days=hl,
                       now=now_date)
    if out_path != load_path:
        delta.path = out_path          # 探索运行：读主文件、写探索副本
    logger = RunLogger(jsonl_path=(log_prefix or config.JOB_HOT_LOG))

    labels = load_base_labels()
    code_to_name = {tax: {l["code"]: l["name_zh"] for l in items} for tax, items in labels.items()}

    jobs = [j for j in delta.data.get("new_jobs", []) if j.get("status") == "pending"]
    if limit is not None:
        jobs = jobs[:limit]

    print(f"[job_hot] {os.path.basename(delta_path)}：pending 新岗位 {len(jobs)} 个"
          f"（source_kind={source_kind}）")
    if dry_run:
        for j in jobs:
            ev = j.get("evidence") or {}
            nsents = sum(len((e.get("sentences") or [])) if isinstance(e, dict) else 0 for e in ev.values())
            print(f"  {j.get('id')} {j.get('name_zh')}（{j.get('name_en', '')}）"
                  f"· 证据文档 {len(ev)} · 证据句 {nsents} · 已关联任务{len(j.get('related_tasks') or [])}/技能{len(j.get('related_skills') or [])}")
        return

    logger.run_start(f"job_hot_update/{os.path.basename(delta_path)}", "hot")
    logger.note(f"ΔG 新岗位 {len(jobs)} 个 pending，开始关联分析")

    for i, j in enumerate(jobs, 1):
        job_id = j.get("id", "")
        name_zh = j.get("name_zh", "")
        evidence_text = _build_job_evidence_text(j, config.JOB_ASSOC_MAX_CHARS)
        logger.batch_start(i, [job_id])
        logger.note(f"[{job_id}] {name_zh}：关联分析（证据文本 {len(evidence_text)} 字符）")

        candidates = _extract_tasks_skills(j, evidence_text, api_key, max_tokens, logger)
        if not candidates:
            logger.note(f"[{job_id}] 无任务/技能候选（证据不足以支撑关联）")
            delta.update_job_links(job_id, [], [])
            delta.save()
            continue
        logger.extract(i, candidates)

        # 关联映射仅面向任务/技能：基层岗位(jobs)不作为映射目标，增量层仅 new_tasks/new_skills 可作合并目标
        assoc_labels = {**labels, "jobs": []}
        delta_items = [it for it in delta.existing_items() if it["array"] in ("new_tasks", "new_skills")]
        decisions = map_signals(candidates, assoc_labels, delta_items,
                                api_key=api_key, max_tokens=max_tokens, logger=logger,
                                prompt_template=PROMPT_JOB_MAP)
        logger.map(i, decisions)

        cand_by_index = {c.index: c for c in candidates}
        related_tasks, related_skills, actions, rejects = [], [], [], []
        for d in decisions:
            cand = cand_by_index.get(d.index)
            if cand is None:
                continue
            if d.status == "reject":
                rejects.append({"name_zh": d.name_zh, "reason": d.reject_reason})
                continue
            # 映射 LLM 失败时的保守 keep-new 在关联场景会过度创建 → 跳过
            if d.is_new and "映射 LLM 失败" in (d.reason or ""):
                rejects.append({"name_zh": d.name_zh, "reason": "映射 LLM 失败，跳过创建"})
                continue
            link = _apply_and_link(delta, cand, d, code_to_name)
            if link is None:
                continue
            side = _LINK_TAX_TO_SIDE.get(link["taxonomy"])
            if side is None:
                continue          # 非任务/技能 tax（如 jobs）不关联
            if side == "task":
                related_tasks.append(link)
            else:
                related_skills.append(link)
            actions.append(f"{link['taxonomy']}:{link['code']} {cand.name_zh}")

        related_tasks = _dedup_links(related_tasks)
        related_skills = _dedup_links(related_skills)
        delta.update_job_links(job_id, related_tasks, related_skills)
        logger.apply(i, actions, rejects)
        logger.note(f"[{job_id}] 关联完成：任务 {len(related_tasks)} / 技能 {len(related_skills)}"
                    f"（拒绝 {len(rejects)} 项）")
        logger.batch_end(i, len(delta.data["new_tasks"]),
                         len(delta.data["new_skills"]), len(delta.data["new_jobs"]))
        delta.save()

    stats = delta.save()
    print(f"岗位热更新完成：{out_path}")
    print(json.dumps(stats, ensure_ascii=False, indent=1))
