# -*- coding: utf-8 -*-
"""论文 ΔG 热更新 CLI 入口（builder 热更新层；迁自 run_paper_signal.py）。

用法（在模块目录下运行）：
  cd codes/builder
  python run_paper_delta.py --tier S --limit 20 --dry-run    # 只解析+预览抽样方案（不调 LLM）
  python run_paper_delta.py --tier S                         # S 档全量信号提取 → ΔG 增量层
  python run_paper_delta.py --papers-dir PATH --no-resume    # 指定目录 / 不恢复断点

参数：
  --papers-dir PATH  论文数据目录（默认 data/papers/专题三_数据质量与多源融合）
  --tier S|A|B       仅处理该分档（默认全部存在分档）
  --limit N          仅处理 N 篇（探索用；写入独立增量文件，不动主断点）
  --stratum tier|tier_dim  分层键：分档（默认）或 分档|主维度
  --chunk N          单次提取 LLM 调用包含的论文数（默认 3）
  --output PATH      ΔG 增量文件输出路径（默认 classify/DeltaG/papers_delta.json）
  --log PREFIX       日志前缀（默认 classify/DeltaG/paper_signal_log.{jsonl,md}）
  --api-key KEY      覆盖 codes/api-key.txt
  --max-tokens N     覆盖 LLM max_tokens
  --no-resume        不恢复断点（默认自动恢复）
  --dry-run          只解析论文+打印抽样方案，不调用 LLM
"""
import argparse
from collections import Counter

# 必须先于 paper_delta 导入 config：缓存 builder 的 config 于 sys.modules，
# 防止 paper_delta 跨模块 sys.path 插入后 `import config` 误解析到 extractor 的 config。
import config  # noqa: F401
from paper_delta import run_pipeline, scan_papers, paper_config


def print_sampling_plan(papers_dir, tier, limit, stratum):
    """只解析论文，打印规模与分层统计（不调用 LLM）。"""
    records = scan_papers(papers_dir, tier=tier, limit=limit)
    if not records:
        print("未找到论文。请检查 --papers-dir 与 --tier。")
        return
    tier_cnt = Counter(r.tier for r in records)
    dim_cnt = Counter((r.dimensions[0] if r.dimensions else "无维度") for r in records)
    print(f"解析论文: {len(records)} 篇（{papers_dir}）")
    print(f"  分档分布: " + ", ".join(f"{k}={v}" for k, v in sorted(tier_cnt.items())))
    if stratum == "tier_dim":
        print(f"  主维度分布: " + ", ".join(f"{k}={v}" for k, v in dim_cnt.most_common(8)))
    print("\n抽查解析质量：")
    for rec in records[:3]:
        print(f"\n  --- {rec.source_file} ---")
        print(f"  arxiv={rec.arxiv_id} is_arxiv={rec.is_arxiv} tier={rec.tier} score={rec.score}")
        print(f"  title={rec.title[:70]}")
        print(f"  pub_date={rec.pub_date} dimensions={rec.dimensions}")
        print(f"  evidence={len(rec.evidence_sentences)} 条")
        print(f"  keywords={rec.keywords}")
        if rec.abstract:
            print(f"  abstract({len(rec.abstract)}字符): {rec.abstract[:80]}...")
        else:
            print(f"  abstract=无")


def main():
    ap = argparse.ArgumentParser(description="论文驱动 ΔG 增量层：提取岗位/任务/技能前瞻信号")
    ap.add_argument("--papers-dir", default=None, help="论文数据目录")
    ap.add_argument("--tier", default=None, choices=["S", "A", "B"], help="仅处理该分档")
    ap.add_argument("--limit", type=int, default=None, help="仅处理 N 篇（探索用）")
    ap.add_argument("--stratum", default="tier", choices=["tier", "tier_dim"],
                    help="分层键：分档或 分档|主维度")
    ap.add_argument("--chunk", type=int, default=None, help="单次提取 LLM 调用的论文数")
    ap.add_argument("--output", default=None, help="ΔG 增量文件输出路径")
    ap.add_argument("--log", default=None, help="日志前缀")
    ap.add_argument("--api-key", default=None, help="覆盖 codes/api-key.txt")
    ap.add_argument("--max-tokens", type=int, default=None, help="覆盖 LLM max_tokens")
    ap.add_argument("--no-resume", action="store_true", help="不恢复断点")
    ap.add_argument("--no-mention", action="store_true",
                    help="跳过 Stage C（基线提及并入 strengthenings）")
    ap.add_argument("--window", default=None,
                    help="时间窗口 YYYY-MM：只处理 pub_date ≤ 窗末的论文（逐窗时序，断点自动衔接）")
    ap.add_argument("--dry-run", action="store_true", help="只解析+预览，不调 LLM")
    args = ap.parse_args()

    papers_dir = args.papers_dir or paper_config.PAPER_DIR
    if args.dry_run:
        print_sampling_plan(papers_dir, args.tier, args.limit, args.stratum)
        return

    run_pipeline(
        papers_dir=papers_dir, tier=args.tier, limit=args.limit,
        stratum=args.stratum, chunk=args.chunk, output=args.output,
        log_prefix=args.log, api_key=args.api_key, max_tokens=args.max_tokens,
        resume=not args.no_resume, no_mention=args.no_mention, window=args.window,
    )


if __name__ == "__main__":
    main()
