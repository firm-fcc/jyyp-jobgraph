"""Strict data contracts for V3 Team Skill linking outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


_ALLOWED_STATUS = {"supported", "partially_supported", "unsupported"}
_ALLOWED_INFERENCE_MODE = {"direct_behavior", "aggregate_signal"}


@dataclass(frozen=True)
class EvidenceObservation:
    text: str
    source_experience_id: str
    start: int | None = None
    end: int | None = None
    fact: str = ""
    behavior: str = ""
    context: str = ""
    result: str = ""

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("evidence text must be non-empty")
        if not self.source_experience_id.strip():
            raise ValueError("source_experience_id must be non-empty")
        if (self.start is None) != (self.end is None):
            raise ValueError("start/end must both be present or both be None")
        if self.start is not None and (self.start < 0 or self.end is None or self.end < self.start):
            raise ValueError("invalid evidence span")

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "source_experience_id": self.source_experience_id,
            "start": self.start,
            "end": self.end,
            "fact": self.fact,
            "behavior": self.behavior,
            "context": self.context,
            "result": self.result,
        }


@dataclass(frozen=True)
class TeamSkillAssessment:
    candidate_id: str
    team_skill_id: str
    team_skill_name: str
    status: str
    inference_mode: str
    evidence: tuple[EvidenceObservation, ...] = field(default_factory=tuple)
    reason: str = ""
    confidence: float | None = None
    atomic_abilities: tuple[str, ...] = field(default_factory=tuple)
    audit_flags: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.candidate_id.strip():
            raise ValueError("candidate_id must be non-empty")
        if not self.team_skill_id.strip() or not self.team_skill_name.strip():
            raise ValueError("team skill id/name must be non-empty")
        if self.status not in _ALLOWED_STATUS:
            raise ValueError(f"unsupported status: {self.status}")
        if self.inference_mode not in _ALLOWED_INFERENCE_MODE:
            raise ValueError(f"unsupported inference mode: {self.inference_mode}")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0,1]")
        if self.status in {"supported", "partially_supported"}:
            if not self.evidence:
                raise ValueError("positive skill status must have at least one evidence item")
            if any(item.start is None or item.end is None for item in self.evidence):
                raise ValueError("positive skill evidence must be grounded with start/end offsets")
        normalized_atomic = tuple(item.strip() for item in self.atomic_abilities if item.strip())
        if len(normalized_atomic) != len(set(normalized_atomic)):
            raise ValueError("atomic_abilities must be unique non-empty strings")
        if self.status == "unsupported" and normalized_atomic:
            raise ValueError("unsupported skill must not contain atomic abilities")
        object.__setattr__(self, "atomic_abilities", normalized_atomic)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "team_skill_id": self.team_skill_id,
            "team_skill_name": self.team_skill_name,
            "status": self.status,
            "inference_mode": self.inference_mode,
            "evidence": [item.to_dict() for item in self.evidence],
            "reason": self.reason,
            "confidence": self.confidence,
            "atomic_abilities": list(self.atomic_abilities),
            "audit_flags": list(self.audit_flags),
        }


@dataclass(frozen=True)
class CandidateSkillProfile:
    candidate_id: str
    skill_registry_version: str
    assessments: tuple[TeamSkillAssessment, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "skill_registry_version": self.skill_registry_version,
            "assessments": [item.to_dict() for item in self.assessments],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CandidateSkillProfile":
        raw_assessments = payload.get("assessments", [])
        if not isinstance(raw_assessments, Sequence) or isinstance(raw_assessments, (str, bytes)):
            raise ValueError("assessments must be an array")
        assessments: list[TeamSkillAssessment] = []
        for raw in raw_assessments:
            if not isinstance(raw, Mapping):
                raise ValueError("assessment must be an object")
            raw_evidence = raw.get("evidence", [])
            if not isinstance(raw_evidence, Sequence) or isinstance(raw_evidence, (str, bytes)):
                raise ValueError("evidence must be an array")
            evidence = tuple(
                EvidenceObservation(
                    text=str(item.get("text", "")),
                    source_experience_id=str(item.get("source_experience_id", "")),
                    start=item.get("start"),
                    end=item.get("end"),
                    fact=str(item.get("fact", "")),
                    behavior=str(item.get("behavior", "")),
                    context=str(item.get("context", "")),
                    result=str(item.get("result", "")),
                )
                for item in raw_evidence
                if isinstance(item, Mapping)
            )
            assessments.append(
                TeamSkillAssessment(
                    candidate_id=str(raw.get("candidate_id", payload.get("candidate_id", ""))),
                    team_skill_id=str(raw.get("team_skill_id", "")),
                    team_skill_name=str(raw.get("team_skill_name", "")),
                    status=str(raw.get("status", "")),
                    inference_mode=str(raw.get("inference_mode", "")),
                    evidence=evidence,
                    reason=str(raw.get("reason", "")),
                    confidence=raw.get("confidence"),
                    atomic_abilities=tuple(
                        str(item).strip()
                        for item in raw.get("atomic_abilities", [])
                        if str(item).strip()
                    ),
                    audit_flags=tuple(str(item) for item in raw.get("audit_flags", [])),
                )
            )
        return cls(
            candidate_id=str(payload.get("candidate_id", "")),
            skill_registry_version=str(payload.get("skill_registry_version", "")),
            assessments=tuple(assessments),
            metadata=payload.get("metadata", {}) if isinstance(payload.get("metadata", {}), Mapping) else {},
        )
