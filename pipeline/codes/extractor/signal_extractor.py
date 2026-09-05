# -*- coding: utf-8 -*-
"""信号提取（Stage A）：对论文批调用 LLM，产出候选信号（论文新信号分类）。

输出每条候选信号带所属 PaperRecord，供后续体系映射与 ΔG 增量层聚合。
LLM 输出畸形/失败 → 返回空信号并记 error，不中断批次。
"""
from paper_prompts import PROMPT_EXTRACT
from llm import ResourceExhaustedError, call_llm

VALID_KINDS = {"new_job", "new_task", "new_skill", "implied_task", "capability_gap", "skillpoint"}
VALID_CONF = {"high", "medium", "low"}
# 单篇论文信号数的程序化防御上限（非提示词配额，仅为防止异常输出失控）
MAX_SIGNALS_PER_PAPER = 20
# 名称长度上限（提示词规范 4-12/14 字；此处为降级保留的截断上限，宽松于规范）
MAX_NAME_CHARS = 20


def fit_name(name, max_chars=MAX_NAME_CHARS):
    """超长名降级：保留末尾核心词（中文名核心概念通常在尾部）截断到 max_chars，**不丢弃信号**。

    优先在「连接词/分隔符」后截断（避免切碎词）；无合适边界则保留末尾 max_chars。
    截断后的名字仍会交给映射层 LLM 做归一化（那里才是最终命名修正处）。
    """
    if len(name) <= max_chars:
        return name
    # 从窗口起点向后找第一个连接词，取其后的最长干净后缀（≤max_chars，且至少 4 字承载核心）
    start = len(name) - max_chars
    for i in range(start, len(name) - 4):
        if name[i] in "的与及和、，,；;/":
            return name[i + 1:]
    # 无合适边界：保留末尾 max_chars（去前导连接词）
    s = name[-max_chars:].lstrip("的与及和、，,；;/ ")
    return s or name[:max_chars]


class Candidate:
    """单条候选信号（含所属记录：论文 PaperRecord 或新闻 NewsRecord）。"""

    def __init__(self, index, record, kind, name_zh, name_en, definition,
                 rationale, evidence, confidence):
        self.index = index            # 批内候选序号（映射阶段对齐用）
        self.record = record          # PaperRecord / NewsRecord（统一 doc_id/pub_date 接口）
        self.paper = record           # 兼容别名（论文场景）
        self.kind = kind
        self.name_zh = name_zh
        self.name_en = name_en
        self.definition = definition  # 定义（它是什么）；缺省回退 rationale
        self.rationale = rationale
        self.evidence = evidence      # list[str] 原文证据句
        self.confidence = confidence  # high/medium/low


def _paper_context(paper, paper_index):
    """构造单篇论文送入 LLM 的精髓信息上下文。"""
    lines = [
        f"paper_index: {paper_index}",
        f"arxiv_id: {paper.arxiv_id} | title: {paper.title} | pub_date: {paper.pub_date} | tier: {paper.tier}",
        f"dimensions: {', '.join(paper.dimensions)}",
    ]
    if paper.evidence_sentences:
        ev = "; ".join(f"[{e['dimension']}] {e['text']}" for e in paper.evidence_sentences)
        lines.append(f"evidence_sentences: {ev}")
    if paper.keywords:
        lines.append(f"keywords: {'; '.join(paper.keywords)}")
    if paper.abstract:
        lines.append(f"abstract: {paper.abstract}")
    lines.append(f"body_excerpt: {paper.body_excerpt}")
    return "\n".join(lines)


def build_extract_prompt(papers):
    ctx = "\n\n---\n\n".join(_paper_context(p, i) for i, p in enumerate(papers))
    return PROMPT_EXTRACT + "\n\n以下是要分析的论文数据（JSON 结构见上）：\n\n" + ctx


def _validate_signal(s, papers):
    """校验单条 LLM 输出信号；非法返回 None。"""
    if not isinstance(s, dict):
        return None
    kind = str(s.get("kind", "")).strip().lower()
    if kind not in VALID_KINDS:
        return None
    name_zh = str(s.get("name_zh", "")).strip()
    if len(name_zh) < 2:
        return None
    if len(name_zh) > MAX_NAME_CHARS:
        fitted = fit_name(name_zh)
        print(f"[validate] 名称超长降级截断（{len(name_zh)}→{len(fitted)}）：{name_zh} → {fitted}")
        name_zh = fitted
    try:
        pi = int(s.get("paper_index", -1))
    except (TypeError, ValueError):
        return None
    if pi < 0 or pi >= len(papers):
        return None
    name_en = str(s.get("name_en", "")).strip()
    definition = str(s.get("definition", "")).strip()
    rationale = str(s.get("rationale", "")).strip()
    evidence = s.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = [evidence] if isinstance(evidence, str) else []
    evidence = [str(e).strip() for e in evidence if str(e).strip()]
    # 无原文证据的信号丢弃（防编造）
    if not evidence:
        return None
    # 定义必填：缺省时回退到 rationale（保证增量层每条信号都有定义）
    if not definition:
        definition = rationale
    conf = str(s.get("confidence", "low")).strip().lower()
    if conf not in VALID_CONF:
        conf = "low"
    # index 暂用 paper_index 占位，批内全局唯一序号由 extract_signals 重新赋值
    return Candidate(pi, papers[pi], kind, name_zh, name_en, definition,
                     rationale, evidence, conf)


def extract_signals(papers, api_key=None, max_tokens=None, logger=None):
    """对一批论文提取信号 → list[Candidate]。LLM 失败返回空列表（记 error，不中断）。"""
    if not papers:
        return []
    prompt = build_extract_prompt(papers)
    try:
        raw = call_llm(prompt, parse_json=True, max_tokens=max_tokens, api_key=api_key)
    except ResourceExhaustedError:
        raise  # 402 熔断：资源性故障中止运行，不降级保信号（2026-09-02 用户裁定）
    except Exception as e:
        if logger:
            logger.error("extract", f"批次 LLM 失败（{len(papers)} 篇）：{e}")
        return []
    signals = raw.get("signals", []) if isinstance(raw, dict) else []
    if not isinstance(signals, list):
        signals = []
    result = []
    per_paper_count = {}
    for s in signals:
        cand = _validate_signal(s, papers)
        if cand is None:
            continue
        pid = cand.paper.arxiv_id
        per_paper_count[pid] = per_paper_count.get(pid, 0) + 1
        if per_paper_count[pid] > MAX_SIGNALS_PER_PAPER:
            continue
        result.append(cand)
    # 批内全局唯一序号（映射阶段对齐用；同一论文的多条信号 index 各不相同）
    for i, c in enumerate(result):
        c.index = i
    return result
