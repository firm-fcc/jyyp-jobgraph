# -*- coding: utf-8 -*-
"""图谱合成：G_eff = G_base ⊕ ΔG（docs/algorithm-design-v2.md §1/§2/§5）。

从窗口快照读取 base 边 + delta 修正，合成 effective 权重，**独立写入**
`data/graph/{窗口}/effective/`（绝不写 base/ 与 delta/；纯函数式，可随时重算覆盖）。

合成规则（参数 settings.yaml → synthesis）：
- gap(E) = max(0, strength_ΔG(E) − E_jd(E))
    strength 取 delta/strengthenings——快照内已按窗口末半衰期重算（§2.3 叠层衰减
    在快照阶段完成，合成不再二次衰减）；E_jd 取 base/entity_freq.json（缺失视为 0）。
    仅处理 taxonomy ∈ {tasks, skills}；jobs 类增强无对应边修正公式，计数跳过。
- J-T / J-S（基图既有边）：Δw = λ_j · gap(右端实体)
- T-S（基图既有边）：Δw = λ_ts · gap(T) · gap(S)
- T-S（合成新边）：双端 gap>0 且无基图边的 (T,S)，按 λ_ts·gap(T)·gap(S) 降序取前 N
- S-SP（基图既有边）：Δw = λ_sp · gap(S)
    设计公式 λ₃·gap(SP)·I(SP∈S) 的降级实现：基图技能点无独立 ΔG 强度、
    叠层 PK- 无父技能挂接，按父技能 gap 修正，注释与文档均已注明。
- 叠层新实体：job_links（PJ-→T/S）作为 G_eff 新边（base_weight=0，delta=link weight）；
  PJ-/PT-/PS-/PK- 全部以节点形式进入 G_eff（new_entities.json 清单）。
- effective = base_weight + delta_weight（§5 加法合成示例）

输出文件：
- effective/{job_task,job_skill,task_skill,skill_skillpoint}.json（schema 同 _edge_file，
  每条边含 origin/base_weight/delta_weight/effective_weight）
- effective/new_entities.json（进入 G_eff 的叠层实体清单 + 关联边数）
- effective/meta.json（λ 参数快照、inputs、stats；保证重算可复现）

纯 stdlib、零 LLM 调用。
"""
import json
import os
from datetime import datetime

import graph_config as config
from graph_snapshot import GraphSnapshot
from snapshot_builder import parse_window, _edge_file


def compute_gaps(snap, entity_freq):
    """叠层增强 → 前瞻 gap。返回 (gaps_tasks, gaps_skills, n_skipped)。

    tasks/skills 分开两张表（code 不冲突但语义不同：T-S 合成新边只取 任务×技能 组合）。
    """
    e_jd = entity_freq or {}
    jd_tasks = e_jd.get("tasks", {}) or {}
    jd_skills = e_jd.get("skills", {}) or {}
    gaps_tasks, gaps_skills, skipped = {}, {}, 0
    for s in snap.strengthenings():
        tax, code = s.get("taxonomy"), s.get("code")
        if tax not in ("tasks", "skills"):
            skipped += 1  # jobs 类增强：公式无对应边修正项
            continue
        strength = s.get("strength", 0) or 0
        jd = (jd_tasks if tax == "tasks" else jd_skills).get(code, 0)
        g = max(0.0, float(strength) - float(jd))
        if g > 0:
            (gaps_tasks if tax == "tasks" else gaps_skills)[code] = g
    return gaps_tasks, gaps_skills, skipped


def _eff_edge(e, origin, delta_w, gap, lam):
    """基图边 → 合成边记录（保留端点字段，追加合成三元组与驱动 gap）。"""
    out = dict(e)
    out["origin"] = origin
    out["base_weight"] = e.get("weight", 0)
    out["delta_weight"] = round(delta_w, 4)
    out["effective_weight"] = round(e.get("weight", 0) + delta_w, 4)
    out["gap"] = round(gap, 4) if gap else 0.0
    out["lambda"] = lam
    return out


def _delta_edge(link, base_names):
    """叠层 job_link → G_eff 新边（base=0）。base_names: {id: name} 兜底补名。"""
    src, dst = link.get("src", ""), link.get("dst", "")
    w = link.get("weight", 0) or 0
    return {"src": src, "src_name": link.get("src_name") or base_names.get(src, ""),
            "dst": dst, "dst_name": link.get("dst_name") or base_names.get(dst, ""),
            "relation": link.get("relation"), "origin": "delta",
            "base_weight": 0.0, "delta_weight": round(float(w), 4),
            "effective_weight": round(float(w), 4), "gap": 0.0, "lambda": None}


def synthesize_edges(snap, entity_freq, params):
    """合成四种 effective 边。返回 (edges, stats)。"""
    lam_j, lam_ts, lam_sp = params["lambda_j"], params["lambda_ts"], params["lambda_sp"]
    gaps_tasks, gaps_skills, n_skipped = compute_gaps(snap, entity_freq)
    edges = {k: [] for k in config.BASE_EDGE_KINDS}
    stats = {"n_gaps": len(gaps_tasks) + len(gaps_skills),
             "n_skipped_job_strengthenings": n_skipped,
             "n_boosted": {}, "n_by_origin": {}, "max_effective": {}}

    # J-T / J-S：基图边按右端实体 gap 修正
    for kind, gaps in (("job_task", gaps_tasks), ("job_skill", gaps_skills)):
        for e in snap.edges(kind):
            gap = gaps.get(e.get("dst"), 0)
            dw = lam_j * gap if gap > 0 else 0
            edges[kind].append(_eff_edge(e, "base", dw, gap, lam_j if gap else None))

    # T-S：基图边按双端 gap 乘积修正 + 合成新边（任务×技能，上限内按强度降序）
    existing_ts = set()
    for e in snap.edges("task_skill"):
        existing_ts.add((e.get("src"), e.get("dst")))
        gt, gs = gaps_tasks.get(e.get("src"), 0), gaps_skills.get(e.get("dst"), 0)
        dw = lam_ts * gt * gs if (gt > 0 and gs > 0) else 0
        edges["task_skill"].append(
            _eff_edge(e, "base", dw, gt * gs if (gt > 0 and gs > 0) else 0, lam_ts if dw else None))
    cands = sorted(((t, s, lam_ts * gaps_tasks[t] * gaps_skills[s])
                    for t in gaps_tasks for s in gaps_skills
                    if (t, s) not in existing_ts),
                   key=lambda x: x[2], reverse=True)[:params["max_new_ts_edges"]]
    node_names = {n["id"]: n.get("name_zh", "") for n in
                  (snap.nodes("base", "tasks") + snap.nodes("base", "skills"))}
    for t, s, dw in cands:
        if dw <= 0:
            continue
        edges["task_skill"].append({"src": t, "src_name": node_names.get(t, ""),
                                    "dst": s, "dst_name": node_names.get(s, ""),
                                    "relation": "task_skill", "origin": "synthesized",
                                    "base_weight": 0.0, "delta_weight": round(dw, 4),
                                    "effective_weight": round(dw, 4),
                                    "gap": round(gaps_tasks[t] * gaps_skills[s], 4),
                                    "lambda": lam_ts})
    stats["n_new_ts_edges"] = len(edges["task_skill"]) - len(existing_ts)

    # S-SP：按父技能 gap 修正（gap(SP) 无独立数据，见模块注释）
    for e in snap.edges("skill_skillpoint"):
        gap = gaps_skills.get(e.get("src"), 0)
        dw = lam_sp * gap if gap > 0 else 0
        edges["skill_skillpoint"].append(_eff_edge(e, "base", dw, gap, lam_sp if gap else None))

    # 叠层 job_links → PJ- 新岗位的 J-T / J-S 边
    idx = snap.node_index()
    for link in snap.job_links():
        rel = link.get("relation")
        if rel not in ("job_task", "job_skill"):
            continue
        if link.get("src") not in idx or link.get("dst") not in idx:
            continue  # 悬空链接快照层已滤，此处双保险
        edges[rel].append(_delta_edge(link, {i: n.get("name_zh", "") for i, n in idx.items()}))

    for kind, arr in edges.items():
        arr.sort(key=lambda x: (x["src"], x["dst"]))
        stats["n_by_origin"][kind] = {
            o: sum(1 for e in arr if e["origin"] == o) for o in ("base", "delta", "synthesized")}
        stats["n_boosted"].setdefault(kind, sum(1 for e in arr if e["delta_weight"] > 0))
        effs = [e["effective_weight"] for e in arr]
        stats["max_effective"][kind] = round(max(effs), 4) if effs else 0.0
    return edges, stats


def _new_entities(snap, edges):
    """叠层实体清单（进入 G_eff 的节点 + 关联边数）。"""
    link_deg = {}
    for arr in edges.values():
        for e in arr:
            link_deg[e["src"]] = link_deg.get(e["src"], 0) + 1
            link_deg[e["dst"]] = link_deg.get(e["dst"], 0) + 1
    items = []
    for kind in ("new_jobs", "new_tasks", "new_skills", "skillpoints"):
        for n in snap.nodes("delta", kind):
            items.append({"id": n["id"], "kind": kind, "name_zh": n.get("name_zh", ""),
                          "strength": n.get("strength", 0), "status": n.get("status", ""),
                          "participates": bool(n.get("participates", False)),
                          "n_links": link_deg.get(n["id"], 0)})
    items.sort(key=lambda x: (x["kind"], x["id"]))
    return {"system_name": "G_eff 叠层新实体清单", "schema_version": "0.1",
            "total": len(items), "items": items}


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def synthesize(window, out_root=None, dry_run=False):
    """合成一个窗口的 G_eff。返回 stats dict（可重复调用，直接覆盖 effective/）。"""
    kind, start, end, _ = parse_window(window)
    out_root = out_root or config.GRAPH_ROOT
    snap = GraphSnapshot.load(window, out_root)
    if not os.path.isdir(snap.path):
        raise FileNotFoundError(f"时间截面不存在: {snap.path}（先 run_snapshot.py build）")
    entity_freq = snap.entity_freq()
    if entity_freq is None:
        print(f"[synth] 警告：base/entity_freq.json 缺失，E_jd 视为 0（gap=strength；"
              f"建议先 run_base_build.py）")
    if not snap.strengthenings():
        # 防假成功：delta 目录在但叠层证据为空时 G_eff≈基图，静默产出会掩盖 ΔG 未运行
        print(f"[synth] 警告：本窗口叠层 strengthenings 为空——G_eff≈基图（无 λ 修正）。"
              f"若非预期，请检查三源 ΔG（papers/news/jd_delta_v2）是否已跑该窗口。")

    params = dict(config.SYN_WEIGHTS)
    edges, stats = synthesize_edges(snap, entity_freq, params)
    stats.update({"window": window, "n_edges": {k: len(v) for k, v in edges.items()},
                  "n_new_entities": len(_new_entities(snap, edges)["items"])})

    n_total = sum(stats["n_edges"].values())
    print(f"[synth] {window}（{kind}，{start}..{end}）：gap 实体 {stats['n_gaps']}，"
          f"合成边 {n_total} 条 {stats['n_edges']}，新 T-S 边 {stats['n_new_ts_edges']}，"
          f"叠层实体 {stats['n_new_entities']}")
    if dry_run:
        return stats

    eff_dir = os.path.join(snap.path, config.EFFECTIVE_SUBDIR)
    for ek, arr in edges.items():
        _write_json(os.path.join(eff_dir, config.EDGE_FILENAMES[ek]),
                    _edge_file(config.EFFECTIVE_EDGE_NAMES[ek], ek, window, arr))
    _write_json(os.path.join(eff_dir, "new_entities.json"), _new_entities(snap, edges))
    meta = {
        "system_name": "图谱合成 G_eff（= G_base ⊕ ΔG）", "schema_version": "0.1",
        "window": window, "period_start": start.isoformat(), "period_end": end.isoformat(),
        "created": datetime.now().isoformat(timespec="seconds"),
        "inputs": {"snapshot": snap.path, "entity_freq_exists": entity_freq is not None,
                   "n_strengthenings": len(snap.strengthenings()),
                   "n_job_links": len(snap.job_links()),
                   "notes": "叠层强度已按窗口末半衰期重算（snapshot 阶段），合成不二次衰减；"
                            "S-SP 修正按父技能 gap（gap(SP) 无独立数据源）"},
        "params": params,
        "params_fingerprint": config.assembly_params_fingerprint(),
        "stats": stats,
    }
    _write_json(os.path.join(eff_dir, config.META_FILENAME), meta)
    print(f"[synth] 已写入：{eff_dir}（base/ 与 delta/ 未做任何修改）")
    return stats


def validate_effective(window, out_root=None):
    """校验合成层：边端点可解析 + total 一致。返回错误清单（空 = 通过）。"""
    snap = GraphSnapshot.load(window, out_root)
    idx = snap.node_index()
    errs = []
    if not os.path.isdir(os.path.join(snap.path, config.EFFECTIVE_SUBDIR)):
        return [f"effective/ 不存在（先 run_synthesis.py build --window {window}）"]
    for ek in config.BASE_EDGE_KINDS:
        path = os.path.join(snap.path, config.EFFECTIVE_SUBDIR, config.EDGE_FILENAMES[ek])
        try:
            fd = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError) as e:
            errs.append(f"effective/{ek}: 读取失败 {e}")
            continue
        if fd.get("total") != len(fd.get("edges", [])):
            errs.append(f"effective/{ek}: total={fd.get('total')} != 实际 {len(fd.get('edges', []))}")
        for e in fd.get("edges", []):
            for side in ("src", "dst"):
                if e.get(side) not in idx:
                    errs.append(f"effective/{ek}: {side} {e.get(side)} 无对应节点")
            if e.get("origin") not in ("base", "delta", "synthesized"):
                errs.append(f"effective/{ek}: 非法 origin {e.get('origin')}")
    return errs
