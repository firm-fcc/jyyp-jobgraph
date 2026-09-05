# -*- coding: utf-8 -*-
"""新闻精髓信息提取：TXT 头块 + 正文 → NewsRecord。

数据格式（data/news/news_raw/{公众号}/*.txt）：
  ═══════════════════════════════════════════════
  标题: ...
  链接: ...
  发布时间: 2026-07-27 17:48:07  +0800   （可为空）
  作者: ...
  公众号: 量子位                        （或"来源: 36氪 RSS"）
  爬取时间: 2026-07-28T11:27:07.542725
  ═══════════════════════════════════════════════
  正文……（通常很长，avg 6.7k 字符）

- doc_id = 相对路径（公众号/文件名），作 ΔG 证据幂等键。
- pub_date 优先取"发布时间"，缺省回退"爬取时间"；再缺 → ""（时间衰减用底权）。
- 正文存全文；过短（< MIN_BODY_CHARS）跳过。
- 解析器纯 stdlib（re），永不因缺字段失败；乱码用 errors="replace" 容忍。
"""
import hashlib
import os
import re
from dataclasses import dataclass, field

import news_config as config

_FIELD_RE = re.compile(r"^([^:：]{1,12}):\s*(.*)$")
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_BODY_SEP_RE = re.compile(config.BODY_SEP_RE)


@dataclass
class NewsRecord:
    """单篇新闻的精髓信息。"""
    doc_id: str = ""            # 相对路径（公众号/文件名），ΔG 证据幂等键
    title: str = ""
    source: str = ""            # 公众号 / 来源
    pub_date: str = ""          # YYYY-MM-DD；缺省从爬取时间推导；再缺 ""
    crawled_at: str = ""
    body: str = ""              # 正文全文
    file_md5: str = ""          # md5(空白折叠全文)，断点去重键
    source_file: str = ""       # 相对新闻目录路径


def _collapse(text):
    return re.sub(r"\s+", " ", text or "").strip()


def _extract_date(text):
    m = _DATE_RE.search(text or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def _split_header_body(raw):
    """返回 (header_lines, body)。优先用 ==== 分隔线；无则取头字段之后的正文。"""
    lines = raw.splitlines()
    for i, ln in enumerate(lines):
        if _BODY_SEP_RE.match(ln):
            return lines[:i], "\n".join(lines[i + 1:])
    # 无分隔线：头字段为开头的 标题/链接/发布时间/... 行，正文为其后所有行
    header_fields = {config.TITLE_FIELD, config.PUB_DATE_FIELD, config.CRAWL_FIELD,
                     "链接", "作者", "公众号", "来源"}
    idx = 0
    for i, ln in enumerate(lines[:30]):
        m = _FIELD_RE.match(ln)
        if m and m.group(1) in header_fields:
            idx = i + 1
        else:
            break
    return lines[:idx], "\n".join(lines[idx:])


def _parse_header(header_lines):
    """解析头块 → {字段名: 值}。"""
    fields = {}
    for ln in header_lines:
        m = _FIELD_RE.match(ln)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def parse_news_file(path, base_dir=None):
    """解析单篇 TXT → NewsRecord；正文过短/空返回 None。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError as e:
        print(f"[news] 读取失败 {path}: {e}")
        return None
    collapsed = _collapse(raw)
    if len(collapsed) < config.MIN_BODY_CHARS:
        return None  # 过短（乱码/空文件）
    header_lines, body = _split_header_body(raw)
    fields = _parse_header(header_lines)

    title = (fields.get(config.TITLE_FIELD) or "").strip()
    source = ""
    for k in config.SOURCE_FIELDS:
        if fields.get(k):
            source = fields[k].strip()
            break
    pub_date = _extract_date(fields.get(config.PUB_DATE_FIELD))
    crawled = (fields.get(config.CRAWL_FIELD) or "").strip()
    if not pub_date:
        pub_date = _extract_date(crawled)

    if not title:
        title = body.splitlines()[0].strip() if body.strip() else ""
    if not source:
        source = os.path.basename(os.path.dirname(path))

    rel = os.path.relpath(path, base_dir) if base_dir else path
    return NewsRecord(
        doc_id=rel[:-4] if rel.endswith(".txt") else rel,
        title=title,
        source=source,
        pub_date=pub_date,
        crawled_at=crawled,
        body=body.strip(),
        file_md5=hashlib.md5(collapsed.encode("utf-8")).hexdigest(),
        source_file=rel,
    )


def scan_news(news_dir=None, source=None, limit=None):
    """扫描公众号子目录下的 TXT → NewsRecord[]。source 限单个公众号；limit 限制返回数。"""
    news_dir = news_dir or config.NEWS_DIR
    if not os.path.isdir(news_dir):
        raise FileNotFoundError(f"新闻数据目录不存在: {news_dir}")
    records = []
    source_dirs = sorted(d for d in os.listdir(news_dir)
                         if os.path.isdir(os.path.join(news_dir, d)))
    if not source_dirs:
        raise ValueError(f"未找到公众号子目录：{news_dir}")
    for sd in source_dirs:
        if source and sd != source:
            continue
        sub = os.path.join(news_dir, sd)
        for fn in sorted(os.listdir(sub)):
            if not fn.endswith(".txt"):
                continue
            rec = parse_news_file(os.path.join(sub, fn), base_dir=news_dir)
            if rec is None:
                continue
            records.append(rec)
            if limit and len(records) >= limit:
                return records
    return records


# ---------------- 映射优先的惰性解析（2026-08-31 优化：全量走盘 28 万篇 ~20 分钟 → 秒级）----------------
# 前提：data/timeline/news/news_mapping.csv（导入工具产出）与 news_raw 盘上文件同步。
# 流程：scandir 对账（~0.4s，失步即回退全量 scan_news）→ 映射行作窗口池（元数据）→
# 抽样后**只解析抽中文件**。同语料+同映射状态下结果确定；语料被重导入后旧窗样本
# 本就不可复现（池规模漂移），与路径无关。
MAPPING_PATH = os.path.join(config.PROJECT_ROOT, "data", "timeline", "news", "news_mapping.csv")
_SYNC_TOLERANCE = 5          # 盘上/映射差超过该数 → 判失步回退（当前实测差 1）


def _disk_txt_index(news_dir):
    """scandir 收集盘上 .txt 相对路径集合（正斜杠/反斜杠按 os.sep 归一）。"""
    out = set()
    for sd in sorted(os.listdir(news_dir)):
        sub = os.path.join(news_dir, sd)
        if not os.path.isdir(sub):
            continue
        for fn in os.listdir(sub):
            if fn.endswith(".txt"):
                out.add(os.path.join(sd, fn))
    return out


def _norm_rel(p):
    return (p or "").replace("/", os.sep).replace("\\", os.sep)


def scan_news_metadata(news_dir=None, mapping_path=None):
    """映射优先的窗口池（**元数据行**，非 NewsRecord）。

    返回 (light_rows, None) 或 (None, reason)——后者表示映射缺失/失步，调用方应
    回退 scan_news 全量扫描。light_row 为 dict：source_file/doc_id/source/title/
    pub_date/crawled_at（doc_id 兼容 parse_news_file 口径 = 去 .txt 相对路径）。
    顺序：按 source_file 排序（确定性；窗口过滤与抽样在 light 行上做）。
    """
    news_dir = news_dir or config.NEWS_DIR
    mapping_path = mapping_path or MAPPING_PATH
    if not os.path.exists(mapping_path):
        return None, f"映射不存在: {mapping_path}"
    import csv
    rows = {}
    with open(mapping_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rel = _norm_rel(r.get("source_file"))
            if rel:
                rows[rel] = r
    disk = _disk_txt_index(news_dir)
    missing_disk = sorted(set(rows) - disk)        # 映射有、盘上无（导入后被删）
    missing_map = sorted(disk - set(rows))         # 盘上有、映射无（新落盘未重建映射）
    if len(missing_disk) + len(missing_map) > _SYNC_TOLERANCE:
        return None, (f"映射失步（仅映射 {len(missing_disk)} / 仅盘上 {len(missing_map)}），"
                      "请重建 news_mapping.csv 后再走快速路径")
    if missing_disk or missing_map:
        print(f"[news] 映射与盘上轻微偏差（仅映射 {len(missing_disk)} / 仅盘上 "
              f"{len(missing_map)}），按交集继续", flush=True)
    light = []
    for rel in sorted(set(rows) & disk):
        r = rows[rel]
        light.append({
            "source_file": rel,
            "doc_id": rel[:-4] if rel.endswith(".txt") else rel,
            "source": (r.get("source") or "").strip(),
            "title": (r.get("title") or "").strip(),
            "pub_date": (r.get("pub_date") or "").strip(),
            "crawled_at": (r.get("crawled_at") or "").strip(),
        })
    return light, None


def parse_news_selected(rel_files, news_dir=None):
    """只解析指定相对路径集合 → NewsRecord[]（保持输入顺序；解析失败跳过并计数）。"""
    news_dir = news_dir or config.NEWS_DIR
    out, dropped = [], 0
    for rel in rel_files:
        rec = parse_news_file(os.path.join(news_dir, rel), base_dir=news_dir)
        if rec is None:
            dropped += 1
            continue
        out.append(rec)
    if dropped:
        print(f"[news] 抽样集解析丢弃 {dropped} 篇（正文过短/不可读）", flush=True)
    return out
