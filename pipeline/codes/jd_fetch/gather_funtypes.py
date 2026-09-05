# -*- coding: utf-8 -*-
"""
收集所有 job 表中的 distinct funtype（按 " or " 拆分为独立部分），输出到 output/all_funtype_parts.json。
结果结构：
{
  "part": { "tables": [...], "count": 该 part 在所有表中出现的行数 },
  ...
}
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "all_funtype_parts.json")


def main():
    section = config.load_config()
    tables = config.get_tables(section)
    print(f"共 {len(tables)} 张表", flush=True)

    conn = config.connect(section)
    cur = conn.cursor()

    part_info = {}  # part -> {"tables": set, "count": int}

    for tbl in tables:
        sql = f"SELECT funtype, COUNT(*) FROM `{tbl}` WHERE funtype IS NOT NULL AND funtype<>'' GROUP BY funtype"
        try:
            cur.execute(sql)
            rows = cur.fetchall()
        except Exception as e:
            print(f"  [warn] {tbl}: {e}", flush=True)
            continue
        for ft, cnt in rows:
            parts = [p.strip() for p in re.split(r"\s+or\s+", ft)]
            for p in parts:
                if p not in part_info:
                    part_info[p] = {"tables": set(), "count": 0}
                part_info[p]["tables"].add(tbl)
                part_info[p]["count"] += cnt
        print(f"  {tbl}: {len(rows)} 个 funtype，累计 {len(part_info)} 个独立部分", flush=True)

    conn.close()

    # 序列化 set -> list
    result = {
        "total_tables": len(tables),
        "total_parts": len(part_info),
        "parts": {p: {"tables": sorted(v["tables"]), "count": v["count"]}
                  for p, v in sorted(part_info.items())},
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n完成！独立 funtype 部分: {len(part_info)} 个")
    print(f"输出: {OUT}")


if __name__ == "__main__":
    main()
