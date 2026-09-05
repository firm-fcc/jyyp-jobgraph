from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from ..bootstrap import bootstrap_candidate_core
from ..config import CANDIDATE_CORE, OUTPUT_DIR, UPLOAD_DIR, candidate_timeout_seconds
from ..runtime_observability import RuntimeTrace, read_events

bootstrap_candidate_core()
from extractor.document_parser_v3 import parse_file_v3  # noqa: E402
from extractor.team_skill_registry import TeamSkillRegistry  # noqa: E402

_ALLOWED_SUFFIXES = {".pdf", ".docx", ".txt"}
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_candidate_id(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return f"upload_{uuid.uuid4().hex[:12]}"
    normalized = _SAFE_ID.sub("_", raw).strip("._-")
    return normalized[:80] or f"upload_{uuid.uuid4().hex[:12]}"


async def save_upload(filename: str, content: bytes) -> tuple[Path, str]:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise ValueError("only PDF, DOCX and TXT resumes are supported")
    if not content:
        raise ValueError("uploaded resume is empty")
    token = uuid.uuid4().hex
    path = UPLOAD_DIR / f"{token}{suffix}"
    path.write_bytes(content)
    return path, token


def preflight_resume(path: Path) -> dict[str, Any]:
    parsed = parse_file_v3(path)
    registry = TeamSkillRegistry()
    return {
        "schema_version": "candidate_preflight_v1",
        "parser": parsed.parser,
        "quality": {
            "passed": parsed.quality.passed,
            "fallback_required": parsed.quality.fallback_required,
            "flags": list(parsed.quality.flags),
            "char_count": parsed.quality.char_count,
            "nonempty_line_count": parsed.quality.nonempty_line_count,
            "readable_char_ratio": parsed.quality.readable_char_ratio,
            "page_count": parsed.quality.page_count,
            "empty_page_count": parsed.quality.empty_page_count,
            "empty_page_ratio": parsed.quality.empty_page_ratio,
        },
        "team_skill_registry_version": registry.version,
        "team_skill_count": len(registry),
    }


def _source_segments(
    diagnostics: dict[str, Any], resume_text: str
) -> list[dict[str, Any]]:
    evidence_diagnostics = diagnostics.get("evidence_extraction", {})
    if not isinstance(evidence_diagnostics, dict):
        return []
    raw_segments = evidence_diagnostics.get("segments", [])
    if not isinstance(raw_segments, list):
        return []

    result: list[dict[str, Any]] = []
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        segment_id = item.get("segment_id")
        section_type = item.get("section_type")
        start = item.get("start")
        end = item.get("end")
        if not isinstance(segment_id, str) or not segment_id.strip():
            continue
        if not isinstance(section_type, str) or not section_type.strip():
            continue
        if type(start) is not int or type(end) is not int:
            continue
        if not 0 <= start <= end <= len(resume_text):
            continue
        result.append(
            {
                "source_experience_id": segment_id,
                "section_type": section_type,
                "start": start,
                "end": end,
                "text": resume_text[start:end],
            }
        )
    return result


def _failure_detail(timing_path: Path, return_code: int) -> str:
    """把子进程的失败落到具体阶段与错误类型上。

    worker 不外传模型返回与简历正文，失败时父进程能拿到的只有退出码。仅凭
    退出码无从判断是解析、抽取还是链接环节出的问题，界面上的"技术细节"
    因而给不出可据以排障的信息。阶段名与异常类名本就写在 timing 里，且随
    diagnostics 一并回传，据此补全这一句不引入新的外泄面。
    """
    stage = None
    error_type = None
    for event in read_events(timing_path):
        if not isinstance(event, dict) or event.get("status") != "FAIL":
            continue
        if event.get("stage") in {"subprocess", "worker"}:
            continue
        stage = event.get("stage") or stage
        error_type = event.get("error_type") or error_type
    if stage is None:
        return (
            f"candidate extraction failed with exit code {return_code}; "
            "inspect safe stage timing"
        )
    suffix = f" ({error_type})" if error_type else ""
    return f"candidate extraction failed at stage {stage}{suffix}"


async def extract_candidate(
    path: Path,
    *,
    candidate_id: str | None = None,
    allow_low_quality_parser: bool = False,
) -> dict[str, Any]:
    cid = _safe_candidate_id(candidate_id)
    output_path = OUTPUT_DIR / f"{uuid.uuid4().hex}_{cid}.json"
    source_path = output_path.with_suffix('.source.json')
    timing_path = output_path.with_suffix('.timing.jsonl')
    trace = RuntimeTrace(timing_path)
    started = time.perf_counter()
    command = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "runtime_candidate_worker.py"),
        "--resume",
        str(path),
        "--candidate-id",
        cid,
        "--output",
        str(output_path),
    ]
    if allow_low_quality_parser:
        command.append("--allow-low-quality-parser")

    env = os.environ.copy()
    env['BACKEND_STAGE_TIMING_FILE'] = str(timing_path)
    trace.emit('subprocess', 'start')
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(CANDIDATE_CORE),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=candidate_timeout_seconds()
        )
    except (TimeoutError, asyncio.TimeoutError):
        process.kill()
        await process.communicate()
        trace.emit('subprocess', 'end', status='TIMEOUT', elapsed_seconds=round(time.perf_counter()-started, 6))
        source_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        raise RuntimeError("candidate extraction timed out")

    trace.emit('subprocess', 'end', status='PASS' if process.returncode == 0 else 'FAIL',
               elapsed_seconds=round(time.perf_counter()-started, 6), return_code=process.returncode)
    if process.returncode != 0:
        source_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        raise RuntimeError(_failure_detail(timing_path, process.returncode))
    if not output_path.exists():
        raise RuntimeError("candidate extraction finished without output JSON")

    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        if not source_path.exists():
            raise RuntimeError('candidate extraction missing canonical source sidecar')
        resume_text = json.loads(source_path.read_text(encoding='utf-8'))['resume_text']
    finally:
        output_path.unlink(missing_ok=True)
        source_path.unlink(missing_ok=True)

    diagnostics = payload.get("diagnostics", {})
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    diagnostics['backend_runtime_timing'] = read_events(timing_path)
    source_segments = _source_segments(diagnostics, resume_text)

    return {
        "schema_version": "candidate_api_response_v1_1",
        "candidate_id": cid,
        "candidate_skill_profile": payload["candidate_skill_profile"],
        "explicit_skill_mentions": payload.get("explicit_skill_mentions", []),
        "diagnostics": diagnostics,
        "grounded_capability_candidates": payload.get(
            "grounded_capability_candidates", []
        ),
        "resume_text": resume_text,
        "source_segments": source_segments,
        "experience_metadata_available": bool(source_segments),
        "runtime_schema": payload.get("schema_version"),
        "proficiency_status": payload.get("candidate_skill_profile", {})
        .get("metadata", {})
        .get("proficiency_status", "not_run"),
    }
