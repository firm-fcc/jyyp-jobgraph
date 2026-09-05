from __future__ import annotations

import csv
import json
from functools import lru_cache
from typing import Any

from ..bootstrap import bootstrap_candidate_core
from ..config import CANONICAL_SKILLS, JD_SUMMARY, JOBS, JOB_SKILL, PROVIDER_SKILLS, WINDOW

bootstrap_candidate_core()
from extractor.target_job_profile_adapter import (  # noqa: E402
    AUXILIARY_SKILL_IDS,
    sha256_file,
    validate_taxonomy_compatibility,
)

_LEVELS = ("P1", "P2", "P3", "P4", "U", "NOT_AVAILABLE")


class JobSummaryNotFoundError(ValueError):
    pass


def _load_object(path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return value


def _taxonomy_detail(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    detail = value.get("detail")
    if not isinstance(detail, dict) or not detail:
        raise ValueError("taxonomy.detail must be a non-empty object")
    return {str(key): dict(item) for key, item in detail.items() if isinstance(item, dict)}


def _name_index(detail: dict[str, dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for skill_id, item in detail.items():
        name = item.get("name_zh")
        if not isinstance(name, str) or not name.strip() or name in result:
            raise ValueError("taxonomy name_zh must be non-empty and unique")
        result[name] = skill_id
    return result


def _parse_presence(raw: str, by_name: dict[str, str]) -> list[str]:
    names = [item.strip() for item in (raw or "").split("|") if item.strip()]
    if len(names) != len(set(names)):
        raise ValueError("duplicate skill in skill_vec_01")
    result: list[str] = []
    for name in names:
        skill_id = by_name.get(name)
        if skill_id is None:
            raise ValueError(f"unknown skill name in skill_vec_01: {name!r}")
        result.append(skill_id)
    return result


def _parse_levels(raw: str, by_name: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for token in [item.strip() for item in (raw or "").split(";") if item.strip()]:
        if ":" not in token:
            raise ValueError(f"invalid skill_vec_prof token: {token!r}")
        name, level = (item.strip() for item in token.rsplit(":", 1))
        skill_id = by_name.get(name)
        if skill_id is None or level not in _LEVELS[:-1] or skill_id in result:
            raise ValueError(f"invalid skill_vec_prof entry: {name!r}")
        result[skill_id] = level
    return result


@lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    provider = _load_object(PROVIDER_SKILLS)
    canonical = _load_object(CANONICAL_SKILLS)
    compatibility = validate_taxonomy_compatibility(provider, canonical)
    provider_detail = _taxonomy_detail(provider)
    canonical_detail = _taxonomy_detail(canonical)
    if set(provider_detail) != set(canonical_detail):
        raise ValueError("provider/canonical taxonomy IDs differ")

    jobs = _load_object(JOBS)
    jobs_detail = jobs.get("detail")
    if not isinstance(jobs_detail, dict):
        raise ValueError("jobs.detail must be an object")

    graph = _load_object(JOB_SKILL)
    if graph.get("window") not in (None, WINDOW):
        raise ValueError("job_skill window mismatch")
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in graph.get("edges", []):
        if not isinstance(edge, dict) or edge.get("relation", "job_skill") != "job_skill":
            continue
        key = (str(edge.get("src", "")), str(edge.get("dst", "")))
        if not all(key) or key in edges:
            raise ValueError("invalid or duplicate job_skill edge")
        edges[key] = dict(edge)

    with open(JD_SUMMARY, encoding="utf-8-sig", newline="") as handle:
        rows = tuple(dict(row) for row in csv.DictReader(handle))
    return {
        "provider": provider,
        "canonical": canonical,
        "canonical_detail": canonical_detail,
        "provider_name_to_id": _name_index(provider_detail),
        "jobs_detail": jobs_detail,
        "rows": rows,
        "edges": edges,
        "compatibility": compatibility,
    }


def build_job_summary(job_code: str) -> dict[str, Any]:
    code = job_code.strip()
    data = _data()
    job = data["jobs_detail"].get(code)
    if not isinstance(job, dict):
        raise JobSummaryNotFoundError(f"unknown standard job code: {code}")
    job_name = job.get("name_zh")
    if not isinstance(job_name, str) or not job_name.strip():
        raise ValueError(f"standard job has no valid name_zh: {code}")

    rows = [row for row in data["rows"] if row.get("std_job") == job_name]
    if not rows:
        raise JobSummaryNotFoundError(f"no {WINDOW} JD rows for standard job code: {code}")

    canonical_detail = data["canonical_detail"]
    counts = {skill_id: 0 for skill_id in canonical_detail}
    distributions = {
        skill_id: {level: 0 for level in _LEVELS} for skill_id in canonical_detail
    }
    for row in rows:
        presence = _parse_presence(row.get("skill_vec_01", ""), data["provider_name_to_id"])
        levels = _parse_levels(row.get("skill_vec_prof", ""), data["provider_name_to_id"])
        extra = set(levels) - set(presence)
        if extra:
            raise ValueError("skill_vec_prof contains a skill absent from skill_vec_01")
        for skill_id in presence:
            counts[skill_id] += 1
            distributions[skill_id][levels.get(skill_id, "NOT_AVAILABLE")] += 1

    skills: list[dict[str, Any]] = []
    jd_count = len(rows)
    for skill_id, metadata in canonical_detail.items():
        edge = data["edges"].get((code, skill_id))
        market_signal = {
            "graph_layer": "effective",
            "is_probability": False,
            "base_weight": edge.get("base_weight") if edge else None,
            "delta_weight": edge.get("delta_weight") if edge else None,
            "effective_weight": edge.get("effective_weight") if edge else None,
        }
        skills.append(
            {
                "team_skill_id": skill_id,
                "team_skill_name": metadata.get("name_zh"),
                "skill_type": metadata.get("skill_type"),
                "is_primary": skill_id not in AUXILIARY_SKILL_IDS,
                "jd_presence_count": counts[skill_id],
                "jd_presence_rate": round(counts[skill_id] / jd_count, 6),
                "level_distribution": distributions[skill_id],
                "market_signal": market_signal,
            }
        )

    skills.sort(
        key=lambda item: (
            item["market_signal"]["effective_weight"] is None,
            -(item["market_signal"]["effective_weight"] or 0.0),
            -item["jd_presence_rate"],
            item["team_skill_id"],
        )
    )
    return {
        "schema_version": "aggregated_job_summary_v1",
        "source_type": "aggregated_job_summary",
        "window": WINDOW,
        "job": {"job_code": code, "job_name": job_name, "jd_count": jd_count},
        "skills": skills,
        "taxonomy": {
            "provider_version": data["provider"].get("version"),
            "canonical_version": data["canonical"].get("version"),
            "identity_rule": "team_skill_id",
            "taxonomy_compatibility": data["compatibility"],
        },
        "provenance": {
            "aggregation": "deterministic_full_window_statistics_no_threshold",
            "jd_filter": {"field": "std_job", "value": job_name},
            "jd_summary_sha256": sha256_file(JD_SUMMARY),
            "jobs_sha256": sha256_file(JOBS),
            "job_skill_sha256": sha256_file(JOB_SKILL),
            "provider_taxonomy_sha256": sha256_file(PROVIDER_SKILLS),
            "canonical_taxonomy_sha256": sha256_file(CANONICAL_SKILLS),
            "raw_jd_evidence_available": False,
        },
        "semantics": {
            "matching_input": False,
            "matching_decision_available": False,
            "required_level_synthesized": False,
            "jd_U": "LEVEL_UNSPECIFIED",
            "jd_U_is_P1": False,
            "market_weight_is_probability": False,
        },
    }


__all__ = ["JobSummaryNotFoundError", "build_job_summary"]
