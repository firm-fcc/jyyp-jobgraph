"""Strict offline bundle for deterministic shadow review mappings.

The bundle is deliberately separate from production orchestration.  It keeps
the existing eight-field :class:`ReviewResult` unchanged, records shadow-only
split information beside that projection, and never executes a controller.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence, TypeVar

from extractor.agentic_schema import (
    CandidateAbility,
    ControlAction,
    ReviewResult,
    ReviewStatus,
)
from extractor.ability_shadow_schema import ShadowAbilityAssessment
from extractor.review_assessment_schema import EvidenceAuditResult


SHADOW_REVIEW_BUNDLE_SCHEMA_VERSION = "shadow_review_bundle_v1"
MAPPER_VERSION = "deterministic_shadow_mapper_v1"


class ShadowBundleError(ValueError):
    """Raised when a shadow review bundle violates its strict contract."""


class DecisionSource(str, Enum):
    DETERMINISTIC_ONLY = "deterministic_only"
    DETERMINISTIC_PLUS_SHADOW = "deterministic_plus_shadow"
    MAPPING_BLOCKED = "mapping_blocked"


EnumT = TypeVar("EnumT", bound=Enum)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_DIAGNOSTIC_KEYS = {
    "resume",
    "resume_text",
    "full_resume",
    "prompt",
    "prompt_content",
    "system_prompt",
    "user_prompt",
    "taxonomy",
    "full_taxonomy",
    "taxonomy_payload",
    "completion",
    "model_response",
    "raw_model_response",
    "raw_response",
    "full_response",
    "completion_text",
}


def _strict_fields(value: Any, fields: set[str], prefix: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ShadowBundleError(f"{prefix} must be an object")
    keys = set(value)
    missing = sorted(fields - keys)
    unknown = sorted(keys - fields)
    if missing:
        raise ShadowBundleError(f"{prefix} missing fields: {', '.join(missing)}")
    if unknown:
        raise ShadowBundleError(f"{prefix} unknown fields: {', '.join(unknown)}")
    return value


def _non_empty(value: Any, prefix: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShadowBundleError(f"{prefix} must be a non-empty string")
    return value.strip()


def _optional_non_empty(value: Any, prefix: str) -> str | None:
    return None if value is None else _non_empty(value, prefix)


def _enum(enum_type: type[EnumT], value: Any, prefix: str) -> EnumT:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise ShadowBundleError(f"{prefix} must be a string enum")
    try:
        return enum_type(value)
    except ValueError as error:
        raise ShadowBundleError(f"{prefix} has invalid value: {value}") from error


def _string_tuple(
    value: Any,
    prefix: str,
    *,
    non_empty: bool = False,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ShadowBundleError(f"{prefix} must be a list")
    result = tuple(_non_empty(item, f"{prefix}[{index}]")
                   for index, item in enumerate(value))
    if non_empty and not result:
        raise ShadowBundleError(f"{prefix} must not be empty")
    if len(result) != len(set(result)):
        raise ShadowBundleError(f"{prefix} must not contain duplicates")
    return result


def _sha256(value: Any, prefix: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    normalized = _non_empty(value, prefix)
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise ShadowBundleError(f"{prefix} must be lowercase SHA-256")
    return normalized


def _validate_diagnostics_node(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ShadowBundleError(f"{path} keys must be strings")
            if key.lower() in _FORBIDDEN_DIAGNOSTIC_KEYS:
                raise ShadowBundleError(
                    f"{path} cannot contain full-input field: {key}")
            _validate_diagnostics_node(nested, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, nested in enumerate(value):
            _validate_diagnostics_node(nested, f"{path}[{index}]")
    elif isinstance(value, str) and len(value) > 2048:
        raise ShadowBundleError(f"{path} string is too large for diagnostics")


def _json_object(value: Any, prefix: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ShadowBundleError(f"{prefix} must be an object")
    _validate_diagnostics_node(value, prefix)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise ShadowBundleError(f"{prefix} must contain JSON-safe values") from error
    return copy.deepcopy(decoded)


def canonical_sha256(value: Mapping[str, Any]) -> str:
    """Hash one JSON-like object using the project's canonical JSON form."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def candidate_sha256(candidate: CandidateAbility) -> str:
    if not isinstance(candidate, CandidateAbility):
        raise TypeError("candidate must be CandidateAbility")
    return canonical_sha256(candidate.to_dict())


def evidence_audit_sha256(audit: EvidenceAuditResult) -> str:
    if not isinstance(audit, EvidenceAuditResult):
        raise TypeError("audit must be EvidenceAuditResult")
    return hashlib.sha256(audit.serialize().encode("utf-8")).hexdigest()


def ability_assessment_sha256(assessment: ShadowAbilityAssessment) -> str:
    if not isinstance(assessment, ShadowAbilityAssessment):
        raise TypeError("assessment must be ShadowAbilityAssessment")
    return hashlib.sha256(assessment.serialize().encode("utf-8")).hexdigest()


def _validate_review_projection(review: ReviewResult) -> None:
    """Apply stricter action/target invariants than the legacy schema itself."""

    action = review.action
    if review.status is ReviewStatus.PASSED:
        if action is not ControlAction.KEEP:
            raise ShadowBundleError("passed ReviewResult must use keep")
    elif not review.error_types:
        raise ShadowBundleError("failed ReviewResult must contain error_types")

    if action is ControlAction.RELOCATE:
        if not review.target_evidence:
            raise ShadowBundleError("relocate requires target_evidence")
        if review.target_ability is not None or review.merge_target_id is not None:
            raise ShadowBundleError("relocate cannot carry another target")
    elif review.target_evidence:
        raise ShadowBundleError("non-relocate action cannot carry target_evidence")

    if action in {ControlAction.NARROW, ControlAction.RENAME}:
        if review.target_ability is None:
            raise ShadowBundleError(f"{action.value} requires target_ability")
        if review.merge_target_id is not None:
            raise ShadowBundleError(f"{action.value} cannot carry merge_target_id")
    elif review.target_ability is not None:
        raise ShadowBundleError(
            f"{action.value} cannot carry target_ability")

    if action is ControlAction.MERGE:
        if review.merge_target_id is None:
            raise ShadowBundleError("merge requires merge_target_id")
    elif review.merge_target_id is not None:
        raise ShadowBundleError(
            f"{action.value} cannot carry merge_target_id")

    if action is ControlAction.KEEP and review.status is not ReviewStatus.PASSED:
        raise ShadowBundleError("keep must use passed status")
    if action is not ControlAction.KEEP and review.status is not ReviewStatus.FAILED:
        raise ShadowBundleError(f"{action.value} must use failed status")


@dataclass(frozen=True)
class SplitRecommendation:
    split_recommended: bool
    suggested_atomic_taxonomy_ids: tuple[str, ...]
    suggested_atomic_abilities: tuple[str, ...]
    supported_component_ids: tuple[str, ...]
    unsupported_component_ids: tuple[str, ...]
    production_action_available: bool
    notes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.split_recommended, bool):
            raise ShadowBundleError("split_recommended must be bool")
        for name in (
            "suggested_atomic_taxonomy_ids",
            "suggested_atomic_abilities",
            "supported_component_ids",
            "unsupported_component_ids",
            "notes",
        ):
            object.__setattr__(self, name, _string_tuple(
                getattr(self, name), f"SplitRecommendation.{name}"))
        if self.production_action_available is not False:
            raise ShadowBundleError("production_action_available must be false")
        if len(self.suggested_atomic_taxonomy_ids) != len(
            self.suggested_atomic_abilities
        ):
            raise ShadowBundleError(
                "suggested taxonomy IDs and ability names must have equal length")
        if set(self.supported_component_ids) & set(self.unsupported_component_ids):
            raise ShadowBundleError(
                "a component cannot be both supported and unsupported")
        if self.split_recommended:
            if len(self.suggested_atomic_taxonomy_ids) < 2:
                raise ShadowBundleError(
                    "split recommendation requires at least two atomic abilities")
            if not set(self.suggested_atomic_taxonomy_ids).issubset(
                self.supported_component_ids
            ):
                raise ShadowBundleError(
                    "every suggested atomic ability must be supported")
        elif self.suggested_atomic_taxonomy_ids or self.suggested_atomic_abilities:
            raise ShadowBundleError(
                "atomic suggestions require split_recommended=true")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SplitRecommendation":
        fields = {
            "split_recommended",
            "suggested_atomic_taxonomy_ids",
            "suggested_atomic_abilities",
            "supported_component_ids",
            "unsupported_component_ids",
            "production_action_available",
            "notes",
        }
        payload = _strict_fields(value, fields, "SplitRecommendation")
        return cls(**{name: payload[name] for name in fields})

    def to_dict(self) -> dict[str, Any]:
        return {
            "split_recommended": self.split_recommended,
            "suggested_atomic_taxonomy_ids": list(
                self.suggested_atomic_taxonomy_ids),
            "suggested_atomic_abilities": list(self.suggested_atomic_abilities),
            "supported_component_ids": list(self.supported_component_ids),
            "unsupported_component_ids": list(self.unsupported_component_ids),
            "production_action_available": False,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ShadowReviewBundle:
    schema_version: str
    resume_id: str
    candidate_id: str
    candidate_sha256: str
    evidence_audit_sha256: str
    ability_assessment_sha256: str | None
    decision_source: DecisionSource
    mapped_review_result: ReviewResult | None
    split_recommendation: SplitRecommendation
    mapper_reason: str
    warnings: tuple[str, ...]
    conflicts: tuple[str, ...]
    requires_human_review: bool
    diagnostics: dict[str, Any]

    def __post_init__(self) -> None:
        if self.schema_version != SHADOW_REVIEW_BUNDLE_SCHEMA_VERSION:
            raise ShadowBundleError(
                f"schema_version must be {SHADOW_REVIEW_BUNDLE_SCHEMA_VERSION}")
        object.__setattr__(self, "resume_id", _non_empty(
            self.resume_id, "ShadowReviewBundle.resume_id"))
        object.__setattr__(self, "candidate_id", _non_empty(
            self.candidate_id, "ShadowReviewBundle.candidate_id"))
        object.__setattr__(self, "candidate_sha256", _sha256(
            self.candidate_sha256, "ShadowReviewBundle.candidate_sha256"))
        object.__setattr__(self, "evidence_audit_sha256", _sha256(
            self.evidence_audit_sha256,
            "ShadowReviewBundle.evidence_audit_sha256",
        ))
        object.__setattr__(self, "ability_assessment_sha256", _sha256(
            self.ability_assessment_sha256,
            "ShadowReviewBundle.ability_assessment_sha256",
            optional=True,
        ))
        source = _enum(
            DecisionSource, self.decision_source,
            "ShadowReviewBundle.decision_source")
        object.__setattr__(self, "decision_source", source)
        split = (
            self.split_recommendation
            if isinstance(self.split_recommendation, SplitRecommendation)
            else SplitRecommendation.from_dict(self.split_recommendation)
        )
        object.__setattr__(self, "split_recommendation", split)
        object.__setattr__(self, "mapper_reason", _non_empty(
            self.mapper_reason, "ShadowReviewBundle.mapper_reason"))
        object.__setattr__(self, "warnings", _string_tuple(
            self.warnings, "ShadowReviewBundle.warnings"))
        object.__setattr__(self, "conflicts", _string_tuple(
            self.conflicts, "ShadowReviewBundle.conflicts"))
        if not isinstance(self.requires_human_review, bool):
            raise ShadowBundleError("requires_human_review must be bool")
        object.__setattr__(self, "diagnostics", _json_object(
            copy.deepcopy(self.diagnostics), "ShadowReviewBundle.diagnostics"))

        review = self.mapped_review_result
        if review is not None:
            if not isinstance(review, ReviewResult):
                if not isinstance(review, Mapping):
                    raise ShadowBundleError(
                        "mapped_review_result must be ReviewResult or null")
                review = ReviewResult.from_dict(review)
            else:
                review = ReviewResult.from_dict(review.to_dict())
            if review.candidate_id != self.candidate_id:
                raise ShadowBundleError(
                    "mapped ReviewResult candidate_id must match bundle")
            _validate_review_projection(review)
            object.__setattr__(self, "mapped_review_result", review)

        if source is DecisionSource.MAPPING_BLOCKED:
            if review is not None:
                raise ShadowBundleError(
                    "mapping_blocked cannot contain mapped_review_result")
            if not self.conflicts:
                raise ShadowBundleError("mapping_blocked requires conflicts")
            if not self.requires_human_review:
                raise ShadowBundleError(
                    "mapping_blocked requires human review")
        else:
            if review is None:
                raise ShadowBundleError(
                    "successful mapping requires mapped_review_result")
            if self.conflicts:
                raise ShadowBundleError(
                    "successful mapping cannot contain conflicts")
            if (
                source is DecisionSource.DETERMINISTIC_ONLY
                and self.ability_assessment_sha256 is not None
            ):
                raise ShadowBundleError(
                    "deterministic_only cannot reference an ability assessment")
            if (
                source is DecisionSource.DETERMINISTIC_PLUS_SHADOW
                and self.ability_assessment_sha256 is None
            ):
                raise ShadowBundleError(
                    "deterministic_plus_shadow requires assessment SHA")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ShadowReviewBundle":
        fields = {
            "schema_version",
            "resume_id",
            "candidate_id",
            "candidate_sha256",
            "evidence_audit_sha256",
            "ability_assessment_sha256",
            "decision_source",
            "mapped_review_result",
            "split_recommendation",
            "mapper_reason",
            "warnings",
            "conflicts",
            "requires_human_review",
            "diagnostics",
        }
        payload = _strict_fields(value, fields, "ShadowReviewBundle")
        review = payload["mapped_review_result"]
        if review is not None:
            review = ReviewResult.from_dict(review)
        return cls(**{
            **{name: payload[name] for name in fields
               if name != "mapped_review_result"},
            "mapped_review_result": review,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "resume_id": self.resume_id,
            "candidate_id": self.candidate_id,
            "candidate_sha256": self.candidate_sha256,
            "evidence_audit_sha256": self.evidence_audit_sha256,
            "ability_assessment_sha256": self.ability_assessment_sha256,
            "decision_source": self.decision_source.value,
            "mapped_review_result": (
                None if self.mapped_review_result is None
                else self.mapped_review_result.to_dict()
            ),
            "split_recommendation": self.split_recommendation.to_dict(),
            "mapper_reason": self.mapper_reason,
            "warnings": list(self.warnings),
            "conflicts": list(self.conflicts),
            "requires_human_review": self.requires_human_review,
            "diagnostics": copy.deepcopy(self.diagnostics),
        }

    def serialize(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
