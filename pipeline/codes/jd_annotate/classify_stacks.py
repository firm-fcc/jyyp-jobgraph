# -*- coding: utf-8 -*-
"""JD 内容 → 技术栈归类（v2.1，2026-08-20）：词库快路 + LLM 兜底。

v2.0 之前：funtype part → 固定技术栈查表（一个 funtype 对应固定栈）——已退役。
v2.1 起：与 skill/task 分类同型的**逐 JD 内容归类**（技术栈为多标签，1-4 类）：
  1. 词库快路（source=rule，零 LLM）：JD 标题/正文命中体系 keywords
     （common.rule_stacks → StackMatchers）→ 直接划分。技术栈名词在 JD 中
     密度高，词库命中即可信，命中部分零成本
  1b. 必空排除表（零 LLM）：词库未命中且标题属非软件域（工艺/机械/化工/航空/
     技工/职能类等，common.EXCLUDE_TITLE_WORDS）→ 直接空栈不送 LLM
  2. LLM 兜底（source=llm）：其余未命中 JD（标题 + 正文前 600 字）按 batch=20 送
     deepseek-v4-flash 多标签归类；按文本指纹（common.jd_text_key，归一化 md5）
     跨 JD 去重——同文只判一次（全量重复文本约 28.6%）；progress 断点续跑

产物 output/jd_stack_cache.jsonl（**只存 LLM 判定**，词库命中由行级引擎在线重算，
避免缓存文件与数据集同量级）：
  {"key": "<md5>", "title": "...", "stacks": ["TS-xx", ...], "confidence": 0-1, "source": "llm"}
行级引擎 annotate_jd.py 按 同口径 key 查表（tier 3）。

用法：
  python classify_stacks.py --stats                                    # 零 LLM：去重规模 + 词库命中率
  python classify_stacks.py --stats --files job_2026_1_1.csv --limit 5000
  python classify_stacks.py --files job_2026_1_1.csv --limit 3000      # 测试范围全流程
  python classify_stacks.py --dry-run --files job_2026_1_1.csv --limit 3000  # 预览送 LLM 的条数与样例
  python classify_stacks.py                                            # 全量（LLM 兜底，断点续跑）

内存边界：指纹集合为全量去重 JD 数（数百万级，每条约 50 字节 set 开销）；
词库未命中暂存 (title, 正文600字) 待判条目——命中率低时该 dict 较大，全量跑前
先用 --stats 评估规模。
"""
import argparse
import csv
import json
import os
import sys
import time

import common

PROGRESS = os.path.join(common.OUT_DIR, "jd_stack_progress.jsonl")
BATCH = 20          # 每次 LLM 调用的 JD 条数
BODY_CHARS = 600    # 送 LLM 的正文摘录长度
MAX_TOKENS = 4000

PROMPT = """你是招聘JD技术栈标注器。给定人工确定的八类技术栈体系（code: 名称 — 说明），判断每条 JD 涉及哪些技术栈。
技术栈体系：
{stack_list}

规则：
- 每条 JD 可命中 1-4 个技术栈（多标签，按相关度排序）；核心工作不涉及任何类别时 stacks 为空数组
- 只依据 JD 的标题与正文摘录判断，不要过度推断；优先依据明确出现的技术、工具、语言、平台与职责领域
严格只输出一个 JSON 数组，不要任何其他文字，格式：
[{{"id":1,"stacks":["TS-xx"],"confidence":0到1}}, ...]
待分类 JD（id. 标题 | 正文摘录）：
{items}}"""


def iter_jd_rows(files, limit=None, jd_dir=None):
    """按 --files（逗号分隔）迭代 (fn, title, text) 行。

    每个 name：含路径分隔符或为绝对路径 → 按给定路径直读（支持 timeline 月度 CSV
    data/timeline/jd/{窗口}.csv 等非 data/jd_dataset 来源）；裸文件名 → 仍相对 data/jd_dataset。
    无 --files → 全量 data/jd_dataset。
    """
    jd_dir = jd_dir or common.JD_DIR
    names = [f.strip() for f in files.split(",")] if files else sorted(
        f for f in os.listdir(jd_dir) if f.endswith(".csv"))
    for fn in names:
        # 含分隔符/绝对路径 → 直读该路径；裸文件名 → 相对 data/jd_dataset
        path = fn if (os.path.isabs(fn) or "/" in fn or os.path.sep in fn) else os.path.join(jd_dir, fn)
        if not os.path.exists(path):
            print(f"[skip] 不存在: {path}")
            continue
        n = 0
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
            for row in csv.DictReader(fh):
                if limit and n >= limit:
                    break
                n += 1
                yield fn, (row.get("job") or "").strip(), row.get("job_information") or ""


def collect_misses(files, limit=None):
    """单遍扫描 → 词库快路 + 必空排除 → 返回 (misses, stats)。

    misses: key → {"title":…, "body": 正文前 BODY_CHARS 字}（待 LLM）
    stats: rows / unique / tier1(标题) / tier2(正文) / excluded(排除表判空) / miss 计数
    排除表（common.EXCLUDE_TITLE_WORDS）：词库未命中且标题属非软件域 → 直接空栈不送 LLM。
    """
    taxonomy = common.load_taxonomy()
    matchers = common.StackMatchers(taxonomy)
    misses, seen = {}, set()
    stats = {"rows": 0, "unique": 0, "tier1": 0, "tier2": 0, "excluded": 0, "miss": 0}
    for _, title, text in iter_jd_rows(files, limit):
        stats["rows"] += 1
        key = common.jd_text_key(title, text)
        if key in seen:
            continue
        seen.add(key)
        stats["unique"] += 1
        _, tier = common.rule_stacks(matchers, title, text)
        if tier == 1:
            stats["tier1"] += 1
        elif tier == 2:
            stats["tier2"] += 1
        elif common.is_excluded_title(title):
            stats["excluded"] += 1
        else:
            stats["miss"] += 1
            misses[key] = {"title": title, "body": text[:BODY_CHARS]}
    return misses, stats


def load_progress(valid_keys):
    done = {}
    if os.path.exists(PROGRESS):
        with open(PROGRESS, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    for k, v in json.loads(line).items():
                        if k in valid_keys:
                            done[k] = v
    return done


def common_cj():
    sys.path.insert(0, os.path.abspath(os.path.join(common.HERE, "..", "job_classify_51job")))
    import classify_jobs
    return classify_jobs


def llm_classify(api_key, model, misses, stack_list, done, max_batches=0):
    """词库未命中且不在断点中的 JD → 分批 LLM 归类，逐批 append progress。"""
    valid_codes = set(common.load_taxonomy())
    pending = [k for k in misses if k not in done]
    if not pending:
        return done
    print(f"LLM 待处理 {len(pending)} 条（batch={BATCH}）...", flush=True)
    n_batches = 0
    for i in range(0, len(pending), BATCH):
        if max_batches and n_batches >= max_batches:
            print(f"已达 --max-batches {max_batches}，停止（剩余 {len(pending) - i} 条留断点续跑）", flush=True)
            break
        chunk = pending[i:i + BATCH]
        items = "\n".join(
            f"{j+1}. {misses[k]['title'] or '(无标题)'} | {misses[k]['body']}"
            for j, k in enumerate(chunk))
        prompt = (PROMPT.replace("{stack_list}", stack_list)
                  .replace("{items}", items))
        result = {}
        try:
            entries = common_cj().call_api(api_key, model, prompt,
                                           max_tokens=MAX_TOKENS, disable_thinking=True)
            by_id = {int(e.get("id", 0)): e for e in entries if isinstance(e, dict)}
            for j, k in enumerate(chunk, 1):
                e = by_id.get(j) or {}
                stacks = [s for s in (e.get("stacks") or []) if s in valid_codes]
                result[k] = {"key": k, "title": misses[k]["title"],
                             "stacks": stacks[:4],
                             "confidence": float(e.get("confidence", 0.5)),
                             "source": "llm"}
        except Exception as e:
            if len(chunk) > 1:  # 整批失败 → 对半重试，单条仍失败才落 fail 标记
                time.sleep(2)
                llm_classify(api_key, model, {k: misses[k] for k in chunk[:1]},
                             stack_list, done, 0)
                llm_classify(api_key, model, {k: misses[k] for k in chunk[1:]},
                             stack_list, done, 0)
                continue
            print(f"  [fail] 单条 {chunk}: {e}", flush=True)
            for k in chunk:
                result[k] = {"key": k, "title": misses[k]["title"], "stacks": [],
                             "confidence": 0.0, "source": "llm",
                             "error": str(e)[:60]}
        done.update(result)
        n_batches += 1
        with open(PROGRESS, "a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
        print(f"  进度 {min(i + BATCH, len(pending))}/{len(pending)}", flush=True)
    return done


def write_cache(done):
    """LLM 判定（断点合并后）→ jd_stack_cache.jsonl（覆盖写，幂等）。"""
    os.makedirs(common.OUT_DIR, exist_ok=True)
    with open(common.JD_STACK_CACHE, "w", encoding="utf-8") as f:
        for k, v in sorted(done.items()):
            f.write(json.dumps(v, ensure_ascii=False) + "\n")
    n_stack = sum(1 for v in done.values() if v["stacks"])
    print(f"\n缓存已写出：{common.JD_STACK_CACHE}")
    print(f"LLM 判定 {len(done)} 条（有栈 {n_stack} / 空 {len(done) - n_stack}）")


def main():
    ap = argparse.ArgumentParser(description="JD 内容 → 技术栈归类（词库快路 + LLM 兜底）")
    ap.add_argument("--files", default="", help="逗号分隔文件名（相对 data/jd_dataset），默认全部")
    ap.add_argument("--limit", type=int, default=None, help="每文件最多处理行数（测试用）")
    ap.add_argument("--stats", action="store_true", help="零 LLM：只统计去重规模与词库命中率")
    ap.add_argument("--dry-run", action="store_true", help="预览将送 LLM 的条数与样例（不调 API）")
    ap.add_argument("--max-batches", type=int, default=0, help="最多 LLM 调用批数（0=不限，测试用）")
    ap.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"))
    args = ap.parse_args()

    print("扫描 JD（词库快路 + 必空排除判定）...", flush=True)
    misses, st = collect_misses(args.files, args.limit)
    rule_cov = (st["tier1"] + st["tier2"]) / max(st["unique"], 1) * 100
    print(f"行 {st['rows']} → 去重 {st['unique']} | 词库命中：标题 {st['tier1']} + 正文 {st['tier2']}"
          f" = {st['tier1'] + st['tier2']}（{rule_cov:.1f}%）"
          f" | 排除表判空 {st['excluded']} | 未命中待 LLM {st['miss']}")
    if st["miss"]:
        est = (st["miss"] + BATCH - 1) // BATCH
        print(f"预计 LLM 调用 {est} 次（batch={BATCH}）")
    if args.stats:
        return
    if args.dry_run:
        print("\n待送 LLM 样例（前 5 条）：")
        for k in list(misses)[:5]:
            print(f"  [{misses[k]['title'] or '(无标题)'}] {misses[k]['body'][:80]}…")
        return
    if not misses:
        print("全部词库命中，无需 LLM")
        return

    done = load_progress(set(misses))
    if done:
        print(f"断点恢复：已完成 {len(done)} 条")
    if len(done) < len(misses):
        cj = common_cj()
        api_key = cj.load_api_key("")
        if not api_key:
            print("错误：未找到 API key", file=sys.stderr)
            sys.exit(1)
        stack_list = "\n".join(f"{c}: {n['name_zh']} — {n['description']}"
                               for c, n in sorted(common.load_taxonomy().items()))
        done = llm_classify(api_key, args.model, misses, stack_list, done, args.max_batches)
    write_cache(done)


if __name__ == "__main__":
    main()
