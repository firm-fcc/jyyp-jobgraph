# -*- coding: utf-8 -*-
"""Builder CLI 入口。

用法：
  python run_builder.py --action cold          # 冷启动
  python run_builder.py --action hot           # 热更新（需已有任务体系）
  python run_builder.py --action full          # 冷启动 + 热更新
  python run_builder.py --action cold --sample 80 --source jd --taxonomy path/to/tasks.json
  python run_builder.py --dry-run              # 只预览抽样方案（不调用 LLM）

参数：
  --action cold|hot|full   执行动作（默认 full）
  --mode task|skill        构建任务体系（默认）或技能体系
  --source jd              数据源类型（预留：news/paper/resume）
  --sample N               冷启动采样条数
  --rounds N               热更新最大轮数
  --batch N                热更新每轮投喂条数
  --chunk N                热更新单次提案交 LLM 的条数（子块大小，默认 50）
  --taxonomy PATH          任务体系输出路径
  --log PATH               跟踪日志前缀（默认 classify/Tasks/builder_log.{jsonl,md}）
  --no-resume              热更新不恢复断点（从头抽样，默认自动恢复断点继续）
  --dry-run                只预览分层抽样方案（数据规模 + 各层配额），不调用 LLM
"""
import argparse
import sys

import config
from builder import Builder


def print_sampling_plan(ds):
    """预览抽样方案（不调用 LLM）：数据规模 + 冷启动/热更新各层配额。"""
    if not hasattr(ds, "preview") or not hasattr(ds, "statistics"):
        print("当前数据源不支持抽样预览（仅 jd 分层抽样器支持）")
        return
    stats = ds.statistics(exclude_consumed=False)
    print(f"数据总量（去重后）: {stats['total']} 条，岗位层: {len(stats['strata'])} 个")
    print(f"各层规模: " + ", ".join(
        f"{s}={c}" for s, c in sorted(stats['strata'].items(), key=lambda kv: -kv[1])[:8]
    ) + " ...")

    cold = ds.preview(config.COLD_SAMPLE, "min_coverage")
    print(f"\n[冷启动] 采样 {config.COLD_SAMPLE} 条（min_coverage，每层≥{config.MIN_PER_STRATUM}）")
    print(f"  覆盖层数: {len(cold)}，合计: {sum(cold.values())}")
    for s, t in list(cold.items())[:12]:
        print(f"    {s}: {t}")
    if len(cold) > 12:
        print(f"    ... 其余 {len(cold) - 12} 层")

    hot = ds.preview(config.HOT_BATCH, "proportional")
    print(f"\n[热更新] 每批 {config.HOT_BATCH} 条（proportional，按层规模占比）")
    print(f"  覆盖层数: {len(hot)}，合计: {sum(hot.values())}")
    for s, t in list(hot.items())[:10]:
        print(f"    {s}: {t}")
    if len(hot) > 10:
        print(f"    ... 其余 {len(hot) - 10} 层")


def main():
    ap = argparse.ArgumentParser(description="Builder：构建/更新任务体系或技能体系")
    ap.add_argument("--action", default="full", choices=["cold", "hot", "full"])
    ap.add_argument("--mode", default="task", choices=["task", "skill"],
                    help="构建任务体系（默认）或技能体系")
    ap.add_argument("--source", default="jd", help="数据源类型（jd；预留 news/paper）")
    ap.add_argument("--sample", type=int, default=None, help="冷启动采样条数")
    ap.add_argument("--rounds", type=int, default=None, help="热更新最大轮数")
    ap.add_argument("--batch", type=int, default=None, help="热更新每轮投喂条数")
    ap.add_argument("--chunk", type=int, default=None, help="热更新单次提案交 LLM 的条数（子块大小，默认 50）")
    ap.add_argument("--taxonomy", default=None, help="任务体系输出路径")
    ap.add_argument("--log", default=None,
                    help="跟踪日志前缀（默认 classify/Tasks/builder_log.{jsonl,md}）")
    ap.add_argument("--no-resume", action="store_true", help="热更新不恢复断点（从头抽样）")
    ap.add_argument("--dry-run", action="store_true", help="只预览抽样方案（不调用 LLM）")
    args = ap.parse_args()

    builder = Builder(source=args.source, mode=args.mode, taxonomy_path=args.taxonomy,
                      log_path=args.log)

    if args.dry_run:
        print_sampling_plan(builder.data_source)
        return

    if args.action == "cold":
        builder.cold_start(args.sample)
    elif args.action == "hot":
        # 热更新需基于已有体系；若无则提示先冷启动
        builder.taxonomy_store.load()
        if not builder.taxonomy_store.tasks():
            print("警告：当前无任务体系，热更新无意义。建议先 --action cold", file=sys.stderr)
        builder.hot_update(args.rounds, args.batch, chunk_size=args.chunk,
                           resume=not args.no_resume)
    else:  # full
        builder.full(args.sample, args.rounds)


if __name__ == "__main__":
    main()
