# -*- coding: utf-8 -*-
"""论文精髓信息提取机制：TXT 头块 + 正文 → PaperRecord（Step ② 核心交付物）。

数据格式（data/papers/ 下按 `S档_核心/` 等分档目录，每篇 .txt）：
  ═══════════════════════════════════════════════════
  【arXiv ID】  2504.18651
  【标题】      ...
  【发表日期】  2025-04-25
  【赛题分档】  S 档（得分: 25, 覆盖 2 个维度）
  【命中维度】  B_技能图谱与知识图谱、C_数据质量与幻觉防控
  【PDF直链】   ...
  【网页版】    ...
  【证据句】
    [B_技能图谱与知识图谱] ...原文证据句...
  【说明】      ...
  ═══════════════════════════════════════════════════
  （以下为论文全文正文）
  Title / Authors / Abstract ... / Keywords: ... / 1. Introduction ...

Abstract 提取采用双启发式（实测存在两种格式）：
  1) 显式标记：正文前 BODY_SCAN_LINES 行内找 `Abstract`/`ABSTRACT`/`摘要`（含 `Abstract:`/`Abstract—` 内联）
  2) 无标记：找 arXiv 标识行，取其前最近的 ≥ABSTRACT_MIN_CHARS 段落；再无则跳过标题/作者/机构取首个长段
Keywords 缺失合法（返回空列表，提示词转向证据句与正文片段）。

解析器纯 stdlib（re），永不因缺字段失败；乱码字节用 errors="replace" 容忍。
"""
import hashlib
import os
import re
from dataclasses import asdict, dataclass, field

import paper_config as config

SEP_RE = re.compile(r"^[═\-=]{3,}\s*$")
FIELD_RE = re.compile(r"^【([^】]+)】\s*(.*)$")
EVIDENCE_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(.*)$")
ARXIV_ID_RE = re.compile(r"^(\d{4})\.(\d{4,5})$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ABSTRACT_RE = re.compile(r"^\s*(?:Abstract|ABSTRACT|摘要)\s*[-–—:：]?\s*(.*)$")
KEYWORDS_RE = re.compile(r"^\s*(?:Keywords?|关键词|KEYWORDS|INDEX TERMS)\s*[:：]?\s*(.*)$")
TIER_DIR_RE = re.compile(r"^([SABC])档_")


@dataclass
class PaperRecord:
    """单篇论文的精髓信息（标题/关键词/摘要/证据句/上下文片段）。"""
    arxiv_id: str = ""
    is_arxiv: bool = False
    title: str = ""
    pub_date: str = ""          # YYYY-MM-DD；缺省从 arXiv YYMM 推导
    tier: str = ""              # S / A / B / C / ""
    score: int = 0              # 赛题相关度得分
    dimensions: list = field(default_factory=list)      # 命中维度，如 ["B_技能图谱与知识图谱"]
    evidence_sentences: list = field(default_factory=list)  # [{"dimension", "text"}]
    abstract: str = ""          # 可为 ""（合法）
    keywords: list = field(default_factory=list)        # 可为 []
    body_excerpt: str = ""      # 正文截断片段（喂 LLM 上下文）
    file_md5: str = ""          # md5(空白折叠全文)，断点去重键
    source_file: str = ""       # 相对论文目录路径，如 "S档_核心/2504.18651.txt"

    def to_dict(self):
        return asdict(self)


# ---------------- 头/正文切分 ----------------
def _split_header_body(lines):
    """返回 (header_lines, body_lines)。优先用正文标记行；无标记则回退到分隔线/字段启发式。"""
    for i, ln in enumerate(lines):
        if config.HEADER_MARKER in ln:
            return lines[:i], lines[i + 1:]
    # 无标记：以分隔线为界（头在两分隔线之间）
    seps = [i for i, ln in enumerate(lines) if SEP_RE.match(ln)]
    if len(seps) >= 2:
        return lines[seps[0] + 1:seps[1]], lines[seps[1] + 1:]
    if seps:
        return lines[:seps[-1]], lines[seps[-1] + 1:]
    # 彻底无结构：头部 = 开头的【字段】/证据行，正文 = 其余
    hdr, idx = [], 0
    for i, ln in enumerate(lines[:40]):
        if FIELD_RE.match(ln) or EVIDENCE_RE.match(ln):
            hdr.append(ln)
            idx = i + 1
    return hdr, lines[idx:]


def _parse_header(header_lines):
    """解析头块 → {字段名: 值}；证据句累积为 [{"dimension", "text"}]。"""
    fields = {}
    current = None
    for ln in header_lines:
        if SEP_RE.match(ln):
            continue
        m = FIELD_RE.match(ln)
        if m:
            current = m.group(1)
            fields[current] = m.group(2).strip()
            continue
        m = EVIDENCE_RE.match(ln)
        if m and current == "证据句":
            fields.setdefault("evidence", []).append(
                {"dimension": m.group(1).strip(), "text": m.group(2).strip()})
    return fields


def _fill_from_header(rec, fields, tier):
    rec.arxiv_id = (fields.get("arXiv ID") or "").strip()
    rec.is_arxiv = bool(ARXIV_ID_RE.match(rec.arxiv_id))
    rec.title = (fields.get("标题") or "").strip()
    pub = (fields.get("发表日期") or "").strip()
    if DATE_RE.match(pub):
        rec.pub_date = pub
    elif rec.is_arxiv:
        m = ARXIV_ID_RE.match(rec.arxiv_id)
        rec.pub_date = f"20{m.group(1)}-{m.group(2)[:2]}-01"
    # 分档：优先调用方传入的目录档（更可靠），否则从头块提取
    rec.tier = tier or ""
    if not rec.tier:
        m = re.search(r"([SABC])\s*档", fields.get("赛题分档") or "")
        rec.tier = m.group(1) if m else ""
    m = re.search(r"得分\s*[:：]?\s*(\d+)", fields.get("赛题分档") or "")
    rec.score = int(m.group(1)) if m else 0
    rec.dimensions = [d.strip() for d in (fields.get("命中维度") or "").split("、") if d.strip()]
    rec.evidence_sentences = fields.get("evidence", [])


# ---------------- 正文解析（摘要/关键词） ----------------
def _is_section_boundary(line):
    """判断一行是否标志摘要结束（Keywords/引言/arXiv 行/小节标题等）。"""
    s = line.strip()
    if not s:
        return False
    if re.match(r"^(Keywords?|关键词|INDEX TERMS)\b", s, re.I):
        return True
    if re.match(r"^(arXiv:|∗?\s*c(?:orresponding )?author|preprint|doi:)", s, re.I):
        return True
    if re.match(r"^\s*(\d+|[IVX]+)\.?\s+[A-Z]", s):      # "1. Introduction" / "I. Intro"
        return True
    if re.match(r"^\s*(Introduction|引言)\b", s):
        return True
    return False


def _truncate(text, max_chars):
    """空白折叠 + 还原行尾连字符断行（如 "pow- erful"→"powerful"）+ 去前导标点，再截断。"""
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"(\w)-\s+(\w)", r"\1\2", text)   # 行尾断词连字符还原
    text = re.sub(r"^[\s.\-–—:：]+", "", text)       # 去摘要前导杂散标点（如 "Abstract." 残留的 "."）
    return text[:max_chars]


def _is_front_matter(s):
    """判断一行是否属于标题/作者/机构等前导信息（用于无标记摘要提取的跳过/截断）。"""
    if len(s) > 120:                      # 长句不太可能是作者/机构行
        return False
    if re.search(r"@|E-?mail", s):
        return True
    if re.search(r"(To whom correspondence|Corresponding author|corresponding author)", s):
        return True
    if re.match(r"^(arXiv:|Preprint)", s, re.I):
        return True
    if re.search(r"(Department|University|Universit|Faculty|Institute|Laboratory|School|College|Center|Centre|Corporation)", s):
        # 不以句号结尾才算前导信息（避免误伤正文中含机构名的不完整句）
        return not s.endswith(".")
    if len(s) >= 100:
        return False
    # 作者行特征：脚注星号 / 全大写姓名+空格大写 / 长全大写名+逗号 / 上标序号（如 1,2,3 或 Leon1）
    if "∗" in s:
        return True
    if re.match(r"^[A-Z]{3,}\s+[A-Z]", s):
        return True
    if re.match(r"^[A-Z]{4,},", s):
        return True
    if re.search(r"\d(?:,\d+)+", s) and not s.endswith("."):
        return True
    if re.search(r"\b[A-Za-z]{3,}\d+,", s) and not s.endswith("."):
        return True
    return False


def _scan_backward_abstract(lines):
    """策略1：从 arXiv 标识行向前扫描收集正文句段（跳过前导信息）作为摘要。

    适用于 arXiv 行出现在摘要之后的格式（如 2308.02624）。
    """
    chunks = []
    for ln in reversed(lines):
        s = ln.strip()
        if not s:
            if chunks:
                break
            continue
        if _is_front_matter(s) or _is_section_boundary(s):
            break
        chunks.append(s)
    chunks.reverse()
    text = " ".join(chunks).strip()
    return _truncate(text, config.ABSTRACT_MAX_CHARS) if len(text) >= config.ABSTRACT_MIN_CHARS else ""


def _scan_forward_abstract(body_lines, known_title):
    """策略2：向前跳过标题/作者/机构/arXiv 行，取首个长句段作为摘要。

    适用于 arXiv 行在正文顶部、摘要无标记的格式（如 2309.13933）。
    """
    title_keys = set()
    if known_title:
        for t in known_title.split():
            w = t.strip(".,:;()").lower()
            if len(w) >= 4:
                title_keys.add(w)
    para = []
    started = False
    for ln in body_lines:
        s = ln.strip()
        if not started:
            if not s:
                continue
            if re.match(r"arXiv:\d{4}\.\d", s) or _is_front_matter(s) or _is_section_boundary(s):
                continue
            # 标题行检测：与已知标题词高度重叠（≥60% 且 ≥2 词）
            words = [w.strip(".,:;()").lower() for w in s.split()]
            words = [w for w in words if len(w) >= 4]
            if words:
                hits = sum(1 for w in words if w in title_keys)
                if hits >= 2 and hits / len(words) >= 0.6:
                    continue
            started = True
            para = [s]
        else:
            if _is_section_boundary(s) or re.match(r"^CCS Concepts\b", s):
                break
            if s:
                para.append(s)
            elif len(" ".join(para)) >= config.ABSTRACT_MIN_CHARS:
                break
    text = " ".join(para).strip()
    return _truncate(text, config.ABSTRACT_MAX_CHARS) if len(text) >= config.ABSTRACT_MIN_CHARS else ""


def _extract_abstract(body_lines, known_title="", arxiv_id=""):
    """多启发式提取摘要；失败返回 ""（合法）。"""
    lines = body_lines[: config.BODY_SCAN_LINES]
    # 1) 显式标记
    for i, ln in enumerate(lines):
        m = ABSTRACT_RE.match(ln)
        if not m:
            continue
        chunks = [m.group(1).strip()] if m.group(1).strip() else []
        for j in range(i + 1, min(i + 1 + 40, len(body_lines))):
            l2 = body_lines[j]
            if _is_section_boundary(l2):
                break
            s = l2.strip()
            if s:
                chunks.append(s)
        text = " ".join(chunks)
        if text:
            return _truncate(text, config.ABSTRACT_MAX_CHARS)
    # 2) 无标记：策略1（论文自身 arXiv 行向后，仅限正文前部）
    #    只用论文自己的 arXiv ID 行（如 "arXiv:2308.02624v1..."），避免误取正文中引用的其他 arXiv 论文
    if arxiv_id:
        own_re = re.compile(r"arXiv:" + re.escape(arxiv_id))
        for k in [i for i, ln in enumerate(body_lines[: config.BODY_SCAN_LINES]) if own_re.search(ln)]:
            para = _scan_backward_abstract(body_lines[:k])
            if para:
                return para
    # 3) 无标记：策略2（跳过前导信息向前取首个长句段）
    return _scan_forward_abstract(body_lines, known_title)


def _extract_keywords(body_lines):
    """搜索 Keywords/关键词/INDEX TERMS，按分隔符拆分去重；缺失返回 []。"""
    for ln in body_lines[: config.KEYWORD_SCAN_LINES]:
        m = KEYWORDS_RE.match(ln)
        if not m:
            continue
        raw = m.group(1)
        parts = re.split(r"[;,、，;]+", raw)
        kws = [re.sub(r"\s+", " ", p).strip(" .") for p in parts]
        kws = [k for k in kws if k]
        return kws[: config.KEYWORDS_MAX]
    return []


# ---------------- 入口 ----------------
def parse_paper_file(path, tier="", base_dir=None):
    """解析单篇 TXT → PaperRecord；正文过短/空文件返回 None。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError as e:
        print(f"[parser] 读取失败 {path}: {e}")
        return None
    collapsed = re.sub(r"\s+", " ", raw).strip()
    if len(collapsed) < config.MIN_CONTENT_CHARS:
        return None  # 过短（乱码/空文件）
    lines = raw.splitlines()
    header_lines, body_lines = _split_header_body(lines)

    rec = PaperRecord(
        source_file=os.path.relpath(path, base_dir) if base_dir else path,
        file_md5=hashlib.md5(collapsed.encode("utf-8")).hexdigest(),
    )
    _fill_from_header(rec, _parse_header(header_lines), tier)

    # 标题缺省：用正文首行（通常重复标题）
    if not rec.title and body_lines:
        rec.title = body_lines[0].strip()

    rec.body_excerpt = collapsed[: config.PAPER_CONTEXT_CHARS]
    rec.abstract = _extract_abstract(body_lines, rec.title, rec.arxiv_id)
    rec.keywords = _extract_keywords(body_lines)
    return rec


def _iter_tier_dirs(papers_dir):
    """收集 (档位目录名, 其父目录)。兼容两种布局：

    ① 单专题（旧）：papers_dir/S档_核心/*.txt
    ② 全库六专题（2026-08-20 起，data/papers/）：papers_dir/专题X_…/S档_核心/*.txt
       —— 父目录参与 source_file 相对路径，跨专题来源可追溯
    """
    subs = sorted(d for d in os.listdir(papers_dir)
                  if os.path.isdir(os.path.join(papers_dir, d)))
    tier_dirs = [(d, papers_dir) for d in subs if TIER_DIR_RE.match(d)]
    if tier_dirs:
        return tier_dirs
    roots = []
    for d in subs:
        sub = os.path.join(papers_dir, d)
        roots.extend((td, sub) for td in sorted(os.listdir(sub))
                     if os.path.isdir(os.path.join(sub, td)) and TIER_DIR_RE.match(td))
    return roots


def scan_papers(papers_dir=None, tier=None, limit=None):
    """扫描分档目录下的 TXT → PaperRecord[]。tier 限 S/A/B；limit 限制返回数（探索用）。

    多专题布局下同一论文可在多个专题各有一份**逐字节相同**的副本（一篇论文可命中
    多个专题）——按 (文件名, file_md5) 去重，保留首个（专题目录序），避免提及/信号
    跨专题重复计权。
    """
    papers_dir = papers_dir or config.PAPER_DIR
    if not os.path.isdir(papers_dir):
        raise FileNotFoundError(f"论文数据目录不存在: {papers_dir}")
    records = []
    seen = {}  # filename -> file_md5
    n_dup = 0
    for td, parent in _iter_tier_dirs(papers_dir):
        if not TIER_DIR_RE.match(td):
            continue
        t = TIER_DIR_RE.match(td).group(1)
        if tier and t != tier:
            continue
        sub = os.path.join(parent, td)
        for fn in sorted(os.listdir(sub)):
            if not fn.endswith(".txt"):
                continue
            rec = parse_paper_file(os.path.join(sub, fn), tier=t, base_dir=papers_dir)
            if rec is None:
                continue
            if seen.get(fn) == rec.file_md5:
                n_dup += 1
                continue
            seen[fn] = rec.file_md5
            records.append(rec)
            if limit and len(records) >= limit:
                return records
    if n_dup:
        print(f"[scan] 跨专题重复论文跳过 {n_dup} 份（同文命中多专题，保留首个）")
    return records
