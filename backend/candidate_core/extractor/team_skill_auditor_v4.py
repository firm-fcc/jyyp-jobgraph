"""V4 deterministic evidence auditor.

V4 keeps the auditor strictly provenance-oriented: it validates that every
positive support quote is traceable to a located resume span and enforces the
cross-context rule for aggregate signals. It deliberately does *not* inspect
LLM-generated fact/behavior hints, because those annotations are non-authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass

from extractor.agentic_schema import CandidateAbility
from extractor.team_skill_registry import TeamSkillRegistry
from extractor.team_skill_verifier_v4 import ModelTeamSkillAssessment



_AI01_APPLICATION_MARKERS = (
    "使用AI模型", "利用AI模型", "调用AI模型", "通过AI模型", "应用AI模型",
)
_AI01_DEVELOPMENT_MARKERS = (
    "训练", "微调", "调优", "模型优化", "模型设计", "算法设计", "损失函数",
    "机器学习", "深度学习", "神经网络", "特征工程", "模型评估", "建模",
)

@dataclass(frozen=True)
class AuditedTeamSkillAssessmentV4:
    team_skill_id: str
    model_status: str
    final_status: str
    support_evidence: tuple[str, ...]
    atomic_ability: str | None
    confidence: float
    reason: str
    audit_passed: bool
    audit_flags: tuple[str, ...]
    source_candidate_ability_id: str
    source_experience_id: str


class TeamSkillAuditorV4:
    def __init__(self, registry: TeamSkillRegistry) -> None:
        self.registry = registry

    @staticmethod
    def _quote_matches(quote: str, evidence_text: str) -> bool:
        return quote in evidence_text

    def audit(
        self,
        evidence_candidate: CandidateAbility,
        assessment: ModelTeamSkillAssessment,
    ) -> AuditedTeamSkillAssessmentV4:
        skill = self.registry.get(assessment.team_skill_id)
        flags: list[str] = []
        all_evidence = tuple(evidence_candidate.evidence)
        located_evidence = tuple(
            item for item in all_evidence
            if item.start is not None and item.end is not None
        )

        for quote in assessment.support_evidence:
            found_anywhere = any(self._quote_matches(quote, item.text) for item in all_evidence)
            found_located = any(self._quote_matches(quote, item.text) for item in located_evidence)
            if not found_anywhere:
                flags.append("support_evidence_not_found")
            elif not found_located:
                flags.append("support_evidence_unlocated")

        if assessment.status in {"supported", "partially_supported"} and not assessment.support_evidence:
            flags.append("missing_support_evidence")
        if assessment.status in {"supported", "partially_supported"} and not located_evidence:
            flags.append("no_located_resume_evidence")
        if skill.inference_mode == "aggregate_signal" and assessment.status == "supported":
            flags.append("aggregate_signal_requires_cross_context_aggregation")

        if assessment.team_skill_id == "T-AI-01" and assessment.status == "supported":
            support_blob = "\n".join(assessment.support_evidence)
            application_only = any(marker in support_blob for marker in _AI01_APPLICATION_MARKERS)
            development_signal = any(marker in support_blob for marker in _AI01_DEVELOPMENT_MARKERS)
            if application_only and not development_signal:
                flags.append("ai01_application_only_not_ml_dl_evidence")

        grounding_failures = {
            "support_evidence_not_found",
            "support_evidence_unlocated",
            "missing_support_evidence",
            "no_located_resume_evidence",
        }
        final_status = assessment.status
        if grounding_failures.intersection(flags):
            final_status = "unsupported"
        elif "ai01_application_only_not_ml_dl_evidence" in flags:
            final_status = "unsupported"
        elif skill.inference_mode == "aggregate_signal" and assessment.status == "supported":
            final_status = "partially_supported"

        return AuditedTeamSkillAssessmentV4(
            team_skill_id=assessment.team_skill_id,
            model_status=assessment.status,
            final_status=final_status,
            support_evidence=assessment.support_evidence,
            atomic_ability=assessment.atomic_ability if final_status != "unsupported" else None,
            confidence=assessment.confidence,
            reason=assessment.reason,
            audit_passed=not flags,
            audit_flags=tuple(dict.fromkeys(flags)),
            source_candidate_ability_id=evidence_candidate.candidate_id,
            source_experience_id=evidence_candidate.project_id,
        )
