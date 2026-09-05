# -*- coding: utf-8 -*-
"""存量缓存 canonical 类别重打标（一次性口径修复，LLM 小额调用）。

背景：L3 首见归一旧版 prompt 的类别指令是裸清单（无定义），导致"标准"被理解为
"技术规范"（CSS/JSON/USB）、"工具"成为兜底类。v2 已注入 CATEGORY_RULES 判定表
（skillpoint_norm.CATEGORY_RULES，唯一事实源），本脚本用新口径重判存量缓存中的
非 curated canonical（curated 类别人工审定，不重判），并整体重写缓存文件。

用法：
  python skillpoint_retag.py                 # 全部非 curated canonical（~75 次调用，~1 元）
  python skillpoint_retag.py --min-count 5   # 只重判出现 ≥5 次的（按两窗使用频次）
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from skillpoint_norm import (ALIAS_CACHE_PATH, CATEGORIES, CATEGORY_RULES,
                              REGISTRY_PATH, SkillpointNormalizer)

_RETAG_BATCH = 50

_RETAG_PROMPT = """你是技术名词分类器。对每个技术名词，按以下判定规则选出最贴切的一个类别。

判定规则：
{rules}

严格只输出一个 JSON 数组（即使只有一个名词也必须包成数组），无其他文字：
[{{"name":"...","category":"..."}}]

待分类（{n} 个）：
{items}}"""


def usage_counts():
    """两窗（当前正式数据）skillpoint canonical 使用频次，供 --min-count 过滤。"""
    from collections import Counter
    counts = Counter()
    derived = os.path.join(HERE, "..", "..", "data", "timeline", "jd_derived")
    import glob
    for path in glob.glob(os.path.join(derived, "*.jd_vectors.jsonl")):
        with open(path, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                if not rec.get("it_related", True):
                    continue
                for sps in (rec.get("skillpoint_map") or {}).values():
                    counts.update(sps)
    return counts


def main():
    ap = argparse.ArgumentParser(description="存量缓存 canonical 类别重打标（新口径）")
    ap.add_argument("--min-count", type=int, default=0,
                    help="只重判使用频次 ≥N 的 canonical（0=全部非 curated）")
    args = ap.parse_args()

    saved = sys.modules.pop("config", None)
    ext_dir = os.path.abspath(os.path.join(HERE, "..", "extractor"))
    sys.path.insert(0, ext_dir)
    try:
        from llm_client import LLMClient
        llm_post = LLMClient()._post
    finally:
        if saved is not None:
            sys.modules["config"] = saved

    norm = SkillpointNormalizer(llm_post=None, use_cache=True)
    reg = json.load(open(REGISTRY_PATH, encoding="utf-8"))
    curated = set(reg.get("curated", {}))
    retired = reg.get("retired", {})

    # 目标：缓存中实际生效的 canonical（加载时已做过退役重映射），排除 curated 与退役名
    targets = sorted(c for c in norm._canon_cat if c not in curated and c not in retired)
    if args.min_count > 0:
        counts = usage_counts()
        targets = [c for c in targets if counts.get(c, 0) >= args.min_count]
    print(f"[retag] 待重判 {len(targets)} 个非 curated canonical"
          f"（min_count={args.min_count}，批 {_RETAG_BATCH}）", flush=True)

    new_cat = {}
    for i in range(0, len(targets), _RETAG_BATCH):
        batch = targets[i:i + _RETAG_BATCH]
        items = "\n".join(f"{j+1}. {n}" for j, n in enumerate(batch))
        prompt = (_RETAG_PROMPT.replace("{rules}", CATEGORY_RULES)
                  .replace("{n}", str(len(batch))).replace("{items}", items))
        try:
            entries = llm_post(prompt) or []
        except Exception as e:
            print(f"[retag] 批失败（保留原类别）{len(batch)} 个: {e}", flush=True)
            continue
        for e in entries:
            if isinstance(e, dict) and e.get("name") in batch and e.get("category") in CATEGORIES:
                new_cat[e["name"]] = e["category"]
        print(f"    进度 {min(i + _RETAG_BATCH, len(targets))}/{len(targets)}"
              f"（已判 {len(new_cat)}）", flush=True)

    # 整体重写缓存：curated canonical 的条目对齐 curated 类别；重判成功的替换类别
    records = [json.loads(l) for l in open(ALIAS_CACHE_PATH, encoding="utf-8") if l.strip()]
    n_changed = 0
    for rec in records:
        canon = retired.get(rec.get("canonical"), rec.get("canonical"))
        rec["canonical"] = canon
        if canon in curated:
            cat = reg["curated"][canon].get("category", rec.get("category", ""))
        else:
            cat = new_cat.get(canon, rec.get("category", ""))
        if cat != rec.get("category"):
            n_changed += 1
        rec["category"] = cat
    with open(ALIAS_CACHE_PATH, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    from collections import Counter
    dist = Counter(r["category"] for r in records)
    print(f"[retag] 完成：重判成功 {len(new_cat)}/{len(targets)}，缓存条目类别变更 {n_changed}"
          f"，重写 {ALIAS_CACHE_PATH}")
    print(f"[retag] 重写后类别分布（按缓存条目）: {dict(dist.most_common())}")


if __name__ == "__main__":
    main()
