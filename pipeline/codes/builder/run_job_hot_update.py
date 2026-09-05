# -*- coding: utf-8 -*-
"""岗位热更新 CLI：消费 ΔG 增量层的 pending 新岗位，LLM 关联分析并回填任务/技能。

用法（在模块目录下运行）：
  cd codes/builder
  python run_job_hot_update.py --dry-run                          # 列出各文件 pending 新岗位（不调 LLM）
  python run_job_hot_update.py --delta classify/DeltaG/papers_delta.json   # 仅处理指定文件
  python run_job_hot_update.py --limit 3                          # 探索：只处理前 3 个，写 *_explore.json
  python run_job_hot_update.py                                    # 全部（papers + news）

参数：
  --source papers|news|all   数据源（默认 all；与 --delta 同用时作 source_kind 覆盖）
  --delta PATH               仅处理指定 ΔG 文件（source_kind 按文件名推断）
  --output PATH              输出路径（默认就地回填；--source all 时不可用）
  --limit N                  探索：只处理前 N 个 pending 岗位，写 {输出}_explore.json
  --dry-run                  只列出 pending 新岗位及证据规模，不调 LLM
  --api-key KEY              覆盖 codes/api-key.txt
  --max-tokens N             提取/映射 max_tokens
"""
import argparse
import os
import sys

# 先导入 builder 自己的 config（sys.modules["config"] 缓存为 builder 版），
# 防止 job_hot_update 跨模块 sys.path 插入后 `import config` 误解析到 extractor 的 config。
import config  # noqa: F401
from job_hot_update import run_pipeline, infer_source_kind


def _delta_files(source, delta):
    """确定要处理的 (ΔG 文件, source_kind) 列表。"""
    if delta:
        return [(delta, infer_source_kind(delta, source))]
    if source == "papers":
        return [(config.DELTA_OUTPUT, "papers")]
    if source == "news":
        return [(config.NEWS_DELTA_OUTPUT, "news")]
    if source == "jd":
        return [(config.JD_DELTA_OUTPUT, "jd")]
    return [(config.DELTA_OUTPUT, "papers"), (config.NEWS_DELTA_OUTPUT, "news"),
            (config.JD_DELTA_OUTPUT, "jd")]


def main():
    ap = argparse.ArgumentParser(description="岗位热更新：ΔG 新岗位 → 任务/技能关联分析")
    ap.add_argument("--source", choices=["papers", "news", "jd", "all"], default="all")
    ap.add_argument("--delta", default=None, help="仅处理指定 ΔG 文件")
    ap.add_argument("--output", default=None, help="输出路径（默认就地回填）")
    ap.add_argument("--limit", type=int, default=None, help="探索：只处理前 N 个 pending 岗位")
    ap.add_argument("--dry-run", action="store_true", help="只列出 pending 新岗位，不调 LLM")
    ap.add_argument("--api-key", default=None, help="覆盖 codes/api-key.txt")
    ap.add_argument("--max-tokens", type=int, default=None, help="提取/映射 max_tokens")
    ap.add_argument("--window", default=None,
                    help="窗口驱动运行（YYYY-MM）：关联分析新出生条目 born_window 盖窗口月（缺省误盖运行日）")
    args = ap.parse_args()

    files = _delta_files(args.source, args.delta)
    if args.output and len(files) > 1:
        raise SystemExit("--output 仅适用于单个 ΔG 文件（请配合 --delta 或 --source papers|news）")

    for path, sk in files:
        if not os.path.exists(path):
            print(f"[job_hot] 跳过（文件不存在，请先运行对应 ΔG 流水线）：{path}")
            continue
        print(f"\n[job_hot] 处理 {path}（source_kind={sk}）")
        run_pipeline(path, source_kind=sk, output=args.output, limit=args.limit,
                     api_key=args.api_key, max_tokens=args.max_tokens, dry_run=args.dry_run,
                     window=args.window)
    print("完成。")


if __name__ == "__main__":
    main()
