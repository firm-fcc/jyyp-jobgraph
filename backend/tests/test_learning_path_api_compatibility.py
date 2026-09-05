"""Explicitly offline contract cases; these are not a replay of real model output."""
from copy import deepcopy
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import app
from backend.config import CANDIDATE_CORE
from backend.services import learning_path_service as service
from backend.services.target_job_service import build_target_job_profile
from extractor.candidate_matching_bridge_v1 import CandidateMatchingBridge
from extractor.learning_path_stage1 import load_skill_development_graph
from extractor.target_job_profile_learning_bridge import TargetJobProfileLearningBridge
from extractor.targeted_learning_path_planner_v2 import LearningPathEngineV2
from extractor.team_skill_schema_v3 import CandidateSkillProfile
from extractor.learning_path_renderer import LearningPathRenderer
from test_api import _candidate_profile

# GRAPH_UNAVAILABLE 一档所代表的是“该能力尚无 curated 图谱”这一渲染契约，与具体是
# 哪一项能力无关。config/ 下的图谱逐批补齐，若以某一项恰好尚未收录为前提，图谱一经
# 补齐本组用例即整体失效。故另取一项，在本文件内把它的图谱排除在引擎之外。
GRAPHLESS_SKILL = "T-DA-01"


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Learning Path contract tests must not call proficiency or models")
    monkeypatch.setattr(service, "infer_proficiency_levels", forbidden)


@pytest.fixture(autouse=True)
def graphless_engine(monkeypatch):
    graphs = [
        load_skill_development_graph(path)
        for path in sorted((CANDIDATE_CORE / "config").glob("skill_development_graph_*.json"))
    ]
    engine = LearningPathEngineV2([g for g in graphs if g.team_skill_id != GRAPHLESS_SKILL])
    monkeypatch.setattr(service, "_learning_engine", lambda: engine)


def request_for(skill_id=GRAPHLESS_SKILL, level="U", present=True):
    profile = _candidate_profile()
    assessment = profile["assessments"][0]
    assessment["team_skill_id"] = skill_id
    if not present:
        profile["assessments"] = []
    return {
        "candidate_profile": profile,
        "target_job_profile": build_target_job_profile(job_id="133663124"),
        "proficiency_levels": {skill_id: level} if present else {},
        "auto_proficiency": False,
    }


def planned(request):
    candidate = CandidateMatchingBridge().build(
        CandidateSkillProfile.from_dict(request["candidate_profile"]),
        request["proficiency_levels"],
    )
    target = TargetJobProfileLearningBridge().build(request["target_job_profile"])
    return service._learning_engine().build(candidate.profile, target.target)


@pytest.mark.parametrize("level,present,gap,mode", [
    ("U", True, "EVIDENCE_INSUFFICIENT", "VERIFY_FIRST"),
    ("P1", False, "MISSING", "LEARN"),
    ("P1", True, "LEVEL_GAP", "DEEPEN"),
])
def test_graph_unavailable_is_http_200(level, present, gap, mode):
    request = request_for(level=level, present=present)
    before = deepcopy(request)
    response = TestClient(app).post("/api/learning-path", json=request)
    assert response.status_code == 200, response.text
    data = response.json()
    path = next(p for p in data["rendered"]["skill_paths"] if p["team_skill_id"] == GRAPHLESS_SKILL)
    assert (path["gap_type"], path["path_mode"], path["path_status"]) == (gap, mode, "GRAPH_UNAVAILABLE")
    assert path["learning_steps"] == []
    assert path["verification_guidance"] is None
    assert path["capstone_guidance"] is None
    assert path["reassessment_required"] is True
    assert request == before


@pytest.mark.parametrize("level,present", [("P1", True), ("U", True), ("P1", False)])
def test_available_graph_keeps_original_renderer_result(level, present):
    request = request_for("T-AI-01", level, present)
    plan = planned(request)
    frozen = LearningPathRenderer().render(plan).to_dict()
    response = TestClient(app).post("/api/learning-path", json=request)
    assert response.status_code == 200, response.text
    paths = response.json()["rendered"]["skill_paths"]
    actual = next(p for p in paths if p["team_skill_id"] == "T-AI-01")
    expected = next(p for p in frozen["skill_paths"] if p["team_skill_id"] == "T-AI-01")
    assert actual == expected
    assert actual["learning_steps"] or actual["verification_guidance"]


def test_satisfied_without_graph_remains_no_action():
    response = TestClient(app).post("/api/learning-path", json=request_for(level="P4"))
    assert response.status_code == 200, response.text
    path = next(p for p in response.json()["rendered"]["skill_paths"] if p["team_skill_id"] == GRAPHLESS_SKILL)
    assert (path["gap_type"], path["path_mode"], path["path_status"]) == ("SATISFIED", "NONE", "NO_ACTION")
    assert path["learning_steps"] == [] and not path["reassessment_required"]


def test_unknown_skill_fails_closed():
    response = TestClient(app).post("/api/learning-path", json=request_for("T-INVALID-999"))
    assert 400 <= response.status_code < 500


@pytest.mark.parametrize("body", [{}, {"candidate_profile": {}, "proficiency_levels": {"T-SW-01": "P9"}},
                                        {"candidate_profile": {}, "unknown_field": True}])
def test_malformed_request_fails_closed(body):
    response = TestClient(app).post("/api/learning-path", json=body)
    assert 400 <= response.status_code < 500


def test_known_frozen_conflict_and_adapter_nonmutation():
    plan = planned(request_for())
    before = plan.to_dict()
    with pytest.raises(ValueError, match="VERIFY_FIRST must contain exactly one"):
        LearningPathRenderer().render(plan)
    rendered = service.render_api_learning_result(plan)
    assert plan.to_dict() == before
    assert [p.team_skill_id for p in rendered.skill_paths] == [p.team_skill_id for p in plan.paths]


def test_adapter_does_not_hide_other_planner_contract_errors():
    plan = planned(request_for())
    index = next(i for i,p in enumerate(plan.paths) if p.team_skill_id == GRAPHLESS_SKILL)
    paths = list(plan.paths)
    paths[index] = replace(paths[index], reassessment_required=False)
    with pytest.raises(ValueError, match="invalid GRAPH_UNAVAILABLE"):
        service.render_api_learning_result(replace(plan, paths=tuple(paths)))
    paths[index] = replace(plan.paths[index], path_status="READY")
    with pytest.raises(ValueError, match="VERIFY_FIRST must contain exactly one"):
        service.render_api_learning_result(replace(plan, paths=tuple(paths)))
