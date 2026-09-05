# -*- coding: utf-8 -*-
"""JD 技能熟练度要求判定（precision-first）：证据组装 → LLM 量规评估 → 契约校验 → 旗标复核。

对 JD 中明确要求的每个技能（49 技能体系，按 code 连接），评估岗位对该技能的
熟练度**要求等级**（P1-P4/U）。移植简历侧（交接包 proficiency_evaluator/validator）三段防线：
  1) 量规注入提示词（jd_proficiency_prompts.RUBRIC，JD 语义版，与简历侧口径相反处见该文件头注）；
  2) 严格输出契约校验：原始文本直解 JSON + 拒绝重复键/多余字段/非法枚举，整块失败重试一次；
  3) 确定性正则旗标（只打旗不改级，供人工复核与校准统计）。

关键设计：
- **证据组装零成本**：复用 Extractor 句级分类（共享 cache/cache_skill.jsonl），
  "句→技能code"映射即证据句来源；同文本二次分类全缓存命中，不产生额外分类调用。
- **词面锚点不定级**：梯度词/年限只注入 prompt 作 lexical_hints，事后做一致性旗标
  （marker_level_conflict 等），等级始终由 LLM 依量规给出。
- 6 个聚合信号技能（简历侧 adapter v1: auxiliary_work_quality）跳过定级。
- 按文本指纹缓存（output/jd_prof_cache.jsonl，gitignored），rubric_version 变更自动失效。

对外 API：
    ev = JDProficiencyEvaluator()
    res = ev.evaluate_jd(jd_text, profile={"title": ..., "funtype": ..., "work_year": ...})
    # res["skills"][code] = {"requirement_level": "P3"|None, "evidence_sufficiency", "dimensions",
    #                        "reason", "uncertainty", "markers", "years_hints", "flags", "review_required"}
    agg = aggregate_proficiency([res1, res2, ...])   # 每技能等级分布 + 旗标计数

llm_call / classifier 可注入（fixtures 测试用 mock，不触网）。
"""
import hashlib
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import config
import jd_proficiency_prompts as pp

LEVELS = ("P1", "P2", "P3", "P4", "U")
DIMENSION_IDS = ("D1", "D2", "D3", "D4")
SUFFICIENCY_VALUES = ("sufficient", "partial", "insufficient")
_LEVEL_RANK = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}

# 聚合信号技能（不定级）：简历侧 team_skill_adapter_v1 标注 auxiliary_work_quality 的 6 项；
# JD 对软技能几乎只写"良好沟通能力"类无梯度表述，定级无意义
AGGREGATE_SKILLS = {"F-1-01", "F-1-03", "F-1-04", "F-3-04", "F-4-01", "F-4-02"}

# ---------------- 词面锚点（提示注入 + 旗标，不定级） ----------------
MARKER_ORDER = ["了解", "熟悉", "掌握", "熟练", "深入理解", "扎实", "精通"]
MARKER_RANK = {"了解": 1, "熟悉": 2, "掌握": 2, "熟练": 3, "深入理解": 3, "扎实": 3, "精通": 4}
# P4 高信号词（旗标校验用：P4 无任一命中 → p4_without_high_signals；
# 校准 2026-05 抽查补入 领导/制定——"领导团队开发""规范制定"均为 P4 高信号表述）
HIGH_SIGNAL_RE = re.compile(r"精通|主导|主持|牵头|带领|领导|架构|选型|技术决策|技术方案|制定|专家|首席|规划")
# 年限表述（阿拉伯数字口径；中文数字场景少，交给 LLM 判断，此处仅做旗标提示）
YEARS_RES = [
    re.compile(r"(\d{1,2})\s*[-~至]\s*\d{1,2}\s*年"),
    re.compile(r"(\d{1,2})\s*年(?:以上|及以上)?"),
]

# 证据句上限（每技能）：控上下文长度，证据按原句序取前 N 条
MAX_EVIDENCE_SENTS = 6
MAX_SENT_CHARS = 300

_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "settings.yaml")


def _settings(*keys, default):
    try:
        import yaml
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            node = yaml.safe_load(f)
        for k in keys:
            node = node[k]
        return node
    except (OSError, ValueError, KeyError, TypeError):
        return default


CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output",
                          "jd_prof_cache.jsonl")

# 证据级缓存（跨 JD 去重）：相同 (技能, 归一化证据) 只判一次。
# 与 per-JD 的 CACHE_PATH（按整条 JD 文本指纹）互补：本缓存按 (技能,证据) 键，
# 使不同 JD 对同一技能的相同证据表述复用同一次 LLM 判定——JD 需求模板化程度高，
# 命中率可观，是熟练度成本的主杠杆（per-JD 缓存跨 JD 不命中）。
EVIDENCE_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output",
                                   "jd_prof_evidence_cache.jsonl")


def _norm_ev(sents):
    """证据句集合 → 归一化指纹（排序+空白归一+md5），供 (技能,证据) 跨 JD 去重键。

    集合语义（排序消除句序差异）、空白归一消除排版差异；与 run_jd_extract._norm_ev 同口径
    （Stage B 的 evidence_dedup 统计基于同一函数，故 Stage C 实测对数与 Stage B 估算一致）。
    """
    norm = sorted(re.sub(r"\s+", " ", s).strip() for s in sents if s and s.strip())
    return hashlib.md5("∥".join(norm).encode("utf-8")).hexdigest()


class ProficiencyParseError(ValueError):
    """模型输出违反严格结果契约。"""


# ---------------- 词面提取（确定性） ----------------
def extract_markers(sentences):
    """证据句中的梯度词（按词表顺序去重）。"""
    text = " ".join(sentences)
    return [m for m in MARKER_ORDER if m in text]


def extract_years(sentences):
    """证据句中的年限表述原文（如"3-5年"、"5年"），去重保序。"""
    text = " ".join(sentences)
    out = []
    for pat in YEARS_RES:
        for m in pat.finditer(text):
            s = m.group(0)
            if s not in out:
                out.append(s)
    return out


def _max_years(years_hints):
    """年限表述 → 最大要求年数（区间取上界；cap 15）。"""
    vals = []
    for s in years_hints:
        nums = [int(n) for n in re.findall(r"\d{1,2}", s)]
        vals.extend(nums)
    return min(max(vals), 15) if vals else None


# ---------------- 严格 JSON 契约（移植简历侧 evaluator） ----------------
def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _strict_load_object(text):
    """原始模型输出 → dict。剥围栏后直解，拒绝重复键；失败抛 ProficiencyParseError。"""
    if not isinstance(text, str) or not text.strip():
        raise ProficiencyParseError("model content must be non-empty text")
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()).strip()
    try:
        payload = json.loads(stripped, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, ValueError) as e:
        raise ProficiencyParseError(f"model output must be exactly one valid JSON object: {e}")
    if not isinstance(payload, dict):
        raise ProficiencyParseError("model output JSON must be an object")
    return payload


def _require_exact_fields(value, expected, name):
    keys = set(value)
    missing = sorted(expected - keys)
    unknown = sorted(keys - expected)
    if missing or unknown:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unknown:
            details.append("unknown: " + ", ".join(unknown))
        raise ProficiencyParseError(f"{name} fields invalid; " + "; ".join(details))


def _non_empty(name, value):
    if not isinstance(value, str) or not value.strip():
        raise ProficiencyParseError(f"{name} must be non-empty text")
    return value.strip()


def _validate_result_item(item):
    """单个技能评估结果 → 规范化 dict（字段/枚举/维度全量校验）。"""
    if not isinstance(item, dict):
        raise ProficiencyParseError("results item must be an object")
    _require_exact_fields(item, {"team_skill_id", "evidence_sufficiency", "dimensions",
                                 "requirement_level", "reason", "uncertainty"},
                          "proficiency result")
    code = _non_empty("team_skill_id", item["team_skill_id"])
    suff = item["evidence_sufficiency"]
    if suff not in SUFFICIENCY_VALUES:
        raise ProficiencyParseError(f"invalid evidence_sufficiency: {suff!r}")
    level = item["requirement_level"]
    if level not in LEVELS:
        raise ProficiencyParseError(f"invalid requirement_level: {level!r}")
    reason = _non_empty("reason", item["reason"])

    dims_value = item["dimensions"]
    if not isinstance(dims_value, dict):
        raise ProficiencyParseError("dimensions must be an object")
    _require_exact_fields(dims_value, set(DIMENSION_IDS), "dimensions")
    dimensions = {}
    for dim in DIMENSION_IDS:
        d = dims_value[dim]
        if not isinstance(d, dict):
            raise ProficiencyParseError(f"dimensions.{dim} must be an object")
        _require_exact_fields(d, {"level", "reason"}, f"dimensions.{dim}")
        if d["level"] not in LEVELS:
            raise ProficiencyParseError(f"invalid dimensions.{dim}.level: {d['level']!r}")
        dimensions[dim] = {"level": d["level"],
                           "reason": _non_empty(f"dimensions.{dim}.reason", d["reason"])}

    unc_value = item["uncertainty"]
    if not isinstance(unc_value, list):
        raise ProficiencyParseError("uncertainty must be a list")
    uncertainty = [_non_empty(f"uncertainty[{i}]", v) for i, v in enumerate(unc_value)]

    return {"team_skill_id": code, "evidence_sufficiency": suff,
            "dimensions": dimensions, "requirement_level": level,
            "reason": reason, "uncertainty": uncertainty}


# ---------------- 确定性旗标（JD 化 validator，只打旗不改级） ----------------
def _flags_for(level, sufficiency, evidence_text, markers, years_hints):
    flags = []
    if sufficiency == "insufficient" and level != "U":
        flags.append("insufficient_level_conflict")
    # 词面锚点 × 等级一致性：跨度 ≥2 档说明证据混合了多档要求（不同子技能点），
    # 词面无法锚定单一等级（LLM 取中档合理），改标歧义；窄跨度才做等级-词面冲突判定
    if level in _LEVEL_RANK and markers:
        ranks = [MARKER_RANK[m] for m in markers]
        top, bottom = max(ranks), min(ranks)
        rank = _LEVEL_RANK[level]
        if top - bottom >= 2:
            flags.append("marker_span_ambiguous")
        elif top - rank >= 2 or rank - bottom >= 2:
            flags.append("marker_level_conflict")
    if level == "P4" and not HIGH_SIGNAL_RE.search(evidence_text):
        flags.append("p4_without_high_signals")
    if level == "P1" and _max_years(years_hints or []) is not None \
            and _max_years(years_hints) >= 5:
        flags.append("years_level_conflict")
    if not evidence_text.strip():
        flags.append("no_evidence")
    return flags


# ---------------- 技能元信息（名称/定义注入 prompt） ----------------
def _skill_meta():
    with open(config.SKILL_TAXONOMY, encoding="utf-8") as f:
        detail = json.load(f).get("detail", {})
    return {c: {"name_zh": d["name_zh"], "definition": d.get("definition", "")}
            for c, d in detail.items()}


def _fingerprint(text):
    return hashlib.md5(re.sub(r"\s+", " ", text or "").strip().encode("utf-8")).hexdigest()


# ---------------- 评估器 ----------------
class JDProficiencyEvaluator:
    """逐 JD 评估技能熟练度要求。

    llm_call:   callable(prompt) -> 原始模型文本（严格契约由本类解析；缺省走 llm.call_llm）
    classifier: callable(text) -> {code: [证据句]}（缺省复用 Extractor 句级分类，全缓存命中）
    """

    def __init__(self, llm_call=None, classifier=None, chunk_skills=None,
                 use_cache=None, api_key=None):
        self.chunk_skills = int(chunk_skills or _settings("jd_proficiency", "chunk_skills",
                                                          default=12))
        self.use_cache = (_settings("jd_proficiency", "use_cache", default=True)
                          if use_cache is None else use_cache)
        if llm_call is None:
            from llm import call_llm
            key = api_key
            llm_call = lambda prompt: call_llm(prompt, parse_json=False, api_key=key)
        self.llm_call = llm_call
        self._classifier = classifier
        self._default_classifier = classifier is None
        self._meta = None
        self._cache = None
        self._ev_cache = None       # 证据级缓存（跨 JD 去重）
        self.concurrency = config.concurrency_total()   # chunk 批并发（= 单key并发 × 启用key数）
        self._lock = threading.Lock()           # 并发下保护 n_calls/n_retries/n_invalid
        # 运行统计（CLI 报告用）
        self.n_calls = 0
        self.n_cache_hits = 0
        self.n_retries = 0
        self.n_invalid = 0

    # ---------- 证据组装 ----------
    def preload(self):
        """立即构建默认证据分类器与技能元信息。

        跨模块调用方（graph/base_builder）会在 extractor config 环境下 import 本模块、
        随后换出 config；惰性初始化若发生在换出后会读到外层模块的 config
        （extractor 专属常量如 CACHE_DIR 会 AttributeError）。调用方在 import 窗口内
        preload() 即可把 extractor 依赖全部绑定在正确环境。
        """
        if self._default_classifier and not hasattr(self, "_extractor"):
            from extractor import Extractor
            self._extractor = Extractor(mode="merged")   # 一句一次 skill+task+skillpoint
            self._taxonomy = None   # merged 内部加载两套体系，_classify_evidence 取 skill 部分
        if self._meta is None:
            self._meta = _skill_meta()
        return self

    def _classify_evidence(self, text):
        """text -> {code: [证据句]}。聚合技能剔除；句序保持原文出现顺序。

        merged 模式 results[s] = {"skills":[{code,skillpoints}], "tasks":[...]}（dict）；
        skill 单模式 results[s] = [{code,skillpoints}]（list）。两者皆支持。
        """
        if self._default_classifier:
            self.preload()
            import text_split
            sentences = text_split.split_sentences(text)
            results, _ = self._extractor._classify_units(sentences, self._taxonomy)
            ev = {}
            for s in sentences:                      # 原文顺序
                rec = results.get(s)
                sks = rec.get("skills", []) if isinstance(rec, dict) else (rec or [])
                for m in sks:
                    code = m.get("code")
                    if not code or code in AGGREGATE_SKILLS:
                        continue
                    lst = ev.setdefault(code, [])
                    if s not in lst:
                        lst.append(s)
            return ev
        return {c: list(ss) for c, ss in (self._classifier(text) or {}).items()
                if c not in AGGREGATE_SKILLS}

    # ---------- 缓存 ----------
    def _load_cache(self):
        if self._cache is not None:
            return self._cache
        self._cache = {}
        if os.path.exists(CACHE_PATH):
            with open(CACHE_PATH, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        self._cache[rec["key"]] = rec
                    except (ValueError, KeyError):
                        continue
        return self._cache

    def _save_cache(self, record):
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._cache[record["key"]] = record

    # ---------- 证据级缓存（跨 JD 去重） ----------
    def _load_ev_cache(self):
        """(技能code, 证据指纹) → 评估记录 dict。惰性加载、进程内缓存。"""
        if self._ev_cache is not None:
            return self._ev_cache
        self._ev_cache = {}
        if os.path.exists(EVIDENCE_CACHE_PATH):
            with open(EVIDENCE_CACHE_PATH, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        self._ev_cache[tuple(rec["key"])] = rec["value"]
                    except (ValueError, KeyError):
                        continue
        return self._ev_cache

    def _save_ev_cache(self, ev_key, value):
        os.makedirs(os.path.dirname(EVIDENCE_CACHE_PATH), exist_ok=True)
        with open(EVIDENCE_CACHE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({"key": list(ev_key), "value": value},
                               ensure_ascii=False) + "\n")
        self._ev_cache[ev_key] = value

    # ---------- 单块 LLM 评估 ----------
    def _evaluate_chunk(self, pairs, profile):
        """一批技能对 → {code: 合法评估结果}。契约失败整块重试一次；仍缺则该技能置 None。

        线程安全：n_calls/n_retries/n_invalid 加锁（chunk 批可并发调用本方法）。
        契约修复：LLM 偶发回显输入 evidence 句（非契约字段），校验前剥离以避免整块失败。
        部分成功重试只发缺失技能（v2：LLM 偶发漏答个别技能，重发整块浪费一半 token；
        配合 prompt "条数相等" 完整性要求，重试率显著下降）。
        """
        out, codes = {}, {p["team_skill_id"] for p in pairs}
        pending = list(pairs)
        for attempt in (1, 2):
            if not pending:
                break
            input_obj = {"jd_profile": profile or {}, "skills": pending}
            prompt = (pp.PROMPT_JD_PROFICIENCY
                      .replace("{rubric}", json.dumps(pp.RUBRIC, ensure_ascii=False, indent=1))
                      .replace("{input}", json.dumps(input_obj, ensure_ascii=False)))
            with self._lock:
                self.n_calls += 1
                if attempt == 2:
                    self.n_retries += 1
            try:
                payload = _strict_load_object(self.llm_call(prompt))
                entries = payload.get("results")
                if not isinstance(entries, list):
                    raise ProficiencyParseError("payload must contain results array")
                for it in entries:
                    it = dict(it) if isinstance(it, dict) else {}
                    it.pop("evidence", None)        # LLM 偶发回显输入 evidence 句，剥离后再严格校验
                    item = _validate_result_item(it)
                    code = item["team_skill_id"]
                    if code in codes and code not in out:
                        out[code] = item
            except (ProficiencyParseError, ValueError) as e:
                with self._lock:
                    self.n_invalid += 1
                if attempt == 2:
                    print(f"[prof] 契约校验失败（放弃本块 {len(pending)} 对）：{e}")
                continue
            pending = [p for p in pending if p["team_skill_id"] not in out]
        return out

    def _eval_pairs_to_recs(self, pairs, profile):
        """并发跑各 chunk → {code: rec}（含 None 分支 + 旗标复核）。chunk 按 concurrency 并发。

        缓存写入由调用方顺序做（避免并发写文件交错）；本方法只产 rec。
        """
        if not pairs:
            return {}
        chunks = [pairs[i:i + self.chunk_skills] for i in range(0, len(pairs), self.chunk_skills)]

        def run_chunk(chunk):
            results = self._evaluate_chunk(chunk, profile)
            out = {}
            for p in chunk:
                c = p["team_skill_id"]
                sents = p["evidence"]
                item = results.get(c)
                if item is None:
                    out[c] = {"team_skill_id": c, "name_zh": p["name_zh"],
                              "requirement_level": None, "evidence_sufficiency": None,
                              "dimensions": {}, "reason": "", "uncertainty": [],
                              "evidence": sents, "markers": p["lexical_hints"],
                              "years_hints": p["years_hints"],
                              "flags": ["llm_no_valid_result"], "review_required": True}
                else:
                    flags = _flags_for(item["requirement_level"], item["evidence_sufficiency"],
                                       " ".join(sents), p["lexical_hints"], p["years_hints"])
                    out[c] = {**item, "name_zh": p["name_zh"], "evidence": sents,
                              "markers": p["lexical_hints"], "years_hints": p["years_hints"],
                              "flags": flags, "review_required": bool(flags)}
            return out

        recs = {}
        if self.concurrency > 1 and len(chunks) > 1:
            with ThreadPoolExecutor(max_workers=self.concurrency) as ex:
                for out in ex.map(run_chunk, chunks):
                    recs.update(out)
        else:
            for chunk in chunks:
                recs.update(run_chunk(chunk))
        return recs

    # ---------- 主入口 ----------
    def evaluate_jd(self, text, profile=None):
        """一条 JD -> {"key", "rubric_version", "skills": {code: {...}}, "n_calls"}。"""
        key = _fingerprint(text)
        cache = self._load_cache()
        hit = cache.get(key)
        if self.use_cache and hit and hit.get("rubric_version") == pp.RUBRIC_VERSION:
            self.n_cache_hits += 1
            return hit

        if self._meta is None:
            self._meta = _skill_meta()
        evidence = self._classify_evidence(text)
        codes = sorted(c for c in evidence if c in self._meta)
        pairs = []
        for c in codes:
            sents = [s[:MAX_SENT_CHARS] for s in evidence[c][:MAX_EVIDENCE_SENTS]]
            meta = self._meta[c]
            pairs.append({"team_skill_id": c, "name_zh": meta["name_zh"],
                          "definition": meta["definition"], "evidence": sents,
                          "lexical_hints": extract_markers(sents),
                          "years_hints": extract_years(sents)})
        calls_before = self.n_calls
        skills = self._eval_pairs_to_recs(pairs, profile)
        record = {"key": key, "rubric_version": pp.RUBRIC_VERSION, "skills": skills,
                  "n_calls": self.n_calls - calls_before}
        if self.use_cache:
            self._save_cache(record)
        return record

    # ---------- 跨 JD 证据去重模式（Stage C：读 Stage B 的 evidence_map） ----------
    def prepare_jd_from_evidence(self, jd_key, evidence_map,
                                 marker_gated=False, soft_gate=None):
        """evaluate_jd_from_evidence 的准备阶段（纯串行调用，跨 JD 去重判定在此发生）。

        → {"key", "skills": 缓存命中/门控 U 的既定结果, "pairs": 待评 LLM 对, "ev_keys"}。
        分离 prepare/finalize 使 Stage C 可两遍式执行：先串行 prepare 全窗 JD（同证据对
        只留首现——并发下重复在飞会破坏"同证据同判定"），再全窗 chunk 并行送 LLM，
        最后串行 finalize 写缓存。
        """
        if soft_gate is None:
            soft_gate = _settings("jd_proficiency", "soft_gate", default=True)
        if self._meta is None:
            self._meta = _skill_meta()
        ev_cache = self._load_ev_cache() if self.use_cache else {}
        skills, pairs, ev_keys = {}, [], {}
        for code, sents in evidence_map.items():
            if code not in self._meta:               # 非可定级技能（体系外/聚合信号）
                continue
            ek = (code, _norm_ev(sents))
            ev_keys[code] = ek
            if self.use_cache:
                hit = ev_cache.get(ek)
                if hit is not None:
                    skills[code] = hit
                    self.n_cache_hits += 1
                    continue
            sents_c = [s[:MAX_SENT_CHARS] for s in sents[:MAX_EVIDENCE_SENTS]]
            markers = extract_markers(sents_c)
            gate = (marker_gated and not markers) or \
                   (soft_gate and code.startswith("F-") and not markers)
            if gate:
                # 标记门控：无梯度词 → 确定性 U（罗列型提及，≠低要求），免 LLM
                reason = ("JD 仅罗列该技能、无梯度表述（marker_gated 确定性 U）"
                          if not code.startswith("F-")
                          else "软技能无梯度表述，无熟练度要求可判（soft_gate 确定性 U，"
                               "下游按最低档要求参与匹配）")
                rec = {"team_skill_id": code, "name_zh": self._meta[code]["name_zh"],
                       "requirement_level": "U", "evidence_sufficiency": "insufficient",
                       "dimensions": {d: {"level": "U", "reason": "无梯度词·标记门控"}
                                       for d in DIMENSION_IDS},
                       "reason": reason,
                       "uncertainty": [], "evidence": sents_c, "markers": [],
                       "years_hints": extract_years(sents_c),
                       "flags": [], "review_required": False}
                skills[code] = rec
                if self.use_cache:
                    self._save_ev_cache(ek, rec)
                continue
            meta = self._meta[code]
            pairs.append({"team_skill_id": code, "name_zh": meta["name_zh"],
                          "definition": meta["definition"], "evidence": sents_c,
                          "lexical_hints": markers,
                          "years_hints": extract_years(sents_c)})
        return {"key": jd_key, "skills": skills, "pairs": pairs, "ev_keys": ev_keys}

    def finalize_jd_from_evidence(self, prep, new_skills):
        """合并 prepare 既定结果 + LLM 新评定 → 完整记录；顺序写证据缓存（串行调用）。"""
        skills = dict(prep["skills"])
        for c, rec in (new_skills or {}).items():
            skills[c] = rec
            if self.use_cache:
                self._save_ev_cache(prep["ev_keys"][c], rec)
        return {"key": prep["key"], "rubric_version": pp.RUBRIC_VERSION, "skills": skills}

    def evaluate_jd_from_evidence(self, jd_key, evidence_map, profile=None,
                                  marker_gated=False, soft_gate=None):
        """从已抽好的 evidence_map 评估一条 JD 的技能熟练度（跨 JD 证据去重）。

        evidence_map: {skill_code: [证据句]}（Stage B 副产品；聚合信号技能已剔除）。
        跨 JD 去重：相同 (技能, 归一化证据) 只判一次——证据级缓存命中即复用，未命中分批
        （chunk_skills）送 LLM；profile 作首现 JD 的定级上下文（后续同证据 JD 复用其判定）。
        marker_gated=True：无梯度词的证据 → 确定性 U（罗列型提及，≠低要求），免 LLM。
        soft_gate（默认 settings jd_proficiency.soft_gate，现 True）：软技能（F- 前缀，
        聚合信号技能之外）无梯度词 → 确定性 U 免 LLM——JD 对软技能几乎只写
        "良好沟通能力"类无梯度表述，且下游约定 U 视作最低档要求参与匹配。
        → {"key": jd_key, "rubric_version", "skills": {code: {...}}, "n_calls"}.
        """
        prep = self.prepare_jd_from_evidence(jd_key, evidence_map,
                                             marker_gated=marker_gated, soft_gate=soft_gate)
        calls_before = self.n_calls
        new_skills = self._eval_pairs_to_recs(prep["pairs"], profile)
        record = self.finalize_jd_from_evidence(prep, new_skills)
        record["n_calls"] = self.n_calls - calls_before
        return record


# ---------------- 聚合（窗口/批次级） ----------------
def aggregate_proficiency(records):
    """多条 JD 的评估记录 -> {code: {name_zh, n, levels: {P1..U}, unset, flags: {..}, review}}。

    levels 只统计 LLM 给出的等级；unset = 契约失败未定级；U 单列（罗列型提及，≠低要求）。
    """
    out = {}
    for rec in records:
        for code, s in (rec.get("skills") or {}).items():
            d = out.setdefault(code, {"name_zh": s.get("name_zh", code),
                                      "n": 0, "levels": {L: 0 for L in LEVELS},
                                      "unset": 0, "flags": {}, "review": 0})
            d["n"] += 1
            lv = s.get("requirement_level")
            if lv in d["levels"]:
                d["levels"][lv] += 1
            else:
                d["unset"] += 1
            if s.get("review_required"):
                d["review"] += 1
            for f in s.get("flags", []):
                d["flags"][f] = d["flags"].get(f, 0) + 1
    return out
