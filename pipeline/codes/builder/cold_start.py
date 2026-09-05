# -*- coding: utf-8 -*-
"""冷启动：采样若干数据 → LLM 归纳 → 形成初始任务体系。"""
import json
from datetime import date

import config
import prompts
from llm import call_llm


def _format_docs(documents):
    """格式化文档列表（编号 + 截断）。"""
    lines = []
    for i, d in enumerate(documents[: config.COLD_SAMPLE]):
        lines.append(f"[{i+1}] {d[: config.DOC_MAX_CHARS]}")
    return "\n".join(lines)


def cold_start(documents, taxonomy_store, n_samples=None, logger=None, mode="task"):
    """从采样文档归纳初始体系（任务或技能），写入 taxonomy_store。"""
    n = n_samples or config.COLD_SAMPLE
    docs_text = _format_docs(documents[:n])

    if mode == "skill":
        prompt = prompts.PROMPT_SKILL_COLD_START.format(
            n=min(n, len(documents)), min_n=config.SKILL_MIN_SKILLS,
            max_n=config.SKILL_MAX_SKILLS, docs=docs_text,
            non_it=prompts.NON_IT_DOMAINS, it_generic=prompts.IT_GENERIC_DUTIES,
        )
    else:
        prompt = prompts.PROMPT_COLD_START.format(
            n=min(n, len(documents)), min_n=config.COLD_MIN_TASKS,
            max_n=config.COLD_MAX_TASKS, docs=docs_text,
            non_it=prompts.NON_IT_DOMAINS, it_generic=prompts.IT_GENERIC_DUTIES,
        )
    items = call_llm(prompt, parse_json=True)
    # 健壮性：LLM 偶发返回 {"tasks": [...]} 对象而非数组，统一规整为列表
    if isinstance(items, dict):
        items = items.get("tasks") or items.get("skills") or []
    if not isinstance(items, list):
        print(f"[cold] 警告：LLM 返回格式异常（{type(items).__name__}），按空列表处理")
        items = []

    taxonomy_store.data["source"] = f"Builder 冷启动（{len(documents)} 条文档归纳，funtype→岗位体系 v2 IT 过滤 + 分层）"
    taxonomy_store.data["version"] = "0.2"
    taxonomy_store.data["date"] = date.today().isoformat()
    if mode == "skill":
        taxonomy_store.data["detail"] = {}
    else:
        taxonomy_store.data["tasks"] = []
    for t in items:
        if not isinstance(t, dict) or not t.get("name_zh"):
            continue
        taxonomy_store.add_task(
            t["name_zh"].strip(),
            t.get("name_en", ""),
            t.get("definition") or t.get("description", ""),
            skill_type=t.get("skill_type"),
        )
    taxonomy_store.save()
    if logger:
        logger.cold_start(len(documents), taxonomy_store.tasks())
    return taxonomy_store
