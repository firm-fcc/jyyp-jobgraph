"""Deterministic audit for V3 Team Skill verification results.

The auditor is the hard evidence gate. A positive semantic judgment is never
allowed to survive unless every cited support span can be traced to a located
resume span (start/end are both present).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from extractor.agentic_schema import CandidateAbility
from extractor.team_skill_registry import TeamSkillRegistry
from extractor.team_skill_verifier_v3 import ModelTeamSkillAssessment


@dataclass(frozen=True)
class AuditedTeamSkillAssessment:
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


class TeamSkillAuditorV3:
    """Rule gate that never calls a model and preserves model_status separately."""

    def __init__(self, registry: TeamSkillRegistry) -> None:
        self.registry = registry

    @staticmethod
    def _quote_matches(quote: str, evidence_text: str) -> bool:
        return quote in evidence_text

    def audit(
        self,
        evidence_candidate: CandidateAbility,
        assessment: ModelTeamSkillAssessment,
    ) -> AuditedTeamSkillAssessment:
        skill = self.registry.get(assessment.team_skill_id)
        flags: list[str] = []
        all_evidence = tuple(evidence_candidate.evidence)
        located_evidence = tuple(
            item for item in all_evidence
            if item.start is not None and item.end is not None
        )

        for quote in assessment.support_evidence:
            found_anywhere = any(
                self._quote_matches(quote, item.text) for item in all_evidence
            )
            found_located = any(
                self._quote_matches(quote, item.text) for item in located_evidence
            )
            if not found_anywhere:
                flags.append("support_evidence_not_found")
            elif not found_located:
                flags.append("support_evidence_unlocated")

        if (
            assessment.status in {"supported", "partially_supported"}
            and not assessment.support_evidence
        ):
            flags.append("missing_support_evidence")

        if (
            assessment.status in {"supported", "partially_supported"}
            and not located_evidence
        ):
            flags.append("no_located_resume_evidence")

        # A single evidence unit cannot establish a cross-context disposition.
        if skill.inference_mode == "aggregate_signal" and assessment.status == "supported":
            flags.append("aggregate_signal_requires_cross_context_aggregation")

        if assessment.status == "supported" and not evidence_candidate.behavior.strip():
            flags.append("missing_observable_behavior")

        grounding_failures = {
            "support_evidence_not_found",
            "support_evidence_unlocated",
            "missing_support_evidence",
            "no_located_resume_evidence",
        }
        final_status = assessment.status
        if grounding_failures.intersection(flags):
            final_status = "unsupported"
        elif skill.inference_mode == "aggregate_signal" and assessment.status == "supported":
            final_status = "partially_supported"
        elif "missing_observable_behavior" in flags and assessment.status == "supported":
            final_status = "partially_supported"

        return AuditedTeamSkillAssessment(
            team_skill_id=assessment.team_skill_id,
            model_status=assessment.status,
            final_status=final_status,
            support_evidence=assessment.support_evidence,
            atomic_ability=(
                assessment.atomic_ability if final_status != "unsupported" else None
            ),
            confidence=assessment.confidence,
            reason=assessment.reason,
            audit_passed=not flags,
            audit_flags=tuple(dict.fromkeys(flags)),
            source_candidate_ability_id=evidence_candidate.candidate_id,
            source_experience_id=evidence_candidate.project_id,
        )


@dataclass(frozen=True)
class AggregatedTeamSkill:
    team_skill_id: str
    final_status: str
    source_candidate_ability_ids: tuple[str, ...]
    source_experience_ids: tuple[str, ...]
    support_evidence: tuple[str, ...]
    atomic_abilities: tuple[str, ...]
    audit_flags: tuple[str, ...]
    confidence: float | None


def aggregate_team_skill_assessments(
    assessments: Sequence[AuditedTeamSkillAssessment],
    registry: TeamSkillRegistry,
) -> tuple[AggregatedTeamSkill, ...]:
    """Aggregate evidence-unit judgments into candidate-level Team Skill results.

    Direct-behavior skills need one supported grounded evidence unit. Aggregate
    signals require positive evidence from at least two distinct source
    experiences, not merely two generated candidate IDs from the same project.
    """
    grouped: dict[str, list[AuditedTeamSkillAssessment]] = {}
    for item in assessments:
        registry.get(item.team_skill_id)
        grouped.setdefault(item.team_skill_id, []).append(item)

    result: list[AggregatedTeamSkill] = []
    for skill_id, items in sorted(grouped.items()):
        skill = registry.get(skill_id)
        supported = [item for item in items if item.final_status == "supported"]
        partial = [item for item in items if item.final_status == "partially_supported"]
        if skill.inference_mode == "aggregate_signal":
            positive_units = partial + supported
            unique_experiences = {
                item.source_experience_id
                for item in positive_units
                if item.source_experience_id.strip()
            }
            final_status = (
                "supported" if len(unique_experiences) >= 2
                else "partially_supported" if positive_units
                else "unsupported"
            )
        else:
            final_status = (
                "supported" if supported
                else "partially_supported" if partial
                else "unsupported"
            )

        positive_items = [item for item in items if item.final_status != "unsupported"]
        confidence = (
            max((item.confidence for item in positive_items), default=None)
            if positive_items else None
        )
        result.append(
            AggregatedTeamSkill(
                team_skill_id=skill_id,
                final_status=final_status,
                source_candidate_ability_ids=tuple(dict.fromkeys(
                    item.source_candidate_ability_id for item in positive_items
                )),
                source_experience_ids=tuple(dict.fromkeys(
                    item.source_experience_id for item in positive_items
                )),
                support_evidence=tuple(dict.fromkeys(
                    quote for item in positive_items for quote in item.support_evidence
                )),
                atomic_abilities=tuple(dict.fromkeys(
                    item.atomic_ability for item in positive_items if item.atomic_ability
                )),
                audit_flags=tuple(dict.fromkeys(
                    flag for item in items for flag in item.audit_flags
                )),
                confidence=confidence,
            )
        )
    return tuple(result)
