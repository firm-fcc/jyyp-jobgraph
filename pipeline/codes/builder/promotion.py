# -*- coding: utf-8 -*-
"""转正（promotion）：叠层强信号 + JD 侧确证 → 写入基准体系文件（受控的基图演化通道）。

条件（settings.yaml → overlay，均可在 CLI/参数覆盖）：
- strength ≥ promote_min_strength（任务/技能 0.25；岗位 0.30）
- **JD 侧确证文档数 ≥ promote_min_jd_docs**（任务/技能 2；岗位 3）——
  合并视图中 ev["src"]=="jd" 的不同 doc_id 数（"信号在 JD 侧数据中出现"的硬条件）
- 非 graduated；skillpoints 不转正（无全局体系文件，随父技能进入基图抽取）

写入（**先备份**到 classify/backup/promotion-{timestamp}/，gitignore，可整体回滚）：
- tasks.json：追加 T-{max+1}（code 续号扫描）
- 技能基准（当前 skills0821.json）：追加「T-DG / F-DG 前瞻转正」组（detail + 简明体系组 skills + total；
  hard/hybrid → T-DG，soft → F-DG）
- jobs 基准（jobs_v2.json，2026-08-22 起；v1 结构兼容见 _write_jobs）：追加 GJ-{NNN}
  （detail + meta.n_jobs；funtypes=[name_zh]：日后 JD funtype/同名即可命中映射获得基图边）
- 三源 ΔG 文件：命中条目 status="graduated" + promoted_to + promoted_date
  （下窗口快照 merge 跳过 graduated → 叠层视图不再重复呈现；证据历史保留在源文件）

时序：缺省作用于窗口 W+1（W 快照保持转正前叠层视图；W+1 基图标签空间天然含新条目
→ 边自然产生）。**回溯转正**（特例，2026-08-30 首例：确证通道修复后证据过线的实体在
证据窗 W 即转正）：graduated_window=W → W 及之后快照跳过 graduated（基图侧经向量回注
新 code + Stage D --force 重建在该窗含新实体与连边），W 之前窗口仍渲染叠层；向量回注
= 任务/技能把确证 JD 的 task_vec/skill_vec 补新 code、岗位把标题确证 JD 的 job_code
改派新 GJ（原 code 存 job_code_promoted_from 留审计）。
"""
import json
import os
import re
import shutil
from datetime import date, datetime

import config
from delta_store import norm
from participation import merged_view

_ARRAY_LABEL = {"new_tasks": "任务", "new_skills": "技能", "new_jobs": "岗位"}
_VERSION_BUMP = 0.1  # 每次转正版本号 +0.1（数值型 version 字段）


# ---------------- 候选评估 ----------------
def _jd_doc_count(entry):
    """JD 侧**确证**文档数：只认确证通道判定"要求掌握"的证据（grade=="require"）。

    发现通道的扫描证据（grade=="scan"，"该词在 JD 中出现过"）只贡献强度、不充当确证
    ——否则 v2 当窗发现的实体"出生即转正"，市场确证门槛形同虚设。
    """
    return sum(1 for ev in (entry.get("evidence") or {}).values()
               if isinstance(ev, dict) and ev.get("src") == "jd"
               and ev.get("grade") == "require")


def evaluate_candidates(delta_files=None, now=None, min_strength=None, min_jd_docs=None,
                        min_strength_jobs=None, min_jd_docs_jobs=None):
    """评估转正候选。返回 (candidates, merged)。

    candidates: [{array, id, name_zh, name_en, definition, strength, jd_docs, is_job}]
    """
    ms = config.OVERLAY_PROMOTE_MIN_STRENGTH if min_strength is None else min_strength
    md = config.OVERLAY_PROMOTE_MIN_JD_DOCS if min_jd_docs is None else min_jd_docs
    msj = config.OVERLAY_PROMOTE_MIN_STRENGTH_JOBS if min_strength_jobs is None else min_strength_jobs
    mdj = config.OVERLAY_PROMOTE_MIN_JD_DOCS_JOBS if min_jd_docs_jobs is None else min_jd_docs_jobs
    merged, _ = merged_view(now=now, delta_files=delta_files)
    cands = []
    for arr in ("new_tasks", "new_skills", "new_jobs"):
        is_job = arr == "new_jobs"
        for it in merged.get(arr, []):
            if it.get("status") == "graduated":
                continue
            jd_docs = _jd_doc_count(it)
            if it.get("strength", 0) >= (msj if is_job else ms) and jd_docs >= (mdj if is_job else md):
                cands.append({"array": arr, "id": it.get("id", ""),
                              "name_zh": it.get("name_zh", ""), "name_en": it.get("name_en", ""),
                              "definition": it.get("definition") or it.get("description") or "",
                              "strength": it.get("strength", 0), "jd_docs": jd_docs,
                              "skill_type": it.get("skill_type", ""), "is_job": is_job})
    cands.sort(key=lambda c: (c["array"], c["name_zh"]))
    return cands, merged


# ---------------- code 续号 ----------------
def _next_month(ym):
    """YYYY-MM → 次月（常规转正的生效窗 = as-of 窗的下一月）。"""
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"


def _next_seq(prefix, codes, width=2):
    """prefix 形如 'T-' / 'T-DG-' / 'GJ-'；扫描已有 code 取最大序号 +1。"""
    pat = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    nums = [int(m.group(1)) for c in codes if (m := pat.match(c or ""))]
    return f"{prefix}{(max(nums) + 1) if nums else 1:0{width}d}"


# ---------------- 基准体系写入 ----------------
def _bump_version(data):
    v = str(data.get("version", ""))
    try:
        data["version"] = f"{round(float(v) + _VERSION_BUMP, 2):g}"
    except ValueError:
        data["version"] = (v + "+p") if v else "0.1"


def _write_tasks(path, entries, today):
    data = json.load(open(path, encoding="utf-8"))
    codes = [t.get("code", "") for t in data.get("tasks", [])]
    out = []
    for e in entries:
        code = _next_seq("T-", codes, 2)
        codes.append(code)
        data["tasks"].append({"code": code, "name_zh": e["name_zh"],
                              "name_en": e.get("name_en", ""),
                              "description": e.get("definition", "")})
        out.append(code)
    data["date"] = today.isoformat()
    _bump_version(data)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return out


def _write_skills(path, entries, today):
    data = json.load(open(path, encoding="utf-8"))
    detail = data.setdefault("detail", {})
    out = []
    for e in entries:
        group = "F-DG" if e.get("skill_type") == "soft" else "T-DG"
        code = _next_seq(f"{group}-", list(detail.keys()), 2)
        detail[code] = {"code": code, "name_zh": e["name_zh"], "name_en": e.get("name_en", ""),
                        "definition": e.get("definition", ""), "skill_type": e.get("skill_type") or "hard"}
        side = "F" if group == "F-DG" else "T"
        groups = data["简明体系"][side].setdefault("groups", {})
        if group not in groups:  # 组惰性创建：前瞻转正组
            groups[group] = {"name": "前瞻转正（ΔG 涌现）", "name_en": "Emergent (ΔG Promoted)",
                             "skills": []}
        if e["name_zh"] not in groups[group]["skills"]:
            groups[group]["skills"].append(e["name_zh"])
        out.append(code)
    data["total"] = len(detail)
    data["date"] = today.isoformat()
    _bump_version(data)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return out


def _write_jobs(path, entries, today):
    """写入岗位基准。目标结构 jobs_v2.json（categories 纯组织维度不动，GJ- 追加进
    detail，meta.n_jobs 同步）；检测到 v1 存档结构（简明体系树）时走兼容路径。
    category 缺省空串：转正收口后由 job_categorize.py（旁路 LLM 归纳+人工确认）
    补齐；候选条目自带 category 时直接写入（校验交由归纳环节兜底）。"""
    data = json.load(open(path, encoding="utf-8"))
    detail = data.setdefault("detail", {})
    out = []
    for e in entries:
        code = _next_seq("GJ-", list(detail.keys()), 3)
        if "简明体系" in data:      # v1 存档结构（jobs0806.json）
            detail[code] = {"code": code, "name_zh": e["name_zh"], "name_en": e.get("name_en", ""),
                            "parent": None, "level": 1, "count": 0,
                            "funtypes": [e["name_zh"]], "aliases": []}
            data["简明体系"]["信息技术岗位"].setdefault("children", []).append(
                {"code": code, "name": e["name_zh"], "name_en": e.get("name_en", ""),
                 "count": 0, "children": [], "funtypes": [e["name_zh"]]})
            data["total"] = len(detail)
            data.setdefault("meta", {})["it_related_nodes"] = len(detail)
        else:                        # v2 当前基准（jobs_v2.json）
            detail[code] = {"code": code, "category": e.get("category", ""), "name_zh": e["name_zh"],
                            "name_en": e.get("name_en", ""),
                            "definition": e.get("definition", ""),
                            "keywords": [], "boundary": "",
                            "funtypes": [e["name_zh"]],
                            "graduated": today.isoformat()}
            data.setdefault("meta", {})["n_jobs"] = len(detail)
        out.append(code)
    data["date"] = today.isoformat()
    _bump_version(data)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return out


# ---------------- ΔG 源标记 ----------------
def _mark_graduated(delta_files, code_by_norm, today, graduated_window=None):
    """三源 ΔG 文件中命中条目（norm 名）标记 graduated。返回 {(src, id): promoted_to}。

    graduated_window（YYYY-MM，缺省 today 的月份）：转正生效窗——该窗及之后快照的
    叠层视图跳过 graduated 条目（基图侧自该窗含新实体，回溯转正时二者对齐）；
    生效窗之前的窗口仍渲染为叠层（彼时实体尚未入基图，历史视图不变）。
    """
    marked = {}
    for src in ("papers", "news", "jd"):
        path = (delta_files or config.DELTA_FILES).get(src)
        if not path or not os.path.exists(path):
            continue
        try:
            data = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        changed = False
        for arr in ("new_jobs", "new_tasks", "new_skills", "skillpoints"):
            for it in data.get(arr, []):
                key = norm(it.get("name_zh", ""))
                if key in code_by_norm and it.get("status") != "graduated":
                    it["status"] = "graduated"
                    it["promoted_to"] = code_by_norm[key]
                    it["promoted_date"] = today.isoformat()
                    it["graduated_window"] = graduated_window or f"{today:%Y-%m}"
                    marked[(src, it.get("id", ""))] = code_by_norm[key]
                    changed = True
        if changed:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
    return marked


def _backup(paths, backup_root=None):
    """写入前备份基准文件到 classify/backup/promotion-{timestamp}/。返回备份目录。"""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(backup_root or config.OVERLAY_BACKUP_DIR, f"promotion-{ts}")
    os.makedirs(bdir, exist_ok=True)
    for p in paths:
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(bdir, os.path.basename(p)))
    return bdir


def _append_log(report, log_path=None):
    log_path = log_path or config.OVERLAY_PROMOTION_LOG
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(report + "\n")
    return log_path


# ---------------- 主入口 ----------------
def run_promotion(delta_files=None, tax_paths=None, dry_run=False,
                  min_strength=None, min_jd_docs=None,
                  min_strength_jobs=None, min_jd_docs_jobs=None, now=None,
                  backup_root=None, graduated_window=None, log_path=None):
    """执行一次转正收口。返回 report dict。

    tax_paths 可注入基准文件路径、backup_root 可注入备份目录、log_path 可注入
    转正日志路径（测试用，避免 fixture 记录混入正式 promotion_log.md）；
    缺省 config 的三体系基准与 classify/backup/。
    """
    today = now or date.today()
    tax = tax_paths or {}
    t_tasks = tax.get("tasks") or config.TASK_TAXONOMY
    t_skills = tax.get("skills") or config.SKILL_TAXONOMY
    t_jobs = tax.get("jobs") or config.JOB_TAXONOMY

    cands, _ = evaluate_candidates(delta_files, today, min_strength, min_jd_docs,
                                   min_strength_jobs, min_jd_docs_jobs)
    print(f"[promote] 转正候选 {len(cands)} 个（门槛：任务/技能 strength≥"
          f"{config.OVERLAY_PROMOTE_MIN_STRENGTH}+jd≥{config.OVERLAY_PROMOTE_MIN_JD_DOCS}，"
          f"岗位 strength≥{config.OVERLAY_PROMOTE_MIN_STRENGTH_JOBS}"
          f"+jd≥{config.OVERLAY_PROMOTE_MIN_JD_DOCS_JOBS}）")
    for c in cands:
        print(f"  - [{_ARRAY_LABEL[c['array']]}] {c['name_zh']}（strength={c['strength']}，"
              f"jd_docs={c['jd_docs']}）")
    if not cands or dry_run:
        return {"n_candidates": len(cands), "dry_run": dry_run,
                "promoted": [], "candidates": cands}

    # 分组写入（任务/技能/岗位各走各的 code 空间）
    by_arr = {arr: [c for c in cands if c["array"] == arr]
              for arr in ("new_tasks", "new_skills", "new_jobs")}
    code_by_norm = {}
    bdir = _backup([p for p in (t_tasks, t_skills, t_jobs) if os.path.exists(p)], backup_root)
    print(f"[promote] 已备份基准文件 → {bdir}")

    promoted = []
    if by_arr["new_tasks"]:
        codes = _write_tasks(t_tasks, by_arr["new_tasks"], today)
        for c, code in zip(by_arr["new_tasks"], codes):
            code_by_norm[norm(c["name_zh"])] = code
            promoted.append({**c, "new_code": code})
    if by_arr["new_skills"]:
        codes = _write_skills(t_skills, by_arr["new_skills"], today)
        for c, code in zip(by_arr["new_skills"], codes):
            code_by_norm[norm(c["name_zh"])] = code
            promoted.append({**c, "new_code": code})
    if by_arr["new_jobs"]:
        codes = _write_jobs(t_jobs, by_arr["new_jobs"], today)
        for c, code in zip(by_arr["new_jobs"], codes):
            code_by_norm[norm(c["name_zh"])] = code
            promoted.append({**c, "new_code": code})

    # 常规转正 graduated_window=W+1（as-of 次月，缺省）；回溯特例显式传 W
    gw = graduated_window or _next_month(f"{today:%Y-%m}")
    marked = _mark_graduated(delta_files, code_by_norm, today, graduated_window=gw)

    lines = [f"## {datetime.now().isoformat(timespec='seconds')} 转正收口",
             f"- 备份：{bdir}",
             f"- 转正 {len(promoted)} 条："]
    for p in promoted:
        lines.append(f"  - {_ARRAY_LABEL[p['array']]} {p['name_zh']} → {p['new_code']}"
                     f"（strength={p['strength']}，jd_docs={p['jd_docs']}）")
    lines.append(f"- ΔG 源标记 graduated：{len(marked)} 条")
    log = _append_log("\n".join(lines), log_path=log_path)
    print(f"[promote] 完成：{len(promoted)} 条转正，ΔG 标记 {len(marked)} 条；日志 {log}")
    return {"n_candidates": len(cands), "dry_run": False, "promoted": promoted,
            "n_marked": len(marked), "backup_dir": bdir, "log": log}
