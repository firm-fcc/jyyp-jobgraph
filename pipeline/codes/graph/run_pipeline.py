# -*- coding: utf-8 -*-
"""逐窗时序编排：模拟数据到达顺序的基图构建流程（也是后续数据更新的固定流程）。

每个窗口（月）按序执行，不做"全历史先标注后处理"：
  0. Stage S0 预抽样（大窗 unique 指纹 > precap 才触发：确定性哈希选 precap 个，
     A 门只归类已选键 → jobcls/S/D0/B/v2 全链受限；w0=N/k 由 Stage S 复合进权重）
  1. Stage A 岗位归类（classify_job --strict，全量该窗；指纹缓存跨窗累积，同文只判一次）
  2. Stage D0 近重复（抄袭）过滤（jd_dedup，零 LLM：simhash+Jaccard 抄袭簇，产 {窗口}.dedup.json）
  3. Stage S 降采样（jd_sample，零 LLM：该窗 IT 超 cap 才降采，变体先剔除，产 {窗口}.sample.json）
  4. Stage B 抽取（run_jd_extract：非IT/范围外/抄袭变体/非采样键跳过 → jd_vectors 源文件）
  5. Stage C 熟练度（run_jd_proficiency --from-vectors → 回填 skill_vec_prof）
  6. Stage D 基图聚合（run_base_build：sample_weight×salary_weight 加权，α 链接上窗；变体在线过滤）
  7.（可选，本编排不代跑）叠层/ΔG：JD 侧信号 python codes/graph/jd_delta_v2.py --window ...

幂等/断点：各步自带缓存或存在性守卫（A 指纹缓存 / S 文件存在跳过 / B 源文件存在跳过 /
C 证据缓存 / D 边文件存在拒覆盖），重跑本编排只补未完成部分；--force-b/--force-d 显式重建。

用法：
  python codes/graph/run_pipeline.py --window 2025-10 --prev-window none   # 时序首批
  python codes/graph/run_pipeline.py --window 2025-11                       # 常规月度更新
  python codes/graph/run_pipeline.py --window 2022-08 --cap 10000           # 大窗降采样
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))


def run_step(title, cmd, extra_args=()):
    cmd = [sys.executable, "-X", "utf8"] + cmd + list(extra_args)
    print(f"\n{'=' * 60}\n[run_pipeline] {title}\n  {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, cwd=REPO)
    if r.returncode != 0:
        sys.exit(f"[run_pipeline] 步骤失败（exit {r.returncode}）：{title}")


def main():
    ap = argparse.ArgumentParser(description="逐窗时序基图流程：A→S→B→C→D")
    ap.add_argument("--window", required=True, help="窗口（YYYY-MM，如 2025-11）")
    ap.add_argument("--prev-window", default="auto",
                    help="Stage D 的上一窗口（auto=上一月；时序首批用 none）")
    ap.add_argument("--cap", type=int, default=None, help="Stage S 窗口 IT 保留上限（缺省 settings jd_sampling.cap）")
    ap.add_argument("--floor", type=int, default=None, help="Stage S 稀疏岗保底")
    ap.add_argument("--force-sample", action="store_true", help="重生成 sample.json")
    ap.add_argument("--force-b", action="store_true", help="重建 jd_vectors 源文件（B 重跑，LLM 走缓存）")
    ap.add_argument("--force-d", action="store_true", help="重建基图边（D 重跑）")
    ap.add_argument("--skip-a", action="store_true", help="跳过 Stage A（该窗归类缓存已完备时）")
    args = ap.parse_args()

    csv = os.path.join(REPO, "data", "timeline", "jd", f"{args.window}.csv")
    if not os.path.exists(csv):
        sys.exit(f"[ERR] 该窗数据未到达：{csv}（时序流程按月引入新数据）")

    if not args.skip_a:
        run_step("Stage S0 预抽样（大窗 unique>precap 才触发；零 LLM）",
                 [os.path.join("codes", "graph", "jd_pre_sample.py"), "--window", args.window])
        run_step("Stage A 岗位归类（strict 门，LLM 兜底走指纹缓存；S0 已选键之外跳过）",
                 [os.path.join("codes", "jd_annotate", "classify_job.py"), "--strict",
                  "--files", csv])
    run_step("Stage D0 近重复（抄袭）过滤（零 LLM，simhash+Jaccard）",
             [os.path.join("codes", "graph", "jd_dedup.py"), "--window", args.window])
    s_args = []
    if args.cap is not None:
        s_args += ["--cap", str(args.cap)]
    if args.floor is not None:
        s_args += ["--floor", str(args.floor)]
    if args.force_sample:
        s_args += ["--force"]
    run_step("Stage S 降采样（如需；零 LLM）",
             [os.path.join("codes", "graph", "jd_sample.py"), "--window", args.window], s_args)

    # 幂等：B/D 产物已存在且未显式 --force-* 时跳过（各步自身也有守卫，此处提前短路省扫描）
    jd_vectors = os.path.join(REPO, "data", "timeline", "jd_derived", f"{args.window}.jd_vectors.jsonl")
    if os.path.exists(jd_vectors) and not args.force_b:
        print(f"\n[run_pipeline] Stage B 已存在，跳过（--force-b 重建）：{jd_vectors}", flush=True)
    else:
        run_step("Stage B 句级抽取（LLM，句级缓存）",
                 [os.path.join("codes", "graph", "run_jd_extract.py"), "--window", args.window],
                 ["--force"] if args.force_b else [])
    run_step("Stage C 熟练度（LLM，证据缓存；已回填则全命中免调用）",
             [os.path.join("codes", "extractor", "run_jd_proficiency.py"),
              "--from-vectors", "--window", args.window])
    base_edges = os.path.join(REPO, "data", "graph", args.window, "base", "job_task.json")
    if os.path.exists(base_edges) and not args.force_d:
        print(f"\n[run_pipeline] Stage D 基图边已存在，跳过（--force-d 重建）：{base_edges}", flush=True)
    else:
        run_step("Stage D 基图聚合（零 LLM，含汇总 CSV）",
                 [os.path.join("codes", "graph", "run_base_build.py"), "--window", args.window,
                  "--prev-window", args.prev_window],
                 ["--force"] if args.force_d else [])
    print(f"\n[run_pipeline] {args.window} 完成。后续（本编排不代跑）：叠层 JD 侧信号（jd_delta_v2）："
          f"python codes/graph/jd_delta_v2.py --window {args.window}；快照/合成/重放见 loop-design §1", flush=True)


if __name__ == "__main__":
    main()
