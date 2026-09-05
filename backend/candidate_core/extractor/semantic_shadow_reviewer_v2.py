"""Independent semantic-only Shadow Reviewer v2.

This module deliberately does not reuse the v1 response parser.  It builds a
bounded semantic request, accepts one strict v2 response from an injected
client, and returns only :class:`SemanticShadowAssessment` objects.  It has no
network client factory, Mapper, Controller, or output writer.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from extractor.ability_shadow_schema import ShadowSchemaError
from extractor.ability_taxonomy_v2 import AbilityTaxonomyV2, TaxonomyNode
from extractor.agentic_llm_client import LLMCompletion
from extractor.agentic_schema import CandidateAbility
from extractor.review_assessment_schema import (
    ComponentEvidenceAssessment,
    ComponentSupport,
    EvidenceAuditResult,
)
from extractor.semantic_shadow_schema_v2 import (
    SEMANTIC_SHADOW_RESPONSE_SCHEMA_VERSION,
    SemanticShadowAssessment,
    SemanticShadowResponseV2,
)


SEMANTIC_SHADOW_REQUEST_SCHEMA_VERSION = "ability_shadow_semantic_request_v2"
DEFAULT_SEMANTIC_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "agentic_ability_reviewer_prompt_v2.txt"
)
_ASSESSMENT_INDEX = re.compile(r"^assessments\[(\d+)\]")


class SemanticShadowReviewerError(RuntimeError):
    """Base class for bounded, non-sensitive v2 Reviewer failures."""

    def __init__(
        self,
        message: str,
        *,
        assessment_index: int | None = None,
        candidate_id: str | None = None,
        error_code: str = "semantic_reviewer_error",
        invalid_field_path: str | None = None,
        missing_field_names: Sequence[str] = (),
        unexpected_field_names: Sequence[str] = (),
        invalid_enum_field: str | None = None,
        constraint_name: str | None = None,
        root_json_type: str | None = None,
        root_field_names: Sequence[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.assessment_index = assessment_index
        self.candidate_id = candidate_id
        self.error_code = error_code
        self.invalid_field_path = invalid_field_path
        self.missing_field_names = tuple(missing_field_names)
        self.unexpected_field_names = tuple(unexpected_field_names)
        self.invalid_enum_field = invalid_enum_field
        self.constraint_name = constraint_name
        self.root_json_type = root_json_type
        self.root_field_names = (
            None if root_field_names is None else tuple(root_field_names)
        )
        self.model: str | None = None
        self.elapsed_ms: float | None = None
        self.usage: dict[str, Any] | None = None
        self.response_sha256: str | None = None
        self.response_bytes: int | None = None

    def attach_completion(self, completion: LLMCompletion) -> None:
        self.model = completion.model
        self.elapsed_ms = completion.elapsed_ms
        self.usage = (
            None if completion.usage is None else copy.deepcopy(completion.usage)
        )
        encoded = completion.content.encode("utf-8")
        self.response_sha256 = hashlib.sha256(encoded).hexdigest()
        self.response_bytes = len(encoded)

    def diagnostics_dict(self) -> dict[str, Any]:
        return {
            "assessment_index": self.assessment_index,
            "candidate_id": self.candidate_id,
            "error_code": self.error_code,
            "invalid_field_path": self.invalid_field_path,
            "missing_field_names": list(self.missing_field_names),
            "unexpected_field_names": list(self.unexpected_field_names),
            "invalid_enum_field": self.invalid_enum_field,
            "constraint_name": self.constraint_name,
            "root_json_type": self.root_json_type,
            "root_field_names": (
                None if self.root_field_names is None
                else list(self.root_field_names)
            ),
            "model": self.model,
            "elapsed_ms": self.elapsed_ms,
            "usage": copy.deepcopy(self.usage),
            "response_sha256": self.response_sha256,
            "response_bytes": self.response_bytes,
        }


class SemanticShadowParseError(SemanticShadowReviewerError):
    """Raised when the model output is not one strict JSON document."""


class SemanticShadowContractError(SemanticShadowReviewerError):
    """Raised when parsed JSON violates the semantic v2 contract."""


class SemanticShadowCompletionClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> LLMCompletion:
        ...


@dataclass(frozen=True)
class SemanticShadowReviewBatchV2:
    resume_id: str
    assessments: tuple[SemanticShadowAssessment, ...]
    deterministic_only_candidate_ids: tuple[str, ...]
    diagnostics: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.resume_id, str) or not self.resume_id.strip():
            raise SemanticShadowContractError("resume_id must be non-empty")
        assessments = tuple(self.assessments)
        deterministic = tuple(self.deterministic_only_candidate_ids)
        ids = [item.candidate_id for item in assessments]
        if len(ids) != len(set(ids)):
            raise SemanticShadowContractError(
                "semantic assessment candidate IDs must be unique")
        if len(deterministic) != len(set(deterministic)):
            raise SemanticShadowContractError(
                "deterministic-only candidate IDs must be unique")
        if set(ids) & set(deterministic):
            raise SemanticShadowContractError(
                "candidate cannot be semantic-reviewed and deterministic-only")
        object.__setattr__(self, "assessments", assessments)
        object.__setattr__(self, "deterministic_only_candidate_ids", deterministic)
        object.__setattr__(self, "diagnostics", copy.deepcopy(self.diagnostics))

    def to_dict(self) -> dict[str, Any]:
        return {
            "resume_id": self.resume_id,
            "assessments": [item.to_dict() for item in self.assessments],
            "deterministic_only_candidate_ids": list(
                self.deterministic_only_candidate_ids),
            "diagnostics": copy.deepcopy(self.diagnostics),
        }


def _load_prompt(path_value: str | Path | None) -> tuple[Path, str, str]:
    path = DEFAULT_SEMANTIC_PROMPT_PATH if path_value is None else Path(path_value)
    if not path.is_file():
        raise ValueError(f"semantic shadow prompt must be an existing file: {path}")
    content = path.read_text(encoding="utf-8-sig")
    if not content.strip():
        raise ValueError("semantic shadow prompt must not be empty")
    return path, content, hashlib.sha256(content.encode("utf-8")).hexdigest()


def _taxonomy_sha256(taxonomy: AbilityTaxonomyV2) -> str:
    return hashlib.sha256(taxonomy.serialize().encode("utf-8")).hexdigest()


def _audit_sha256(audit: EvidenceAuditResult) -> str:
    return hashlib.sha256(audit.serialize().encode("utf-8")).hexdigest()


def _root_type(value: Any) -> str:
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "unknown"


def _root_names(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, Mapping):
        return None
    names = sorted(str(key)[:80] for key in value)[:32]
    return tuple(names)


class SemanticShadowReviewerV2:
    """Batch unresolved candidates into one injected semantic v2 call."""

    def __init__(
        self,
        client: SemanticShadowCompletionClient,
        taxonomy: AbilityTaxonomyV2,
        prompt_path: str | Path | None = None,
    ) -> None:
        if client is None or not callable(getattr(client, "complete", None)):
            raise TypeError("client must provide complete(system_prompt, user_prompt)")
        if not isinstance(taxonomy, AbilityTaxonomyV2):
            raise TypeError("taxonomy must be AbilityTaxonomyV2")
        self.client = client
        self.taxonomy = taxonomy
        self.prompt_path, self.system_prompt, self.prompt_sha256 = _load_prompt(
            prompt_path)
        self.prompt_file = self.prompt_path.name
        self.taxonomy_sha256 = _taxonomy_sha256(taxonomy)

    def build_request(
        self,
        resume_id: str,
        candidates: Sequence[CandidateAbility],
        audits: Sequence[EvidenceAuditResult],
    ) -> dict[str, Any]:
        candidate_by_id, audit_by_id, ordered_ids = self._validate_inputs(
            resume_id, candidates, audits)
        return {
            "schema_version": SEMANTIC_SHADOW_REQUEST_SCHEMA_VERSION,
            "resume_id": resume_id,
            "taxonomy_version": self.taxonomy.taxonomy_version,
            "review_scope": "semantic_only",
            "candidate_contexts": [
                self._candidate_context(
                    candidate_by_id[candidate_id], audit_by_id[candidate_id])
                for candidate_id in ordered_ids
                if audit_by_id[candidate_id].requires_model_review
            ],
        }

    def review(
        self,
        resume_id: str,
        candidates: Sequence[CandidateAbility],
        audits: Sequence[EvidenceAuditResult],
    ) -> SemanticShadowReviewBatchV2:
        candidate_by_id, audit_by_id, ordered_ids = self._validate_inputs(
            resume_id, candidates, audits)
        model_ids = tuple(
            candidate_id for candidate_id in ordered_ids
            if audit_by_id[candidate_id].requires_model_review)
        deterministic_ids = tuple(
            candidate_id for candidate_id in ordered_ids
            if not audit_by_id[candidate_id].requires_model_review)
        payload = self.build_request(resume_id, candidates, audits)
        encoded_request = json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False)
        if not model_ids:
            return SemanticShadowReviewBatchV2(
                resume_id=resume_id,
                assessments=(),
                deterministic_only_candidate_ids=deterministic_ids,
                diagnostics=self._diagnostics(
                    len(ordered_ids), 0, len(deterministic_ids), 0,
                    len(encoded_request.encode("utf-8")), None),
            )
        completion = self.client.complete(self.system_prompt, encoded_request)
        if not isinstance(completion, LLMCompletion):
            raise SemanticShadowReviewerError("client must return LLMCompletion")
        try:
            assessments = self._parse_response(
                completion.content, model_ids, audit_by_id)
        except SemanticShadowReviewerError as error:
            error.attach_completion(completion)
            raise
        return SemanticShadowReviewBatchV2(
            resume_id=resume_id,
            assessments=assessments,
            deterministic_only_candidate_ids=deterministic_ids,
            diagnostics=self._diagnostics(
                len(ordered_ids), len(model_ids), len(deterministic_ids),
                len(assessments), len(encoded_request.encode("utf-8")), completion),
        )

    def parse_response(
        self,
        content: str,
        resume_id: str,
        candidates: Sequence[CandidateAbility],
        audits: Sequence[EvidenceAuditResult],
    ) -> tuple[SemanticShadowAssessment, ...]:
        _, audit_by_id, ordered_ids = self._validate_inputs(
            resume_id, candidates, audits)
        model_ids = tuple(
            candidate_id for candidate_id in ordered_ids
            if audit_by_id[candidate_id].requires_model_review)
        return self._parse_response(content, model_ids, audit_by_id)

    @staticmethod
    def _validate_inputs(
        resume_id: str,
        candidates: Sequence[CandidateAbility],
        audits: Sequence[EvidenceAuditResult],
    ) -> tuple[
        dict[str, CandidateAbility],
        dict[str, EvidenceAuditResult],
        tuple[str, ...],
    ]:
        if not isinstance(resume_id, str) or not resume_id.strip():
            raise ValueError("resume_id must be non-empty")
        if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
            raise TypeError("candidates must be a sequence")
        if isinstance(audits, (str, bytes)) or not isinstance(audits, Sequence):
            raise TypeError("audits must be a sequence")
        candidate_by_id: dict[str, CandidateAbility] = {}
        ordered_ids: list[str] = []
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, CandidateAbility):
                raise TypeError(f"candidates[{index}] must be CandidateAbility")
            if candidate.resume_id != resume_id:
                raise SemanticShadowContractError(
                    "candidate belongs to a different resume",
                    candidate_id=candidate.candidate_id,
                    invalid_field_path="resume_id")
            if candidate.candidate_id in candidate_by_id:
                raise SemanticShadowContractError(
                    "duplicate candidate_id in candidates",
                    candidate_id=candidate.candidate_id,
                    invalid_field_path="candidate_id")
            candidate_by_id[candidate.candidate_id] = candidate
            ordered_ids.append(candidate.candidate_id)
        audit_by_id: dict[str, EvidenceAuditResult] = {}
        for index, audit in enumerate(audits):
            if not isinstance(audit, EvidenceAuditResult):
                raise TypeError(f"audits[{index}] must be EvidenceAuditResult")
            if audit.resume_id != resume_id:
                raise SemanticShadowContractError(
                    "audit belongs to a different resume",
                    candidate_id=audit.candidate_id,
                    invalid_field_path="resume_id")
            if audit.candidate_id in audit_by_id:
                raise SemanticShadowContractError(
                    "duplicate candidate_id in audits",
                    candidate_id=audit.candidate_id,
                    invalid_field_path="candidate_id")
            audit_by_id[audit.candidate_id] = audit
        missing = sorted(set(candidate_by_id) - set(audit_by_id))
        unknown = sorted(set(audit_by_id) - set(candidate_by_id))
        if missing:
            raise SemanticShadowContractError(
                "candidate is missing EvidenceAuditResult",
                candidate_id=missing[0], invalid_field_path="audits")
        if unknown:
            raise SemanticShadowContractError(
                "EvidenceAuditResult references unknown candidate",
                candidate_id=unknown[0], invalid_field_path="audits")
        return candidate_by_id, audit_by_id, tuple(ordered_ids)

    def _candidate_context(
        self,
        candidate: CandidateAbility,
        audit: EvidenceAuditResult,
    ) -> dict[str, Any]:
        components = [self._component_summary(item)
                      for item in audit.component_assessments]
        supported_ids = [
            item.taxonomy_id for item in audit.component_assessments
            if item.support is ComponentSupport.SUPPORTED
        ]
        unsupported_ids = [
            item.taxonomy_id for item in audit.component_assessments
            if item.support is ComponentSupport.UNSUPPORTED
        ]
        available_atomic_ids = [
            taxonomy_id for taxonomy_id in supported_ids
            if self.taxonomy.get_node(taxonomy_id).node_type in {"activity", "ability"}
        ]
        return {
            "candidate_id": candidate.candidate_id,
            "ability": candidate.ability,
            "normalized_ability": candidate.normalized_ability,
            "fact": candidate.fact,
            "behavior": candidate.behavior,
            "reason": candidate.reason,
            "evidence_auditor_constraints": {
                "evidence_audit_sha256": _audit_sha256(audit),
                "evidence_decision": audit.evidence_decision.value,
                "compound_label": audit.compound_label.value,
                "blocking_issues": list(audit.blocking_issues),
                "non_blocking_notes": list(audit.non_blocking_notes),
                "component_support": components,
            },
            "supported_component_ids": supported_ids,
            "unsupported_component_ids": unsupported_ids,
            "available_atomic_taxonomy_ids": available_atomic_ids,
            "taxonomy_subset": [
                self._taxonomy_summary(self.taxonomy.get_node(taxonomy_id))
                for taxonomy_id in audit.taxonomy_subset_ids
            ],
        }

    @staticmethod
    def _component_summary(
        component: ComponentEvidenceAssessment,
    ) -> dict[str, Any]:
        return {
            "taxonomy_id": component.taxonomy_id,
            "support": component.support.value,
            "missing_requirements": list(component.missing_requirements),
            "strong_qualifier_failures": list(
                component.strong_qualifier_failures),
        }

    @staticmethod
    def _taxonomy_summary(node: TaxonomyNode) -> dict[str, Any]:
        return {
            "id": node.id,
            "canonical_name": node.canonical_name,
            "node_type": node.node_type,
            "level": node.level,
            "description": node.description,
            "includes": list(node.includes),
            "excludes": list(node.excludes),
            "definition_confidence": node.confidence,
            "review_status": node.review_status,
            "strong_qualifiers": list(node.strong_qualifiers),
            "allowed_compounds": list(node.allowed_compounds),
            "forbidden_inferences": list(node.forbidden_inferences),
        }

    def _parse_response(
        self,
        content: str,
        expected_ids: Sequence[str],
        audit_by_id: Mapping[str, EvidenceAuditResult],
    ) -> tuple[SemanticShadowAssessment, ...]:
        if not isinstance(content, str):
            raise SemanticShadowParseError(
                "response content must be text",
                error_code="invalid_type", invalid_field_path="response")
        try:
            decoded = json.loads(content)
        except json.JSONDecodeError as error:
            raise SemanticShadowParseError(
                "response must be one strict JSON document",
                error_code="invalid_json", invalid_field_path="response") from error
        scopes = {
            candidate_id: audit_by_id[candidate_id].taxonomy_subset_ids
            for candidate_id in expected_ids
        }
        try:
            response = SemanticShadowResponseV2.from_dict(
                decoded,
                taxonomy_scopes=scopes,
                expected_candidate_ids=expected_ids,
            )
        except ShadowSchemaError as error:
            index = None
            match = _ASSESSMENT_INDEX.match(error.invalid_field_path or "")
            if match is not None:
                index = int(match.group(1))
            candidate_id = None
            if (
                index is not None
                and isinstance(decoded, Mapping)
                and isinstance(decoded.get("assessments"), list)
                and index < len(decoded["assessments"])
                and isinstance(decoded["assessments"][index], Mapping)
            ):
                raw_id = decoded["assessments"][index].get("candidate_id")
                if isinstance(raw_id, str) and raw_id in set(expected_ids):
                    candidate_id = raw_id
            raise SemanticShadowContractError(
                "semantic response violates the v2 contract",
                assessment_index=index,
                candidate_id=candidate_id,
                error_code=error.error_code,
                invalid_field_path=error.invalid_field_path,
                missing_field_names=error.missing_field_names,
                unexpected_field_names=error.unexpected_field_names,
                invalid_enum_field=error.invalid_enum_field,
                constraint_name=error.constraint_name,
                root_json_type=_root_type(decoded),
                root_field_names=_root_names(decoded),
            ) from error
        return response.assessments

    def _diagnostics(
        self,
        input_count: int,
        model_count: int,
        deterministic_count: int,
        accepted_count: int,
        request_size: int,
        completion: LLMCompletion | None,
    ) -> dict[str, Any]:
        return {
            "prompt_file": self.prompt_file,
            "prompt_sha256": self.prompt_sha256,
            "taxonomy_version": self.taxonomy.taxonomy_version,
            "taxonomy_sha256": self.taxonomy_sha256,
            "request_contract_version": SEMANTIC_SHADOW_REQUEST_SCHEMA_VERSION,
            "response_contract_version": SEMANTIC_SHADOW_RESPONSE_SCHEMA_VERSION,
            "input_candidate_count": input_count,
            "model_review_candidate_count": model_count,
            "deterministic_only_count": deterministic_count,
            "accepted_assessment_count": accepted_count,
            "request_size_bytes": request_size,
            "model": None if completion is None else completion.model,
            "elapsed_ms": None if completion is None else completion.elapsed_ms,
            "usage": (
                None if completion is None or completion.usage is None
                else copy.deepcopy(completion.usage)
            ),
            "response_sha256": (
                None if completion is None
                else hashlib.sha256(
                    completion.content.encode("utf-8")).hexdigest()
            ),
            "controller_executed": False,
        }
