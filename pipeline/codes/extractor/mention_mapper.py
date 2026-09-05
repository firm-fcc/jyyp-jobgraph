# -*- coding: utf-8 -*-
"""提及映射：新闻中提及的名称 → 既有体系 code。

两级映射：
1. 程序化 norm 精确匹配（免费、精准）→ 直接得 code。
2. 未匹配 → 按 type 分组交 LLM 语义映射（提及名 + 该体系标签 → code 或 null）。
   LLM 失败 → 该批未匹配提及丢弃（新闻为辅助信号，宁缺毋滥）。
"""
import json
import re

import news_prompts
from llm import ResourceExhaustedError, call_llm

_TYPE_TO_TAX = {"skill": "skills", "task": "tasks", "job": "jobs"}


def norm(name):
    return re.sub(r"[\s、，,.;·\-_（）()]", "", name or "").lower()


def _build_lookup(labels):
    """norm(名称) → code，按体系 {skills/tasks/jobs}。"""
    lookup = {"skills": {}, "tasks": {}, "jobs": {}}
    for tax, items in labels.items():
        for it in items:
            for name in (it.get("name_zh"), it.get("name_en")):
                if name:
                    lookup[tax].setdefault(norm(name), it["code"])
    return lookup


def _llm_map_group(mentions, tax, labels, api_key, max_tokens, logger):
    """对同 type 的未匹配提及做一次 LLM 映射 → {name: code}。"""
    items = labels[tax]
    label_text = "\n".join(f"{l['code']}:{l['name_zh']}" for l in items) or "（无）"
    mention_json = json.dumps([{"name": m["name"], "type": m["type"]} for m in mentions],
                              ensure_ascii=False, indent=1)
    prompt = (news_prompts.MENTION_MAP_PROMPT
              .replace("{labels}", label_text)
              .replace("{mentions}", mention_json))
    try:
        raw = call_llm(prompt, parse_json=True, max_tokens=max_tokens, api_key=api_key)
    except ResourceExhaustedError:
        raise  # 402 熔断：资源性故障中止运行，不降级保信号（2026-09-02 用户裁定）
    except Exception as e:
        if logger:
            logger.error("mention_map", f"提及映射 LLM 失败（{tax}，{len(mentions)} 条）：{e}")
        return {}
    rows = raw if isinstance(raw, list) else (raw.get("results", []) if isinstance(raw, dict) else [])
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip()
        code = str(row.get("code") or "").strip()
        if name and code:
            result[name] = code
    return result


def map_mentions(mentions, labels, api_key=None, max_tokens=None, logger=None):
    """mentions: [{"type","name","evidence"}]（跨批累积）；labels: load_base_labels()。

    返回 {name: code}（无法映射的提及不出现）。
    """
    if not mentions:
        return {}
    lookup = _build_lookup(labels)
    result = {}
    unmatched_by_type = {}

    for m in mentions:
        name = (m.get("name") or "").strip()
        if not name:
            continue
        tax = _TYPE_TO_TAX.get(m.get("type", ""))
        if tax and lookup[tax].get(norm(name)):
            result[name] = lookup[tax][norm(name)]
            continue
        unmatched_by_type.setdefault(tax or "other", []).append(m)

    for tax, group in unmatched_by_type.items():
        if tax not in _TYPE_TO_TAX.values():
            continue  # 未知 type 丢弃
        result.update(_llm_map_group(group, tax, labels, api_key, max_tokens, logger))
    return result
