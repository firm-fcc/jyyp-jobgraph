# -*- coding: utf-8 -*-
"""新闻 ΔG 热更新 CLI 入口（builder 热更新层）。

用法（在模块目录下运行）：
  cd codes/builder
  python run_news_delta.py --limit 20 --dry-run     # 只解析 + 来源分布预览（不调 LLM）
  python run_news_delta.py --limit 5                # 小样本 live（探索，独立产物）
  python run_news_delta.py                          # 全量（默认断点续跑）
  python run_news_delta.py --source 量子位 --no-resume

参数：
  --news-dir PATH     新闻数据目录（默认 data/news/news_raw）
  --source NAME       仅处理该公众号
  --limit N           仅处理 N 篇（探索用；写入独立增量文件，不动主断点/主产物）
  --chunk N           单次提取 LLM 调用的新闻数（默认 3）
  --output PATH       新闻 ΔG 增量文件输出路径（默认 classify/DeltaG/news_delta.json）
  --log PREFIX        日志前缀（默认 classify/DeltaG/news_delta_log.{jsonl,md}）
  --api-key KEY       覆盖 codes/api-key.txt
  --max-tokens N      提取/映射 max_tokens
  --filter-tokens N   相关性过滤 max_tokens（默认 300）
  --no-resume         不恢复断点（默认自动恢复）
  --dry-run           只解析 + 来源分布预览，不调用 LLM
"""
import argparse
from collections import Counter

# 必须先于 news_delta 导入 config：缓存 builder 的 config 于 sys.modules，
# 防止 news_delta 跨模块 sys.path 插入后 `import config` 误解析到 extractor 的 config。
import config  # noqa: F401
from news_delta import run_pipeline, scan_news, news_config


def print_sampling_plan(news_dir, source, limit):
    """只解析 + 来源分布预览（免费，不调 LLM）。"""
    records = scan_news(news_dir, source=source, limit=limit)
    if not records:
        print("未找到新闻。请检查 --news-dir 与 --source。")
        return
    print(f"解析新闻: {len(records)} 篇（{news_dir}）")
    src_cnt = Counter(r.source for r in records)
    print("来源分布: " + ", ".join(f"{k}={v}" for k, v in src_cnt.most_common(6)))
    print("过滤策略：全量进入 LLM 相关性过滤（无关键词门槛，方案 B）")


def main():
    ap = argparse.ArgumentParser(description="新闻驱动 ΔG 增量层：相关性过滤 → 信号提取/提及 → 增量聚合")
    ap.add_argument("--news-dir", default=None, help="新闻数据目录")
    ap.add_argument("--source", default=None, help="仅处理该公众号")
    ap.add_argument("--limit", type=int, default=None, help="仅处理 N 篇（探索用）")
    ap.add_argument("--chunk", type=int, default=None, help="单次提取 LLM 调用的新闻数")
    ap.add_argument("--output", default=None, help="新闻 ΔG 输出路径")
    ap.add_argument("--log", default=None, help="日志前缀")
    ap.add_argument("--api-key", default=None, help="覆盖 codes/api-key.txt")
    ap.add_argument("--max-tokens", type=int, default=None, help="提取/映射 max_tokens")
    ap.add_argument("--filter-tokens", type=int, default=None, help="标题过滤 max_tokens（默认 300）")
    ap.add_argument("--no-resume", action="store_true", help="不恢复断点")
    ap.add_argument("--window", default=None,
                    help="时间窗口 YYYY-MM：只处理 pub_date ≤ 窗末的新闻（逐窗时序，断点自动衔接）")
    ap.add_argument("--dry-run", action="store_true", help="只解析 + 来源分布预览，不调 LLM")
    args = ap.parse_args()

    news_dir = args.news_dir or news_config.NEWS_DIR
    if args.dry_run:
        print_sampling_plan(news_dir, args.source, args.limit)
        return

    run_pipeline(
        news_dir=news_dir, source=args.source, limit=args.limit, chunk=args.chunk,
        output=args.output, log_prefix=args.log, api_key=args.api_key,
        max_tokens=args.max_tokens, filter_max_tokens=args.filter_tokens,
        resume=not args.no_resume, window=args.window,
    )


if __name__ == "__main__":
    main()
