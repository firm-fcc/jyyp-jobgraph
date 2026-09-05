# -*- coding: utf-8 -*-
"""新闻信号提取（Stage A）：相关新闻 → 新信号候选 + 提及名称。

- 输入：相关 NewsRecord 批（title + body 截断）。
- 输出：(candidates, mentions_by_doc)。
  candidates: list[Candidate]（新信号，复用 signal_extractor.Candidate，record=NewsRecord）
  mentions_by_doc: {doc_id: [{"type","name","evidence"}]}（既有技能/任务/岗位提及）
- 新信号必带 definition + evidence（提示词强约束 + 校验兜底）。
- LLM 失败 → 返回空，不中断批次。
"""
import os
import re

import yaml

import news_prompts
from llm import ResourceExhaustedError, call_llm
from signal_extractor import Candidate

VALID_KINDS = {"new_skill", "new_task", "new_job"}
VALID_CONF = {"high", "medium", "low"}
VALID_MENTION_TYPES = {"skill", "task", "job"}
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
# 提取时正文截断（前 N 字符，median 5.1k → 覆盖导语与核心段落）。
# 统一在全局参数中心 codes/settings.yaml → news 节；按路径读取（不经 import config，
# 跨模块导入时不会命中消费方的 config——原"参数须模块内自洽"的限制由此解除）。
_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "settings.yaml")


def _settings(*keys, default):
    """从全局参数中心读取（逐级下钻）；文件缺失/损坏/键不存在回退 default。"""
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            node = yaml.safe_load(f)
        for k in keys:
            node = node[k]
        return node
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        return default


NEWS_EXTRACT_BODY_CHARS = _settings("news", "extract_body_chars", default=3000)


def _body_excerpt(record):
    return re.sub(r"\s+", " ", record.body or "").strip()[: NEWS_EXTRACT_BODY_CHARS]


def _news_context(record, news_index):
    return (f"news_index: {news_index}\n"
            f"title: {record.title}\n"
            f"pub_date: {record.pub_date} | source: {record.source}\n"
            f"body: {_body_excerpt(record)}")


def build_extract_prompt(records):
    ctx = "\n\n---\n\n".join(_news_context(r, i) for i, r in enumerate(records))
    return news_prompts.NEWS_EXTRACT_PROMPT + "\n\n以下是要分析的新闻数据：\n\n" + ctx


def _validate_signal(s, record, index):
    """校验单条新信号；非法返回 None。"""
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
    definition = str(s.get("definition", "")).strip()
    evidence = s.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = [evidence] if isinstance(evidence, str) else []
    evidence = [str(e).strip() for e in evidence if str(e).strip()]
    # 定义与证据必填（用户硬性要求：名称+证据/证据源+定义）
    if not definition or not evidence:
        return None
    name_en = str(s.get("name_en", "")).strip()
    conf = str(s.get("confidence", "low")).strip().lower()
    if conf not in VALID_CONF:
        conf = "low"
    rationale = evidence[0] if evidence else definition
    return Candidate(index, record, kind, name_zh, name_en, definition,
                     rationale, evidence, conf)


def _validate_mention(m, record):
    """校验单条提及；非法返回 None。"""
    if not isinstance(m, dict):
        return None
    mtype = str(m.get("type", "")).strip().lower()
    if mtype not in VALID_MENTION_TYPES:
        return None
    name = str(m.get("name", "")).strip()
    if len(name) < 2:
        return None
    evidence = m.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = [evidence] if isinstance(evidence, str) else []
    evidence = [str(e).strip() for e in evidence if str(e).strip()]
    return {"type": mtype, "name": name, "evidence": evidence}


def extract_news_signals(records, api_key=None, max_tokens=None, logger=None):
    """对一批相关新闻提取信号 → (candidates, mentions_by_doc)。"""
    if not records:
        return [], {}
    prompt = build_extract_prompt(records)
    try:
        raw = call_llm(prompt, parse_json=True, max_tokens=max_tokens, api_key=api_key)
    except ResourceExhaustedError:
        raise  # 402 熔断：资源性故障中止运行，不降级保信号（2026-09-02 用户裁定）
    except Exception as e:
        if logger:
            logger.error("news_extract", f"批次 LLM 失败（{len(records)} 篇）：{e}")
        return [], {}
    entries = raw.get("news_signals", []) if isinstance(raw, dict) else []
    if not isinstance(entries, list):
        entries = []

    candidates = []
    mentions = {}
    idx = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            ni = int(entry.get("news_index", -1))
        except (TypeError, ValueError):
            continue
        if ni < 0 or ni >= len(records):
            continue
        rec = records[ni]
        for s in entry.get("new_signals", []) or []:
            cand = _validate_signal(s, rec, idx)
            if cand is None:
                continue
            candidates.append(cand)
            idx += 1
        for m in entry.get("mentions", []) or []:
            vm = _validate_mention(m, rec)
            if vm:
                mentions.setdefault(rec.doc_id, []).append(vm)
    return candidates, mentions
