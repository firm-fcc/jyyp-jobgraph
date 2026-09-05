"""Deterministic bridge from frozen CandidateSkillProfile to Matching/Learning input.

This module does not call models, change Candidate decisions, or infer proficiency.
Only ``supported`` primary Team Skills enter the matching profile.  A supplied
proficiency level is attached only as an observation; missing levels remain
``None`` and are handled fail-closed by the existing GapEngine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .learning_path_stage1 import (
    AUXILIARY_TEAM_SKILL_IDS,
    CandidateLearningProfile,
    GroundedEvidence,
    ObservedTeamSkill,
    PROFICIENCY_LEVELS,
)
from .team_skill_schema_v3 import CandidateSkillProfile


class CandidateMatchingBridgeError(ValueError):
    pass


@dataclass(frozen=True)
class BridgedCandidateLearningProfile:
    profile: CandidateLearningProfile
    diagnostics: Mapping[str, Any]


def _normalize_proficiency_levels(
    values: Mapping[str, str] | Sequence[object] | None,
) -> dict[str, str]:
    """Normalize either {team_skill_id: level} or ProficiencyResult-like items."""

    if values is None:
        return {}

    out: dict[str, str] = {}
    if isinstance(values, Mapping):
        items = values.items()
    elif isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        normalized_items: list[tuple[object, object]] = []
        for item in values:
            if isinstance(item, Mapping):
                sid = item.get("ability_id") or item.get("team_skill_id")
                level = item.get("final_level")
            else:
                sid = getattr(item, "ability_id", None) or getattr(item, "team_skill_id", None)
                level = getattr(item, "final_level", None)
            normalized_items.append((sid, level))
        items = normalized_items
    else:
        raise TypeError("proficiency_levels must be a mapping, sequence, or None")

    for raw_sid, raw_level in items:
        sid = str(raw_sid or "").strip()
        level = str(raw_level or "").strip()
        if not sid:
            raise CandidateMatchingBridgeError("proficiency result team_skill_id must be non-empty")
        if level not in PROFICIENCY_LEVELS:
            raise CandidateMatchingBridgeError(
                f"invalid proficiency level for {sid}: {level!r}"
            )
        if sid in out:
            raise CandidateMatchingBridgeError(f"duplicate proficiency result for {sid}")
        out[sid] = level
    return out


class CandidateMatchingBridge:
    """Convert frozen Candidate output into the existing deterministic Gap input."""

    def build(
        self,
        profile: CandidateSkillProfile,
        proficiency_levels: Mapping[str, str] | Sequence[object] | None = None,
    ) -> BridgedCandidateLearningProfile:
        if not isinstance(profile, CandidateSkillProfile):
            raise TypeError("profile must be CandidateSkillProfile")
        if not profile.candidate_id.strip():
            raise CandidateMatchingBridgeError("candidate_id must be non-empty")

        levels = _normalize_proficiency_levels(proficiency_levels)
        assessments_by_id = {item.team_skill_id: item for item in profile.assessments}
        if len(assessments_by_id) != len(profile.assessments):
            raise CandidateMatchingBridgeError(
                "candidate assessments must be unique by team_skill_id"
            )
        unknown_levels = sorted(set(levels) - set(assessments_by_id))
        if unknown_levels:
            raise CandidateMatchingBridgeError(
                "proficiency results reference unknown Team Skills: "
                + ", ".join(unknown_levels)
            )

        supported: list[ObservedTeamSkill] = []
        excluded_partial: list[str] = []
        excluded_auxiliary: list[str] = []
        excluded_unsupported: list[str] = []
        ignored_auxiliary_levels: list[str] = []

        for assessment in profile.assessments:
            sid = assessment.team_skill_id
            if sid in AUXILIARY_TEAM_SKILL_IDS:
                if assessment.status == "supported":
                    excluded_auxiliary.append(sid)
                if sid in levels:
                    ignored_auxiliary_levels.append(sid)
                continue
            if assessment.status == "partially_supported":
                excluded_partial.append(sid)
                if sid in levels:
                    raise CandidateMatchingBridgeError(
                        f"proficiency level supplied for partially_supported skill {sid}"
                    )
                continue
            if assessment.status != "supported":
                excluded_unsupported.append(sid)
                if sid in levels:
                    raise CandidateMatchingBridgeError(
                        f"proficiency level supplied for unsupported skill {sid}"
                    )
                continue
            if not assessment.evidence:
                raise CandidateMatchingBridgeError(
                    f"supported skill has no grounded evidence: {sid}"
                )

            evidence = tuple(
                GroundedEvidence(
                    text=item.text,
                    source_id=item.source_experience_id,
                    start=item.start,
                    end=item.end,
                )
                for item in assessment.evidence
            )
            capabilities = tuple(assessment.atomic_abilities) or tuple(
                item.text for item in assessment.evidence
            )
            supported.append(
                ObservedTeamSkill(
                    team_skill_id=sid,
                    team_skill_name=assessment.team_skill_name,
                    evidence=evidence,
                    observed_capabilities=capabilities,
                    observed_proficiency=levels.get(sid),
                    achieved_subskills=(),
                )
            )

        candidate = CandidateLearningProfile(
            candidate_id=profile.candidate_id,
            supported_team_skills=tuple(supported),
            explicit_mentions=(),
        )
        applied = sorted(sid for sid in levels if sid not in AUXILIARY_TEAM_SKILL_IDS)
        diagnostics = {
            "schema_version": "candidate_matching_bridge_v1",
            "source_profile_schema": profile.metadata.get("schema_version"),
            "candidate_decisions_mutated": False,
            "partial_support_counts_as_supported": False,
            "auxiliary_skills_graded": False,
            "supported_primary_skill_ids": [item.team_skill_id for item in supported],
            "supported_without_proficiency": [
                item.team_skill_id
                for item in supported
                if item.observed_proficiency is None
            ],
            "proficiency_levels_applied": applied,
            "excluded_partially_supported": sorted(excluded_partial),
            "excluded_auxiliary_supported": sorted(excluded_auxiliary),
            "excluded_unsupported": sorted(excluded_unsupported),
            "ignored_auxiliary_proficiency_levels": sorted(ignored_auxiliary_levels),
        }
        return BridgedCandidateLearningProfile(candidate, diagnostics)


__all__ = [
    "BridgedCandidateLearningProfile",
    "CandidateMatchingBridge",
    "CandidateMatchingBridgeError",
]
