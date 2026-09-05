from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=False)

from backend.config import CANDIDATE_CORE, WINDOW, matching_threshold  # noqa: E402
from backend.schemas import HealthResponse, LearningPathRequest, MatchRequest  # noqa: E402
from backend.services.candidate_service import (  # noqa: E402
    extract_candidate,
    preflight_resume,
    save_upload,
)
from backend.services.learning_path_service import run_learning_path  # noqa: E402
from backend.services.job_catalog_service import search_jobs, std_job_index  # noqa: E402
from backend.services.matching_service import run_matching  # noqa: E402
from backend.services.proficiency_service import llm_is_configured  # noqa: E402
from backend.services.target_job_service import build_target_job_profile  # noqa: E402
from backend.services.job_summary_service import (  # noqa: E402
    JobSummaryNotFoundError,
    build_job_summary,
)
from backend.services.aggregated_target_job_service import (  # noqa: E402
    build_aggregated_target_job_profile,
)

app = FastAPI(
    title="Challenge26 Candidate × Job Backend",
    version="1.0.0",
    description=(
        "FastAPI handoff for frozen Candidate r4.3.4, TargetJobProfile v1.1, "
        "Matching v1 and frozen Learning Path core."
    ),
)

def configured_cors_origins() -> list[str]:
    # Keep the existing middleware; accept the delivery template's explicit name.
    raw = os.getenv("CORS_ALLOWED_ORIGINS")
    if raw is None:
        raw = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    result = [item.strip() for item in raw.split(",") if item.strip()]
    if "*" in result:
        raise ValueError("CORS requires explicit origins when credentials are allowed")
    return result


origins = configured_cors_origins()
# 接入侧扩展：前端 dev server 的端口随实例而变（vite 端口占用时自动顺延），
# 逐个登记不现实。本服务只监听回环地址，故另按正则放行本机任意端口；
# 交付包新加的通配校验仍然生效 —— 这里放行的是 localhost 与 127.0.0.1，
# 不是任意来源。需收紧时把 CORS_ORIGIN_REGEX 置空即可。
origin_regex = os.getenv(
    "CORS_ORIGIN_REGEX",
    r"http://(localhost|127\.0\.0\.1)(:\d+)?",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _curated_graph_count() -> int:
    """收录的能力发展图谱份数。逐批补齐，故按 config/ 下的文件实算而非写死。"""
    return len(list((CANDIDATE_CORE / "config").glob("skill_development_graph_*.json")))


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    threshold_error = False
    try:
        calibrated = matching_threshold() is not None
    except ValueError:
        calibrated = False
        threshold_error = True
    return HealthResponse(
        matching_calibrated=calibrated,
        llm_configured=llm_is_configured(),
        window=WINDOW,
        limitations={
            "matching_decision": "CALIBRATED" if calibrated else "NOT_CALIBRATED",
            "learning_path_curated_graph_count": _curated_graph_count(),
            "job_window": WINDOW,
            "matching_threshold_invalid": threshold_error,
        },
    )


@app.post("/api/candidate")
async def candidate_api(
    file: UploadFile = File(...),
    candidate_id: str | None = Form(default=None),
    preflight: bool = Query(default=False),
    allow_low_quality_parser: bool = Query(default=False),
):
    max_bytes = int(os.getenv("MAX_RESUME_BYTES", str(10 * 1024 * 1024)))
    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"resume exceeds {max_bytes} bytes")

    path = None
    try:
        path, _ = await save_upload(file.filename or "resume", content)
        if preflight:
            return await run_in_threadpool(preflight_resume, path)
        return await extract_candidate(
            path,
            candidate_id=candidate_id,
            allow_low_quality_parser=allow_low_quality_parser,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


@app.get("/api/jobs")
def jobs_api(
    q: str = Query(default=""),
    limit: int = Query(default=30, ge=1, le=100),
    # 接入侧扩展：按标准岗位名精确取该岗位下的 JD 列表，见 job_catalog_service
    std_job: str = Query(default="", description="Exact standard job name filter"),
):
    try:
        return search_jobs(q, limit, std_job)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/job-index")
def job_index_api():
    """接入侧扩展：本窗口每个标准岗位名下的 JD 条数。

    前端的岗位选择器据此判断哪些岗位可进入匹配 —— 本窗口没有 JD 的岗位
    不列为可选项，否则点进去只会拿到一个空列表。
    """
    counts = std_job_index()
    return {
        "schema_version": "job_index_v1",
        "window": WINDOW,
        "total_jd": sum(counts.values()),
        "counts": counts,
    }


@app.get("/api/target-job/{job_id}")
def target_job_api(
    job_id: str,
    jd_key: str | None = Query(default=None, description="If provided, select by jd_key instead of job_id"),
):
    try:
        return build_target_job_profile(job_id=None if jd_key else job_id, jd_key=jd_key)
    except ValueError as exc:
        detail = str(exc)
        status = 409 if "expected exactly one JD" in detail else 400
        raise HTTPException(status_code=status, detail=detail) from exc


@app.get("/api/target-job-profile/{job_code}")
def aggregated_target_job_api(job_code: str):
    """按岗位（而非单条招聘信息）给出的比对基准。

    与 /api/target-job/{job_id} 同为 target_job_profile v1.1，差别在取数范围：
    那一个取窗口内的某一条招聘信息，本接口把该岗位窗口内的全部条目合成一份，
    每项要求带着它的原始计数。口径见 aggregated_target_job_service。
    """
    try:
        return build_aggregated_target_job_profile(job_code)
    except JobSummaryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/job-summary/{job_code}")
def job_summary_api(job_code: str):
    try:
        return build_job_summary(job_code)
    except JobSummaryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/match")
def match_api(request: MatchRequest):
    try:
        return run_matching(**request.model_dump())
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/learning-path")
def learning_path_api(request: LearningPathRequest):
    try:
        return run_learning_path(**request.model_dump())
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
