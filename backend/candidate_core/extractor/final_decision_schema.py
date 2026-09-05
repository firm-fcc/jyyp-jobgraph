"""Strict response contract for the final ability decision layer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


FINAL_DECISION_RESPONSE_VERSION = "final_decision_response_v1"


class FinalDecisionSchemaError(ValueError):
    """Raised when a final-decision response violates the strict contract."""


class FinalDecisionType(str, Enum):
    MAPPED = "mapped"
    UNMAPPED = "unmapped"
    REJECT = "reject"
    SPLIT = "split"


class FormalClassificationStatus(str, Enum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    INSUFFICIENT = "insufficient"
    BLOCKED = "blocked"


def _object(value: Any, fields: set[str], path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FinalDecisionSchemaError(f"{path} must be an object")
    missing = sorted(fields - set(value))
    unknown = sorted(set(value) - fields)
    if missing:
        raise FinalDecisionSchemaError(
            f"{path} missing fields: {', '.join(missing)}"
        )
    if unknown:
        raise FinalDecisionSchemaError(
            f"{path} unknown fields: {', '.join(unknown)}"
        )
    return value


def _text(value: Any, path: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise FinalDecisionSchemaError(f"{path} must be a non-empty string")
    return value.strip()


def _bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise FinalDecisionSchemaError(f"{path} must be bool")
    return value


def _enum(enum_type: type[Enum], value: Any, path: str):
    if not isinstance(value, str):
        raise FinalDecisionSchemaError(f"{path} must be a string enum")
    try:
        return enum_type(value)
    except ValueError as error:
        raise FinalDecisionSchemaError(f"{path} has an invalid value") from error


@dataclass(frozen=True)
class AtomicFinalDecision:
    decision: FinalDecisionType
    ability_id: str | None
    unmapped_ability: str | None
    classification_status: FormalClassificationStatus
    evidence: str
    reason: str
    review_required: bool
    blocking_basis: str | None

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        path: str = "decision",
    ) -> "AtomicFinalDecision":
        fields = {
            "decision",
            "ability_id",
            "unmapped_ability",
            "classification_status",
            "evidence",
            "reason",
            "review_required",
            "blocking_basis",
        }
        payload = _object(value, fields, path)
        decision = _enum(FinalDecisionType, payload["decision"], f"{path}.decision")
        if decision not in {
            FinalDecisionType.MAPPED,
            FinalDecisionType.UNMAPPED,
            FinalDecisionType.REJECT,
        }:
            raise FinalDecisionSchemaError(
                f"{path}.decision must be mapped, unmapped, or reject"
            )
        ability_id = _text(payload["ability_id"], f"{path}.ability_id", optional=True)
        unmapped = _text(
            payload["unmapped_ability"],
            f"{path}.unmapped_ability",
            optional=True,
        )
        status = _enum(
            FormalClassificationStatus,
            payload["classification_status"],
            f"{path}.classification_status",
        )
        blocking = _text(
            payload["blocking_basis"],
            f"{path}.blocking_basis",
            optional=True,
        )
        if decision is FinalDecisionType.MAPPED:
            if ability_id is None or unmapped is not None:
                raise FinalDecisionSchemaError(
                    f"{path} mapped decision requires only ability_id"
                )
        elif decision is FinalDecisionType.UNMAPPED:
            if ability_id is not None or unmapped is None:
                raise FinalDecisionSchemaError(
                    f"{path} unmapped decision requires only unmapped_ability"
                )
        else:
            if ability_id is not None or unmapped is not None:
                raise FinalDecisionSchemaError(
                    f"{path} reject decision cannot carry an ability"
                )
            if status not in {
                FormalClassificationStatus.INSUFFICIENT,
                FormalClassificationStatus.BLOCKED,
            }:
                raise FinalDecisionSchemaError(
                    f"{path} reject decision must be insufficient or blocked"
                )
        if status is FormalClassificationStatus.BLOCKED and blocking is None:
            raise FinalDecisionSchemaError(
                f"{path}.blocking_basis is required for blocked"
            )
        if status is not FormalClassificationStatus.BLOCKED and blocking is not None:
            raise FinalDecisionSchemaError(
                f"{path}.blocking_basis is only allowed for blocked"
            )
        return cls(
            decision=decision,
            ability_id=ability_id,
            unmapped_ability=unmapped,
            classification_status=status,
            evidence=_text(payload["evidence"], f"{path}.evidence"),
            reason=_text(payload["reason"], f"{path}.reason"),
            review_required=_bool(
                payload["review_required"], f"{path}.review_required"
            ),
            blocking_basis=blocking,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "ability_id": self.ability_id,
            "unmapped_ability": self.unmapped_ability,
            "classification_status": self.classification_status.value,
            "evidence": self.evidence,
            "reason": self.reason,
            "review_required": self.review_required,
            "blocking_basis": self.blocking_basis,
        }


@dataclass(frozen=True)
class CandidateFinalDecision:
    candidate_id: str
    decision: FinalDecisionType
    ability_id: str | None
    unmapped_ability: str | None
    classification_status: FormalClassificationStatus | None
    evidence: str | None
    reason: str
    review_required: bool
    blocking_basis: str | None
    records: tuple[AtomicFinalDecision, ...]

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        path: str = "decision",
    ) -> "CandidateFinalDecision":
        fields = {
            "candidate_id",
            "decision",
            "ability_id",
            "unmapped_ability",
            "classification_status",
            "evidence",
            "reason",
            "review_required",
            "blocking_basis",
            "records",
        }
        payload = _object(value, fields, path)
        candidate_id = _text(payload["candidate_id"], f"{path}.candidate_id")
        decision = _enum(FinalDecisionType, payload["decision"], f"{path}.decision")
        records_value = payload["records"]
        if isinstance(records_value, (str, bytes)) or not isinstance(
            records_value, Sequence
        ):
            raise FinalDecisionSchemaError(f"{path}.records must be a list")
        records = tuple(
            AtomicFinalDecision.from_dict(item, path=f"{path}.records[{index}]")
            for index, item in enumerate(records_value)
        )
        reason = _text(payload["reason"], f"{path}.reason")
        review_required = _bool(
            payload["review_required"], f"{path}.review_required"
        )
        if decision is FinalDecisionType.SPLIT:
            for field in (
                "ability_id",
                "unmapped_ability",
                "classification_status",
                "evidence",
                "blocking_basis",
            ):
                if payload[field] is not None:
                    raise FinalDecisionSchemaError(
                        f"{path}.{field} must be null for split"
                    )
            if len(records) < 2:
                raise FinalDecisionSchemaError(
                    f"{path}.records requires at least two atomic records for split"
                )
            return cls(
                candidate_id=candidate_id,
                decision=decision,
                ability_id=None,
                unmapped_ability=None,
                classification_status=None,
                evidence=None,
                reason=reason,
                review_required=review_required,
                blocking_basis=None,
                records=records,
            )
        if records:
            raise FinalDecisionSchemaError(
                f"{path}.records must be empty for non-split decision"
            )
        atomic = AtomicFinalDecision.from_dict(
            {
                "decision": payload["decision"],
                "ability_id": payload["ability_id"],
                "unmapped_ability": payload["unmapped_ability"],
                "classification_status": payload["classification_status"],
                "evidence": payload["evidence"],
                "reason": payload["reason"],
                "review_required": payload["review_required"],
                "blocking_basis": payload["blocking_basis"],
            },
            path=path,
        )
        return cls(
            candidate_id=candidate_id,
            decision=atomic.decision,
            ability_id=atomic.ability_id,
            unmapped_ability=atomic.unmapped_ability,
            classification_status=atomic.classification_status,
            evidence=atomic.evidence,
            reason=atomic.reason,
            review_required=atomic.review_required,
            blocking_basis=atomic.blocking_basis,
            records=(),
        )

    def atomic_records(self) -> tuple[AtomicFinalDecision, ...]:
        if self.decision is FinalDecisionType.SPLIT:
            return self.records
        return (
            AtomicFinalDecision(
                decision=self.decision,
                ability_id=self.ability_id,
                unmapped_ability=self.unmapped_ability,
                classification_status=self.classification_status,
                evidence=self.evidence,
                reason=self.reason,
                review_required=self.review_required,
                blocking_basis=self.blocking_basis,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "decision": self.decision.value,
            "ability_id": self.ability_id,
            "unmapped_ability": self.unmapped_ability,
            "classification_status": (
                None
                if self.classification_status is None
                else self.classification_status.value
            ),
            "evidence": self.evidence,
            "reason": self.reason,
            "review_required": self.review_required,
            "blocking_basis": self.blocking_basis,
            "records": [item.to_dict() for item in self.records],
        }


@dataclass(frozen=True)
class FinalDecisionResponse:
    decisions: tuple[CandidateFinalDecision, ...]

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        expected_candidate_ids: Sequence[str] | None = None,
    ) -> "FinalDecisionResponse":
        payload = _object(
            value, {"schema_version", "decisions"}, "response"
        )
        if payload["schema_version"] != FINAL_DECISION_RESPONSE_VERSION:
            raise FinalDecisionSchemaError("response schema_version is invalid")
        raw = payload["decisions"]
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            raise FinalDecisionSchemaError("response.decisions must be a list")
        decisions = tuple(
            CandidateFinalDecision.from_dict(
                item, path=f"decisions[{index}]"
            )
            for index, item in enumerate(raw)
        )
        ids = [item.candidate_id for item in decisions]
        if len(ids) != len(set(ids)):
            raise FinalDecisionSchemaError("decision candidate_ids must be unique")
        if expected_candidate_ids is not None and ids != list(expected_candidate_ids):
            raise FinalDecisionSchemaError(
                "decision candidate_ids do not match requested candidates"
            )
        return cls(decisions=decisions)

    @classmethod
    def parse_json(
        cls,
        content: str,
        **kwargs: Any,
    ) -> "FinalDecisionResponse":
        if not isinstance(content, str) or not content.strip():
            raise FinalDecisionSchemaError("response content must be non-empty text")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as error:
            raise FinalDecisionSchemaError(
                "response must be one strict JSON document"
            ) from error
        return cls.from_dict(payload, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": FINAL_DECISION_RESPONSE_VERSION,
            "decisions": [item.to_dict() for item in self.decisions],
        }
