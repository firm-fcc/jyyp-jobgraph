"""Development trace for grounded open capability candidates.

The V3 competition-facing prediction is still the standardized Team Skill
profile.  This trace keeps the open, evidence-grounded capability candidates so
that a valid fine-grained ability is not silently lost when the 49-node registry
has no clean equivalent.  Trace records are diagnostic only and are excluded from
the primary Team Skill metric.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from extractor.agentic_schema import CandidateAbility
from extractor.team_skill_auditor_v3 import AuditedTeamSkillAssessment


def build_grounded_capability_trace(
    evidence_candidates: Sequence[CandidateAbility],
    audited_assessments: Sequence[AuditedTeamSkillAssessment],
) -> list[dict[str, Any]]:
    by_source: dict[str, list[AuditedTeamSkillAssessment]] = defaultdict(list)
    for item in audited_assessments:
        by_source[item.source_candidate_ability_id].append(item)

    result: list[dict[str, Any]] = []
    for candidate in evidence_candidates:
        grounded = [
            item for item in candidate.evidence
            if item.start is not None and item.end is not None
        ]
        if not grounded:
            continue
        outcomes = by_source.get(candidate.candidate_id, [])
        non_unsupported = [
            {
                "team_skill_id": item.team_skill_id,
                "status": item.final_status,
                "confidence": item.confidence,
            }
            for item in outcomes
            if item.final_status != "unsupported"
        ]
        result.append(
            {
                "source_candidate_ability_id": candidate.candidate_id,
                "extracted_capability_hint": candidate.ability,
                "fact_hint": candidate.fact,
                "behavior_hint": candidate.behavior,
                "extraction_confidence": candidate.confidence,
                "evidence_type": candidate.category.get(
                    "evidence_type", "demonstrated_behavior"
                ),
                "grounded_evidence": [
                    {
                        "text": item.text,
                        "start": item.start,
                        "end": item.end,
                        "source_experience_id": candidate.project_id,
                    }
                    for item in grounded
                ],
                "non_unsupported_team_skill_outcomes": non_unsupported,
                "metric_role": "diagnostic_only_not_team_skill_prediction",
            }
        )
    return result
