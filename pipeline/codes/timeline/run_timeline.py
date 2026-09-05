# -*- coding: utf-8 -*-
"""时间线编排器 CLI：把 JD / 新闻 / 论文 按时间戳统一编排，供图谱按时间顺序导入。

用法（在模块目录下运行）：
  cd codes/timeline
  python run_timeline.py --dry-run              # 只打印三类数据规模/时间分布计划（不写文件）
  python run_timeline.py --jd                   # 只生成 JD 月度文件
  python run_timeline.py --news --papers        # 只生成新闻/论文映射表
  python run_timeline.py --limit 100            # 探索：每源只处理 100 条
  python run_timeline.py                        # 全部生成

参数：
  --jd / --news / --papers   选择要编排的数据源（默认全部）
  --out PATH                 输出根目录（默认 data/timeline）
  --jd-dir PATH              覆盖 JD 数据目录
  --news-dir PATH            覆盖新闻数据目录
  --papers-dir PATH          覆盖论文数据目录
  --dry-run                  只打印规模/分布计划，不写任何文件
  --limit N                  每源限制处理条数（探索用）
"""
import argparse

import timeline_config as config
from timeline_builder import (
    build_jd_timeline, build_news_mapping, build_papers_mapping,
)


def _print_stats(tag, stats):
    print(f"=== {tag} ===")
    for k, v in stats.items():
        if k == "months" and isinstance(v, dict):
            if not v:
                print("  月份分布: (空)")
                continue
            m0, m1 = min(v), max(v)
            print(f"  月份分布: {m0}..{m1} ({len(v)} 个月)，最大 {max(v, key=v.get)}={v[max(v, key=v.get)]}")
        elif k == "tiers" and isinstance(v, dict):
            print(f"  分档: {', '.join(f'{t}:{c}' for t, c in sorted(v.items()))}")
        else:
            print(f"  {k}: {v}")


def main():
    ap = argparse.ArgumentParser(description="时间线编排器：JD 按月分组 + 新闻/论文映射表")
    ap.add_argument("--jd", action="store_true", help="编排 JD（按月分组）")
    ap.add_argument("--news", action="store_true", help="编排新闻（映射表）")
    ap.add_argument("--papers", action="store_true", help="编排论文（映射表）")
    ap.add_argument("--out", default=None, help="输出根目录（默认 data/timeline）")
    ap.add_argument("--jd-dir", default=None, help="覆盖 JD 数据目录")
    ap.add_argument("--news-dir", default=None, help="覆盖新闻数据目录")
    ap.add_argument("--papers-dir", default=None, help="覆盖论文数据目录")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件")
    ap.add_argument("--limit", type=int, default=None, help="每源限制处理条数（探索用）")
    args = ap.parse_args()

    # 默认全部数据源
    flags = {"jd": args.jd, "news": args.news, "papers": args.papers}
    if not any(flags.values()):
        flags = {"jd": True, "news": True, "papers": True}
    out_dir = args.out or config.TIMELINE_DIR
    mode = "dry-run 计划" if args.dry_run else "生成"

    print(f"[timeline] {mode} → {out_dir}（limit={args.limit}）")

    if flags["jd"]:
        stats = build_jd_timeline(jd_dir=args.jd_dir, out_dir=out_dir,
                                  limit=args.limit, dry_run=args.dry_run)
        _print_stats("JD", stats)
    if flags["news"]:
        stats = build_news_mapping(news_dir=args.news_dir, out_dir=out_dir,
                                   limit=args.limit, dry_run=args.dry_run)
        _print_stats("NEWS", stats)
    if flags["papers"]:
        stats = build_papers_mapping(papers_dir=args.papers_dir, out_dir=out_dir,
                                     limit=args.limit, dry_run=args.dry_run)
        _print_stats("PAPERS", stats)

    print("完成。")


if __name__ == "__main__":
    main()
