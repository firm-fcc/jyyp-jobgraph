# -*- coding: utf-8 -*-
"""JD 侧 ΔG 热更新 CLI 入口（builder 热更新层）。

**已弃用（2026-08-27）**：100 条/窗的抽样投喂对低频新信号漏检率过高（0.1% 出现率
漏检 ~90%）。生产路径改为 `codes/graph/jd_delta_v2.py`（全量确定性扫描 + 残差 LLM 裁决，
发现覆盖率=100% IT JD，LLM 量=残差候选数）；本入口保留仅为兼容与对照。

用法（在模块目录下运行）：
  cd codes/builder
  python run_jd_delta.py --window 2026-05 --dry-run      # 只解析 + 抽样分布预览（不调 LLM）
  python run_jd_delta.py --window 2026-05 --limit 30     # 小样本 live（探索，独立产物）
  python run_jd_delta.py --window 2026-05                # 全量（默认断点续跑）

参数：
  --window YYYY-MM       时间窗口（必填；JD 文件默认 data/timeline/jd/{window}.csv）
  --jd-csv PATH          覆盖默认 JD 月度文件
  --sample N             抽样总量上限（默认 settings.yaml → jd.sample_total）
  --per-funtype N        每 funtype 抽样上限
  --limit N              仅处理 N 条（探索用；写入独立增量文件，不动主断点/主产物）
  --chunk N              单次提取 LLM 调用的 JD 条数
  --output PATH          JD ΔG 输出路径（默认 classify/DeltaG/jd_delta.json）
  --log PREFIX           日志前缀
  --api-key KEY          覆盖 codes/api-key.txt
  --max-tokens N         提取/映射 max_tokens
  --no-resume            不恢复断点（默认自动恢复）
  --dry-run              只解析 + 抽样预览，不调用 LLM
"""
import argparse

# 必须先于 jd_delta 导入 config：缓存 builder 的 config 于 sys.modules。
import config  # noqa: F401
from jd_delta import run_pipeline


def main():
    ap = argparse.ArgumentParser(description="JD 侧 ΔG 热更新：市场确证 → 叠层（权重 1.0）")
    ap.add_argument("--window", required=True, help="时间窗口 YYYY-MM")
    ap.add_argument("--jd-csv", default=None, help="JD 月度 CSV（默认 data/timeline/jd/{window}.csv）")
    ap.add_argument("--sample", type=int, default=None, help="抽样总量上限")
    ap.add_argument("--per-funtype", type=int, default=None, help="每 funtype 抽样上限")
    ap.add_argument("--limit", type=int, default=None, help="仅处理 N 条（探索用）")
    ap.add_argument("--chunk", type=int, default=None, help="单次提取 LLM 调用的 JD 条数")
    ap.add_argument("--output", default=None, help="JD ΔG 输出路径")
    ap.add_argument("--log", default=None, help="日志前缀")
    ap.add_argument("--api-key", default=None, help="覆盖 codes/api-key.txt")
    ap.add_argument("--max-tokens", type=int, default=None, help="提取/映射 max_tokens")
    ap.add_argument("--no-resume", action="store_true", help="不恢复断点")
    ap.add_argument("--dry-run", action="store_true", help="只解析 + 预览，不调 LLM")
    args = ap.parse_args()

    run_pipeline(
        window=args.window, jd_csv=args.jd_csv, sample_total=args.sample,
        per_funtype=args.per_funtype, limit=args.limit, chunk=args.chunk,
        output=args.output, log_prefix=args.log, api_key=args.api_key,
        max_tokens=args.max_tokens, resume=not args.no_resume, dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
