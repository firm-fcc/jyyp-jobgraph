# -*- coding: utf-8 -*-
"""Extractor 核心：JD 分句 → 句级分类 → 技能/任务计数。

对外 API（供 Agent 调度）：
    ext = Extractor(mode="skill")            # 或 "task"
    result = ext.extract(jd_text)            # -> {"code": count}
    results = ext.extract_many([jd1, jd2])   # -> [result, ...]

优化：
- 分句后逐句分类（1:1 / 1:n）
- 句级缓存命中复用（跨运行持久化）
- 句内去重（同一 JD 内重复句只分类一次）
"""
from collections import Counter, defaultdict

import cache as cache_mod
import text_split
from llm_client import LLMClient

# merged 句级缓存版本：prompt 口径变更时 +1 隔离旧缓存（v2 = 技能点禁品牌/设备/泛指，
# 2026-08-25；v3 = 叠层候选参与分类 + overlays 输出，2026-08-30——一次性全量句重抽）。
# 非 merged 模式沿用原 mode 名（skill/task 缓存不受影响）。
MERGED_CACHE_MODE = "merged_v3"


def build_overlay_labels(items):
    """叠层候选文本块：- 名称（类型）：定义。

    items：participating_items 输出（[{"name_zh","array","definition",...}]）。
    同名跨 kind（任务+技能）以类型后缀区分；定义截断 80 字。
    """
    kind_cn = {"new_tasks": "任务", "new_skills": "技能", "new_jobs": "岗位"}
    lines = []
    for it in items:
        nm = (it.get("name_zh") or "").strip()
        if not nm:
            continue
        k = kind_cn.get(it.get("array"), "实体")
        dfn = (it.get("definition") or "").strip().replace("\n", " ")
        if len(dfn) > 80:
            dfn = dfn[:80] + "…"
        lines.append(f"- {nm}（{k}）：{dfn}" if dfn else f"- {nm}（{k}）")
    return "\n".join(lines)


class Extractor:
    def __init__(self, mode="skill", llm_client=None, use_cache=True, overlay_items=None):
        self.mode = mode
        self.llm = llm_client or LLMClient()
        self.use_cache = use_cache
        cache_mode = MERGED_CACHE_MODE if mode == "merged" else mode
        self.cache = cache_mod.ResultCache(cache_mode) if use_cache else None
        self._skill_tax = None   # merged 模式惰性加载两套体系
        self._task_tax = None
        # 叠层候选（merged 模式）：注入分类提示词的临时标签；命中输出 overlays
        self.set_overlay_items(overlay_items or [])

    def set_overlay_items(self, items):
        """设置/更新叠层候选清单（participating_items 输出）。窗口运行前调用一次。"""
        self.overlay_items = list(items)
        self.overlay_labels = build_overlay_labels(self.overlay_items) or None
        self._overlay_names = {it.get("name_zh", "") for it in self.overlay_items}

    def _ensure_merged_tax(self):
        if self._skill_tax is None:
            import taxonomy as tax
            self._skill_tax = tax.load("skill")
            self._task_tax = tax.load("task")

    # ---------- 核心分类（JD 句子 / 论文文本片段通用） ----------
    def _classify_units(self, units, taxonomy, prompt_template=None):
        """对去重后的文本单元分类，返回 (results, aggregated)。

        results: {unit: [{"code", "skillpoints"}]}（含缓存命中；paper_mention 用它取证据）
        aggregated: {"skill_counts", "skillpoint_counts", "skill_skillpoint_map"}
        按单元出现频次计数（含重复单元）。
        prompt_template：自定义分类提示词（含 {labels}/{sentences} 占位符）；缺省用 JD 模板。
        """
        if self.mode == "merged":
            return self._classify_merged(units)
        unique = text_split.dedupe_preserve_order(units)

        # 1) 缓存查找
        to_llm = []
        results = {}  # unit -> [{"code", "skillpoints"}]
        for s in unique:
            if self.cache:
                cached = self.cache.get(s)
                if cached is not None:
                    results[s] = cached
                    continue
            to_llm.append(s)

        # 2) LLM 分类未命中单元
        if to_llm:
            if prompt_template is not None:
                llm_result = self.llm.classify_with(to_llm, taxonomy, prompt_template)
            else:
                llm_result = self.llm.classify_sentences(to_llm, taxonomy)
            for s, matches in llm_result.items():
                results[s] = matches
                if self.cache:
                    self.cache.set(s, matches)
            # 未返回结果的单元视为空
            for s in to_llm:
                if s not in results:
                    results[s] = []

        # 3) 聚合（按单元出现频次）
        skill_counter = Counter()          # 技能/任务 → 句频
        sp_counter = Counter()             # 技能点 → 句频
        sp_map = defaultdict(Counter)      # 技能码 → {技能点: 句频}

        for s in units:  # 用原单元（含重复）计数
            for m in results.get(s, []):
                code = m.get("code")
                if not code:
                    continue
                skill_counter[code] += 1
                for sp in m.get("skillpoints", []):
                    sp_counter[sp] += 1
                    sp_map[code][sp] += 1

        aggregated = {
            "skill_counts": dict(skill_counter),
            "skillpoint_counts": dict(sp_counter),
            "skill_skillpoint_map": {c: dict(cnt) for c, cnt in sp_map.items()},
        }
        return results, aggregated

    # ---------- 合并模式（一句一次：技能+技能点+任务+叠层候选） ----------
    def _classify_merged(self, units):
        """逐句一次分类技能(含技能点)+任务+叠层候选 → (results, aggregated)。

        results: {unit: {"skills": [{"code","skillpoints"}], "tasks": [code],
                         "overlays": [名称, ...]}}（旧缓存条目无 overlays 键，视为 []）
        aggregated: {"skill_counts","task_counts","skillpoint_counts","skill_skillpoint_map",
                     "overlay_counts"}（按单元出现频次计数，含重复）。
        叠层命中只进确证证据流（overlays/overlay_counts），不进基图 skill/task 计数。
        """
        self._ensure_merged_tax()
        unique = text_split.dedupe_preserve_order(units)
        to_llm, results = [], {}
        for s in unique:
            if self.cache:
                cached = self.cache.get(s)
                if cached is not None:
                    if "overlays" not in cached:
                        cached = dict(cached, overlays=[])
                    results[s] = cached
                    continue
            to_llm.append(s)
        if to_llm:
            llm_result = self.llm.classify_merged(to_llm, self._skill_tax, self._task_tax,
                                                  overlay_labels=getattr(self, "overlay_labels", None))
            for s, m in llm_result.items():
                results[s] = m
                if self.cache:
                    self.cache.set(s, m)
            for s in to_llm:
                if s not in results:
                    results[s] = {"skills": [], "tasks": [], "overlays": []}
        skill_c, task_c, sp_c = Counter(), Counter(), Counter()
        ov_c = Counter()
        sp_map = defaultdict(Counter)
        for s in units:
            m = results.get(s) or {"skills": [], "tasks": [], "overlays": []}
            for sk in m.get("skills", []):
                code = sk.get("code")
                if not code:
                    continue
                skill_c[code] += 1
                for sp in sk.get("skillpoints", []):
                    sp_c[sp] += 1
                    sp_map[code][sp] += 1
            for c in m.get("tasks", []):
                task_c[c] += 1
            for nm in m.get("overlays", []):
                if nm in self._overlay_names:
                    ov_c[nm] += 1
        aggregated = {
            "skill_counts": dict(skill_c),
            "task_counts": dict(task_c),
            "skillpoint_counts": dict(sp_c),
            "skill_skillpoint_map": {c: dict(cnt) for c, cnt in sp_map.items()},
            "overlay_counts": dict(ov_c),
        }
        return results, aggregated

    # ---------- 单条 JD ----------
    def extract(self, jd_text, taxonomy=None):
        """从一条 JD 提取技能/任务及其技能点。

        返回结构：
          skill/task 模式：{"skill_counts": {code: n}, "skillpoint_counts": {sp: n},
                            "skill_skillpoint_map": {code: {sp: n}}}（task 模式 skillpoint 相关为空）
          merged 模式：上述 + "task_counts": {code: n}（一句一次出技能+技能点+任务）
        merged 模式 taxonomy 参数忽略（两套体系在 _classify_merged 内惰性加载）。
        """
        sentences = text_split.split_sentences(jd_text)
        _, aggregated = self._classify_units(sentences, taxonomy)
        return aggregated

    # ---------- 批量 JD ----------
    def extract_many(self, jd_list, taxonomy):
        """批量提取。jd_list: [text, ...]；返回 [result, ...]"""
        return [self.extract(jd, taxonomy) for jd in jd_list]

    # ---------- 统计 ----------
    def stats(self):
        s = {"llm": self.llm.stats()}
        if self.cache:
            s["cache"] = self.cache.stats()
        return s
