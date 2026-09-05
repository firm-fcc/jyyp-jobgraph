# -*- coding: utf-8 -*-
"""Stage D0：窗口内 JD 近重复（抄袭）过滤——simhash + 分块候选 + Jaccard 复核。

回应赛题"抄袭"要求（algorithm-design.md §4.4.2 / A.4 预留方案的轻量实现）：
招聘平台大量互相抄袭 / 复用模板的 JD（换公司名/薪资/标题微改后重发），若只做精确
指纹去重（jd_text_key），换皮变体会被计为多份独立证据，虚增频次与边权重。

机制（确定性、零 LLM、逐窗产物化）：
- **正文 simhash64**：对去噪正文（_kept_text 去福利/公司介绍段）的字符 3-gram 计数加权
  simhash——标题不参与（抄袭常改标题不改正文，这正是精确指纹漏掉的情形）；
- **分块候选**（8×8 位 pigeonhole）：海明距 ≤7 的指纹必有至少一块 8 位完全相同，
  桶索引把两两比较从 O(n²) 降到近邻候选；
- **Jaccard 复核**：候选对的 3-gram 哈希集合 Jaccard ≥ 0.95 才确认（simhash 只做
  召回，精确率由复核保证，对应 v1 设计"相似度>95%"）；
- **星型聚类 + 保最早**：按 (opentime, jobid) 序贪心并入已见簇根（根恒为簇内最早），
  抄袭簇只保留最早发布的代表，其余为变体；
- 产物 `data/timeline/jd_derived/{窗口}.dedup.json`（变体键→代表键映射 + 统计），
  消费方在线过滤：Stage S（采样分母）/ Stage B（抽取输入）/ Stage D（聚合，存量窗
  经 replay 追溯）/ jd_delta_v2（全量扫描）。

范围口径：仅窗内去重——同文跨月重发按"当月在场"计（逐月 presence 是时序统计的
设计语义，α 跨窗衰减链已处理其历史累积）；跨窗时序抄袭（v1 A.4 第 3 条）不在此期。
正文过短（< min_chars）不参与近重复（高误报，精确去重已覆盖逐字相同）。

用法（项目根目录）：
  python codes/graph/jd_dedup.py --window 2022-06           # 产 {窗口}.dedup.json
  python codes/graph/jd_dedup.py --window 2022-06 --dry-run # 只看统计与样例聚类

参数：settings.yaml → jd_dedup（hamming_max / jaccard_min / ngram / min_chars）。
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))

_GRAPH_DIR = HERE
if _GRAPH_DIR not in sys.path:
    sys.path.insert(0, _GRAPH_DIR)

import graph_config as gconfig  # noqa: E402
import run_jd_extract as rje    # noqa: E402  _kept_text / load_full_classification
import common as ann_common     # noqa: E402  jd_text_key


def _setting(*keys, default):
    try:
        import yaml
        with open(os.path.join(REPO, "codes", "settings.yaml"), encoding="utf-8") as f:
            node = yaml.safe_load(f)
        for k in keys:
            node = node[k]
        return node
    except Exception:
        return default


HAMMING_MAX = _setting("jd_dedup", "hamming_max", default=6)
JACCARD_MIN = _setting("jd_dedup", "jaccard_min", default=0.95)
NGRAM = _setting("jd_dedup", "ngram", default=3)
MIN_CHARS = _setting("jd_dedup", "min_chars", default=80)
# 分块 pigeonhole：海明距 ≤7 的两指纹必共享至少一个 8 位块（8 块 7 错 → 必有零错块）。
# simhash 只管召回（海明 ≤6 的候选不漏），精确率由 Jaccard ≥0.95 复核把守。
BLOCKS, BLOCK_BITS = 8, 8

_gram_hash_cache = {}                           # gram → 64 位哈希（跨文档复用，blake2b 确定性）


def _g64(gram):
    h = _gram_hash_cache.get(gram)
    if h is None:
        import hashlib
        h = int.from_bytes(hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest(),
                           "big")
        _gram_hash_cache[gram] = h
    return h


def gram_hashes(text, n=NGRAM):
    """去噪正文 → 字符 n-gram 的 64 位哈希列表（空白折叠后切）。"""
    import re
    s = re.sub(r"\s+", "", text)
    return [_g64(s[i:i + n]) for i in range(len(s) - n + 1)]


def simhash(hashes):
    """哈希列表（可重复）→ 64 位 simhash 指纹。"""
    if not hashes:
        return 0
    v = [0] * 64
    for h in hashes:
        for b in range(64):
            v[b] += 1 if (h >> b) & 1 else -1
    fp = 0
    for b in range(64):
        if v[b] > 0:
            fp |= 1 << b
    return fp


def hamming(a, b):
    return bin(a ^ b).count("1")


def blocks_of(fp):
    """64 位指纹 → 4 个 (块号, 块值)。"""
    mask = (1 << BLOCK_BITS) - 1
    return [(i, (fp >> (i * BLOCK_BITS)) & mask) for i in range(BLOCKS)]


# ---------------- 扫描与聚类 ----------------
def scan_unique_docs(window):
    """窗口 CSV → 唯一 IT JD 的正文档案（精确指纹去重在前，取首见行=最早 opentime）。

    timeline CSV 行内按 opentime 升序（build_jd_timeline 保证），首见即最早。
    返回 [{key, jobid, opentime, text}]，按 (opentime, jobid, 首见序) 稳定排序。
    """
    csv_path = os.path.join(gconfig.TIMELINE_JD_DIR, f"{window}.csv")
    if not os.path.exists(csv_path):
        sys.exit(f"[dedup] timeline CSV 不存在：{csv_path}")
    import csv as _csv
    cls_map, _ = rje.load_full_classification(csv_path, strict=True)
    docs, seen = [], {}
    with open(csv_path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        for row in _csv.DictReader(fh):
            text, title = row.get("job_information") or "", row.get("job") or ""
            key = ann_common.jd_text_key(title, text)
            if key in seen:
                continue
            seen[key] = True
            c = cls_map.get(key) or {}
            if not c.get("it_related"):
                continue
            kept = rje._kept_text(text)
            if len(kept.strip()) < MIN_CHARS:
                continue                          # 短正文不参与近重复（精确去重已覆盖）
            docs.append({"key": key, "jobid": row.get("jobid") or "",
                         "opentime": (row.get("opentime") or "").strip(), "text": kept})
    docs.sort(key=lambda d: (d["opentime"], d["jobid"]))
    return docs


def cluster_near_dups(docs, hamming_max=HAMMING_MAX, jaccard_min=JACCARD_MIN):
    """星型贪心聚类：按时间序遍历，与桶内已见簇根比对（海明+Jaccard 双确认）。

    根恒为簇内最早成员（union 时较晚者并入较早者），天然实现"保留最早发布"。
    返回 (parent 数组, 统计)：parent[i] = 簇根下标。
    """
    n = len(docs)
    parent = list(range(n))
    fps, gsets = [0] * n, [None] * n

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    buckets = defaultdict(list)
    n_cand = n_conf = 0
    for i in range(n):
        hs = gram_hashes(docs[i]["text"])
        gsets[i] = set(hs)
        fps[i] = simhash(hs)
        matched = None
        seen_roots = set()
        for bi, val in blocks_of(fps[i]):
            for prior in buckets.get((bi, val), ()):
                r = find(prior)
                if r in seen_roots:
                    continue
                seen_roots.add(r)
                n_cand += 1
                if hamming(fps[i], fps[r]) <= hamming_max:
                    a, b = gsets[i], gsets[r]
                    if len(a & b) / len(a | b) >= jaccard_min:
                        matched = r
                        n_conf += 1
                        break
            if matched is not None:
                break
        if matched is not None:
            parent[i] = matched                   # 较晚并入较早（matched < i 恒成立）
        for bi, val in blocks_of(fps[i]):
            buckets[(bi, val)].append(i)
    stats = {"n_docs": n, "n_candidate_pairs": n_cand, "n_confirmed": n_conf}
    return parent, stats


def build_variants(window, dry_run=False, force=False, hamming_max=HAMMING_MAX,
                   jaccard_min=JACCARD_MIN):
    """主入口：产 {窗口}.dedup.json。返回统计 dict。"""
    out_path = os.path.join(gconfig.JD_DERIVED_DIR, gconfig.JD_DEDUP_FILENAME.format(window=window))
    if os.path.exists(out_path) and not force and not dry_run:
        print(f"[dedup] 已存在，跳过（--force 重生成）：{out_path}")
        return json.load(open(out_path, encoding="utf-8")).get("stats", {})

    docs = scan_unique_docs(window)
    parent, st = cluster_near_dups(docs, hamming_max, jaccard_min)
    clusters = defaultdict(list)
    for i in range(len(docs)):
        # parent 链可能两跳（贪心并入后根未路径压缩到底），收敛到最终根
        r = i
        while parent[r] != r:
            r = parent[r]
        clusters[r].append(i)
    variants, sizes = {}, []
    for root, members in clusters.items():
        if len(members) < 2:
            continue
        sizes.append((len(members), root))
        for m in members:
            if m != root:
                variants[docs[m]["key"]] = docs[root]["key"]
    sizes.sort(reverse=True)

    stats = {**st, "n_clusters": len(sizes), "n_variants": len(variants),
             "variant_ratio": round(len(variants) / len(docs), 4) if docs else 0.0,
             "largest": [{"size": s, "rep": docs[r]["jobid"],
                          "opentime": docs[r]["opentime"][:10]} for s, r in sizes[:5]]}
    print(f"[dedup] {window}：{len(docs)} 条唯一 IT JD（正文≥{MIN_CHARS}字）→ 候选对 "
          f"{st['n_candidate_pairs']}，确认 {st['n_confirmed']}，抄袭簇 {len(sizes)} 个，"
          f"变体 {len(variants)} 条（{stats['variant_ratio']:.1%}）")
    for it in stats["largest"]:
        print(f"    最大簇：{it['size']} 条（代表 {it['rep']}，首发 {it['opentime']}）")
    if dry_run:
        for s, r in sizes[:3]:
            mem = [m for m in clusters[r] if m != r][:2]
            print(f"    样例簇（{s} 条）代表正文：{docs[r]['text'][:70]}…")
            for m in mem:
                print(f"      变体：{docs[m]['text'][:70]}…")
        return stats

    rec = {
        "schema_version": "0.1",
        "stage": "dedup_near_dup",
        "window": window,
        "created": datetime.now().isoformat(timespec="seconds"),
        "producer": "codes/graph/jd_dedup.py",
        "params": {"hamming_max": hamming_max, "jaccard_min": jaccard_min,
                   "ngram": NGRAM, "min_chars": MIN_CHARS,
                   "scope": "window-internal(kept_text, title excluded)"},
        "stats": stats,
        "variants": variants,
        "notes": "抄袭簇保留最早发布（opentime, jobid 序）；变体在 S/B/D/jd_delta_v2 "
                 "消费时在线过滤；跨月重发按逐月在场计（时序统计语义）",
    }
    os.makedirs(gconfig.JD_DERIVED_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    print(f"[dedup] 完成：{out_path}")
    return stats


def load_variants(window):
    """消费方 helper：{变体 jd_key: 代表 jd_key}（产物缺失 → 空 dict，向后兼容）。"""
    p = os.path.join(gconfig.JD_DERIVED_DIR, gconfig.JD_DEDUP_FILENAME.format(window=window))
    try:
        return json.load(open(p, encoding="utf-8")).get("variants") or {}
    except (OSError, ValueError):
        return {}


def main():
    ap = argparse.ArgumentParser(description="Stage D0：窗口内 JD 近重复（抄袭）过滤")
    ap.add_argument("--window", required=True, help="窗口（YYYY-MM）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的 dedup.json")
    ap.add_argument("--dry-run", action="store_true", help="只看统计与样例聚类，不写文件")
    args = ap.parse_args()
    build_variants(args.window, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
