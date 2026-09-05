# -*- coding: utf-8 -*-
"""论文提及识别：识别论文文本对**既有**技能/任务/岗位的提及（分类抽取层）。

与「新信号发现」（signal_extractor，找新事物）互补：本模块回答"哪些已有能力被论文讨论到"，
产出 per-paper 提及频次 + 证据 + 跨论文聚合，供 ΔG 增量层 strengthenings 与演化分析消费。

复用 Extractor 的分句/缓存/LLM 设施：
- 提单元：标题 + 关键词（各作一个单元）+ 摘要分句 + 证据句
- 分类：按 mode（skill|task|job）用论文版提示词（paper_prompts.get_paper_prompt），复用句级缓存
- 聚合：每篇论文 {code: 单元频次} + evidence（命中的原文单元）；跨论文聚合 {code: 提及论文数}
"""
import text_split
from extractor import Extractor
from paper_prompts import get_paper_prompt

VALID_MODES = ("skill", "task", "job")


class PaperMentionExtractor(Extractor):
    """论文提及识别器（Extractor 子类，复用 llm / cache / 分句设施）。"""

    def __init__(self, mode="skill", llm_client=None, use_cache=True):
        if mode not in VALID_MODES:
            raise ValueError(f"未知 mode: {mode}（应为 skill / task / job）")
        super().__init__(mode=mode, llm_client=llm_client, use_cache=use_cache)

    # ---------- 提单元构建 ----------
    def _paper_units(self, paper):
        """把论文精髓信息拆成提单元（标题/关键词/摘要句/证据句）。"""
        units = []
        title = getattr(paper, "title", "") or ""
        if title:
            units.append("标题：" + title)
        keywords = getattr(paper, "keywords", None) or []
        if keywords:
            units.append("关键词：" + "; ".join(keywords))
        abstract = getattr(paper, "abstract", "") or ""
        if abstract:
            units.extend(text_split.split_sentences(abstract))
        for ev in getattr(paper, "evidence_sentences", None) or []:
            t = (ev.get("text") if isinstance(ev, dict) else str(ev)).strip()
            if t:
                units.append(t)
        return text_split.dedupe_preserve_order([u for u in units if u])

    # ---------- 单篇论文提及 ----------
    def extract_paper(self, paper, taxonomy):
        """单篇论文 → {"mentions": {code: 单元频次}, "evidence": {code: [原文单元]}, "skillpoints": {sp: 频次}}"""
        units = self._paper_units(paper)
        results, agg = self._classify_units(units, taxonomy,
                                            prompt_template=get_paper_prompt(self.mode))
        evidence = {}
        for u, matches in results.items():
            for m in matches:
                code = m.get("code")
                if not code:
                    continue
                evidence.setdefault(code, [])
                if u not in evidence[code]:
                    evidence[code].append(u)
        return {
            "mentions": agg["skill_counts"],
            "evidence": evidence,
            "skillpoints": agg["skillpoint_counts"],
        }

    # ---------- 批量 ----------
    def extract_papers(self, papers, taxonomy):
        """批量。papers: list[PaperRecord]；返回 [per_paper, ...]"""
        return [self.extract_paper(p, taxonomy) for p in papers]
