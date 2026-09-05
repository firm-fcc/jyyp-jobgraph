from __future__ import annotations

from typing import Any

from ..bootstrap import bootstrap_candidate_core
from ..config import matching_threshold
from .aggregated_target_job_service import build_aggregated_target_job_profile
from .proficiency_service import infer_proficiency_levels
from .target_job_service import build_target_job_profile

bootstrap_candidate_core()
from extractor.matching_pipeline_v1 import MatchingPipelineV1  # noqa: E402
from extractor.team_skill_schema_v3 import CandidateSkillProfile  # noqa: E402


def resolve_target(
    *,
    target_job_profile: dict[str, Any] | None,
    job_id: str | None,
    jd_key: str | None,
    job_code: str | None = None,
) -> dict[str, Any]:
    """四种给法取其一：现成的 profile、jobid、jd_key，或标准岗位编码。

    前三种落到单条招聘信息上；给 job_code 则按该岗位窗口内的全部招聘信息
    汇总出一份基准，见 aggregated_target_job_service。
    """
    given = [target_job_profile is not None, bool(job_id), bool(jd_key), bool(job_code)]
    if sum(given) != 1:
        raise ValueError(
            "provide exactly one of target_job_profile / job_id / jd_key / job_code"
        )
    if target_job_profile is not None:
        return target_job_profile
    if job_code:
        return build_aggregated_target_job_profile(job_code)
    return build_target_job_profile(job_id=job_id, jd_key=jd_key)


def levels_for_target(
    levels: dict[str, str],
    target: dict[str, Any],
) -> dict[str, str]:
    """把候选人全量的熟练度档位收窄到这个岗位真正要比等级的那几项。

    冻结的判定引擎按这个顺序判：候选人档位为 U 先于"岗位未设等级要求"生效
    （见 learning_path_stage1 的 gap 判定），于是一项能力若岗位没写要到什么级、
    而候选人的证据又不足以定级，带上那个 U 反倒把本该算满足的一项判成证据不足。

    U 的意思是"证据不足以判定等级"，岗位没设等级要求时这句话无处安放：既然
    不比等级，就不必先问候选人是什么等级。故只把岗位标了等级的那几项的档位
    交给引擎，其余项不带档位进去，照其有无证据判定。

    候选人全量定级（proficiency_scope=candidate）时才需要这一步：按目标岗位
    定级本就只覆盖这几项。返回值只用于喂引擎，对外仍回完整的档位表，
    调用方据以在改选岗位时复用，不必重新定级。
    """
    graded = {
        str(item.get("team_skill_id", "")).strip()
        for item in target.get("skills", [])
        if isinstance(item, dict) and item.get("requirement_status") == "EXPLICIT_LEVEL"
    }
    return {sid: level for sid, level in levels.items() if sid in graded}


def gradable_candidate_skill_ids(candidate: CandidateSkillProfile) -> list[str]:
    """候选人自身已具备、可定级的能力。

    与 explicit_level_skill_ids 相对：那一支按目标岗位要哪几项取，换岗位就换一批；
    这一支只看候选人有什么，与目标岗位无关，因而一次算齐即可反复使用。
    证据不足以支撑的项不送去定级 —— 定级器要的是行为证据，没有证据的项
    送过去也只会回一个 U，白付一次模型调用。
    """
    return [
        item.team_skill_id
        for item in candidate.assessments
        if getattr(item, "status", None) == "supported"
    ]


def explicit_level_skill_ids(target: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for item in target.get("skills", []):
        if not isinstance(item, dict):
            continue
        if item.get("requirement_status") == "EXPLICIT_LEVEL" and item.get("learning_path_target_eligible"):
            sid = str(item.get("team_skill_id", "")).strip()
            if sid:
                result.append(sid)
    return result


def run_matching(
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
    target = resolve_target(
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
            else explicit_level_skill_ids(target)
        )
        levels, proficiency_details = infer_proficiency_levels(
            candidate,
            target_team_skill_ids=wanted,
        )
        source = "auto_on_demand" if proficiency_scope == "target" else "auto_candidate_wide"

    engine_levels = (
        levels_for_target(levels, target) if proficiency_scope == "candidate" else levels
    )
    pipeline = MatchingPipelineV1(decision_threshold=matching_threshold())
    output = pipeline.run(
        candidate_profile=candidate,
        target_job_profile=target,
        proficiency_levels=engine_levels,
    ).to_dict()
    output["target_job_profile"] = target
    output["proficiency"] = {
        "source": source,
        "levels": levels,
        "details": proficiency_details,
        "scope": proficiency_scope if source.startswith("auto") else None,
    }
    return output
