# -*- coding: utf-8 -*-
"""监督 Agent：判断热更新提案是否必要。

原则：任务体系尽量精简；语义充分接近的任务视为同一任务，禁止不必要新增。
返回：被批准的提案列表（原 proposal.updates 的子集）。
"""
import json

import prompts
from llm import call_llm


def _as_index(value):
    """LLM 返回的 index 可能是数字或字符串，统一为 int；无法解析返回 None。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value):
    """LLM 返回的 approved 可能是 bool 或 'true'/'false' 字符串，统一为 bool。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "是", "y")
    return False


def supervise(proposal, taxonomy_store, mode="task"):
    """监督提案，返回 (approved_updates, rejected_reasons)。

    approved_updates: proposal.updates 中被批准的子集
    rejected_reasons: [{"index":i,"reason":...}] 被拒绝项

    健壮性：LLM 返回的 index 可能是字符串（"0"）而非数字（0），
    若不统一类型会导致与 enumerate 的 int 索引比对全部失配、误拒所有提案；
    approved 也可能是 "true"/"false" 字符串。这里统一做类型规整并跳过越界/非法项。
    """
    updates = proposal.get("updates", [])
    if not updates:
        return [], []

    proposals_text = json.dumps(updates, ensure_ascii=False, indent=1)
    prompt_cls = prompts.PROMPT_SKILL_SUPERVISE if mode == "skill" else prompts.PROMPT_SUPERVISE
    prompt = prompt_cls.format(
        labels=taxonomy_store.to_labels(),
        proposals=proposals_text,
        total_tasks=len(taxonomy_store.tasks()),
        non_it=prompts.NON_IT_DOMAINS, it_generic=prompts.IT_GENERIC_DUTIES,
    )
    decision = call_llm(prompt, parse_json=True)
    decisions = decision.get("decisions", []) if isinstance(decision, dict) else []

    approved_idx = set()
    rejected = []
    for d in decisions:
        if not isinstance(d, dict):
            continue
        idx = _as_index(d.get("index"))
        if idx is None or idx < 0 or idx >= len(updates):
            continue  # index 无法解析或越界：跳过该条决策
        if _as_bool(d.get("approved")):
            approved_idx.add(idx)
        else:
            rejected.append({"index": idx, "reason": d.get("reason", ""),
                             "map_to": d.get("map_to")})

    approved = [u for i, u in enumerate(updates) if i in approved_idx]
    return approved, rejected
