# -*- coding: utf-8 -*-
"""论文 ΔG 热更新流水线编排（迁自 paper_signal）：parse → sample → extract（Stage A）→ map（Stage B）→ upsert（ΔG 增量层）→ 基线提及并入 strengthenings（Stage C，2026-08-22）。

热更新语义：默认恢复断点 → 只消费新增论文（单遍，无冷启动）。
探索运行（--limit）使用独立增量文件与日志，不污染主产物与主断点。

分层职责：
- 论文**解析**（scan_papers/PaperSource）→ `codes/paper_signal/`（处理层）
- 论文**分类**（signal_extractor/taxonomy_mapper/paper_mention）→ `codes/extractor/`（分类层）
- ΔG **热更新**（delta_store）→ 本模块（builder）
"""
import json
import os
import sys
from collections import Counter

# 先导入 builder 自己的 config（sys.modules["config"] 缓存为 builder 版），再做跨模块导入，
# 使 signal_extractor/taxonomy_mapper/paper_llm 的 `import config` 命中同一 builder config。
import config
from delta_store import DeltaStore
from paper_logger import RunLogger
from participation import participating_delta_items

# 跨模块导入：论文解析层与分类层加入 sys.path。
# 唯一命名（paper_config/paper_sampler/paper_prompts）避免与 builder 自己的 config/prompts/llm 冲突。
_HERE = os.path.dirname(os.path.abspath(__file__))
for _sub in ("..", "paper_signal"), ("..", "extractor"):
    _p = os.path.abspath(os.path.join(_HERE, *_sub))
    if _p not in sys.path:
        sys.path.insert(0, _p)
import paper_config  # noqa: F401  论文解析层配置（PAPER_DIR 等）
from paper_parser import scan_papers
from paper_source import PaperSource
from paper_sampler import StratifiedSampler
from signal_extractor import extract_signals
from taxonomy_mapper import load_base_labels, map_signals

# Stage C 提及并入的每 code 证据句上限（控增量层文件体积）
MENTION_EVIDENCE_CAP = 5
# 提及识别只跑 skill/task 两模式：gap 修正只消费任务/技能（岗位类 strengthenings
# 在合成时被跳过），省一遍 job 分类的 LLM 成本
MENTION_MODES = ("skill", "task")
_TAX_BY_MODE = {"skill": "skills", "task": "tasks"}


def make_mention_extractors(api_key=None):
    """构建提及识别器（skill/task 双模式）+ 各自体系。config 换出习语（同
    graph/base_builder.make_extractors）：paper_mention→cache 链路需要 extractor
    版 config 的 CACHE_DIR 等专属常量，不能在 builder config 环境下惰性构建。"""
    saved = sys.modules.pop("config", None)
    try:
        import taxonomy as tax
        from paper_mention import PaperMentionExtractor
        from llm_client import LLMClient
        exts, taxs = {}, {}
        for mode in MENTION_MODES:
            exts[mode] = PaperMentionExtractor(mode=mode, llm_client=LLMClient(api_key=api_key))
            taxs[mode] = tax.load(mode)
        return exts, taxs
    finally:
        if saved is not None:
            sys.modules["config"] = saved
        else:
            sys.modules.pop("config", None)


def strengthen_paper_mentions(delta, records, mention_exts, mention_tax,
                              logger=None, round_no=0):
    """Stage C：论文对**基线**体系的提及 → strengthenings（提及识别接入，roadmap P1）。

    与新闻侧不同：论文提及识别是分类式（提单元直接分类到体系 code，复用句级缓存），
    无需名称→code 映射。confidence 固定 medium（与新闻侧口径一致），tier 权重与
    半衰期衰减由 delta_store 证据合成机制处理；同 paper_id 重复并入幂等。
    返回并入条数。
    """
    n = 0
    for record in records:
        for mode in MENTION_MODES:
            res = mention_exts[mode].extract_paper(record, mention_tax[mode]) or {}
            names = mention_tax[mode].code_to_name
            for code, ev_units in (res.get("evidence") or {}).items():
                if not ev_units:
                    continue
                delta.strengthen_existing(record, _TAX_BY_MODE[mode], code,
                                          names.get(code, ""), ev_units[:MENTION_EVIDENCE_CAP],
                                          "medium")
                n += 1
    if logger:
        logger.note(f"批{round_no} 基线提及：{n} 条并入 strengthenings")
    return n


def _derive_paths(output, log_prefix, limit):
    """确定增量文件/断点/日志路径。探索运行（limit）用独立路径，不动主产物。"""
    if output:
        out_path = output
    elif limit is not None:
        out_path = config.DELTA_OUTPUT.rsplit(".json", 1)[0] + "_explore.json"
    else:
        out_path = config.DELTA_OUTPUT
    ckpt = os.path.splitext(out_path)[0] + "_checkpoint.json"
    if log_prefix:
        log_jsonl = log_prefix + ".jsonl"
    elif limit is not None:
        log_jsonl = config.DELTA_LOG.rsplit(".jsonl", 1)[0] + "_explore.jsonl"
    else:
        log_jsonl = config.DELTA_LOG
    return out_path, ckpt, log_jsonl


def _tier_counts(records):
    return ", ".join(f"{k}={v}" for k, v in sorted(Counter(r.tier for r in records).items()))


def _window_end(window):
    """YYYY-MM → 窗末日 date（逐窗 as-of 基准）。"""
    import calendar
    from datetime import date
    y, m = int(window[:4]), int(window[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def _window_start(window):
    """YYYY-MM → 窗首日 date（月度增量口径）。"""
    from datetime import date
    return date(int(window[:4]), int(window[5:7]), 1)


def run_pipeline(papers_dir, tier=None, limit=None, stratum="tier", chunk=None,
                 output=None, log_prefix=None, api_key=None, max_tokens=None, resume=True,
                 no_mention=False, window=None):
    """执行论文信号 → ΔG 增量层 全流程（no_mention=True 跳过 Stage C 基线提及并入）。

    window（YYYY-MM，月度增量口径）：只处理 pub_date 落在**本窗月份内**的论文——
    窗口 W 只消费 W 月发表的文档，更早月份属其自身窗口（错过即不入场，与
    "数据基面=当月处理的数据"的逐窗时序一致）；断点缓存保证每篇终身一次。
    参与门（标签空间）as-of 窗末，体系后续演进不泄漏进历史窗口。
    缺日期论文保守保留（快照层同口径）。
    """
    chunk = chunk or config.EXTRACT_CHUNK
    out_path, ckpt_path, log_jsonl = _derive_paths(output, log_prefix, limit)
    exploration = limit is not None
    window_end = _window_end(window) if window else None

    records = scan_papers(papers_dir, tier=tier, limit=limit)
    if not records:
        raise SystemExit(f"未找到论文（papers_dir={papers_dir}, tier={tier}）")
    if window_end:
        n_before = len(records)
        ws = _window_start(window).isoformat()
        we = window_end.isoformat()
        records = [r for r in records if not r.pub_date or ws <= r.pub_date <= we]
        print(f"[window] {window}：月度增量过滤（{ws}..{we}）{n_before} → {len(records)} 篇")
        if not records:
            raise SystemExit(f"窗口 {window} 当月无可用论文")

    source = PaperSource(records, stratum=stratum)
    logger = RunLogger(jsonl_path=log_jsonl)
    delta = DeltaStore(out_path,
                       source_desc=f"学术论文（{papers_dir}，tier={tier or '全部'}"
                                   + (f"，窗口 {window}" if window else "") + "）",
                       now=window_end)
    labels = load_base_labels()
    code_to_name = {tax: {l["code"]: l["name_zh"] for l in items} for tax, items in labels.items()}
    mention_exts, mention_tax = (None, None)
    if not no_mention:
        mention_exts, mention_tax = make_mention_extractors(api_key)

    if resume and not exploration:
        consumed = StratifiedSampler.load_checkpoint(ckpt_path)
        if consumed:
            source.restore_consumed(consumed)
            logger.note(f"断点恢复：已消费 {len(consumed)} 条，剩余 {source.remaining()} 条")

    logger.run_start(f"papers/tier={tier or 'all'}", "hot")
    logger.note(f"解析论文 {len(records)} 篇（分档：{_tier_counts(records)}），批次大小 {chunk}")

    round_no = 0
    while source.remaining() > 0:
        round_no += 1
        batch = source.next_batch(chunk)
        logger.batch_start(round_no, [r.arxiv_id for r in batch])

        # Stage A：信号提取（extractor 分类层）
        candidates = extract_signals(batch, api_key=api_key, max_tokens=max_tokens, logger=logger)
        logger.extract(round_no, candidates)

        # Stage B：体系映射（extractor 分类层；含程序化精确匹配预过滤）
        # delta_items = 本文件全量（跨文档合并）+ 跨源参与条目（可见性门控，见 participation）
        decisions = map_signals(candidates, labels,
                                delta.existing_items()
                                + participating_delta_items(exclude_src="papers", now=window_end),
                                api_key=api_key, max_tokens=max_tokens, logger=logger)
        logger.map(round_no, decisions)

        # 应用 → 增量层（builder 热更新存储）
        cand_by_index = {c.index: c for c in candidates}
        actions, rejects = [], []
        for d in decisions:
            cand = cand_by_index.get(d.index)
            if cand is None:
                continue
            action, detail = delta.apply(cand, d, code_to_name=code_to_name)
            if action == "reject":
                rejects.append({"name_zh": d.name_zh, "reason": d.reject_reason})
            else:
                actions.append(f"{action}:{d.name_zh}")
        logger.apply(round_no, actions, rejects)

        # Stage C：基线提及 → strengthenings（分类式提及识别，复用句级缓存）
        if mention_exts is not None:
            strengthen_paper_mentions(delta, batch, mention_exts, mention_tax,
                                       logger=logger, round_no=round_no)

        if not exploration:
            source.save_checkpoint(ckpt_path)
        delta.save()
        logger.batch_end(round_no, len(delta.data["new_tasks"]),
                         len(delta.data["new_skills"]), len(delta.data["new_jobs"]))

    logger.note(f"处理完成：{len(records)} 篇，共 {round_no} 批")
    stats = delta.save()
    print(f"\nΔG 增量层已更新：{out_path}")
    print(json.dumps(stats, ensure_ascii=False, indent=1))
