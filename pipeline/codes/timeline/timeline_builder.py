# -*- coding: utf-8 -*-
"""时间线编排器核心：把 JD / 新闻 / 论文 按时间戳统一编排，供图谱按时间顺序导入。

- **JD**：按 `opentime` 月份重新分组 → `data/timeline/jd/{YYYY-MM}.csv`
  （统一 schema = 两批源 CSV 的并集，缺列填空；行内按 opentime 升序）
- **新闻 / 论文**（单篇单文件）：生成文件→时间戳映射表 CSV →
  `data/timeline/{news,papers}/*_mapping.csv`（消费方按 CSV 排序后顺序读取）

日期逻辑**复用解析层**，保证与下游 ΔG 处理使用同一时间戳：
- 论文：`paper_parser.scan_papers`（pub_date = 【发表日期】→ arXiv YYMM 回退）
- 新闻：`news_parser.scan_news`（pub_date = 发布时间 → 爬取时间回退）

纯 stdlib、零 LLM 调用。
"""
import csv
import os
import re
import sys
from collections import Counter, defaultdict

import timeline_config as config

# 跨模块导入：解析层加入 sys.path。
# 模块名 paper_config / news_config 唯一，避免与各模块自身 config 冲突（沿用 builder 既有约定）。
_HERE = os.path.dirname(os.path.abspath(__file__))
for _sub in ("paper_signal",), ("news_signal",):
    _p = os.path.abspath(os.path.join(_HERE, "..", *_sub))
    if _p not in sys.path:
        sys.path.insert(0, _p)
import paper_parser  # noqa: F401
import news_parser  # noqa: F401

_JD_MONTH_RE = re.compile(r"^\d{4}-\d{2}")   # 兼容 "2025-12-25" 与 "2025-12-25 17:16:31"

# JD 统一 schema：两批源 CSV（9 列 / 11 列）的并集，缺列填空；
# techstack/level/level_source 为双维度标注列（codes/jd_annotate/annotate_jd.py 追加，
# 未标注的源文件自然填空，向后兼容）
JD_COLUMNS = ["jobid", "job", "funtype", "salary", "place", "work_year", "degree",
              "company", "opentime", "job_information", "_table",
              "techstack", "level", "level_source"]

# 映射表列
NEWS_MAPPING_COLUMNS = ["source_file", "doc_id", "source", "title", "pub_date", "crawled_at", "file_md5"]
PAPER_MAPPING_COLUMNS = ["source_file", "arxiv_id", "tier", "title", "pub_date", "file_md5"]


def _write_csv(path, columns, rows):
    """写 CSV（utf-8-sig，与源 JD 文件一致）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({c: (r.get(c) or "") for c in columns})


def _out_kind(out_dir, kind, limit):
    """探索运行（limit）写入 `_explore/{kind}`，不动正式产物。"""
    if limit is not None:
        return os.path.join(out_dir, "_explore", kind)
    return os.path.join(out_dir, kind)


# ==================== JD：按月重新分组 ====================
def _read_jd_rows(jd_dir, limit=None):
    """读取全部 JD CSV → [(month, row)]；month 为 'YYYY-MM'，不可解析为 None。"""
    rows = []
    files = sorted(f for f in os.listdir(jd_dir)
                   if f.startswith("job_") and f.endswith(".csv"))
    for fn in files:
        path = os.path.join(jd_dir, fn)
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
            for r in csv.DictReader(fh):
                d = {c: (r.get(c) or "") for c in JD_COLUMNS}
                m = _JD_MONTH_RE.match((d["opentime"] or "").strip())
                rows.append((m.group(0) if m else None, d))
                if limit and len(rows) >= limit:
                    return rows
    return rows


def build_jd_timeline(jd_dir=None, out_dir=None, limit=None, dry_run=False):
    """按 opentime 月份把 JD 重新分组为月度文件。返回统计 dict。"""
    jd_dir = jd_dir or config.JD_DIR
    out_dir = out_dir or config.TIMELINE_DIR
    rows = _read_jd_rows(jd_dir, limit=limit)

    months = defaultdict(list)
    unknown = []
    for m, d in rows:
        (months[m] if m else unknown).append(d)

    # 行内按 opentime 升序（两种日期格式按字符串前缀均可正确排序），同时间按 jobid 稳定
    for m in months:
        months[m].sort(key=lambda r: (r["opentime"], r["jobid"]))

    stats = {
        "n_files": len(set(d["_table"] for _, d in rows)),
        "n_rows": len(rows),
        "n_unknown": len(unknown),
        "n_months": len(months),
        "months": {m: len(months[m]) for m in sorted(months)},
    }
    if dry_run:
        return stats

    sub = _out_kind(out_dir, config.JD_OUT_SUBDIR, limit)
    for m, recs in sorted(months.items()):
        _write_csv(os.path.join(sub, f"{m}.csv"), JD_COLUMNS, recs)
    if unknown:
        _write_csv(os.path.join(sub, config.JD_UNKNOWN_FILENAME), JD_COLUMNS, unknown)
    return stats


# ==================== 新闻 / 论文：文件→时间戳映射表 ====================
def build_news_mapping(news_dir=None, out_dir=None, limit=None, dry_run=False):
    """生成新闻文件→时间戳映射表（复用 news_parser，pub_date 含发布时间→爬取时间回退）。"""
    news_dir = news_dir or config.NEWS_DIR
    out_dir = out_dir or config.TIMELINE_DIR
    records = news_parser.scan_news(news_dir, limit=limit)

    rows = [{c: getattr(r, c, "") for c in NEWS_MAPPING_COLUMNS} for r in records]
    # 缺日期排末尾（pub_date 空 → "9999"），同日期按 source_file 稳定
    rows.sort(key=lambda r: (r["pub_date"] or "9999", r["source_file"]))

    stats = {
        "n_rows": len(rows),
        "n_sources": len({r["source"] for r in rows}),
        "n_with_date": sum(1 for r in rows if r["pub_date"]),
        "n_missing_date": sum(1 for r in rows if not r["pub_date"]),
    }
    if dry_run:
        return stats
    _write_csv(os.path.join(_out_kind(out_dir, config.NEWS_OUT_SUBDIR, limit), config.NEWS_MAPPING_FILENAME),
               NEWS_MAPPING_COLUMNS, rows)
    return stats


def build_papers_mapping(papers_dir=None, out_dir=None, limit=None, dry_run=False):
    """生成论文文件→时间戳映射表（复用 paper_parser，pub_date 含【发表日期】→arXiv YYMM 回退）。"""
    papers_dir = papers_dir or config.PAPER_DIR
    out_dir = out_dir or config.TIMELINE_DIR
    records = paper_parser.scan_papers(papers_dir, limit=limit)

    rows = [{c: getattr(r, c, "") for c in PAPER_MAPPING_COLUMNS} for r in records]
    rows.sort(key=lambda r: (r["pub_date"] or "9999", r["source_file"]))

    stats = {
        "n_rows": len(rows),
        "tiers": dict(Counter(r["tier"] for r in rows)),
        "n_with_date": sum(1 for r in rows if r["pub_date"]),
        "n_missing_date": sum(1 for r in rows if not r["pub_date"]),
    }
    if dry_run:
        return stats
    _write_csv(os.path.join(_out_kind(out_dir, config.PAPER_OUT_SUBDIR, limit), config.PAPER_MAPPING_FILENAME),
               PAPER_MAPPING_COLUMNS, rows)
    return stats
