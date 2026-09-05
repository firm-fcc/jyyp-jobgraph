# -*- coding: utf-8 -*-
"""JD 双维度标注引擎：techstack（多标签）+ level（单标签），纯规则零 LLM。

输入 data/jd_dataset/*.csv，输出在每行末尾追加 3 列：
  - techstack   技术栈 code 列表，"TS-01|TS-09" 形式；无栈为空
  - level       L0 实习/应届、L1 初级(0-2年)、L2 中级(3-4年)、L3 高级(5-9年)、L4 专家(10年+)；判不出为空
  - level_source  work_year | text | title | 空（判级依据，便于质检）

技术栈三层解析（v2.1 逐 JD 归类，词库快路 + LLM 兜底；funtype→固定栈查表已退役）：
  第1层 标题词库：体系关键词命中（common.rule_stacks tier 1，命中即划分、跳过正文扫描）
  第2层 正文词库：标题未命中时扫 job_information 前 4000 字关键词（tier 2）
  第3层 LLM 缓存：词库未命中的 JD 由 classify_stacks.py 送 LLM 归类（按文本指纹
        去重、断点续跑），本引擎按同口径指纹查 output/jd_stack_cache.jsonl（tier 3）
 多标签 cap 4；词库未命中且缓存缺载时为空（可先跑 classify_stacks.py 补缓存）。
 技术栈体系 v2.0：人工确定的八类，见 classify/TechStacks/techstacks.json。

级别规则（需求口径，优先级 work_year 列 > 正文年限 > 正文应届 > 标题级别词 > funtype 级别词）：
  - work_year：在校生/应届→L0；区间"N-M年"取下界；"N年及以上"取 N
  - 正文：阿拉伯+中文数字年限模式（"N年(以上)…经验"/"经验N年"），取**最高**年限要求（cap 15），
    "无经验/经验不限"→0
  - 标题（兜底）：实习/应届→L0；初级/助理→L1；中级→L2；高级/资深→L3；专家/首席/总监/架构师→L4；
    经理/主管属管理序列，不参与级别
  - years→级别：0-2→L1，3-4→L2，5-9→L3，≥10→L4

级别规则 lv2（2026-08-31，正文/标题无线索时救回率 +13pp，27 条抽样零误报）：
  - 正文年限追加：年限标签（"工作年限：3年"）、"经验"前置区间/中文数字（"经验：3-5年"）、
    "经历"措辞（"3年以上…经历"）、经验距离 10→20 字、英文年限（"3-year(s)"）、
    语境锚裸"年以上"（"相关领域工作5年以上"）
  - 正文"接受/欢迎应届"→L0（置于年限之后，年限优先）
  - 标题追加英文级别词：senior/junior/principal/staff/expert（全词边界）
  - funtype 级别词兜底（title 无级别词而平台类目有，如 title"版本管理工程师"+
    funtype"高级软件工程师"）；level_source=funtype 供前端区分可信度

用法：
  测试（写副本，绝不碰源文件）：
    python annotate_jd.py --files job_2026_1_1.csv --out-dir output/_test
    python annotate_jd.py --files job_2026_04_09.csv,job_2022_8_10.csv --limit 5000 --out-dir output/_test
  报告（只扫描统计，不写文件）：
    python annotate_jd.py --files job_2026_04_09.csv --report
  全量原地加列（将来运行；逐文件 temp→校验→原子替换，断点续跑）：
    python annotate_jd.py --in-place
"""
import argparse
import csv
import json
import os
import re
import sys
from collections import Counter

import common

NEW_COLS = ["techstack", "level", "level_source"]
# 级别规则版本：lv1=2026-08 初版（正文年限距离10 + 标题中文词表）；lv2=2026-08-31 扩展
# （见模块 docstring）。改规则必须 bump，并同步 run_jd_extract 写入 vectors meta。
LEVEL_RULES_VERSION = "lv2"
LEVEL_NAMES = {"L0": "实习/应届", "L1": "初级(0-2年)", "L2": "中级(3-4年)",
               "L3": "高级(5-9年)", "L4": "专家(10年+)"}

# ---------------------------------------------------------------- 级别规则
CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_to_int(s):
    """中文数字（一~九十九）→ int；不合法返回 None。"""
    if not s:
        return None
    if len(s) == 1:
        return CN_NUM.get(s)
    if s[0] == "十":
        return 10 + CN_NUM.get(s[1], 0) if len(s) == 2 else None
    if "十" in s:
        i = s.index("十")
        head = CN_NUM.get(s[:i])
        tail = CN_NUM.get(s[i + 1:], 0) if len(s) > i + 1 else 0
        return head * 10 + tail if head is not None else None
    return None


_TEXT_YEAR_PATTERNS = [
    # "3-5年(以上)?…经验"（取下界）；"N年(以上)?…经验"（中间容忍 ≤10 个非断句字符）
    re.compile(r"(\d{1,2})\s*[-~至]\s*\d{1,2}\s*年(?:以上|及以上)?[^，。；;\n]{0,10}经验"),
    re.compile(r"(\d{1,2})\s*年(?:以上|及以上)?[^，。；;\n]{0,10}经验"),
    re.compile(r"经验[：:]?\s*(?:需|需要)?\s*(\d{1,2})\s*年"),
]
_TEXT_YEAR_CN = re.compile(r"([一二两三四五六七八九十]{1,3})\s*年(?:以上|及以上)?[^，。；;\n]{0,10}经验")
_NO_EXP_RE = re.compile(r"无(?:需|需相关|相关|工作)?经验|经验不限|不限经验")

# ---- lv2 追加模式（命名组三选一：lo=区间下界 / n=单值 / cn=中文数字；仍取最高年限，cap 15）----
_CN_NUM_CLASS = r"[一二两三四五六七八九十]{1,3}"
_YEAR_VAL = rf"(?:(?P<lo>\d{{1,2}})\s*[-~至]\s*\d{{1,2}}|(?P<n>\d{{1,2}})|(?P<cn>{_CN_NUM_CLASS}))"
_TEXT_YEAR_PATTERNS_V2 = [
    # 年限标签措辞："工作年限：3年" "年限要求：三年" "经验要求：2-3年"
    re.compile(rf"(?:工作年限|年限要求|经验要求|工作经历)[：:.\s]*{_YEAR_VAL}\s*年"),
    # "经验"前置的区间/中文数字："经验：3-5年" "工作经验 三 年"
    re.compile(rf"经验[：:]?\s*{_YEAR_VAL}\s*年"),
    # "经历"措辞："3年以上工作经历" "1年以上化妆品行业从业经历"
    re.compile(rf"{_YEAR_VAL}\s*年(?:以上|及以上)?[^，。；;\n]{{0,12}}经历"),
    # 经验距离 10→20 字："3年以上机电安装、工程技术、施工管理经验"（顿号不禁断）
    re.compile(rf"(?P<lo>\d{{1,2}})\s*[-~至]\s*\d{{1,2}}\s*年(?:以上|及以上)?[^，。；;\n]{{0,20}}经验"),
    re.compile(rf"(?P<n>\d{{1,2}})\s*年(?:以上|及以上)?[^，。；;\n]{{0,20}}经验"),
    re.compile(rf"(?P<cn>{_CN_NUM_CLASS})\s*年(?:以上|及以上)?[^，。；;\n]{{0,20}}经验"),
    # 英文年限："3 years" "3+ years" "8-year" "3 to 5 years"（n 取左值即下界）
    re.compile(r"(?P<n>\d{1,2})\s*\+?\s*(?:[-~to]+\s*\d{0,2}\s*)?years?", re.IGNORECASE),
    # 语境锚裸"年以上"（无"经验"字样，防"公司成立3年以上"类误报需前后语境词）
    re.compile(rf"(?:工作|从事|相关|行业|本岗|岗位|开发|运维|测试|研发)\D{{0,6}}(?P<n>\d{{1,2}})\s*年(?:以上|及以上)"),
    re.compile(r"(?P<n>\d{1,2})\s*年(?:以上|及以上)(?:的)?(?:工作|相关|项目|行业|从业|开发|运维|测试|管理|研究)"),
]
# 正文"接受/欢迎应届"→L0（置于年限之后：正文既给年限又欢迎应届的以年限为准）
_FRESH_OK_RE = re.compile(r"接受应届|欢迎应届|应届生亦可|应届毕业(?:生)?[亦可]|亦招应届|可接收应届")

_TITLE_L0_RE = re.compile(r"实习|应届")
_TITLE_LEVEL_WORDS = [("初级", "L1"), ("助理", "L1"), ("中级", "L2"), ("高级", "L3"),
                      ("资深", "L3"), ("专家", "L4"), ("首席", "L4"), ("总监", "L4"),
                      ("架构师", "L4")]
# 经理/主管 = 管理序列，不映射级别
# lv2 英文级别词（全词边界，"seniority"不误命中 senior）
_EN_TITLE_LEVEL_WORDS = [("principal", "L4"), ("staff", "L4"), ("senior", "L3"),
                         ("junior", "L1"), ("expert", "L4")]


def parse_work_year(work_year):
    """结构化 work_year 列 → (level 或 None, years 或 None)。在校生/应届直接 L0。"""
    wy = (work_year or "").strip()
    if not wy:
        return None, None
    if "在校生" in wy or "应届" in wy:
        return "L0", 0
    if "无需经验" in wy or "无经验" in wy or "经验不限" in wy or "不限" in wy:
        return None, 0
    m = re.search(r"(\d{1,2})\s*[-~至]\s*\d{1,2}\s*年", wy)
    if m:
        return None, int(m.group(1))
    m = re.search(r"(\d{1,2})\s*年", wy)
    if m:
        return None, int(m.group(1))
    return None, None


def parse_text_years(text):
    """正文年限要求 → int（最高年限要求，cap 15）；"无经验"→0；无线索→None。

    lv2：官方三模式+中文数字（距离 10）之后追加 V2 模式（年限标签/经验前置/
    经历措辞/距离 20/英文年限/语境锚裸年以上），仍取所有线索的最高值。
    """
    if not text:
        return None
    text = text[:6000]
    years = []
    for pat in _TEXT_YEAR_PATTERNS:
        years.extend(int(m.group(1)) for m in pat.finditer(text))
    for m in _TEXT_YEAR_CN.finditer(text):
        v = _cn_to_int(m.group(1))
        if v:
            years.append(v)
    for pat in _TEXT_YEAR_PATTERNS_V2:
        for m in pat.finditer(text):
            g = m.groupdict()
            if g.get("lo") is not None:
                years.append(int(g["lo"]))
            elif g.get("n") is not None:
                years.append(int(g["n"]))
            elif g.get("cn") is not None:
                v = _cn_to_int(g["cn"])
                if v:
                    years.append(v)
    if years:
        return min(max(years), 15)
    return 0 if _NO_EXP_RE.search(text) else None


def years_to_level(n):
    if n <= 2:
        return "L1"
    if n <= 4:
        return "L2"
    if n <= 9:
        return "L3"
    return "L4"


def title_level(title):
    """标题/类目级别词 → level（取命中词的最高档；实习/应届优先 L0）。

    lv2：追加英文级别词（senior/junior/principal/staff/expert，全词边界）。
    funtype 兜底也走本函数（平台类目串同词表）。
    """
    if not title:
        return None
    if _TITLE_L0_RE.search(title):
        return "L0"
    lv = None
    for w, l in _TITLE_LEVEL_WORDS:
        if w in title and (lv is None or l > lv):
            lv = l
    low = title.lower()
    for w, l in _EN_TITLE_LEVEL_WORDS:
        if re.search(rf"\b{w}\b", low) and (lv is None or l > lv):
            lv = l
    return lv


def resolve_level(work_year, title, text, funtype=""):
    """→ (level, source)。优先级 work_year 列 > 正文年限 > 正文应届 > 标题词 >
    funtype 级别词；判不出 ("","")。funtype 兜底 level_source="funtype"
    （平台类目词，可信度低于标题，前端可按 source 区分展示）。"""
    lv, yrs = parse_work_year(work_year)
    if lv:
        return lv, "work_year"
    if yrs is not None:
        return years_to_level(yrs), "work_year"
    ty = parse_text_years(text)
    if ty is not None:
        return years_to_level(ty), "text"
    if text and _FRESH_OK_RE.search(text):
        return "L0", "text"
    tl = title_level(title)
    if tl:
        return tl, "title"
    fl = title_level(funtype)
    if fl:
        return fl, "funtype"
    return "", ""


# ---------------------------------------------------------------- 技术名词确定性抽取
# （2026-09-03，JD 解析出口专用）：registry/L3 名录 + 技术栈关键词在标题/正文的词面
# 命中（英文词边界、中文子串），零 LLM。用途：JD 解析的结构化"技能点"补充通道——
# 句级抽取按"要求掌握"语义偏保守，词面明确出现的技术名词是解析要素不应漏
# （2026-05 评测实证：句级层技能点召回 0.37，缺漏 18/20 为名录内名词）。
# 口径边界：本层只进解析出口（eval_jd_parse 等解析消费方），图谱基面消费不变
# （图谱技能点仍由 B 句级抽取产生，频次口径不引入词面匹配）。
_TECH_POOL = None          # {名: canonical}（registry curated+别名 + L3 缓存 + 技术栈关键词）
_TECH_POOL_LOCK = __import__("threading").Lock()


# 泛概念停用词（2026-09-03 评测校准）：L3 缓存沉淀的非技术名词（动词/泛概念/学科名），
# 词面匹配时到处命中造成技能点误报（TOP FP 实测：优化/测试/应用/软件工程/集成…）。
# 只拦 L3/关键词来源；registry curated 免检。具体技术变体不受影响（"模型部署"保留、
# 单独"部署"拦下；"图像处理"不在表内故保留）。
_TECH_STOPWORDS = {
    "优化", "测试", "应用", "软件工程", "集成", "调试", "算法", "模型", "通信",
    "跟踪", "部署", "监控", "识别", "安全", "视觉", "网络", "迭代开发", "架构设计",
    "性能优化", "容器化", "数据库", "API", "Web", "Ai", "软件开发", "数据分析",
    "项目管理", "需求分析", "系统设计", "方案设计", "数据处理", "模型训练",
}


def _load_tech_pool():
    """构建 名→canonical 名录（进程内一次）。

    权威序：registry（curated+别名）> L3 缓存 > 技术栈关键词。canonical 按
    casefold 统一到 registry 正形（L3 存在 7 组大小写分裂）；关键词只收
    ASCII 或 registry 内名词——中文关键词多为角色/概念词（软件工程师/后端/
    数据开发），不是技能点。
    """
    global _TECH_POOL
    with _TECH_POOL_LOCK:
        if _TECH_POOL is not None:
            return _TECH_POOL
        pool = {}
        here = os.path.dirname(os.path.abspath(__file__))
        reg = json.load(open(os.path.join(here, "..", "graph", "skillpoint_registry.json"),
                             encoding="utf-8"))
        retired = reg.get("retired", {})
        canon_form = {}                        # casefold → registry 正形
        for canon, d in reg.get("curated", {}).items():
            canon = retired.get(canon, canon)
            pool[canon] = canon
            canon_form.setdefault(canon.casefold(), canon)
            for a in (d.get("aliases") or []):
                pool.setdefault(a, canon)
                canon_form.setdefault(a.casefold(), canon)
        l3 = os.path.join(here, "..", "graph", "output", "skillpoint_alias_cache.jsonl")
        if os.path.exists(l3):
            with open(l3, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get("name") and r.get("canonical"):
                        c2 = canon_form.get(r["canonical"].casefold(), r["canonical"])
                        if c2 not in _TECH_STOPWORDS:
                            pool.setdefault(r["name"], c2)
        tsd = json.load(open(os.path.join(here, "..", "..", "classify", "TechStacks",
                                          "techstacks.json"), encoding="utf-8"))["detail"]
        for v in tsd.values():
            for kw in (v.get("keywords") or []):
                if len(kw) >= 2 and (kw.isascii() or kw in pool):
                    if kw not in _TECH_STOPWORDS:
                        pool.setdefault(kw, canon_form.get(kw.casefold(), kw))
        _TECH_POOL = {k: v for k, v in pool.items()
                      if len(k) >= 2 or (k.isascii() and not k.isalpha())}
        return _TECH_POOL


def extract_tech_mentions(title, text):
    """确定性技术名词抽取 → canonical 名集合（零 LLM）。

    英文名词用词边界（避免 Go 命中 Google、C 命中 C++/C#/element-ui 内部——
    短名边界附加 +#. 连字符；长名边界含连字符防 react ⊂ react-native）；
    中文名词子串匹配。位置抑制：被更长命中完全覆盖的短命中丢弃
    （Spring ⊂ Spring Boot、微信 ⊂ 微信小程序——同位置取最长，硬口径
    "不同代际/组件保持独立"的词面侧防线）。
    """
    pool = _load_tech_pool()
    hay = f"{title or ''}\n{text or ''}"
    hay_l = hay.casefold()
    hits = []
    for name, canon in pool.items():
        if name.isascii():
            n = name.casefold()
            esc = re.escape(n)
            # 短名边界附加 +.#（防 C ⊂ C++/C#）；长名尾边界放行数字（版本号并入母项：
            # MySQL8/Windows10/Vue3 应命中 MySQL/Windows/Vue），首边界仍含数字
            if len(n) <= 2:
                pat = re.compile(rf"(?<![A-Za-z0-9_+.#-]){esc}(?![A-Za-z0-9_+.#-])")
            else:
                pat = re.compile(rf"(?<![A-Za-z0-9_-]){esc}(?![A-Za-z_-])")
            for m in pat.finditer(hay_l):
                hits.append((m.start(), m.end(), canon))
        else:
            start = hay.find(name)
            while start != -1:
                hits.append((start, start + len(name), canon))
                start = hay.find(name, start + 1)
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    out, occupied = set(), []
    for s, e, c in hits:
        if any(s >= os_ and e <= oe for os_, oe in occupied):
            continue
        occupied.append((s, e))
        out.add(c)
    return out


# ---------------------------------------------------------------- 技术栈三层解析
class StackAnnotator:
    def __init__(self):
        self.matchers = common.StackMatchers(common.load_taxonomy())
        self.cache = {}  # 文本指纹 → LLM 判定（词库未命中的 JD；容缺：文件不存在则空表）
        if os.path.exists(common.JD_STACK_CACHE):
            with open(common.JD_STACK_CACHE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        ent = json.loads(line)
                        self.cache[ent["key"]] = ent

    def resolve(self, title, text):
        """→ (stacks 列表, tier)。tier：1=标题词库、2=正文词库、3=LLM 缓存、0=无。"""
        stacks, tier = common.rule_stacks(self.matchers, title, text)
        if stacks:
            return stacks, tier
        ent = self.cache.get(common.jd_text_key(title, text))
        if ent:
            return ent.get("stacks", [])[:4], 3
        return [], 0


# ---------------------------------------------------------------- 文件处理
def iter_annotated(rows, annotator):
    """逐行补 3 列，产出 (新行 dict, tier, level, source, wy_level, text_level) 供统计。"""
    for row in rows:
        title = row.get("job") or ""
        text = row.get("job_information") or ""
        stacks, tier = annotator.resolve(title, text)
        level, source = resolve_level(row.get("work_year") or "", title, text,
                                      row.get("funtype") or "")
        new = dict(row)
        new["techstack"] = "|".join(stacks)
        new["level"] = level
        new["level_source"] = source
        # 一致性验证素材：结构化列与正文各自独立判级
        wy_lv, wy_yrs = parse_work_year(row.get("work_year") or "")
        if wy_lv is None and wy_yrs is not None:
            wy_lv = years_to_level(wy_yrs)
        ty = parse_text_years(text)
        text_lv = years_to_level(ty) if ty is not None else None
        yield new, tier, level, source, wy_lv, text_lv


class Stats:
    def __init__(self):
        self.n = 0
        self.tier = Counter()          # 1/2/3/0
        self.stack_rows = Counter()    # code → 行数（多标签）
        self.level = Counter()
        self.source = Counter()
        self.wy_text_pairs = Counter() # (wy_level, text_level) 一致性
        self.samples = {"tier1": [], "tier3": [], "level": []}

    def update(self, new, tier, level, source, wy_lv, text_lv):
        self.n += 1
        self.tier[tier] += 1
        for c in (new["techstack"].split("|") if new["techstack"] else []):
            self.stack_rows[c] += 1
        self.level[level] += 1
        self.source[source] += 1
        if wy_lv and text_lv:
            self.wy_text_pairs[(wy_lv, text_lv)] += 1
        if tier == 1 and len(self.samples["tier1"]) < 5:
            self.samples["tier1"].append((new.get("funtype"), new.get("job"), new["techstack"]))
        if tier == 3 and len(self.samples["tier3"]) < 5:
            self.samples["tier3"].append((new.get("job"), (new.get("job_information") or "")[:60], new["techstack"]))
        if source == "text" and len(self.samples["level"]) < 5:
            self.samples["level"].append((new["level"], (new.get("job_information") or "")[:60]))


def read_rows(path, limit=None):
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader):
            if limit and i >= limit:
                break
            yield row


def write_csv(path, fieldnames, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: (r.get(c) or "") for c in fieldnames})


def report_md(stats, title, extra=""):
    lines = [f"# JD 双维度标注报告：{title}", "", f"- 扫描行数：{stats.n}", ""]
    lines.append("## 技术栈覆盖（分层）")
    t = stats.tier
    tot = max(stats.n, 1)
    for k, name in [(1, "第1层 标题词库"), (2, "第2层 正文词库"), (3, "第3层 LLM 缓存"), (0, "未识别")]:
        lines.append(f"- {name}: {t.get(k, 0)}（{t.get(k, 0) / tot * 100:.1f}%）")
    lines.append("")
    lines.append("## 技术栈行数分布（多标签）")
    taxonomy = common.load_taxonomy()
    for c, n in stats.stack_rows.most_common():
        nm = taxonomy.get(c, {}).get("name_zh", c)
        lines.append(f"- {c} {nm}: {n}（{n / tot * 100:.1f}%）")
    lines.append("")
    lines.append("## 级别分布")
    for lv in ["L0", "L1", "L2", "L3", "L4", ""]:
        name = LEVEL_NAMES.get(lv, "未定级")
        lines.append(f"- {lv or '∅'} {name}: {stats.level.get(lv, 0)}（{stats.level.get(lv, 0) / tot * 100:.1f}%）")
    for s in ["work_year", "text", "title", ""]:
        lines.append(f"  - 判级依据 {s or '空'}: {stats.source.get(s, 0)}")
    lines.append("")
    if stats.wy_text_pairs:
        total_pair = sum(stats.wy_text_pairs.values())
        agree = sum(n for (a, b), n in stats.wy_text_pairs.items() if a == b)
        off1 = sum(n for (a, b), n in stats.wy_text_pairs.items()
                   if a != b and abs(int(a[1]) - int(b[1])) == 1)
        lines.append("## 一致性验证（有 work_year 且正文可判级的行：正文判级 vs 结构化列判级）")
        lines.append(f"- 样本 {total_pair}：完全一致 {agree}（{agree / total_pair * 100:.1f}%），"
                     f"差一档 {off1}（{off1 / total_pair * 100:.1f}%），"
                     f"差两档以上 {total_pair - agree - off1}（{(total_pair - agree - off1) / total_pair * 100:.1f}%）")
        lines.append(f"- 混淆对 top：{stats.wy_text_pairs.most_common(8)}")
    if extra:
        lines.append(extra)
    lines.append("")
    lines.append("## 抽样")
    for k, cap in [("tier1", "第1层样例 (funtype, 标题, 栈)"), ("tier3", "第3层样例 (标题, 正文片段, 栈)"),
                   ("level", "正文判级样例 (级别, 正文片段)")]:
        if stats.samples[k]:
            lines.append(f"### {cap}")
            for s in stats.samples[k]:
                lines.append(f"- {s}")
    return "\n".join(lines)


def run_files(args):
    annotator = StackAnnotator()
    jd_dir = common.JD_DIR
    files = args.files.split(",") if args.files else []
    stats = Stats()
    for fn in files:
        path = os.path.join(jd_dir, fn.strip())
        if not os.path.exists(path):
            print(f"[skip] 不存在: {path}")
            continue
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
            fieldnames = list(csv.DictReader(fh).fieldnames or [])
        out_cols = fieldnames + [c for c in NEW_COLS if c not in fieldnames]
        gen = iter_annotated(read_rows(path, args.limit), annotator)
        if args.out_dir:
            def rows_with_stats():
                for new, tier, level, source, wy, tl in gen:
                    stats.update(new, tier, level, source, wy, tl)
                    yield new
            write_csv(os.path.join(args.out_dir, fn.strip()), out_cols, rows_with_stats())
        else:
            for new, tier, level, source, wy, tl in gen:
                stats.update(new, tier, level, source, wy, tl)
        print(f"[done] {fn}: 累计 {stats.n} 行")
    return stats


def in_place(args):
    """全量原地加列（temp → 行数/jobid 校验 → 原子替换），供后续全量运行。"""
    progress_path = os.path.join(common.OUT_DIR, "annotate_progress.json")
    progress = {}
    if os.path.exists(progress_path):
        progress = json.load(open(progress_path, encoding="utf-8"))
    annotator = StackAnnotator()
    files = ([f.strip() for f in args.files.split(",")] if args.files
             else sorted(f for f in os.listdir(common.JD_DIR) if f.endswith(".csv")))
    stats = Stats()
    for fn in files:
        if progress.get(fn) == "done":
            print(f"[skip] {fn} 已完成")
            continue
        path = os.path.join(common.JD_DIR, fn)
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
            fieldnames = list(csv.DictReader(fh).fieldnames or [])
        if all(c in fieldnames for c in NEW_COLS):
            progress[fn] = "done"
            print(f"[skip] {fn} 已含新列")
            continue
        out_cols = fieldnames + NEW_COLS
        tmp = path + ".tmp"
        orig_jobids = Counter()
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
            for row in csv.DictReader(fh):
                orig_jobids[row.get("jobid")] += 1
        gen = iter_annotated(read_rows(path), annotator)

        def rows_with_stats():
            for new, tier, level, source, wy, tl in gen:
                stats.update(new, tier, level, source, wy, tl)
                yield new
        write_csv(tmp, out_cols, rows_with_stats())
        # 校验：行数 + jobid 多重集一致
        with open(tmp, encoding="utf-8-sig", newline="") as fh:
            new_jobids = Counter(row.get("jobid") for row in csv.DictReader(fh))
        if new_jobids != orig_jobids:
            print(f"[abort] {fn}: jobid 校验不一致（原 {sum(orig_jobids.values())} vs 新 {sum(new_jobids.values())}），保留 tmp 待查")
            continue
        os.replace(tmp, path)
        progress[fn] = "done"
        with open(progress_path, "w", encoding="utf-8") as f:
            json.dump(progress, f, ensure_ascii=False, indent=1)
        print(f"[ok] {fn} 原地加列完成（累计 {stats.n} 行）")
    # summary.json meta 记录
    sp = os.path.join(common.JD_DIR, "summary.json")
    if os.path.exists(sp) and stats.n:
        summary = json.load(open(sp, encoding="utf-8"))
        meta = summary.setdefault("meta", {})
        meta["jd_dims_annotation"] = {
            "date": "2026-08-18",
            "columns": NEW_COLS,
            "n_rows": stats.n,
            "stack_tier_dist": {str(k): v for k, v in stats.tier.items()},
            "level_dist": dict(stats.level),
            "note": "codes/jd_annotate/annotate_jd.py --in-place；技术栈体系 classify/TechStacks/techstacks.json",
        }
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print("summary.json meta.jd_dims_annotation 已更新")


def main():
    ap = argparse.ArgumentParser(description="JD 技术栈/级别双维度标注")
    ap.add_argument("--files", default="", help="逗号分隔的文件名（相对 data/jd_dataset），默认全部")
    ap.add_argument("--limit", type=int, default=None, help="每文件最多处理行数（测试用）")
    ap.add_argument("--out-dir", default="", help="测试模式：注写副本到该目录（不改源文件）")
    ap.add_argument("--report", action="store_true", help="只扫描统计并写报告，不输出任何 CSV")
    ap.add_argument("--in-place", action="store_true", help="全量原地加列（temp+校验+原子替换）")
    args = ap.parse_args()

    if args.in_place:
        in_place(args)
        return
    if not args.files:
        print("测试/报告模式需 --files 指定文件；全量请用 --in-place", file=sys.stderr)
        sys.exit(1)
    stats = run_files(args)
    md = report_md(stats, f"{args.files}（limit={args.limit}）")
    os.makedirs(common.OUT_DIR, exist_ok=True)
    out = os.path.join(common.OUT_DIR, "jd_dims_report.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    print(md)
    print(f"\n报告已写入 {out}")


if __name__ == "__main__":
    main()
