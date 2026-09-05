"""Offline data contracts for the evidence-driven agentic workflow.

This module intentionally depends only on the Python standard library.  It
contains no model calls and performs validation both for direct dataclass
construction and for JSON-like dictionaries loaded through ``from_dict``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, TypeVar


class SchemaValidationError(ValueError):
    """Raised when an agentic workflow payload violates its contract."""


class ErrorType(str, Enum):
    EVIDENCE_NOT_FOUND = "evidence_not_found"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    OVER_INFERENCE = "over_inference"
    OUT_OF_SCOPE = "out_of_scope"
    BAD_NAME = "bad_name"
    SYNONYM_DUPLICATE = "synonym_duplicate"


class ControlAction(str, Enum):
    KEEP = "keep"
    DELETE = "delete"
    NARROW = "narrow"
    RENAME = "rename"
    RELOCATE = "relocate"
    MERGE = "merge"
    REPAIR = "repair"
    LOW_CONFIDENCE = "low_confidence"


class CandidateStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    DELETED = "deleted"
    NEEDS_REPAIR = "needs_repair"
    LOW_CONFIDENCE = "low_confidence"
    MERGED = "merged"


class ReviewStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"


EnumType = TypeVar("EnumType", bound=Enum)


def _as_enum(enum_type: type[EnumType], value: Any, field_name: str) -> EnumType:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise SchemaValidationError(f"{field_name} must be a string enum value")
    try:
        return enum_type(value)
    except ValueError as error:
        allowed = ", ".join(member.value for member in enum_type)
        raise SchemaValidationError(
            f"invalid {field_name}: {value!r}; allowed values: {allowed}"
        ) from error


def _require_non_empty_string(field_name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaValidationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _validate_fields(
    data: Mapping[str, Any],
    required: set[str],
    optional: set[str],
    object_name: str,
) -> None:
    if not isinstance(data, Mapping):
        raise SchemaValidationError(f"{object_name} must be a mapping")
    missing = sorted(required - set(data))
    if missing:
        raise SchemaValidationError(
            f"{object_name} missing required fields: {', '.join(missing)}"
        )
    unknown = sorted(set(data) - required - optional)
    if unknown:
        raise SchemaValidationError(
            f"{object_name} contains unknown fields: {', '.join(unknown)}"
        )


@dataclass
class Evidence:
    """One exact evidence fragment and its project-level provenance."""

    text: str
    project_id: str
    start: int | None = None
    end: int | None = None

    def __post_init__(self) -> None:
        self.text = _require_non_empty_string("evidence.text", self.text)
        if not isinstance(self.project_id, str):
            raise SchemaValidationError("evidence.project_id must be a string")
        self.project_id = self.project_id.strip()

        if (self.start is None) != (self.end is None):
            raise SchemaValidationError(
                "evidence.start and evidence.end must both be set or both be null"
            )
        if self.start is not None:
            if (
                isinstance(self.start, bool)
                or isinstance(self.end, bool)
                or not isinstance(self.start, int)
                or not isinstance(self.end, int)
            ):
                raise SchemaValidationError(
                    "evidence.start and evidence.end must be integers"
                )
            if self.start < 0 or self.end <= self.start:
                raise SchemaValidationError(
                    "evidence span must satisfy 0 <= start < end"
                )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Evidence":
        _validate_fields(
            data,
            required={"text", "project_id"},
            optional={"start", "end"},
            object_name="Evidence",
        )
        return cls(
            text=data["text"],
            project_id=data["project_id"],
            start=data.get("start"),
            end=data.get("end"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "project_id": self.project_id,
            "start": self.start,
            "end": self.end,
        }


def _copy_evidence_list(value: Any, field_name: str) -> list[Evidence]:
    if not isinstance(value, list):
        raise SchemaValidationError(f"{field_name} must be a list")
    result: list[Evidence] = []
    for index, item in enumerate(value):
        if isinstance(item, Evidence):
            result.append(Evidence.from_dict(item.to_dict()))
        elif isinstance(item, Mapping):
            result.append(Evidence.from_dict(item))
        else:
            raise SchemaValidationError(
                f"{field_name}[{index}] must be an Evidence object or mapping"
            )
    return result


@dataclass
class CandidateAbility:
    candidate_id: str
    resume_id: str
    project_id: str
    fact: str
    behavior: str
    ability: str
    normalized_ability: str
    category: dict[str, str]
    evidence: list[Evidence]
    reason: str
    confidence: float
    source: str
    revision_round: int
    parent_candidate_id: str | None
    status: CandidateStatus
    lineage: list[str] | None = None

    def __post_init__(self) -> None:
        self.candidate_id = _require_non_empty_string(
            "candidate_id", self.candidate_id
        )
        self.resume_id = _require_non_empty_string("resume_id", self.resume_id)
        if not isinstance(self.project_id, str):
            raise SchemaValidationError("project_id must be a string")
        self.project_id = self.project_id.strip()
        self.fact = _require_non_empty_string("fact", self.fact)
        self.behavior = _require_non_empty_string("behavior", self.behavior)
        self.ability = _require_non_empty_string("ability", self.ability)
        self.normalized_ability = _require_non_empty_string(
            "normalized_ability", self.normalized_ability
        )
        self.reason = _require_non_empty_string("reason", self.reason)
        self.source = _require_non_empty_string("source", self.source)

        if not isinstance(self.category, dict):
            raise SchemaValidationError("category must be a dictionary")
        copied_category: dict[str, str] = {}
        for key, value in self.category.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise SchemaValidationError(
                    "category keys and values must be strings"
                )
            copied_category[key] = value
        self.category = copied_category
        self.evidence = _copy_evidence_list(self.evidence, "evidence")

        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
        ):
            raise SchemaValidationError("confidence must be a number")
        self.confidence = float(self.confidence)
        if not math.isfinite(self.confidence):
            raise SchemaValidationError("confidence must be finite")
        if not 0.0 <= self.confidence <= 1.0:
            raise SchemaValidationError("confidence must be between 0 and 1")

        if isinstance(self.revision_round, bool) or not isinstance(
            self.revision_round, int
        ):
            raise SchemaValidationError("revision_round must be an integer")
        if not 0 <= self.revision_round <= 1:
            raise SchemaValidationError("revision_round must be 0 or 1")

        if self.parent_candidate_id is not None:
            self.parent_candidate_id = _require_non_empty_string(
                "parent_candidate_id", self.parent_candidate_id
            )
            if self.parent_candidate_id == self.candidate_id:
                raise SchemaValidationError(
                    "parent_candidate_id cannot equal candidate_id"
                )
        self.status = _as_enum(CandidateStatus, self.status, "status")

        if self.lineage is None:
            normalized_lineage = [self.candidate_id]
        else:
            if not isinstance(self.lineage, list):
                raise SchemaValidationError("lineage must be a list")
            normalized_lineage = []
            for index, lineage_id in enumerate(self.lineage):
                lineage_id = _require_non_empty_string(
                    f"lineage[{index}]", lineage_id
                )
                if lineage_id in normalized_lineage:
                    raise SchemaValidationError(
                        f"lineage contains duplicate id: {lineage_id}"
                    )
                normalized_lineage.append(lineage_id)
            if normalized_lineage.count(self.candidate_id) != 1:
                raise SchemaValidationError(
                    "candidate_id must appear exactly once in lineage"
                )
        if (
            self.parent_candidate_id
            and self.parent_candidate_id not in normalized_lineage
        ):
            raise SchemaValidationError(
                "parent_candidate_id must be present in lineage"
            )
        self.lineage = normalized_lineage

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CandidateAbility":
        required = {
            "candidate_id",
            "resume_id",
            "project_id",
            "fact",
            "behavior",
            "ability",
            "normalized_ability",
            "category",
            "evidence",
            "reason",
            "confidence",
            "source",
            "revision_round",
            "parent_candidate_id",
            "status",
        }
        _validate_fields(
            data,
            required=required,
            optional={"lineage"},
            object_name="CandidateAbility",
        )
        return cls(
            candidate_id=data["candidate_id"],
            resume_id=data["resume_id"],
            project_id=data["project_id"],
            fact=data["fact"],
            behavior=data["behavior"],
            ability=data["ability"],
            normalized_ability=data["normalized_ability"],
            category=data["category"],
            evidence=data["evidence"],
            reason=data["reason"],
            confidence=data["confidence"],
            source=data["source"],
            revision_round=data["revision_round"],
            parent_candidate_id=data["parent_candidate_id"],
            status=data["status"],
            lineage=data.get("lineage"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "resume_id": self.resume_id,
            "project_id": self.project_id,
            "fact": self.fact,
            "behavior": self.behavior,
            "ability": self.ability,
            "normalized_ability": self.normalized_ability,
            "category": dict(self.category),
            "evidence": [item.to_dict() for item in self.evidence],
            "reason": self.reason,
            "confidence": self.confidence,
            "source": self.source,
            "revision_round": self.revision_round,
            "parent_candidate_id": self.parent_candidate_id,
            "status": self.status.value,
            "lineage": list(self.lineage),
        }

    def copy_with(self, **changes: Any) -> "CandidateAbility":
        data = self.to_dict()
        data.update(changes)
        return CandidateAbility.from_dict(data)


@dataclass
class ReviewResult:
    candidate_id: str
    status: ReviewStatus
    error_types: list[ErrorType]
    action: ControlAction
    reason: str
    target_ability: str | None
    target_evidence: list[Evidence]
    merge_target_id: str | None

    def __post_init__(self) -> None:
        self.candidate_id = _require_non_empty_string(
            "candidate_id", self.candidate_id
        )
        self.status = _as_enum(ReviewStatus, self.status, "status")
        if not isinstance(self.error_types, list):
            raise SchemaValidationError("error_types must be a list")
        normalized_errors: list[ErrorType] = []
        for index, error_type in enumerate(self.error_types):
            normalized = _as_enum(
                ErrorType, error_type, f"error_types[{index}]"
            )
            if normalized not in normalized_errors:
                normalized_errors.append(normalized)
        self.error_types = normalized_errors
        self.action = _as_enum(ControlAction, self.action, "action")
        self.reason = _require_non_empty_string("reason", self.reason)

        if self.target_ability is not None:
            self.target_ability = _require_non_empty_string(
                "target_ability", self.target_ability
            )
        self.target_evidence = _copy_evidence_list(
            self.target_evidence, "target_evidence"
        )
        if self.merge_target_id is not None:
            self.merge_target_id = _require_non_empty_string(
                "merge_target_id", self.merge_target_id
            )

        if self.status is ReviewStatus.PASSED:
            if self.error_types:
                raise SchemaValidationError(
                    "a passed review cannot contain error_types"
                )
            if self.action is not ControlAction.KEEP:
                raise SchemaValidationError(
                    "a passed review must suggest the keep action"
                )
        elif not self.error_types:
            raise SchemaValidationError(
                "a failed review must contain at least one error_type"
            )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReviewResult":
        required = {
            "candidate_id",
            "status",
            "error_types",
            "action",
            "reason",
            "target_ability",
            "target_evidence",
            "merge_target_id",
        }
        _validate_fields(
            data,
            required=required,
            optional=set(),
            object_name="ReviewResult",
        )
        return cls(
            candidate_id=data["candidate_id"],
            status=data["status"],
            error_types=data["error_types"],
            action=data["action"],
            reason=data["reason"],
            target_ability=data["target_ability"],
            target_evidence=data["target_evidence"],
            merge_target_id=data["merge_target_id"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "status": self.status.value,
            "error_types": [item.value for item in self.error_types],
            "action": self.action.value,
            "reason": self.reason,
            "target_ability": self.target_ability,
            "target_evidence": [item.to_dict() for item in self.target_evidence],
            "merge_target_id": self.merge_target_id,
        }
