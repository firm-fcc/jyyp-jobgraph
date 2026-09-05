"""Minimal deterministic orchestration for Matching v1.

Inputs are already-produced CandidateSkillProfile and TargetJobProfile v1.1.
No Candidate/JD extraction or model call is performed here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from .candidate_matching_bridge_v1 import CandidateMatchingBridge
from .learning_path_stage1 import JobLearningTarget
from .matching_engine_v1 import MatchResult, MatchingEngineV1
from .target_job_profile_learning_bridge import TargetJobProfileLearningBridge
from .team_skill_schema_v3 import CandidateSkillProfile


@dataclass(frozen=True)
class MatchingPipelineOutput:
    match_result: MatchResult
    candidate_bridge_diagnostics: Mapping[str, Any]
    target_bridge_diagnostics: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "matching_pipeline_output_v1",
            "match_result": self.match_result.to_dict(),
            "diagnostics": {
                "candidate_bridge": dict(self.candidate_bridge_diagnostics),
                "target_bridge": dict(self.target_bridge_diagnostics),
            },
        }


def _matching_target_view(target: JobLearningTarget) -> JobLearningTarget:
    """Return an immutable Matching-only view without planner constraints."""
    return replace(
        target,
        requirements=tuple(
            replace(requirement, required_subskill_ids=())
            for requirement in target.requirements
        ),
    )


class MatchingPipelineV1:
    def __init__(self, decision_threshold: float | None = None) -> None:
        self._candidate_bridge = CandidateMatchingBridge()
        self._target_bridge = TargetJobProfileLearningBridge()
        self._engine = MatchingEngineV1(decision_threshold=decision_threshold)

    def run(
        self,
        *,
        candidate_profile: CandidateSkillProfile,
        target_job_profile: Mapping[str, Any],
        proficiency_levels: Mapping[str, str] | Sequence[object] | None = None,
    ) -> MatchingPipelineOutput:
        candidate = self._candidate_bridge.build(candidate_profile, proficiency_levels)
        target = self._target_bridge.build(target_job_profile)
        skill_names = {
            str(item.get("team_skill_id")): str(item.get("team_skill_name"))
            for item in target_job_profile.get("skills", [])
            if isinstance(item, Mapping)
            and item.get("team_skill_id")
            and item.get("team_skill_name")
        }
        result = self._engine.match(
            candidate.profile,
            _matching_target_view(target.target),
            skill_names=skill_names,
        )
        return MatchingPipelineOutput(
            match_result=result,
            candidate_bridge_diagnostics=candidate.diagnostics,
            target_bridge_diagnostics=target.diagnostics,
        )


__all__ = ["MatchingPipelineOutput", "MatchingPipelineV1"]
