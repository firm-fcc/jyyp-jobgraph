# -*- coding: utf-8 -*-
"""新闻 ΔG 热更新流水线编排：parse → filter（LLM 相关性过滤，无关键词门槛）→ extract（新信号+提及）→ mention-map → signal-map → upsert。

分层职责：
- 新闻**解析**（scan_news/NewsSource）→ `codes/news_signal/`（处理层）
- 新闻**分类**（news_filter/news_extractor/mention_mapper/taxonomy_mapper）→ `codes/extractor/`（分类层）
- ΔG **热更新**（delta_store）→ 本模块（builder）

成本关键：filter 仅看 title + 导语（前 800 字符，小 token 调用），仅相关新闻进全文提取。
（2026-08-15 方案 B：移除标题关键词硬门槛——新信号天然在词表之外，门槛实测丢 10% 信号。）

热更新语义：默认恢复断点 → 只消费新增新闻；`--limit` 探索运行用独立文件，不动主断点/主产物。
"""
import json
import os
import sys
from collections import Counter

# 先导入 builder 自己的 config（sys.modules["config"] 缓存为 builder 版），再做跨模块导入，
# 使 extractor 各模块的 `import config` 命中同一 builder config。
import config
from delta_store import DeltaStore
from paper_logger import RunLogger
from participation import participating_delta_items

# 跨模块导入：新闻处理层与分类层加入 sys.path。
# 唯一命名（news_config/news_sampler/news_prompts/paper_prompts）避免与 builder 自身 config/prompts 冲突。
_HERE = os.path.dirname(os.path.abspath(__file__))
for _sub in ("..", "news_signal"), ("..", "extractor"):
    _p = os.path.abspath(os.path.join(_HERE, *_sub))
    if _p not in sys.path:
        sys.path.insert(0, _p)
import news_config  # noqa: F401  新闻处理层配置（NEWS_DIR 等）
from news_parser import scan_news
from news_source import NewsSource
from news_sampler import StratifiedSampler

import random
from news_filter import filter_relevant
from news_extractor import extract_news_signals
from mention_mapper import map_mentions
from taxonomy_mapper import load_base_labels, map_signals

_MENTION_TAX = {"skill": "skills", "task": "tasks", "job": "jobs"}


def _derive_paths(output, log_prefix, limit):
    """确定增量文件/断点/日志路径。探索运行（limit）用独立路径，不动主产物。"""
    if output:
        out_path = output
    elif limit is not None:
        out_path = config.NEWS_DELTA_OUTPUT.rsplit(".json", 1)[0] + "_explore.json"
    else:
        out_path = config.NEWS_DELTA_OUTPUT
    ckpt = os.path.splitext(out_path)[0] + "_checkpoint.json"
    if log_prefix:
        log_jsonl = log_prefix + ".jsonl"
    elif limit is not None:
        log_jsonl = config.NEWS_DELTA_LOG.rsplit(".jsonl", 1)[0] + "_explore.jsonl"
    else:
        log_jsonl = config.NEWS_DELTA_LOG
    return out_path, ckpt, log_jsonl


def _window_start(window):
    """YYYY-MM → 窗首日 date（月度增量口径）。"""
    from datetime import date
    return date(int(window[:4]), int(window[5:7]), 1)


def _window_end(window):
    """YYYY-MM → 窗末日 date（逐窗 as-of 基准）。"""
    import calendar
    from datetime import date
    y, m = int(window[:4]), int(window[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def _apply_sample_cap(window, records):
    """月度降采样（2026-08-30 用户裁定）：单窗处理上限，超出均匀随机抽样——先抽样
    再筛选（后续相关性过滤/抽取只作用于抽样集）。

    确定性：random.Random(窗口种子) → 同窗重跑抽样一致（可重演）；抽样记录落
    data/timeline/news_derived/{window}.sample.json（pool/cap/doc_ids）。新闻信号
    走定性聚合（实体发现/强度 noisy-OR），无需逆概率加权——均匀抽样即可。
    """
    cap = int(config.NEWS_SAMPLE_CAP or 0)
    if not cap or len(records) <= cap:
        return records
    n_pool = len(records)
    rng = random.Random(f"news-sample-{window}")
    keep = sorted(rng.sample(range(n_pool), cap))
    sampled = [records[i] for i in keep]
    try:
        os.makedirs(config.NEWS_DERIVED_DIR, exist_ok=True)
        path = os.path.join(config.NEWS_DERIVED_DIR, f"{window}.sample.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"window": window, "pool_size": n_pool, "cap": cap,
                       "n_sampled": len(sampled), "seed": f"news-sample-{window}",
                       "doc_ids": [r.doc_id for r in sampled],
                       "note": "新闻月度降采样（先抽样再筛选）；确定性种子可重演"},
                      f, ensure_ascii=False, indent=1)
        print(f"[sample] {window}：月度降采样 {n_pool} → {cap}（种子 news-sample-{window}，"
              f"记录 → {path}）")
    except OSError as e:
        print(f"[sample] 抽样记录写入失败（不影响处理）：{e}")
    return sampled


def run_pipeline(news_dir, source=None, limit=None, chunk=None, output=None, log_prefix=None,
                 api_key=None, max_tokens=None, filter_max_tokens=None, resume=True,
                 window=None):
    """执行新闻信号 → 新闻 ΔG 增量层 全流程。

    window（YYYY-MM，月度增量口径）：只处理 pub_date 落在**本窗月份内**的新闻——
    窗口 W 只消费 W 月发表的文档，更早月份属其自身窗口（错过即不入场）。断点缓存保证
    每篇只处理一次；参与门（标签空间）as-of 窗末。
    缺日期新闻保守保留（快照层同口径，量少）。
    """
    chunk = chunk or config.NEWS_EXTRACT_CHUNK
    out_path, ckpt_path, log_jsonl = _derive_paths(output, log_prefix, limit)
    exploration = limit is not None
    window_end = _window_end(window) if window else None

    # 窗口运行走**映射优先惰性解析**（2026-08-31 优化：全量走盘 28 万篇 ~20 分钟 →
    # 秒级）——news_mapping.csv 元数据作窗口池，抽样后只解析抽中文件；映射缺失/失步
    # 自动回退全量 scan_news（正确性优先）。非窗口运行（source 探索/limit）保持全量。
    if window_end and not source and not limit:
        from news_parser import scan_news_metadata, parse_news_selected
        import types as _types
        light, why_not = scan_news_metadata(news_dir)
        if light is not None:
            ws = _window_start(window).isoformat()
            we = window_end.isoformat()
            pool = [r for r in light if not r["pub_date"] or ws <= r["pub_date"] <= we]
            print(f"[window] {window}：映射池过滤（{ws}..{we}）{len(light)} → {len(pool)} 篇（惰性解析）")
            if not pool:
                raise SystemExit(f"窗口 {window} 前无可用新闻")
            light_objs = [_types.SimpleNamespace(**r) for r in pool]
            sampled = _apply_sample_cap(window, light_objs)
            records = parse_news_selected([r.source_file for r in sampled], news_dir)
            if not records:
                raise SystemExit(f"窗口 {window} 抽样集全部解析失败")
        else:
            print(f"[news] 快速路径不可用，回退全量扫描：{why_not}")
            records = scan_news(news_dir, source=source, limit=limit)
            ws = _window_start(window).isoformat()
            we = window_end.isoformat()
            records = [r for r in records if not r.pub_date or ws <= r.pub_date <= we]
            print(f"[window] {window}：月度增量过滤（{ws}..{we}）全量 → {len(records)} 篇")
            if not records:
                raise SystemExit(f"窗口 {window} 前无可用新闻")
            records = _apply_sample_cap(window, records)
    else:
        records = scan_news(news_dir, source=source, limit=limit)
        if not records:
            raise SystemExit(f"未找到新闻（news_dir={news_dir}, source={source}）")
        if window_end:
            n_before = len(records)
            ws = _window_start(window).isoformat()
            we = window_end.isoformat()
            records = [r for r in records if not r.pub_date or ws <= r.pub_date <= we]
            print(f"[window] {window}：月度增量过滤（{ws}..{we}）{n_before} → {len(records)} 篇")
            if not records:
                raise SystemExit(f"窗口 {window} 前无可用新闻")
            records = _apply_sample_cap(window, records)

    nsrc = NewsSource(records)
    logger = RunLogger(jsonl_path=log_jsonl)
    delta = DeltaStore(out_path,
                       source_desc=f"行业新闻（{news_dir}，source={source or '全部'}"
                                   + (f"，窗口 {window}" if window else "") + "）",
                       source_kind="news", source_weight=config.NEWS_SOURCE_WEIGHT,
                       half_life_days=config.NEWS_HALF_LIFE_DAYS, now=window_end)
    labels = load_base_labels()
    code_to_name = {tax: {l["code"]: l["name_zh"] for l in items} for tax, items in labels.items()}

    if resume and not exploration:
        consumed = StratifiedSampler.load_checkpoint(ckpt_path)
        if consumed:
            nsrc.restore_consumed(consumed)
            logger.note(f"断点恢复：已消费 {len(consumed)} 条，剩余 {nsrc.remaining()} 条")

    logger.run_start(f"news/source={source or 'all'}", "hot")
    logger.note(f"解析新闻 {len(records)} 篇，批次大小 {chunk}")

    filter_stats = Counter()
    round_no = 0
    while nsrc.remaining() > 0:
        round_no += 1
        batch = nsrc.next_batch(chunk)
        logger.batch_start(round_no, [r.doc_id for r in batch])

        # 过滤（LLM 相关性过滤：title + 导语，无关键词门槛）
        relevant, fstats = filter_relevant(batch, api_key=api_key,
                                           max_tokens=filter_max_tokens, logger=logger)
        filter_stats.update(fstats)
        logger.note(f"批{round_no} 过滤：{len(batch)} 篇 → LLM 相关 {len(relevant)}")
        if not relevant:
            if not exploration:
                nsrc.save_checkpoint(ckpt_path)
            logger.batch_end(round_no, len(delta.data["new_tasks"]),
                             len(delta.data["new_skills"]), len(delta.data["new_jobs"]))
            continue

        # 提取（新信号 + 提及）
        candidates, mentions_by_doc = extract_news_signals(relevant, api_key=api_key,
                                                           max_tokens=max_tokens, logger=logger)
        logger.extract(round_no, candidates)

        # 提及 → 体系映射 → strengthenings
        rec_by_doc = {r.doc_id: r for r in relevant}
        all_mentions = [m for ms in mentions_by_doc.values() for m in ms]
        if all_mentions:
            name2code = map_mentions(all_mentions, labels, api_key=api_key,
                                     max_tokens=max_tokens, logger=logger)
            n_strengthen = 0
            for doc_id, ms in mentions_by_doc.items():
                record = rec_by_doc.get(doc_id)
                if record is None:
                    continue
                for m in ms:
                    code = name2code.get(m["name"])
                    tax = _MENTION_TAX.get(m.get("type"))
                    if not code or not tax:
                        continue
                    delta.strengthen_existing(record, tax, code, m["name"], m["evidence"], "medium")
                    n_strengthen += 1
            logger.note(f"批{round_no} 提及映射：{len(all_mentions)} 条 → {n_strengthen} 条并入 strengthenings")

        # 新信号 → 体系映射 → ΔG
        # delta_items = 本文件全量（跨文档合并）+ 跨源参与条目（可见性门控，见 participation）
        decisions = map_signals(candidates, labels,
                                delta.existing_items()
                                + participating_delta_items(exclude_src="news", now=window_end),
                                api_key=api_key, max_tokens=max_tokens, logger=logger)
        logger.map(round_no, decisions)
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

        if not exploration:
            nsrc.save_checkpoint(ckpt_path)
        delta.save()
        logger.batch_end(round_no, len(delta.data["new_tasks"]),
                         len(delta.data["new_skills"]), len(delta.data["new_jobs"]))

    logger.note(f"过滤总计：扫描 {filter_stats['scanned']}，LLM 相关 {filter_stats['llm_relevant']}")
    logger.note(f"处理完成：{len(records)} 篇，共 {round_no} 批")
    stats = delta.save()
    print(f"\n新闻 ΔG 增量层已更新：{out_path}")
    print(json.dumps(stats, ensure_ascii=False, indent=1))
