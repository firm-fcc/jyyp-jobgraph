# -*- coding: utf-8 -*-
"""zip 全量新闻 txt → news_raw 补充入库（一次性，2026-08-30）。

背景：全量爬取结果 新闻内容爬取结果.zip（33 源 302,548 篇）与 news_raw 既有语料比对后
补充入库——byte 保真拷贝（不重排头块：zip 内 TXT 为「标题/来源/发布时间/链接 + 正文」
无分隔线格式，news_parser._split_header_body 的回退路径可解析，pub_date 取发布时间）。

入库规则（与既有语料口径一致）：
- 全文空白折叠 < min_body_chars（settings.yaml → news.min_body_chars，200 字）→ 拒绝，
  记入 data/news/zip_import_rejects.txt（与 docx_convert_rejects.txt 平行；
  该口径 = news_parser.parse_news_file 的跳过条件，保证 news_raw 内文件 100% 可解析）；
- 目标同名文件已存在 → 跳过不覆盖（保持既有 md5/断点稳定；已入库源重跑自动幂等）；
- Windows 非法字符清洗、超长路径截断（截断名尾部拼内容摘要哈希保唯一）；
- 重名（清洗/截断后碰撞）→ 追加 _2/_3…（与 convert_docx.out_path 同约定）。

文件夹映射（zip 源目录 → news_raw 目录；末尾 4 个为本次新增目录）：
  36氪→36kr、AI科技评论→AI科技评论、InfoQ中文→infoQ_cn、InfoQ英文→infoQ、
  PyTorch→pytorch blog、TechCrunch→techcrunch、VentureBeat→vb、Wired→wired、ZDNet→zdnet、
  华为云开发者社区→华为、大厂日爆0816→大厂日爆、大数据文摘→大数据文摘、
  字节跳动技术工程0816→byte_dance、数字生命卡兹克→数字生命卡兹克、新智元→新智元、
  晚点LatePost→晚点、机器之心→机器之心、极客公园→geekpark、澎湃新闻→澎湃新闻*、
  界面新闻→界面新闻科技、百度智能云→百度智能云*、美团技术团队→美团、
  腾讯技术工程0816→tencent、量子位→量子位、阿里技术→alibaba、雷峰网→雷锋网、
  Ars Technica→ars、Google AI→google_ai、Hugging Face→huggingface、
  The New Stack→the new stack、Synced Review(机器之心英文站)→synced、
  Hacker News→hacker_news*、OpenAI Blog→openai_blog*

用法：
  python import_zip.py --dry-run                 # 全源统计预览（不写文件）
  python import_zip.py --source 澎湃新闻         # 单源入库
  python import_zip.py --source 36氪 --limit 100 # 单源限量（验证用）
  python import_zip.py                           # 全量入库
输出：data/news/news_raw/{目标文件夹}/*.txt
      + data/news/sources_summary_zip_import.csv（逐源汇总）
      + data/news/zip_import_rejects.txt（短文拒绝清单）
"""
import argparse
import csv
import datetime
import hashlib
import os
import re
import sys
import zipfile

import news_config as config

HERE = os.path.dirname(os.path.abspath(__file__))
NEWS_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "data", "news"))
RAW_ROOT = os.path.join(NEWS_ROOT, "news_raw")

FOLDER_MAP = {
    "36氪": "36kr", "AI科技评论": "AI科技评论", "InfoQ中文": "infoQ_cn",
    "InfoQ英文": "infoQ", "PyTorch": "pytorch blog", "TechCrunch": "techcrunch",
    "VentureBeat": "vb", "Wired": "wired", "ZDNet": "zdnet",
    "华为云开发者社区": "华为", "大厂日爆0816": "大厂日爆", "大数据文摘": "大数据文摘",
    "字节跳动技术工程0816": "byte_dance", "数字生命卡兹克": "数字生命卡兹克",
    "新智元": "新智元", "晚点LatePost": "晚点", "机器之心": "机器之心",
    "极客公园": "geekpark", "澎湃新闻": "澎湃新闻", "界面新闻": "界面新闻科技",
    "百度智能云": "百度智能云", "美团技术团队": "美团", "腾讯技术工程0816": "tencent",
    "量子位": "量子位", "阿里技术": "alibaba", "雷峰网": "雷锋网",
    "Ars Technica": "ars", "Google AI": "google_ai", "Hugging Face": "huggingface",
    "The New Stack": "the new stack", "Synced Review(机器之心英文站)": "synced",
    "Hacker News": "hacker_news", "OpenAI Blog": "openai_blog",
}

_COLLAPSE_RE = re.compile(r"\s+")
_INVALID_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_DATE_RE = re.compile(r"^发布时间[:：]\s*(\d{4}-\d{2}-\d{2})", re.M)
MAX_PATH = 240          # 目标全路径安全上限（NTFS 260 预留余量）
PROGRESS_EVERY = 20000  # 大源进度打印间隔


def _sanitize_stem(stem):
    """清洗 Windows 非法字符与首尾空白/点；返回 (stem, 是否修改)。"""
    s = _INVALID_RE.sub("_", stem)
    s = s.strip(" .")
    return (s, s != stem)


def _fit_path(folder_dir, stem):
    """超长路径截断：stem 尾部拼原文摘要哈希 8 位保唯一。返回 (stem, 是否截断)。"""
    ext = ".txt"
    budget = MAX_PATH - len(os.path.join(folder_dir, "")) - len(ext)
    if len(stem) <= budget:
        return stem, False
    digest = hashlib.md5(stem.encode("utf-8")).hexdigest()[:8]
    s = stem[: max(budget - 9, 1)].rstrip(" ._")
    return f"{s}_{digest}", True


def _target_path(folder_dir, stem):
    """目标路径：已存在（含 _2/_3… 兜底碰撞）→ 追加后缀。返回 (path, 碰撞次数)。"""
    base = os.path.join(folder_dir, stem + ".txt")
    if not os.path.exists(base):
        return base, 0
    n = 2
    while os.path.exists(f"{base[:-4]}_{n}.txt"):
        n += 1
    return f"{base[:-4]}_{n}.txt", n - 1


def main():
    ap = argparse.ArgumentParser(description="zip 全量新闻补充入库 news_raw")
    ap.add_argument("--zip", default=r"D:\CodeLib\challenge\新闻内容爬取结果.zip",
                    help="源 zip 路径（默认根目录 新闻内容爬取结果.zip）")
    ap.add_argument("--source", default=None, help="仅处理该 zip 源目录名")
    ap.add_argument("--limit", type=int, default=None, help="单源处理条数上限（验证用）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    min_chars = config.MIN_BODY_CHARS
    rejects_path = os.path.join(NEWS_ROOT, "zip_import_rejects.txt")
    summary_path = os.path.join(NEWS_ROOT, "sources_summary_zip_import.csv")

    z = zipfile.ZipFile(args.zip)
    entries = {}   # zip源 → [(文件名, ZipInfo)]
    for info in z.infolist():
        if info.is_dir() or info.filename.endswith("/"):
            continue
        parts = info.filename.split("/")
        if len(parts) != 3:
            print(f"[warn] 非常规层级，跳过: {info.filename}")
            continue
        if parts[1] not in FOLDER_MAP:
            print(f"[warn] 未映射的 zip 源目录，跳过: {parts[1]}")
            continue
        entries.setdefault(parts[1], []).append((parts[2], info))

    # 小源先跑（快速覆盖多数源），大源压轴
    order = sorted(entries, key=lambda s: len(entries[s]))
    if args.source:
        order = [s for s in order if s == args.source]
        if not order:
            raise SystemExit(f"zip 中无源目录: {args.source}")

    total = {k: 0 for k in ("zip_n", "imported", "skipped_exist", "rejected_short",
                            "sanitized", "truncated", "collisions")}
    rejects_fh = None if args.dry_run else open(rejects_path, "w", encoding="utf-8")
    rows = []   # 汇总 CSV 行

    try:
        if rejects_fh:
            rejects_fh.write(f"# zip→news_raw 入库短文拒绝清单（{datetime.date.today()}，"
                             f"全文空白折叠 < {min_chars} 字，与 news_parser 跳过条件同口径）\n")
        for zsrc in order:
            target = FOLDER_MAP[zsrc]
            st = {k: 0 for k in total}
            st["zip_n"] = len(entries[zsrc])
            dates, nodate = [], 0
            folder_dir = os.path.join(RAW_ROOT, target)
            if not args.dry_run:
                os.makedirs(folder_dir, exist_ok=True)
            for fn, info in entries[zsrc]:
                if args.limit and st["imported"] + st["skipped_exist"] >= args.limit:
                    break
                data = z.read(info)
                text = data.decode("utf-8", errors="replace")
                # 与 parse_news_file 同口径：全文空白折叠后过短 → 拒绝
                if len(_COLLAPSE_RE.sub(" ", text).strip()) < min_chars:
                    st["rejected_short"] += 1
                    if rejects_fh:
                        rejects_fh.write(f"{zsrc}/{fn}: 正文过短({len(text.strip())}字)\n")
                    continue
                m = _DATE_RE.search(text)
                if m:
                    dates.append(m.group(1))
                else:
                    nodate += 1
                stem, ext = os.path.splitext(fn)
                stem, changed = _sanitize_stem(stem)
                if changed:
                    st["sanitized"] += 1
                stem, trunc = _fit_path(folder_dir, stem)
                if trunc:
                    st["truncated"] += 1
                    changed = True
                # 未清洗/未截断的原始名已存在 = 同一文章已入库（或重跑）→ 跳过保幂等；
                # 仅清洗/截断造成的人工同名才追加后缀（见 _target_path）
                if not changed and os.path.exists(os.path.join(folder_dir, stem + ".txt")):
                    st["skipped_exist"] += 1
                elif args.dry_run:
                    st["imported"] += 1
                else:
                    dst, col = _target_path(folder_dir, stem)
                    if col:
                        st["collisions"] += 1
                    with open(dst, "wb") as f:
                        f.write(data)
                    st["imported"] += 1
                if (st["imported"] + st["skipped_exist"]) % PROGRESS_EVERY == 0 and \
                        (st["imported"] + st["skipped_exist"]) > 0:
                    print(f"    [{zsrc}] 已处理 {st['imported'] + st['skipped_exist']}/{st['zip_n']}", flush=True)
            for k in total:
                total[k] += st[k]
            rows.append([target, zsrc, st["zip_n"], st["imported"], st["skipped_exist"],
                         st["rejected_short"], st["sanitized"], st["truncated"],
                         st["collisions"], nodate,
                         min(dates) if dates else "", max(dates) if dates else ""])
            print(f"  {zsrc} → {target}: zip {st['zip_n']} | 入库 {st['imported']}"
                  f" | 已存在跳过 {st['skipped_exist']} | 短文拒绝 {st['rejected_short']}"
                  f" | 清洗 {st['sanitized']} | 截断 {st['truncated']} | 碰撞 {st['collisions']}", flush=True)
    finally:
        if rejects_fh:
            rejects_fh.close()

    mode = "（dry-run 未写入）" if args.dry_run else ""
    if not args.dry_run:
        with open(summary_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["目标文件夹", "zip源", "zip文件数", "入库数", "已存在跳过", "短文拒绝",
                        "文件名清洗", "路径截断", "重名追加后缀", "无日期文章数", "最早发布", "最晚发布"])
            w.writerows(rows)
        print(f"\n汇总文档: {summary_path}")
        print(f"拒绝清单: {rejects_path}")
    print(f"合计: zip {total['zip_n']} | 入库 {total['imported']} | 已存在跳过 "
          f"{total['skipped_exist']} | 短文拒绝 {total['rejected_short']} | 清洗 "
          f"{total['sanitized']} | 截断 {total['truncated']} | 碰撞 {total['collisions']}{mode}")


if __name__ == "__main__":
    main()
