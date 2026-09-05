# -*- coding: utf-8 -*-
"""Stage S0：大窗预抽样（unique 指纹封顶 + 确定性哈希 + 逆概率因子 w0）。

背景（2026-09-03 用户裁定：大窗口先抽部分样本再做分类与降采样，比例/数量授权研判）：
timeline 新批次 CSV 的 wc -l 因描述字段内嵌换行虚高 ~14 倍（2026-03 物理行 148 万、
真实记录 10.1 万 / unique 指纹 ~9.5 万），"史上最大窗"实为 2022-07（27.3 万 unique）
以下。真实成本结构：Stage A 规则快路解决 ~97%、LLM 兜底随 unique 线性（2025-12：
11.6k unique → 6,961 条 LLM 判定）；Stage B/C 已被 Stage S cap 封顶不随窗涨。故
预抽样的收益对象只有 A 门与 D0/S 扫描——unique 超过 precap 才触发，未触发窗口
零行为变化（无文件消费点，完全向后兼容）。

设计：
- 触发与选择：窗口 unique 指纹数 N > precap 时，按 score=md5(key+salt) 取最小
  precap 个（与 Stage S 同款确定性哈希：可复现可审计，precap 上调时已选键集
  单调扩展，指纹/句级缓存自然衔接）；N ≤ precap 时文件写 keys=null（仅审计记录）。
- 逆概率因子：w0 = N/k（uniform 选择，单一常数）。Stage S 把 w0 复合进各层权重
  （N_j/k_j × w0），窗口总量/边权保持无偏；基面原则不变——预抽样后的选择集就是
  该窗的语料总体（D0 变体簇在集内检测，未选文档任何产物不消费）。
- 传播机制：S0 只约束 Stage A（classify_job.collect 在指纹计算后过滤未选键）→
  jobcls 缓存只含已选键 → S/D0/B/v2/summary 经 load_full_classification 全链自动
  受限（唯一改写点在 A 门，下游零改动）。
- 口径注意：预抽样窗的 IT 总量/岗位构成为估计值（×w0 复原总量）；转正确证计数
  （jd_docs）按基面硬计数——require 证据积累速度按选择率 r=k/N 折减（r≥0.6 时
  影响轻微），生命周期门自然消化。

用法：
  python codes/graph/jd_pre_sample.py --window 2026-03            # 按参数生成（存在则跳过）
  python codes/graph/jd_pre_sample.py --window 2026-03 --force    # 重生成
  python codes/graph/jd_pre_sample.py --window 2026-03 --cap 50000
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))

_GRAPH_DIR = HERE
for _d in (_GRAPH_DIR,):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import graph_config as gconfig                       # noqa: E402
sys.path.insert(0, os.path.join(REPO, "codes", "jd_annotate"))
import common as ann_common                          # noqa: E402  jd_text_key 同口径
import classify_stacks                               # noqa: E402  iter_jd_rows 同口径


def presample_path(window):
    return os.path.join(gconfig.JD_DERIVED_DIR, gconfig.JD_PRESAMPLE_FILENAME.format(window=window))


def select_keys(universe, cap, salt):
    """确定性哈希选择（纯函数）→ (selected, w0)。

    universe: 可迭代 jd_key 集合；按 score=md5(key+salt) 升序取前 cap 个。
    单调性：cap 上调时已选键集是新键集的前缀子集（扩展采样不弃已跑缓存）。
    """
    keys = list(universe)
    if len(keys) <= cap:
        return set(keys), 1.0
    scored = sorted(keys, key=lambda x: int(hashlib.md5((x + salt).encode("utf-8")).hexdigest(), 16))
    return set(scored[:cap]), round(len(keys) / cap, 4)


def build_universe(csv_path):
    """CSV → (n_records, unique 指纹集)。与 classify_job.collect 同口径（jd_text_key）。"""
    seen, n = set(), 0
    for _, title, text in classify_stacks.iter_jd_rows(csv_path, None):
        n += 1
        seen.add(ann_common.jd_text_key(title, text))
    return n, seen


def load_presample(window):
    """读 {窗口}.presample.json → rec 或 None（无文件）。keys=null 表示未触发。"""
    p = presample_path(window)
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def make_presample(window, cap=None, salt=None, force=False):
    """生成 {window}.presample.json（零 LLM）。已存在且未 --force 时跳过。"""
    cap = gconfig.JD_PRESAMPLE_CAP if cap is None else cap
    salt = gconfig.JD_PRESAMPLE_SALT if salt is None else salt
    csv_path = os.path.join(gconfig.TIMELINE_JD_DIR, f"{window}.csv")
    if not os.path.exists(csv_path):
        sys.exit(f"[ERR] timeline CSV 不存在：{csv_path}")
    out_path = presample_path(window)
    if os.path.exists(out_path) and not force:
        rec = json.load(open(out_path, encoding="utf-8"))
        trig = bool(rec.get("keys"))
        print(f"[S0] 已存在，跳过（--force 重生成）：{out_path}"
              + (f"（触发：{len(rec['keys'])} 键）" if trig else "（未触发）"))
        return out_path

    if cap and cap > 0:
        print(f"[S0] {window}：扫描 CSV 构建 unique 指纹全集（与 A 门同口径，零 LLM）...", flush=True)
        n_rows, universe = build_universe(csv_path)
        selected, w0 = select_keys(universe, cap, salt)
        triggered = len(universe) > cap
    else:  # cap<=0：显式关闭（审计记录用）
        n_rows, selected, w0, triggered = 0, set(), 1.0, False
    rec = {
        "schema_version": "0.1",
        "stage": "S0_presample",
        "window": window,
        "created": datetime.now().isoformat(timespec="seconds"),
        "producer": "codes/graph/jd_pre_sample.py",
        "params": {"precap": cap, "salt": salt},
        "population": {"records": n_rows, "unique": len(universe) if cap and cap > 0 else None,
                       "selected": len(selected)},
        "triggered": triggered,
        "weight": w0,
        "keys": sorted(selected) if triggered else None,
        "notes": "unique 指纹 > precap 才触发；w0=N/k 由 Stage S 复合进逆概率权重；"
                 "传播=A 门过滤 → jobcls 只含已选键 → S/D0/B/v2 全链受限",
    }
    os.makedirs(gconfig.JD_DERIVED_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    if triggered:
        print(f"[S0] 触发预抽样：unique {len(universe)} → 选 {len(selected)}"
              f"（{len(selected)/len(universe):.1%}，w0={w0}）→ {out_path}")
    else:
        print(f"[S0] 未触发（unique {len(universe) if cap and cap > 0 else '?'} ≤ precap={cap}），"
              f"全量处理，keys=null → {out_path}")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Stage S0：大窗预抽样 → {窗口}.presample.json")
    ap.add_argument("--window", required=True, help="窗口（YYYY-MM）")
    ap.add_argument("--cap", type=int, default=None,
                    help=f"unique 指纹保留上限（默认 settings jd_sampling.presample_cap="
                         f"{gconfig.JD_PRESAMPLE_CAP}；≤0 关闭）")
    ap.add_argument("--salt", default=None, help="确定性哈希种子")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的 presample.json")
    args = ap.parse_args()
    make_presample(args.window, args.cap, args.salt, args.force)


if __name__ == "__main__":
    main()
