"""On-demand adapter from frozen Team Skill profiles to proficiency inputs.

This module performs no model or API calls.  It only exposes supported direct
Team Skills with exact grounded evidence through the existing proficiency input
contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from extractor.agentic_schema import CandidateAbility, CandidateStatus, Evidence
from extractor.review_assessment_schema import EvidenceAuditResult
from extractor.team_skill_schema_v3 import (
    CandidateSkillProfile,
    TeamSkillAssessment,
)


_SUPPORTED_WARRANT_FLAG = "supported_warrant"


@dataclass(frozen=True)
class ProficiencyEvaluatorInput:
    """Validated arguments for one existing ``ProficiencyEvaluator.evaluate`` call."""

    team_skill_id: str
    team_skill_name: str
    ability: CandidateAbility
    evidence: tuple[Evidence, ...]
    audit_result: EvidenceAuditResult

    def evaluator_args(
        self,
    ) -> tuple[CandidateAbility, tuple[Evidence, ...], EvidenceAuditResult]:
        return self.ability, self.evidence, self.audit_result


def is_proficiency_assessable(assessment: TeamSkillAssessment) -> bool:
    """Return whether a frozen Team Skill assessment may enter proficiency."""

    if not isinstance(assessment, TeamSkillAssessment):
        raise TypeError("assessment must be TeamSkillAssessment")
    return (
        assessment.status == "supported"
        and assessment.inference_mode == "direct_behavior"
        and bool(assessment.evidence)
        and all(item.start is not None and item.end is not None for item in assessment.evidence)
        and _SUPPORTED_WARRANT_FLAG not in {
            flag.strip().casefold() for flag in assessment.audit_flags
        }
    )


def build_proficiency_evaluator_inputs(
    profile: CandidateSkillProfile,
    target_team_skill_ids: Sequence[str] | None = None,
) -> tuple[ProficiencyEvaluatorInput, ...]:
    """Build on-demand evaluator inputs for eligible Team Skills in profile order."""

    if not isinstance(profile, CandidateSkillProfile):
        raise TypeError("profile must be CandidateSkillProfile")
    if not isinstance(profile.candidate_id, str) or not profile.candidate_id.strip():
        raise ValueError("profile.candidate_id must be non-empty")

    assessments_by_id: dict[str, TeamSkillAssessment] = {}
    for assessment in profile.assessments:
        if assessment.candidate_id != profile.candidate_id:
            raise ValueError("assessment candidate_id must match profile.candidate_id")
        if assessment.team_skill_id in assessments_by_id:
            raise ValueError("profile assessments must have unique team_skill_id values")
        assessments_by_id[assessment.team_skill_id] = assessment

    requested: frozenset[str] | None = None
    if target_team_skill_ids is not None:
        if isinstance(target_team_skill_ids, (str, bytes)) or not isinstance(
            target_team_skill_ids, Sequence
        ):
            raise TypeError("target_team_skill_ids must be a sequence of strings or None")
        normalized = []
        for index, value in enumerate(target_team_skill_ids):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"target_team_skill_ids[{index}] must be a non-empty string"
                )
            skill_id = value.strip()
            if skill_id in normalized:
                raise ValueError("target_team_skill_ids must not contain duplicates")
            normalized.append(skill_id)
        unknown = sorted(set(normalized) - set(assessments_by_id))
        if unknown:
            raise ValueError("unknown target_team_skill_ids: " + ", ".join(unknown))
        requested = frozenset(normalized)

    results = []
    for assessment in profile.assessments:
        if requested is not None and assessment.team_skill_id not in requested:
            continue
        if not is_proficiency_assessable(assessment):
            continue
        results.append(_to_evaluator_input(profile, assessment))
    return tuple(results)


def _to_evaluator_input(
    profile: CandidateSkillProfile,
    assessment: TeamSkillAssessment,
) -> ProficiencyEvaluatorInput:
    evidence = tuple(
        Evidence(
            text=item.text,
            project_id=item.source_experience_id,
            start=item.start,
            end=item.end,
        )
        for item in assessment.evidence
    )
    bridge_id = f"{profile.candidate_id}::{assessment.team_skill_id}"
    ability = CandidateAbility(
        candidate_id=bridge_id,
        resume_id=profile.candidate_id,
        project_id=evidence[0].project_id,
        fact=evidence[0].text,
        behavior=evidence[0].text,
        ability=assessment.team_skill_name,
        normalized_ability=assessment.team_skill_name,
        category={
            "ability_id": assessment.team_skill_id,
            "team_skill_id": assessment.team_skill_id,
        },
        evidence=list(evidence),
        reason="frozen direct Team Skill support with grounded evidence",
        confidence=assessment.confidence if assessment.confidence is not None else 1.0,
        source="team_skill_proficiency_bridge_v434",
        revision_round=0,
        parent_candidate_id=None,
        status=CandidateStatus.APPROVED,
        lineage=[bridge_id],
    )
    audit_result = EvidenceAuditResult.from_dict(
        {
            "schema_version": "evidence_audit_result_v1",
            "resume_id": ability.resume_id,
            "candidate_id": ability.candidate_id,
            "current_evidence_audits": [
                {
                    "evidence_index": index,
                    "text": item.text,
                    "start": item.start,
                    "end": item.end,
                    "project_id": item.project_id,
                    "exactness_status": "exact",
                    "matched_catalog_span_id": (
                        f"{assessment.team_skill_id}:{index}:{item.start}:{item.end}"
                    ),
                    "issues": [],
                }
                for index, item in enumerate(evidence)
            ],
            "taxonomy_subset_ids": [],
            "taxonomy_selection_trace": [],
            "component_assessments": [],
            "evidence_decision": "sufficient",
            "recommended_relocation_span_ids": [],
            "compound_label": "not_compound",
            "blocking_issues": [],
            "non_blocking_notes": [
                "frozen direct Team Skill support confirmed upstream"
            ],
            "requires_model_review": False,
            "diagnostics": {
                "source": "team_skill_proficiency_bridge_v434",
                "team_skill_id": assessment.team_skill_id,
            },
        }
    )
    return ProficiencyEvaluatorInput(
        team_skill_id=assessment.team_skill_id,
        team_skill_name=assessment.team_skill_name,
        ability=ability,
        evidence=evidence,
        audit_result=audit_result,
    )
