# -*- coding: utf-8 -*-
"""热更新提案 Agent：分析新数据，对照当前体系提出 add/merge/modify 提案。"""
import config
import prompts
from llm import call_llm


def _format_docs(documents):
    lines = []
    for i, d in enumerate(documents):
        lines.append(f"[{i+1}] {d[: config.DOC_MAX_CHARS]}")
    return "\n".join(lines)


def propose_updates(documents, taxonomy_store, mode="task"):
    """返回提案 dict：{"covered": bool, "updates": [ {...} ]}"""
    prompt_cls = prompts.PROMPT_SKILL_PROPOSE if mode == "skill" else prompts.PROMPT_PROPOSE
    prompt = prompt_cls.format(
        labels=taxonomy_store.to_labels(),
        docs=_format_docs(documents),
        n_tasks=len(taxonomy_store.tasks()),
        non_it=prompts.NON_IT_DOMAINS, it_generic=prompts.IT_GENERIC_DUTIES,
    )
    proposal = call_llm(prompt, parse_json=True)
    # 规范化
    proposal.setdefault("covered", False)
    proposal.setdefault("updates", [])
    return proposal
