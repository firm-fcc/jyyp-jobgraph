# -*- coding: utf-8 -*-
"""jd_annotate 公共层：路径常量、JD 文本指纹、技术栈体系加载与关键词匹配器。

技术栈/级别双维度标注的共享工具：
- `scan_jd_funtypes`：单遍扫描 data/jd_dataset/*.csv 统计 distinct funtype（带缓存，避免重复扫 6.3GB）
- `split_parts`：按 " or " 拆 funtype（与 gather_funtypes / funtype_it_map 同一拆分口径）
- `jd_text_key`：JD 文本指纹（标题+正文归一化 md5）——LLM 归类缓存与行级引擎查表的同口径主键
- `rule_stacks`：词库快路（标题/正文关键词命中即划分），生产端（classify_stacks）与
  行级引擎（annotate_jd）共用，保证两端规则判定一致
- `load_taxonomy`：加载 classify/TechStacks/techstacks.json
- `StackMatchers`：关键词匹配器——中文关键词按子串、ascii 关键词按字母数字边界
  （避免 "java" 命中 "javascript"、"go" 命中 "golang"），大小写不敏感
"""
import csv
import hashlib
import json
import os
import re
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "output")
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
JD_DIR = os.path.join(REPO, "data", "jd_dataset")
TAXONOMY_PATH = os.path.join(REPO, "classify", "TechStacks", "techstacks.json")
JD_STACK_CACHE = os.path.join(OUT_DIR, "jd_stack_cache.jsonl")
FUNTYPE_CACHE = os.path.join(OUT_DIR, "all_funtype_strings_cache.json")

# 必空排除表（v2.1，2026-08-20）：标题含以下关键词**且标题/正文词库均未命中**的 JD
# 直接判空栈、不送 LLM——被宽口径 IT 过滤收入数据集的非软件类岗位（工艺/机械/化工/
# 航空/技工/职能类）是未命中主体的主要成分，排除后 LLM 兜底量大减。
# 收录原则：**即使全文无软件关键词也几乎必空**的物理制造/职能域；
# 电子/半导体/电气/芯片等边界域（涉及嵌入式 C/PLC，八类下归属需权衡）不收，留给 LLM。
# 排除只作用于生产端（classify_stacks 跳过 LLM）；引擎端无缓存条目自然为空，两端一致。
EXCLUDE_TITLE_WORDS = [
    # 物理/制造域
    "工艺", "制程", "机械", "材料", "化工", "化学", "涂料", "电镀", "焊接", "模具",
    "铸造", "航空", "航天", "飞行器", "适航", "电力", "电机", "仪器仪表", "技工", "技师",
    "维修",
    # 职能域（词库未命中时无软件栈）
    "产品经理", "项目经理", "产品专员", "项目专员", "文员", "行政", "人事", "客服",
    "销售", "采购",
]


def is_excluded_title(title):
    """标题命中必空排除表 → True（调用方须先确认词库未命中，排除表不覆盖有栈 JD）。"""
    t = title or ""
    return any(w in t for w in EXCLUDE_TITLE_WORDS)


def scan_jd_funtypes(jd_dir=None, use_cache=True, refresh=False):
    """扫描 data/jd_dataset 全部 CSV → Counter(funtype字符串 → 行数)。

    结果缓存到 output/all_funtype_strings_cache.json（约 288 个 distinct 值），
    后续运行默认直接读缓存；refresh=True 强制重扫。
    """
    jd_dir = jd_dir or JD_DIR
    if use_cache and not refresh and os.path.exists(FUNTYPE_CACHE):
        with open(FUNTYPE_CACHE, encoding="utf-8") as f:
            data = json.load(f)
        return Counter(data["funtype_counts"]), data.get("n_files", 0)

    counts = Counter()
    n_files = 0
    for fn in sorted(os.listdir(jd_dir)):
        if not fn.endswith(".csv"):
            continue
        n_files += 1
        with open(os.path.join(jd_dir, fn), encoding="utf-8-sig", errors="replace", newline="") as fh:
            for row in csv.DictReader(fh):
                ft = (row.get("funtype") or "").strip()
                if ft:
                    counts[ft] += 1
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(FUNTYPE_CACHE, "w", encoding="utf-8") as f:
        json.dump({"n_files": n_files, "n_rows": sum(counts.values()),
                   "funtype_counts": dict(counts)}, f, ensure_ascii=False, indent=1)
    return counts, n_files


def split_parts(funtype):
    """funtype 字符串按 " or " 拆为独立部分（与既有 IT 映射同一口径）。"""
    return [p.strip() for p in re.split(r"\s+or\s+", funtype or "") if p.strip()]


def jd_text_key(title, text):
    """JD 文本指纹：标题 + 正文合并空白归一化后的 md5。

    同文重复 JD（重复发布/模板抄袭，全量约 28.6%）与跨表重复共享同一指纹，
    LLM 只判一次、引擎按指纹查缓存。两端（classify_stacks / annotate_jd）必须共用本函数。
    """
    norm = re.sub(r"\s+", " ", f"{title or ''}\n{text or ''}").strip()
    return hashlib.md5(norm.encode("utf-8")).hexdigest()


def rule_stacks(matchers, title, text, body_chars=4000, cap=4):
    """词库快路：标题/正文关键词命中即划分 → (stacks, tier)。

    tier：1=标题关键词命中（跳过正文扫描，省 CPU）；2=正文关键词命中；0=未命中（交 LLM 兜底）。
    返回按首现位置序的栈 code 列表（cap 4，多标签）。生产端与行级引擎共用，判定一致。
    """
    hits = [code for _, code in matchers.scan(title)]
    if hits:
        return hits[:cap], 1
    hits = [code for _, code in matchers.scan((text or "")[:body_chars])]
    if hits:
        return hits[:cap], 2
    return [], 0


def load_taxonomy(path=None):
    """加载技术栈体系 → detail dict（code → 节点）。"""
    with open(path or TAXONOMY_PATH, encoding="utf-8") as f:
        return json.load(f)["detail"]


def _kw_matcher(kw):
    """构建单个关键词的匹配函数：返回首现位置（未命中 -1）。

    ascii 词用字母数字边界（避免 "java" 命中 "javascript"、"go" 命中 "golang"），
    中文关键词按子串；均大小写不敏感。
    """
    if kw.isascii():
        pat = re.compile(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", re.IGNORECASE)

        def match_pos(text):
            m = pat.search(text)
            return m.start() if m else -1
    else:
        def match_pos(text):
            return text.find(kw)
    return match_pos


class StackMatchers:
    """体系关键词匹配器集合：scan(text) → [(首现位置, code), ...] 按出现顺序。"""

    def __init__(self, taxonomy_detail):
        self._entries = []  # (match_pos, code)
        for code, node in sorted(taxonomy_detail.items()):
            for kw in node.get("keywords") or []:
                self._entries.append((_kw_matcher(kw), code))

    def scan(self, text):
        """返回 [(first_pos, code)]，同一栈取最早命中位置，按位置排序。"""
        if not text:
            return []
        best = {}
        for match_pos, code in self._entries:
            pos = match_pos(text)
            if pos >= 0 and (code not in best or pos < best[code]):
                best[code] = pos
        return sorted((pos, code) for code, pos in best.items())
