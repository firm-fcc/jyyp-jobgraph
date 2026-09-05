# -*- coding: utf-8 -*-
"""Deterministic bridge from Target Job Profile v1.1 to frozen Learning Path input."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .learning_path_stage1 import JobLearningTarget, JobSkillRequirement
from .subskill_requirement_resolver_v1 import SubskillRequirementResolverV1


class TargetJobProfileBridgeError(ValueError):
    pass


@dataclass(frozen=True)
class BridgedJobLearningTarget:
    target: JobLearningTarget
    diagnostics: Mapping[str, Any]


class TargetJobProfileLearningBridge:
    """Contract conversion with deterministic curated subskill mapping only."""

    def __init__(self, resolver: SubskillRequirementResolverV1 | None = None) -> None:
        self._resolver = resolver or SubskillRequirementResolverV1()

    def build(self, profile: Mapping[str, Any]) -> BridgedJobLearningTarget:
        if profile.get("schema_version") != "target_job_profile_v1.1":
            raise TargetJobProfileBridgeError("unsupported target job profile schema")
        taxonomy = profile.get("taxonomy")
        if not isinstance(taxonomy, Mapping):
            raise TargetJobProfileBridgeError("target profile taxonomy block missing")
        compatibility = taxonomy.get("taxonomy_compatibility")
        if not isinstance(compatibility, Mapping) or compatibility.get("status") != "PASS":
            raise TargetJobProfileBridgeError("taxonomy compatibility is not PASS")

        job = profile.get("job")
        if not isinstance(job, Mapping):
            raise TargetJobProfileBridgeError("target profile job block missing")
        job_id = str(job.get("jobid") or job.get("jd_key") or "").strip()
        job_title = str(job.get("title") or job.get("job_name") or "").strip()
        if not job_id or not job_title:
            raise TargetJobProfileBridgeError("job id/title required")

        raw_skills = profile.get("skills")
        if not isinstance(raw_skills, list):
            raise TargetJobProfileBridgeError("target profile skills must be array")

        requirements = []
        included = []
        excluded = []
        resolver_diagnostics = []
        for skill in raw_skills:
            if not isinstance(skill, Mapping):
                raise TargetJobProfileBridgeError("skill must be object")
            sid = str(skill.get("team_skill_id") or "").strip()
            status = skill.get("requirement_status")
            eligible = skill.get("learning_path_target_eligible") is True
            if not eligible:
                excluded.append({"team_skill_id": sid, "reason": str(status)})
                continue
            if skill.get("is_primary") is not True:
                raise TargetJobProfileBridgeError(f"eligible skill is not primary: {sid}")
            if status not in {"EXPLICIT_LEVEL", "LEVEL_UNSPECIFIED"}:
                raise TargetJobProfileBridgeError(f"unexpected eligible requirement_status for {sid}: {status}")
            required_level = skill.get("required_level")
            if status == "EXPLICIT_LEVEL" and required_level not in {"P1", "P2", "P3", "P4"}:
                raise TargetJobProfileBridgeError(f"explicit level missing/invalid for {sid}")
            if status == "LEVEL_UNSPECIFIED" and required_level is not None:
                raise TargetJobProfileBridgeError(f"LEVEL_UNSPECIFIED must have required_level=None for {sid}")
            evidence_ref = str(skill.get("requirement_evidence_ref") or "").strip()
            if not evidence_ref.startswith("structured_jd_summary:"):
                raise TargetJobProfileBridgeError(f"structured provenance missing for {sid}")
            raw_skill_points = skill.get("skill_points", [])
            resolution = self._resolver.resolve(sid, raw_skill_points)
            requirements.append(
                JobSkillRequirement(
                    team_skill_id=sid,
                    requirement_type="core",
                    required_level=required_level,
                    requirement_evidence=(evidence_ref,),
                    required_capabilities=(),
                    market_trend_rank=None,
                    required_subskill_ids=resolution.required_subskill_ids,
                )
            )
            included.append(sid)
            resolver_diagnostics.append(
                {
                    "team_skill_id": resolution.team_skill_id,
                    "resolution_status": resolution.resolution_status,
                    "input_skill_points": (
                        list(raw_skill_points)
                        if isinstance(raw_skill_points, list)
                        and all(isinstance(value, str) for value in raw_skill_points)
                        else []
                    ),
                    "matched_terms": list(resolution.matched_terms),
                    "required_subskill_ids": list(resolution.required_subskill_ids),
                }
            )

        if not requirements:
            raise TargetJobProfileBridgeError("no eligible primary skill requirements")

        target = JobLearningTarget(
            job_id=job_id,
            job_title=job_title,
            requirements=tuple(requirements),
        )
        diagnostics = {
            "included_skill_ids": included,
            "excluded_skills": excluded,
            "requirement_type_policy": "single_jd_detected_skill_compatibility_default",
            "requirement_type_semantics": (
                "core means included in the current single-JD target set; it is not a must-have/preferred inference"
            ),
            "raw_jd_evidence_available": False,
            "requirement_evidence_policy": "STRUCTURED_JD_SUMMARY_PROVENANCE",
            "market_trend_rank_policy": "NOT_APPLIED_V1_1",
            "required_capability_policy": "NOT_INFERRED",
            "required_subskill_policy": "DETERMINISTIC_JD_SKILLPOINT_TO_CURATED_GRAPH_V1",
            "techstack_policy": "IGNORED_FOR_SUBSKILL_RESOLUTION",
            "required_subskill_resolutions": resolver_diagnostics,
        }
        return BridgedJobLearningTarget(target=target, diagnostics=diagnostics)


__all__ = [
    "BridgedJobLearningTarget",
    "TargetJobProfileBridgeError",
    "TargetJobProfileLearningBridge",
]
