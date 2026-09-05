from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANDIDATE_CORE = ROOT / "candidate_core"
JOB_DATA = ROOT / "job_data" / "2022-10"
RUNTIME = ROOT / "runtime"
UPLOAD_DIR = RUNTIME / "uploads"
OUTPUT_DIR = RUNTIME / "outputs"

PROVIDER_SKILLS = JOB_DATA / "provider_skills.json"
CANONICAL_SKILLS = CANDIDATE_CORE / "config" / "team_skills_v0.4.json"
JOBS = JOB_DATA / "jobs.json"
JD_SUMMARY = JOB_DATA / "jd_summary_2022-10.csv"
JOB_SKILL = JOB_DATA / "job_skill_effective.json"
WINDOW = "2022-10"


def matching_threshold() -> float | None:
    raw = os.getenv("MATCHING_DECISION_THRESHOLD", "").strip()
    if not raw:
        return None
    value = float(raw)
    if not 0.0 <= value <= 1.0:
        raise ValueError("MATCHING_DECISION_THRESHOLD must be in [0,1]")
    return value


def candidate_timeout_seconds() -> float:
    return float(os.getenv("CANDIDATE_REQUEST_TIMEOUT", "900"))


for path in (UPLOAD_DIR, OUTPUT_DIR):
    path.mkdir(parents=True, exist_ok=True)
