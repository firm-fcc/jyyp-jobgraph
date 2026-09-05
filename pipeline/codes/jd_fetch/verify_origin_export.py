# -*- coding: utf-8 -*-
"""
JD-Origin 导入与导出验证（一次性，2026-08）。

  1. 导入完整性：import_progress.json 汇总（zst + folder 各表 rows vs expected）
  2. IT 占比 sanity：本轮新增 CSV 的 IT 行数 / 库内总行数（参照现有数据 ~7.8%）
  3. 抽样比对：新 CSV 随机 20 行 vs 库内记录（jobid+表 → 关键字段一致）
  4. 跨快照统计：distinct jobid、与既有 2025-2026 CSV 的 jobid 重叠、opentime 月份分布

输出 output/import_report.md
"""
import csv
import glob
import json
import os
import random
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config

CONFIG_ORIGIN = os.path.join(HERE, "config_origin.yaml")
PROGRESS = os.path.join(HERE, "output", "import_progress.json")
REPORT = os.path.join(HERE, "output", "import_report.md")
JD_DIR = os.path.join(config.PROJECT_ROOT, "data", "jd_dataset")

OLD_TABLE_RE = re.compile(r"^job_202[56]_")   # 既有 2025-2026 数据（远端 job51 库导出）
NEW_TABLE_RE = re.compile(r"^job\.csv$|^job_(2022_|2023_|2024_)")  # 本轮 JD-Origin（含 zst 基础表 job.csv）


def read_csv_columns(path, cols):
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            yield {c: row.get(c) for c in cols}


def main():
    section = config.load_config(CONFIG_ORIGIN)
    report = ["# JD-Origin 导入与导出验证报告", ""]
    random.seed(42)

    # ---------- 1. 导入完整性 ----------
    prog = json.load(open(PROGRESS, encoding="utf-8"))
    by_src = {}
    for tbl, info in prog["tables"].items():
        by_src.setdefault(info["source"], []).append((tbl, info))
    report.append("## 1. 导入完整性（import_progress.json）")
    for src, items in sorted(by_src.items()):
        ok = sum(1 for _, i in items if i["rows"] == i["expected"])
        rows = sum(i["rows"] for _, i in items)
        report.append(f"- **{src}**：{len(items)} 表，{ok} 表行数与 dump AUTO_INCREMENT 一致，合计 {rows:,} 行")
        bad = [(t, i) for t, i in items if i["rows"] != i["expected"]]
        for t, i in bad:
            report.append(f"  - ✗ {t}: {i['rows']:,} != 期望 {i['expected']:,}")
    report.append("")

    # ---------- 2/3/4. CSV 侧 ----------
    new_csvs = sorted(p for p in glob.glob(os.path.join(JD_DIR, "*.csv"))
                      if NEW_TABLE_RE.match(os.path.basename(p)))
    old_csvs = sorted(p for p in glob.glob(os.path.join(JD_DIR, "*.csv"))
                      if OLD_TABLE_RE.match(os.path.basename(p)))
    report.append(f"## 2. 导出规模：本轮新增 CSV {len(new_csvs)} 个（既有 2025-26 数据 {len(old_csvs)} 个不动）")

    new_rows, new_jobids, months, sample_rows = 0, set(), Counter(), []
    for p in new_csvs:
        for rec in read_csv_columns(p, ["jobid", "opentime", "job", "funtype", "_table"]):
            new_rows += 1
            new_jobids.add(rec["jobid"])
            m = (rec.get("opentime") or "")[:7]
            if m:
                months[m] += 1
            if random.random() < 20 / max(new_rows, 1) or len(sample_rows) < 20:
                if len(sample_rows) < 400:
                    sample_rows.append(rec)
    report.append(f"- IT 行数：{new_rows:,} | distinct jobid：{len(new_jobids):,}")

    # 库内总行数 → IT 占比
    conn = config.connect(section)
    cur = conn.cursor()
    total_db = 0
    for tbl in config.get_tables(section):
        cur.execute(f"SELECT COUNT(*) FROM `{tbl}`")
        total_db += cur.fetchone()[0]
    report.append(f"- 库内总行数（74 张 job 表）：{total_db:,} | IT 占比：{new_rows/total_db*100:.1f}%"
                  f"（参照现有 2025-26 数据约 7.8%）")

    # ---------- 3. 抽样比对 ----------
    report.append("## 3. 抽样比对（随机 20 行 vs 库内记录）")
    mism = 0
    for rec in random.sample(sample_rows, min(20, len(sample_rows))):
        tbl = rec["_table"]
        cur.execute(f"SELECT job, funtype, opentime FROM `{tbl}` WHERE jobid=%s LIMIT 1", (rec["jobid"],))
        r = cur.fetchone()
        if not r or r[0] != rec["job"] or r[1] != rec["funtype"] or str(r[2]) != rec["opentime"]:
            mism += 1
            report.append(f"  - ✗ {tbl} jobid={rec['jobid']}: CSV={rec['job']}/{rec['funtype']}/{rec['opentime']} vs DB={r}")
    report.append(f"- 比对 {min(20, len(sample_rows))} 行，不一致 {mism} 行")
    conn.close()

    # ---------- 4. 跨快照统计 ----------
    report.append("## 4. 跨快照统计")
    old_jobids = set()
    for p in old_csvs:
        for rec in read_csv_columns(p, ["jobid"]):
            old_jobids.add(rec["jobid"])
    overlap = new_jobids & old_jobids
    report.append(f"- 既有 2025-26 数据 distinct jobid：{len(old_jobids):,}")
    report.append(f"- 新旧 jobid 重叠：{len(overlap):,}（{len(overlap)/max(len(new_jobids),1)*100:.1f}% of 新数据）")
    report.append(f"- 跨快照重复率（新数据内）：{(1 - len(new_jobids)/max(new_rows,1))*100:.1f}%"
                  f"（{new_rows:,} 行 → {len(new_jobids):,} 个职位）")
    report.append("- opentime 月份分布（Top 15）：" +
                  ", ".join(f"{m}={c:,}" for m, c in months.most_common(15)))
    report.append(f"- opentime 覆盖月份：{min(months)} ~ {max(months)}（共 {len(months)} 个月）")

    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")
    print("\n".join(report))
    print(f"\n报告已写入: {REPORT}")


if __name__ == "__main__":
    main()
