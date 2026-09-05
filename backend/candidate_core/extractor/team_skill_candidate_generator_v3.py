"""Recall-oriented candidate generation for Team Skill verification."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Sequence

from extractor.agentic_schema import CandidateAbility
from extractor.team_skill_registry import RankedTeamSkill, TeamSkill, TeamSkillRegistry


_AI01_DEVELOPMENT_MARKERS = (
    "训练", "微调", "调优", "模型优化", "模型设计", "损失函数", "网络结构",
    "深度模型", "模型评估", "语义分割模型",
)
_AI01_WEAK_CONTEXT_MARKERS = (
    "熟悉", "擅长", "掌握", "研究方向", "研究点", "课题研究", "研究并撰写",
    "数据集生成算法",
)
_SW01_WEAK_CONTEXT_MARKERS = (
    "基于现有代码框架", "团务", "共青团", "场地申请", "设备维护", "特征的清洗",
)
_SW01_DIRECT_DELIVERY_RE = re.compile(
    r"(开发|实现|编程|编码|程序设计|模块.{0,8}(开发|维护)|"
    r"接口.{0,8}(开发|适配)|bug\s*修复|软件.{0,8}(设计|测试|调试))",
    re.IGNORECASE,
)
_F303_ADMIN_MARKERS = ("场地申请", "设备维护", "团务", "共青团", "校级活动", "校园活动")
_F303_PLANNING_MARKERS = (
    "排期", "计划", "统筹", "分配", "协调", "资源", "进度", "优先级", "预算", "人力",
)
_DA01_WEAK_CONTEXT_MARKERS = ("擅长方向", "研究方向", "系统建模", "路径规划")
_DA01_QUANTITATIVE_MARKERS = (
    "数学", "统计", "回归", "方程", "求解", "目标函数", "优化模型", "数值", "概率",
    "矩阵", "损失函数", "样本挖掘", "软间隔",
)


@dataclass(frozen=True)
class TeamSkillCandidatePool:
    skills: tuple[TeamSkill, ...]
    ranked: tuple[RankedTeamSkill, ...]
    fallback_all: bool
    retrieval_text: str
    located_evidence_count: int


class TeamSkillCandidateGeneratorV3:
    """Build a candidate set; never decides whether a skill is supported.

    Located resume spans are the strongest retrieval material. Generated fact,
    behavior and ability_hint may expand recall, but remain retrieval-only hints.
    """

    def __init__(self, registry: TeamSkillRegistry) -> None:
        self.registry = registry

    @staticmethod
    def _grounded_text(evidence_candidate: CandidateAbility) -> str:
        return "\n".join(
            item.text
            for item in evidence_candidate.evidence
            if item.start is not None and item.end is not None
        )

    def allows_skill(self, evidence_candidate: CandidateAbility, skill: TeamSkill) -> bool:
        """Apply narrow source/context eligibility before semantic verification."""
        text = self._grounded_text(evidence_candidate)
        if skill.code == "T-AI-01":
            if (
                any(marker in text for marker in _AI01_WEAK_CONTEXT_MARKERS)
                and not any(marker in text for marker in _AI01_DEVELOPMENT_MARKERS)
            ):
                return False
        elif skill.code == "T-SW-01":
            if (
                any(marker in text for marker in _SW01_WEAK_CONTEXT_MARKERS)
                and not _SW01_DIRECT_DELIVERY_RE.search(text)
            ):
                return False
        elif skill.code == "F-3-03":
            if (
                any(marker in text for marker in _F303_ADMIN_MARKERS)
                and not any(marker in text for marker in _F303_PLANNING_MARKERS)
            ):
                return False
        elif skill.code == "T-DA-01":
            if (
                any(marker in text for marker in _DA01_WEAK_CONTEXT_MARKERS)
                and not any(marker in text for marker in _DA01_QUANTITATIVE_MARKERS)
            ):
                return False
        return True

    @staticmethod
    def _deterministic_recall_ids(evidence_texts: Sequence[str] | str) -> tuple[str, ...]:
        units = (
            (evidence_texts,)
            if isinstance(evidence_texts, str)
            else tuple(text for text in evidence_texts if text and text.strip())
        )

        def matches(predicate) -> bool:
            return any(predicate(text) for text in units)

        def software_implementation(text: str) -> bool:
            return bool(re.search(
                r"(代码|程序|软件|模块|框架).{0,24}(实现|开发|构建|编程)|"
                r"(实现|开发|构建|编程).{0,24}(代码|程序|软件|模块|框架)",
                text,
            ))

        def algorithm_method_behavior(text: str) -> bool:
            direct_algorithm = (
                "算法" in text
                and any(marker in text for marker in ("设计", "实现", "提出", "开发", "构建"))
            )
            method_research = (
                any(marker in text for marker in ("优化方法", "进化优化", "求解方法"))
                and any(marker in text for marker in ("研究", "设计", "实现", "提出", "开发", "构建"))
            )
            return direct_algorithm or method_research

        rules = (
            ("T-SW-01", matches(software_implementation)),
            ("F-3-01", matches(lambda text: "迭代" in text and any(marker in text for marker in ("总结", "复盘", "经验", "反思")))),
            ("T-SW-04", matches(lambda text: any(marker in text for marker in ("评价实验", "测试验证", "验证测试", "性能测试", "质量评估")))),
            ("T-SW-05", matches(lambda text: "交互" in text and any(marker in text for marker in ("功能", "建立", "设计", "开发", "实现")))),
            ("T-DA-02", matches(lambda text: any(marker in text for marker in ("数据库", "知识库", "数据集", "集合")) and any(marker in text for marker in ("创建", "插入", "导入", "导出", "查询", "管理", "增删改查")))),
            ("T-SW-02", matches(algorithm_method_behavior)),
            ("F-3-03", matches(lambda text: any(marker in text for marker in ("团队", "项目", "人员", "受试者", "客户", "部门")) and any(marker in text for marker in ("管理", "协调", "统筹", "计划", "推进", "排期")) and any(marker in text for marker in ("进度", "汇报", "总结", "交付", "资源")))),
            ("T-AI-05", matches(lambda text: any(marker.casefold() in text.casefold() for marker in ("ICL", "few-shot", "zero-shot", "提示学习", "提示设计", "提示词")))),
        )
        return tuple(skill_id for skill_id, matched in rules if matched)

    def generate(
        self,
        evidence_candidate: CandidateAbility,
        *,
        top_k: int = 8,
        semantic_scores: Mapping[str, float] | None = None,
        include_auxiliary: bool = False,
        recall_safe_fallback: bool = True,
    ) -> TeamSkillCandidatePool:
        located_texts = [
            item.text for item in evidence_candidate.evidence
            if item.start is not None and item.end is not None
        ]
        # ability/fact/behavior are intentionally retrieval-only hints, never evidence.
        parts = [
            *located_texts,
            evidence_candidate.fact,
            evidence_candidate.behavior,
            evidence_candidate.ability,
        ]
        retrieval_text = "\n".join(part for part in parts if part and part.strip())
        lexical_ranked = self.registry.rank_lexically(
            retrieval_text,
            top_k=None,
            include_auxiliary=include_auxiliary,
            semantic_scores=semantic_scores,
        )
        lexical_by_id = {
            item.skill.code: item
            for item in lexical_ranked
            if self.allows_skill(evidence_candidate, item.skill)
        }
        ordered: list[RankedTeamSkill] = []
        seen: set[str] = set()
        for skill_id in self._deterministic_recall_ids(located_texts):
            skill = self.registry.get(skill_id)
            if skill.code in seen or not self.allows_skill(evidence_candidate, skill):
                continue
            item = lexical_by_id.get(skill.code)
            ordered.append(
                item
                if item is not None
                else RankedTeamSkill(
                    skill=skill,
                    lexical_score=3.0,
                    semantic_score=None,
                    combined_score=3.0,
                    matched_phrases=("deterministic_context_recall",),
                )
            )
            seen.add(skill.code)
        for item in lexical_by_id.values():
            if item.skill.code in seen:
                continue
            ordered.append(item)
            seen.add(item.skill.code)
        ranked = tuple(ordered[:top_k])
        if ranked:
            return TeamSkillCandidatePool(
                skills=tuple(item.skill for item in ranked),
                ranked=ranked,
                fallback_all=False,
                retrieval_text=retrieval_text,
                located_evidence_count=len(located_texts),
            )
        if not recall_safe_fallback:
            return TeamSkillCandidatePool(
                (), (), False, retrieval_text, len(located_texts)
            )
        skills = self.registry.all() if include_auxiliary else self.registry.primary()
        return TeamSkillCandidatePool(
            skills=skills,
            ranked=(),
            fallback_all=True,
            retrieval_text=retrieval_text,
            located_evidence_count=len(located_texts),
        )
