"""Rubric-guided, evidence-grounded proficiency evaluation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from extractor.agentic_llm_client import LLMCompletion
from extractor.agentic_schema import CandidateAbility, Evidence
from extractor.proficiency_validator import ProficiencyValidator
from extractor.review_assessment_schema import (
    DeterministicEvidenceDecision,
    EvidenceAuditResult,
)


LEVELS = frozenset({"P1", "P2", "P3", "P4", "U"})
SUFFICIENCY_VALUES = frozenset({"sufficient", "partial", "insufficient"})
DIMENSION_IDS = ("D1", "D2", "D3", "D4")


class ProficiencyEvaluationError(RuntimeError):
    """Base class for controlled proficiency failures."""


class ProficiencyParseError(ProficiencyEvaluationError):
    """Raised when the model response violates the strict result contract."""


class CompletionClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> LLMCompletion:
        ...


@dataclass(frozen=True)
class DimensionAssessment:
    level: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"level": self.level, "reason": self.reason}


@dataclass(frozen=True)
class ProficiencyResult:
    ability_id: str
    ability_name: str
    evidence_sufficiency: str
    dimensions: dict[str, DimensionAssessment]
    final_level: str
    reason: str
    uncertainty: tuple[str, ...]
    review_required: bool
    validator_flags: tuple[str, ...]
    rubric_version: str
    model: str
    prompt_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ability_id": self.ability_id,
            "ability_name": self.ability_name,
            "evidence_sufficiency": self.evidence_sufficiency,
            "dimensions": {
                key: self.dimensions[key].to_dict() for key in DIMENSION_IDS
            },
            "final_level": self.final_level,
            "reason": self.reason,
            "uncertainty": list(self.uncertainty),
            "review_required": self.review_required,
            "validator_flags": list(self.validator_flags),
            "rubric_version": self.rubric_version,
            "model": self.model,
            "prompt_sha256": self.prompt_sha256,
        }


class ProficiencyEvaluator:
    """Assess an already-confirmed ability from its audited evidence only."""

    def __init__(
        self,
        client: CompletionClient,
        rubric_path: str | Path | None = None,
        prompt_path: str | Path | None = None,
        validator: ProficiencyValidator | None = None,
    ) -> None:
        self.client = client
        root = Path(__file__).resolve().parent.parent
        self.rubric_path = Path(
            rubric_path or root / "config" / "proficiency_rubric.json"
        )
        self.prompt_path = Path(
            prompt_path or root / "config" / "proficiency_v0.2.txt"
        )
        self.rubric = self._load_rubric(self.rubric_path)
        self.system_prompt = self.prompt_path.read_text(
            encoding="utf-8-sig"
        ).strip()
        if not self.system_prompt:
            raise ValueError("proficiency prompt must not be empty")
        self.prompt_sha256 = hashlib.sha256(
            self.system_prompt.encode("utf-8")
        ).hexdigest()
        self.validator = validator or ProficiencyValidator()

    def evaluate(
        self,
        ability: CandidateAbility,
        evidence: Sequence[Evidence],
        audit_result: EvidenceAuditResult,
    ) -> ProficiencyResult:
        evidence_items = self._validate_inputs(ability, evidence, audit_result)
        ability_id = self._ability_id(ability)
        request = {
            "ability": {
                "ability_id": ability_id,
                "ability_name": ability.ability,
                "normalized_ability": ability.normalized_ability,
            },
            "evidence": [item.to_dict() for item in evidence_items],
            "evidence_profile": self._audit_profile(audit_result),
            "rubric": {
                "rubric_version": self.rubric["rubric_version"],
                "evidence_sufficiency": self.rubric["evidence_sufficiency"],
                "levels": self.rubric["levels"],
                "dimensions": self.rubric["dimensions"],
                "strict_rules": self.rubric["strict_rules"],
            },
            "ability_specific": self._ability_anchor(
                ability_id, ability.ability, ability.normalized_ability
            ),
        }
        completion = self.client.complete(
            self.system_prompt,
            json.dumps(request, ensure_ascii=False, separators=(",", ":")),
        )
        if not isinstance(completion, LLMCompletion):
            raise ProficiencyEvaluationError("client must return LLMCompletion")
        payload = self._parse_json_object(completion.content)
        normalized = self._validate_model_payload(payload)
        validation = self.validator.validate(normalized, evidence_items)

        return ProficiencyResult(
            ability_id=ability_id,
            ability_name=ability.ability,
            evidence_sufficiency=normalized["evidence_sufficiency"],
            dimensions=normalized["dimensions"],
            final_level=normalized["final_level"],
            reason=normalized["reason"],
            uncertainty=tuple(normalized["uncertainty"]),
            review_required=validation.review_required,
            validator_flags=validation.flags,
            rubric_version=self.rubric["rubric_version"],
            model=completion.model,
            prompt_sha256=self.prompt_sha256,
        )

    @staticmethod
    def _validate_inputs(
        ability: CandidateAbility,
        evidence: Sequence[Evidence],
        audit_result: EvidenceAuditResult,
    ) -> list[Evidence]:
        if not isinstance(ability, CandidateAbility):
            raise TypeError("ability must be CandidateAbility")
        if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
            raise TypeError("evidence must be a sequence of Evidence")
        evidence_items = list(evidence)
        if not evidence_items or any(
            not isinstance(item, Evidence) for item in evidence_items
        ):
            raise ValueError("evidence must contain at least one Evidence object")
        if [item.to_dict() for item in evidence_items] != [
            item.to_dict() for item in ability.evidence
        ]:
            raise ValueError("evidence must exactly match ability.evidence")
        if not isinstance(audit_result, EvidenceAuditResult):
            raise TypeError("audit_result must be EvidenceAuditResult")
        if audit_result.resume_id != ability.resume_id:
            raise ValueError("audit_result.resume_id must match ability.resume_id")
        if audit_result.candidate_id != ability.candidate_id:
            raise ValueError("audit_result.candidate_id must match ability.candidate_id")
        if (
            audit_result.evidence_decision
            is not DeterministicEvidenceDecision.SUFFICIENT
        ):
            raise ValueError("upstream ability evidence must be sufficient")
        if audit_result.requires_model_review:
            raise ValueError("upstream ability evidence still requires model review")
        return evidence_items

    @staticmethod
    def _ability_id(ability: CandidateAbility) -> str:
        for key in ("ability_id", "taxonomy_id"):
            value = ability.category.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ability.normalized_ability

    @staticmethod
    def _audit_profile(audit_result: EvidenceAuditResult) -> dict[str, Any]:
        return {
            "schema_version": audit_result.schema_version,
            "evidence_decision": audit_result.evidence_decision.value,
            "requires_model_review": audit_result.requires_model_review,
            "blocking_issues": list(audit_result.blocking_issues),
            "non_blocking_notes": list(audit_result.non_blocking_notes),
            "current_evidence_exactness": [
                item.exactness_status.value
                for item in audit_result.current_evidence_audits
            ],
            "component_support": [
                {
                    "taxonomy_id": item.taxonomy_id,
                    "canonical_name": item.canonical_name,
                    "support": item.support.value,
                    "missing_requirements": list(item.missing_requirements),
                }
                for item in audit_result.component_assessments
            ],
        }

    def _ability_anchor(
        self,
        ability_id: str,
        ability_name: str,
        normalized_ability: str,
    ) -> dict[str, Any]:
        candidates = {
            ability_id.casefold(),
            ability_name.casefold(),
            normalized_ability.casefold(),
        }
        for anchor_name, anchor in self.rubric["ability_specific"].items():
            declared = {
                str(item).casefold()
                for item in (
                    list(anchor.get("ability_ids", []))
                    + list(anchor.get("ability_names", []))
                )
            }
            if candidates & declared:
                return {"anchor_id": anchor_name, **anchor}
        return {}

    @classmethod
    def _parse_json_object(cls, content: str) -> Mapping[str, Any]:
        if not isinstance(content, str) or not content.strip():
            raise ProficiencyParseError("model content must be non-empty text")
        try:
            payload = json.loads(
                content.strip(),
                object_pairs_hook=cls._reject_duplicate_keys,
                parse_constant=cls._reject_json_constant,
            )
        except (json.JSONDecodeError, ValueError) as error:
            raise ProficiencyParseError(
                "model output must be exactly one valid JSON object"
            ) from error
        if not isinstance(payload, Mapping):
            raise ProficiencyParseError("model output JSON must be an object")
        return payload

    @staticmethod
    def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON field: {key}")
            result[key] = value
        return result

    @staticmethod
    def _reject_json_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON number is not allowed: {value}")

    @classmethod
    def _validate_model_payload(
        cls, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        expected = {
            "evidence_sufficiency",
            "dimensions",
            "final_level",
            "reason",
            "uncertainty",
        }
        cls._require_exact_fields(payload, expected, "proficiency result")
        sufficiency = payload["evidence_sufficiency"]
        if sufficiency not in SUFFICIENCY_VALUES:
            raise ProficiencyParseError("invalid evidence_sufficiency")
        final_level = payload["final_level"]
        if final_level not in LEVELS:
            raise ProficiencyParseError("invalid final_level")
        reason = cls._non_empty("reason", payload["reason"])

        dimensions_value = payload["dimensions"]
        if not isinstance(dimensions_value, Mapping):
            raise ProficiencyParseError("dimensions must be an object")
        cls._require_exact_fields(
            dimensions_value, set(DIMENSION_IDS), "dimensions"
        )
        dimensions: dict[str, DimensionAssessment] = {}
        for dimension_id in DIMENSION_IDS:
            item = dimensions_value[dimension_id]
            if not isinstance(item, Mapping):
                raise ProficiencyParseError(
                    f"dimensions.{dimension_id} must be an object"
                )
            cls._require_exact_fields(
                item, {"level", "reason"}, f"dimensions.{dimension_id}"
            )
            level = item["level"]
            if level not in LEVELS:
                raise ProficiencyParseError(
                    f"invalid dimensions.{dimension_id}.level"
                )
            dimensions[dimension_id] = DimensionAssessment(
                level=level,
                reason=cls._non_empty(
                    f"dimensions.{dimension_id}.reason", item["reason"]
                ),
            )

        uncertainty_value = payload["uncertainty"]
        if not isinstance(uncertainty_value, list):
            raise ProficiencyParseError("uncertainty must be a list")
        uncertainty = [
            cls._non_empty(f"uncertainty[{index}]", item)
            for index, item in enumerate(uncertainty_value)
        ]
        return {
            "evidence_sufficiency": sufficiency,
            "dimensions": dimensions,
            "final_level": final_level,
            "reason": reason,
            "uncertainty": uncertainty,
        }

    @staticmethod
    def _require_exact_fields(
        value: Mapping[str, Any], expected: set[str], name: str
    ) -> None:
        keys = set(value)
        missing = sorted(expected - keys)
        unknown = sorted(keys - expected)
        if missing or unknown:
            details = []
            if missing:
                details.append("missing: " + ", ".join(missing))
            if unknown:
                details.append("unknown: " + ", ".join(unknown))
            raise ProficiencyParseError(f"{name} fields invalid; " + "; ".join(details))

    @staticmethod
    def _non_empty(name: str, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ProficiencyParseError(f"{name} must be non-empty text")
        return value.strip()

    @staticmethod
    def _load_rubric(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"cannot load proficiency rubric: {path}") from error
        required = {
            "rubric_version",
            "scope",
            "theory_references",
            "evidence_sufficiency",
            "levels",
            "dimensions",
            "strict_rules",
            "ability_specific",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError("proficiency rubric has invalid root fields")
        if set(value["levels"]) != LEVELS:
            raise ValueError("proficiency rubric must define P1-P4 and U")
        if set(value["dimensions"]) != set(DIMENSION_IDS):
            raise ValueError("proficiency rubric must define D1-D4")
        if set(value["evidence_sufficiency"]) != SUFFICIENCY_VALUES:
            raise ValueError("proficiency rubric has invalid sufficiency values")
        if not isinstance(value["ability_specific"], dict):
            raise ValueError("ability_specific must be an object")
        return value
