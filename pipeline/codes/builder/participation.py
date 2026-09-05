# -*- coding: utf-8 -*-
"""叠层可见性（participation）：哪些叠层实体有资格参与下一次更新。

生命周期语义（docs/loop-design.md「叠层生命周期」）：
- **参与门槛**：三源 merge 视图中 strength ≥ overlay.participate_min_strength 的实体，
  进入下一次更新的映射标签空间（papers/news/jd 管线的 delta_items、JD 提取提示词的
  overlay 清单、mention 映射的扩展标签）——「作为体系的一部分参与下一次更新」。
- **遗忘 = 降级而非删除**：周期内无再现 → 半衰期衰减使 strength 下降 → 跌破参与门槛即
  休眠（失去可见性），但条目保留在 ΔG 文件中；再次出现时 noisy-OR 累积强度即可唤醒。
- **增强**：反复出现 → 证据 noisy-OR 累积，强度单调上升（delta_store 既有机制）。

合并视图复用 graph/snapshot_builder.merge_delta（三源、按窗口末重算强度）——
本模块是 builder → graph 的受控依赖（graph 不反向依赖本模块，无环）。
"""
import json
import os
import sys
from datetime import date

import config

_HERE = os.path.dirname(os.path.abspath(__file__))
_GRAPH_DIR = os.path.abspath(os.path.join(_HERE, "..", "graph"))
if _GRAPH_DIR not in sys.path:
    sys.path.insert(0, _GRAPH_DIR)

# 注意：graph_config 会把 builder 版 config 缓存进 sys.modules["config"]，本模块自身
# 已在 builder 目录（import config 即 builder 版），先导入 graph 侧不受影响。
from snapshot_builder import merge_delta  # noqa: E402

_KIND_TO_TAX = {"new_jobs": "jobs", "new_tasks": "tasks", "new_skills": "skills",
                "skillpoints": "skills"}
_KIND_TYPE = {"new_jobs": "job", "new_tasks": "task", "new_skills": "skill",
              "skillpoints": "skill"}


def merged_view(now=None, delta_files=None):
    """三源 ΔG 的合并视图（截至 now，缺省今天）。返回 (merged_dict, files_used)。"""
    now = now or date.today()
    d_files = delta_files or config.DELTA_FILES
    data, used = {}, []
    for src in ("papers", "news", "jd"):
        path = d_files.get(src)
        if path and os.path.exists(path):
            try:
                data[src] = json.load(open(path, encoding="utf-8"))
                used.append(path)
            except (OSError, ValueError):
                pass
    merged, _ = merge_delta(data.get("papers") or {}, data.get("news") or {},
                            data.get("jd") or {}, now)
    return merged, used


def _remap_active(now, remapped_window):
    """处置生效判定（与 graph/snapshot_builder._remap_active 同口径）：now ≥ 生效窗首日。"""
    try:
        y, m = int(remapped_window[:4]), int(remapped_window[5:7])
        return now >= date(y, m, 1)
    except (TypeError, ValueError):
        return False


def participating_items(min_strength=None, now=None, delta_files=None, exclude_src=None):
    """达到参与门槛的叠层实体（供下次更新的标签空间）。

    返回 [{"id","name_zh","array","strength","sources"}]。
    已处置条目（remapped_window 生效，即 now ≥ 生效窗首日）不参与——与快照层
    _merge_named_entries 的处置退役口径一致（历史窗口不受影响）。
    exclude_src（"papers"|"news"|"jd"）：剔除**仅**存在于该源的条目——调用方
    （如 news_delta）传自己的源名，同文件条目由调用方的 existing_items() 全量提供
    （跨文档合并不受门控），此处只注入跨源参与条目，避免同条目两个 id 重复注入。
    """
    min_strength = config.OVERLAY_PARTICIPATE_MIN if min_strength is None else min_strength
    now = now or date.today()
    merged, _ = merged_view(now=now, delta_files=delta_files)
    out = []
    for arr in ("new_jobs", "new_tasks", "new_skills"):
        for it in merged.get(arr, []):
            if it.get("strength", 0) < min_strength:
                continue
            rw = it.get("remapped_window")
            if rw and _remap_active(now, rw):
                continue
            sources = set(it.get("sources", []) or [])
            if exclude_src and sources and sources <= {exclude_src}:
                continue
            out.append({"id": it.get("id", ""), "name_zh": it.get("name_zh", ""),
                        "array": arr, "strength": it.get("strength", 0),
                        "definition": it.get("definition") or it.get("description") or "",
                        "name_en": it.get("name_en", ""),
                        "born_window": it.get("born_window") or "",
                        "remapped_window": it.get("remapped_window") or "",
                        "sources": sorted(sources)})
    return out


def overlay_labels(items):
    """参与实体 → 扩展标签形态（对齐 load_base_labels 的 {tasks, skills, jobs}）。

    叠层 id（PT-/PS-/PJ-）直接作 code：与基线 code 空间不冲突，map_mentions 的
    norm 查找与 LLM 映射均可直接命中；命中叠层 id = 确证（jd 侧）或跨源合并（论文/新闻侧）。
    """
    labels = {"tasks": [], "skills": [], "jobs": []}
    for it in items:
        tax = _KIND_TO_TAX.get(it.get("array", ""))
        if tax:
            labels[tax].append({"code": it["id"], "name_zh": it.get("name_zh", ""),
                                "name_en": "", "overlay": True})
    return labels


def overlay_labels_text(items):
    """参与实体清单文本（注入 JD 提取提示词的 {overlay_labels} 占位）。"""
    if not items:
        return "（无）"
    lines = []
    for it in items:
        kind = _KIND_TYPE.get(it.get("array", ""), "signal")
        lines.append(f"- {it['id']}:{it['name_zh']}（{kind}，强度 {it.get('strength', 0)}）")
    return "\n".join(lines)


def participating_delta_items(exclude_src=None, now=None, delta_files=None):
    """供 map_signals(delta_items=...) 的跨源参与条目（形态对齐 DeltaStore.existing_items）。

    调用方应与自身 existing_items() 拼接：同文件全量（跨文档合并不受门控） +
    跨源仅参与可见者（可见性门控）。exclude_src 传调用方自己的源名。
    """
    return [{"id": it["id"], "name_zh": it["name_zh"], "array": it["array"]}
            for it in participating_items(exclude_src=exclude_src, now=now,
                                          delta_files=delta_files)]
