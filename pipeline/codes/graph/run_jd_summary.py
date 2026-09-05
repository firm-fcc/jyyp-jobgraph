# -*- coding: utf-8 -*-
"""JD 多维分类汇总 CSV 生成（独立运行；Stage D 也会自动调用）。

用法：python run_jd_summary.py --window 2025-10 [--out-dir DIR]
"""
import argparse
import sys

from jd_summary import write_summary_csv


def main():
    ap = argparse.ArgumentParser(description="JD 多维分类汇总 CSV（读 jd_vectors → CSV）")
    ap.add_argument("--window", required=True, help="窗口（YYYY-MM，如 2025-10）")
    ap.add_argument("--out-dir", default="", help="输出目录（默认 data/graph/data/）")
    args = ap.parse_args()
    path, n = write_summary_csv(args.window, args.out_dir or None)
    print(f"汇总 CSV 已写出：{path}（{n} 行 JD）")


if __name__ == "__main__":
    sys.exit(main())
