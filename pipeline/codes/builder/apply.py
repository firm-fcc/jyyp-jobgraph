# -*- coding: utf-8 -*-
"""应用热更新提案：将批准的 add/merge/modify 落到任务体系。"""
import config


def apply_updates(taxonomy_store, approved_updates):
    """把批准的提案应用到 taxonomy_store。返回应用动作日志。"""
    log = []
    for u in approved_updates:
        action = u.get("action")
        try:
            if action == "add":
                t = u.get("task", {})
                if t.get("name_zh"):
                    name = t["name_zh"].strip()
                    # 同名防重：LLM 可能跨批次/跨重检重复提案已存在的任务
                    if any(x["name_zh"] == name for x in taxonomy_store.tasks()):
                        log.append(f"skip add {name}（已存在同名任务）")
                        continue
                    task = taxonomy_store.add_task(
                        name, t.get("name_en", ""),
                        t.get("definition") or t.get("description", ""),
                        skill_type=t.get("skill_type"))
                    log.append(f"add {task['code']}:{task['name_zh']}")
            elif action == "merge":
                codes = u.get("merge_codes") or []
                if len(codes) >= 2:
                    ok = taxonomy_store.merge_tasks(codes[0], codes[1])
                    if ok:
                        log.append(f"merge {codes[1]} -> {codes[0]}")
            elif action == "modify":
                code = u.get("target_code") or (u.get("task") or {}).get("code")
                t = u.get("task", {})
                if code:
                    ok = taxonomy_store.modify_task(
                        code, t.get("name_zh"), t.get("name_en"),
                        t.get("definition") or t.get("description"), t.get("skill_type"))
                    if ok:
                        log.append(f"modify {code}")
        except Exception as e:
            log.append(f"error: {action} -> {e}")
    taxonomy_store.save()
    return log
