"""Deterministic Candidate x Job matching engine v1.

The score is deliberately asymmetric: it measures the fraction of current Job
requirements that are verified as satisfied by the Candidate.  Extra Candidate
skills cannot reduce the score.  The engine reuses the frozen GapEngine and
contains no learned weights, cosine similarity, or hidden threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .learning_path_stage1 import (
    CandidateLearningProfile,
    GapEngine,
    GapType,
    JobLearningTarget,
)


DECISION_MATCH = "MATCH"
DECISION_NO_MATCH = "NO_MATCH"
DECISION_NOT_CALIBRATED = "NOT_CALIBRATED"


@dataclass(frozen=True)
class MatchSkillItem:
    team_skill_id: str
    team_skill_name: str
    required_level: str | None
    candidate_level: str | None
    gap_type: str
    path_mode: str
    requirement_type: str
    requirement_evidence: tuple[str, ...]
    candidate_evidence: tuple[Mapping[str, Any], ...]
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_skill_id": self.team_skill_id,
            "team_skill_name": self.team_skill_name,
            "required_level": self.required_level,
            "candidate_level": self.candidate_level,
            "gap_type": self.gap_type,
            "path_mode": self.path_mode,
            "requirement_type": self.requirement_type,
            "requirement_evidence": list(self.requirement_evidence),
            "candidate_evidence": [dict(item) for item in self.candidate_evidence],
            "explanation": self.explanation,
        }


@dataclass(frozen=True)
class MatchResult:
    candidate_id: str
    job_id: str
    job_title: str
    match_score: float
    decision: str
    decision_threshold: float | None
    summary: Mapping[str, int]
    metrics: Mapping[str, float]
    skills: tuple[MatchSkillItem, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "match_result_v1",
            "candidate_id": self.candidate_id,
            "job_id": self.job_id,
            "job_title": self.job_title,
            "match_score": self.match_score,
            "decision": self.decision,
            "decision_threshold": self.decision_threshold,
            "summary": dict(self.summary),
            "metrics": dict(self.metrics),
            "skills": [item.to_dict() for item in self.skills],
            "semantics": {
                "score": "verified_satisfied_job_requirements / eligible_job_requirements",
                "score_direction": "job_requirement_coverage_asymmetric",
                "extra_candidate_skills_penalized": False,
                "cosine_used": False,
                "jd_U": "LEVEL_UNSPECIFIED",
                "jd_U_is_P1": False,
                "candidate_U": "EVIDENCE_INSUFFICIENT_FOR_LEVEL_COMPARISON",
                "partially_supported_counts_as_supported": False,
            },
        }


class MatchingEngineV1:
    def __init__(self, decision_threshold: float | None = None) -> None:
        if decision_threshold is not None and not 0.0 <= decision_threshold <= 1.0:
            raise ValueError("decision_threshold must be in [0,1] or None")
        self.decision_threshold = decision_threshold
        self._gap_engine = GapEngine()

    def match(
        self,
        candidate: CandidateLearningProfile,
        target: JobLearningTarget,
        *,
        skill_names: Mapping[str, str] | None = None,
    ) -> MatchResult:
        if not isinstance(candidate, CandidateLearningProfile):
            raise TypeError("candidate must be CandidateLearningProfile")
        if not isinstance(target, JobLearningTarget):
            raise TypeError("target must be JobLearningTarget")
        if not target.requirements:
            raise ValueError("target must contain at least one eligible requirement")

        gaps = self._gap_engine.evaluate(candidate, target)
        by_requirement = {item.team_skill_id: item for item in target.requirements}
        observed_by_id = candidate.supported_by_id

        counts = {value.value: 0 for value in GapType}
        details: list[MatchSkillItem] = []
        for gap in gaps:
            counts[gap.gap_type.value] += 1
            requirement = by_requirement[gap.team_skill_id]
            observed = observed_by_id.get(gap.team_skill_id)
            name = (
                (skill_names or {}).get(gap.team_skill_id)
                or (observed.team_skill_name if observed is not None else None)
                or gap.team_skill_name
                or gap.team_skill_id
            )
            candidate_evidence = (
                tuple(item.to_dict() for item in observed.evidence)
                if observed is not None
                else ()
            )
            details.append(
                MatchSkillItem(
                    team_skill_id=gap.team_skill_id,
                    team_skill_name=name,
                    required_level=gap.required_level,
                    candidate_level=gap.observed_level,
                    gap_type=gap.gap_type.value,
                    path_mode=gap.path_mode.value,
                    requirement_type=gap.requirement_type,
                    requirement_evidence=requirement.requirement_evidence,
                    candidate_evidence=candidate_evidence,
                    explanation=gap.explanation,
                )
            )

        n = len(gaps)
        satisfied = counts[GapType.SATISFIED.value]
        missing = counts[GapType.MISSING.value]
        level_gap = counts[GapType.LEVEL_GAP.value]
        uncertain = counts[GapType.EVIDENCE_INSUFFICIENT.value]

        verified_fit = satisfied / n
        skill_coverage = (n - missing) / n
        metrics = {
            "verified_fit": round(verified_fit, 6),
            "skill_coverage": round(skill_coverage, 6),
            "level_gap_rate": round(level_gap / n, 6),
            "uncertainty_rate": round(uncertain / n, 6),
            "missing_rate": round(missing / n, 6),
        }
        summary = {
            "required_skills": n,
            "satisfied": satisfied,
            "level_gap": level_gap,
            "evidence_insufficient": uncertain,
            "missing": missing,
        }

        if self.decision_threshold is None:
            decision = DECISION_NOT_CALIBRATED
        else:
            decision = (
                DECISION_MATCH
                if verified_fit >= self.decision_threshold
                else DECISION_NO_MATCH
            )

        return MatchResult(
            candidate_id=candidate.candidate_id,
            job_id=target.job_id,
            job_title=target.job_title,
            match_score=round(100.0 * verified_fit, 2),
            decision=decision,
            decision_threshold=self.decision_threshold,
            summary=summary,
            metrics=metrics,
            skills=tuple(details),
        )


__all__ = [
    "DECISION_MATCH",
    "DECISION_NO_MATCH",
    "DECISION_NOT_CALIBRATED",
    "MatchResult",
    "MatchSkillItem",
    "MatchingEngineV1",
]
