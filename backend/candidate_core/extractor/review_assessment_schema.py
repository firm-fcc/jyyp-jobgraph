"""Strict versioned contracts for deterministic shadow evidence assessment.

The structures in this module are deliberately independent from the production
``ReviewResult`` contract.  They use only the Python standard library and do
not perform model calls, controller actions, or candidate mutation.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, TypeVar


ASSESSMENT_SCHEMA_VERSION = "evidence_audit_result_v1"


class AssessmentSchemaError(ValueError):
    """Raised when a shadow assessment payload violates its contract."""


class EvidenceExactnessStatus(str, Enum):
    EXACT = "exact"
    MISSING = "missing"
    INVALID_RANGE = "invalid_range"
    TEXT_MISMATCH = "text_mismatch"
    WRONG_PROJECT = "wrong_project"
    DUPLICATE = "duplicate"
    AMBIGUOUS = "ambiguous"


class RequirementSupport(str, Enum):
    MET = "met"
    UNMET = "unmet"
    PARTIALLY_MET = "partially_met"
    NOT_APPLICABLE = "not_applicable"
    REQUIRES_MODEL_REVIEW = "requires_model_review"


class ComponentSupport(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    PARTIALLY_SUPPORTED = "partially_supported"
    AMBIGUOUS = "ambiguous"


class DeterministicEvidenceDecision(str, Enum):
    SUFFICIENT = "sufficient"
    INSUFFICIENT_BUT_RELOCATABLE = "insufficient_but_relocatable"
    INSUFFICIENT_AND_NOT_RELOCATABLE = "insufficient_and_not_relocatable"
    MISSING = "missing"
    REQUIRES_MODEL_REVIEW = "requires_model_review"


class CompoundAssessmentLabel(str, Enum):
    NOT_COMPOUND = "not_compound"
    COMPOUND_SUPPORTED = "compound_supported"
    COMPOUND_UNSUPPORTED = "compound_unsupported"
    SPLIT_RECOMMENDED = "split_recommended"
    AMBIGUOUS = "ambiguous"


TRACE_REASONS = {
    "exact_canonical",
    "alias",
    "ability_token",
    "fact_token",
    "behavior_token",
    "evidence_token",
    "related_tool",
    "related_knowledge",
    "parent",
    "child",
    "allowed_compound",
    "requirement_trigger",
    "safe_fallback",
}


EnumT = TypeVar("EnumT", bound=Enum)


def _strict_fields(
    value: Mapping[str, Any], required: set[str], prefix: str
) -> None:
    if not isinstance(value, Mapping):
        raise AssessmentSchemaError(f"{prefix} must be an object")
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing:
        raise AssessmentSchemaError(
            f"{prefix} missing fields: {', '.join(missing)}"
        )
    if unknown:
        raise AssessmentSchemaError(
            f"{prefix} unknown fields: {', '.join(unknown)}"
        )


def _non_empty(value: Any, prefix: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AssessmentSchemaError(f"{prefix} must be a non-empty string")
    return value.strip()


def _string(value: Any, prefix: str) -> str:
    if not isinstance(value, str):
        raise AssessmentSchemaError(f"{prefix} must be a string")
    return value


def _enum(enum_type: type[EnumT], value: Any, prefix: str) -> EnumT:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise AssessmentSchemaError(f"{prefix} must be a string enum value")
    try:
        return enum_type(value)
    except ValueError as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise AssessmentSchemaError(
            f"{prefix} is invalid: {value!r}; allowed: {allowed}"
        ) from error


def _string_tuple(
    value: Any, prefix: str, *, non_empty: bool = False
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise AssessmentSchemaError(f"{prefix} must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        text = _non_empty(item, f"{prefix}[{index}]")
        if text in result:
            raise AssessmentSchemaError(f"{prefix} contains duplicate: {text}")
        result.append(text)
    if non_empty and not result:
        raise AssessmentSchemaError(f"{prefix} must not be empty")
    return tuple(result)


def _optional_int(value: Any, prefix: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssessmentSchemaError(f"{prefix} must be an integer or null")
    return value


def _json_object(value: Any, prefix: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AssessmentSchemaError(f"{prefix} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise AssessmentSchemaError(f"{prefix} keys must be strings")
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise AssessmentSchemaError(f"{prefix} must be JSON-serializable") from error


@dataclass(frozen=True)
class EvidenceSpanAudit:
    evidence_index: int
    text: str
    start: int | None
    end: int | None
    project_id: str
    exactness_status: EvidenceExactnessStatus
    matched_catalog_span_id: str | None
    issues: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.evidence_index, bool) or not isinstance(
            self.evidence_index, int
        ) or self.evidence_index < 0:
            raise AssessmentSchemaError("evidence_index must be a non-negative integer")
        object.__setattr__(self, "text", _string(self.text, "EvidenceSpanAudit.text"))
        object.__setattr__(self, "project_id", _string(
            self.project_id, "EvidenceSpanAudit.project_id"))
        object.__setattr__(self, "start", _optional_int(
            self.start, "EvidenceSpanAudit.start"))
        object.__setattr__(self, "end", _optional_int(
            self.end, "EvidenceSpanAudit.end"))
        object.__setattr__(self, "exactness_status", _enum(
            EvidenceExactnessStatus, self.exactness_status,
            "EvidenceSpanAudit.exactness_status"))
        if self.matched_catalog_span_id is not None:
            object.__setattr__(self, "matched_catalog_span_id", _non_empty(
                self.matched_catalog_span_id,
                "EvidenceSpanAudit.matched_catalog_span_id"))
        object.__setattr__(self, "issues", _string_tuple(
            self.issues, "EvidenceSpanAudit.issues"))
        if self.exactness_status is EvidenceExactnessStatus.EXACT:
            if self.start is None or self.end is None:
                raise AssessmentSchemaError("exact evidence requires start and end")
            if self.matched_catalog_span_id is None:
                raise AssessmentSchemaError("exact evidence requires catalog span id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceSpanAudit":
        fields = {
            "evidence_index", "text", "start", "end", "project_id",
            "exactness_status", "matched_catalog_span_id", "issues",
        }
        _strict_fields(value, fields, "EvidenceSpanAudit")
        return cls(**{name: value[name] for name in fields})

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_index": self.evidence_index,
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "project_id": self.project_id,
            "exactness_status": self.exactness_status.value,
            "matched_catalog_span_id": self.matched_catalog_span_id,
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class RequirementCheck:
    requirement_id: str
    requirement_description: str
    status: RequirementSupport
    matched_texts: tuple[str, ...]
    matched_span_ids: tuple[str, ...]
    missing_items: tuple[str, ...]
    forbidden_shortcut_hits: tuple[str, ...]
    deterministic: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "requirement_id", _non_empty(
            self.requirement_id, "RequirementCheck.requirement_id"))
        object.__setattr__(self, "requirement_description", _non_empty(
            self.requirement_description,
            "RequirementCheck.requirement_description"))
        object.__setattr__(self, "status", _enum(
            RequirementSupport, self.status, "RequirementCheck.status"))
        for name in (
            "matched_texts", "matched_span_ids", "missing_items",
            "forbidden_shortcut_hits",
        ):
            object.__setattr__(self, name, _string_tuple(
                getattr(self, name), f"RequirementCheck.{name}"))
        if not isinstance(self.deterministic, bool):
            raise AssessmentSchemaError("RequirementCheck.deterministic must be bool")
        if not self.deterministic and self.status is not RequirementSupport.REQUIRES_MODEL_REVIEW:
            raise AssessmentSchemaError(
                "non-deterministic requirement must require model review"
            )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RequirementCheck":
        fields = {
            "requirement_id", "requirement_description", "status",
            "matched_texts", "matched_span_ids", "missing_items",
            "forbidden_shortcut_hits", "deterministic",
        }
        _strict_fields(value, fields, "RequirementCheck")
        return cls(**{name: value[name] for name in fields})

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "requirement_description": self.requirement_description,
            "status": self.status.value,
            "matched_texts": list(self.matched_texts),
            "matched_span_ids": list(self.matched_span_ids),
            "missing_items": list(self.missing_items),
            "forbidden_shortcut_hits": list(self.forbidden_shortcut_hits),
            "deterministic": self.deterministic,
        }


@dataclass(frozen=True)
class ComponentEvidenceAssessment:
    taxonomy_id: str
    canonical_name: str
    support: ComponentSupport
    current_evidence_requirement_checks: tuple[RequirementCheck, ...]
    relocation_requirement_checks: tuple[RequirementCheck, ...]
    matched_current_evidence: tuple[str, ...]
    matched_relocation_span_ids: tuple[str, ...]
    missing_requirements: tuple[str, ...]
    strong_qualifier_failures: tuple[str, ...]
    requires_model_review: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "taxonomy_id", _non_empty(
            self.taxonomy_id, "ComponentEvidenceAssessment.taxonomy_id"))
        object.__setattr__(self, "canonical_name", _non_empty(
            self.canonical_name, "ComponentEvidenceAssessment.canonical_name"))
        object.__setattr__(self, "support", _enum(
            ComponentSupport, self.support,
            "ComponentEvidenceAssessment.support"))
        for name in (
            "current_evidence_requirement_checks",
            "relocation_requirement_checks",
        ):
            raw = getattr(self, name)
            if not isinstance(raw, (list, tuple)):
                raise AssessmentSchemaError(f"{name} must be a list")
            checks = tuple(
                item if isinstance(item, RequirementCheck)
                else RequirementCheck.from_dict(item)
                for item in raw
            )
            ids = [item.requirement_id for item in checks]
            if len(ids) != len(set(ids)):
                raise AssessmentSchemaError(f"{name} has duplicate requirement_id")
            object.__setattr__(self, name, checks)
        for name in (
            "matched_current_evidence", "matched_relocation_span_ids",
            "missing_requirements", "strong_qualifier_failures",
        ):
            object.__setattr__(self, name, _string_tuple(
                getattr(self, name), f"ComponentEvidenceAssessment.{name}"))
        if not isinstance(self.requires_model_review, bool):
            raise AssessmentSchemaError(
                "ComponentEvidenceAssessment.requires_model_review must be bool")
        if self.requires_model_review and self.support is not ComponentSupport.AMBIGUOUS:
            raise AssessmentSchemaError(
                "model-review component support must be ambiguous")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ComponentEvidenceAssessment":
        fields = {
            "taxonomy_id", "canonical_name", "support",
            "current_evidence_requirement_checks",
            "relocation_requirement_checks", "matched_current_evidence",
            "matched_relocation_span_ids", "missing_requirements",
            "strong_qualifier_failures", "requires_model_review",
        }
        _strict_fields(value, fields, "ComponentEvidenceAssessment")
        return cls(**{name: value[name] for name in fields})

    def to_dict(self) -> dict[str, Any]:
        return {
            "taxonomy_id": self.taxonomy_id,
            "canonical_name": self.canonical_name,
            "support": self.support.value,
            "current_evidence_requirement_checks": [
                item.to_dict() for item in self.current_evidence_requirement_checks
            ],
            "relocation_requirement_checks": [
                item.to_dict() for item in self.relocation_requirement_checks
            ],
            "matched_current_evidence": list(self.matched_current_evidence),
            "matched_relocation_span_ids": list(self.matched_relocation_span_ids),
            "missing_requirements": list(self.missing_requirements),
            "strong_qualifier_failures": list(self.strong_qualifier_failures),
            "requires_model_review": self.requires_model_review,
        }


@dataclass(frozen=True)
class TaxonomySelectionTraceEntry:
    taxonomy_id: str
    score: int
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "taxonomy_id", _non_empty(
            self.taxonomy_id, "TaxonomySelectionTraceEntry.taxonomy_id"))
        if isinstance(self.score, bool) or not isinstance(self.score, int):
            raise AssessmentSchemaError("TaxonomySelectionTraceEntry.score must be int")
        object.__setattr__(self, "reasons", _string_tuple(
            self.reasons, "TaxonomySelectionTraceEntry.reasons", non_empty=True))
        unknown = sorted(set(self.reasons) - TRACE_REASONS)
        if unknown:
            raise AssessmentSchemaError(
                f"TaxonomySelectionTraceEntry has unknown reasons: {', '.join(unknown)}")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaxonomySelectionTraceEntry":
        fields = {"taxonomy_id", "score", "reasons"}
        _strict_fields(value, fields, "TaxonomySelectionTraceEntry")
        return cls(**{name: value[name] for name in fields})

    def to_dict(self) -> dict[str, Any]:
        return {
            "taxonomy_id": self.taxonomy_id,
            "score": self.score,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class EvidenceAuditResult:
    schema_version: str
    resume_id: str
    candidate_id: str
    current_evidence_audits: tuple[EvidenceSpanAudit, ...]
    taxonomy_subset_ids: tuple[str, ...]
    taxonomy_selection_trace: tuple[TaxonomySelectionTraceEntry, ...]
    component_assessments: tuple[ComponentEvidenceAssessment, ...]
    evidence_decision: DeterministicEvidenceDecision
    recommended_relocation_span_ids: tuple[str, ...]
    compound_label: CompoundAssessmentLabel
    blocking_issues: tuple[str, ...]
    non_blocking_notes: tuple[str, ...]
    requires_model_review: bool
    diagnostics: dict[str, Any]

    def __post_init__(self) -> None:
        if self.schema_version != ASSESSMENT_SCHEMA_VERSION:
            raise AssessmentSchemaError(
                f"schema_version must be {ASSESSMENT_SCHEMA_VERSION}")
        object.__setattr__(self, "resume_id", _non_empty(
            self.resume_id, "EvidenceAuditResult.resume_id"))
        object.__setattr__(self, "candidate_id", _non_empty(
            self.candidate_id, "EvidenceAuditResult.candidate_id"))

        typed_lists = (
            ("current_evidence_audits", EvidenceSpanAudit),
            ("taxonomy_selection_trace", TaxonomySelectionTraceEntry),
            ("component_assessments", ComponentEvidenceAssessment),
        )
        for name, item_type in typed_lists:
            raw = getattr(self, name)
            if not isinstance(raw, (list, tuple)):
                raise AssessmentSchemaError(f"EvidenceAuditResult.{name} must be a list")
            values = tuple(
                item if isinstance(item, item_type) else item_type.from_dict(item)
                for item in raw
            )
            object.__setattr__(self, name, values)

        for name in (
            "taxonomy_subset_ids", "recommended_relocation_span_ids",
            "blocking_issues", "non_blocking_notes",
        ):
            object.__setattr__(self, name, _string_tuple(
                getattr(self, name), f"EvidenceAuditResult.{name}"))
        component_ids = [item.taxonomy_id for item in self.component_assessments]
        if len(component_ids) != len(set(component_ids)):
            raise AssessmentSchemaError("component taxonomy_id must be unique")
        if not set(component_ids).issubset(self.taxonomy_subset_ids):
            raise AssessmentSchemaError("components must belong to taxonomy subset")
        trace_ids = [item.taxonomy_id for item in self.taxonomy_selection_trace]
        if len(trace_ids) != len(set(trace_ids)):
            raise AssessmentSchemaError("taxonomy trace IDs must be unique")
        if set(trace_ids) != set(self.taxonomy_subset_ids):
            raise AssessmentSchemaError("taxonomy trace must cover the complete subset")
        if len(self.current_evidence_audits) != len({
            item.evidence_index for item in self.current_evidence_audits
        }):
            raise AssessmentSchemaError("evidence_index must be unique")

        object.__setattr__(self, "evidence_decision", _enum(
            DeterministicEvidenceDecision, self.evidence_decision,
            "EvidenceAuditResult.evidence_decision"))
        object.__setattr__(self, "compound_label", _enum(
            CompoundAssessmentLabel, self.compound_label,
            "EvidenceAuditResult.compound_label"))
        if not isinstance(self.requires_model_review, bool):
            raise AssessmentSchemaError(
                "EvidenceAuditResult.requires_model_review must be bool")
        routing_component_ids = set(component_ids)
        if isinstance(self.diagnostics, Mapping):
            raw_target_ids = self.diagnostics.get("target_component_ids")
            if (
                isinstance(raw_target_ids, list)
                and all(isinstance(item, str) for item in raw_target_ids)
            ):
                routing_component_ids = set(raw_target_ids)
        component_requires = any(
            item.requires_model_review
            and item.taxonomy_id in routing_component_ids
            for item in self.component_assessments
        )
        decision_requires = (
            self.evidence_decision
            is DeterministicEvidenceDecision.REQUIRES_MODEL_REVIEW
        )
        compound_requires = self.compound_label is CompoundAssessmentLabel.AMBIGUOUS
        semantic_handoff = (
            "final_ability_representation_requires_model_review"
            in self.non_blocking_notes
        )
        if self.requires_model_review != (
            decision_requires or component_requires or compound_requires
            or semantic_handoff
        ):
            raise AssessmentSchemaError(
                "requires_model_review is inconsistent with nested assessments")
        if set(self.blocking_issues) & set(self.non_blocking_notes):
            raise AssessmentSchemaError(
                "an issue cannot be both blocking and non-blocking")
        object.__setattr__(self, "diagnostics", _json_object(
            copy.deepcopy(self.diagnostics), "EvidenceAuditResult.diagnostics"))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceAuditResult":
        fields = {
            "schema_version", "resume_id", "candidate_id",
            "current_evidence_audits", "taxonomy_subset_ids",
            "taxonomy_selection_trace", "component_assessments",
            "evidence_decision", "recommended_relocation_span_ids",
            "compound_label", "blocking_issues", "non_blocking_notes",
            "requires_model_review", "diagnostics",
        }
        _strict_fields(value, fields, "EvidenceAuditResult")
        return cls(**{name: value[name] for name in fields})

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "resume_id": self.resume_id,
            "candidate_id": self.candidate_id,
            "current_evidence_audits": [
                item.to_dict() for item in self.current_evidence_audits
            ],
            "taxonomy_subset_ids": list(self.taxonomy_subset_ids),
            "taxonomy_selection_trace": [
                item.to_dict() for item in self.taxonomy_selection_trace
            ],
            "component_assessments": [
                item.to_dict() for item in self.component_assessments
            ],
            "evidence_decision": self.evidence_decision.value,
            "recommended_relocation_span_ids": list(
                self.recommended_relocation_span_ids),
            "compound_label": self.compound_label.value,
            "blocking_issues": list(self.blocking_issues),
            "non_blocking_notes": list(self.non_blocking_notes),
            "requires_model_review": self.requires_model_review,
            "diagnostics": copy.deepcopy(self.diagnostics),
        }

    def serialize(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
