"""Strict loader for the project's anonymized real-resume JSON records.

This loader intentionally accepts only the internal ``real_resume_dataset_v1``
schema. Raw platform snapshots (for example objects with ``raw_visible_text``)
are rejected so development runs do not accidentally reintroduce direct PII.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


_CANDIDATE_ID_RE = re.compile(r"^candidate_\d{4}$")
_REQUIRED_FIELDS = {
    "schema_version",
    "candidate_id",
    "source_category",
    "resume_text",
    "sections",
}
_ALLOWED_FIELDS = _REQUIRED_FIELDS | {"source_file", "metadata"}


class InternalResumeRecordError(ValueError):
    """Raised when an internal anonymized resume record is malformed."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InternalResumeRecordError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise InternalResumeRecordError(f"non-standard JSON number is not allowed: {value}")


@dataclass(frozen=True)
class InternalResumeRecordV3:
    path: str
    candidate_id: str
    source_category: str
    resume_text: str
    sections: Mapping[str, tuple[str, ...]]
    metadata: Mapping[str, Any]
    source_file: str | None
    file_sha256: str


def load_internal_resume_record(path: str | Path) -> InternalResumeRecordV3:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() != ".json":
        raise InternalResumeRecordError("internal resume record must be a .json file")

    raw_bytes = path.read_bytes()
    try:
        payload = json.loads(
            raw_bytes.decode("utf-8-sig"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise InternalResumeRecordError("record must be UTF-8 JSON") from exc
    except json.JSONDecodeError as exc:
        raise InternalResumeRecordError("record must be one valid JSON object") from exc

    if not isinstance(payload, Mapping):
        raise InternalResumeRecordError("record root must be a JSON object")
    keys = set(payload)
    missing = _REQUIRED_FIELDS - keys
    unknown = keys - _ALLOWED_FIELDS
    if missing:
        raise InternalResumeRecordError(
            "record missing fields: " + ", ".join(sorted(missing))
        )
    if unknown:
        # Raw platform snapshots carry fields such as raw_visible_text/source.
        # Reject instead of silently accepting a PII-bearing schema.
        raise InternalResumeRecordError(
            "record contains unsupported fields for anonymized schema: "
            + ", ".join(sorted(unknown))
        )

    if payload["schema_version"] != "real_resume_dataset_v1":
        raise InternalResumeRecordError(
            "unsupported schema_version; only real_resume_dataset_v1 is accepted"
        )

    candidate_id = str(payload["candidate_id"]).strip()
    if not _CANDIDATE_ID_RE.fullmatch(candidate_id):
        raise InternalResumeRecordError("candidate_id must match candidate_XXXX")
    source_category = str(payload["source_category"]).strip()
    if not source_category:
        raise InternalResumeRecordError("source_category must be non-empty")
    resume_text = payload["resume_text"]
    if not isinstance(resume_text, str) or not resume_text.strip():
        raise InternalResumeRecordError("resume_text must be non-empty text")

    raw_sections = payload["sections"]
    if not isinstance(raw_sections, Mapping):
        raise InternalResumeRecordError("sections must be an object")
    sections: dict[str, tuple[str, ...]] = {}
    for name, values in raw_sections.items():
        if not isinstance(name, str) or not name.strip():
            raise InternalResumeRecordError("section name must be a non-empty string")
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise InternalResumeRecordError(f"sections.{name} must be an array of strings")
        sections[name] = tuple(v for v in values if v.strip())

    metadata = payload.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise InternalResumeRecordError("metadata must be an object")
    text_length = metadata.get("text_length")
    if text_length is not None:
        if isinstance(text_length, bool) or not isinstance(text_length, int):
            raise InternalResumeRecordError("metadata.text_length must be an integer")
        if text_length != len(resume_text):
            raise InternalResumeRecordError(
                "metadata.text_length does not match resume_text length"
            )

    source_file = payload.get("source_file")
    if source_file is not None and not isinstance(source_file, str):
        raise InternalResumeRecordError("source_file must be a string or null")

    return InternalResumeRecordV3(
        path=str(path),
        candidate_id=candidate_id,
        source_category=source_category,
        resume_text=resume_text,
        sections=sections,
        metadata=dict(metadata),
        source_file=source_file,
        file_sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )
