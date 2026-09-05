# -*- coding: utf-8 -*-
"""基图边计算 CLI：JD 月度 CSV → data/graph/{窗口}/base/ 四种边。

用法（在模块目录下运行）：
  cd codes/graph
  python run_base_build.py --window 2026-05 --dry-run   # 预览抽样分布（不调 LLM 不写文件）
  python run_base_build.py --window 2026-05             # 抽样 200 条 JD 构建基图边（LLM）
  python run_base_build.py --window 2026-05 --force     # 覆盖已非空的基图边

参数：
  --window YYYY-MM|YYYY-Qn   时间窗口（必填；JD 文件默认 data/timeline/jd/{window}.csv）
  --jd-csv PATH              覆盖默认 JD 月度文件
  --sample N                 抽样总量上限（默认 settings.yaml → graph_base.sample_total）
  --per-job N                每岗位抽样上限
  --no-salary-weight         关闭薪资加权（全部权重 1.0）
  --prev-window LABEL|none   上一窗口（默认 auto = 上一月/季；none 关闭历史衰减）
  --dry-run                  只解析+抽样预览，不调 LLM 不写文件
  --force                    基图边已非空时允许覆盖
"""
import argparse
import sys

from base_builder import build_base


def main():
    ap = argparse.ArgumentParser(description="基图边计算：JD → J-T/J-S/T-S/S-SP")
    ap.add_argument("--window", required=True, help="时间窗口 YYYY-MM 或 YYYY-Qn")
    ap.add_argument("--jd-csv", default=None, help="JD 月度 CSV（默认 data/timeline/jd/{window}.csv）")
    ap.add_argument("--sample", type=int, default=None, help="抽样总量上限")
    ap.add_argument("--per-job", type=int, default=None, help="每岗位抽样上限")
    ap.add_argument("--no-salary-weight", action="store_true", help="关闭薪资加权")
    ap.add_argument("--prev-window", default="auto",
                    help="上一窗口标签（auto=自动推导；none=关闭历史衰减）")
    ap.add_argument("--dry-run", action="store_true", help="只预览抽样，不调 LLM 不写文件")
    ap.add_argument("--force", action="store_true", help="覆盖已非空的基图边")
    args = ap.parse_args()

    try:
        stats = build_base(args.window, jd_csv=args.jd_csv, sample_total=args.sample,
                           per_job=args.per_job,
                           salary_weight=False if args.no_salary_weight else None,
                           prev_window=None if args.prev_window == "none" else args.prev_window,
                           dry_run=args.dry_run, force=args.force)
    except (FileExistsError, FileNotFoundError) as e:
        print(f"[base] 中止：{e}")
        sys.exit(1)
    if args.dry_run:
        print("dry-run 预览完成（未调 LLM、未写文件）。")
    else:
        print("完成。")
    return stats


if __name__ == "__main__":
    main()
