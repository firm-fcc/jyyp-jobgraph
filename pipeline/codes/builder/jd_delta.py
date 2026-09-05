# -*- coding: utf-8 -*-
"""JD 侧 ΔG 热更新流水线：timeline 月度 CSV → 抽样 → extract（新信号+提及）→ map → upsert。

JD 是市场当前需求的**确证源**（非前瞻源）：
- **新信号入叠层**（权重 1.0）：JD 明确要求、超出常规体系的任务/技能/技能点 → jd_delta.json
- **确证叠层实体**：JD 提及参与可见的叠层实体（论文/新闻的前瞻信号）→ 同名条目合并证据
  （本源文件按名称落盘，跨源聚合靠快照层 norm 合并；ev.src="jd" 的证据数 = 转正判据）
- **基线提及跳过**：命中基线体系的提及不入叠层（基图频次域已覆盖，避免与 E_jd 重复计权）
- **不从 JD 发现新岗位**（岗位体系沿用 51job funtype 分类）；new_job 候选仅在能并入
  既有叠层岗位时生效，全新岗位丢弃（记日志）

热更新语义：默认恢复断点（按 jobid）→ 只消费新增 JD；--limit/--output 探索运行用独立路径。
分层职责：JD 抽样/记录（本模块）→ 分类（extractor 的 jd_extractor/mention_mapper/taxonomy_mapper）
→ ΔG 聚合（delta_store）。
"""
import csv
import hashlib
import json
import os
import sys

# 先导入 builder 自己的 config（sys.modules["config"] 缓存为 builder 版），再做跨模块导入。
import config
from delta_store import DeltaStore
from paper_logger import RunLogger
from participation import participating_items, participating_delta_items, overlay_labels, overlay_labels_text

# 跨模块导入：分类层（extractor 目录）。
_HERE = os.path.dirname(os.path.abspath(__file__))
_EXT_DIR = os.path.abspath(os.path.join(_HERE, "..", "extractor"))
if _EXT_DIR not in sys.path:
    sys.path.insert(0, _EXT_DIR)
from jd_extractor import extract_jd_signals
from mention_mapper import map_mentions
from taxonomy_mapper import load_base_labels, map_signals

_MENTION_TAX = {"skill": "skills", "task": "tasks", "job": "jobs"}
_TAX_TO_ARRAY = {"tasks": "new_tasks", "skills": "new_skills", "jobs": "new_jobs"}


class JDRecord:
    """JD 记录适配：统一 doc_id/pub_date 接口（doc_id=jobid → 证据幂等键）。"""

    def __init__(self, jobid, title, funtype, salary, pub_date, body):
        self.doc_id = str(jobid)
        self.arxiv_id = ""
        self.title = title
        self.funtype = funtype
        self.salary = salary
        self.pub_date = pub_date
        self.body = body


def scan_jd_records(csv_path, sample_total=None, per_funtype=None, min_text_chars=None):
    """流式扫描 timeline 月度 CSV → 按 funtype 分层抽样 → JDRecord 列表。

    文本短于 min_text_chars 或与已抽样完全重复（md5）的行跳过。
    返回 (records, stats)。
    """
    sample_total = config.JD_SAMPLE_TOTAL if sample_total is None else sample_total
    per_funtype = config.JD_PER_FUNTYPE if per_funtype is None else per_funtype
    min_text_chars = config.JD_MIN_TEXT_CHARS if min_text_chars is None else min_text_chars
    records, per_ft, seen_text = [], {}, set()
    n_scanned = n_short = n_dup = 0
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            n_scanned += 1
            ft = (row.get("funtype") or "").strip()
            if per_ft.get(ft, 0) >= per_funtype:
                continue
            body = (row.get("job_information") or row.get("job") or "").strip()
            if len(body) < min_text_chars:
                n_short += 1
                continue
            key = hashlib.md5(body.encode("utf-8")).hexdigest()
            if key in seen_text:
                n_dup += 1
                continue
            seen_text.add(key)
            per_ft[ft] = per_ft.get(ft, 0) + 1
            pub_date = (row.get("opentime") or "")[:10]
            records.append(JDRecord(row.get("jobid", ""), row.get("job", ""), ft,
                                    row.get("salary", ""), pub_date, body))
            if len(records) >= sample_total:
                break
    stats = {"n_scanned": n_scanned, "n_sampled": len(records), "n_funtypes": len(per_ft),
             "n_skip_short": n_short, "n_skip_dup": n_dup}
    return records, stats


def _derive_paths(output, log_prefix, limit):
    """确定增量文件/断点/日志路径。探索运行（limit）用独立路径，不动主产物。"""
    if output:
        out_path = output
    elif limit is not None:
        out_path = config.JD_DELTA_OUTPUT.rsplit(".json", 1)[0] + "_explore.json"
    else:
        out_path = config.JD_DELTA_OUTPUT
    ckpt = os.path.splitext(out_path)[0] + "_checkpoint.json"
    if log_prefix:
        log_jsonl = log_prefix + ".jsonl"
    elif limit is not None:
        log_jsonl = config.JD_DELTA_LOG.rsplit(".jsonl", 1)[0] + "_explore.jsonl"
    else:
        log_jsonl = config.JD_DELTA_LOG
    return out_path, ckpt, log_jsonl


def _load_consumed(ckpt_path):
    try:
        return set(json.load(open(ckpt_path, encoding="utf-8")).get("consumed", []))
    except (OSError, ValueError):
        return set()


def _save_consumed(ckpt_path, consumed):
    os.makedirs(os.path.dirname(ckpt_path), exist_ok=True)
    with open(ckpt_path, "w", encoding="utf-8") as f:
        json.dump({"consumed": sorted(consumed), "n": len(consumed)}, f, ensure_ascii=False)


def _window_end(window):
    """YYYY-MM → 窗末日 date（参与门 as-of 基准）。"""
    import calendar
    from datetime import date
    if not window:
        return None
    y, m = int(window[:4]), int(window[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def run_pipeline(window, jd_csv=None, sample_total=None, per_funtype=None, limit=None,
                 chunk=None, output=None, log_prefix=None, api_key=None, max_tokens=None,
                 resume=True, dry_run=False):
    """执行 JD 确证信号 → jd_delta.json 全流程。返回 DeltaStore.stats()（dry-run 返回抽样统计）。"""
    jd_csv = jd_csv or os.path.join(config.PROJECT_ROOT, "data", "timeline", "jd", f"{window}.csv")
    if not os.path.exists(jd_csv):
        raise SystemExit(f"JD 月度文件不存在: {jd_csv}（先运行 timeline 编排）")
    records, scan = scan_jd_records(jd_csv, sample_total, per_funtype)
    print(f"[jd] {window}：扫描 {scan['n_scanned']} 行 → 抽样 {scan['n_sampled']} 条 / "
          f"{scan['n_funtypes']} funtype（短文本={scan['n_skip_short']} 重复={scan['n_skip_dup']}）")
    if limit is not None:
        records = records[: limit]
    if dry_run:
        from collections import Counter
        top = Counter(r.funtype for r in records).most_common(8)
        print(f"[jd] dry-run：待处理 {len(records)} 条；funtype 分布 top: {top}")
        return scan

    chunk = chunk or config.JD_EXTRACT_CHUNK
    out_path, ckpt_path, log_jsonl = _derive_paths(output, log_prefix, limit)
    exploration = limit is not None

    logger = RunLogger(jsonl_path=log_jsonl)
    window_end = _window_end(window) if window else None
    delta = DeltaStore(out_path, source_desc=f"JD 确证（window={window}, csv={jd_csv}）",
                       source_kind="jd", source_weight=config.JD_SOURCE_WEIGHT,
                       half_life_days=config.JD_HALF_LIFE_DAYS, now=window_end)
    labels = load_base_labels()

    # 参与可见的叠层实体（完整清单给提取提示词与提及映射；delta_items 用跨源门控版）。
    # as-of 窗末（原 date.today() 口径在历史窗口回放时不可复现，2026-08-27 修正）
    part = participating_items(now=window_end)
    part_by_id = {it["id"]: it for it in part}
    if part:
        ov = overlay_labels(part)
        ext_labels = {tax: list(labels[tax]) + ov.get(tax, []) for tax in labels}
    else:
        ext_labels = labels
    logger.run_start(f"jd/window={window}", "confirm")
    logger.note(f"参与可见叠层实体 {len(part)} 个（确证目标）")

    consumed = _load_consumed(ckpt_path) if (resume and not exploration) else set()
    if consumed:
        records = [r for r in records if r.doc_id not in consumed]
        logger.note(f"断点恢复：已消费 {len(consumed)} 条，剩余 {len(records)} 条")

    n_confirm = n_base_skip = n_new = n_job_drop = 0
    round_no = 0
    for i in range(0, len(records), chunk):
        round_no += 1
        batch = records[i: i + chunk]
        logger.batch_start(round_no, [r.doc_id for r in batch])

        # Stage A：信号提取（新信号 + 提及；overlay 清单注入提示词）
        candidates, mentions_by_doc = extract_jd_signals(
            batch, overlay_labels=overlay_labels_text(part),
            api_key=api_key, max_tokens=max_tokens, logger=logger)
        logger.extract(round_no, candidates)

        # 提及 → 扩展标签映射（基线 + 叠层参与实体）→ 确证 / 基线跳过
        rec_by_doc = {r.doc_id: r for r in batch}
        all_mentions = [m for ms in mentions_by_doc.values() for m in ms]
        if all_mentions:
            name2code = map_mentions(all_mentions, ext_labels, api_key=api_key,
                                      max_tokens=max_tokens, logger=logger)
            for doc_id, ms in mentions_by_doc.items():
                record = rec_by_doc.get(doc_id)
                if record is None:
                    continue
                for m in ms:
                    code = name2code.get(m["name"])
                    tax = _MENTION_TAX.get(m.get("type"))
                    if not code or not tax:
                        continue
                    if code in part_by_id:  # 叠层实体 → 确证（跨源聚合靠快照 norm 合并）
                        it = part_by_id[code]
                        # 锚定**规范名**而非提及原文名：LLM 兜底映射的提及名可能是近似
                        # 变体，锚定规范名才能保证与跨源同名条目在快照层合并、jd_docs 被统计；
                        # 提及原文保留在证据句里，ref_id 记被确证的叠层 id 便于溯源。
                        delta.confirm_named(_TAX_TO_ARRAY[tax], it["name_zh"], record,
                                            m["evidence"], "high",
                                            name_en=it.get("name_en", ""),
                                            definition=it.get("definition", ""), ref_id=code)
                        n_confirm += 1
                    else:  # 基线体系 → 基图频次域，不入叠层（避免与 E_jd 重复计权）
                        n_base_skip += 1
            logger.note(f"批{round_no} 提及：{len(all_mentions)} 条 → 确证 {n_confirm}，基线跳过 {n_base_skip}")

        # Stage B：新信号 → 体系映射 → ΔG（map_to 基线=跳过；全新 new_job=丢弃）
        decisions = map_signals(candidates, labels,
                                delta.existing_items()
                                + participating_delta_items(exclude_src="jd"),
                                api_key=api_key, max_tokens=max_tokens, logger=logger)
        logger.map(round_no, decisions)
        cand_by_index = {c.index: c for c in candidates}
        actions, rejects = [], []
        for d in decisions:
            cand = cand_by_index.get(d.index)
            if cand is None:
                continue
            if d.status == "reject":
                rejects.append({"name_zh": d.name_zh, "reason": d.reject_reason})
                continue
            if d.map_to:
                n_base_skip += 1  # 基线体系（基图频次域）
                actions.append(f"base-skip:{d.name_zh}")
                continue
            if d.is_new and cand.kind == "new_job":
                n_job_drop += 1  # JD 不发现新岗位：仅当并入既有叠层岗位（merge_into）才生效
                actions.append(f"job-drop:{d.name_zh}")
                continue
            action, detail = delta.apply(cand, d)
            if action == "new":
                n_new += 1
            actions.append(f"{action}:{d.name_zh}")
        logger.apply(round_no, actions, rejects)

        if not exploration:
            consumed.update(r.doc_id for r in batch)
            _save_consumed(ckpt_path, consumed)
        delta.save()
        logger.batch_end(round_no, len(delta.data["new_tasks"]),
                         len(delta.data["new_skills"]), len(delta.data["new_jobs"]))

    logger.note(f"处理完成：{len(records)} 条，确证 {n_confirm}，新信号 {n_new}，"
                f"基线跳过 {n_base_skip}，全新岗位丢弃 {n_job_drop}")
    stats = delta.save()
    print(f"\nJD ΔG 增量层已更新：{out_path}")
    print(json.dumps({**stats, "n_confirm": n_confirm, "n_new": n_new,
                      "n_base_skip": n_base_skip, "n_job_drop": n_job_drop},
                     ensure_ascii=False, indent=1))
    return stats
