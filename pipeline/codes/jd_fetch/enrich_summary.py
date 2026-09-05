# -*- coding: utf-8 -*-
"""
为 data/jd_dataset/summary.json 补充整体元描述：
  - 数据总量（27 张 job 表的总行数）
  - IT 相关数据量（IT 行总量 + 抽样导出量）
  - funtype 维度统计
结构：
  { "meta": {...整体信息...}, "tables": {表名: {...每表信息...}} }
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_DIR = os.path.join(PROJECT_ROOT, "data", "jd_dataset")
SUMMARY = os.path.join(OUT_DIR, "summary.json")
MAP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "funtype_it_map.json")


def main():
    summary = json.load(open(SUMMARY, encoding="utf-8"))
    section = config.load_config()
    tables = config.get_tables(section)

    conn = config.connect(section)
    cur = conn.cursor()

    # 各表总行数
    total_rows = {}
    for tbl in tables:
        try:
            cur.execute(f"SELECT COUNT(*) FROM `{tbl}`")
            total_rows[tbl] = cur.fetchone()[0]
        except Exception as e:
            total_rows[tbl] = 0
            print(f"  [warn] {tbl}: {e}", flush=True)
    conn.close()

    it_rows_total = sum(v.get("it_rows_total", 0) for v in summary.values())
    it_rows_exported = sum(v.get("it_rows_sampled", 0) for v in summary.values())

    it_map = json.load(open(MAP_FILE, encoding="utf-8"))["parts"]
    it_parts = sum(1 for j in it_map.values() if j["it_related"])

    meta = {
        "description": "IT 相关招聘 JD 数据集元信息（2026-08-05 构建）",
        "source_db": "job51 (MySQL)",
        "tables_total": len(tables),
        "tables_with_it_data": len(summary),
        "total_jd_rows": sum(total_rows.values()),
        "it_rows_total": it_rows_total,
        "it_rows_exported": it_rows_exported,
        "sample_policy": "全量导出（未抽样）",
        "funtype_parts_total": len(it_map),
        "funtype_it_parts": it_parts,
        "note": "total_jd_rows=27张job表全量行数；it_rows_total=按funtype过滤的IT行总数；it_rows_exported=导出行数（全量时等于it_rows_total）",
    }
    per_table = {}
    for t, v in summary.items():
        per_table[t] = dict(v)
        per_table[t]["total_rows"] = total_rows.get(t, 0)

    result = {"meta": meta, "tables": per_table}
    with open(SUMMARY, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"已写入 {SUMMARY}")


if __name__ == "__main__":
    main()
