# -*- coding: utf-8 -*-
"""转正 CLI：叠层强信号 + JD 确证 → 基准体系文件。

用法（在模块目录下运行）：
  cd codes/builder
  python run_promotion.py --dry-run                # 只列候选，不写任何文件
  python run_promotion.py                          # 执行转正（先备份再写入）
  python run_promotion.py --as-of 2022-07-31       # 逐窗回放：以窗末为衰减基准
  python run_promotion.py --min-strength 0.2 --min-jd-docs 1   # 临时降门槛（评估用）

参数：
  --dry-run             只评估打印候选，不写基准文件/不标记 ΔG
  --as-of YYYY-MM-DD    强度衰减基准日（缺省今天；逐窗回放必须传窗末日，
                        否则历史证据被深度衰减错杀候选）
  --min-strength F      覆盖任务/技能转正强度门槛
  --min-jd-docs N       覆盖任务/技能 JD 确证文档数门槛
  --min-strength-jobs F / --min-jd-docs-jobs N    覆盖岗位门槛
  --papers-delta/--news-delta/--jd-delta PATH     覆盖 ΔG 源路径（评估实验用）
"""
import argparse
import json
import sys
from datetime import date

import config  # noqa: F401  先缓存 builder 版 config
from promotion import run_promotion


def main():
    ap = argparse.ArgumentParser(description="叠层转正：强信号 + JD 确证 → 基准体系")
    ap.add_argument("--dry-run", action="store_true", help="只评估候选，不写入")
    ap.add_argument("--min-strength", type=float, default=None)
    ap.add_argument("--min-jd-docs", type=int, default=None)
    ap.add_argument("--min-strength-jobs", type=float, default=None)
    ap.add_argument("--min-jd-docs-jobs", type=int, default=None)
    ap.add_argument("--papers-delta", default=None)
    ap.add_argument("--news-delta", default=None)
    ap.add_argument("--jd-delta", default=None)
    ap.add_argument("--graduated-window", default=None,
                    help="转正生效窗 YYYY-MM（缺省 as-of 次月=常规语义；回溯特例显式传 W）")
    ap.add_argument("--as-of", default=None,
                    help="强度衰减基准日 YYYY-MM-DD（缺省今天；逐窗回放必须传窗末日，"
                         "否则历史证据被深度衰减错杀）")
    args = ap.parse_args()

    delta_files = None
    if args.papers_delta or args.news_delta or args.jd_delta:
        delta_files = dict(config.DELTA_FILES)
        for flag, src in (("papers_delta", "papers"), ("news_delta", "news"), ("jd_delta", "jd")):
            v = getattr(args, flag)
            if v:
                delta_files[src] = v

    now = None
    if args.as_of:
        try:
            now = date.fromisoformat(args.as_of)
        except ValueError:
            sys.exit(f"--as-of 需为 YYYY-MM-DD：{args.as_of}")

    report = run_promotion(delta_files=delta_files, dry_run=args.dry_run,
                           min_strength=args.min_strength, min_jd_docs=args.min_jd_docs,
                           min_strength_jobs=args.min_strength_jobs,
                           min_jd_docs_jobs=args.min_jd_docs_jobs, now=now,
                           graduated_window=args.graduated_window)
    if not report.get("dry_run"):
        print(json.dumps({k: v for k, v in report.items() if k != "candidates"},
                         ensure_ascii=False, indent=1))
        # 转正后类别归纳（旁路环节）：新岗位 category 在 _write_jobs 落盘时为空，
        # 收口后立即补齐；非 tty 环境只提示人工运行，绝不静默调 LLM/写基准
        new_jobs = [p for p in report.get("promoted", []) if p.get("array") == "new_jobs"]
        if new_jobs:
            if sys.stdin.isatty() and sys.stdout.isatty():
                from job_categorize import categorize
                print(f"\n[promote] {len(new_jobs)} 个新转正岗位，进入类别归纳（回车接受建议/"
                      "输入 code 改判/s 跳过）：")
                categorize()
            else:
                print(f"\n[promote] {len(new_jobs)} 个新转正岗位待归类，"
                      f"请人工运行：cd codes/builder && python job_categorize.py")
    print("完成。" if not report.get("dry_run") else "dry-run 评估完成（未写入任何文件）。")


if __name__ == "__main__":
    main()
