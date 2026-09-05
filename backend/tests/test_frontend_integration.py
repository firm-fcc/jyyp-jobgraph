"""Browser-facing contracts only; no model inference or Gold access."""
from pathlib import Path

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
import app as api

client = TestClient(api.app)
ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


@pytest.mark.parametrize("origin", ORIGINS)
@pytest.mark.parametrize("method,path", [("GET", "/health"), ("POST", "/api/candidate")])
def test_cors_preflight(origin, method, path):
    response = client.options(path, headers={"Origin": origin,
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": "content-type"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert method in response.headers["access-control-allow-methods"]


@pytest.mark.parametrize("origin", ORIGINS)
def test_cors_get_and_post(origin):
    response = client.get("/health", headers={"Origin": origin})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    # Actual parser request, explicitly preflight=true: never calls a model.
    text = "匿名结构示例\n项目经历：实现模型训练，并完成验证与部署。\n教育经历：计算机专业。\n技能：Python、模型评估、数据分析及可复现实验。"
    response = client.post("/api/candidate?preflight=true", headers={"Origin": origin},
        files={"file": ("structure_example.txt", text.encode(), "text/plain")})
    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == origin


def test_cors_unapproved_origin_has_no_allow_header():
    headers = {"Origin": "https://unapproved.invalid"}
    for response in (client.get("/health", headers=headers),
                     client.post("/api/match", headers=headers, json={}),
                     client.options("/api/match", headers={**headers, "Access-Control-Request-Method": "POST"})):
        assert "access-control-allow-origin" not in response.headers


def test_cors_config_alias_and_no_wildcard(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://legacy.invalid")
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    assert api.configured_cors_origins() == ["http://legacy.invalid"]
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173, http://127.0.0.1:5173")
    assert api.configured_cors_origins() == list(ORIGINS)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")
    with pytest.raises(ValueError, match="explicit origins"):
        api.configured_cors_origins()
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "")
    assert api.configured_cors_origins() == []


def test_openapi_routes_and_request_contracts():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    spec = response.json()
    # 岗位索引与按岗位编码聚合的基准是后加的两条，此前遗漏在这份清单之外。
    expected = {"/health": "get", "/api/jobs": "get", "/api/candidate": "post",
        "/api/job-index": "get", "/api/target-job/{job_id}": "get",
        "/api/target-job-profile/{job_code}": "get",
        "/api/job-summary/{job_code}": "get",
        "/api/match": "post", "/api/learning-path": "post"}
    assert set(spec["paths"]) == set(expected)
    for path, method in expected.items():
        assert method in spec["paths"][path]
    candidate = spec["paths"]["/api/candidate"]["post"]
    assert "multipart/form-data" in candidate["requestBody"]["content"]
    assert spec["components"]["schemas"]["MatchRequest"]["additionalProperties"] is False
    assert spec["components"]["schemas"]["LearningPathRequest"]["additionalProperties"] is False


def test_deployment_template_threshold_and_cors(monkeypatch):
    values = dotenv_values(Path(__file__).resolve().parents[1] / ".env.example")
    for name in ("LLM_API_KEY", "LLM_MODEL", "LLM_API_URL"):
        assert values[name] == ""
    assert values["MATCHING_DECISION_THRESHOLD"] == "0.380952"
    assert values["CORS_ALLOWED_ORIGINS"].split(",") == list(ORIGINS)
    monkeypatch.setenv("MATCHING_DECISION_THRESHOLD", values["MATCHING_DECISION_THRESHOLD"])
    health = client.get("/health").json()
    assert health["matching_calibrated"] is True
