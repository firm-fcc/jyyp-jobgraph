# -*- coding: utf-8 -*-
"""JD ΔG v2：采样基面确定性扫描 + 残差 LLM 裁决（逐窗时序发现通道，2026-08-27）。

替代旧 `builder/run_jd_delta.py` 的 100 条抽样投喂（覆盖率 ~0.2%，低频新信号漏检）。

**数据基面 = Stage S 降采样后的选择集**（采样是成本所致的数据量限制，图谱的一切产物
只能来自基面内数据；A/D0 的全量属入口过滤，采样后的环节不再消费基面外文档）。
确定性部分扫基面内全部文档，LLM 只裁决频次过槛的残差候选。

复用与分工（与 HotUpdater 的关系见 graph/README「参数重放操作面」）：
- 本模块 = `builder/HotUpdater` 引擎（propose→supervise→apply→重检）换投喂与落点：
  投喂 = 基面差集的残差候选（而非抽样 JD 全文），落点 = ΔG 叠层（而非直接改体系文件）；
  `run_builder`（HotUpdater 原样）仍负责离线体系构建/大修。
- 产物与旧 jd_delta.json 同 schema（DeltaStore.confirm_named 写入），
  快照/合成/转正消费方零改动。

通道：
- 发现① 英文 token 差集：任职/职责段抽英文词（含数字前缀如 5G、版本号、+/# 后缀）→
  norm 折叠后对已知词表差集；
- 发现② 中文 n-gram 时间差分：CJK 连续段抽 2-8 字片段按**文档频**统计（Apriori 逐级
  上卷控内存：k-gram 只在其两个边界 (k-1)-gram 均过频时才计数，无漏检），双背景差分
  （已知中文词表 + 裁决缓存中的非技术判定）+ df 带宽 [min_docs, max_df_ratio·N]
  （下限砍随机搭配，上限砍通用语言搭配）→ 子串归约（同簇并最长片段）；
- 裁决：LLM 批量判定是否技术载体 → 规范名 + 类型；**只有 task/skill（经新颖性守门）
  落 ΔG 叠层**——JD 侧体系级演化的唯一发现通道。skillpoint（发现权威在 B 阶段抽取 +
  三层归一）与 alias（被涵盖短语，市场存在已由基图统计）只入跨窗缓存排水。
  首窗冷启动为一次性的"背景学习"（通用搭配涌入带内，裁决一遍后逐窗衰减）；
- 确证：**已迁移至 Stage B 叠层分类参与**（run_jd_extract Pass 4，2026-08-30）——
  原算法设计：出生窗早于本窗的参与实体作为临时标签注入分类提示词，与既有技能/任务
  一起在分类任务中运行，命中（含同义/近义表述）→ require 级证据（confirm_named 落
  jd_delta.json，按 doc_id 幂等）。本模块的子串预筛确证通道已退役（全名精确匹配漏
  语域错配：论文学名 vs JD 俗名，19 实体 9 窗 0 命中）。

不做（沿用旧口径）：不从 JD 发现新岗位（new_jobs）；命中基线体系的提及不入叠层
（基图频次域 E_jd 已覆盖，避免重复计权）。

用法（项目根目录）：
  python codes/graph/jd_delta_v2.py --window 2022-06 --dry-run   # 零 LLM：池统计 + TOP 候选预览
  python codes/graph/jd_delta_v2.py --window 2022-06             # 全流程（发现裁决；确证在 Stage B）

参数：settings.yaml → jd_delta_v2（min_docs / max_df_ratio / max_candidates）。
裁决缓存：codes/graph/output/jd_v2_adjudication.jsonl（跨窗终身复用）。
"""
import argparse
import calendar
import csv
import json
import os
import re
import sys
import time
import types
from collections import defaultdict
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))

# ---- 导入次序（config 缓存舞步，同 paper_delta/make_extractors 约定）----
# 1) run_jd_extract（同目录）：其 import graph_config 会把 builder 版 config 缓存进
#    sys.modules["config"]，并带来 jd_text_key/_kept_text/load_full_classification；
# 2) builder 侧（delta_store/hot_update/participation）在 builder config 环境导入；
# 3) extractor 侧（text_split/llm_client）用 config 换出习语导入。
import run_jd_extract as rje  # noqa: E402

_BUILDER_DIR = os.path.join(REPO, "codes", "builder")
if _BUILDER_DIR not in sys.path:
    sys.path.insert(0, _BUILDER_DIR)
import config as builder_config  # noqa: E402  builder 版（graph_config 已缓存同一对象）
from delta_store import DeltaStore  # noqa: E402
from hot_update import HotUpdater  # noqa: E402
from participation import participating_items  # noqa: E402

import common as ann_common  # noqa: E402  jd_annotate（run_jd_extract 已置入 sys.path）

from skillpoint_norm import REGISTRY_PATH, ALIAS_CACHE_PATH, norm_key  # noqa: E402


def _extractor_imports():
    """text_split / LLMClient 需 extractor 版 config（SENTENCE_* / load_api_key），换出导入。"""
    ext = os.path.join(REPO, "codes", "extractor")
    if ext in sys.path:
        sys.path.remove(ext)
    sys.path.insert(0, ext)
    saved = sys.modules.pop("config", None)
    try:
        import text_split
        from llm_client import LLMClient
        return text_split, LLMClient
    finally:
        if saved is not None:
            sys.modules["config"] = saved
        else:
            sys.modules.pop("config", None)


def _builder_env_imports():
    """taxonomy_mapper.load_base_labels 读 builder config 的体系路径，须在 builder 环境导入。"""
    from taxonomy_mapper import load_base_labels
    return load_base_labels


# ---------------- 参数（settings.yaml → jd_delta_v2） ----------------
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


MIN_DOCS = _setting("jd_delta_v2", "min_docs", default=5)
MAX_DF_RATIO = _setting("jd_delta_v2", "max_df_ratio", default=0.05)
MAX_CANDIDATES = _setting("jd_delta_v2", "max_candidates", default=300)
ADJ_BATCH = _setting("jd_delta_v2", "adj_batch", default=50)
NGRAM_MIN, NGRAM_MAX = 2, 8
ZH_MIN_LEN = _setting("jd_delta_v2", "zh_min_len", default=3)   # 中文候选最短字数：
# 2 字候选被通用词（命令/建立/承担/管理）淹没，真技术中文名多为 3+ 字（低代码/
# 提示词工程）；2 字真词（信创/等保）由论文源与 L3 兜底。上限 8 让"掌握数据结构和
# 算法"级长短语全形入表（右续延检验才不至于在帽檐处失明；Apriori 逐级上卷使长级
# 计数成本极小）。
EVIDENCE_CAP = 5          # 每候选证据句上限（与 MENTION_EVIDENCE_CAP 同口径）
ADJ_CACHE_PATH = os.path.join(HERE, "output", "jd_v2_adjudication.jsonl")

_EN_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\+#\.]{1,17}")
_HAS_LETTER_RE = re.compile(r"[A-Za-z]")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{%d,}" % NGRAM_MIN)

# 中文候选的边界修剪（保守词表）：n-gram 常把动词/要求语境词卷进真名（"熟悉提示词工程"
# → 候选"熟悉提示词工"）。修剪只减 LLM 批量，语义兜底仍在裁决层（规范名由 LLM 给出）。
_CONTEXT_PREFIXES = ("熟悉", "掌握", "精通", "了解", "具备", "具有", "熟练", "负责", "要求",
                     "需要", "使用", "进行", "参与", "协助", "完成", "独立", "深入", "擅长",
                     "相关", "良好", "优秀", "以及", "任职", "优先", "较强的", "扎实的",
                     "基本的", "一定的", "本科", "大专", "有", "善于", "能够")
_CONTEXT_SUFFIXES = ("经验", "能力", "要求", "相关", "工作", "背景", "优先", "人员",
                     "知识", "技能", "水平", "意识", "精神", "思维", "学历", "的")

# 英文功能词（英文成句 JD 的 to/and/in 类；一次性清单，避免每窗送裁）
_EN_STOPWORDS = frozenset("""a an and are as at be been by for from has have in into is it its
of on or our ours out over the to under up we were who with you your will shall can may that
this these those they them their there here when where which while what how why not no yes
must should would need needs other others more most many much less least very well good
skills skill required preferred plus strong excellent etc""".split())


def _trim_context(name):
    """迭代剥掉候选名首尾的语境词（可剥至空=纯语境词，返回空串）。"""
    prev = None
    while prev != name:
        prev = name
        for p in _CONTEXT_PREFIXES:
            if len(name) >= len(p) and name.startswith(p):
                name = name[len(p):]
                break
        for s in _CONTEXT_SUFFIXES:
            if len(name) >= len(s) and name.endswith(s):
                name = name[:-len(s)]
                break
    return name


_EDGE_FUNC_CHARS = set("的了与和或等及在对从以及被把")


def _section_split(text):
    """JD 正文 → (kept_text 非 other 段, req_text 任职要求段)。

    复用 run_jd_extract 的段头分类；要求段文本供中文候选的"要求段亲和度"排序
    （技术名词集中在任职要求段，职责性搭配集中在岗位职责段）。
    """
    lines = text.splitlines()
    secs, cur_kind, cur = [], "unknown", []
    for line in lines:
        kind = rje._classify_header(line)
        if kind:
            if cur:
                secs.append((cur_kind, cur))
            cur_kind, cur = kind, []
        else:
            cur.append(line)
    if cur:
        secs.append((cur_kind, cur))
    kept = [l for kind, ls in secs for l in ls if kind != "other"]
    req = [l for kind, ls in secs if kind == "req" for l in ls]
    return ("\n".join(kept) if kept else text), "\n".join(req)


def window_end_date(window):
    """YYYY-MM → 窗末日 date。"""
    y, m = int(window[:4]), int(window[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def window_start_date(window):
    """YYYY-MM → 窗首日 date（确证通道的参与口径：出生窗不确证，至少滞后一窗）。"""
    return date(int(window[:4]), int(window[5:7]), 1)


# ================= 已知词表 =================
def build_known_vocab():
    """已知词表 norm 键集合 + 基线体系标签。

    词表构成（norm 折叠后并集）：49 技能（中英名）/ 27 任务（中英名）/ 131 岗位（中英名，
    岗位名不应作为新信号出现）/ 技能点注册表 137（canonical+aliases+retired 键）/
    L3 别名缓存（历史窗口沉淀）/ 八类技术栈 keywords+aliases。
    """
    keys = set()
    load_base_labels = _builder_env_imports()
    labels = load_base_labels()
    for tax, items in labels.items():
        for l in items:
            for nm in (l.get("name_zh"), l.get("name_en")):
                if nm:
                    keys.add(norm_key(nm))
    reg = json.load(open(REGISTRY_PATH, encoding="utf-8"))
    curated = reg.get("curated") or {}          # {canonical: {category, aliases}}
    for canon, d in curated.items():
        keys.add(norm_key(canon))
        for a in (d.get("aliases") or []):
            keys.add(norm_key(a))
    for old in (reg.get("retired") or {}):
        keys.add(norm_key(old))
    if os.path.exists(ALIAS_CACHE_PATH):
        with open(ALIAS_CACHE_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                for nm in (d.get("name"), d.get("canonical")):
                    if nm:
                        keys.add(norm_key(nm))
    tax = ann_common.load_taxonomy()            # 直接返回 {TS-xx: {...}} 明细
    detail = (tax or {}).get("detail")
    if not detail and isinstance(tax, dict):
        detail = tax
    for cat in (detail or {}).values():
        for nm in (cat.get("keywords") or []) + (cat.get("aliases") or []) \
                + [cat.get("name_zh"), cat.get("name_en")]:
            if nm:
                keys.add(norm_key(str(nm)))
    keys.discard("")
    return keys, labels


# ================= 裁决缓存 =================
def load_adjudication_cache():
    """{候选 norm 键: 缓存行}。同窗幂等、跨窗终身复用。"""
    cache = {}
    if os.path.exists(ADJ_CACHE_PATH):
        with open(ADJ_CACHE_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                cache[d.get("key", "")] = d
    return cache


def append_adjudication(rows):
    os.makedirs(os.path.dirname(ADJ_CACHE_PATH), exist_ok=True)
    with open(ADJ_CACHE_PATH, "a", encoding="utf-8") as f:
        for d in rows:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")


# ================= 全量扫描 =================
def scan_window_docs(window, limit=None, strict=True):
    """窗口 CSV → 去重唯一 IT JD 列表（句级切分，去福利/公司介绍段）。

    复用 Stage A 窗口归类缓存（load_full_classification：it_scope 在线过滤）与
    B 阶段同款 _kept_text 分段 + text_split 分句。返回 [{jobid, opentime, job_code,
    funtype, jd_key, sents}]。
    """
    text_split, _ = _extractor_imports()
    csv_path = os.path.join(rje.TIMELINE_JD_DIR, f"{window}.csv")
    if not os.path.exists(csv_path):
        sys.exit(f"[v2] timeline CSV 不存在：{csv_path}")
    t0 = time.time()
    cls_map, _cls_stats = rje.load_full_classification(csv_path, strict=strict)
    # Stage D0 近重复（抄袭）过滤：变体不进发现/确证的文档池
    import jd_dedup
    near_dup = jd_dedup.load_variants(window)
    # 数据基面 = Stage S 降采样后的选择集。采样是成本所致的数据量限制——图谱的一切
    # 产物（含本通道的发现与确证）只能来自采样后的数据；A/D0 的全量属于入口过滤，
    # 采样之后的环节不得再消费基面外文档。采样未触发（keys=null）时全量即基面。
    sample_path = os.path.join(rje.gconfig.JD_DERIVED_DIR,
                               rje.gconfig.JD_SAMPLE_FILENAME.format(window=window))
    sample_keys = None
    if os.path.exists(sample_path):
        srec = json.load(open(sample_path, encoding="utf-8"))
        sample_keys = srec.get("keys")        # {jd_key: weight}；null=未触发降采样
        if sample_keys:
            print(f"[basis] 数据基面 = 采样选择集 {len(sample_keys)} 键")
        else:
            print("[basis] sample.json 未触发降采样（全量即基面）")
    n_rows, docs, seen = 0, [], set()
    with open(csv_path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        for row in csv.DictReader(fh):
            n_rows += 1
            text, title = row.get("job_information") or "", row.get("job") or ""
            key = ann_common.jd_text_key(title, text)
            if key in seen:
                continue
            seen.add(key)
            if key in near_dup:
                continue
            if sample_keys is not None and key not in sample_keys:
                continue                      # 基面外文档不进发现/确证池
            c = cls_map.get(key) or {}
            if not c.get("it_related"):
                continue
            kept, req = _section_split(text)
            docs.append({"jobid": row.get("jobid") or "", "opentime": row.get("opentime") or "",
                         "job_code": c.get("job_code") or "", "funtype": row.get("funtype") or "",
                         "jd_key": key, "sents": text_split.split_sentences(kept),
                         "req_sents": text_split.split_sentences(req)})
            if limit and len(docs) >= limit:
                break
    print(f"[scan] {window}: {n_rows} 行 → 唯一 IT JD {len(docs)} 条（归类+分句 {time.time()-t0:.0f}s）")
    return docs


def _doc_tokens(sents):
    """一句列表 → {token_norm: 首个原始拼写}（英文通道，文档级去重，剔功能词）。"""
    local = {}
    for s in sents:
        for m in _EN_TOKEN_RE.findall(s):
            tok = m.strip(".+#")
            if len(tok) < 2 or tok.isdigit() or not _HAS_LETTER_RE.search(tok):
                continue
            k = norm_key(tok)
            if k and k not in local:
                local[k] = tok
    for sw in _EN_STOPWORDS:
        local.pop(sw, None)
    return local


def count_english_df(docs):
    """英文 token 文档频。返回 {norm: df}。"""
    df = defaultdict(int)
    for doc in docs:
        for k in _doc_tokens(doc["sents"]):
            df[k] += 1
    return dict(df)


def _doc_runs(doc):
    return _CJK_RUN_RE.findall(" ".join(doc["sents"]))


def count_chinese_df(docs, min_docs, ngram_max=NGRAM_MAX):
    """中文 n-gram 文档频（Apriori 逐级上卷，无漏检，内存有界）。

    性质：df(k-gram)≥min_docs ⟹ 其每个 (k-1)-gram 的 df 也 ≥min_docs ⟹ 只在两个边界
    (k-1)-gram 均过频时计数不漏任何最终过频的 k-gram。返回 {n: {gram: df}}（仅过频者）。
    """
    # level 2：bigram 全量计数（唯一中文 bigram 有界）
    bi = defaultdict(int)
    runs_cache = [_doc_runs(d) for d in docs]
    for runs in runs_cache:
        local = set()
        for r in runs:
            for i in range(len(r) - 1):
                local.add(r[i:i + 2])
        for g in local:
            bi[g] += 1
    out = {2: {g: c for g, c in bi.items() if c >= min_docs}}
    prev = set(out[2])
    for k in range(3, ngram_max + 1):
        dfk = defaultdict(int)
        for runs in runs_cache:
            local = set()
            for r in runs:
                L = len(r)
                if L < k:
                    continue
                for i in range(L - k + 1):
                    if r[i:i + k - 1] in prev and r[i + 1:i + k] in prev:
                        local.add(r[i:i + k])
            for g in local:
                dfk[g] += 1
        out[k] = {g: c for g, c in dfk.items() if c >= min_docs}
        prev = set(out[k])
        if not prev:
            break
    return out


def _count_req_df(docs, cands):
    """中文候选的"要求段亲和度"：req 段文档频 / 全段文档频（技术名词集中任职要求段）。

    修剪后的名字（≥3 字 ≤6 字）必是原文连续子串，故在 req 段 runs 上重生成同长 gram
    即可命中。就地写 c["req_df"] / c["req_ratio"]。
    """
    keys = {c["key"] for c in cands}
    if not keys:
        return
    df = defaultdict(int)
    for doc in docs:
        local = set()
        for s in doc.get("req_sents") or []:
            for r in _CJK_RUN_RE.findall(s):
                for k in range(3, min(NGRAM_MAX, len(r)) + 1):
                    for i in range(len(r) - k + 1):
                        g = norm_key(r[i:i + k])
                        if g in keys:
                            local.add(g)
        for g in local:
            df[g] += 1
    for c in cands:
        c["req_df"] = df.get(c["key"], 0)
        c["req_ratio"] = round(c["req_df"] / c["df"], 3) if c["df"] else 0.0


def pool_candidates(docs, known_norms, adj_cache, min_docs=MIN_DOCS,
                    max_df_ratio=MAX_DF_RATIO, n_docs=None):
    """双通道 → 词表/缓存差集 → 边缘函数字过滤 → 子串归约 → 语境词修剪 → 带宽上限
    （中文按修剪后终名的真实 df）→ 要求段亲和度排序 → 通道轮转合并。返回 (候选列表, 统计)。

    中文处理次序有讲究：子串归约必须在修剪**之前**（修剪会把"以上相关"削成"以上"而
    消失，留下碎片"以上相"成孤儿）；碎片判定为"A 是更长候选 B 的真子串且 df(A) ≤
    1.2×df(B) → 弃 A"。中文排序键 = (要求段亲和度 desc, df desc)；英文 = df desc；
    两通道按序轮转合并，保证裁决配额内都有代表。
    """
    n = n_docs if n_docs is not None else len(docs)
    ceil = max(1, int(max_df_ratio * n))
    stats = {"n_docs": n, "df_band": [min_docs, ceil]}

    en_df = count_english_df(docs)
    zh_levels = count_chinese_df(docs, min_docs)

    n_en_band = n_zh_band = n_en_vocab = n_zh_vocab = n_cached = n_edge = n_ceil = 0
    en_pool = []
    for k, df in en_df.items():
        if df < min_docs:
            continue
        n_en_band += 1
        if k in known_norms:
            n_en_vocab += 1
            continue
        if k in adj_cache:
            n_cached += 1
            continue
        en_pool.append({"key": k, "name": k, "channel": "en", "df": df})

    # 中文：收全量过频 gram（不做词表/带宽过滤——词表命中或超上限的父词必须留在
    # 归约集里当"碎片杀手"，否则"软件开发"被提前吸收后其碎片"件开发"成孤儿）
    zh_raw = []
    for level, grams in zh_levels.items():
        for g, df in grams.items():
            n_zh_band += 1
            if g[0] in _EDGE_FUNC_CHARS or g[-1] in _EDGE_FUNC_CHARS:
                n_edge += 1                          # "的团队合作精"/"与产品"类跨界碎片
                continue
            zh_raw.append({"key": norm_key(g), "name": g, "channel": "zh", "df": df})

    # 子串归约（修剪与带宽之前）：按 df 降序遍历每个候选，枚举其自身子串查哈希集
    # 标记碎片——复杂度 O(候选数×子串数)，替代 O(n²) 两两比对（全窗池数十万候选时
    # 两两比对不可行）。碎片：A 是更长候选 B 的真子串且 df(A) ≤ 1.2×df(B)。
    zh_raw.sort(key=lambda c: -c["df"])
    by_name = {c["name"]: c for c in zh_raw}
    frag_names = set()
    for b in zh_raw:
        bs, L = b["name"], len(b["name"])
        for m in range(2, L):                   # 真子串（2 ≤ 长度 < L）
            for i in range(L - m + 1):
                c = by_name.get(bs[i:i + m])
                if c is not None and c["df"] <= 1.2 * b["df"]:
                    frag_names.add(c["name"])
    frag_keys = {norm_key(n) for n in frag_names}
    zh_kept = [c for c in zh_raw if c["name"] not in frag_names]
    # 语境词修剪（修剪后二次边缘过滤）→ 同名合并（df 取修剪后终名的 gram 真值）
    # → 带宽上限按终名 df → 续延检验（防修剪与 k-gram 截断制造的孤儿碎片）
    raw_df = {}
    ext_right = {}                        # 去尾形式 → 右延展形的最大 df（碎片检验用）
    for lv in zh_levels.values():
        for g, df in lv.items():
            raw_df[norm_key(g)] = df
            if len(g) >= 3:
                ext_right[g[:-1]] = max(ext_right.get(g[:-1], 0), df)
    zh_merged = {}
    for c in zh_kept:
        if norm_key(c["name"]) in known_norms:
            n_zh_vocab += 1
            continue
        name = _trim_context(c["name"])
        if len(name) < ZH_MIN_LEN or name[0] in _EDGE_FUNC_CHARS or name[-1] in _EDGE_FUNC_CHARS:
            continue
        k = norm_key(name)
        if k in frag_keys:               # 修剪借尸还魂：罕见长变体修剪出的碎片名再拦一次
            continue
        if k in adj_cache:
            n_cached += 1
            continue
        if k in known_norms:
            n_zh_vocab += 1
            continue
        df_final = raw_df.get(k, c["df"])
        if df_final > ceil:              # 带宽按修剪后终名的真实 df：中频变体（"年以上
            n_ceil += 1                  # 经验"带内）修剪出的超限短词（"年以上"）在此封死
            continue
        if k not in zh_merged or zh_merged[k]["df"] < df_final:
            zh_merged[k] = {"key": k, "name": name, "channel": "zh",
                            "df": df_final}
    # 右续延检验：候选右侧接某固定字符的文档频 ≥0.8×自身 ⇒ 几乎从不独立成词，是碎片
    # （"开发经"→"开发经验"占 889/905；帽檐碎片"握数据结构和算"→"…算法"）。只看右
    # 侧——中文动词左粘附常见（"掌握数据结构和算法"），左确定性对真词误杀。修剪产生
    # 的名字同样要过检。
    n_frag_ext = 0
    zh_pool = []
    for c in zh_merged.values():
        r = ext_right.get(c["name"], 0)
        if r >= 0.8 * c["df"] and r >= min_docs:
            n_frag_ext += 1
            continue
        zh_pool.append(c)
    _count_req_df(docs, zh_pool)

    en_pool.sort(key=lambda c: -c["df"])
    zh_pool.sort(key=lambda c: -c["df"])   # 要求段亲和度见候选的 req_ratio 字段（统计用）
    pool, i, j = [], 0, 0
    while i < len(en_pool) or j < len(zh_pool):
        if i < len(en_pool):
            pool.append(en_pool[i])
            i += 1
        if j < len(zh_pool):
            pool.append(zh_pool[j])
            j += 1
    stats.update({"en_band": n_en_band, "zh_band": n_zh_band,
                  "hit_vocab": n_en_vocab + n_zh_vocab, "hit_cache": n_cached,
                  "edge_filtered": n_edge, "ceil_filtered": n_ceil,
                  "ext_fragments": n_frag_ext,
                  "pool_en": len(en_pool), "pool_zh": len(zh_pool),
                  "reduced_fragments": len(zh_raw) - len(zh_kept)})
    return pool, stats


def collect_evidence(docs, selected, cap=EVIDENCE_CAP):
    """为选中候选补证据句（单遍扫描，全部满 cap 即提前终止）。

    候选 key 在句中的出现：英文按 token norm 命中（_doc_tokens），中文按 n-gram 子串
    （从 CJK 连续段生成 2-6 gram 对照 selected 键集）。就地写 c["evidence"] =
    [(doc下标, 句子)]，返回更新后的候选列表。
    """
    need = {c["key"]: c for c in selected}
    for c in selected:
        c["evidence"] = []
    for di, doc in enumerate(docs):
        if not need:
            break
        tok_norms = set(_doc_tokens(doc["sents"]))
        grams = set()
        for r in _doc_runs(doc):
            for k in range(NGRAM_MIN, min(NGRAM_MAX, len(r)) + 1):
                for i in range(len(r) - k + 1):
                    g = r[i:i + k]
                    if norm_key(g) in need or g in need:
                        grams.add(norm_key(g))
        hits = (tok_norms | grams) & set(need)
        for k in hits:
            c = need[k]
            if len(c["evidence"]) >= cap:
                continue
            for s in doc["sents"]:
                if _cand_in_sentence(c, s):
                    c["evidence"].append((di, s.strip()))
                    break
        need = {k: c for k, c in need.items() if len(c["evidence"]) < cap}
    return selected


def _cand_in_sentence(cand, sent):
    """候选是否出现在句中（英文按 norm token 命中，中文按子串）。"""
    if cand["channel"] == "zh":
        return cand["name"] in sent
    return cand["key"] in set(_doc_tokens([sent]))


# ================= LLM 裁决（HotUpdater 注入） =================
# 四种落点：alias（涵盖式映射，最优先）/ skillpoint（具体技术点，最常见）/
# task、skill（体系外新职责/能力域——须过独立的新颖性复核守门，见 _novelty_recheck）
_KIND_ARRAY = {"skillpoint": "skillpoints", "skill": "new_skills", "task": "new_tasks"}
NOVELTY_BATCH = 25          # 新颖性复核批大小（含证据句，批宜小）

_ADJ_PROMPT = """你是招聘数据的技术术语鉴定器。以下候选词来自某月 IT 岗位 JD 的任职要求文本，
已经过已知技术词表差集筛选（均不在词表中），并按文档频排序。请逐一判定每个候选是否为
【技术载体】——可独立学习、可考核的技术/方法/工具/平台/认证。

任务/技能边界与命名纪律（各通道统一，2026-08-30）：
- 任务 = 承担的工作职责/活动（动词性表述，"做什么"，如 多模态数据融合建模）；
- 技能 = 可学习掌握的能力/方法/知识（名词性表述，"会什么"，如 提示工程）；
- 命名一律从**从业者视角**（应聘者/员工做什么、会什么），不从机器/系统视角——
  人可"做"仿真数据增强，但不能"做"机器人技能学习（那是机器在学，人做的是机器人技能示教）；
- 同一名称不得同时判为任务与技能（跨类重名禁止；二者必择其一）；
- 命名取精要（一般 ≤10 字）：去"与/及/和"并列连接与"能力/技术"类冗缀，取单一核心概念，
  便于与市场文本（JD 措辞）对齐；不造冗长学名。

判定规则（落点优先级：alias 最先 → skillpoint → 仅在确无涵盖时 task/skill）：
- 是技术载体 → 给出规范名（该技术以英文名行世则保留英文写法）、可选英文名、类别 kind：
  * alias：含义可被基线体系某技能/任务**涵盖** → 给 alias_to。涵盖包括：同物异名
    （系统架构 = 系统架构设计）、上下位包含（前端开发 ⊂ 应用软件开发、PoC ⊂ 售前技术支持、
    数据迁移 ⊂ 数据库开发与管理）、近义指称（日常维护 ≈ 系统运维）。判定时先问
    "这是否只是某个基线条目所描述工作的另一种说法或一个子集"，是则必须 alias。
  * skillpoint：细粒度技术点（一门语言/框架/库/工具/协议/认证，如 Flink、AUTOSAR）——最常见
  * task：**基线任何任务都无法涵盖**（含上下位）的全新工作职责域（如 2023 年前的
    "大模型应用开发"）→ 必须给 nearest（最接近的基线 code）与 why_not（为何它也不涵盖）
  * skill：**基线任何技能都无法涵盖**的全新能力域 → 同样必须给 nearest 与 why_not
  task/skill 判定会另行经独立的新颖性复核（复核基调宁严勿宽，宁映射勿新增）；
  拿不准时初判就应降级为 alias，不要把把握不足的候选推给复核。
- 排除（is_tech=false）：公司名、产品型号、地名、学历/职能词、通用商务与语言搭配
  （"相关专业""良好沟通"）、泛化修饰词。
- 不同代际/相邻技术不得合并（C≠C++≠C#）；版本号并入母项（Vue3→Vue）。

基线体系（alias 判定参照）：
{base_labels}

候选（输入，JSON 数组）：
{candidates}

输出：JSON 数组，每项 {{"key": 同输入, "is_tech": bool, "canonical": str, "name_en": str,
"kind": "skillpoint"|"skill"|"task"|"alias", "alias_to": {{"taxonomy": "skills"|"tasks",
"code": str}} | null, "nearest": str, "why_not": str, "rationale": str}}。即使只有一个
结果也必须包成数组。task/skill 必填 nearest 与 why_not，alias/其余可不填。"""


_NOVELTY_PROMPT = """你是技术体系边界的守门员。以下候选来自 IT 岗位 JD，初判为"全新任务/新技能"。
任务/技能边界与命名纪律（各通道统一，2026-08-30）：
- 任务 = 承担的工作职责/活动（动词性表述，"做什么"，如 多模态数据融合建模）；
- 技能 = 可学习掌握的能力/方法/知识（名词性表述，"会什么"，如 提示工程）；
- 命名一律从**从业者视角**（应聘者/员工做什么、会什么），不从机器/系统视角——
  人可"做"仿真数据增强，但不能"做"机器人技能学习（那是机器在学，人做的是机器人技能示教）；
- 同一名称不得同时判为任务与技能（跨类重名禁止；二者必择其一）；
- 命名取精要（一般 ≤10 字）：去"与/及/和"并列连接与"能力/技术"类冗缀，取单一核心概念，
  便于与市场文本（JD 措辞）对齐；不造冗长学名。

任务/技能体系是人工校准的封闭粗粒度体系——请做新颖性复核：逐条对照基线清单，判断候选含义是否
其实可被某个基线条目**涵盖**。涵盖包括三种：同物异名（系统架构=系统架构设计）、上下位包含
（候选只是该条目所描述工作的一部分，如 前端开发⊂应用软件开发、PoC⊂售前技术支持）、近义指称
（日常维护≈系统运维）。判涵盖的尺度要宽：只要合理的市场沟通中该候选所指的工作会被归入某基线
条目，就算涵盖。

复核基调：**宁严勿宽**——拿不准一律判 covered=true（走映射增强即可，不给体系加新条目；
新条目会进入全管线的标签空间，误加的代价远高于一次映射）。仅当所有基线条目确实都不涵盖、
候选又确属可持续的工作职责/能力域（而非一次性项目措辞）时，才判 covered=false。

基线体系（taxonomy:code 名称）：
{base_labels}

候选（含证据句）：
{items}

输出 JSON 数组，每项 {{"key": 同输入, "covered": bool, "taxonomy": "tasks"|"skills",
"code": str, "nearest": str, "why_not": str}}。covered=true 时 taxonomy/code 必填且必须
是基线中真实存在的 code；covered=false 时 nearest 给最接近的基线 code、why_not 说明为何
它也不涵盖。"""


def _novelty_recheck(llm, pairs, base_labels, batch=NOVELTY_BATCH):
    """task/skill 判定的守门复核（与初判不同的提示视角，偏保守）。

    返回 {key: {"covered": False, "nearest", "why_not"} | {"covered": True, "alias_to"}}；
    复核失败、或 covered=true 但 code 无效的 key 不出现在返回中——调用方按"未决"处理
    （不缓存、不应用，留待下窗重试），不给体系加没把握的新条目。
    """
    label_lines = []
    for tax in ("tasks", "skills"):
        label_lines.extend(f"{tax}:{l['code']} {l['name_zh']}" for l in base_labels.get(tax, []))
    out = {}
    for i in range(0, len(pairs), batch):
        chunk = pairs[i:i + batch]
        items = json.dumps(
            [{"key": r["key"], "name": r["canonical"] or r["name"], "kind": r["kind"],
              "evidence": [s[:120] for _, s in (c.get("evidence") or [])[:2]]}
             for c, r in chunk], ensure_ascii=False)
        prompt = _NOVELTY_PROMPT.format(base_labels="\n".join(label_lines), items=items)
        try:
            rows = llm._post(prompt) or []
        except Exception:
            rows = []
        for v in rows:
            if not isinstance(v, dict) or not v.get("key"):
                continue
            if not v.get("covered"):
                out[v["key"]] = {"covered": False, "nearest": v.get("nearest") or "",
                                 "why_not": v.get("why_not") or ""}
                continue
            tax, code = v.get("taxonomy") or "", v.get("code") or ""
            if any(l["code"] == code for l in base_labels.get(tax, [])):
                out[v["key"]] = {"covered": True,
                                 "alias_to": {"taxonomy": tax, "code": code}}
    return out


class _DeltaShim:
    """HotUpdater 的 store 适配：tasks() 供同名 add 防重，save() 落 ΔG。"""

    def __init__(self, delta):
        self.delta = delta

    def tasks(self):
        return [{"name_zh": it["name_zh"]} for it in self.delta.existing_items()]

    def save(self):
        return self.delta.save()


class _CandidateSource:
    """HotUpdater 的 data_source：按 ADJ_BATCH 吐候选批，断点 = 已消费键。"""

    def __init__(self, candidates, consumed=None):
        self.items = list(candidates)
        self.pos = 0
        if consumed:
            self.pos = sum(1 for c in self.items if c["key"] in consumed)

    def remaining(self):
        return len(self.items) - self.pos

    def next_batch(self, n):
        take = self.items[self.pos:self.pos + n]
        self.pos += len(take)
        return take

    def save_checkpoint(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"consumed": [c["key"] for c in self.items[:self.pos]]}, f)


def _adjudicate_llm(llm, pending, label_lines):
    """裁决批调用 + 失败递归减半；单条仍失败返回空（候选留待重检/下窗，不缓存）。"""
    if not pending:
        return []
    cand_json = json.dumps(
        [{"key": c["key"], "name": c["name"], "channel": c["channel"],
          "df": c["df"], "examples": [s for _, s in (c.get("evidence") or [])[:2]]}
         for c in pending], ensure_ascii=False)
    prompt = _ADJ_PROMPT.format(base_labels="\n".join(label_lines), candidates=cand_json)
    try:
        rows = llm._post(prompt)
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    except Exception:
        pass
    if len(pending) == 1:
        return []
    mid = len(pending) // 2
    return (_adjudicate_llm(llm, pending[:mid], label_lines)
            + _adjudicate_llm(llm, pending[mid:], label_lines))


def make_propose_fn(llm, adj_cache, base_labels, window, delta):
    """HotUpdater.propose_fn：缓存全命中 → covered 不调 LLM；否则批判未裁决候选。

    裁决即落缓存（含 is_tech=false——非技术判定是跨窗背景的一部分）；updates 只携带
    技术判定（canonical 与 ΔG 已有条目同名 → action=merge_evidence，防重检空转；
    existing_names 每次调用现查，首批 apply 后的同名裁决自动转证据合并）。
    """
    label_lines = []
    for tax, items in base_labels.items():
        if tax == "jobs":
            continue
        label_lines.extend(f"{tax}:{l['code']} {l['name_zh']}" for l in items)

    def propose_fn(batch, store):
        pending = [c for c in batch if c["key"] not in adj_cache]
        if not pending:
            return {"covered": True, "updates": []}
        existing_names = {norm_key(it["name_zh"]) for it in delta.existing_items()}
        rows = _adjudicate_llm(llm, pending, label_lines)
        by_key = {r.get("key"): r for r in rows if isinstance(r, dict)}
        built = []                                   # (cand, row)
        for c in pending:
            v = by_key.get(c["key"])
            if v is None:
                continue                       # 本批未裁决 → 留待重检/下窗
            is_tech = bool(v.get("is_tech"))
            canonical = (v.get("canonical") or "").strip()
            kind = v.get("kind") or ""
            alias_to = v.get("alias_to") or None
            raw_kind = kind
            flipped = False
            if is_tech and alias_to:
                tax = (alias_to.get("taxonomy") or "")
                code = (alias_to.get("code") or "")
                if any(l["code"] == code for l in base_labels.get(tax, [])):
                    kind = "alias"
                else:
                    alias_to, is_tech, flipped = None, False, True    # 幻觉 code → 非技术缓存
            elif is_tech and kind not in _KIND_ARRAY:
                alias_to, is_tech, flipped = None, False, True        # 未知 kind → 非技术缓存
            if is_tech and not canonical:
                is_tech, flipped = False, True
            row = {"key": c["key"], "name": c["name"], "channel": c["channel"],
                   "is_tech": is_tech, "canonical": canonical if is_tech else "",
                   "name_en": (v.get("name_en") or "").strip() if is_tech else "",
                   "kind": kind if is_tech else "", "alias_to": alias_to if is_tech else None,
                   "rationale": (v.get("rationale") or "")[:200], "window": window}
            if flipped:
                row["coerced_from"] = raw_kind      # 审计：初判被压为非技术背景
            built.append((c, row))
        # 新颖性守门：task/skill 判定复核——covered → 改 alias；确无涵盖 → 维持新实体
        # （nearest/why_not 入缓存行留审计）；复核未决 → 不入缓存不应用，下窗重试
        need = [(c, r) for c, r in built if r["is_tech"] and r["kind"] in ("task", "skill")]
        recheck = _novelty_recheck(llm, need, base_labels) if need else {}
        new_cache, updates = [], []
        for c, row in built:
            rr = recheck.get(row["key"])
            if rr is not None and rr.get("covered"):
                row["kind"], row["alias_to"] = "alias", rr["alias_to"]
            elif rr is not None:
                row["nearest"] = (rr.get("nearest") or "")[:60]
                row["why_not"] = (rr.get("why_not") or "")[:200]
            elif row["is_tech"] and row["kind"] in ("task", "skill"):
                continue                    # 复核未决 → 留待下窗（不缓存、不应用）
            new_cache.append(row)
            if row["is_tech"]:
                action = "merge_evidence" if norm_key(row["canonical"]) in existing_names else "add"
                updates.append({"action": action, "task": {"name_zh": row["canonical"]},
                                "verdict": row, "cand": c})
        if new_cache:
            append_adjudication(new_cache)
            for r in new_cache:
                adj_cache[r["key"]] = r
        return {"covered": all(c["key"] in adj_cache for c in batch), "updates": updates}

    return propose_fn


def make_supervise_fn(base_labels):
    """契约校验：canonical 长度/非空、kind 合法、alias code 有效、证据存在。"""
    def supervise_fn(proposal, store):
        approved, rejected = [], []
        for u in proposal.get("updates", []):
            v, c = u.get("verdict") or {}, u.get("cand") or {}
            canonical = (v.get("canonical") or "").strip()
            ok = bool(canonical) and 2 <= len(canonical) <= 30 and bool(c.get("evidence"))
            if ok and v.get("kind") == "alias":
                a = v.get("alias_to") or {}
                ok = any(l["code"] == a.get("code") for l in base_labels.get(a.get("taxonomy") or "", []))
            elif ok and v.get("kind") not in _KIND_ARRAY:
                ok = False
            (approved if ok else rejected).append(
                u if ok else f"{c.get('name')}: 契约失败 kind={v.get('kind')}")
        return approved, rejected
    return supervise_fn


def make_apply_fn(delta, docs):
    """把批准的裁决写入 ΔG：只有 task/skill 落叠层（JD 侧体系级演化的唯一发现通道）。

    skillpoint 的发现权威在 B 阶段抽取 + 三层归一（叠层技能点无父技能关联、不转正，
    属惰性条目）；alias（被涵盖短语）的市场存在已由基图对基面无偏统计，scan 级提及
    增强会与基图频次重复计权——两者只入裁决缓存排水（防重裁），不写 ΔG。
    """
    def apply_fn(store, approved):
        log = []
        for u in approved:
            v, c = u["verdict"], u["cand"]
            canon = v["canonical"]
            if v["kind"] not in ("task", "skill"):
                log.append(f"drain:{v['kind']}:{canon}")
                continue
            n_written = 0
            for di, sent in c["evidence"][:EVIDENCE_CAP]:
                doc = docs[di]
                shim = types.SimpleNamespace(doc_id=doc["jobid"] or doc["jd_key"],
                                             pub_date=(doc["opentime"] or "")[:10])
                arr = _KIND_ARRAY[v["kind"]]
                entry, created = delta.confirm_named(
                    arr, canon, shim, [sent[:300]], "high",
                    name_en=v.get("name_en") or "",
                    definition=v.get("rationale") or "", grade="scan")
                if created:
                    entry["origin"] = "jd_v2_scan"
                n_written += 1
            log.append(f"{v['kind']}:{canon}({n_written} docs)")
        return log
    return apply_fn


_BASE_LABELS_CACHE = {}      # apply_fn 的 alias 名称解析缓存（模块级，build_known_vocab 后填充）


def adjudicate(window, docs, pool, llm, adj_cache, base_labels, delta,
               max_candidates=MAX_CANDIDATES, dry_run=False):
    """残差裁决主入口（HotUpdater 注入）。返回 (选中数, 统计)。"""
    selected = pool[:max_candidates]
    deferred = len(pool) - len(selected)
    if dry_run or not selected:
        return selected, {"selected": len(selected), "deferred": deferred}
    collect_evidence(docs, selected)
    selected = [c for c in selected if c.get("evidence")]     # 无证据句者下窗再试
    propose_fn = make_propose_fn(llm, adj_cache, base_labels, window, delta)
    supervise_fn = make_supervise_fn(base_labels)
    apply_fn = make_apply_fn(delta, docs)
    source = _CandidateSource(selected)
    ckpt = ADJ_CACHE_PATH.replace(".jsonl", f".{window}.checkpoint.json")
    updater = HotUpdater(taxonomy_store=_DeltaShim(delta), propose_fn=propose_fn,
                         supervise_fn=supervise_fn, apply_fn=apply_fn)
    logs = updater.run(source, max_rounds=1, batch_size=max(1, len(selected)),
                       max_recheck=3, checkpoint_path=ckpt, chunk_size=ADJ_BATCH)
    for ln in logs:
        print(f"  [adj] {ln}")
    return selected, {"selected": len(selected), "deferred": deferred}


def run(window, limit=None, dry_run=False, api_key=None,
        output=None, max_candidates=None):
    max_candidates = max_candidates or MAX_CANDIDATES
    known_norms, base_labels = build_known_vocab()
    _BASE_LABELS_CACHE.clear()
    _BASE_LABELS_CACHE.update(base_labels)
    print(f"[vocab] 已知词表 {len(known_norms)} 键（体系+注册表+L3 缓存+技术栈）")
    adj_cache = load_adjudication_cache()
    print(f"[cache] 裁决缓存 {len(adj_cache)} 条")

    docs = scan_window_docs(window, limit=limit)
    pool, stats = pool_candidates(docs, known_norms, adj_cache)
    print(f"[pool] {json.dumps(stats, ensure_ascii=False)}")
    # TOP 候选预览：补 1-2 句证据便于人工抽查（dry-run 也跑，满 2 句即止）
    preview = collect_evidence(docs, [dict(c, evidence=[]) for c in pool[:15]], cap=2) \
        if pool else []
    by_key = {c["key"]: c for c in preview}
    print(f"[pool] TOP 候选预览（df 降序，共 {len(pool)}，本窗裁决上限 {max_candidates}）：")
    for c in pool[:15]:
        ev = by_key.get(c["key"], {}).get("evidence") or []
        ex = ev[0][1][:60] + ("…" if ev and len(ev[0][1]) > 60 else "") if ev else ""
        print(f"  [{c['channel']}] {c['name']}（df={c['df']}）{ex}")

    if dry_run:
        print("[dry-run] 零 LLM 结束：以上为差集残差池（未含 LLM 裁决与 ΔG 写入）")
        return stats

    _, LLMClient = _extractor_imports()
    llm = LLMClient(api_key=api_key)
    out_path = output or builder_config.JD_DELTA_OUTPUT
    delta = DeltaStore(out_path, source_desc=f"JD v2 全量扫描（窗口 {window}）",
                       source_kind="jd", now=window_end_date(window))
    _, adj_stats = adjudicate(window, docs, pool, llm, adj_cache, base_labels, delta,
                              max_candidates=max_candidates)
    stats = delta.save()
    stats.update({"adjudication": adj_stats,
                  "confirm": {"note": "确证通道已迁移至 Stage B 叠层分类参与（run_jd_extract Pass 4），2026-08-30"}})
    print(f"\n[v2] ΔG 已更新：{out_path}")
    print(json.dumps(stats, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser(description="JD ΔG v2：全量扫描 + 残差裁决（逐窗发现/确证）")
    ap.add_argument("--window", required=True, help="时间窗口 YYYY-MM")
    ap.add_argument("--limit", type=int, default=None, help="仅扫描前 N 条唯一 IT JD（探索）")
    ap.add_argument("--max-candidates", type=int, default=None, help="本窗裁决候选上限")
    ap.add_argument("--dry-run", action="store_true", help="零 LLM：只扫描 + 池统计 + 预览")
    ap.add_argument("--api-key", default=None, help="覆盖 codes/api-key.txt")
    ap.add_argument("--output", default=None, help="ΔG 输出路径（默认 jd_delta.json）")
    args = ap.parse_args()
    run(args.window, limit=args.limit, dry_run=args.dry_run,
        api_key=args.api_key, output=args.output, max_candidates=args.max_candidates)


if __name__ == "__main__":
    main()
