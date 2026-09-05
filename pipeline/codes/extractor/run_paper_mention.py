# -*- coding: utf-8 -*-
"""论文提及识别 CLI 入口（分类抽取层）。

用法（在模块目录下运行）：
  cd codes/extractor
  python run_paper_mention.py --mode skill --tier S --limit 3          # 论文数据（走 paper_parser，含断点）
  python run_paper_mention.py --mode task --papers-dir PATH --no-resume
  python run_paper_mention.py --mode job --input "某篇论文摘要文本..."  # 原始文本（同 JD extractor）

参数：
  --mode skill|task|job     提及分类类型（默认 skill）
  --papers-dir PATH         论文数据目录（默认 data/papers/专题三_数据质量与多源融合）
  --tier S|A|B              仅处理该分档
  --limit N                 仅处理 N 篇（探索用；写入独立输出与断点，不动主产物）
  --input PATH/文本          原始文本 / 文件 / 目录（代替论文数据，无断点）
  --output PATH             提及结果 JSON（默认 classify/DeltaG/papers_mentions.json）
  --chunk N                 论文批大小（断点粒度，默认 15）
  --no-cache                禁用句级缓存
  --no-resume               不恢复断点
  --dry-run                 只解析论文 + 打印规模，不调用 LLM
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

import config
import taxonomy as tax
from paper_mention import PaperMentionExtractor

# 跨模块导入：论文解析层（paper_signal）——其配置为唯一命名 paper_config，天然不冲突
_PS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "paper_signal"))
if _PS not in sys.path:
    sys.path.insert(0, _PS)
import paper_config  # noqa: F401
from paper_parser import scan_papers
from paper_source import PaperSource
from paper_sampler import StratifiedSampler

DEFAULT_OUTPUT = os.path.join(config.PROJECT_ROOT, "classify", "DeltaG", "papers_mentions.json")


def read_text_input(path):
    """读文本/文件/目录 → 文本列表（与 run_extractor.read_input 同构）。"""
    if os.path.isdir(path):
        texts = []
        for f in sorted(os.listdir(path)):
            if f.endswith((".txt", ".md", ".csv")):
                texts.extend(read_text_input(os.path.join(path, f)))
        return texts
    with open(path, encoding="utf-8") as f:
        return [f.read()]


def _as_paper(text):
    """把原始文本包装为具备 title/keywords/abstract/evidence_sentences 属性的轻量对象。"""
    class _P:
        pass
    p = _P()
    p.arxiv_id = ""
    p.title = ""
    p.keywords = []
    p.abstract = text
    p.evidence_sentences = []
    return p


def _aggregate(per_paper):
    """跨论文聚合：mention 提及论文数 / 单元频次 / 技能点频次。"""
    n_papers = Counter()          # code → 提及该条目的论文数
    n_units = Counter()           # code → 提及该条目的单元频次（跨论文累计）
    sp_counter = Counter()        # 技能点 → 频次
    for r in per_paper:
        for code, cnt in r["mentions"].items():
            n_papers[code] += 1
            n_units[code] += cnt
        for sp, cnt in r["skillpoints"].items():
            sp_counter[sp] += cnt
    return {"mention_paper_counts": dict(n_papers),
            "mention_unit_counts": dict(n_units),
            "skillpoint_counts": dict(sp_counter)}


def run_on_papers(ext, taxonomy, papers_dir, tier, limit, output, chunk, resume):
    """论文数据路径：scan_papers → PaperSource（去重/断点）→ 逐篇提及识别。"""
    records = scan_papers(papers_dir, tier=tier, limit=limit)
    if not records:
        raise SystemExit(f"未找到论文（papers_dir={papers_dir}, tier={tier}）")
    source = PaperSource(records, stratum="tier")
    ckpt = os.path.splitext(output)[0] + "_checkpoint.json"
    if resume:
        consumed = StratifiedSampler.load_checkpoint(ckpt)
        if consumed:
            source.restore_consumed(consumed)
            print(f"断点恢复：已消费 {len(consumed)} 条，剩余 {source.remaining()} 条", file=sys.stderr)
    per_paper = []
    while source.remaining() > 0:
        batch = source.next_batch(chunk)
        for rec in batch:
            r = ext.extract_paper(rec, taxonomy)
            per_paper.append({"paper_id": rec.arxiv_id, "title": rec.title,
                              "mentions": r["mentions"], "evidence": r["evidence"],
                              "skillpoints": r["skillpoints"]})
        source.save_checkpoint(ckpt)
    return per_paper, len(records)


def run_on_texts(ext, taxonomy, texts):
    """原始文本路径：每段文本视为一篇论文的抽象。"""
    per_paper = []
    for i, text in enumerate(texts):
        r = ext.extract_paper(_as_paper(text), taxonomy)
        per_paper.append({"paper_id": str(i), "title": text.splitlines()[0][:60] if text else "",
                          "mentions": r["mentions"], "evidence": r["evidence"],
                          "skillpoints": r["skillpoints"]})
    return per_paper, len(texts)


def main():
    ap = argparse.ArgumentParser(description="论文提及识别：识别论文对既有技能/任务/岗位的提及")
    ap.add_argument("--mode", default="skill", choices=["skill", "task", "job"])
    ap.add_argument("--papers-dir", default=None, help=f"论文数据目录（默认 {paper_config.PAPER_DIR}）")
    ap.add_argument("--tier", default=None, choices=["S", "A", "B"], help="仅处理该分档")
    ap.add_argument("--limit", type=int, default=None, help="仅处理 N 篇（探索用）")
    ap.add_argument("--input", default=None, help="原始文本 / 文件 / 目录（代替论文数据）")
    ap.add_argument("--output", default=None, help=f"提及结果 JSON（默认 {DEFAULT_OUTPUT}）")
    ap.add_argument("--chunk", type=int, default=15, help="论文批大小（断点粒度，默认 15）")
    ap.add_argument("--no-cache", action="store_true", help="禁用句级缓存")
    ap.add_argument("--no-resume", action="store_true", help="不恢复断点")
    ap.add_argument("--dry-run", action="store_true", help="只解析论文 + 打印规模，不调用 LLM")
    args = ap.parse_args()

    # 输出路径：探索运行（--limit）用独立文件，不动主产物/主断点
    if args.output:
        output = args.output
    elif args.limit is not None:
        output = DEFAULT_OUTPUT.rsplit(".json", 1)[0] + "_explore.json"
    else:
        output = DEFAULT_OUTPUT

    taxonomy = tax.load(args.mode)
    print(f"模式: {args.mode} | 体系: {taxonomy.name} | 标签数: {len(taxonomy)}", file=sys.stderr)

    if args.dry_run:
        records = scan_papers(args.papers_dir or paper_config.PAPER_DIR, tier=args.tier, limit=args.limit)
        print(f"解析论文: {len(records)} 篇", file=sys.stderr)
        from collections import Counter as _C
        print("分档分布: " + ", ".join(f"{k}={v}" for k, v in sorted(_C(r.tier for r in records).items())),
              file=sys.stderr)
        return

    ext = PaperMentionExtractor(mode=args.mode, use_cache=not args.no_cache)

    if args.input is not None:
        texts = read_text_input(args.input) if os.path.exists(args.input) else [args.input]
        per_paper, n_scanned = run_on_texts(ext, taxonomy, texts)
    else:
        per_paper, n_scanned = run_on_papers(ext, taxonomy, args.papers_dir or paper_config.PAPER_DIR,
                                             args.tier, args.limit, output, args.chunk,
                                             resume=not args.no_resume)

    n_processed = len(per_paper)
    out = {
        "mode": args.mode,
        "taxonomy": taxonomy.name,
        "num_papers": n_processed,
        "num_papers_scanned": n_scanned,
        "aggregate": _aggregate(per_paper),
        "per_paper": per_paper,
        "stats": ext.stats(),
    }
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"已输出: {output}（处理 {n_processed} / 扫描 {n_scanned} 篇论文）", file=sys.stderr)


if __name__ == "__main__":
    main()
