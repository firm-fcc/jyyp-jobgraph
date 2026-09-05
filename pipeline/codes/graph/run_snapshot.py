# -*- coding: utf-8 -*-
"""图谱时间截面快照 CLI：构建 / 枚举 / 校验。

用法（在模块目录下运行）：
  cd codes/graph
  python run_snapshot.py list                          # 列出已有时间截面
  python run_snapshot.py build --dry-run               # 预览默认窗口统计（不写文件）
  python run_snapshot.py build --window 2026-05        # 构建 2026-05 截面
  python run_snapshot.py build --window 2026-Q2 --force # 构建季度窗口（覆盖已有）
  python run_snapshot.py check --window 2026-05        # 结构校验

参数：
  --window YYYY-MM|YYYY-Qn|auto   时间窗口（默认 auto = 数据最大月份）
  --granularity month|quarter     窗口粒度（供显示；实际以 window 标签为准）
  --out PATH                      输出根目录（默认 data/graph）
  --dry-run                       只打印统计，不写文件
  --force                         覆盖已有窗口
"""
import argparse
import sys

import graph_config as config
from snapshot_builder import build_snapshot, auto_window, parse_window
from graph_snapshot import GraphSnapshot


def _print_stats(stats):
    for k, v in stats.items():
        print(f"  {k}: {v}")


def main():
    ap = argparse.ArgumentParser(description="图谱时间截面快照：构建 / 枚举 / 校验")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列出已有时间截面")
    p_list.add_argument("--out", default=None, help="根目录（默认 data/graph）")

    p_build = sub.add_parser("build", help="构建一个时间截面快照")
    p_build.add_argument("--window", default="auto",
                         help="时间窗口 YYYY-MM 或 YYYY-Qn（默认 auto=数据最大月份）")
    p_build.add_argument("--granularity", choices=["month", "quarter"], default=None,
                         help="窗口粒度（供显示；实际以 window 标签为准）")
    p_build.add_argument("--out", default=None, help="输出根目录（默认 data/graph）")
    p_build.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    p_build.add_argument("--force", action="store_true", help="覆盖已有窗口")
    p_build.add_argument("--papers-delta", default=None,
                         help="覆盖论文 ΔG 源路径（默认 classify/DeltaG/papers_delta.json）")
    p_build.add_argument("--news-delta", default=None,
                         help="覆盖新闻 ΔG 源路径（默认 classify/DeltaG/news_delta.json）")
    p_build.add_argument("--jd-delta", default=None,
                         help="覆盖 JD ΔG 源路径（默认 classify/DeltaG/jd_delta.json）")
    p_build.add_argument("--reset-base-edges", action="store_true",
                         help="force 重建时重置基图边为空 schema（默认保留已非空的边）")

    p_check = sub.add_parser("check", help="校验一个时间截面结构")
    p_check.add_argument("--window", required=True)
    p_check.add_argument("--out", default=None)

    args = ap.parse_args()
    out_root = args.out or config.GRAPH_ROOT

    if args.cmd == "list":
        slices = GraphSnapshot.list_slices(out_root)
        if not slices:
            print(f"[snapshot] 无时间截面（{out_root}）")
            return
        print(f"[snapshot] 已有 {len(slices)} 个时间截面（{out_root}）：")
        for w in slices:
            kind, start, end, _ = parse_window(w)
            print(f"  {w}  ({kind}, {start}..{end})")
        return

    if args.cmd == "build":
        window = args.window
        if window == "auto":
            window = auto_window()
            print(f"[snapshot] auto 窗口 → {window}")
        kind, start, end, _ = parse_window(window)
        print(f"[snapshot] 构建 {window}（{kind}，{start}..{end}）→ {out_root}"
              f"（{'dry-run 预览' if args.dry_run else '生成'}）")
        delta_files = None
        for flag, src in (("papers_delta", "papers"), ("news_delta", "news"), ("jd_delta", "jd")):
            override = getattr(args, flag, None)
            if override:
                delta_files = dict(delta_files or config.DELTA_FILES)
                delta_files[src] = override
        try:
            stats = build_snapshot(window, out_root=out_root,
                                   dry_run=args.dry_run, force=args.force,
                                   delta_files=delta_files,
                                   keep_base_edges=not args.reset_base_edges)
        except FileExistsError as e:
            print(f"[snapshot] 中止：{e}")
            sys.exit(1)
        _print_stats(stats)
        print("完成。")
        return

    if args.cmd == "check":
        snap = GraphSnapshot.load(args.window, out_root)
        errs = snap.validate()
        s = snap.summary()
        print(f"[snapshot] 校验 {args.window}：")
        print(f"  窗口 {s['window']}  period {s['period'][0]}..{s['period'][1]}")
        print(f"  缺文件/解析失败：{len(s['missing'])}")
        for m in s["missing"]:
            print(f"    ! {m}")
        if errs:
            print(f"  结构错误：{len(errs)}")
            for e in errs:
                print(f"    ✗ {e}")
            sys.exit(1)
        print("  ✓ 全部通过")
        return


if __name__ == "__main__":
    main()
