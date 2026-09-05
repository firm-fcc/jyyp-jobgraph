# -*- coding: utf-8 -*-
"""组装期参数重放编排器：改 settings.yaml 组装参数后零 LLM 重建全部窗口产物。

两层分离（graph/README「参数重放操作面」）：
- LLM 层产物 = 证据（jd_vectors / 三源 ΔG / 句级与证据缓存），一次付费终身复用；
- 组装层 = 参数化纯函数——Stage D 基图聚合（α/ts_w/薪资加权）、快照强度重算
  （tier 权重/半衰期/min_strength，按窗末从原始证据全量重算）、合成（λ）。

本脚本按时间序重跑组装层：run_base_build --force（α 链级联，必须从最早窗口起整链重建）
→ 已有快照的窗口 run_snapshot build --force → run_synthesis build。全程零 LLM 调用。

用法（项目根目录）：
  python codes/graph/replay.py --dry-run            # 只打印计划
  python codes/graph/replay.py --windows 2022-05,2022-06
  python codes/graph/replay.py --all                # 所有有 jd_vectors 的窗口

改参范围（A 组，纯组装）→ 直接重放：
  strength.* / overlay.* / graph_base.alpha|ts_w1|ts_w2|salary_weight / synthesis.*
改 LLM 侧参数（B 组：并发/批组织/截断/jd_sampling/jd_gate/it_scope 等）不在重放范围。
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import graph_config as config  # noqa: E402

GRAPH_ROOT = config.GRAPH_ROOT


def windows_with_vectors():
    """有 jd_vectors 源文件（=LLM 层已跑）的窗口，按时间序。"""
    out = []
    for fn in os.listdir(config.JD_DERIVED_DIR):
        if fn.endswith(".jd_vectors.jsonl"):
            out.append(fn[: -len(".jd_vectors.jsonl")])
    return sorted(out)


def _has_snapshot(window):
    return os.path.exists(os.path.join(GRAPH_ROOT, window, config.META_FILENAME))


def _run(cmd, dry_run):
    print(f"\n>>> {' '.join(cmd)}")
    if dry_run:
        return
    r = subprocess.run(cmd, cwd=REPO)
    if r.returncode != 0:
        sys.exit(f"[replay] 步骤失败（exit {r.returncode}）：{' '.join(cmd)}")


def _read_json(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def replay(windows, dry_run=False):
    """按时间序重放组装层。返回统计。"""
    all_windows = windows_with_vectors()
    if not all_windows:
        sys.exit("[replay] 没有任何窗口有 jd_vectors 源文件（LLM 层未跑，无可重放）")
    unknown = [w for w in windows if w not in all_windows]
    if unknown:
        sys.exit(f"[replay] 这些窗口没有 jd_vectors：{unknown}（可用：{all_windows}）")
    # α 链完整性：重放必须从最早窗口起（freq 是累积量，中途起跑会混用新旧参数的链）
    if windows[0] != all_windows[0]:
        sys.exit(f"[replay] α 链要求从最早窗口 {all_windows[0]} 起整链重建（收到 {windows[0]}）；"
                 f"缺中间窗口时后续 freq 口径不一致")
    missing = [w for w in all_windows if w < windows[-1] and w not in windows]
    if missing:
        sys.exit(f"[replay] 窗口列表中间有洞（{missing}），α 链会断裂；请给连续前缀列表")

    cur_fp = config.assembly_params_fingerprint()
    plan = []
    for i, w in enumerate(windows):
        prev = windows[i - 1] if i else "none"
        steps = [("run_base_build", ["--window", w, "--prev-window", prev, "--force"])]
        if _has_snapshot(w):
            steps.append(("run_snapshot", ["build", "--window", w, "--force"]))
            steps.append(("run_synthesis", ["build", "--window", w]))
        plan.append((w, prev, steps))

    print(f"[replay] 计划：{len(windows)} 窗（{windows[0]}..{windows[-1]}），"
          f"当前组装参数指纹 {cur_fp}（logic {config.ASSEMBLY_LOGIC_VERSION}）——全程零 LLM")
    for w, prev, steps in plan:
        tag = "+ 快照/合成" if len(steps) > 1 else "（仅 base，未建快照）"
        print(f"  {w}：prev={prev} {tag}")

    if dry_run:
        print("[replay] dry-run 结束（未执行）")
        return {"windows": len(windows), "dry_run": True}

    for w, prev, steps in plan:
        for script, args in steps:
            _run([sys.executable, os.path.join("codes", "graph",
                                               f"{script}.py")] + args, dry_run)

    # 指纹核对：重放后所有产物应记录同一指纹
    print(f"\n[replay] 产物指纹核对（当前 {cur_fp}）：")
    stale = 0
    for w, _, _ in plan:
        bi = _read_json(os.path.join(GRAPH_ROOT, w, config.BASE_SUBDIR,
                                     config.BASE_AUX_FILENAMES["build_info"]))
        sm = _read_json(os.path.join(GRAPH_ROOT, w, config.META_FILENAME))
        em = _read_json(os.path.join(GRAPH_ROOT, w, config.EFFECTIVE_SUBDIR, config.META_FILENAME))
        fps = {"base": bi.get("params_fingerprint"), "snap": sm.get("params_fingerprint"),
               "eff": em.get("params_fingerprint")}
        bad = [k for k, v in fps.items() if v and v != cur_fp]
        # base 必在；snap/eff 仅已建快照的窗口要求
        need = {"base"} | ({"snap", "eff"} if _has_snapshot(w) else set())
        bad += [k for k in need if not fps.get(k)]
        mark = "陈旧!" if bad else "ok"
        stale += bool(bad)
        print(f"  {w}: base={fps['base']} snap={fps['snap'] or '-'} eff={fps['eff'] or '-'}"
              f"  {mark}")
    print(f"[replay] 完成：{len(windows)} 窗重放，指纹陈旧 {stale} 窗"
          + ("（请检查未重建的产物）" if stale else "，全部一致"))
    return {"windows": len(windows), "stale": stale, "fingerprint": cur_fp}


def main():
    ap = argparse.ArgumentParser(description="组装期参数重放（零 LLM）：D→快照→合成 整链重建")
    ap.add_argument("--windows", default=None, help="逗号分隔窗口列表（连续前缀，如 2022-05,2022-06）")
    ap.add_argument("--all", action="store_true", help="重放所有有 jd_vectors 的窗口")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划不执行")
    args = ap.parse_args()
    if args.all:
        windows = windows_with_vectors()
    elif args.windows:
        windows = [w.strip() for w in args.windows.split(",") if w.strip()]
    else:
        ap.error("需 --windows 或 --all")
    replay(windows, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
