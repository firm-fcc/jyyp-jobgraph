# -*- coding: utf-8 -*-
"""Stage S：窗口内 JD 降采样（分层封顶 + 稀疏岗保底 + 确定性哈希 + 逆概率加权）。

时序流程（逐窗推进，模拟数据到达；后续数据更新同流程）：
  新一个月 CSV → Stage A 岗位归类（classify_job --strict，全量该窗、缓存复用）
  → **本模块 Stage S**（零 LLM：读归类结果，产 {窗口}.sample.json）
  → Stage B 抽取（run_jd_extract 只处理采样键，记录 sample_weight）
  → Stage C 熟练度 → Stage D 基图聚合（权重 = sample_weight × salary_weight）。

设计（2026-08-26 蒙特卡洛验证，2025-10 全量基线 ×20 种子）：
- **A 门全量、只采 B/C**：A 便宜（~5% 成本）且按指纹缓存一次付费；换来每窗**精确**的
  IT 总量与岗位构成（时序分析的分子分母），以及分层标签（保底采样需知道哪些岗稀疏）。
- **分层封顶**：窗口目标 T = min(该窗 IT 数, cap)，比例率 r = T/N；小窗全保（近期月份
  信息最全，α 衰减链本就更重视近期），只有 2022-2024 巨月被封顶。
- **岗位层保底 floor**：层内 ≤floor 全保；大层采样不低于 floor——保证 97 岗每窗都有
  逐岗时序分辨率（无保底纯比例抽样实测漏 31% 边权重、稀疏岗整层消失，不可去）。
- **确定性哈希选取**：score = md5(jd_key + salt)，层内取最小 k 个——可复现可审计，
  扩展采样（如 cap 1万→2万）时已跑键集不变，句级/证据缓存自动衔接。
- **逆概率加权**：w = N_j/k_j（层总体/层采样数），使窗口级频次是总体量的无偏估计；
  月度总量趋势直接用 A 门精确分母，不受抽样影响。

验证结论（rate=10% 口径）：J-T/J-S/T-S 边 Jaccard ≥0.96、公共边权重 Pearson ≥0.989、
漏边权重 ≤0.3%、技能分布 TV ≤0.017、top30 技能重合 99%、岗位层零丢失；S-SP 长尾边
最敏感（Jaccard ~0.80）但漏失均为低权边。大窗各层绝对样本量不低于验证对应层，
以上为误差上界（√n 收敛）。

用法：
  python jd_sample.py --window 2025-10                 # 按settings参数生成（已存在则跳过）
  python jd_sample.py --window 2022-08 --cap 20000     # 覆盖 cap
  python jd_sample.py --window 2025-10 --force         # 重生成
"""
import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
TIMELINE_JD_DIR = os.path.join(REPO, "data", "timeline", "jd")

_GRAPH_DIR = HERE
for _d in (_GRAPH_DIR,):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import graph_config as gconfig
import run_jd_extract as rje   # load_full_classification / load_it_scope 复用


def stratified_sample(cls_map, cap, floor, salt):
    """归类结果 → 采样键集与逆概率权重（纯函数，fixture 可测）。

    cls_map: {jd_key: {job_code, it_related}}（load_full_classification 产物）。
    → (n_it, keys_w, per_job)：keys_w 为 {jd_key: weight}；rate≥1 时全保（权重全 1）。
    """
    strata = defaultdict(list)
    for key, c in cls_map.items():
        if c.get("it_related") and c.get("job_code"):
            strata[c["job_code"]].append(key)
    n_it = sum(len(v) for v in strata.values())
    rate = min(1.0, cap / n_it) if n_it else 1.0
    keys_w, per_job = {}, {}
    for code, ks in strata.items():
        n = len(ks)
        if rate >= 1.0 or n <= floor:
            k = n
        else:
            k = min(n, max(floor, round(rate * n)))
        per_job[code] = {"n": n, "k": k, "weight": round(n / k, 4)}
        if k >= n:
            for x in ks:
                keys_w[x] = 1.0
        else:
            scored = sorted(ks, key=lambda x: int(
                hashlib.md5((x + salt).encode("utf-8")).hexdigest(), 16))
            w = n / k
            for x in scored[:k]:
                keys_w[x] = w
    return n_it, keys_w, per_job


def apply_presample(keys_w, pre_rec):
    """Stage S0 预抽样激活时复合逆概率因子 w0（纯函数）→ (keys_w', active)。

    pre_rec 为 {keys, weight}（keys 非空=触发）。各键权重 ×w0（含未触 S cap 的
    全保权重 1.0 → w0），窗口总量/边权保持无偏；未触发 → 原样返回。
    """
    if not pre_rec or not pre_rec.get("keys"):
        return keys_w, False
    w0 = pre_rec.get("weight") or 1.0
    return {k: round(w * w0, 4) for k, w in keys_w.items()}, True


def make_sample(window, cap=None, floor=None, salt=None, force=False):
    """生成 {window}.sample.json（零 LLM）。A 门未跑满（未分类残留）时中止提醒。"""
    cap = gconfig.JD_SAMPLE_CAP if cap is None else cap
    floor = gconfig.JD_SAMPLE_FLOOR if floor is None else floor
    salt = gconfig.JD_SAMPLE_SALT if salt is None else salt

    csv_path = os.path.join(TIMELINE_JD_DIR, f"{window}.csv")
    if not os.path.exists(csv_path):
        sys.exit(f"[ERR] timeline CSV 不存在：{csv_path}")
    out_path = os.path.join(gconfig.JD_DERIVED_DIR, gconfig.JD_SAMPLE_FILENAME.format(window=window))
    if os.path.exists(out_path) and not force:
        print(f"[S] 已存在，跳过（--force 重生成）：{out_path}")
        return out_path

    print(f"[S] {window}：加载岗位归类（classify_job 缓存在线重算，零 LLM）...", flush=True)
    cls_map, st = rje.load_full_classification(csv_path, strict=True)
    n_unclassified = sum(1 for c in cls_map.values() if c.get("unclassified"))
    if n_unclassified:
        sys.exit(f"[ERR] {n_unclassified} 条未归类（先跑 Stage A：classify_job --strict --files {csv_path}）")

    # Stage D0 近重复（抄袭）过滤：变体不进采样分母与键集（防频次虚增 + 省抽样额度）
    import jd_dedup
    near_dup = jd_dedup.load_variants(window)
    if near_dup:
        cls_map = {k: v for k, v in cls_map.items() if k not in near_dup}
        print(f"[S] 近重复过滤：剔除抄袭变体 {len(near_dup)} 条（{window}.dedup.json）", flush=True)

    n_it, keys_w, per_job = stratified_sample(cls_map, cap, floor, salt)
    n_sampled = len(keys_w)
    sampled = n_sampled < n_it

    # Stage S0 预抽样（2026-09-03）：大窗 A 门只归类已选键（cls_map 已受限），
    # 这里复合 w0=N/k 使窗口总量/边权无偏；预抽样激活时 keys 必须显式给出
    # （B 按键过滤 + 写 sample_weight），即使 S 自身未触 cap
    import jd_pre_sample
    pre_rec = jd_pre_sample.load_presample(window)
    if pre_rec and pre_rec.get("keys"):
        pre_keys = set(pre_rec["keys"])
        n_out = sum(1 for k in cls_map if k not in pre_keys)
        if n_out:  # 理论为 0（A 门已过滤）；防御：jobcls 陈旧时对齐
            cls_map = {k: v for k, v in cls_map.items() if k in pre_keys}
        keys_w, pre_active = apply_presample(keys_w, pre_rec)
    else:
        pre_active = False
    emit_keys = sampled or pre_active

    rec = {
        "schema_version": "0.1",
        "stage": "S_sample",
        "window": window,
        "created": datetime.now().isoformat(timespec="seconds"),
        "producer": "codes/graph/jd_sample.py",
        "params": {"cap": cap, "floor": floor, "salt": salt,
                   "it_scope_version": st.get("it_scope_version", "")},
        "population": {
            "csv_rows": st["rows"], "unique": st["unique"], "it_in_scope": n_it,
            "unique_all": st.get("unique_all", st["unique"]),
            "out_of_scope": st.get("out_of_scope", 0), "excluded_non_it": st["excluded"],
            "near_dup_variants": len(near_dup),
            "per_job": {c: d["n"] for c, d in sorted(per_job.items())},
        },
        "sample": {
            "sampled": emit_keys,
            "n_sampled": n_sampled,
            "effective_rate": round(n_sampled / n_it, 4) if n_it else 1.0,
            "per_job": {c: {"k": d["k"], "weight": d["weight"]}
                        for c, d in sorted(per_job.items())},
        },
        # keys 仅在实际降采样/预抽样时给出（Stage B 过滤 + 权重）；全保时为 null（不过滤，
        # 文件仍保留各层分母供时序分析）
        "keys": keys_w if emit_keys else None,
        "notes": "逆概率权重 w=N_j/k_j（预抽样窗再 ×w0）；Stage B 按键过滤并写 sample_weight，"
                 "Stage D 乘入频次",
    }
    if pre_active:
        rec["pre_sample"] = {
            "file": gconfig.JD_PRESAMPLE_FILENAME.format(window=window),
            "unique_all": pre_rec["population"]["unique"],
            "selected": pre_rec["population"]["selected"],
            "weight": pre_rec.get("weight"),
            "effective": round(pre_rec["population"]["selected"]
                               / max(pre_rec["population"]["unique"], 1), 4),
            "note": "预抽样窗 IT 总量/岗位构成为估计值（×w0 复原总量）；确证计数按基面",
        }
    os.makedirs(gconfig.JD_DERIVED_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)

    print(f"[S] 完成：{out_path}", flush=True)
    print(f"    IT 总体 {n_it} → 采样 {n_sampled}（有效率 {rec['sample']['effective_rate']:.1%}，"
          f"cap={cap} floor={floor}）")
    if pre_active:
        ps = rec["pre_sample"]
        print(f"    [S0] 预抽样窗：unique {ps['unique_all']} → 已选 {ps['selected']}"
              f"（{ps['effective']:.1%}，w0={ps['weight']} 已复合进权重；总体为估计值）")
    if sampled:
        big = sorted(((d['n'], c, d['k']) for c, d in per_job.items()), reverse=True)[:5]
        print("    最大层（n→k）：" + " ".join(f"{c} {n}→{k}" for n, c, k in big))
    elif not emit_keys:
        print("    未触 cap，全量保留（keys=null 不过滤）")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Stage S：窗口内 JD 降采样 → {窗口}.sample.json")
    ap.add_argument("--window", required=True, help="窗口（YYYY-MM）")
    ap.add_argument("--cap", type=int, default=None, help=f"窗口 IT 保留上限（默认 settings jd_sampling.cap={gconfig.JD_SAMPLE_CAP}）")
    ap.add_argument("--floor", type=int, default=None, help=f"稀疏岗保底（默认 {gconfig.JD_SAMPLE_FLOOR}）")
    ap.add_argument("--salt", default=None, help="确定性哈希种子")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的 sample.json")
    args = ap.parse_args()
    make_sample(args.window, args.cap, args.floor, args.salt, args.force)


if __name__ == "__main__":
    main()
