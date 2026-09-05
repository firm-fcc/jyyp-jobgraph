"""Minimal semantic-only response contract for Shadow Reviewer v2.

The model supplies only unresolved semantic choices. Evidence, deterministic
component support, relocation, diagnostics and production actions are absent.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from extractor.ability_shadow_schema import (
    AbilityValidity,
    RepresentationLabel,
    ShadowSchemaError,
)


SEMANTIC_SHADOW_RESPONSE_SCHEMA_VERSION = "ability_shadow_semantic_response_v2"


def _strict(value: Any, fields: set[str], path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ShadowSchemaError(
            f"{path} must be an object",
            error_code="invalid_type",
            invalid_field_path=path,
        )
    missing = sorted(fields - set(value))
    unexpected = sorted(set(value) - fields)
    if missing:
        raise ShadowSchemaError(
            f"{path} is missing required fields",
            error_code="missing_fields",
            invalid_field_path=path,
            missing_field_names=missing,
        )
    if unexpected:
        raise ShadowSchemaError(
            f"{path} has unknown fields",
            error_code="unexpected_fields",
            invalid_field_path=path,
            unexpected_field_names=unexpected,
        )
    return value


def _text(value: Any, path: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ShadowSchemaError(
            f"{path} must be a non-empty string",
            error_code="invalid_string",
            invalid_field_path=path,
        )
    return value.strip()


def _strings(value: Any, path: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ShadowSchemaError(
            f"{path} must be a list",
            error_code="invalid_type",
            invalid_field_path=path,
        )
    result = tuple(_text(item, f"{path}[{index}]") for index, item in enumerate(value))
    if len(result) != len(set(result)):
        raise ShadowSchemaError(
            f"{path} must not contain duplicates",
            error_code="duplicate_items",
            invalid_field_path=path,
        )
    return result


def _enum(enum_type: type, value: Any, path: str):
    if not isinstance(value, str):
        raise ShadowSchemaError(
            f"{path} must be a string enum",
            error_code="invalid_enum",
            invalid_field_path=path,
            invalid_enum_field=path,
        )
    try:
        return enum_type(value)
    except ValueError as error:
        raise ShadowSchemaError(
            f"{path} has an invalid enum value",
            error_code="invalid_enum",
            invalid_field_path=path,
            invalid_enum_field=path,
        ) from error


def _confidence(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ShadowSchemaError(
            f"{path} must be a number",
            error_code="invalid_confidence",
            invalid_field_path=path,
            constraint_name="finite_confidence_between_zero_and_one",
        )
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ShadowSchemaError(
            f"{path} must be finite and between 0 and 1",
            error_code="invalid_confidence",
            invalid_field_path=path,
            constraint_name="finite_confidence_between_zero_and_one",
        )
    return result


@dataclass(frozen=True)
class SemanticShadowAssessment:
    candidate_id: str
    ability_validity: AbilityValidity
    preferred_taxonomy_id: str | None
    representation_labels: tuple[RepresentationLabel, ...]
    split_recommended: bool
    suggested_atomic_taxonomy_ids: tuple[str, ...]
    reason: str
    confidence: float

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        field_path: str = "assessment",
        taxonomy_scope: Sequence[str] | None = None,
    ) -> "SemanticShadowAssessment":
        fields = {
            "candidate_id", "ability_validity", "preferred_taxonomy_id",
            "representation_labels", "split_recommended",
            "suggested_atomic_taxonomy_ids", "reason", "confidence",
        }
        payload = _strict(value, fields, field_path)
        candidate_id = _text(payload["candidate_id"], f"{field_path}.candidate_id")
        validity = _enum(
            AbilityValidity, payload["ability_validity"],
            f"{field_path}.ability_validity")
        preferred = _text(
            payload["preferred_taxonomy_id"],
            f"{field_path}.preferred_taxonomy_id", optional=True)
        raw_labels = payload["representation_labels"]
        if isinstance(raw_labels, (str, bytes)) or not isinstance(raw_labels, Sequence):
            raise ShadowSchemaError(
                f"{field_path}.representation_labels must be a list",
                error_code="invalid_type",
                invalid_field_path=f"{field_path}.representation_labels",
            )
        labels = tuple(_enum(
            RepresentationLabel, item,
            f"{field_path}.representation_labels[{index}]",
        ) for index, item in enumerate(raw_labels))
        if not labels:
            raise ShadowSchemaError(
                f"{field_path}.representation_labels must not be empty",
                error_code="empty_collection",
                invalid_field_path=f"{field_path}.representation_labels",
            )
        if len(labels) != len(set(labels)):
            raise ShadowSchemaError(
                f"{field_path}.representation_labels must not contain duplicates",
                error_code="duplicate_items",
                invalid_field_path=f"{field_path}.representation_labels",
            )
        split = payload["split_recommended"]
        if not isinstance(split, bool):
            raise ShadowSchemaError(
                f"{field_path}.split_recommended must be bool",
                error_code="invalid_type",
                invalid_field_path=f"{field_path}.split_recommended",
            )
        atomic = _strings(
            payload["suggested_atomic_taxonomy_ids"],
            f"{field_path}.suggested_atomic_taxonomy_ids")
        if split and len(atomic) < 2:
            raise ShadowSchemaError(
                "split recommendation requires at least two atomic taxonomy IDs",
                error_code="constraint_violation",
                invalid_field_path=f"{field_path}.suggested_atomic_taxonomy_ids",
                constraint_name="split_component_consistency",
            )
        if not split and atomic:
            raise ShadowSchemaError(
                "atomic taxonomy IDs require split_recommended=true",
                error_code="constraint_violation",
                invalid_field_path=f"{field_path}.suggested_atomic_taxonomy_ids",
                constraint_name="split_component_consistency",
            )
        if taxonomy_scope is not None:
            scope = set(taxonomy_scope)
            if preferred is not None and preferred not in scope:
                raise ShadowSchemaError(
                    "preferred taxonomy ID is outside candidate scope",
                    error_code="constraint_violation",
                    invalid_field_path=f"{field_path}.preferred_taxonomy_id",
                    constraint_name="semantic_taxonomy_scope",
                )
            if not set(atomic).issubset(scope):
                raise ShadowSchemaError(
                    "suggested atomic taxonomy IDs are outside candidate scope",
                    error_code="constraint_violation",
                    invalid_field_path=f"{field_path}.suggested_atomic_taxonomy_ids",
                    constraint_name="semantic_taxonomy_scope",
                )
        return cls(
            candidate_id=candidate_id,
            ability_validity=validity,
            preferred_taxonomy_id=preferred,
            representation_labels=labels,
            split_recommended=split,
            suggested_atomic_taxonomy_ids=atomic,
            reason=_text(payload["reason"], f"{field_path}.reason"),
            confidence=_confidence(payload["confidence"], f"{field_path}.confidence"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "ability_validity": self.ability_validity.value,
            "preferred_taxonomy_id": self.preferred_taxonomy_id,
            "representation_labels": [item.value for item in self.representation_labels],
            "split_recommended": self.split_recommended,
            "suggested_atomic_taxonomy_ids": list(self.suggested_atomic_taxonomy_ids),
            "reason": self.reason,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class SemanticShadowResponseV2:
    assessments: tuple[SemanticShadowAssessment, ...]

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        taxonomy_scopes: Mapping[str, Sequence[str]] | None = None,
        expected_candidate_ids: Sequence[str] | None = None,
    ) -> "SemanticShadowResponseV2":
        payload = _strict(
            value, {"schema_version", "assessments"}, "response")
        if payload["schema_version"] != SEMANTIC_SHADOW_RESPONSE_SCHEMA_VERSION:
            raise ShadowSchemaError(
                "response schema_version has an unsupported value",
                error_code="invalid_enum",
                invalid_field_path="schema_version",
                invalid_enum_field="schema_version",
            )
        raw = payload["assessments"]
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            raise ShadowSchemaError(
                "assessments must be a list",
                error_code="invalid_type",
                invalid_field_path="assessments",
            )
        items = []
        for index, item in enumerate(raw):
            candidate_id = item.get("candidate_id") if isinstance(item, Mapping) else None
            scope = None
            if isinstance(candidate_id, str) and taxonomy_scopes is not None:
                scope = taxonomy_scopes.get(candidate_id)
            items.append(SemanticShadowAssessment.from_dict(
                item,
                field_path=f"assessments[{index}]",
                taxonomy_scope=scope,
            ))
        ids = [item.candidate_id for item in items]
        if len(ids) != len(set(ids)):
            raise ShadowSchemaError(
                "assessment candidate IDs must be unique",
                error_code="duplicate_items",
                invalid_field_path="assessments",
                constraint_name="unique_candidate_id",
            )
        if expected_candidate_ids is not None and ids != list(expected_candidate_ids):
            raise ShadowSchemaError(
                "assessment candidate IDs do not match the requested candidates",
                error_code="constraint_violation",
                invalid_field_path="assessments",
                constraint_name="assessment_candidate_coverage",
            )
        return cls(assessments=tuple(items))

    @classmethod
    def parse_json(
        cls,
        content: str,
        **kwargs: Any,
    ) -> "SemanticShadowResponseV2":
        if not isinstance(content, str):
            raise ShadowSchemaError(
                "response content must be text",
                error_code="invalid_type",
                invalid_field_path="response",
            )
        try:
            value = json.loads(content)
        except json.JSONDecodeError as error:
            raise ShadowSchemaError(
                "response must be strict JSON",
                error_code="invalid_json",
                invalid_field_path="response",
            ) from error
        return cls.from_dict(value, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SEMANTIC_SHADOW_RESPONSE_SCHEMA_VERSION,
            "assessments": [item.to_dict() for item in self.assessments],
        }

    def serialize(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False)
