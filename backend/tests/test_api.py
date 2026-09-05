from __future__ import annotations

import asyncio
import csv
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import app
from backend.config import CANDIDATE_CORE, CANONICAL_SKILLS, JD_SUMMARY
from backend.services import candidate_service

client = TestClient(app)


def _candidate_profile():
    return {
        "candidate_id": "demo_candidate",
        "skill_registry_version": "0.4",
        "assessments": [
            {
                "candidate_id": "demo_candidate",
                "team_skill_id": "T-AI-01",
                "team_skill_name": "机器学习与深度学习",
                "status": "supported",
                "inference_mode": "direct_behavior",
                "evidence": [
                    {
                        "text": "独立训练并优化深度学习模型，完成部署验证。",
                        "source_experience_id": "demo_project",
                        "start": 0,
                        "end": 22,
                        "fact": "",
                        "behavior": "",
                        "context": "",
                        "result": "",
                    }
                ],
                "reason": "demo fixture",
                "confidence": 0.95,
                "atomic_abilities": ["训练并优化深度学习模型"],
                "audit_flags": [],
            }
        ],
        "metadata": {"schema_version": "candidate_skill_profile_v4_3_4"},
    }


def test_health_without_llm_configuration(monkeypatch):
    for name in ("LLM_API_KEY", "LLM_MODEL", "LLM_API_URL", "LLM_API_BASE"):
        monkeypatch.delenv(name, raising=False)
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["candidate_runtime"] == "r4.3.4"
    assert payload["matching_schema"] == "match_result_v1"
    assert payload["llm_configured"] is False
    assert payload["limitations"]["job_window"] == "2022-10"


def test_health_with_llm_configuration_does_not_call_llm(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_API_BASE", "https://provider.invalid/v1")
    monkeypatch.delenv("LLM_API_URL", raising=False)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["llm_configured"] is True


def test_candidate_preflight_without_llm():
    text = (
        "候选人测试简历\n"
        "项目经历：独立完成机器学习模型训练、验证与部署，并对误差进行分析。\n"
        "教育经历：计算机相关专业。\n"
        "技能与成果：Python、数据分析、模型评估，具备完整项目文档与复现实验记录。\n"
    )
    response = client.post(
        "/api/candidate?preflight=true",
        files={"file": ("resume.txt", text.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["team_skill_count"] == 49
    assert payload["team_skill_registry_version"] == "0.4"


def test_real_target_job_133663124():
    response = client.get("/api/target-job/133663124")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema_version"] == "target_job_profile_v1.1"
    assert payload["job"]["jobid"] == "133663124"
    assert payload["semantics"]["jd_U_is_P1"] is False


def test_matching_manual_proficiency_not_calibrated():
    response = client.post(
        "/api/match",
        json={
            "candidate_profile": _candidate_profile(),
            "job_id": "133663124",
            "proficiency_levels": {"T-AI-01": "P3"},
            "auto_proficiency": False,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    result = payload["match_result"]
    assert result["schema_version"] == "match_result_v1"
    assert result["decision"] == "NOT_CALIBRATED"
    assert result["summary"]["required_skills"] == 7
    assert result["summary"]["satisfied"] >= 1


def test_learning_path_manual_proficiency():
    response = client.post(
        "/api/learning-path",
        json={
            "candidate_profile": _candidate_profile(),
            "job_id": "133663124",
            "proficiency_levels": {"T-AI-01": "P3"},
            "auto_proficiency": False,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema_version"] == "learning_path_api_response_v1"
    assert payload["gap_summary"]["total_requirements"] == 7
    # 收录的图谱数随 config/ 下的文件增减而变，写死一个具体数会在每次补齐后失效。
    assert payload["diagnostics"]["curated_graph_count"] == len(
        list((CANDIDATE_CORE / "config").glob("skill_development_graph_*.json"))
    )


def test_job_catalog_search():
    response = client.get("/api/jobs?q=算法工程师&limit=5")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema_version"] == "job_catalog_response_v1"
    assert 1 <= len(payload["items"]) <= 5
    assert all("jobid" in item and "jd_key" in item for item in payload["items"])


class _FakeProcess:
    returncode = 0

    async def communicate(self):
        return b"ok", b""

    def kill(self):
        raise AssertionError("fake process should not be killed")


def _fake_candidate_payload(text: str, *, segments: list[dict] | None = None) -> dict:
    evidence_text = "实现模型训练"
    start = text.index(evidence_text)
    end = start + len(evidence_text)
    return {
        "schema_version": "resume_capability_v3_run_ready_r4_3_4",
        "candidate_skill_profile": {
            "candidate_id": "candidate_api_test",
            "skill_registry_version": "0.4",
            "assessments": [
                {
                    "candidate_id": "candidate_api_test",
                    "team_skill_id": "T-AI-01",
                    "team_skill_name": "机器学习与深度学习",
                    "status": "supported",
                    "inference_mode": "direct_behavior",
                    "evidence": [
                        {
                            "text": evidence_text,
                            "source_experience_id": "resume_full",
                            "start": start,
                            "end": end,
                        }
                    ],
                    "reason": "test fixture",
                    "confidence": 0.9,
                    "atomic_abilities": [],
                    "audit_flags": [],
                }
            ],
            "metadata": {"proficiency_status": "not_run_in_preuse_entrypoint"},
        },
        "grounded_capability_candidates": [{"candidate_id": "capability_1"}],
        "explicit_skill_mentions": [],
        "diagnostics": {
            "evidence_extraction": {
                "mode": "segment_aware_batched" if segments else "full_resume",
                "segments": segments or [],
            }
        },
    }


def _run_mocked_candidate(monkeypatch, tmp_path: Path, text: str, payload: dict) -> dict:
    resume_path = tmp_path / "resume.txt"
    resume_path.write_text(text, encoding="utf-8")

    async def fake_exec(*command, **kwargs):
        output_path = Path(command[command.index("--output") + 1])
        output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        output_path.with_suffix('.source.json').write_text(json.dumps({'resume_text':text}),encoding='utf-8')
        return _FakeProcess()

    monkeypatch.setattr(candidate_service.asyncio, "create_subprocess_exec", fake_exec)
    return asyncio.run(
        candidate_service.extract_candidate(
            resume_path,
            candidate_id="candidate_api_test",
        )
    )


def test_candidate_response_v11_full_resume_keeps_compatibility(monkeypatch, tmp_path):
    text = "候选人\n项目经历\n实现模型训练，并完成验证与部署。\n技能：Python。"
    result = _run_mocked_candidate(
        monkeypatch, tmp_path, text, _fake_candidate_payload(text)
    )
    assert result["schema_version"] == "candidate_api_response_v1_1"
    for field in (
        "candidate_skill_profile",
        "explicit_skill_mentions",
        "diagnostics",
        "runtime_schema",
        "proficiency_status",
    ):
        assert field in result
    assert result["resume_text"] == text
    assert result["grounded_capability_candidates"] == [{"candidate_id": "capability_1"}]
    assert result["source_segments"] == []
    assert result["experience_metadata_available"] is False
    for assessment in result["candidate_skill_profile"]["assessments"]:
        for evidence in assessment["evidence"]:
            start, end = evidence["start"], evidence["end"]
            assert 0 <= start <= end <= len(result["resume_text"])
            assert result["resume_text"][start:end] == evidence["text"]


def test_candidate_segment_text_uses_exact_canonical_offsets(monkeypatch, tmp_path):
    text = "候选人\n项目经历\n实现模型训练，并完成验证与部署。\n技能：Python。"
    segment_text = "项目经历\n实现模型训练，并完成验证与部署。"
    start = text.index(segment_text)
    end = start + len(segment_text)
    segments = [
        {
            "segment_id": "project_001",
            "section_type": "project_experience",
            "start": start,
            "end": end,
        },
        {
            "segment_id": "invalid_out_of_range",
            "section_type": "project_experience",
            "start": 0,
            "end": len(text) + 1,
        },
    ]
    result = _run_mocked_candidate(
        monkeypatch, tmp_path, text, _fake_candidate_payload(text, segments=segments)
    )
    assert result["experience_metadata_available"] is True
    assert result["source_segments"] == [
        {
            "source_experience_id": "project_001",
            "section_type": "project_experience",
            "start": start,
            "end": end,
            "text": text[start:end],
        }
    ]


def test_candidate_endpoint_response_has_v11_fields(monkeypatch):
    async def fake_extract(path, *, candidate_id, allow_low_quality_parser):
        return {
            "schema_version": "candidate_api_response_v1_1",
            "candidate_id": candidate_id,
            "candidate_skill_profile": {},
            "explicit_skill_mentions": [],
            "diagnostics": {},
            "grounded_capability_candidates": [],
            "resume_text": "测试简历文本",
            "source_segments": [],
            "experience_metadata_available": False,
            "runtime_schema": "resume_capability_v3_run_ready_r4_3_4",
            "proficiency_status": "not_run",
        }

    monkeypatch.setattr("app.extract_candidate", fake_extract)
    response = client.post(
        "/api/candidate",
        data={"candidate_id": "api_contract_test"},
        files={"file": ("resume.txt", "测试简历文本".encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema_version"] == "candidate_api_response_v1_1"
    assert payload["resume_text"] == "测试简历文本"
    assert payload["source_segments"] == []


def test_aggregated_job_summary_aid_01():
    response = client.get("/api/job-summary/AID-01")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema_version"] == "aggregated_job_summary_v1"
    assert payload["source_type"] == "aggregated_job_summary"
    assert payload["job"]["job_name"] == "算法工程师"
    assert payload["job"]["jd_count"] > 1
    assert payload["taxonomy"]["identity_rule"] == "team_skill_id"
    canonical_ids = set(json.loads(CANONICAL_SKILLS.read_text(encoding="utf-8"))["detail"])
    assert {item["team_skill_id"] for item in payload["skills"]} == canonical_ids
    for item in payload["skills"]:
        assert 0.0 <= item["jd_presence_rate"] <= 1.0
        assert sum(item["level_distribution"].values()) == item["jd_presence_count"]
    assert payload["semantics"]["required_level_synthesized"] is False
    assert payload["semantics"]["jd_U_is_P1"] is False


def test_aggregated_job_summary_manual_skill_recount():
    payload = client.get("/api/job-summary/AID-01").json()
    canonical = json.loads(CANONICAL_SKILLS.read_text(encoding="utf-8"))["detail"]
    skill_id = "T-AI-01"
    skill_name = canonical[skill_id]["name_zh"]
    with open(JD_SUMMARY, encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["std_job"] == "算法工程师"]
    manual_count = sum(
        skill_name in {value.strip() for value in row["skill_vec_01"].split("|")}
        for row in rows
    )
    by_id = {item["team_skill_id"]: item for item in payload["skills"]}
    assert by_id[skill_id]["jd_presence_count"] == manual_count


def test_aggregated_job_summary_unknown_code_is_404():
    response = client.get("/api/job-summary/NOT-A-JOB")
    assert response.status_code == 404
