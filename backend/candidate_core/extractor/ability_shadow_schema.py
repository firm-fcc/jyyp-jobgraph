"""Strict shadow-only Ability Reviewer assessment contract.

This module intentionally does not import or produce the production
``ReviewResult`` type.  The structures are immutable, versioned and suitable
for deterministic validation of model-shaped JSON in offline experiments.
"""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence, TypeVar

from extractor.review_assessment_schema import (
    ComponentSupport,
    CompoundAssessmentLabel,
)


SHADOW_ASSESSMENT_SCHEMA_VERSION = "ability_shadow_assessment_v1"
SHADOW_RESPONSE_SCHEMA_VERSION = "ability_shadow_response_v1"


class ShadowSchemaError(ValueError):
    """Raised when shadow assessment data violates the versioned contract."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "schema_validation_error",
        invalid_field_path: str | None = None,
        missing_field_names: Sequence[str] = (),
        unexpected_field_names: Sequence[str] = (),
        invalid_enum_field: str | None = None,
        constraint_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.invalid_field_path = invalid_field_path
        self.missing_field_names = tuple(missing_field_names)
        self.unexpected_field_names = tuple(unexpected_field_names)
        self.invalid_enum_field = invalid_enum_field
        self.constraint_name = constraint_name

    def rebased(self, old_prefix: str, new_prefix: str) -> "ShadowSchemaError":
        """Return the same safe diagnostic with a collection-relative path."""

        path = self.invalid_field_path
        if path == old_prefix:
            path = new_prefix or None
        elif path is not None and path.startswith(old_prefix + "."):
            suffix = path[len(old_prefix) + 1:]
            path = f"{new_prefix}.{suffix}" if new_prefix else suffix
        enum_path = self.invalid_enum_field
        if enum_path == old_prefix:
            enum_path = new_prefix or None
        elif enum_path is not None and enum_path.startswith(old_prefix + "."):
            suffix = enum_path[len(old_prefix) + 1:]
            enum_path = f"{new_prefix}.{suffix}" if new_prefix else suffix
        return ShadowSchemaError(
            str(self),
            error_code=self.error_code,
            invalid_field_path=path,
            missing_field_names=self.missing_field_names,
            unexpected_field_names=self.unexpected_field_names,
            invalid_enum_field=enum_path,
            constraint_name=self.constraint_name,
        )


class AbilityValidity(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    PARTIALLY_SUPPORTED = "partially_supported"
    AMBIGUOUS = "ambiguous"


class RepresentationLabel(str, Enum):
    ACCEPTABLE = "acceptable"
    ABILITY_NAME_TOO_BROAD = "ability_name_too_broad"
    ABILITY_NAME_BAD = "ability_name_bad"
    COMPOUND_BUT_SUPPORTED = "compound_but_supported"
    COMPOUND_AND_UNSUPPORTED = "compound_and_unsupported"
    BEHAVIOR_WORDING_TOO_STRONG = "behavior_wording_too_strong"
    DUPLICATE = "duplicate"
    AMBIGUOUS = "ambiguous"


class SemanticComponentSupport(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    PARTIALLY_SUPPORTED = "partially_supported"
    AMBIGUOUS = "ambiguous"


EnumT = TypeVar("EnumT", bound=Enum)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _strict_fields(
    value: Any,
    required: set[str],
    prefix: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ShadowSchemaError(
            f"{prefix} must be an object",
            error_code="invalid_type",
            invalid_field_path=prefix,
        )
    keys = set(value)
    missing = sorted(required - keys)
    unknown = sorted(keys - required)
    if missing:
        raise ShadowSchemaError(
            f"{prefix} is missing fields",
            error_code="missing_fields",
            invalid_field_path=prefix,
            missing_field_names=missing,
        )
    if unknown:
        raise ShadowSchemaError(
            f"{prefix} has unknown fields",
            error_code="unexpected_fields",
            invalid_field_path=prefix,
            unexpected_field_names=unknown,
        )
    return value


def _non_empty(value: Any, prefix: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShadowSchemaError(
            f"{prefix} must be a non-empty string",
            error_code="invalid_string",
            invalid_field_path=prefix,
        )
    return value.strip()


def _optional_non_empty(value: Any, prefix: str) -> str | None:
    return None if value is None else _non_empty(value, prefix)


def _enum(enum_type: type[EnumT], value: Any, prefix: str) -> EnumT:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise ShadowSchemaError(
            f"{prefix} must be a string enum",
            error_code="invalid_enum",
            invalid_field_path=prefix,
            invalid_enum_field=prefix,
        )
    try:
        return enum_type(value)
    except ValueError as error:
        raise ShadowSchemaError(
            f"{prefix} has invalid value",
            error_code="invalid_enum",
            invalid_field_path=prefix,
            invalid_enum_field=prefix,
        ) from error


def _confidence(value: Any, prefix: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ShadowSchemaError(
            f"{prefix} must be a number",
            error_code="invalid_confidence",
            invalid_field_path=prefix,
            constraint_name="finite_confidence_between_zero_and_one",
        )
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ShadowSchemaError(
            f"{prefix} must be finite and between 0 and 1",
            error_code="invalid_confidence",
            invalid_field_path=prefix,
            constraint_name="finite_confidence_between_zero_and_one",
        )
    return result


def _string_tuple(
    value: Any,
    prefix: str,
    *,
    non_empty: bool = False,
    unique: bool = True,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ShadowSchemaError(
            f"{prefix} must be a list",
            error_code="invalid_type",
            invalid_field_path=prefix,
        )
    result = tuple(
        _non_empty(item, f"{prefix}[{index}]")
        for index, item in enumerate(value)
    )
    if non_empty and not result:
        raise ShadowSchemaError(
            f"{prefix} must not be empty",
            error_code="empty_collection",
            invalid_field_path=prefix,
        )
    if unique and len(result) != len(set(result)):
        raise ShadowSchemaError(
            f"{prefix} must not contain duplicates",
            error_code="duplicate_items",
            invalid_field_path=prefix,
        )
    return result


def _enum_tuple(
    enum_type: type[EnumT],
    value: Any,
    prefix: str,
    *,
    non_empty: bool = False,
) -> tuple[EnumT, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ShadowSchemaError(
            f"{prefix} must be a list",
            error_code="invalid_type",
            invalid_field_path=prefix,
        )
    result = tuple(
        _enum(enum_type, item, f"{prefix}[{index}]")
        for index, item in enumerate(value)
    )
    if non_empty and not result:
        raise ShadowSchemaError(
            f"{prefix} must not be empty",
            error_code="empty_collection",
            invalid_field_path=prefix,
        )
    if len(result) != len(set(result)):
        raise ShadowSchemaError(
            f"{prefix} must not contain duplicates",
            error_code="duplicate_items",
            invalid_field_path=prefix,
        )
    return result


def _json_object(value: Any, prefix: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ShadowSchemaError(
            f"{prefix} must be an object",
            error_code="invalid_type",
            invalid_field_path=prefix,
        )
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
        raise ShadowSchemaError(
            f"{prefix} must contain JSON-safe values",
            error_code="invalid_json_value",
            invalid_field_path=prefix,
        ) from error
    return copy.deepcopy(decoded)


@dataclass(frozen=True)
class ShadowComponentAssessment:
    taxonomy_id: str
    canonical_name: str
    support: SemanticComponentSupport
    evidence_audit_support: ComponentSupport
    missing_requirements: tuple[str, ...]
    satisfied_requirements: tuple[str, ...]
    semantic_reason: str
    confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "taxonomy_id", _non_empty(
            self.taxonomy_id, "ShadowComponentAssessment.taxonomy_id"))
        object.__setattr__(self, "canonical_name", _non_empty(
            self.canonical_name, "ShadowComponentAssessment.canonical_name"))
        object.__setattr__(self, "support", _enum(
            SemanticComponentSupport,
            self.support,
            "ShadowComponentAssessment.support",
        ))
        object.__setattr__(self, "evidence_audit_support", _enum(
            ComponentSupport,
            self.evidence_audit_support,
            "ShadowComponentAssessment.evidence_audit_support",
        ))
        object.__setattr__(self, "missing_requirements", _string_tuple(
            self.missing_requirements,
            "ShadowComponentAssessment.missing_requirements",
        ))
        object.__setattr__(self, "satisfied_requirements", _string_tuple(
            self.satisfied_requirements,
            "ShadowComponentAssessment.satisfied_requirements",
        ))
        object.__setattr__(self, "semantic_reason", _non_empty(
            self.semantic_reason, "ShadowComponentAssessment.semantic_reason"))
        object.__setattr__(self, "confidence", _confidence(
            self.confidence, "ShadowComponentAssessment.confidence"))
        if set(self.missing_requirements) & set(self.satisfied_requirements):
            raise ShadowSchemaError(
                "a requirement cannot be both missing and satisfied",
                error_code="constraint_violation",
                invalid_field_path="ShadowComponentAssessment.missing_requirements",
                constraint_name="component_requirement_partition",
            )
        if self.support is SemanticComponentSupport.SUPPORTED:
            if self.missing_requirements:
                raise ShadowSchemaError(
                    "supported component cannot have missing requirements",
                    error_code="constraint_violation",
                    invalid_field_path="ShadowComponentAssessment.missing_requirements",
                    constraint_name="component_support_consistency",
                )
        elif self.support is SemanticComponentSupport.UNSUPPORTED:
            if not self.missing_requirements:
                raise ShadowSchemaError(
                    "unsupported component must identify a missing requirement",
                    error_code="constraint_violation",
                    invalid_field_path="ShadowComponentAssessment.missing_requirements",
                    constraint_name="component_support_consistency",
                )
        elif self.support is SemanticComponentSupport.PARTIALLY_SUPPORTED:
            if not self.missing_requirements or not self.satisfied_requirements:
                raise ShadowSchemaError(
                    "partially supported component needs satisfied and missing requirements",
                    error_code="constraint_violation",
                    invalid_field_path="ShadowComponentAssessment.missing_requirements",
                    constraint_name="component_support_consistency",
                )

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        field_path: str = "ShadowComponentAssessment",
    ) -> "ShadowComponentAssessment":
        fields = {
            "taxonomy_id", "canonical_name", "support",
            "evidence_audit_support", "missing_requirements",
            "satisfied_requirements", "semantic_reason", "confidence",
        }
        payload = _strict_fields(value, fields, field_path)
        try:
            return cls(**{name: payload[name] for name in fields})
        except ShadowSchemaError as error:
            raise error.rebased("ShadowComponentAssessment", field_path) from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "taxonomy_id": self.taxonomy_id,
            "canonical_name": self.canonical_name,
            "support": self.support.value,
            "evidence_audit_support": self.evidence_audit_support.value,
            "missing_requirements": list(self.missing_requirements),
            "satisfied_requirements": list(self.satisfied_requirements),
            "semantic_reason": self.semantic_reason,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class ShadowAbilityAssessment:
    schema_version: str
    resume_id: str
    candidate_id: str
    taxonomy_version: str
    evidence_audit_sha256: str
    ability_validity: AbilityValidity
    preferred_taxonomy_id: str | None
    allowed_taxonomy_ids: tuple[str, ...]
    representation_labels: tuple[RepresentationLabel, ...]
    component_assessments: tuple[ShadowComponentAssessment, ...]
    compound_label: CompoundAssessmentLabel
    split_recommended: bool
    suggested_atomic_taxonomy_ids: tuple[str, ...]
    warnings: tuple[str, ...]
    reason: str
    confidence: float
    diagnostics: dict[str, Any]

    def __post_init__(self) -> None:
        if self.schema_version != SHADOW_ASSESSMENT_SCHEMA_VERSION:
            raise ShadowSchemaError(
                "schema_version has an unsupported value",
                error_code="invalid_enum",
                invalid_field_path="ShadowAbilityAssessment.schema_version",
                invalid_enum_field="ShadowAbilityAssessment.schema_version",
            )
        object.__setattr__(self, "resume_id", _non_empty(
            self.resume_id, "ShadowAbilityAssessment.resume_id"))
        object.__setattr__(self, "candidate_id", _non_empty(
            self.candidate_id, "ShadowAbilityAssessment.candidate_id"))
        object.__setattr__(self, "taxonomy_version", _non_empty(
            self.taxonomy_version, "ShadowAbilityAssessment.taxonomy_version"))
        audit_hash = _non_empty(
            self.evidence_audit_sha256,
            "ShadowAbilityAssessment.evidence_audit_sha256",
        )
        if not _SHA256_PATTERN.fullmatch(audit_hash):
            raise ShadowSchemaError(
                "ShadowAbilityAssessment.evidence_audit_sha256 must be SHA-256",
                error_code="invalid_format",
                invalid_field_path="ShadowAbilityAssessment.evidence_audit_sha256",
                constraint_name="sha256_format",
            )
        object.__setattr__(self, "evidence_audit_sha256", audit_hash)
        object.__setattr__(self, "ability_validity", _enum(
            AbilityValidity,
            self.ability_validity,
            "ShadowAbilityAssessment.ability_validity",
        ))
        object.__setattr__(self, "preferred_taxonomy_id", _optional_non_empty(
            self.preferred_taxonomy_id,
            "ShadowAbilityAssessment.preferred_taxonomy_id",
        ))
        allowed = _string_tuple(
            self.allowed_taxonomy_ids,
            "ShadowAbilityAssessment.allowed_taxonomy_ids",
            non_empty=True,
        )
        object.__setattr__(self, "allowed_taxonomy_ids", allowed)
        if (
            self.preferred_taxonomy_id is not None
            and self.preferred_taxonomy_id not in allowed
        ):
            raise ShadowSchemaError(
                "preferred_taxonomy_id must belong to allowed_taxonomy_ids",
                error_code="constraint_violation",
                invalid_field_path="ShadowAbilityAssessment.preferred_taxonomy_id",
                constraint_name="preferred_taxonomy_scope",
            )
        labels = _enum_tuple(
            RepresentationLabel,
            self.representation_labels,
            "ShadowAbilityAssessment.representation_labels",
            non_empty=True,
        )
        object.__setattr__(self, "representation_labels", labels)
        raw_components = self.component_assessments
        if isinstance(raw_components, (str, bytes)) or not isinstance(
            raw_components, Sequence
        ):
            raise ShadowSchemaError(
                "ShadowAbilityAssessment.component_assessments must be a list",
                error_code="invalid_type",
                invalid_field_path="ShadowAbilityAssessment.component_assessments",
            )
        components_list = []
        for index, item in enumerate(raw_components):
            if isinstance(item, ShadowComponentAssessment):
                components_list.append(item)
            else:
                components_list.append(ShadowComponentAssessment.from_dict(
                    item,
                    field_path=f"ShadowAbilityAssessment.component_assessments[{index}]",
                ))
        components = tuple(components_list)
        component_ids = [item.taxonomy_id for item in components]
        if len(component_ids) != len(set(component_ids)):
            raise ShadowSchemaError(
                "component taxonomy_id must be unique",
                error_code="duplicate_items",
                invalid_field_path="ShadowAbilityAssessment.component_assessments",
                constraint_name="unique_component_taxonomy_id",
            )
        if not set(component_ids).issubset(allowed):
            raise ShadowSchemaError(
                "component taxonomy_id must belong to allowed_taxonomy_ids",
                error_code="constraint_violation",
                invalid_field_path="ShadowAbilityAssessment.component_assessments",
                constraint_name="component_taxonomy_scope",
            )
        object.__setattr__(self, "component_assessments", components)
        object.__setattr__(self, "compound_label", _enum(
            CompoundAssessmentLabel,
            self.compound_label,
            "ShadowAbilityAssessment.compound_label",
        ))
        if not isinstance(self.split_recommended, bool):
            raise ShadowSchemaError(
                "ShadowAbilityAssessment.split_recommended must be bool",
                error_code="invalid_type",
                invalid_field_path="ShadowAbilityAssessment.split_recommended",
            )
        suggested = _string_tuple(
            self.suggested_atomic_taxonomy_ids,
            "ShadowAbilityAssessment.suggested_atomic_taxonomy_ids",
        )
        object.__setattr__(self, "suggested_atomic_taxonomy_ids", suggested)
        if not set(suggested).issubset(allowed):
            raise ShadowSchemaError(
                "suggested atomic taxonomy IDs must belong to the allowed scope",
                error_code="constraint_violation",
                invalid_field_path="ShadowAbilityAssessment.suggested_atomic_taxonomy_ids",
                constraint_name="suggested_atomic_taxonomy_scope",
            )
        if self.split_recommended:
            if len(suggested) < 2:
                raise ShadowSchemaError(
                    "split recommendation requires at least two atomic taxonomy IDs",
                    error_code="constraint_violation",
                    invalid_field_path="ShadowAbilityAssessment.suggested_atomic_taxonomy_ids",
                    constraint_name="split_component_consistency",
                )
        elif suggested:
            raise ShadowSchemaError(
                "suggested atomic taxonomy IDs require split_recommended=true",
                error_code="constraint_violation",
                invalid_field_path="ShadowAbilityAssessment.suggested_atomic_taxonomy_ids",
                constraint_name="split_component_consistency",
            )
        object.__setattr__(self, "warnings", _string_tuple(
            self.warnings, "ShadowAbilityAssessment.warnings"))
        object.__setattr__(self, "reason", _non_empty(
            self.reason, "ShadowAbilityAssessment.reason"))
        object.__setattr__(self, "confidence", _confidence(
            self.confidence, "ShadowAbilityAssessment.confidence"))
        object.__setattr__(self, "diagnostics", _json_object(
            copy.deepcopy(self.diagnostics),
            "ShadowAbilityAssessment.diagnostics",
        ))
        self._validate_compound_consistency()

    def _validate_compound_consistency(self) -> None:
        labels = set(self.representation_labels)
        supported_label = RepresentationLabel.COMPOUND_BUT_SUPPORTED
        unsupported_label = RepresentationLabel.COMPOUND_AND_UNSUPPORTED
        if self.compound_label is CompoundAssessmentLabel.NOT_COMPOUND:
            if supported_label in labels or unsupported_label in labels:
                raise ShadowSchemaError(
                    "not_compound cannot use a compound representation label",
                    error_code="constraint_violation",
                    invalid_field_path="ShadowAbilityAssessment.representation_labels",
                    constraint_name="compound_representation_consistency",
                )
            if self.split_recommended:
                raise ShadowSchemaError(
                    "not_compound cannot recommend split",
                    error_code="constraint_violation",
                    invalid_field_path="ShadowAbilityAssessment.split_recommended",
                    constraint_name="split_component_consistency",
                )
        elif self.compound_label is CompoundAssessmentLabel.COMPOUND_SUPPORTED:
            if supported_label not in labels or unsupported_label in labels:
                raise ShadowSchemaError(
                    "compound_supported requires compound_but_supported",
                    error_code="constraint_violation",
                    invalid_field_path="ShadowAbilityAssessment.representation_labels",
                    constraint_name="compound_representation_consistency",
                )
            if self.split_recommended:
                raise ShadowSchemaError(
                    "compound_supported cannot recommend split",
                    error_code="constraint_violation",
                    invalid_field_path="ShadowAbilityAssessment.split_recommended",
                    constraint_name="split_component_consistency",
                )
        elif self.compound_label is CompoundAssessmentLabel.COMPOUND_UNSUPPORTED:
            if unsupported_label not in labels:
                raise ShadowSchemaError(
                    "compound_unsupported requires compound_and_unsupported",
                    error_code="constraint_violation",
                    invalid_field_path="ShadowAbilityAssessment.representation_labels",
                    constraint_name="compound_representation_consistency",
                )
        elif self.compound_label is CompoundAssessmentLabel.SPLIT_RECOMMENDED:
            if not self.split_recommended:
                raise ShadowSchemaError(
                    "split_recommended compound label requires split recommendation",
                    error_code="constraint_violation",
                    invalid_field_path="ShadowAbilityAssessment.split_recommended",
                    constraint_name="split_component_consistency",
                )
            if not ({supported_label, unsupported_label} & labels):
                raise ShadowSchemaError(
                    "split recommendation needs a compound representation label",
                    error_code="constraint_violation",
                    invalid_field_path="ShadowAbilityAssessment.representation_labels",
                    constraint_name="compound_representation_consistency",
                )
        elif self.compound_label is CompoundAssessmentLabel.AMBIGUOUS:
            if RepresentationLabel.AMBIGUOUS not in labels:
                raise ShadowSchemaError(
                    "ambiguous compound requires ambiguous representation",
                    error_code="constraint_violation",
                    invalid_field_path="ShadowAbilityAssessment.representation_labels",
                    constraint_name="compound_representation_consistency",
                )

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        taxonomy_scope: Sequence[str] | None = None,
    ) -> "ShadowAbilityAssessment":
        fields = {
            "schema_version", "resume_id", "candidate_id",
            "taxonomy_version", "evidence_audit_sha256", "ability_validity",
            "preferred_taxonomy_id", "allowed_taxonomy_ids",
            "representation_labels", "component_assessments",
            "compound_label", "split_recommended",
            "suggested_atomic_taxonomy_ids", "warnings", "reason",
            "confidence", "diagnostics",
        }
        payload = _strict_fields(value, fields, "ShadowAbilityAssessment")
        try:
            result = cls(**{name: payload[name] for name in fields})
        except ShadowSchemaError as error:
            raise error.rebased("ShadowAbilityAssessment", "") from error
        if taxonomy_scope is not None:
            scope = set(_string_tuple(
                taxonomy_scope,
                "ShadowAbilityAssessment.taxonomy_scope",
                non_empty=True,
            ))
            referenced = set(result.allowed_taxonomy_ids)
            if not referenced.issubset(scope):
                unknown = sorted(referenced - scope)
                raise ShadowSchemaError(
                    "assessment references taxonomy IDs outside candidate scope",
                    error_code="constraint_violation",
                    invalid_field_path="allowed_taxonomy_ids",
                    constraint_name="assessment_taxonomy_scope",
                )
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "resume_id": self.resume_id,
            "candidate_id": self.candidate_id,
            "taxonomy_version": self.taxonomy_version,
            "evidence_audit_sha256": self.evidence_audit_sha256,
            "ability_validity": self.ability_validity.value,
            "preferred_taxonomy_id": self.preferred_taxonomy_id,
            "allowed_taxonomy_ids": list(self.allowed_taxonomy_ids),
            "representation_labels": [
                item.value for item in self.representation_labels
            ],
            "component_assessments": [
                item.to_dict() for item in self.component_assessments
            ],
            "compound_label": self.compound_label.value,
            "split_recommended": self.split_recommended,
            "suggested_atomic_taxonomy_ids": list(
                self.suggested_atomic_taxonomy_ids),
            "warnings": list(self.warnings),
            "reason": self.reason,
            "confidence": self.confidence,
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
