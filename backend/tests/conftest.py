"""Local deterministic regression: never read deployment credentials or call LLMs."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "candidate_core"))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
for name in ("LLM_API_KEY", "LLM_MODEL", "LLM_API_URL", "LLM_API_BASE", "MATCHING_DECISION_THRESHOLD"):
    os.environ.pop(name, None)
for name, path in {
    "TARGETJOB_PROVIDER_TAXONOMY": ROOT / "job_data/2022-10/provider_skills.json",
    "TARGETJOB_CANONICAL_TAXONOMY": ROOT / "candidate_core/config/team_skills_v0.4.json",
    "TARGETJOB_JOBS": ROOT / "job_data/2022-10/jobs.json",
    "TARGETJOB_JD_SUMMARY": ROOT / "job_data/2022-10/jd_summary_2022-10.csv",
    "TARGETJOB_JOB_SKILL": ROOT / "job_data/2022-10/job_skill_effective.json",
}.items():
    os.environ[name] = str(path)


@pytest.fixture(autouse=True)
def offline_test_guard(monkeypatch):
    import requests
    import urllib.request
    def blocked(*args, **kwargs):
        raise AssertionError("External HTTP requests are forbidden in unit tests")
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.delenv("MATCHING_DECISION_THRESHOLD", raising=False)
