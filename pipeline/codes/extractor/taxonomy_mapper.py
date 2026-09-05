# -*- coding: utf-8 -*-
"""体系映射（Stage B）：候选信号 × 基础体系（tasks/skills/jobs）+ 增量层已有条目 → 决策。

- 先做**程序化精确匹配预过滤**（norm 名称命中基础标签 → 直接 map_to，省 LLM 调用）
- 剩余候选交给 LLM 映射监督（语义接近=已覆盖；下位概念=已覆盖；工具/岗位名当任务/技能=reject；
  与增量层已有条目语义等价=merge_into；仅完全无法映射才 keep）
- is_new 由程序推导（keep 且无 map_to 且无 merge_into），不由 LLM 声明
- LLM 失败/漏判 → 保守保留（keep-new），信号不丢
"""
import json
import re

import config
from paper_prompts import PROMPT_MAP
from llm import ResourceExhaustedError, call_llm

VALID_FINAL_KINDS = {"new_job", "new_task", "new_skill", "implied_task", "capability_gap", "skillpoint"}
VALID_TAXONOMIES = {"tasks", "skills", "jobs"}


def norm(name):
    """名称归一化：去空白/标点/大小写，用于程序化匹配。"""
    return re.sub(r"[\s、，,.;·\-_（）()]", "", name or "").lower()


def load_base_labels():
    """加载基础体系标签（只读）。返回 {tasks, skills, jobs}，各为 [{code, name_zh, name_en, skill_type?}]。"""
    labels = {"tasks": [], "skills": [], "jobs": []}
    try:
        data = json.load(open(config.TASK_TAXONOMY, encoding="utf-8"))
        labels["tasks"] = [{"code": t["code"], "name_zh": t.get("name_zh", ""), "name_en": t.get("name_en", "")}
                           for t in data.get("tasks", [])]
    except Exception as e:
        print(f"[map] 加载任务体系失败: {e}")
    try:
        data = json.load(open(config.SKILL_TAXONOMY, encoding="utf-8"))
        labels["skills"] = [{"code": d.get("code", c), "name_zh": d.get("name_zh", ""),
                             "name_en": d.get("name_en", ""), "skill_type": d.get("skill_type", "")}
                            for c, d in data.get("detail", {}).items()]
    except Exception as e:
        print(f"[map] 加载技能体系失败: {e}")
    try:
        data = json.load(open(config.JOB_TAXONOMY, encoding="utf-8"))
        labels["jobs"] = [{"code": d.get("code", c), "name_zh": d.get("name_zh", ""),
                           "name_en": d.get("name_en", "")}
                          for c, d in data.get("detail", {}).items()]
    except Exception as e:
        print(f"[map] 加载岗位体系失败: {e}")
    return labels


class Decision:
    """单条候选的映射裁决。"""

    def __init__(self, index, final_kind, name_zh, name_en, status, map_to,
                 merge_into, reject_reason, reason):
        self.index = index
        self.final_kind = final_kind
        self.name_zh = name_zh
        self.name_en = name_en
        self.status = status          # keep / reject
        self.map_to = map_to          # {"taxonomy","code"} 或 None
        self.merge_into = merge_into  # 增量层条目 id 或 None
        self.reject_reason = reject_reason
        self.reason = reason

    @property
    def is_new(self):
        """是否新条目（程序推导）。"""
        return self.status == "keep" and not self.map_to and not self.merge_into


def _build_norm_lookup(labels):
    """norm(名称) → code，跨 name_zh / name_en。"""
    lookup = {"tasks": {}, "skills": {}, "jobs": {}}
    for tax, items in labels.items():
        for it in items:
            for name in (it.get("name_zh"), it.get("name_en")):
                if name:
                    lookup[tax].setdefault(norm(name), it["code"])
    return lookup


def _exact_match_prefilter(cands, labels):
    """程序化精确匹配：norm 名称命中基础标签 → 直接 map_to。返回 (decisions, remaining)。"""
    lookup = _build_norm_lookup(labels)
    decisions, remaining = [], []
    for cand in cands:
        target = None
        for tax in ("tasks", "skills", "jobs"):
            code = lookup[tax].get(norm(cand.name_zh)) or lookup[tax].get(norm(cand.name_en))
            if code:
                target = {"taxonomy": tax, "code": code}
                break
        if target:
            decisions.append(Decision(cand.index, cand.kind, cand.name_zh, cand.name_en, "keep",
                                      target, None, "",
                                      f"名称精确命中基础体系 {target['taxonomy']}:{target['code']}"))
        else:
            remaining.append(cand)
    return decisions, remaining


def _validate_decision(d, remaining):
    """校验单条 LLM 映射裁决；非法返回 None。"""
    if not isinstance(d, dict):
        return None
    try:
        idx = int(d.get("index", -1))
    except (TypeError, ValueError):
        return None
    cand = next((c for c in remaining if c.index == idx), None)
    if cand is None:
        return None
    status = str(d.get("status", "keep")).strip().lower()
    if status not in ("keep", "reject"):
        status = "keep"
    final_kind = str(d.get("final_kind", "")).strip().lower()
    if final_kind not in VALID_FINAL_KINDS:
        final_kind = cand.kind
    name_zh = str(d.get("name_zh") or cand.name_zh).strip() or cand.name_zh
    name_en = str(d.get("name_en") or cand.name_en).strip() or cand.name_en

    map_to = d.get("map_to")
    if not isinstance(map_to, dict):
        map_to = None
    else:
        tax = str(map_to.get("taxonomy", "")).strip().lower()
        code = str(map_to.get("code", "")).strip()
        if tax not in VALID_TAXONOMIES or not code:
            map_to = None
        else:
            map_to = {"taxonomy": tax, "code": code}

    merge_into = str(d.get("merge_into") or "").strip() or None
    if map_to and merge_into:          # 二者互斥，优先 map_to
        merge_into = None
    reject_reason = str(d.get("reject_reason") or "").strip()
    if status == "reject" and not reject_reason:
        reject_reason = "粒度不符或与现有体系重复（未说明）"
    reason = str(d.get("reason") or "").strip()
    return Decision(idx, final_kind, name_zh, name_en, status, map_to,
                    merge_into, reject_reason, reason)


# ---------------- keep 终审守门（第二道独立 LLM） ----------------
_RECHECK_PROMPT = """
任务/技能边界与命名纪律（各通道统一，2026-08-30）：
- 任务 = 承担的工作职责/活动（动词性表述，"做什么"，如 多模态数据融合建模）；
- 技能 = 可学习掌握的能力/方法/知识（名词性表述，"会什么"，如 提示工程）；
- 命名一律从**从业者视角**（应聘者/员工做什么、会什么），不从机器/系统视角——
  人可"做"仿真数据增强，但不能"做"机器人技能学习（那是机器在学，人做的是机器人技能示教）；
- 同一名称不得同时判为任务与技能（跨类重名禁止；二者必择其一）；
- 命名取精要（一般 ≤10 字）：去"与/及/和"并列连接与"能力/技术"类冗缀，取单一核心概念，
  便于与市场文本（JD 措辞）对齐；不造冗长学名。

你是岗位能力图谱体系边界的守门员。以下候选已初判为"新任务/新技能"（keep），但初判在
粒度边界上不稳定——同一候选不同批次可能判出相反结果。请做终审，基调**宁严勿宽、宁映射勿新增**：
新条目会进入叠层并可能最终转正写入基准体系，误加的代价远高于一次映射或合并。

对每条候选按优先级判定：
- **map**：含义可被基础体系某任务/技能**涵盖**，涵盖尺度从宽——上下位包含（如"数据准备优化"⊂
  数据分析/数据开发）、领域/场景变体（"医疗X"⊂"X"）、近义措辞、单一工程流水线的环节名 ⊂ 对应
  通用任务。→ taxonomy+code（必须真实存在）。
- **merge**：与增量层已有条目或**同批其他候选**属同一职责/能力域——同一数据/技术/系统域的兄弟
  环节（采集/标注/构建/预处理，容灾/切换/监控/热备）是一个任务的不同侧面：多篇/多条支持同一
  信号 = 更强信号而非多条，一篇文章的工程流水线各环节不是多个任务。target 填增量层条目 id；
  目标是同批候选时填其名称并在 cluster_name 给该簇统一采用的简洁规范名。
- **keep**：基础体系与增量层确实都无法涵盖、且确属**可跨雇主复用的抽象职责/能力类别**（非单一
  产品的工程环节、非一次性项目措辞、非具体数据集/硬件操作）→ nearest（最接近条目 code）+
  why_not（为何它也不涵盖）。

拿不准一律 map/merge，不给体系加没把握的新条目。

基础体系：
【任务】{task_labels}
【技能】{skill_labels}

增量层已有条目（merge 目标）：
{delta_labels}

候选（含初判依据；同批候选互为合并目标）：
{candidates}

仅输出 JSON 数组：[{{"name": 同输入, "action": "map"|"merge"|"keep", "taxonomy": "tasks"|"skills",
"code": str, "target": str, "cluster_name": str, "nearest": str, "why_not": str}}]"""


def _post_rename_prefilter(decisions, labels):
    """改名后确定性复检（2026-09-01，用户裁定：根治守门归簇改名绕过同名预检）。

    recheck 的"同批归簇改名"可能把簇名起成基图条目名（实证：AI幻觉识别与校验=T-AI-13），
    或同批出现跨类同名（跨 kind 重名根源之一）。本复检在 recheck 之后运行：
    - 最终名 norm 命中基图任务/技能 → 强制 map 基线（不新建）；
    - 同批跨类同名 → 后例 final_kind 对齐先例（delta.apply 按名合并为单条目，证据并集）。
    """
    lookup = _build_norm_lookup(labels)
    seen = {}
    n_map = n_align = 0
    for d in decisions:
        if d.status == "reject" or d.map_to:
            continue
        nm = norm(d.name_zh) or norm(d.name_en)
        if not nm:
            continue
        hit = None
        for tax in ("tasks", "skills", "jobs"):
            code = lookup[tax].get(nm)
            if code:
                hit = {"taxonomy": tax, "code": code}
                break
        if hit:
            d.map_to = hit
            d.merge_into = None
            d.reason = f"改名后同名复检：最终名与基线 {hit['taxonomy']}:{hit['code']} 同名 → 强制映射"
            n_map += 1
            continue
        if nm in seen and d.final_kind not in ("skillpoint",):
            first = seen[nm]
            if first.final_kind != d.final_kind and d.final_kind != "skillpoint"                     and first.final_kind != "skillpoint":
                d.final_kind = first.final_kind
                d.reason = f"同批跨类同名复检：{d.name_zh} 对齐先例侧 {first.final_kind}（单条目合并）"
                n_align += 1
        else:
            seen[nm] = d
    return n_map, n_align


def recheck_keeps(decisions, labels, delta_items, api_key=None, max_tokens=None, logger=None):
    """keep 判定（new_task/new_skill）的守门终审：第二道独立 LLM，就地修订 decisions。

    单遍映射在粒度边界上不稳定（实测同一候选两批次判出相反结果），终审以不同视角
    提示词复核：被涵盖 → map_to；兄弟环节 → merge_into / 同批改名归簇（store 按名
    去重自然合并）；确无涵盖 → 保留并在 reason 记 nearest/why_not 审计；终审结论
    无效（幻觉 code 等）→ 拒绝（宁缺毋滥）。整体 LLM 失败不推翻初判（信号不丢）。
    """
    keeps = [d for d in decisions
             if d.status == "keep" and not d.map_to and not d.merge_into
             and d.final_kind in ("new_task", "new_skill")]
    if not keeps:
        return
    task_labels = "\n".join(f"{l['code']}:{l['name_zh']}" for l in labels["tasks"]) or "（无）"
    skill_labels = "\n".join(f"{l['code']}:{l['name_zh']}" for l in labels["skills"]) or "（无）"
    delta_labels = "\n".join(f"{it['id']}:{it['name_zh']} ({it.get('array', '')})"
                             for it in delta_items) or "（无）"
    cand_json = json.dumps([{"name": d.name_zh, "kind": d.final_kind,
                             "reason": (d.reason or "")[:120]} for d in keeps],
                           ensure_ascii=False, indent=1)
    prompt = (_RECHECK_PROMPT
              .replace("{task_labels}", task_labels)
              .replace("{skill_labels}", skill_labels)
              .replace("{delta_labels}", delta_labels)
              .replace("{candidates}", cand_json))
    valid_code = {l["code"] for lst in labels.values() for l in lst}
    valid_ids = {it["id"] for it in delta_items}
    try:
        raw = call_llm(prompt, parse_json=True, max_tokens=max_tokens, api_key=api_key)
    except ResourceExhaustedError:
        raise  # 402 熔断：资源性故障中止运行，不降级保信号（2026-09-02 用户裁定）
    except Exception as e:
        if logger:
            logger.error("recheck", f"守门终审 LLM 失败：{e}（本批 keep 按初判保留）")
        return
    rows = raw if isinstance(raw, list) else []
    by_name = {norm(r.get("name")): r for r in rows if isinstance(r, dict) and r.get("name")}
    for d in keeps:
        r = by_name.get(norm(d.name_zh))
        if r is None:
            continue                                   # 终审漏判 → 维持初判
        act = r.get("action")
        if act == "map" and r.get("code") in valid_code:
            d.map_to = {"taxonomy": r.get("taxonomy") or "tasks", "code": r["code"]}
            d.reason = f"守门终审 map→{r['code']}"
        elif act == "merge" and (r.get("target") in valid_ids or r.get("cluster_name")):
            tgt = r.get("target") or ""
            if tgt in valid_ids:
                d.merge_into = tgt
                d.reason = f"守门终审 merge→{tgt}"
            else:                                      # 同批归簇：统一改簇名，store 按名去重合并
                cn = str(r.get("cluster_name") or "").strip()[:14]
                if not cn:
                    continue
                d.name_zh = cn
                tgt_norm = norm(tgt)
                if tgt_norm and tgt_norm != norm(cn):
                    for d2 in decisions:
                        if d2 is not d and norm(d2.name_zh) == tgt_norm:
                            d2.name_zh = cn
                d.reason = f"守门终审 同批归簇→{cn}"
        elif act == "keep":
            d.reason = f"守门通过（nearest={r.get('nearest', '')}）：{(r.get('why_not') or '')[:80]}"
        else:
            d.status = "reject"
            d.reject_reason = f"守门终审无效（action={act}），宁缺毋滥"


# ---------------- 岗位守门（普适性 + 基线同义/子岗，2026-09-02） ----------------
_JOB_GATE_PROMPT = """
你是**岗位体系守门员**。以下候选已初判为"新岗位"（keep），但论文/新闻侧岗位命名存在两类
失守：与基线岗位同义/近义（"数据标注员"≈"数据标注师"、"GIS开发者"≈"GIS工程师"）、或为
基线岗位的子岗/变体（"GIS数据库管理员"⊂"GIS工程师"+"DBA"）。请逐条终审，基调
**宁严勿宽、宁映射勿新增**：新岗位会进入叠层并可能转正写入基准岗位体系，重复岗位的代价
远高于一次映射。

对每条候选按优先级判定：
- **map**：与岗位体系某条目为**同一岗位**——同义/近义措辞（标注员≈标注师、开发者≈工程师、
  研究员≈研究工程师）、子岗/分工变体（"X数据库管理员"⊂"GIS工程师"或"DBA"）、行业/场景
  前缀变体（"医疗X"⊂"X"）→ taxonomy=jobs + code（必须真实存在）。
- **merge**：与叠层已有岗位条目同一岗位（跨源同一信号）→ target 填其 id。
- **reject**：**非普适市场岗位**——只存在于论文/新闻研究语境的角色（机构/场景/人群限定：
  某国立法机构、某校园中心、某系统用户等）、名称 >10 字或过于具体无法作为招聘头衔、
  或实为任务/技能/工具的误判 → reject_reason 一句话。
- **keep**：确属岗位体系与叠层均无对应、且为**普适市场岗位头衔**（脱离原文语境可直接
  出现在 JD 招聘标题中）→ nearest（最接近基线岗位 code）+ why_not。

拿不准一律 map/reject，不给体系加没把握的新岗位。

岗位体系（138 条）：
{job_labels}

叠层已有岗位条目（merge 目标）：
{delta_job_labels}

候选（含初判依据）：
{candidates}

仅输出 JSON 数组：[{{"name": 同输入, "action": "map"|"merge"|"reject"|"keep",
"taxonomy": "jobs", "code": str, "target": str, "nearest": str, "why_not": str,
"reject_reason": str}}]"""


# ---------------- 字面近邻复核（第三道门，2026-09-02） ----------------
_NEAR_RECHECK_PROMPT = """
你是体系边界的复核员。下列候选已被判定为"新条目"，但其名称与基础体系某条目**字面高度相似**
（同义/近义/上下位/领域变体的高危形态——混批判定不稳定，这里是**小批逐对**复核）。基调
**宁严勿宽、宁映射勿新增**：

- **same**：候选与对照条目为同一任务/技能/岗位——同义/近义措辞（"纠偏"≈"校验"、"标注员"≈
  "标注师"）、上下位/粒度变体（"文档级X"⊂"X"）、领域/场景前缀变体（"医疗X"/"KG-X"⊂"X"）、
  仅多"能力/技术"类冗缀 → 视为已覆盖；
- **diff**：字面相似但内涵确实不同（"生成式AI算法工程师"与"算法工程师"是不同市场头衔、
  "图像恢复"与"图像识别"是不同职责、"模型压缩"与"模型部署"不同环节）→ 确为新条目。

对照对（候选 | 最相似基线条目）：
{pairs}

仅输出 JSON 数组：[{{"pair": 候选名, "action": "same"|"diff", "reason": 一句话}}]
"""


def _lcs_len(a, b):
    """最长公共子串长度（连续）。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def _near_names(name, pool, top=2):
    """返回 pool 中与 name 字面近邻的 [(code, name_zh, score)]：
    字符集重合率 ≥0.5 或最长公共子串 ≥4 字。"""
    chars = set(name)
    out = []
    for code, nm in pool:
        ov = len(chars & set(nm)) / max(len(chars), len(set(nm))) if nm else 0.0
        lcs = _lcs_len(name, nm)
        if ov >= 0.5 or lcs >= 4:
            out.append((code, nm, round(max(ov, lcs / 8), 3)))
    out.sort(key=lambda t: -t[2])
    return out[:top]


def near_recheck(decisions, labels, api_key=None, max_tokens=None, logger=None):
    """字面近邻复核（第三道门）：keep 条目名称与基线高度相似 → 小批逐对 LLM 终裁。

    背景（2026-09-02 用户裁定"增强监督"）：LLM 守门在混批下对近同义漏放（实证：
    AI幻觉识别与纠偏能力 vs 基线 AI幻觉识别与校验、大模型输出质量评估 vs AI输出质量评估）。
    确定性相似度预筛（字符重合率/公共子串）零成本圈定高危对，小批逐对裁决稳定
    （批尺寸敏感实证：≤7 行稳定、大混批漂移）。same → 强制 map 基线；diff → 维持。
    LLM 失败不推翻（信号不丢，宁可下窗再筛）。
    """
    pool = [("tasks", l["code"], l["name_zh"]) for l in labels["tasks"]] + \
           [("skills", l["code"], l["name_zh"]) for l in labels["skills"]] + \
           [("jobs", l["code"], l["name_zh"]) for l in labels["jobs"]]
    pairs = []          # [(decision, [(tax, code, name), ...])]
    for d in decisions:
        if d.status != "keep" or d.map_to or d.merge_into or d.final_kind == "skillpoint":
            continue
        nm = d.name_zh or d.name_en
        if not nm:
            continue
        near = _near_names(nm, [(c, n) for _t, c, n in pool if n and n != nm])
        if near:
            code2tax = {c: t for t, c, _n in pool}
            pairs.append((d, [(code2tax.get(c, "tasks"), c, n) for c, n, _s in near]))
    if not pairs:
        return
    pair_lines = []
    for d, near in pairs:
        for tax, code, nm in near:
            pair_lines.append(f"{d.name_zh}（{d.final_kind}） | {tax}:{code} {nm}")
    prompt = (_NEAR_RECHECK_PROMPT
              .replace("{pairs}", "\n".join(pair_lines)))
    code_map = {(tax, code): nm for tax, code, nm in pool}
    try:
        raw = call_llm(prompt, parse_json=True, max_tokens=max_tokens, api_key=api_key)
    except ResourceExhaustedError:
        raise  # 402 熔断：资源性故障中止运行，不降级保信号（2026-09-02 用户裁定）
    except Exception as e:
        if logger:
            logger.error("near_recheck", f"近邻复核 LLM 失败：{e}（本批维持初判）")
        return
    rows = raw if isinstance(raw, list) else []
    verdict = {}
    for r in rows:
        if isinstance(r, dict) and r.get("pair"):
            verdict.setdefault(norm(r["pair"]), []).append(r)
    n_same = 0
    for d, near in pairs:
        vs = verdict.get(norm(d.name_zh))
        if not vs:
            continue
        same_rows = [v for v in vs if str(v.get("action")).lower() == "same"]
        if len(same_rows) == len(near) and near:      # 所有对照对均判 same 才强制映射
            tax, code, _ = near[0]
            d.map_to = {"taxonomy": tax, "code": code}
            d.merge_into = None
            d.reason = f"近邻复核 same→{tax}:{code}（{same_rows[0].get('reason', '')[:60]}）"
            n_same += 1
        elif same_rows:
            d.reason = f"近邻复核部分同（{len(same_rows)}/{len(near)}），保守维持新条目"
    if n_same:
        print(f"[map] 近邻复核：{n_same} 条字面近重复→强制映射基线", flush=True)


def recheck_job_keeps(decisions, labels, delta_items, api_key=None, max_tokens=None, logger=None):
    """new_job 类 keep 的岗位守门终审（第二道独立 LLM + 确定性基线同名检查）。

    背景（2026-09-02 用户裁定）：论文侧岗位无第二道门——近同义（数据标注员 vs 基线
    AID-18 数据标注师）与场景限定角色（国会AI事务专员等）直接出生。本守门：
    - 确定性：最终名 norm 精确命中基线岗位（含映射 LLM 改名后的碰撞）→ 强制 map；
    - LLM 终审：同义/子岗 → map 基线；叠层同岗 → merge；非普适场景角色/过长过具体 →
      reject；确属普适新岗位 → keep（记 nearest/why_not 审计）。无效结论（幻觉 code）
      → 拒绝（宁缺毋滥）。整体 LLM 失败不推翻初判（信号不丢）。
    """
    keeps = [d for d in decisions
             if d.status == "keep" and not d.map_to and not d.merge_into
             and d.final_kind == "new_job"]
    if not keeps:
        return
    job_lookup = _build_norm_lookup(labels)["jobs"]
    # 确定性基线同名（映射改名可能撞上基线岗位名，零 LLM 根治）
    pending = []
    for d in keeps:
        nm = norm(d.name_zh) or norm(d.name_en)
        code = job_lookup.get(nm) if nm else None
        if code:
            d.map_to = {"taxonomy": "jobs", "code": code}
            d.reason = f"岗位守门同名复检：与基线岗位 {code} 同名 → 强制映射"
        else:
            pending.append(d)
    if not pending:
        return
    job_labels = "\n".join(f"{l['code']}:{l['name_zh']}" for l in labels["jobs"]) or "（无）"
    delta_job_labels = "\n".join(f"{it['id']}:{it['name_zh']}"
                                 for it in delta_items if it.get("array") == "new_jobs") or "（无）"
    cand_json = json.dumps([{"name": d.name_zh, "kind": "new_job",
                             "reason": (d.reason or "")[:120]} for d in pending],
                           ensure_ascii=False, indent=1)
    prompt = (_JOB_GATE_PROMPT
              .replace("{job_labels}", job_labels)
              .replace("{delta_job_labels}", delta_job_labels)
              .replace("{candidates}", cand_json))
    valid_job_codes = {l["code"] for l in labels["jobs"]}
    valid_ids = {it["id"] for it in delta_items}
    try:
        raw = call_llm(prompt, parse_json=True, max_tokens=max_tokens, api_key=api_key)
    except ResourceExhaustedError:
        raise  # 402 熔断：资源性故障中止运行，不降级保信号（2026-09-02 用户裁定）
    except Exception as e:
        if logger:
            logger.error("job_gate", f"岗位守门 LLM 失败：{e}（本批岗位按初判保留）")
        return
    rows = raw if isinstance(raw, list) else []
    by_name = {norm(r.get("name")): r for r in rows if isinstance(r, dict) and r.get("name")}
    for d in pending:
        r = by_name.get(norm(d.name_zh))
        if r is None:
            continue                                   # 终审漏判 → 维持初判
        act = r.get("action")
        if act == "map" and r.get("code") in valid_job_codes:
            d.map_to = {"taxonomy": "jobs", "code": r["code"]}
            d.reason = f"岗位守门 map→{r['code']}"
        elif act == "merge" and r.get("target") in valid_ids:
            d.merge_into = r["target"]
            d.reason = f"岗位守门 merge→{r['target']}"
        elif act == "reject":
            d.status = "reject"
            d.reject_reason = str(r.get("reject_reason") or "").strip() or "非普适市场岗位（守门判定）"
            d.reason = f"岗位守门 reject：{d.reject_reason}"
        elif act == "keep":
            d.reason = f"岗位守门通过（nearest={r.get('nearest', '')}）：{(r.get('why_not') or '')[:80]}"
        else:
            d.status = "reject"
            d.reject_reason = f"岗位守门终审无效（action={act}），宁缺毋滥"


def map_signals(candidates, labels, delta_items, api_key=None, max_tokens=None, logger=None,
                prompt_template=None):
    """映射候选信号 → 决策列表（决策带 index，与候选 index 对齐）。

    labels: load_base_labels() 的返回。delta_items: 增量层已有条目 [{"id","name_zh","array"}]。
    prompt_template: 可选自定义映射提示词模板（含 {task_labels}/{skill_labels}/{job_labels}
    /{delta_labels}/{candidates} 占位符）；缺省用 PROMPT_MAP（论文场景，向后兼容）。
    """
    if not candidates:
        return []
    decisions, remaining = _exact_match_prefilter(candidates, labels)
    if not remaining:
        return decisions

    task_labels = "\n".join(f"{l['code']}:{l['name_zh']}" for l in labels["tasks"]) or "（无）"
    skill_labels = "\n".join(f"{l['code']}:{l['name_zh']} ({l.get('skill_type', '')})" for l in labels["skills"]) or "（无）"
    # 岗位标签（255 行）仅在有岗位类候选时注入：长列表会稀释 LLM 对任务/技能标签的比对注意力
    if any(c.kind == "new_job" for c in remaining):
        job_labels = "\n".join(f"{l['code']}:{l['name_zh']}" for l in labels["jobs"]) or "（无）"
    else:
        job_labels = "（本批无岗位类候选，岗位体系略）"
    delta_labels = "\n".join(f"{it['id']}:{it['name_zh']} ({it['array']})" for it in delta_items) or "（无）"
    cand_json = json.dumps([{
        "index": c.index, "kind": c.kind, "name_zh": c.name_zh, "name_en": c.name_en,
        "rationale": c.rationale, "confidence": c.confidence,
    } for c in remaining], ensure_ascii=False, indent=1)

    template = prompt_template or PROMPT_MAP
    prompt = (template
              .replace("{task_labels}", task_labels)
              .replace("{skill_labels}", skill_labels)
              .replace("{job_labels}", job_labels)
              .replace("{delta_labels}", delta_labels)
              .replace("{candidates}", cand_json))

    try:
        raw = call_llm(prompt, parse_json=True, max_tokens=max_tokens, api_key=api_key)
    except ResourceExhaustedError:
        raise  # 402 熔断：资源性故障中止运行，不降级保信号（2026-09-02 用户裁定）
    except Exception as e:
        if logger:
            logger.error("map", f"映射 LLM 失败：{e}")
        # 保守处理：全部按 keep-new 保留，信号不丢
        for c in remaining:
            decisions.append(Decision(c.index, c.kind, c.name_zh, c.name_en, "keep",
                                      None, None, "", "映射 LLM 失败，保守保留"))
        return decisions

    decs = raw.get("decisions", []) if isinstance(raw, dict) else []
    if not isinstance(decs, list):
        decs = []
    seen = set()
    for d in decs:
        dd = _validate_decision(d, remaining)
        if dd is None or dd.index in seen:
            continue
        seen.add(dd.index)
        decisions.append(dd)
    # LLM 漏判的候选 → 保守保留（keep-new）
    for c in remaining:
        if c.index not in seen:
            decisions.append(Decision(c.index, c.kind, c.name_zh, c.name_en, "keep",
                                      None, None, "", "LLM 未裁决，保守保留"))
    # 防御：kind=skillpoint 的候选不应被拒绝（skillpoint 是技能体系底层实体记录层）
    for d in decisions:
        if d.status != "reject":
            continue
        cand = next((c for c in remaining if c.index == d.index), None)
        if cand and cand.kind == "skillpoint":
            d.status = "keep"
            d.final_kind = "skillpoint"
            d.map_to = None
            d.merge_into = None
            d.reject_reason = ""
            d.reason = "skillpoint 类实体保留至技能点层（防御修正）"
    # keep 终审守门（第二道独立 LLM）：粒度/涵盖/兄弟合并的最终裁定
    recheck_keeps(decisions, labels, delta_items, api_key=api_key,
                  max_tokens=max_tokens, logger=logger)
    # 岗位守门（第二道独立 LLM + 确定性基线同名）：普适性 + 同义/子岗，2026-09-02
    recheck_job_keeps(decisions, labels, delta_items, api_key=api_key,
                      max_tokens=max_tokens, logger=logger)
    # 字面近邻复核（第三道门）：高度相似名 → 小批逐对终裁，堵 LLM 守门混批漏放，2026-09-02
    near_recheck(decisions, labels, api_key=api_key, max_tokens=max_tokens, logger=logger)
    # 改名后确定性复检：归簇改名可能起成基图名/跨类同名（2024-07 实证），零 LLM 根治
    n_map, n_align = _post_rename_prefilter(decisions, labels)
    if n_map or n_align:
        _log_note = getattr(globals().get("logger", None), "note", None)
        print(f"[map] 改名后复检：{n_map} 条同名→映射基线，{n_align} 条跨类同名→对齐合并", flush=True)
    return decisions
