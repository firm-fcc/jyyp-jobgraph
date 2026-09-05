"""Build the final candidate-level V3 profile from audited evidence decisions."""

from __future__ import annotations

from collections import defaultdict
from typing import Mapping, Sequence

from extractor.agentic_schema import CandidateAbility
from extractor.team_skill_auditor_v3 import (
    AuditedTeamSkillAssessment,
    aggregate_team_skill_assessments,
)
from extractor.team_skill_registry import TeamSkillRegistry
from extractor.team_skill_schema_v3 import (
    CandidateSkillProfile,
    EvidenceObservation,
    TeamSkillAssessment,
)


def _observation_from_quote(candidate: CandidateAbility, quote: str) -> EvidenceObservation:
    for evidence in candidate.evidence:
        if evidence.start is None or evidence.end is None:
            continue
        index = evidence.text.find(quote)
        if index < 0:
            continue
        start = evidence.start + index
        end = start + len(quote)
        return EvidenceObservation(
            text=quote,
            source_experience_id=candidate.project_id,
            start=start,
            end=end,
            fact=candidate.fact,
            behavior=candidate.behavior,
            context=candidate.project_id,
            result="",
        )
    raise ValueError(
        "positive audited support_evidence cannot be mapped to a located resume span"
    )


def build_candidate_skill_profile(
    *,
    candidate_id: str,
    evidence_candidates: Sequence[CandidateAbility],
    audited_assessments: Sequence[AuditedTeamSkillAssessment],
    registry: TeamSkillRegistry,
    metadata: Mapping[str, object] | None = None,
) -> CandidateSkillProfile:
    source_by_id = {candidate.candidate_id: candidate for candidate in evidence_candidates}
    aggregated = aggregate_team_skill_assessments(audited_assessments, registry)
    audits_by_skill: dict[str, list[AuditedTeamSkillAssessment]] = defaultdict(list)
    for audit in audited_assessments:
        audits_by_skill[audit.team_skill_id].append(audit)

    assessments: list[TeamSkillAssessment] = []
    for aggregate in aggregated:
        skill = registry.get(aggregate.team_skill_id)
        positive = [
            item for item in audits_by_skill[skill.code]
            if item.final_status != "unsupported"
        ]
        observations: list[EvidenceObservation] = []
        seen_evidence: set[tuple[str, int, int, str]] = set()
        for item in positive:
            source = source_by_id.get(item.source_candidate_ability_id)
            if source is None:
                continue
            for quote in item.support_evidence:
                observation = _observation_from_quote(source, quote)
                assert observation.start is not None and observation.end is not None
                key = (
                    observation.source_experience_id,
                    observation.start,
                    observation.end,
                    observation.text,
                )
                if key in seen_evidence:
                    continue
                seen_evidence.add(key)
                observations.append(observation)
        reason_items = positive if aggregate.final_status != "unsupported" else audits_by_skill[skill.code]
        reasons = tuple(dict.fromkeys(item.reason for item in reason_items if item.reason))
        assessments.append(
            TeamSkillAssessment(
                candidate_id=candidate_id,
                team_skill_id=skill.code,
                team_skill_name=skill.name_zh,
                status=aggregate.final_status,
                inference_mode=skill.inference_mode,
                evidence=tuple(observations),
                reason=" | ".join(reasons),
                confidence=aggregate.confidence,
                atomic_abilities=aggregate.atomic_abilities,
                audit_flags=aggregate.audit_flags,
            )
        )

    return CandidateSkillProfile(
        candidate_id=candidate_id,
        skill_registry_version=registry.version,
        assessments=tuple(assessments),
        metadata={
            "schema_version": "candidate_skill_profile_v3_preuse",
            "primary_metric_key": ["candidate_id", "team_skill_id"],
            "evidence_grounding_required": True,
            **(dict(metadata) if metadata else {}),
        },
    )
