# -*- coding: utf-8 -*-
"""
新批次新闻 docx → txt 批量转换（一次性，2026-08）。

背景：新一批公众号文章（2023-2026，5 个来源 5427 篇 .docx）由 python-docx 生成，
结构统一：第 1 段 = 标题，第 2 段 = 「来源: X发布时间: YYYY-MM-DD HH:MM:SS链接: url」
（三个字段挤在一行），其后为正文。转换为 news_raw 既有 TXT 约定（news_parser 可解析）：

  标题: ...
  链接: ...
  发布时间: YYYY-MM-DD HH:MM:SS
  作者:
  公众号: ...
  来源方式: docx
  爬取时间: ...
  ============================================================（60 个 =）
  正文（段落换行保留）

文件夹映射（新批次目录 → news_raw 目录）：
  机器之心 → 机器之心（新增来源）；雷峰网 → 雷锋网；美团技术团队 → 美团；
  华为云开发者社区 → 华为（公众号原值保留，与既有"华为云开发者联盟"区分）；数字生命卡兹克 → 数字生命卡兹克

用法：
  python convert_docx.py --input /tmp/news_new/数据2023到2026 --dry-run   # 预览统计
  python convert_docx.py --input /tmp/news_new/数据2023到2026            # 转换写入
输出：data/news/news_raw/{目标文件夹}/*.txt + data/news/sources_summary_2023_2026_batch.csv
"""
import argparse
import csv
import datetime
import os
import re
import zipfile
import xml.etree.ElementTree as ET

W_P = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
W_T = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"

HERE = os.path.dirname(os.path.abspath(__file__))
NEWS_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "data", "news"))

FOLDER_MAP = {
    "机器之心": "机器之心",
    "雷峰网": "雷锋网",
    "美团技术团队": "美团",
    "华为云开发者社区": "华为",
    "数字生命卡兹克": "数字生命卡兹克",
}

SEP = "=" * 60
SRC_RE = re.compile(r"来源[:：]\s*(.+?)(?=发布时间[:：]|链接[:：]|$)")
DATE_RE = re.compile(r"发布时间[:：]\s*(\d{4}-\d{2}-\d{2}(?:\s*\d{1,2}:\d{2}(?::\d{2})?)?)")
URL_RE = re.compile(r"链接[:：]\s*(\S+)")


def extract_paras(docx_path):
    """docx → 非空段落列表（保留顺序）。损坏文件抛异常由调用方统计。"""
    with zipfile.ZipFile(docx_path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    paras = []
    for p in root.iter(W_P):
        text = "".join(t.text or "" for t in p.iter(W_T))
        if text.strip():
            paras.append(text.strip())
    return paras


def parse_article(paras):
    """段落列表 → (title, source, pub_time, url, body)。元数据行取前 3 段中首个命中者。"""
    title = paras[0] if paras else ""
    meta_idx, src, pub, url = -1, "", "", ""
    for i, para in enumerate(paras[:3]):
        m = DATE_RE.search(para) or URL_RE.search(para)
        if not m and not SRC_RE.search(para):
            continue
        meta_idx = i
        s = SRC_RE.search(para)
        d = DATE_RE.search(para)
        u = URL_RE.search(para)
        if s:
            src = s.group(1).strip()
        if d:
            pub = d.group(1).strip()
        if u:
            url = u.group(1).strip()
        break
    body = paras[1:meta_idx] + paras[meta_idx + 1:] if meta_idx > 0 else paras[1:]
    return title, src, pub, url, "\n".join(body)


def out_path(target_dir, name):
    """{name}.txt；重名时追加 _2/_3…（与批次内 docx 自带的 _1 后缀不冲突）。"""
    base = os.path.join(target_dir, name + ".txt")
    if not os.path.exists(base):
        return base, 0
    n = 2
    while os.path.exists(f"{base[:-4]}_{n}.txt"):
        n += 1
    return f"{base[:-4]}_{n}.txt", n - 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="解压后的批次根目录（含各来源文件夹）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    args = ap.parse_args()

    now = datetime.datetime.now().isoformat(timespec="seconds")
    raw_root = os.path.join(NEWS_ROOT, "news_raw")
    stats = {}   # 目标文件夹 → 统计
    bad = []     # 损坏/异常文件
    total = collisions = 0

    for folder in sorted(os.listdir(args.input)):
        target = FOLDER_MAP.get(folder)
        if not target:
            print(f"[skip] 未映射的来源文件夹: {folder}")
            continue
        target_dir = os.path.join(raw_root, target)
        os.makedirs(target_dir, exist_ok=True)
        st = stats.setdefault(target, {"src_names": set(), "n": 0, "dates": [], "nodate": 0,
                                       "chars": 0, "collisions": 0})
        files = sorted(f for f in os.listdir(os.path.join(args.input, folder)) if f.endswith(".docx"))
        for fn in files:
            path = os.path.join(args.input, folder, fn)
            try:
                paras = extract_paras(path)
            except Exception as e:
                bad.append(f"{folder}/{fn}: {e}")
                continue
            title, src, pub, url, body = parse_article(paras)
            if not title or len(body) < 200:
                bad.append(f"{folder}/{fn}: 标题缺失或正文过短({len(body)}字)")
                continue
            st["src_names"].add(src or folder)
            if pub[:10]:
                st["dates"].append(pub[:10])
            else:
                st["nodate"] += 1
            st["chars"] += len(body)
            st["n"] += 1
            total += 1
            if args.dry_run:
                continue
            dst, col = out_path(target_dir, fn[:-5])
            if col:
                collisions += 1
                st["collisions"] += 1
            with open(dst, "w", encoding="utf-8") as f:
                f.write(f"标题: {title}\n链接: {url}\n发布时间: {pub}\n作者: \n"
                        f"公众号: {src or folder}\n来源方式: docx\n爬取时间: {now}\n"
                        f"{SEP}\n\n{body}\n")
        print(f"  {folder} → {target}: {st['n']} 篇"
              f"{'' if args.dry_run else '（重名 ' + str(st['collisions']) + '）'}", flush=True)

    # 统计文档（与既有 sources_summary.csv 平行，不含 TS/PT 密度——需 LLM 评估后另算）
    summary_path = os.path.join(NEWS_ROOT, "sources_summary_2023_2026_batch.csv")
    with open(summary_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["目标文件夹", "公众号(来源)", "文章数", "最早发布", "最晚发布",
                    "无日期文章数", "平均正文字符数", "重名追加后缀数"])
        for target in sorted(stats):
            st = stats[target]
            w.writerow([target, "/".join(sorted(st["src_names"])), st["n"],
                        min(st["dates"]) if st["dates"] else "",
                        max(st["dates"]) if st["dates"] else "",
                        st["nodate"], round(st["chars"] / max(st["n"], 1)), st["collisions"]])
    print(f"\n共转换 {total} 篇 | 异常 {len(bad)} | 重名 {collisions}"
          f"{'（dry-run 未写入）' if args.dry_run else ''}")
    print(f"统计文档: {summary_path}")
    if bad:
        for b in bad[:20]:
            print(f"  [bad] {b}")
        if len(bad) > 20:
            print(f"  ...共 {len(bad)} 条")


if __name__ == "__main__":
    main()
