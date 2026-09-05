# -*- coding: utf-8 -*-
"""ΔG 增量层存取与聚合（papers_delta.json / news_delta.json / jd_delta.json）。

源无关：`DeltaStore(source_kind="papers"|"news"|"jd")` 决定证据键字段与权重语义——
- papers：evidence 按 paper_id（record.doc_id），contrib = TIER_WEIGHTS[tier] × conf × 0.5^(age/730)
- news：evidence 按 news_id（record.doc_id），contrib = NEWS_SOURCE_WEIGHT × conf × 0.5^(age/180)
- jd：evidence 按 jobid（record.doc_id），contrib = JD_SOURCE_WEIGHT × conf × 0.5^(age/365)
  （市场确证源：权重 1.0=概率上限；新证据均写 ev["src"]=source_kind 供跨源判源）

要点：
- evidence 按 record.doc_id 索引（dict keyed by doc_id）→ **幂等**：同一文档重复处理不重复证据
- 强度 noisy-OR：strength = 1 - Π(1 - contrib)；反复出现→强度单调上升（生命周期"增强"要素）
- 保存时剔除 strength < MIN_STRENGTH 的**单文档**一次性噪声（多证据信号永不删除——遗忘=半衰期
  衰减降低可见性，见 builder/participation.py，而非删除）
- id 前缀：PJ- / PT- / PS- / PK-（in-kind），与基础体系 T-/S- 不冲突
- new_jobs[].status："pending"（待确证）→ 岗位热更新回填关联；"graduated"（已转正入基图，
  promotion.py 标记，快照合并跳过）
"""
import json
import os
import re
from datetime import date, datetime

import config

_ID_PREFIX = {"new_jobs": "PJ", "new_tasks": "PT", "new_skills": "PS", "skillpoints": "PK"}
_SOURCE_META = {
    "papers": ("ΔG 增量层（论文信号）", "papers", "学术论文"),
    "news": ("ΔG 增量层（新闻信号）", "news", "行业新闻"),
    "jd": ("ΔG 增量层（JD 确证信号）", "jd", "招聘 JD"),
}


def norm(name):
    return re.sub(r"[\s、，,.;·\-_（）()]", "", name or "").lower()


def _recency_decay(pub_date, now, half_life):
    """时间衰减：0.5 ** (age_days / half_life)。日期缺失用固定底权。"""
    if not pub_date:
        return config.RECENCY_UNKNOWN_DECAY
    try:
        d = datetime.strptime(pub_date, "%Y-%m-%d").date()
    except ValueError:
        return config.RECENCY_UNKNOWN_DECAY
    days = max(0, (now - d).days)
    return 0.5 ** (days / half_life)


def _noisy_or(contribs):
    p = 1.0
    for c in contribs:
        if c > 0:
            p *= (1 - c)
    return 1 - p


class DeltaStore:
    """ΔG 增量层：读/写 papers_delta.json 或 news_delta.json，幂等 upsert + 强度聚合 + 编号生成。"""

    def __init__(self, path=None, source_desc="", source_kind="papers",
                 source_weight=1.0, half_life_days=None, now=None):
        self.path = path or config.DELTA_OUTPUT
        self.source_kind = source_kind if source_kind in _SOURCE_META else "papers"
        self.source_weight = source_weight
        self.half_life = half_life_days or config.HALF_LIFE_DAYS
        self.now = now or date.today()
        # 入场窗戳：本 store 实例写入的新条目"体系首次学到该信号"的窗口标签。
        # 出生以**体系学习时序**为准（而非证据日期）——确证/转正的滞后语义据此判定：
        # 市场响应必须来自入场窗之后的数据，压缩回填语料不能凭旧日期提前确证。
        self.born_window = f"{self.now:%Y-%m}" if self.now else ""
        self.data = self._load_or_new(source_desc)

    # ---------------- 元数据 ----------------
    def _load_or_new(self, source_desc):
        if os.path.exists(self.path):
            try:
                data = json.load(open(self.path, encoding="utf-8"))
                for k in ("new_jobs", "new_tasks", "new_skills", "skillpoints", "strengthenings"):
                    data.setdefault(k, [])
                return data
            except Exception as e:
                print(f"[delta] 读取已有增量层失败，重建：{e}")
        name, kind, default_src = _SOURCE_META[self.source_kind]
        return {
            "meta": {
                "system_name": name,
                "version": "0.1",
                "date": self.now.isoformat(),
                "source": source_desc or default_src,
                "source_kind": kind,
                "stats": {},
            },
            "new_jobs": [], "new_tasks": [], "new_skills": [], "skillpoints": [],
            "strengthenings": [],
        }

    # ---------------- 查询 ----------------
    def _find_strengthening(self, taxonomy, code):
        for s in self.data["strengthenings"]:
            if s.get("taxonomy") == taxonomy and s.get("code") == code:
                return s
        return None

    def _find_by_id(self, eid):
        for arr in ("new_jobs", "new_tasks", "new_skills", "skillpoints"):
            for it in self.data[arr]:
                if it.get("id") == eid:
                    return it
        return None

    def _find_by_name(self, arr, name_zh):
        n = norm(name_zh)
        for it in self.data[arr]:
            if norm(it.get("name_zh", "")) == n:
                return it
        return None

    @staticmethod
    def _target_array(final_kind):
        if final_kind in ("new_job",):
            return "new_jobs"
        if final_kind in ("new_task", "implied_task"):
            return "new_tasks"
        if final_kind in ("new_skill", "capability_gap"):
            return "new_skills"
        if final_kind == "skillpoint":
            return "skillpoints"
        return None

    def existing_items(self):
        """增量层已有条目（供映射阶段的跨文档合并判断）。"""
        out = []
        for arr in ("new_jobs", "new_tasks", "new_skills", "skillpoints"):
            for it in self.data[arr]:
                out.append({"id": it.get("id"), "name_zh": it.get("name_zh", ""), "array": arr})
        return out

    # ---------------- 贡献与证据 ----------------
    @staticmethod
    def _doc_id(record):
        """证据幂等键：record.doc_id（news）或 arxiv_id（paper）。"""
        return getattr(record, "doc_id", None) or getattr(record, "arxiv_id", "")

    def _evidence_entry(self, candidate, record):
        ev = {"date": getattr(record, "pub_date", "") or "",
              "sentences": list(candidate.evidence),
              "confidence": candidate.confidence,
              "src": self.source_kind}  # 前向显式判源（旧数据无 src：快照按 tier 键回退判源）
        if self.source_kind == "papers":
            ev["tier"] = getattr(record, "tier", "") or ""
        return ev

    def _contrib(self, ev):
        cw = config.CONF_WEIGHT.get(ev.get("confidence") or "", 0.3)
        decay = _recency_decay(ev.get("date", ""), self.now, self.half_life)
        if self.source_kind in ("news", "jd"):
            return self.source_weight * cw * decay
        tw = config.TIER_WEIGHTS.get(ev.get("tier") or "", 0.2)
        return tw * cw * decay

    # ---------------- 应用 ----------------
    def apply(self, candidate, decision, code_to_name=None):
        """把一条裁决应用到增量层。返回 (action, detail) 供日志。"""
        if decision.status == "reject":
            return ("reject", decision.reject_reason)

        record = candidate.record
        doc_id = self._doc_id(record)
        ev = self._evidence_entry(candidate, record)
        contrib = self._contrib(ev)

        # 1) 映射到基础体系 → strengthenings（已有条目的强度增强）
        if decision.map_to:
            tax, code = decision.map_to["taxonomy"], decision.map_to["code"]
            entry = self._find_strengthening(tax, code)
            if entry is None:
                name = ""
                if code_to_name:
                    name = code_to_name.get(tax, {}).get(code, "")
                entry = {"taxonomy": tax, "code": code, "name_zh": name,
                         "source_kinds": [], "strength": 0.0, "max_contrib": 0.0, "evidence": {}}
                self.data["strengthenings"].append(entry)
            self._merge_evidence(entry, doc_id, ev, contrib)
            if decision.final_kind not in entry["source_kinds"]:
                entry["source_kinds"].append(decision.final_kind)
            return ("strengthen", f"{tax}:{code}")

        # 2) 合并到增量层已有条目（跨文档同一信号）
        if decision.merge_into:
            entry = self._find_by_id(decision.merge_into)
            if entry is not None:
                self._merge_evidence(entry, doc_id, ev, contrib)
                return ("merge", f"{decision.merge_into}/{entry.get('name_zh', '')}")
            decision.merge_into = None  # id 找不到 → 降级为新建

        # 3) 新条目
        arr = self._target_array(decision.final_kind)
        if arr is None:
            return ("skip", f"未知 final_kind: {decision.final_kind}")
        entry = self._find_by_name(arr, decision.name_zh)
        if entry is None:
            entry = self._create_entry(arr, decision, candidate.definition or candidate.rationale)
        self._merge_evidence(entry, doc_id, ev, contrib)
        return ("new", f"{arr}/{entry['id']}")

    def strengthen_existing(self, record, taxonomy, code, name, evidence, confidence,
                            grade=""):
        """直接把一条"既有提及"并入 strengthenings（mention 路径，跳过体系映射）。

        grade：证据等级标记（"scan"=扫描发现，"require"=确证通道判定"要求掌握"）——
        转正的 JD 确证计数只认 require；空=旧数据未分级。
        """
        ev = {"date": getattr(record, "pub_date", "") or "",
              "sentences": list(evidence),
              "confidence": confidence,
              "src": self.source_kind}
        if grade:
            ev["grade"] = grade
        if self.source_kind == "papers":
            ev["tier"] = getattr(record, "tier", "") or ""
        contrib = self._contrib(ev)
        entry = self._find_strengthening(taxonomy, code)
        if entry is None:
            entry = {"taxonomy": taxonomy, "code": code, "name_zh": name,
                     "source_kinds": ["mention"], "strength": 0.0, "max_contrib": 0.0, "evidence": {}}
            self.data["strengthenings"].append(entry)
        self._merge_evidence(entry, self._doc_id(record), ev, contrib)
        if "mention" not in entry["source_kinds"]:
            entry["source_kinds"].append("mention")
        return entry

    def update_job_links(self, job_id, related_tasks=None, related_skills=None):
        """岗位热更新：回填 new_jobs 条目的任务/技能关联。返回岗位条目或 None。"""
        job = self._find_by_id(job_id)
        if job is None:
            return None
        if related_tasks is not None:
            job["related_tasks"] = related_tasks
        if related_skills is not None:
            job["related_skills"] = related_skills
        return job

    def confirm_named(self, array, name_zh, record, sentences, confidence="high",
                      name_en="", definition="", ref_id="", grade=""):
        """JD 侧确证（按名称落**本源文件**，跨源聚合靠快照层 norm 合并）。

        叠层实体可能存在于 papers/news 文件而本源没有同名条目——确证证据无法跨文件写，
        因此按名称写入本源（jd）文件：已有同名 → 合并证据；无 → 新建最小条目
        （definition 取参与实体定义，ref_id 记被确证的跨源叠层 id 便于追溯）。
        快照合并按 norm(name_zh) 聚合两源条目 → 证据并集、强度按判源重算。
        grade="require"（确证通道"要求掌握"）才计入转正的 JD 确证文档数；
        grade="scan"（发现通道）只贡献强度，不充当确证。
        同 doc_id 幂等。返回 (entry, created)。
        """
        entry = self._find_by_name(array, name_zh)
        created = False
        if entry is None:
            shim = type("DecisionShim", (), {})()  # 轻量 shim：_create_entry 只读 name_zh/name_en
            shim.name_zh = name_zh
            shim.name_en = name_en
            entry = self._create_entry(array, shim, definition)
            if ref_id:
                entry["ref_id"] = ref_id
            created = True
        ev = {"date": getattr(record, "pub_date", "") or "",
              "sentences": list(sentences),
              "confidence": confidence,
              "src": self.source_kind}
        if grade:
            ev["grade"] = grade
        self._merge_evidence(entry, self._doc_id(record), ev, self._contrib(ev))
        if self.source_kind == "jd" and "jd_confirm" not in entry.get("source_kinds", []):
            entry.setdefault("source_kinds", []).append("jd_confirm")
        return entry, created

    def _create_entry(self, arr, decision, definition):
        entry = {
            "id": self._next_id(arr),
            "name_zh": decision.name_zh,
            "name_en": decision.name_en,
            "evidence": {},
            "strength": 0.0,
            "max_contrib": 0.0,
            "born_window": self.born_window,
        }
        if arr == "new_jobs":
            entry["status"] = "pending"          # 未来岗位热更新模块消费；绝不写入 jobs0806.json
            entry["definition"] = definition
            entry["related_tasks"] = []
            entry["related_skills"] = []
        elif arr == "new_skills":
            entry["skill_type"] = "hard"
            entry["definition"] = definition
        elif arr == "new_tasks":
            entry["description"] = definition
        else:  # skillpoints
            entry["description"] = definition
        self.data[arr].append(entry)
        return entry

    def _next_id(self, arr):
        prefix = _ID_PREFIX[arr]
        nums = []
        for it in self.data[arr]:
            m = re.match(rf"^{prefix}-(\d+)$", it.get("id", ""))
            if m:
                nums.append(int(m.group(1)))
        return f"{prefix}-{max(nums) + 1 if nums else 1:03d}"

    # ---------------- 证据合并与强度 ----------------
    def _merge_evidence(self, entry, doc_id, ev, contrib):
        evidence = entry.setdefault("evidence", {})
        if doc_id in evidence:
            old = evidence[doc_id]
            old_sent = set(old.get("sentences", []))
            for s in ev.get("sentences", []):
                if s not in old_sent:
                    old.setdefault("sentences", []).append(s)
                    old_sent.add(s)
        else:
            evidence[doc_id] = dict(ev)
        entry["evidence"] = {pid: evidence[pid] for pid in sorted(evidence)}
        self._recompute_strength(entry)

    def _recompute_strength(self, entry):
        contribs = [self._contrib(ev) for ev in entry.get("evidence", {}).values()]
        entry["strength"] = round(_noisy_or(contribs), 4)
        entry["max_contrib"] = round(max(contribs), 4) if contribs else 0.0
        dates = [ev.get("date", "") for ev in entry.get("evidence", {}).values() if ev.get("date")]
        if dates:
            entry["first_seen"] = min(dates)
            entry["last_seen"] = max(dates)
        if self.source_kind == "papers":
            tiers = sorted({ev.get("tier", "") for ev in entry.get("evidence", {}).values() if ev.get("tier")})
            if tiers:
                entry["tiers"] = tiers

    # ---------------- 保存 ----------------
    def save(self):
        """保存并返回统计。剔除强度过低的一次性噪声。"""
        pruned = self._prune_low_strength()
        if pruned:
            print(f"[delta] 剔除低强度一次性信号 {len(pruned)} 条：{', '.join(pruned[:10])}")
        stats = self.data["meta"].setdefault("stats", {})
        for arr in ("new_jobs", "new_tasks", "new_skills", "skillpoints"):
            stats[f"n_{arr}"] = len(self.data[arr])
        stats["n_strengthenings"] = len(self.data["strengthenings"])
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        return stats

    def _prune_low_strength(self):
        pruned = []
        for arr in ("new_jobs", "new_tasks", "new_skills", "skillpoints"):
            keep = []
            for it in self.data[arr]:
                # 岗位关联产物（assoc_from）保留：避免相关链接悬空
                if it.get("assoc_from"):
                    keep.append(it)
                    continue
                # 仅剔除：单篇支撑且强度低于阈值（真正的一次性噪声）
                if it.get("strength", 0) < config.MIN_STRENGTH and len(it.get("evidence", {})) <= 1:
                    pruned.append(it.get("name_zh", it.get("id", "")))
                    continue
                keep.append(it)
            self.data[arr] = keep
        return pruned
