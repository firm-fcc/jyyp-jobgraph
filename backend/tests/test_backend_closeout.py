import asyncio
import copy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as api
from backend.config import matching_threshold
from backend.services import candidate_service as cs, proficiency_service as ps
from backend.services.matching_service import run_matching, resolve_target
from backend.services.learning_path_service import run_learning_path
from backend.services.target_job_service import build_target_job_profile
from backend.services import job_summary_service as js
from extractor.learning_path_stage1 import CandidateLearningProfile, JobLearningTarget, JobSkillRequirement, GapEngine, GapType, PathMode
from extractor.matching_engine_v1 import MatchingEngineV1
from extractor.team_skill_schema_v3 import CandidateSkillProfile
from test_api import _candidate_profile

client = TestClient(api.app, raise_server_exceptions=False)
THRESHOLD = 0.380952


@pytest.mark.parametrize("raw,expected", [(None,None),("",None),("  ",None),("0",0),("1",1),("0.380952",THRESHOLD)])
def test_threshold_parse(monkeypatch, raw, expected):
    if raw is not None:
        monkeypatch.setenv("MATCHING_DECISION_THRESHOLD", raw)
    assert matching_threshold() == expected


@pytest.mark.parametrize("raw", ["garbage", "38.0952", "-0.1", "1.1", "nan", "inf"])
def test_invalid_threshold_fail_closed_and_health_stays_200(monkeypatch, raw):
    monkeypatch.setenv("MATCHING_DECISION_THRESHOLD", raw)
    with pytest.raises(ValueError): matching_threshold()
    health = client.get("/health")
    assert health.status_code == 200
    assert not health.json()["matching_calibrated"]
    assert health.json()["limitations"]["matching_threshold_invalid"]
    response = client.post("/api/match", json={"candidate_profile": _candidate_profile(), "job_id":"133663124", "auto_proficiency":False})
    assert response.status_code == 400


def test_calibrated_health_and_public_freeze(monkeypatch):
    monkeypatch.setenv("MATCHING_DECISION_THRESHOLD",str(THRESHOLD))
    assert client.get("/health").json()["matching_calibrated"]
    root=Path(__file__).resolve().parents[1]
    assert json.loads((root/"config/matching_threshold_v1.json").read_text())["threshold"]==THRESHOLD
    assert "MATCHING_DECISION_THRESHOLD=0.380952" in (root/".env.example").read_text()


@pytest.mark.parametrize("supported_count,decision", [(47618,"NO_MATCH"),(47619,"MATCH"),(47620,"MATCH")])
def test_exact_frozen_threshold_comparison(monkeypatch,supported_count,decision):
    # Isolate engine comparison with a deterministic GapEngine test double.
    # 47619/125000 == 0.380952 exactly as a float; no threshold approximation.
    candidate=CandidateLearningProfile("threshold_test")
    requirement=JobSkillRequirement("T-AI-01", requirement_type="core", required_level="P2", requirement_evidence=("fixture",))
    target=JobLearningTarget("threshold_job","fixture",(requirement,))
    missing=GapEngine().evaluate(candidate,target)[0]
    satisfied=replace(missing,gap_type=GapType.SATISFIED,path_mode=PathMode.NONE)
    gaps=(satisfied,)*supported_count+(missing,)*(125000-supported_count)
    engine=MatchingEngineV1(THRESHOLD)
    monkeypatch.setattr(engine._gap_engine,"evaluate",lambda *_:gaps)
    result=engine.match(candidate,target)
    assert result.decision==decision
    if supported_count==47619:
        assert supported_count/125000 == THRESHOLD == result.metrics["verified_fit"]


@pytest.mark.parametrize("key,model,url,base,expected", [
    ("","m","https://example.invalid/chat/completions","",False),
    ("test-only","","https://example.invalid/chat/completions","",False),
    ("test-only","m","","",False),
    ("test-only","m","file:///tmp/key","",False),
    ("test-only","m","http://[invalid","",False),
    ("test-only","m","https://example.invalid/chat/completions","",True),
    ("test-only","m","","https://example.invalid/v1",True),
    ("test-only","m","","https://example.invalid/chat/completions",True),
    ("test-only","m","","https://example.invalid",True),
])
def test_llm_readiness_is_local(monkeypatch,key,model,url,base,expected):
    for name,val in zip(("LLM_API_KEY","LLM_MODEL","LLM_API_URL","LLM_API_BASE"),(key,model,url,base)):
        monkeypatch.setenv(name,val)
    assert ps.llm_is_configured() is expected
    assert client.get("/health").json()["llm_configured"] is expected


def test_proficiency_absent_target_ids_filtered_before_frozen_bridge(monkeypatch):
    seen=[]
    class Evaluator:
        def evaluate(self, ability, evidence, audit):
            seen.append(ability.category["team_skill_id"])
            return SimpleNamespace(final_level="P2",to_dict=lambda:{"final_level":"P2"})
    monkeypatch.setattr(ps,"_build_evaluator",lambda _trace=None:Evaluator())
    profile=CandidateSkillProfile.from_dict(_candidate_profile())
    levels,details=ps.infer_proficiency_levels(profile,target_team_skill_ids=["T-AI-01","T-AI-01","T-AI-03"])
    assert seen==["T-AI-01"] and levels=={"T-AI-01":"P2"} and len(details)==1


def test_no_eligible_proficiency_never_calls_llm(monkeypatch):
    monkeypatch.setattr(ps,"_build_evaluator",lambda _trace=None:pytest.fail("must not call"))
    profile=_candidate_profile();profile["assessments"][0]["status"]="partially_supported"
    assert ps.infer_proficiency_levels(CandidateSkillProfile.from_dict(profile),target_team_skill_ids=["T-AI-01","T-AI-03"])==({},[])


def test_missing_proficiency_config_fails_closed(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY",raising=False)
    with pytest.raises(RuntimeError): ps._build_evaluator()


def test_evaluator_constructs_frozen_components_without_request(monkeypatch):
    for name,value in {"LLM_API_KEY":"test-only", "LLM_MODEL":"fake", "LLM_API_URL":"https://example.invalid/chat/completions"}.items():
        monkeypatch.setenv(name,value)
    from extractor.proficiency_evaluator import ProficiencyEvaluator
    assert isinstance(ps._build_evaluator(),ProficiencyEvaluator)


def test_auto_proficiency_matching_then_reuse_for_learning(monkeypatch):
    calls=[]
    class Evaluator:
        def evaluate(self,*args):
            calls.append(1)
            return SimpleNamespace(final_level="P2",to_dict=lambda:{"final_level":"P2"})
    monkeypatch.setattr(ps,"_build_evaluator",lambda _trace=None:Evaluator())
    monkeypatch.setenv("MATCHING_DECISION_THRESHOLD",str(THRESHOLD))
    matching=run_matching(candidate_profile=_candidate_profile(),job_id="133663124")
    assert len(calls)==1
    m=matching["match_result"];s=m["summary"]
    assert m["decision"] in {"MATCH","NO_MATCH"}
    assert sum(s[k] for k in ("satisfied","level_gap","evidence_insufficient","missing"))==s["required_skills"]
    learning=run_learning_path(candidate_profile=_candidate_profile(),job_id="133663124",proficiency_levels=matching["proficiency"]["levels"],auto_proficiency=False)
    assert len(calls)==1
    assert learning["proficiency"]["source"]=="provided"
    # 此处原先断言至少有一项落在 GRAPH_UNAVAILABLE。该档取决于 config/ 下恰好还缺
    # 哪一项能力的图谱，图谱补齐后便不再自然出现；其渲染契约改由
    # test_learning_path_api_compatibility 以受限图谱集显式构造覆盖。
    assert all(
        x["path_status"] in {"READY","NO_ACTION","GRAPH_UNAVAILABLE"}
        for x in learning["rendered"]["skill_paths"]
    )
    # Also execute the automatic learning service path deterministically.
    run_learning_path(candidate_profile=_candidate_profile(),job_id="133663124",auto_proficiency=True)
    assert len(calls)==2


@pytest.mark.parametrize("route",["/api/match","/api/learning-path"])
def test_malformed_requests(route):
    assert client.post(route,json={}).status_code==422
    assert client.post(route,json={"candidate_profile":{},"extra":1}).status_code==422
    assert client.post(route,json={"candidate_profile":_candidate_profile(),"proficiency_levels":{"T-AI-01":"P9"}}).status_code==422
    assert client.post(route,json={"candidate_profile":_candidate_profile(),"auto_proficiency":False}).status_code==400


@pytest.mark.parametrize("route,function",[("/api/match","run_matching"),("/api/learning-path","run_learning_path")])
def test_runtime_errors_are_502(monkeypatch,route,function):
    def fail(**kwargs): raise RuntimeError("simulated provider failure")
    monkeypatch.setattr(api,function,fail)
    assert client.post(route,json={"candidate_profile":_candidate_profile(),"job_id":"133663124"}).status_code==502


def test_target_selectors_and_errors():
    target=build_target_job_profile(job_id="133663124")
    assert resolve_target(target_job_profile=target,job_id=None,jd_key=None)==target
    with pytest.raises(ValueError): resolve_target(target_job_profile=target,job_id="x",jd_key=None)
    with pytest.raises(ValueError): build_target_job_profile()
    jobs=client.get("/api/jobs?limit=1").json()
    key=jobs["items"][0]["jd_key"]
    assert client.get("/api/target-job/unused",params={"jd_key":key}).status_code==200
    assert client.get("/api/target-job/NOT-A-JOB").status_code in {400,409}
    assert client.get("/api/jobs?limit=0").status_code==422


@pytest.mark.parametrize("filename,content,status",[("bad.exe",b"x",400),("empty.txt",b"",400)])
def test_bad_candidate_upload(filename,content,status):
    assert client.post("/api/candidate",files={"file":(filename,content)}).status_code==status


def test_oversized_upload(monkeypatch):
    monkeypatch.setenv("MAX_RESUME_BYTES","2")
    assert client.post("/api/candidate",files={"file":("x.txt",b"abc")}).status_code==413


def test_candidate_runtime_failure_is_502(monkeypatch):
    async def fail(*args,**kwargs): raise RuntimeError("simulated extraction failure")
    monkeypatch.setattr(api,"extract_candidate",fail)
    assert client.post("/api/candidate",files={"file":("x.txt",b"text")}).status_code==502


@pytest.mark.parametrize("failure",["timeout","nonzero","missing_output"])
def test_candidate_subprocess_fail_closed(monkeypatch,tmp_path,failure):
    path=tmp_path/"input.txt";path.write_text("anonymous test resume",encoding="utf-8")
    class Process:
        returncode=1 if failure=="nonzero" else 0
        killed=False
        async def communicate(self):
            if failure=="timeout" and not self.killed: raise asyncio.TimeoutError()
            return b"",b"simulated technical failure"
        def kill(self): self.killed=True
    process=Process()
    async def fake(*args,**kwargs): return process
    monkeypatch.setattr(cs.asyncio,"create_subprocess_exec",fake)
    with pytest.raises(RuntimeError): asyncio.run(cs.extract_candidate(path))
    if failure=="timeout": assert process.killed


def test_source_segments_reject_bad_metadata():
    assert cs._source_segments({"evidence_extraction":[]},"abcd")==[]
    assert cs._source_segments({"evidence_extraction":{"segments":{}}},"abcd")==[]
    bad=[None,{}, {"segment_id":"a"},{"segment_id":"a","section_type":"project","start":True,"end":2},
         {"segment_id":"a","section_type":"project","start":0,"end":10}]
    assert cs._source_segments({"evidence_extraction":{"segments":bad}},"abcd")==[]


@pytest.mark.parametrize("raw",["x|x","unknown"])
def test_job_summary_presence_contract(raw):
    with pytest.raises(ValueError): js._parse_presence(raw,{"x":"T-AI-01"})


@pytest.mark.parametrize("raw",["x", "x:P9", "x:P2;x:P2"])
def test_job_summary_level_contract(raw):
    with pytest.raises(ValueError): js._parse_levels(raw,{"x":"T-AI-01"})


def test_candidate_pdf_preflight_from_delivery():
    p=Path(__file__).resolve().parents[1]/"examples/anonymous_dev_resume.pdf"
    response=client.post("/api/candidate?preflight=true",files={"file":(p.name,p.read_bytes(),"application/pdf")})
    assert response.status_code==200 and response.json()["quality"]["passed"]
