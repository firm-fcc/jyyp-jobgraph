"""Build an evidence-bound candidate-level Team Skill profile for V4.1.

Model-generated reason/atomic text is intentionally not surfaced as authoritative
output. Final explanations are constructed deterministically from exact located
resume quotes, so the user-facing profile cannot strengthen roles or actions
beyond the source evidence.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Mapping, Sequence

from extractor.agentic_schema import CandidateAbility
from extractor.team_skill_auditor_v3 import aggregate_team_skill_assessments
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
            fact="",
            behavior="",
            context=candidate.project_id,
            result="",
        )
    raise ValueError("positive audited support_evidence cannot be mapped to a located resume span")


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _deterministic_reason(skill_name: str, status: str, observations: Sequence[EvidenceObservation]) -> str:
    if status == "unsupported":
        return f"当前已验证的原文证据不足以支持【{skill_name}】。"
    quotes = "；".join(f"「{_compact(item.text)}」" for item in observations)
    if status == "supported":
        return f"已定位原文证据{quotes}，经受约束验证后支持【{skill_name}】。"
    return f"已定位原文证据{quotes}，与【{skill_name}】明确相关，但现有原文不足以完全确认该能力。"


def _evidence_bound_atomic_scopes(skill_name: str, observations: Sequence[EvidenceObservation]) -> tuple[str, ...]:
    # Pre-freeze representation: deliberately extractive. After the team confirms
    # whether the 49 nodes are leaves or parents, this can be replaced by a frozen
    # fine-skill/atomic layer without changing the primary Team Skill decision.
    values = []
    for item in observations:
        value = f"{skill_name}｜{_compact(item.text)}"
        if value not in values:
            values.append(value)
    return tuple(values)


def build_candidate_skill_profile(
    *,
    candidate_id: str,
    evidence_candidates: Sequence[CandidateAbility],
    audited_assessments: Sequence[object],
    registry: TeamSkillRegistry,
    metadata: Mapping[str, object] | None = None,
) -> CandidateSkillProfile:
    source_by_id = {candidate.candidate_id: candidate for candidate in evidence_candidates}
    aggregated = aggregate_team_skill_assessments(audited_assessments, registry)
    audits_by_skill: dict[str, list[object]] = defaultdict(list)
    for audit in audited_assessments:
        audits_by_skill[audit.team_skill_id].append(audit)

    assessments: list[TeamSkillAssessment] = []
    for aggregate in aggregated:
        skill = registry.get(aggregate.team_skill_id)
        items = audits_by_skill[skill.code]
        positive = [item for item in items if item.final_status != "unsupported"]
        # Candidate-level status and candidate-level evidence must use the same
        # support threshold.  For direct-behavior skills, once a Skill is
        # supported, partially-supported units remain diagnostic only and must
        # not be promoted into the final supported evidence list.  Otherwise a
        # weak/related span can leak into a Skill that was supported by another
        # strong evidence unit.  Aggregate-signal skills are the exception:
        # multiple positive contexts are intentionally combined to establish
        # support at candidate level.
        if aggregate.final_status == "unsupported":
            contributors = []
        elif skill.inference_mode == "aggregate_signal":
            contributors = positive
        elif aggregate.final_status == "supported":
            contributors = [item for item in items if item.final_status == "supported"]
        else:
            contributors = [item for item in items if item.final_status == "partially_supported"]

        observations: list[EvidenceObservation] = []
        seen_evidence: set[tuple[str, int, int, str]] = set()
        for item in contributors:
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

        assessments.append(
            TeamSkillAssessment(
                candidate_id=candidate_id,
                team_skill_id=skill.code,
                team_skill_name=skill.name_zh,
                status=aggregate.final_status,
                inference_mode=skill.inference_mode,
                evidence=tuple(observations),
                reason=_deterministic_reason(skill.name_zh, aggregate.final_status, observations),
                confidence=(
                    max((item.confidence for item in contributors), default=None)
                    if contributors else None
                ),
                atomic_abilities=(
                    _evidence_bound_atomic_scopes(skill.name_zh, observations)
                    if aggregate.final_status != "unsupported" else ()
                ),
                audit_flags=aggregate.audit_flags,
            )
        )

    return CandidateSkillProfile(
        candidate_id=candidate_id,
        skill_registry_version=registry.version,
        assessments=tuple(assessments),
        metadata={
            "schema_version": "candidate_skill_profile_v4_3_4",
            "primary_metric_key": ["candidate_id", "team_skill_id"],
            "evidence_grounding_required": True,
            "semantic_decision_source": "grounded_resume_evidence_only",
            "fact_behavior_hints_authoritative": False,
            "verifier_receives_ability_hint": False,
            "final_explanation_mode": "deterministic_grounded",
            "atomic_scope_mode": "evidence_bound_team_skill_leaf_frozen",
            "verifier_contract": "compact_evidence_index_v2",
            "evidence_aggregation_policy": "status_consistent_source_units",
            "partial_evidence_promoted_into_supported_skill": False,
            **(dict(metadata) if metadata else {}),
        },
    )
