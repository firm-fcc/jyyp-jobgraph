# -*- coding: utf-8 -*-
"""
按 funtype 筛选并获取 IT 相关招聘 JD。

依赖 output/funtype_it_map.json（funtype 部分 → it_related 判断）。
对每张 job 表：
  1. 取该表 distinct funtype 字符串
  2. 拆分 " or "，任一 part 为 IT 相关 → 该 funtype 字符串为 IT
  3. 用 IN 查询抓取 IT 行，按 funtype 抽样（--limit-per-funtype）控制规模
输出 data/jd_dataset/{table}.csv（每表一个 CSV 文件）+ data/jd_dataset/summary.json
"""
import argparse
import csv
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

# 数据集输出到 data/jd_dataset/（项目数据目录）
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_DIR = os.path.join(PROJECT_ROOT, "data", "jd_dataset")
MAP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "funtype_it_map.json")

# 关键字段，控制输出体积（job_information 为 JD 正文）
FIELDS = "jobid, job, funtype, salary, place, work_year, degree, company, opentime, job_information"


def is_it_funtype(ft, it_map):
    parts = [p.strip() for p in re.split(r"\s+or\s+", ft)]
    return any(it_map.get(p, {}).get("it_related", False) for p in parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-per-funtype", type=int, default=0, help="每个 funtype 最多保留的行数，0=不限制（全量）")
    ap.add_argument("--tables", nargs="*", default=None, help="限定表名（默认全部）")
    ap.add_argument("--config", default=None, help="MySQL 配置 yaml（默认 jd_fetch/config.yaml 指向远端 job51；"
                                                   "JD-Origin 老数据用 config_origin.yaml 指向本地 51job 库）")
    ap.add_argument("--map", default=MAP_FILE, help=f"funtype IT 映射文件（默认 {MAP_FILE}；"
                                                    "老数据用 output/funtype_it_map_origin.json）")
    ap.add_argument("--out-dir", default=OUT_DIR, help=f"CSV 输出目录（默认 {OUT_DIR}）")
    args = ap.parse_args()

    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    it_map = json.load(open(args.map, encoding="utf-8"))["parts"]
    section = config.load_config(args.config)
    tables = config.get_tables(section) if not args.tables else args.tables

    conn = config.connect(section)
    cur = conn.cursor()
    summary = {}

    for tbl in tables:
        # 0. 动态获取表列，仅选存在的字段（各表结构略有差异）
        cur.execute(f"DESCRIBE `{tbl}`")
        table_cols = [r[0] for r in cur.fetchall()]
        fields = [c for c in FIELDS.split(", ") if c in table_cols]
        if "funtype" not in fields:
            print(f"  {tbl}: 无 funtype 列，跳过", flush=True)
            continue
        # 1. distinct funtype
        cur.execute(f"SELECT DISTINCT funtype FROM `{tbl}` WHERE funtype IS NOT NULL AND funtype<>''")
        distinct_ft = [r[0] for r in cur.fetchall()]
        it_strings = [ft for ft in distinct_ft if is_it_funtype(ft, it_map)]
        if not it_strings:
            print(f"  {tbl}: 无 IT funtype", flush=True)
            continue
        # 2. IN 查询
        placeholders = ",".join(["%s"] * len(it_strings))
        sql = (f"SELECT {','.join(fields)} FROM `{tbl}` "
               f"WHERE funtype IN ({placeholders})")
        cur.execute(sql, it_strings)
        rows = cur.fetchall()
        # 3. 按 funtype 抽样（limit=0 表示全量保留）
        cols = fields
        kept = []
        seen = {}
        for row in rows:
            rec = dict(zip(cols, row))
            ft = rec["funtype"]
            if args.limit_per_funtype and seen.get(ft, 0) >= args.limit_per_funtype:
                continue
            seen[ft] = seen.get(ft, 0) + 1
            rec["_table"] = tbl
            kept.append(rec)
        # 写 CSV（utf-8-sig 便于 Excel 打开；csv 模块处理正文中的逗号/引号/换行）
        out_file = os.path.join(out_dir, f"{tbl}.csv")
        fieldnames = cols + ["_table"] if kept else [c for c in cols] + ["_table"]
        with open(out_file, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(kept)
        summary[tbl] = {"it_funtype_strings": len(it_strings), "it_rows_total": len(rows),
                        "it_rows_sampled": len(kept), "csv": out_file}
        print(f"  {tbl}: IT funtype {len(it_strings)} | IT 行 {len(rows)} | 抽样 {len(kept)}", flush=True)

    conn.close()

    # 合并写入 summary：保留已有 meta 与其他表的条目（原为整文件覆写，追加新表会丢历史）
    summary_path = os.path.join(out_dir, "summary.json")
    merged = {}
    if os.path.exists(summary_path):
        try:
            old = json.load(open(summary_path, encoding="utf-8"))
        except Exception:
            old = {}
        if isinstance(old.get("tables"), dict):  # 旧版嵌套结构（表条目包在 "tables" 键下）
            merged.update(old.pop("tables"))
        merged.update(old)  # meta 等顶层键
    merged.update(summary)
    if "meta" in merged:
        merged["meta"]["origin_extension"] = {
            "date": time.strftime("%Y-%m-%d"),
            "new_tables": len(summary),
            "new_it_rows": sum(v["it_rows_sampled"] for v in summary.values()),
            "note": "JD-Origin 老数据（2022-2024）经 config_origin.yaml + funtype_it_map_origin.json 追加",
        }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    total = sum(v["it_rows_sampled"] for v in summary.values())
    print(f"\n完成！本轮 {len(summary)} 张表，IT JD 总数: {total}")


if __name__ == "__main__":
    main()
