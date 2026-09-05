# -*- coding: utf-8 -*-
"""Extractor CLI 入口。

用法示例：
  python run_extractor.py --mode skill --input "负责Java后端开发，熟悉Spring Cloud"
  python run_extractor.py --mode task --input data/jd_dataset/job_2026_05_30.csv --output out.json --limit 10
  python run_extractor.py --mode skill --input path/to/jd.txt

参数：
  --mode skill|task       抽取类型（默认 skill）
  --input PATH            单个文本 / .txt/.md 文件 / .csv（取 job_information 列）/ 目录
  --output PATH           输出 JSON（默认 stdout）
  --limit N               最多处理的 JD 条数（测试用）
  --no-cache              禁用句级缓存
  --taxonomy PATH         自定义分类体系文件（覆盖默认）
"""
import argparse
import csv
import json
import os
import sys

import config
import taxonomy as tax
from extractor import Extractor


def read_input(path):
    """根据输入路径类型返回 JD 文本列表。"""
    if os.path.isdir(path):
        texts = []
        for f in sorted(os.listdir(path)):
            if f.endswith((".txt", ".md", ".csv")):
                texts.extend(read_input(os.path.join(path, f)))
        return texts
    if path.endswith(".csv"):
        texts = []
        with open(path, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            col = "job_information" if "job_information" in (reader.fieldnames or []) else "job"
            for row in reader:
                texts.append(row.get(col, "") or "")
        return texts
    # 视为文本文件
    with open(path, encoding="utf-8") as f:
        return [f.read()]


def main():
    ap = argparse.ArgumentParser(description="Extractor：从 JD 提取技能/任务并计数")
    ap.add_argument("--mode", default="skill", choices=["skill", "task"])
    ap.add_argument("--input", required=True, help="文本 / 文件 / CSV / 目录")
    ap.add_argument("--output", default=None, help="输出 JSON 路径")
    ap.add_argument("--limit", type=int, default=None, help="最多处理的 JD 条数")
    ap.add_argument("--no-cache", action="store_true", help="禁用句级缓存")
    ap.add_argument("--taxonomy", default=None, help="自定义分类体系 JSON 路径")
    args = ap.parse_args()

    # 加载分类体系
    if args.taxonomy:
        # 支持自定义：文件结构为 {"items": [{code,name_zh,name_en}]} 或 {"skills"/"tasks": [...]}
        data = json.load(open(args.taxonomy, encoding="utf-8"))
        items = data.get("items") or data.get("skills") or data.get("tasks") or data.get("detail", {}).values()
        labels = []
        for it in items:
            if isinstance(it, dict):
                labels.append({"code": it.get("code", ""), "name_zh": it.get("name_zh", it.get("name", "")),
                               "name_en": it.get("name_en", "")})
        from taxonomy import Taxonomy
        taxonomy = Taxonomy(labels, args.mode, "自定义体系")
    else:
        taxonomy = tax.load(args.mode)

    print(f"模式: {args.mode} | 体系: {taxonomy.name} | 标签数: {len(taxonomy)}", file=sys.stderr)

    # 读取输入：是路径则按文件/目录读，否则视为原始 JD 文本
    if os.path.exists(args.input):
        jds = read_input(args.input)
    else:
        jds = [args.input]
    if args.limit:
        jds = jds[: args.limit]
    print(f"待处理 JD 数: {len(jds)}", file=sys.stderr)

    # 抽取
    ext = Extractor(mode=args.mode, use_cache=not args.no_cache)
    results = ext.extract_many(jds, taxonomy)

    # 汇总：跨 JD 聚合技能计数 / 技能点计数 / 技能→技能点映射
    from collections import Counter, defaultdict
    agg_skill = Counter()
    agg_sp = Counter()
    agg_map = defaultdict(Counter)
    for r in results:
        for code, n in r.get("skill_counts", {}).items():
            agg_skill[code] += n
        for sp, n in r.get("skillpoint_counts", {}).items():
            agg_sp[sp] += n
        for code, sps in r.get("skill_skillpoint_map", {}).items():
            for sp, n in sps.items():
                agg_map[code][sp] += n

    out = {
        "mode": args.mode,
        "taxonomy": taxonomy.name,
        "num_jds": len(jds),
        "skill_counts": dict(agg_skill),
        "skillpoint_counts": dict(agg_sp),
        "skill_skillpoint_map": {c: dict(cnt) for c, cnt in agg_map.items()},
        "per_jd": results,
        "stats": ext.stats(),
    }
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"已输出: {args.output}", file=sys.stderr)
    else:
        print(json.dumps({k: out[k] for k in ("mode", "num_jds", "skill_counts",
                                              "skillpoint_counts", "skill_skillpoint_map", "stats")},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
