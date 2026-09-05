# -*- coding: utf-8 -*-
"""存量窗口 skillpoint 归一回填：jd_vectors 的 skillpoint_map → canonical 空间。

对已跑过的窗口（如 2022-05/06）补做三层归一（新窗口由 run_jd_extract 在线生效，
无需本工具）。L1/L2 命中零成本；未知名走 LLM 首见归一（每名终身一次，缓存跨窗复用）。
回填后需重建该窗基图（python run_base_build.py --window W --prev-window ... --force，
零 LLM）以更新 S-SP 边。

用法：
  python skillpoint_backfill.py --windows 2022-05,2022-06
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "extractor"))

from skillpoint_norm import ALIAS_CACHE_PATH, SkillpointNormalizer


def backfill_window(window, norm):
    path = os.path.join(HERE, "..", "..", "data", "timeline", "jd_derived",
                        f"{window}.jd_vectors.jsonl")
    path = os.path.abspath(path)
    if not os.path.exists(path):
        print(f"[backfill] 跳过 {window}：无 {os.path.basename(path)}")
        return
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    n_raw = n_canon = 0
    for rec in records:
        spm = rec.get("skillpoint_map") or {}
        if not spm:
            continue
        n_raw += sum(len(v) for v in spm.values())
        rec["skillpoint_map"] = norm.normalize_skillpoint_map(spm)
        n_canon += sum(len(v) for v in rec["skillpoint_map"].values())
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    uniq = {sp for r in records for sps in (r.get("skillpoint_map") or {}).values() for sp in sps}
    print(f"[backfill] {window}：技能点实例 {n_raw} → {n_canon}（归并 {n_raw - n_canon}），"
          f"唯一 canonical {len(uniq)}")

    # meta 补记归一信息
    meta_path = path.replace(".jd_vectors.jsonl", ".jd_vectors.meta.json")
    if os.path.exists(meta_path):
        try:
            meta = json.load(open(meta_path, encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        meta["skillpoint_norm"] = {"registry_version": norm.registry_version,
                                   "backfilled": True, **norm.stats}
        json.dump(meta, open(meta_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser(description="存量窗口 skillpoint 三层归一回填")
    ap.add_argument("--windows", required=True, help="逗号分隔，如 2022-05,2022-06")
    ap.add_argument("--no-llm", action="store_true", help="只做 L1/L2（未知名保留，不调 LLM）")
    args = ap.parse_args()

    llm_post = None
    if not args.no_llm:
        # 复用 extractor 的 LLMClient（config-swap 同 run_jd_extract）
        saved = sys.modules.pop("config", None)
        ext_dir = os.path.abspath(os.path.join(HERE, "..", "extractor"))
        sys.path.insert(0, ext_dir)
        try:
            from llm_client import LLMClient
            llm_post = LLMClient()._post
        finally:
            if saved is not None:
                sys.modules["config"] = saved
    norm = SkillpointNormalizer(llm_post=llm_post, use_cache=True)
    for w in args.windows.split(","):
        w = w.strip()
        if w:
            backfill_window(w, norm)
    print(f"[backfill] 归一统计：{norm.stats}（别名缓存 {ALIAS_CACHE_PATH}）")


if __name__ == "__main__":
    main()
