"""High-yield deterministic risk flags for proficiency assessments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from extractor.agentic_schema import Evidence


_TECHNOLOGY_PATTERN = re.compile(
    r"\b(?:LLM|Transformer|LoRA|Diffusion|YOLO)\b|大语言模型|大模型|强化学习",
    re.IGNORECASE,
)
_BASIC_ACTION_PATTERN = re.compile(
    r"完成|执行|运行|训练模型|模型训练|调参|构建|实现|开发|清洗|标注|"
    r"评估|验证|部署|选择|设计|比较|诊断|优化|迭代"
)
_JUDGMENT_PATTERN = re.compile(
    r"比较|对比|权衡|诊断|定位.{0,12}(问题|原因|瓶颈)|优化|迭代|"
    r"方法设计|设计.{0,12}(方法|方案|策略)|策略调整|调整.{0,12}策略|"
    r"非例行|提出|解决.{0,16}(复杂|局限|瓶颈|难题)"
)
_COMPLEXITY_PATTERN = re.compile(
    r"高度复杂|复杂|模糊|跨系统|大规模|多约束|非例行|关键瓶颈"
)
_LEADERSHIP_PATTERN = re.compile(r"主导|主持|牵头|带领|指导|负责制定")
_STRATEGY_PATTERN = re.compile(
    r"制定|创新|新方法|关键.{0,8}策略|方法设计|架构设计|提出"
)
_IMPACT_PATTERN = re.compile(
    r"系统级|项目级|重大影响|上线|落地|行业首个|开创|"
    r"(?:提升|降低|缩短|减少|增长).{0,12}(?:%|％|倍|个百分点|分钟|小时)"
)
_BACKGROUND_PATTERN = re.compile(
    r"学校|大学|名校|公司品牌|知名公司|职称|工作年限|多年经验|证书|精通"
)


@dataclass(frozen=True)
class ValidationOutcome:
    review_required: bool
    flags: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_required": self.review_required,
            "flags": list(self.flags),
        }


class ProficiencyValidator:
    """Flag likely over-inference without changing the model's level."""

    def validate(
        self,
        result: Mapping[str, Any],
        evidence: Sequence[Evidence],
    ) -> ValidationOutcome:
        if not isinstance(result, Mapping):
            raise TypeError("result must be a mapping")
        if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
            raise TypeError("evidence must be a sequence of Evidence")
        if not evidence or any(not isinstance(item, Evidence) for item in evidence):
            raise ValueError("evidence must contain at least one Evidence object")

        evidence_text = " ".join(item.text for item in evidence)
        final_level = result.get("final_level")
        sufficiency = result.get("evidence_sufficiency")
        reason = result.get("reason", "")
        if not isinstance(reason, str):
            reason = ""

        flags: list[str] = []
        if sufficiency == "insufficient" and final_level != "U":
            flags.append("insufficient_evidence_level_conflict")

        if final_level == "P1" and not _BASIC_ACTION_PATTERN.search(evidence_text):
            flags.append("p1_without_observed_basic_behavior")

        has_judgment = bool(_JUDGMENT_PATTERN.search(evidence_text))
        has_impact = bool(_IMPACT_PATTERN.search(evidence_text))
        has_complexity = bool(_COMPLEXITY_PATTERN.search(evidence_text))
        has_leadership = bool(_LEADERSHIP_PATTERN.search(evidence_text))
        has_strategy = bool(_STRATEGY_PATTERN.search(evidence_text))

        if final_level == "P3" and not has_judgment:
            flags.append("p3_without_technical_judgment")

        if final_level in {"P3", "P4"}:
            if (
                _TECHNOLOGY_PATTERN.search(evidence_text)
                and not any((has_judgment, has_leadership, has_impact))
            ):
                flags.append("technology_name_inflation")
            if "负责" in evidence_text and not any(
                (has_judgment, has_complexity, has_impact)
            ):
                flags.append("responsibility_wording_inflation")
            if _BACKGROUND_PATTERN.search(reason):
                flags.append("background_signal_reliance")

        if final_level == "P4":
            p4_signal_count = sum(
                (has_leadership, has_complexity, has_strategy, has_impact)
            )
            if p4_signal_count < 3 or not has_impact or not (
                has_strategy or has_judgment
            ):
                flags.append("p4_insufficient_high_level_signals")

        unique_flags = tuple(dict.fromkeys(flags))
        return ValidationOutcome(bool(unique_flags), unique_flags)
