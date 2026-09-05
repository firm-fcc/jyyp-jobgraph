# -*- coding: utf-8 -*-
"""JD 侧信号提取（Stage A）：JD 记录 → 新信号候选 + 提及名称。

- 输入：JDRecord 批（title=职位名 / funtype / body=job_information 截断；由 builder/jd_delta 构造）。
- 输出：(candidates, mentions_by_doc)。
  candidates: list[Candidate]（复用 signal_extractor.Candidate，record=JDRecord）
  mentions_by_doc: {doc_id: [{"type","name","evidence"}]}
- 叠层可见实体清单（overlay_labels）注入提示词：明确提及者输出 mention（确证通道），
  避免对已跟踪实体重复输出 new_signal。
- 新信号必带 definition + evidence（提示词强约束 + 校验兜底）；LLM 失败返回空，不中断批次。
"""
import os
import re

import yaml

import jd_prompts
from llm import ResourceExhaustedError, call_llm
from signal_extractor import Candidate

VALID_KINDS = {"new_skill", "new_task", "new_job", "skillpoint"}
VALID_CONF = {"high", "medium", "low"}
VALID_MENTION_TYPES = {"skill", "task", "job"}
MAX_NAME_CHARS = 20

_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "settings.yaml")


def _settings(*keys, default):
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            node = yaml.safe_load(f)
        for k in keys:
            node = node[k]
        return node
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        return default


JD_EXTRACT_BODY_CHARS = _settings("jd", "extract_body_chars", default=2000)


def _body_excerpt(record):
    return re.sub(r"\s+", " ", record.body or "").strip()[: JD_EXTRACT_BODY_CHARS]


def _jd_context(record, jd_index):
    return (f"jd_index: {jd_index}\n"
            f"title: {record.title}\n"
            f"funtype: {record.funtype} | pub_date: {record.pub_date}\n"
            f"body: {_body_excerpt(record)}")


def build_extract_prompt(records, overlay_labels=None):
    labels_text = overlay_labels or "（无）"
    prompt = jd_prompts.JD_EXTRACT_PROMPT.replace("{overlay_labels}", labels_text)
    ctx = "\n\n---\n\n".join(_jd_context(r, i) for i, r in enumerate(records))
    return prompt + "\n\n以下是要分析的 JD 数据：\n\n" + ctx


def _fit_name(name, max_chars=MAX_NAME_CHARS):
    """超长名降级：连接词边界截断保留末尾核心（与 signal_extractor.fit_name 同策略）。"""
    if len(name) <= max_chars:
        return name
    start = len(name) - max_chars
    for i in range(start, len(name) - 4):
        if name[i] in "的与及和、，,；;/":
            return name[i + 1:]
    s = name[-max_chars:].lstrip("的与及和、，,；;/ ")
    return s or name[:max_chars]


def _validate_signal(s, record, index):
    if not isinstance(s, dict):
        return None
    kind = str(s.get("kind", "")).strip().lower()
    if kind not in VALID_KINDS:
        return None
    name_zh = str(s.get("name_zh", "")).strip()
    if len(name_zh) < 2:
        return None
    if len(name_zh) > MAX_NAME_CHARS:
        name_zh = _fit_name(name_zh)
    definition = str(s.get("definition", "")).strip()
    evidence = s.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = [evidence] if isinstance(evidence, str) else []
    evidence = [str(e).strip() for e in evidence if str(e).strip()]
    if not definition or not evidence:
        return None
    name_en = str(s.get("name_en", "")).strip()
    conf = str(s.get("confidence", "low")).strip().lower()
    if conf not in VALID_CONF:
        conf = "low"
    rationale = evidence[0]
    return Candidate(index, record, kind, name_zh, name_en, definition,
                     rationale, evidence, conf)


def _validate_mention(m, record):
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


def extract_jd_signals(records, overlay_labels=None, api_key=None, max_tokens=None, logger=None):
    """对一批 JD 提取信号 → (candidates, mentions_by_doc)。"""
    if not records:
        return [], {}
    prompt = build_extract_prompt(records, overlay_labels)
    try:
        raw = call_llm(prompt, parse_json=True, max_tokens=max_tokens, api_key=api_key)
    except ResourceExhaustedError:
        raise  # 402 熔断：资源性故障中止运行，不降级保信号（2026-09-02 用户裁定）
    except Exception as e:
        if logger:
            logger.error("jd_extract", f"批次 LLM 失败（{len(records)} 条）：{e}")
        return [], {}
    entries = raw.get("jd_signals", []) if isinstance(raw, dict) else []
    if not isinstance(entries, list):
        entries = []

    candidates = []
    mentions = {}
    idx = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            ji = int(entry.get("jd_index", -1))
        except (TypeError, ValueError):
            continue
        if ji < 0 or ji >= len(records):
            continue
        rec = records[ji]
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
