# -*- coding: utf-8 -*-
"""Target Job Profile Adapter v1.1.

Deterministic integration adapter from the algorithm group's single-JD summary
artifacts to a stable Team-Skill-ID target-job profile.

The adapter does not call a model, infer missing requirements, aggregate jobs,
or modify frozen candidate/learning-path semantics.
"""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

SCHEMA_VERSION = "target_job_profile_v1.1"
SOURCE_TYPE = "single_jd"
LEVELS = {"P1", "P2", "P3", "P4", "U"}
AUXILIARY_SKILL_IDS = {
    "F-1-01", "F-1-03", "F-1-04", "F-3-04", "F-4-01", "F-4-02"
}


class TargetJobProfileError(ValueError):
    pass


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_json(path: str | Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise TargetJobProfileError(f"JSON root must be object: {path}")
    return value


def _taxonomy_detail(taxonomy: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    detail = taxonomy.get("detail")
    if not isinstance(detail, dict) or not detail:
        raise TargetJobProfileError("taxonomy.detail must be a non-empty object")
    out: Dict[str, Dict[str, Any]] = {}
    for sid, raw in detail.items():
        if not isinstance(raw, dict):
            raise TargetJobProfileError(f"taxonomy skill {sid} must be object")
        if raw.get("code", sid) != sid:
            raise TargetJobProfileError(f"taxonomy code mismatch for {sid}")
        out[sid] = dict(raw)
    return out


def validate_taxonomy_compatibility(
    provider_taxonomy: Mapping[str, Any],
    canonical_taxonomy: Mapping[str, Any],
) -> Dict[str, Any]:
    """Fail closed on identity/semantic drift; display-name drift is allowed."""
    provider = _taxonomy_detail(provider_taxonomy)
    canonical = _taxonomy_detail(canonical_taxonomy)
    if set(provider) != set(canonical):
        missing = sorted(set(canonical) - set(provider))
        extra = sorted(set(provider) - set(canonical))
        raise TargetJobProfileError(
            f"taxonomy skill ID set mismatch; missing={missing}, extra={extra}"
        )
    name_differences: List[Dict[str, str]] = []
    for sid in sorted(canonical):
        p = provider[sid]
        c = canonical[sid]
        for field in ("code", "definition", "skill_type"):
            pv = p.get(field, sid if field == "code" else None)
            cv = c.get(field, sid if field == "code" else None)
            if pv != cv:
                raise TargetJobProfileError(
                    f"taxonomy semantic drift for {sid}.{field}: provider={pv!r}, canonical={cv!r}"
                )
        if p.get("name_zh") != c.get("name_zh"):
            name_differences.append({
                "team_skill_id": sid,
                "canonical_name": str(c.get("name_zh", "")),
                "provider_name": str(p.get("name_zh", "")),
            })
    return {
        "status": "PASS",
        "identity_rule": "team_skill_id",
        "semantic_fields_checked": ["code", "definition", "skill_type"],
        "display_name_difference_count": len(name_differences),
        "display_name_differences": name_differences,
    }


def _name_index(detail: Mapping[str, Mapping[str, Any]]) -> Dict[str, str]:
    by_name: Dict[str, str] = {}
    for sid, raw in detail.items():
        name = raw.get("name_zh")
        if not isinstance(name, str) or not name.strip():
            raise TargetJobProfileError(f"taxonomy skill {sid} has invalid name_zh")
        if name in by_name:
            raise TargetJobProfileError(f"duplicate taxonomy name_zh: {name!r}")
        by_name[name] = sid
    return by_name


def _job_indexes(jobs: Mapping[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    detail = jobs.get("detail")
    if not isinstance(detail, dict) or not detail:
        raise TargetJobProfileError("jobs.detail must be a non-empty object")
    by_id: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, str] = {}
    for jid, raw in detail.items():
        if not isinstance(raw, dict):
            raise TargetJobProfileError(f"job {jid} must be object")
        if raw.get("code", jid) != jid:
            raise TargetJobProfileError(f"job code mismatch for {jid}")
        name = raw.get("name_zh")
        if not isinstance(name, str) or not name.strip():
            raise TargetJobProfileError(f"job {jid} has invalid name_zh")
        if name in by_name:
            raise TargetJobProfileError(f"duplicate job name_zh: {name!r}")
        by_id[jid] = dict(raw)
        by_name[name] = jid
    return by_id, by_name


def _parse_skill_presence(raw: str, by_name: Mapping[str, str]) -> List[Tuple[str, str]]:
    names = [x.strip() for x in (raw or "").split("|") if x.strip()]
    if len(names) != len(set(names)):
        raise TargetJobProfileError("duplicate skill in skill_vec_01")
    result: List[Tuple[str, str]] = []
    for name in names:
        sid = by_name.get(name)
        if sid is None:
            raise TargetJobProfileError(f"unknown skill name in skill_vec_01: {name!r}")
        result.append((sid, name))
    return result


def _parse_proficiency(raw: str, by_name: Mapping[str, str]) -> Dict[str, Tuple[str, str]]:
    out: Dict[str, Tuple[str, str]] = {}
    for token in [x.strip() for x in (raw or "").split(";") if x.strip()]:
        if ":" not in token:
            raise TargetJobProfileError(f"invalid skill_vec_prof token: {token!r}")
        name, level = token.rsplit(":", 1)
        name, level = name.strip(), level.strip()
        sid = by_name.get(name)
        if sid is None:
            raise TargetJobProfileError(f"unknown skill name in skill_vec_prof: {name!r}")
        if level not in LEVELS:
            raise TargetJobProfileError(f"invalid JD proficiency level {level!r} for {name!r}")
        if sid in out:
            raise TargetJobProfileError(f"duplicate proficiency result for skill {sid}")
        out[sid] = (name, level)
    return out


def _parse_skillpoint_map(
    raw: Optional[str],
    by_name: Mapping[str, str],
) -> Dict[str, Tuple[str, ...]]:
    if raw is None:
        return {}
    if not isinstance(raw, str):
        raise TargetJobProfileError("skillpoint_map must be a string")
    if not raw.strip():
        return {}
    out: Dict[str, Tuple[str, ...]] = {}
    for raw_token in raw.split(";"):
        token = raw_token.strip()
        if not token:
            raise TargetJobProfileError("skillpoint_map contains an empty Team Skill token")
        if ":" not in token:
            raise TargetJobProfileError(f"invalid skillpoint_map token: {token!r}")
        name, raw_points = token.split(":", 1)
        name = name.strip()
        sid = by_name.get(name)
        if sid is None:
            raise TargetJobProfileError(f"unknown skill name in skillpoint_map: {name!r}")
        if sid in out:
            raise TargetJobProfileError(f"duplicate skill in skillpoint_map: {name!r}")
        points: List[str] = []
        for raw_point in raw_points.split(","):
            point = raw_point.strip()
            if point and point not in points:
                points.append(point)
        out[sid] = tuple(points)
    return out


def _edge_index(graph: Optional[Mapping[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    if graph is None:
        return {}
    if graph.get("relation") not in (None, "job_skill"):
        raise TargetJobProfileError("job_skill graph relation mismatch")
    edges = graph.get("edges")
    if not isinstance(edges, list):
        raise TargetJobProfileError("job_skill graph must contain edges array")
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            raise TargetJobProfileError("job_skill edge must be object")
        if edge.get("relation", "job_skill") != "job_skill":
            continue
        src, dst = edge.get("src"), edge.get("dst")
        if not isinstance(src, str) or not isinstance(dst, str):
            raise TargetJobProfileError("job_skill edge requires string src/dst")
        key = (src, dst)
        if key in out:
            raise TargetJobProfileError(f"duplicate job_skill edge: {src}->{dst}")
        out[key] = dict(edge)
    return out


def _market_signal(edge: Optional[Mapping[str, Any]], layer: Optional[str]) -> Optional[Dict[str, Any]]:
    if edge is None:
        return None
    value: Dict[str, Any] = {
        "graph_layer": layer,
        "is_probability": False,
        "origin": edge.get("origin", "base"),
    }
    for key in ("base_weight", "delta_weight", "effective_weight", "gap", "weight", "lambda"):
        if key in edge:
            value[key] = edge.get(key)
    return value


def _find_jd_row(csv_path: str | Path, *, jd_key: Optional[str], jobid: Optional[str]) -> Dict[str, str]:
    if bool(jd_key) == bool(jobid):
        raise TargetJobProfileError("provide exactly one selector: jd_key or jobid")
    matches: List[Dict[str, str]] = []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"jd_key", "jobid", "std_job", "skill_vec_01", "skill_vec_prof"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise TargetJobProfileError(f"JD summary missing required columns: {sorted(required)}")
        for row in reader:
            if jd_key is not None and row.get("jd_key") == jd_key:
                matches.append(row)
            elif jobid is not None and row.get("jobid") == str(jobid):
                matches.append(row)
    if len(matches) != 1:
        raise TargetJobProfileError(f"expected exactly one JD; found {len(matches)}")
    return matches[0]


def _requirement_semantics(sid: str, raw_level: Optional[str]) -> Tuple[str, Optional[str], bool, bool]:
    if sid in AUXILIARY_SKILL_IDS:
        if raw_level is not None:
            raise TargetJobProfileError(f"auxiliary skill {sid} unexpectedly graded: {raw_level}")
        return "AUXILIARY_NOT_GRADED", None, False, False
    if raw_level == "U":
        return "LEVEL_UNSPECIFIED", None, True, False
    if raw_level in {"P1", "P2", "P3", "P4"}:
        return "EXPLICIT_LEVEL", raw_level, True, True
    if raw_level is None:
        return "PROFICIENCY_NOT_AVAILABLE", None, False, False
    raise TargetJobProfileError(f"unexpected proficiency state: {raw_level!r}")


@dataclass(frozen=True)
class TargetJobProfileAdapter:
    provider_taxonomy_path: Path
    canonical_taxonomy_path: Path
    jobs_path: Path
    jd_summary_csv: Path
    job_skill_path: Optional[Path] = None
    window: Optional[str] = None
    graph_layer: Optional[str] = None

    @classmethod
    def from_paths(
        cls,
        *,
        provider_taxonomy_path: str | Path,
        canonical_taxonomy_path: str | Path,
        jobs_path: str | Path,
        jd_summary_csv: str | Path,
        job_skill_path: str | Path | None = None,
        window: str | None = None,
        graph_layer: str | None = None,
    ) -> "TargetJobProfileAdapter":
        layer = graph_layer
        if job_skill_path and layer is None:
            text = str(job_skill_path).replace("\\", "/")
            layer = "effective" if "/effective/" in text else "base" if "/base/" in text else "unknown"
        return cls(
            provider_taxonomy_path=Path(provider_taxonomy_path),
            canonical_taxonomy_path=Path(canonical_taxonomy_path),
            jobs_path=Path(jobs_path),
            jd_summary_csv=Path(jd_summary_csv),
            job_skill_path=Path(job_skill_path) if job_skill_path else None,
            window=window,
            graph_layer=layer,
        )

    def build_single_jd(self, *, jd_key: str | None = None, jobid: str | None = None) -> Dict[str, Any]:
        provider_taxonomy = _load_json(self.provider_taxonomy_path)
        canonical_taxonomy = _load_json(self.canonical_taxonomy_path)
        compatibility = validate_taxonomy_compatibility(provider_taxonomy, canonical_taxonomy)
        provider_detail = _taxonomy_detail(provider_taxonomy)
        canonical_detail = _taxonomy_detail(canonical_taxonomy)
        provider_name_to_id = _name_index(provider_detail)

        jobs = _load_json(self.jobs_path)
        by_job_id, job_name_to_id = _job_indexes(jobs)
        row = _find_jd_row(self.jd_summary_csv, jd_key=jd_key, jobid=jobid)
        presence = _parse_skill_presence(row.get("skill_vec_01", ""), provider_name_to_id)
        prof = _parse_proficiency(row.get("skill_vec_prof", ""), provider_name_to_id)
        skillpoints = _parse_skillpoint_map(row.get("skillpoint_map"), provider_name_to_id)
        presence_ids = {sid for sid, _ in presence}
        extra_prof = sorted(set(prof) - presence_ids)
        if extra_prof:
            raise TargetJobProfileError(
                "skill_vec_prof contains skills absent from skill_vec_01: " + ", ".join(extra_prof)
            )
        extra_skillpoints = sorted(set(skillpoints) - presence_ids)
        if extra_skillpoints:
            raise TargetJobProfileError(
                "skillpoint_map contains skills absent from skill_vec_01: "
                + ", ".join(extra_skillpoints)
            )

        std_job = (row.get("std_job") or "").strip()
        job_code = job_name_to_id.get(std_job)
        if job_code is None:
            raise TargetJobProfileError(f"std_job is not in jobs taxonomy: {std_job!r}")

        job_graph: Optional[Dict[str, Any]] = None
        edge_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
        if self.job_skill_path is not None:
            job_graph = _load_json(self.job_skill_path)
            graph_window = job_graph.get("window")
            if self.window and graph_window and self.window != graph_window:
                raise TargetJobProfileError(
                    f"window mismatch: adapter={self.window!r}, job_skill graph={graph_window!r}"
                )
            edge_map = _edge_index(job_graph)

        jd_key_value = str(row.get("jd_key") or "")
        skills: List[Dict[str, Any]] = []
        warnings: List[Dict[str, Any]] = []
        for sid, provider_name in presence:
            pmeta = provider_detail[sid]
            cmeta = canonical_detail[sid]
            raw_level = prof.get(sid, (None, None))[1]
            status, required_level, target_eligible, level_eligible = _requirement_semantics(sid, raw_level)
            if status == "PROFICIENCY_NOT_AVAILABLE":
                warnings.append({
                    "code": "MISSING_PRIMARY_PROFICIENCY",
                    "team_skill_id": sid,
                    "message": "Primary skill is present in skill_vec_01 but absent from skill_vec_prof.",
                })

            edge = edge_map.get((job_code, sid))
            if edge is None and self.job_skill_path is not None:
                warnings.append({
                    "code": "MARKET_EDGE_NOT_AVAILABLE",
                    "team_skill_id": sid,
                    "message": "No standard-job market edge was found for this JD-required skill.",
                })

            provenance = f"structured_jd_summary:{self.window or 'unknown'}:{jd_key_value}:skill_vec_01:{sid}"
            skill_points = skillpoints.get(sid, ())
            skill_item = {
                "team_skill_id": sid,
                "team_skill_name": cmeta.get("name_zh"),
                "provider_skill_name": pmeta.get("name_zh"),
                "skill_type": cmeta.get("skill_type"),
                "is_primary": sid not in AUXILIARY_SKILL_IDS,
                "requirement_present": True,
                "required_level_raw": raw_level,
                "required_level": required_level,
                "requirement_status": status,
                "learning_path_target_eligible": target_eligible,
                "level_comparison_eligible": level_eligible,
                "requirement_evidence_kind": "STRUCTURED_JD_SUMMARY_PROVENANCE",
                "requirement_evidence_ref": provenance,
                "market_signal": _market_signal(edge, self.graph_layer),
                "skill_points": list(skill_points),
            }
            if skill_points:
                skill_item["skill_point_evidence_ref"] = (
                    f"structured_jd_summary:{self.window or 'unknown'}:{jd_key_value}:"
                    f"skillpoint_map:{sid}"
                )
            skills.append(skill_item)

        skills.sort(key=lambda x: x["team_skill_id"])
        warnings.sort(key=lambda x: (x.get("code", ""), x.get("team_skill_id", "")))
        return {
            "schema_version": SCHEMA_VERSION,
            "source_type": SOURCE_TYPE,
            "window": self.window or (job_graph.get("window") if job_graph else None),
            "job": {
                "job_code": job_code,
                "job_name": by_job_id[job_code].get("name_zh"),
                "jd_key": row.get("jd_key"),
                "jobid": row.get("jobid"),
                "title": row.get("title"),
                "std_job": std_job,
                "opentime": row.get("opentime"),
                "level": row.get("level"),
                "level_source": row.get("level_source"),
                "techstack": row.get("techstack"),
            },
            "taxonomy": {
                "provider_version": provider_taxonomy.get("version"),
                "canonical_version": canonical_taxonomy.get("version"),
                "provider_taxonomy_sha256": sha256_file(self.provider_taxonomy_path),
                "canonical_taxonomy_sha256": sha256_file(self.canonical_taxonomy_path),
                "taxonomy_compatibility": compatibility,
                "identity_rule": "team_skill_id",
            },
            "source_provenance": {
                "jd_summary_sha256": sha256_file(self.jd_summary_csv),
                "jobs_sha256": sha256_file(self.jobs_path),
                "job_skill_sha256": sha256_file(self.job_skill_path) if self.job_skill_path else None,
                "graph_layer": self.graph_layer,
                "raw_jd_evidence_available": False,
                "skillpoint_map_available": bool(skillpoints),
            },
            "semantics": {
                "jd_U": "LEVEL_UNSPECIFIED",
                "jd_U_is_P1": False,
                "market_weight_is_probability": False,
                "market_weight_role": "advisory_only_not_ranked_in_v1.1",
            },
            "skills": skills,
            "warnings": warnings,
        }


def write_json(value: Mapping[str, Any], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")


__all__ = [
    "AUXILIARY_SKILL_IDS",
    "SCHEMA_VERSION",
    "SOURCE_TYPE",
    "TargetJobProfileAdapter",
    "TargetJobProfileError",
    "validate_taxonomy_compatibility",
    "sha256_file",
    "write_json",
]
