from __future__ import annotations

from functools import lru_cache
from typing import Any

from ..bootstrap import bootstrap_candidate_core
from ..config import CANONICAL_SKILLS, JD_SUMMARY, JOBS, JOB_SKILL, PROVIDER_SKILLS, WINDOW

bootstrap_candidate_core()
from extractor.target_job_profile_adapter import (  # noqa: E402
    TargetJobProfileAdapter,
    TargetJobProfileError,
)


@lru_cache(maxsize=1)
def _adapter() -> TargetJobProfileAdapter:
    return TargetJobProfileAdapter.from_paths(
        provider_taxonomy_path=PROVIDER_SKILLS,
        canonical_taxonomy_path=CANONICAL_SKILLS,
        jobs_path=JOBS,
        jd_summary_csv=JD_SUMMARY,
        job_skill_path=JOB_SKILL,
        window=WINDOW,
        graph_layer="effective",
    )


def build_target_job_profile(*, job_id: str | None = None, jd_key: str | None = None) -> dict[str, Any]:
    if bool(job_id) == bool(jd_key):
        raise ValueError("provide exactly one of job_id or jd_key")
    try:
        if jd_key:
            return _adapter().build_single_jd(jd_key=jd_key)
        return _adapter().build_single_jd(jobid=str(job_id))
    except TargetJobProfileError as exc:
        raise ValueError(str(exc)) from exc
