from __future__ import annotations

from functools import lru_cache
from dataclasses import replace
from typing import Any

from ..bootstrap import bootstrap_candidate_core
from ..config import CANDIDATE_CORE
from .matching_service import (
    explicit_level_skill_ids,
    gradable_candidate_skill_ids,
    levels_for_target,
    resolve_target,
)
from .proficiency_service import infer_proficiency_levels

bootstrap_candidate_core()
from extractor.candidate_matching_bridge_v1 import CandidateMatchingBridge  # noqa: E402
from extractor.learning_path_renderer import (  # noqa: E402
    LearningPathRenderer, RenderedLearningPathResult, RenderedSkillPath,
)
from extractor.learning_path_stage1 import (  # noqa: E402
    GapType, PathMode, LearningPathResult, load_skill_development_graph,
)
from extractor.targeted_learning_path_planner_v2 import LearningPathEngineV2  # noqa: E402
from extractor.target_job_profile_learning_bridge import TargetJobProfileLearningBridge  # noqa: E402
from extractor.team_skill_schema_v3 import CandidateSkillProfile  # noqa: E402
from extractor.team_skill_registry import TeamSkillRegistry  # noqa: E402


@lru_cache(maxsize=1)
def _learning_engine() -> LearningPathEngineV2:
    paths = sorted((CANDIDATE_CORE / "config").glob("skill_development_graph_*.json"))
    graphs = [load_skill_development_graph(path) for path in paths]
    return LearningPathEngineV2(graphs)


def render_api_learning_result(result: LearningPathResult) -> RenderedLearningPathResult:
    """Represent a planner-declared missing graph without inventing planner steps.

    All other paths still use the frozen renderer, including its fail-closed
    contract validation. Neither the planner result nor its modes are changed.
    """
    if len(result.gap_items) != len(result.paths):
        raise ValueError("planner gap_items and paths must have equal length")
    normal = []
    unavailable = {}
    expected_modes = {
        GapType.MISSING: PathMode.LEARN,
        GapType.LEVEL_GAP: PathMode.DEEPEN,
        GapType.EVIDENCE_INSUFFICIENT: PathMode.VERIFY_FIRST,
    }
    for index, (gap, path) in enumerate(zip(result.gap_items, result.paths)):
        if path.path_status != "GRAPH_UNAVAILABLE":
            normal.append(index)
            continue
        if (gap.team_skill_id != path.team_skill_id
                or gap.path_mode is not path.mode
                or expected_modes.get(gap.gap_type) is not path.mode
                or path.ordered_steps or path.evidence_tasks
                or path.capstone_evidence_task is not None
                or path.reassessment_required is not True):
            raise ValueError("invalid GRAPH_UNAVAILABLE planner contract")
        if gap.gap_type is GapType.LEVEL_GAP and (
                gap.observed_level is None or gap.required_level is None):
            raise ValueError("LEVEL_GAP must preserve observed and required proficiency levels")
        unavailable[index] = RenderedSkillPath(
            team_skill_id=gap.team_skill_id,
            team_skill_name=gap.team_skill_name,
            gap_type=gap.gap_type.value,
            observed_level=gap.observed_level,
            required_level=gap.required_level,
            path_mode=path.mode.value,
            achieved_node_ids=tuple(item.subskill_id for item in path.achieved_subskills),
            current_state=gap.explanation,
            gap_explanation=gap.explanation,
            development_goal="该能力存在差距，但当前没有 curated development graph；未生成学习或验证步骤。",
            learning_steps=(),
            specialization_extensions=(),
            verification_guidance=None,
            capstone_guidance=None,
            reassessment_required=path.reassessment_required,
            reassessment_guidance=path.reassessment_reason,
            path_status=path.path_status,
        )
    renderable = replace(
        result,
        gap_items=tuple(result.gap_items[i] for i in normal),
        paths=tuple(result.paths[i] for i in normal),
    )
    rendered = LearningPathRenderer().render(renderable)
    by_index = dict(zip(normal, rendered.skill_paths))
    by_index.update(unavailable)
    return replace(rendered, skill_paths=tuple(by_index[i] for i in range(len(result.paths))))


def run_learning_path(
    *,
    candidate_profile: dict[str, Any],
    target_job_profile: dict[str, Any] | None = None,
    job_id: str | None = None,
    jd_key: str | None = None,
    job_code: str | None = None,
    proficiency_levels: dict[str, str] | None = None,
    auto_proficiency: bool = True,
    proficiency_scope: str = "target",
) -> dict[str, Any]:
    candidate = CandidateSkillProfile.from_dict(candidate_profile)
    TeamSkillRegistry().validate_skill_ids(item.team_skill_id for item in candidate.assessments)
    target_profile = resolve_target(
        target_job_profile=target_job_profile,
        job_id=job_id,
        jd_key=jd_key,
        job_code=job_code,
    )

    proficiency_details: list[dict[str, Any]] = []
    levels = dict(proficiency_levels or {})
    source = "provided" if proficiency_levels is not None else "not_run"
    if proficiency_levels is None and auto_proficiency:
        wanted = (
            gradable_candidate_skill_ids(candidate)
            if proficiency_scope == "candidate"
            else explicit_level_skill_ids(target_profile)
        )
        levels, proficiency_details = infer_proficiency_levels(
            candidate,
            target_team_skill_ids=wanted,
        )
        source = "auto_on_demand" if proficiency_scope == "target" else "auto_candidate_wide"

    engine_levels = (
        levels_for_target(levels, target_profile) if proficiency_scope == "candidate" else levels
    )
    candidate_bridge = CandidateMatchingBridge().build(candidate, engine_levels)
    target_bridge = TargetJobProfileLearningBridge().build(target_profile)
    result = _learning_engine().build(candidate_bridge.profile, target_bridge.target)
    rendered = render_api_learning_result(result)

    return {
        "schema_version": "learning_path_api_response_v1",
        "path_status": result.path_status,
        "gap_summary": result.gap_summary.to_dict(),
        "rendered": rendered.to_dict(),
        "proficiency": {
            "source": source,
            "levels": levels,
            "details": proficiency_details,
        },
        "diagnostics": {
            "candidate_bridge": dict(candidate_bridge.diagnostics),
            "target_bridge": dict(target_bridge.diagnostics),
            "curated_graph_count": len(list((CANDIDATE_CORE / "config").glob("skill_development_graph_*.json"))),
        },
    }
