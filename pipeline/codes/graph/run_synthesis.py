# -*- coding: utf-8 -*-
"""图谱合成 CLI：G_eff = G_base ⊕ ΔG → data/graph/{窗口}/effective/。

用法（在模块目录下运行）：
  cd codes/graph
  python run_synthesis.py build --window 2026-05 --dry-run   # 预览合成统计（不写文件）
  python run_synthesis.py build --window 2026-05             # 合成（覆盖 effective/，可重算）
  python run_synthesis.py check --window 2026-05             # 校验合成层（端点/total/origin）

合成只读 base/ 与 delta/、只写 effective/（独立存储）；纯 stdlib 零 LLM。
"""
import argparse
import sys

from synthesis import synthesize, validate_effective


def main():
    ap = argparse.ArgumentParser(description="图谱合成：G_eff = G_base ⊕ ΔG")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="合成一个窗口的 G_eff")
    p_build.add_argument("--window", required=True, help="时间窗口 YYYY-MM 或 YYYY-Qn")
    p_build.add_argument("--out", default=None, help="快照根目录（默认 data/graph）")
    p_build.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")

    p_check = sub.add_parser("check", help="校验合成层结构")
    p_check.add_argument("--window", required=True)
    p_check.add_argument("--out", default=None)

    args = ap.parse_args()

    if args.cmd == "build":
        try:
            stats = synthesize(args.window, out_root=args.out, dry_run=args.dry_run)
        except FileNotFoundError as e:
            print(f"[synth] 中止：{e}")
            sys.exit(1)
        for k, v in stats.items():
            print(f"  {k}: {v}")
        print("完成。" if not args.dry_run else "dry-run 预览完成（未写文件）。")
        return

    errs = validate_effective(args.window, args.out)
    if errs:
        print(f"[synth] 校验 {args.window}：{len(errs)} 处错误")
        for e in errs:
            print(f"  ✗ {e}")
        sys.exit(1)
    print(f"[synth] 校验 {args.window}：✓ 全部通过")


if __name__ == "__main__":
    main()
