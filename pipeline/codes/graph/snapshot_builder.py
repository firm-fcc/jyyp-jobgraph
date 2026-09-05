# -*- coding: utf-8 -*-
"""图谱时间截面快照构建：从基图体系 + 叠层两源生成 `data/graph/{窗口}/`。

存储机制：每个时间窗口（月 `YYYY-MM` 或季度 `YYYY-Qn`）一个文件夹，
内含 `base/`（基图，节点=体系 JSON 原样拷贝 + 空边 schema）与
`delta/`（叠层，papers+news 合并为单层，证据按窗口过滤、强度用窗口末重算）。

叠层合并语义（截至窗口末的累积单层视图）：
- 合并键：new_* 按 `norm(name_zh)`；strengthenings 按 `(taxonomy, code)`。
- 证据日期过滤：`date ≤ period_end`（含末天）；无日期或解析失败**保守保留**。
- 强度重算：按证据逐条判定来源（`"tier" in ev` = 论文，否则新闻），
  复用 `delta_store._recency_decay/_noisy_or/norm` + `config` 权重常量。
  **不调用 `DeltaStore._contrib`（绑定单 source_kind）或 `save()`（改源文件+剪枝）。**

纯 stdlib、零 LLM 调用。`data/` 已 gitignore，快照可脚本重建。
"""
import calendar
import json
import os
import re
import sys
from datetime import date, datetime

import graph_config as config
import delta_store  # builder 目录已由 graph_config 加入 sys.path

# ---------------- 时间窗口解析 ----------------
_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_QUAR_RE = re.compile(r"^(\d{4})-Q([1-4])$")
_YM_PREFIX_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])")


def parse_window(window):
    """解析时间窗口标签 → (kind, start, end, sort_key)。kind ∈ {'month','quarter'}。

    - `YYYY-MM`：单月窗口；`YYYY-Qn`：季度窗口。
    - sort_key = 起始年月的对齐值（Q1→1、Q2→4…），使两种粒度可混排。
    - period_end 为该月/季**末天**（含当天）。
    """
    if not isinstance(window, str) or not window:
        raise ValueError(f"window 必须是 'YYYY-MM' 或 'YYYY-Qn' 字符串: {window!r}")
    m = _MONTH_RE.match(window)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        start = date(y, mo, 1)
        end = date(y, mo, calendar.monthrange(y, mo)[1])
        return "month", start, end, y * 12 + mo
    q = _QUAR_RE.match(window)
    if q:
        y, n = int(q.group(1)), int(q.group(2))
        start = date(y, 3 * n - 2, 1)
        end = date(y, 3 * n, calendar.monthrange(y, 3 * n)[1])
        return "quarter", start, end, y * 12 + 3 * n
    raise ValueError(f"无法解析时间窗口: {window!r}（应为 YYYY-MM 或 YYYY-Qn）")


def auto_window(delta_files=None):
    """推导默认窗口：JD timeline 最大月份 > 叠层证据最大月份 > 当前月。"""
    jd_dir = os.path.join(config.PROJECT_ROOT, "data", "timeline", "jd")
    months = []
    if os.path.isdir(jd_dir):
        for fn in os.listdir(jd_dir):
            m = _YM_PREFIX_RE.match(fn.replace(".csv", ""))
            if m:
                months.append((int(m.group(1)), int(m.group(2))))
    if months:
        y, mo = max(months)
        return f"{y:04d}-{mo:02d}"
    ev = _max_evidence_month(delta_files)
    if ev:
        y, mo = ev
        return f"{y:04d}-{mo:02d}"
    y, mo = date.today().year, date.today().month
    print(f"[snapshot] 无可用时间数据，回退当前月 {y:04d}-{mo:02d}")
    return f"{y:04d}-{mo:02d}"


def _max_evidence_month(delta_files=None):
    """叠层证据的最大月份（用于 auto_window）。"""
    maxm = None
    for src, path in (delta_files or config.DELTA_FILES).items():
        if not os.path.exists(path):
            continue
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        for arr in ("new_jobs", "new_tasks", "new_skills", "skillpoints", "strengthenings"):
            for it in data.get(arr, []) if isinstance(data, dict) else []:
                ev = it.get("evidence") if isinstance(it, dict) else None
                for v in (ev or {}).values():
                    if not isinstance(v, dict):
                        continue
                    m = _YM_PREFIX_RE.match(v.get("date", ""))
                    if m:
                        key = (int(m.group(1)), int(m.group(2)))
                        if maxm is None or key > maxm:
                            maxm = key
    return maxm


# ---------------- 强度重算（按证据逐条判定来源） ----------------
def _contrib(ev, window_end):
    """单条证据的贡献权重：按显式 src 判源（jd）→ tier 键判源（论文）→ 兜底新闻。

    - ev["src"]=="jd"：JD 确证（JD_SOURCE_WEIGHT × 半衰期 365）
    - "tier" in ev：论文（TIER_WEIGHTS × 半衰期 730；旧数据无 src，走此分支）
    - 其余：新闻（0.4 × 半衰期 180；旧数据无 src 无 tier 兜底于此）
    """
    if ev.get("src") == "jd":
        tw, hl = config.JD_SOURCE_WEIGHT, config.JD_HALF_LIFE_DAYS
    elif "tier" in ev:
        tw, hl = config.TIER_WEIGHTS.get(ev.get("tier") or "", 0.2), config.HALF_LIFE_DAYS
    else:
        tw, hl = config.NEWS_SOURCE_WEIGHT, config.NEWS_HALF_LIFE_DAYS
    cw = config.CONF_WEIGHT.get(ev.get("confidence") or "", 0.3)
    decay = delta_store._recency_decay(ev.get("date", ""), window_end, hl)
    return tw * cw * decay


def _evidence_in_window(ev, window_end):
    """证据是否落在窗口内（含末天）。无日期或解析失败保守保留（与 _recency_decay 同一套解析）。"""
    d = ev.get("date", "")
    if not d:
        return True
    try:
        dd = datetime.strptime(d, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return True
    return dd <= window_end


def _filter_evidence(ev_dict, window_end, stats):
    """按窗口过滤 evidence。返回 (kept, n_filtered, n_dateless)。"""
    kept, n_filtered, n_dateless = {}, 0, 0
    for doc_id, ev in sorted(ev_dict.items()):
        if not isinstance(ev, dict):
            continue
        if not ev.get("date"):
            n_dateless += 1
        if _evidence_in_window(ev, window_end):
            kept[doc_id] = dict(ev)
        else:
            n_filtered += 1
    return kept, n_filtered, n_dateless


def recompute_entry(entry, window_end):
    """就地重算强度字段（strength/max_contrib/first_seen/last_seen/tiers）。"""
    contribs = [_contrib(ev, window_end) for ev in entry.get("evidence", {}).values()]
    entry["strength"] = round(delta_store._noisy_or(contribs), 4)
    entry["max_contrib"] = round(max(contribs), 4) if contribs else 0.0
    dates = sorted({ev.get("date", "") for ev in entry.get("evidence", {}).values() if ev.get("date")})
    if dates:
        entry["first_seen"], entry["last_seen"] = dates[0], dates[-1]
    else:
        entry.pop("first_seen", None)
        entry.pop("last_seen", None)
    tiers = sorted({ev.get("tier", "") for ev in entry.get("evidence", {}).values() if ev.get("tier")})
    if tiers:
        entry["tiers"] = tiers
    else:
        entry.pop("tiers", None)


# ---------------- 叠层合并 ----------------
def _union_links(lists):
    """关联链接并集去重（按 taxonomy+code）。"""
    seen, out = set(), []
    for links in lists:
        for l in links:
            if not isinstance(l, dict):
                continue
            key = (l.get("taxonomy", ""), l.get("code", ""))
            if not key[0] or key in seen:
                continue
            seen.add(key)
            out.append(l)
    return out


def _merge_same_name(group):
    """同 norm 名条目合并：保留定义更丰富的一条，evidence 并集（同 doc_id 句子去重），
    id 取 papers 侧优先（其次 jd，最后 news）。"""
    base = max(group, key=lambda e: (e.get("_src") == "papers", e.get("_src") == "jd",
                                     len(e.get("definition") or e.get("description") or "")))
    merged = dict(base)
    merged.pop("_src", None)
    ev = {}
    for e in group:
        for doc_id, v in e.get("evidence", {}).items():
            if doc_id not in ev:
                ev[doc_id] = dict(v)
                continue
            sents = ev[doc_id].setdefault("sentences", [])
            seen = set(sents)
            for s in v.get("sentences", []):
                if s not in seen:
                    sents.append(s)
                    seen.add(s)
    merged["evidence"] = ev
    srcs = sorted({e.get("_src") for e in group if e.get("_src")})
    merged["sources"] = srcs or merged.get("sources", [])
    return merged


def _remap_active(window_end, remapped_window):
    """处置生效判定：window_end（date）≥ 生效窗首日 → 条目已退役。

    remapped_window="YYYY-MM"（人工处置/裁决脚本写入）：生效窗及之后的快照/参与视图
    不再含该条目；生效窗之前的窗口不受影响（历史快照逐字节保持，不回溯重写）。
    """
    try:
        y, m = int(remapped_window[:4]), int(remapped_window[5:7])
        return window_end >= date(y, m, 1)
    except (TypeError, ValueError):
        return False


def _merge_named_entries(source_lists, window_end, stats):
    """合并 new_jobs/new_tasks/new_skills/skillpoints 四类：按 norm(name_zh) 分组、过滤、重算。

    source_lists: [(src_name, entries), ...]（papers/news/jd 三源，缺源传空列表）。
    已转正条目（status=="graduated"）跳过——它们已入基图，不再属于叠层视图。
    已处置条目（remapped_window 生效）跳过——并入既有体系/跨kind 裁决/类别名退役，
    自生效窗起（退役不回溯：历史窗口该条目本就存在）。
    重命名（2026-08-30 裁定）：**就地改名、回溯传播**——store 单条新名条目自出生窗起
    在所有窗口渲染新名（快照经 replay 全窗重建统一呈现）；改名审计链（rename_history）
    只留 ΔG store，不进快照产物。
    """
    buckets, order = {}, []
    for src, entries in source_lists:
        for e in entries:
            if not isinstance(e, dict):
                continue
            if e.get("status") == "graduated":
                gw = e.get("graduated_window")
                if not gw or _remap_active(window_end, gw):
                    stats["n_graduated_skipped"] += 1
                    continue
                # 生效窗前：实体尚未入基图，仍按叠层渲染（历史视图不变）
            rw = e.get("remapped_window")
            if rw and _remap_active(window_end, rw):
                stats["n_remapped_skipped"] += 1
                continue
            ev = e.get("evidence") or {}
            if not isinstance(ev, dict):
                ev = {}
            kept, n_f, n_dl = _filter_evidence(ev, window_end, stats)
            stats["n_evidence_kept"] += len(kept)
            stats["n_evidence_filtered"] += n_f
            stats["n_dateless_evidence"] += n_dl
            if not kept:
                stats["n_dropped_no_evidence"] += 1
                continue
            entry = dict(e)
            entry["evidence"] = {k: v for k, v in kept.items()}
            entry["_src"] = src
            # 处置/改名审计元数据不进快照产物（图谱消费者只见现名）
            entry.pop("remapped_window", None)
            entry.pop("remap_note", None)
            entry.pop("renamed_window", None)
            entry.pop("renamed_from", None)
            entry.pop("rename_history", None)
            key = delta_store.norm(entry.get("name_zh", ""))
            if not key:
                continue
            if key not in buckets:
                buckets[key] = []
                order.append(key)
            buckets[key].append(entry)

    out = []
    for key in order:
        group = buckets[key]
        if len(group) == 1:
            merged = group[0]
            merged["sources"] = [group[0]["_src"]]
        else:
            stats["n_merged_norm"] += 1
            merged = _merge_same_name(group)
            # 岗位关联链接并集去重（papers/news/jd 可能各自回填过）
            for field in ("related_tasks", "related_skills"):
                if any(field in e for e in group):
                    merged[field] = _union_links([e.get(field, []) for e in group])
        # 入场窗 = 跨源最早入场（确证滞后语义：市场响应须来自最早入场窗之后的数据）
        bws = [e.get("born_window") for e in group if e.get("born_window")]
        if bws:
            merged["born_window"] = min(bws)
        recompute_entry(merged, window_end)
        # 镜像 _prune_low_strength：单证据且强度低于阈值 → 剔除
        if merged.get("strength", 0) < config.MIN_STRENGTH and len(merged.get("evidence", {})) <= 1:
            stats["n_dropped_low_strength"] += 1
            continue
        # 可见性标记：≥ 参与门槛才进入下一次更新的标签空间（遗忘=跌破门槛休眠，不删除）
        merged["participates"] = bool(merged.get("strength", 0) >= config.OVERLAY_PARTICIPATE_MIN)
        merged.pop("_src", None)
        out.append(merged)
    out.sort(key=lambda x: x.get("id", ""))
    return out


def _merge_strengthenings(source_lists, window_end, stats):
    """合并 strengthenings：按 (taxonomy, code) 分组、过滤、重算。source_lists 同上。"""
    buckets, order = {}, []
    for src, entries in source_lists:
        for e in entries:
            if not isinstance(e, dict):
                continue
            ev = e.get("evidence") or {}
            if not isinstance(ev, dict):
                ev = {}
            kept, n_f, n_dl = _filter_evidence(ev, window_end, stats)
            stats["n_evidence_kept"] += len(kept)
            stats["n_evidence_filtered"] += n_f
            stats["n_dateless_evidence"] += n_dl
            if not kept:
                stats["n_dropped_no_evidence"] += 1
                continue
            entry = dict(e)
            entry["evidence"] = {k: v for k, v in kept.items()}
            entry["_src"] = src
            key = (entry.get("taxonomy", ""), entry.get("code", ""))
            if not key[0] or not key[1]:
                continue
            if key not in buckets:
                buckets[key] = []
                order.append(key)
            buckets[key].append(entry)

    out = []
    for key in order:
        group = buckets[key]
        if len(group) == 1:
            merged = group[0]
            merged["sources"] = [group[0]["_src"]]
        else:
            stats["n_merged_norm"] += 1
            merged = _merge_same_name(group)
        recompute_entry(merged, window_end)
        if merged.get("strength", 0) < config.MIN_STRENGTH and len(merged.get("evidence", {})) <= 1:
            stats["n_dropped_low_strength"] += 1
            continue
        merged.pop("_src", None)
        out.append(merged)
    out.sort(key=lambda x: (x.get("taxonomy", ""), x.get("code", "")))
    return out


def merge_delta(papers, news, jd, window_end):
    """合并 papers+news+jd 增量层为单层 ΔG（截至 window_end）。返回 (merged_dict, stats)。

    三源均可为空 dict（源文件缺失/为空时合法）。全空 → 空 ΔG。
    """
    papers, news, jd = papers or {}, news or {}, jd or {}
    merged = {"new_jobs": [], "new_tasks": [], "new_skills": [], "skillpoints": [], "strengthenings": []}
    stats = {"n_evidence_kept": 0, "n_evidence_filtered": 0, "n_merged_norm": 0,
             "n_dropped_no_evidence": 0, "n_dropped_low_strength": 0, "n_dateless_evidence": 0,
             "n_graduated_skipped": 0, "n_remapped_skipped": 0}
    for arr in ("new_jobs", "new_tasks", "new_skills", "skillpoints"):
        merged[arr] = _merge_named_entries(
            [(src, d.get(arr, [])) for src, d in (("papers", papers), ("news", news), ("jd", jd))],
            window_end, stats)
    merged["strengthenings"] = _merge_strengthenings(
        [("papers", papers.get("strengthenings", [])),
         ("news", news.get("strengthenings", [])),
         ("jd", jd.get("strengthenings", []))], window_end, stats)
    return merged, stats


# ---------------- 岗位关联边 ----------------
def _build_job_links(merged_delta, base_nodes):
    """从 new_jobs[].related_tasks/related_skills 转边，过滤悬空 dst。

    目标空间：taxonomy ∈ {tasks, skills} → 基层 code（T-/S-）；∈ {new_tasks, new_skills} → 叠层 id（PT-/PS-）。
    """
    delta_ids = set()
    for arr in ("new_tasks", "new_skills"):
        for it in merged_delta.get(arr, []):
            delta_ids.add(it.get("id", ""))
    base_ids = set()
    for nk in ("jobs", "tasks", "skills"):
        data = base_nodes.get(nk)
        if not isinstance(data, dict):
            continue
        if nk in ("jobs", "skills"):
            base_ids.update(data.get("detail", {}).keys())
        elif nk == "tasks":
            base_ids.update(t.get("code", "") for t in data.get("tasks", []))

    links = []
    for job in merged_delta.get("new_jobs", []):
        weight = job.get("strength", 0) or 1.0
        for spec, relation in ((job.get("related_tasks") or [], "job_task"),
                               (job.get("related_skills") or [], "job_skill")):
            for link in spec:
                if not isinstance(link, dict):
                    continue
                tax, code = link.get("taxonomy", ""), link.get("code", "")
                if tax in ("new_tasks", "new_skills"):
                    ok = code in delta_ids
                elif tax in ("tasks", "skills"):
                    ok = code in base_ids
                else:
                    ok = False
                if not ok:
                    continue
                links.append({"src": job.get("id", ""), "src_name": job.get("name_zh", ""),
                              "dst": code, "dst_name": link.get("name_zh", ""),
                              "relation": relation, "taxonomy": tax, "weight": round(weight, 4)})
    seen, out = set(), []
    for l in links:
        key = (l["src"], l["dst"], l["relation"])
        if key in seen:
            continue
        seen.add(key)
        out.append(l)
    out.sort(key=lambda x: (x["src"], x["dst"], x["relation"]))
    return out


# ---------------- 写文件 ----------------
def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def _keep_existing(path):
    """keep_base_edges：目标边/节点文件已非空（total>0）则返回原内容，否则 None。

    Loop 中「重建快照 → 合成」不得清空 base_builder 已算好的基图边与技能点；
    base/ 下的 freq/entity_freq/build_info 附属产物本模块从不写入，天然保留。
    """
    if not os.path.exists(path):
        return None
    try:
        data = json.load(open(path, encoding="utf-8"))
        if isinstance(data, dict) and data.get("total", 0) > 0:
            return data
    except (OSError, ValueError):
        pass
    return None


def _existing_edge_counts(slice_dir):
    """磁盘上已有基图边计数（force 重建时供 meta.stats 汇总）。"""
    base_dir = os.path.join(slice_dir, config.BASE_SUBDIR)
    counts = {}
    for ek in config.BASE_EDGE_KINDS:
        p = os.path.join(base_dir, config.EDGE_FILENAMES[ek])
        try:
            counts[ek] = json.load(open(p, encoding="utf-8")).get("total", 0)
        except (OSError, ValueError):
            counts[ek] = 0
    return counts


def _edge_file(system_name, relation, window, edges):
    return {"system_name": system_name, "schema_version": "0.1", "window": window,
            "relation": relation, "created": datetime.now().isoformat(timespec="seconds"),
            "total": len(edges), "edges": edges}


def _item_file(system_name, window, items):
    return {"system_name": system_name, "schema_version": "0.1", "window": window,
            "total": len(items), "items": items}


_EDGE_NAMES = {
    "job_task": "关系：岗位→任务（基图）",
    "job_skill": "关系：岗位→技能（基图）",
    "task_skill": "关系：任务→技能（基图）",
    "skill_skillpoint": "关系：技能→技能点（基图）",
}
_DELTA_NODE_NAMES = {
    "new_jobs": "新岗位", "new_tasks": "新任务", "new_skills": "新技能", "skillpoints": "技能点",
}


def _count_nodes(nk, data):
    if not isinstance(data, dict):
        return 0
    if nk in ("jobs", "skills"):
        return len(data.get("detail", {}))
    if nk == "tasks":
        return len(data.get("tasks", []))
    return 0


def _write_slice(slice_dir, window, kind, start, end,
                 base_nodes, merged_delta, job_links,
                 base_inputs, delta_inputs, stats, delta_missing,
                 keep_base_edges=True):
    base_dir = os.path.join(slice_dir, config.BASE_SUBDIR)
    delta_dir = os.path.join(slice_dir, config.DELTA_SUBDIR)

    # 基图节点：体系 JSON 原样拷贝
    for nk in ("jobs", "tasks", "skills"):
        if base_nodes.get(nk) is not None:
            _write_json(os.path.join(base_dir, config.BASE_NODE_FILENAMES[nk]), base_nodes[nk])
    # 基图技能点：新 schema，初始空（base_builder 回填；已非空且 keep 时保留）
    sp_path = os.path.join(base_dir, config.BASE_NODE_FILENAMES["skillpoints"])
    if not (keep_base_edges and _keep_existing(sp_path) is not None):
        _write_json(sp_path, {"system_name": "技能点体系（基图）", "schema_version": "0.1",
                              "window": window, "total": 0, "skillpoints": {}})
    # 基图边：空 schema（base_builder 填充；已非空且 keep 时保留，防 Loop 重建快照丢边）
    for ek in config.BASE_EDGE_KINDS:
        p = os.path.join(base_dir, config.EDGE_FILENAMES[ek])
        if keep_base_edges and _keep_existing(p) is not None:
            continue
        _write_json(p, _edge_file(_EDGE_NAMES[ek], ek, window, []))

    # 叠层节点
    for nk, fname in config.DELTA_NODE_FILENAMES.items():
        _write_json(os.path.join(delta_dir, fname),
                    _item_file(f"叠层{_DELTA_NODE_NAMES[nk]}（ΔG）", window, merged_delta.get(nk, [])))
    # 叠层边
    _write_json(os.path.join(delta_dir, config.EDGE_FILENAMES["strengthenings"]),
                _item_file("叠层增强（strengthenings）", window, merged_delta.get("strengthenings", [])))
    _write_json(os.path.join(delta_dir, config.EDGE_FILENAMES["job_links"]),
                _edge_file("叠层岗位关联边（新岗位→任务/技能）", "job_links", window, job_links))

    meta = {
        "system_name": "图谱时间截面快照", "schema_version": "0.1",
        "window": window, "granularity": kind,
        "period_start": start.isoformat(), "period_end": end.isoformat(),
        "created": datetime.now().isoformat(timespec="seconds"),
        "inputs": {"base": base_inputs, "delta": delta_inputs},
        "weights": config.WEIGHTS,
        "params_fingerprint": config.assembly_params_fingerprint(),
        "delta_missing": delta_missing,
        "stats": stats,
    }
    _write_json(os.path.join(slice_dir, config.META_FILENAME), meta)


# ---------------- 构建入口 ----------------
def build_snapshot(window, out_root=None, dry_run=False, force=False, delta_files=None,
                   keep_base_edges=True):
    """构建一个时间截面快照。返回 stats dict。已有窗口默认拒绝覆盖（--force 才重写）。

    keep_base_edges：force 重建时保留已非空的基图边/技能点文件（Loop 可重入）；
    置 False 则重置为空 schema（--reset-base-edges）。base/ 下 freq/entity_freq 等
    附属产物本模块不写、不受影响。
    """
    kind, start, end, _ = parse_window(window)
    out_root = out_root or config.GRAPH_ROOT
    slice_dir = os.path.join(out_root, window)

    if os.path.exists(slice_dir) and not dry_run and not force:
        raise FileExistsError(f"时间截面已存在: {slice_dir}（用 --force 覆盖）")

    # 基图节点源（缺源 → 记录 missing，不崩溃）
    base_nodes, base_inputs = {}, {}
    for nk, path in config.BASE_NODE_FILES.items():
        data = None
        if os.path.exists(path):
            try:
                data = json.load(open(path, encoding="utf-8"))
            except Exception as e:
                print(f"[snapshot] 基图节点 {nk} 读取失败：{e}")
        base_nodes[nk] = data
        base_inputs[nk] = {"path": path,
                           "date": data.get("date", "") if isinstance(data, dict) else "",
                           "total": _count_nodes(nk, data),
                           "exists": data is not None}

    # 叠层源（三源：papers/news/jd；缺失/为空合法）。显式传入的 delta_files 即完整
    # 规格——缺键=该源缺席（与 participation.merged_view 口径一致；不得兜底读生产
    # 文件，否则调用方只覆盖部分源时会被 classify/DeltaG 的真实产物污染）
    delta_sources, delta_inputs = {}, {}
    d_files = delta_files if delta_files is not None else config.DELTA_FILES
    for src in ("papers", "news", "jd"):
        path = d_files.get(src) or ""
        data = None
        if path and os.path.exists(path):
            try:
                data = json.load(open(path, encoding="utf-8"))
            except Exception as e:
                print(f"[snapshot] 叠层源 {src} 读取失败：{e}")
        delta_sources[src] = data
        delta_inputs[src] = {"path": path, "exists": data is not None}

    papers, news, jd = delta_sources.get("papers"), delta_sources.get("news"), delta_sources.get("jd")
    if papers is None and news is None and jd is None:
        delta_missing = True
        merged_delta = {"new_jobs": [], "new_tasks": [], "new_skills": [], "skillpoints": [], "strengthenings": []}
        merge_stats = {}
    else:
        delta_missing = False
        merged_delta, merge_stats = merge_delta(papers or {}, news or {}, jd or {}, end)

    job_links = _build_job_links(merged_delta, base_nodes)

    stats = {
        "n_base_jobs": base_inputs["jobs"]["total"],
        "n_base_tasks": base_inputs["tasks"]["total"],
        "n_base_skills": base_inputs["skills"]["total"],
        "n_base_skillpoints": 0,
        "n_new_jobs": len(merged_delta.get("new_jobs", [])),
        "n_new_tasks": len(merged_delta.get("new_tasks", [])),
        "n_new_skills": len(merged_delta.get("new_skills", [])),
        "n_delta_skillpoints": len(merged_delta.get("skillpoints", [])),
        "n_strengthenings": len(merged_delta.get("strengthenings", [])),
        "n_job_links": len(job_links),
        # 基图边计数：读磁盘上写入前的状态（force+keep 时即被保留的边；新建时为 0）
        "n_base_edges": _existing_edge_counts(slice_dir),
        **{k: merge_stats.get(k, 0) for k in
           ("n_evidence_kept", "n_evidence_filtered", "n_merged_norm",
            "n_dropped_no_evidence", "n_dropped_low_strength", "n_dateless_evidence",
            "n_graduated_skipped", "n_remapped_skipped")},
    }

    print(f"[snapshot] {window}（{kind}，{start}..{end}）："
          f"基图 岗位{stats['n_base_jobs']}/任务{stats['n_base_tasks']}/技能{stats['n_base_skills']}；"
          f"叠层 新岗位{stats['n_new_jobs']}/新任务{stats['n_new_tasks']}/新技能{stats['n_new_skills']}/"
          f"增强{stats['n_strengthenings']}"
          f"{'（跳过已转正 ' + str(stats['n_graduated_skipped']) + ' 条）' if stats['n_graduated_skipped'] else ''}"
          f"{'（处置退役 ' + str(stats.get('n_remapped_skipped', 0)) + ' 条）' if stats.get('n_remapped_skipped') else ''}"
          f"（delta_missing={delta_missing}）")
    if dry_run:
        return stats

    _write_slice(slice_dir, window, kind, start, end,
                 base_nodes, merged_delta, job_links,
                 base_inputs, delta_inputs, stats, delta_missing,
                 keep_base_edges=keep_base_edges)
    print(f"[snapshot] 已写入：{slice_dir}")
    return stats
