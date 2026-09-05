"""Offline-capable semantic-only Shadow Ability Reviewer.

The reviewer consumes immutable ``EvidenceAuditResult`` constraints, batches
all semantic handoffs for one resume into one injected client call, and emits
only ``ShadowAbilityAssessment`` objects.  It has no production client,
``ReviewResult`` mapper, Controller integration, or filesystem output path.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from extractor.ability_shadow_schema import (
    SHADOW_ASSESSMENT_SCHEMA_VERSION,
    SHADOW_RESPONSE_SCHEMA_VERSION,
    AbilityValidity,
    SemanticComponentSupport,
    ShadowAbilityAssessment,
    ShadowSchemaError,
)
from extractor.ability_taxonomy_v2 import AbilityTaxonomyV2, TaxonomyNode
from extractor.agentic_llm_client import LLMCompletion
from extractor.agentic_schema import CandidateAbility
from extractor.review_assessment_schema import (
    ComponentEvidenceAssessment,
    ComponentSupport,
    CompoundAssessmentLabel,
    EvidenceAuditResult,
)


SHADOW_REQUEST_SCHEMA_VERSION = "ability_shadow_request_v1"
DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "agentic_ability_reviewer_prompt_v1.txt"
)


class ShadowReviewerError(RuntimeError):
    """Base class for safe Shadow Reviewer failures."""

    def __init__(
        self,
        message: str,
        *,
        assessment_index: int | None = None,
        candidate_id: str | None = None,
        invalid_field: str | None = None,
        invalid_reference_id: str | None = None,
        root_json_type: str | None = None,
        root_field_names: Sequence[str] | None = None,
        missing_fields: Sequence[str] = (),
        error_code: str = "reviewer_contract_error",
        invalid_field_path: str | None = None,
        missing_field_names: Sequence[str] = (),
        unexpected_field_names: Sequence[str] = (),
        invalid_enum_field: str | None = None,
        constraint_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.assessment_index = assessment_index
        self.candidate_id = candidate_id
        self.invalid_field = invalid_field
        self.invalid_reference_id = invalid_reference_id
        self.root_json_type = root_json_type
        self.root_field_names = (
            None if root_field_names is None else tuple(root_field_names)
        )
        self.missing_fields = tuple(missing_fields)
        self.error_code = error_code
        self.invalid_field_path = invalid_field_path
        self.missing_field_names = tuple(
            missing_field_names or missing_fields)
        self.unexpected_field_names = tuple(unexpected_field_names)
        self.invalid_enum_field = invalid_enum_field
        self.constraint_name = constraint_name
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
        self.response_sha256 = hashlib.sha256(
            completion.content.encode("utf-8")
        ).hexdigest()
        self.response_bytes = len(completion.content.encode("utf-8"))

    def diagnostics_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "elapsed_ms": self.elapsed_ms,
            "usage": copy.deepcopy(self.usage),
            "response_sha256": self.response_sha256,
            "response_bytes": self.response_bytes,
            "root_json_type": self.root_json_type,
            "root_field_names": (
                None if self.root_field_names is None
                else list(self.root_field_names)
            ),
            "missing_fields": list(self.missing_fields),
            "error_code": self.error_code,
            "invalid_field_path": self.invalid_field_path,
            "missing_field_names": list(self.missing_field_names),
            "unexpected_field_names": list(self.unexpected_field_names),
            "invalid_enum_field": self.invalid_enum_field,
            "constraint_name": self.constraint_name,
            "assessment_index": self.assessment_index,
            "candidate_id": self.candidate_id,
            "invalid_field": self.invalid_field,
            "invalid_reference_id": self.invalid_reference_id,
        }

    def __str__(self) -> str:
        details = [self.message]
        for key, value in self.diagnostics_dict().items():
            if value is not None:
                details.append(
                    f"{key}="
                    + json.dumps(value, ensure_ascii=False, sort_keys=True)
                )
        return "; ".join(details)


class ShadowParseError(ShadowReviewerError):
    """Raised when a response is not one strict JSON response object."""


class ShadowContractError(ShadowReviewerError):
    """Raised when parsed JSON conflicts with the shadow contract."""


class ShadowCompletionClient(Protocol):
    """Only injectable clients are accepted; no network client is created."""

    def complete(self, system_prompt: str, user_prompt: str) -> LLMCompletion:
        ...


def _json_root_type(value: Any) -> str:
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


def _safe_root_field_names(value: Mapping[str, Any]) -> tuple[str, ...]:
    """Return bounded field names without retaining response values."""

    limit = 32
    width = 80
    names = sorted(str(key)[:width] for key in value.keys())
    if len(names) > limit:
        return (*names[:limit], "<additional_fields_omitted>")
    return tuple(names)


@dataclass(frozen=True)
class ShadowReviewBatchResult:
    resume_id: str
    assessments: tuple[ShadowAbilityAssessment, ...]
    deterministic_only_candidate_ids: tuple[str, ...]
    diagnostics: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.resume_id, str) or not self.resume_id.strip():
            raise ShadowContractError("batch resume_id must be non-empty")
        assessments = tuple(self.assessments)
        ids = [item.candidate_id for item in assessments]
        if len(ids) != len(set(ids)):
            raise ShadowContractError("batch assessment candidate IDs must be unique")
        deterministic = tuple(self.deterministic_only_candidate_ids)
        if len(deterministic) != len(set(deterministic)):
            raise ShadowContractError("deterministic-only candidate IDs must be unique")
        if set(ids) & set(deterministic):
            raise ShadowContractError(
                "a candidate cannot be both model-reviewed and deterministic-only")
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
    path = DEFAULT_PROMPT_PATH if path_value is None else Path(path_value)
    if not path.is_file():
        raise ValueError(f"shadow prompt must be an existing file: {path}")
    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise ValueError(f"cannot read shadow prompt: {path}") from error
    if not content.strip():
        raise ValueError("shadow prompt must not be empty")
    return path, content, hashlib.sha256(content.encode("utf-8")).hexdigest()


def evidence_audit_sha256(audit: EvidenceAuditResult) -> str:
    """Return the stable hash binding a semantic assessment to one audit."""

    if not isinstance(audit, EvidenceAuditResult):
        raise TypeError("audit must be EvidenceAuditResult")
    return hashlib.sha256(audit.serialize().encode("utf-8")).hexdigest()


def _taxonomy_sha256(taxonomy: AbilityTaxonomyV2) -> str:
    return hashlib.sha256(taxonomy.serialize().encode("utf-8")).hexdigest()


def _strict_root(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ShadowParseError(
            "shadow response root must be a JSON object",
            root_json_type=_json_root_type(value),
            missing_fields=("assessments", "schema_version"),
        )
    required = {"schema_version", "assessments"}
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    root_fields = _safe_root_field_names(value)
    if missing:
        raise ShadowContractError(
            "shadow response is missing fields: " + ", ".join(missing),
            invalid_field=missing[0],
            root_json_type="object",
            root_field_names=root_fields,
            missing_fields=missing,
        )
    if unknown:
        raise ShadowContractError(
            "shadow response has unknown fields: " + ", ".join(unknown),
            invalid_field=unknown[0],
            root_json_type="object",
            root_field_names=root_fields,
        )
    if value["schema_version"] != SHADOW_RESPONSE_SCHEMA_VERSION:
        raise ShadowContractError(
            f"response schema_version must be {SHADOW_RESPONSE_SCHEMA_VERSION}",
            invalid_field="schema_version",
            root_json_type="object",
            root_field_names=root_fields,
        )
    if not isinstance(value["assessments"], list):
        raise ShadowContractError(
            "response assessments must be a list",
            invalid_field="assessments",
            root_json_type="object",
            root_field_names=root_fields,
        )
    return value


class AbilityShadowReviewer:
    """Batch semantic handoffs for one resume using an injected client."""

    def __init__(
        self,
        client: ShadowCompletionClient,
        taxonomy: AbilityTaxonomyV2,
        prompt_path: str | Path | None = None,
    ) -> None:
        if client is None or not callable(getattr(client, "complete", None)):
            raise TypeError("client must provide complete(system_prompt, user_prompt)")
        if not isinstance(taxonomy, AbilityTaxonomyV2):
            raise TypeError("taxonomy must be AbilityTaxonomyV2")
        self.client = client
        self.taxonomy = taxonomy
        (
            self.prompt_path,
            self.system_prompt,
            self.prompt_sha256,
        ) = _load_prompt(prompt_path)
        self.prompt_file = self.prompt_path.name
        self.taxonomy_sha256 = _taxonomy_sha256(taxonomy)

    def build_request(
        self,
        resume_id: str,
        candidates: Sequence[CandidateAbility],
        audits: Sequence[EvidenceAuditResult],
    ) -> dict[str, Any]:
        """Build a bounded semantic-only request without calling the client."""

        candidate_by_id, audit_by_id, ordered_ids = self._validate_inputs(
            resume_id, candidates, audits)
        contexts = [
            self._candidate_context(candidate_by_id[candidate_id], audit_by_id[candidate_id])
            for candidate_id in ordered_ids
            if audit_by_id[candidate_id].requires_model_review
        ]
        return {
            "schema_version": SHADOW_REQUEST_SCHEMA_VERSION,
            "resume_id": resume_id,
            "taxonomy_version": self.taxonomy.taxonomy_version,
            "review_scope": "semantic_only",
            "candidate_contexts": contexts,
        }

    def review(
        self,
        resume_id: str,
        candidates: Sequence[CandidateAbility],
        audits: Sequence[EvidenceAuditResult],
    ) -> ShadowReviewBatchResult:
        candidate_by_id, audit_by_id, ordered_ids = self._validate_inputs(
            resume_id, candidates, audits)
        model_ids = tuple(
            candidate_id for candidate_id in ordered_ids
            if audit_by_id[candidate_id].requires_model_review
        )
        deterministic_ids = tuple(
            candidate_id for candidate_id in ordered_ids
            if not audit_by_id[candidate_id].requires_model_review
        )
        payload = {
            "schema_version": SHADOW_REQUEST_SCHEMA_VERSION,
            "resume_id": resume_id,
            "taxonomy_version": self.taxonomy.taxonomy_version,
            "review_scope": "semantic_only",
            "candidate_contexts": [
                self._candidate_context(
                    candidate_by_id[candidate_id], audit_by_id[candidate_id])
                for candidate_id in model_ids
            ],
        }
        encoded_request = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if not model_ids:
            return ShadowReviewBatchResult(
                resume_id=resume_id,
                assessments=(),
                deterministic_only_candidate_ids=deterministic_ids,
                diagnostics=self._diagnostics(
                    input_count=len(ordered_ids),
                    model_count=0,
                    deterministic_count=len(deterministic_ids),
                    raw_count=0,
                    accepted_count=0,
                    request_size=len(encoded_request.encode("utf-8")),
                    completion=None,
                ),
            )
        completion = self.client.complete(self.system_prompt, encoded_request)
        if not isinstance(completion, LLMCompletion):
            raise ShadowReviewerError("client must return LLMCompletion")
        try:
            assessments, raw_count = self._parse_response(
                completion.content,
                model_ids,
                candidate_by_id,
                audit_by_id,
            )
        except (ShadowParseError, ShadowContractError) as error:
            error.attach_completion(completion)
            raise
        return ShadowReviewBatchResult(
            resume_id=resume_id,
            assessments=assessments,
            deterministic_only_candidate_ids=deterministic_ids,
            diagnostics=self._diagnostics(
                input_count=len(ordered_ids),
                model_count=len(model_ids),
                deterministic_count=len(deterministic_ids),
                raw_count=raw_count,
                accepted_count=len(assessments),
                request_size=len(encoded_request.encode("utf-8")),
                completion=completion,
            ),
        )

    def parse_response(
        self,
        content: str,
        resume_id: str,
        candidates: Sequence[CandidateAbility],
        audits: Sequence[EvidenceAuditResult],
    ) -> tuple[ShadowAbilityAssessment, ...]:
        """Strictly parse a response without invoking a client."""

        candidate_by_id, audit_by_id, ordered_ids = self._validate_inputs(
            resume_id, candidates, audits)
        model_ids = tuple(
            item for item in ordered_ids if audit_by_id[item].requires_model_review
        )
        assessments, _ = self._parse_response(
            content, model_ids, candidate_by_id, audit_by_id)
        return assessments

    def _validate_inputs(
        self,
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
                raise ShadowContractError(
                    "candidate belongs to a different resume",
                    candidate_id=candidate.candidate_id,
                    invalid_field="resume_id",
                )
            if candidate.candidate_id in candidate_by_id:
                raise ShadowContractError(
                    "duplicate candidate_id in input candidates",
                    candidate_id=candidate.candidate_id,
                    invalid_field="candidate_id",
                )
            candidate_by_id[candidate.candidate_id] = candidate
            ordered_ids.append(candidate.candidate_id)
        audit_by_id: dict[str, EvidenceAuditResult] = {}
        for index, audit in enumerate(audits):
            if not isinstance(audit, EvidenceAuditResult):
                raise TypeError(f"audits[{index}] must be EvidenceAuditResult")
            if audit.resume_id != resume_id:
                raise ShadowContractError(
                    "audit belongs to a different resume",
                    candidate_id=audit.candidate_id,
                    invalid_field="resume_id",
                )
            if audit.candidate_id in audit_by_id:
                raise ShadowContractError(
                    "duplicate candidate_id in audits",
                    candidate_id=audit.candidate_id,
                    invalid_field="candidate_id",
                )
            audit_by_id[audit.candidate_id] = audit
        missing_audits = sorted(set(candidate_by_id) - set(audit_by_id))
        unknown_audits = sorted(set(audit_by_id) - set(candidate_by_id))
        if missing_audits:
            raise ShadowContractError(
                "missing EvidenceAuditResult for candidate",
                candidate_id=missing_audits[0],
                invalid_field="audits",
            )
        if unknown_audits:
            raise ShadowContractError(
                "EvidenceAuditResult references unknown candidate",
                candidate_id=unknown_audits[0],
                invalid_field="audits",
            )
        return candidate_by_id, audit_by_id, tuple(ordered_ids)

    def _candidate_context(
        self,
        candidate: CandidateAbility,
        audit: EvidenceAuditResult,
    ) -> dict[str, Any]:
        subset = [self.taxonomy.get_node(item) for item in audit.taxonomy_subset_ids]
        audit_components = [
            self._component_constraint(item) for item in audit.component_assessments
        ]
        return {
            "candidate": {
                "candidate_id": candidate.candidate_id,
                "fact": candidate.fact,
                "behavior": candidate.behavior,
                "ability": candidate.ability,
                "normalized_ability": candidate.normalized_ability,
                "reason": candidate.reason,
                "confidence": candidate.confidence,
            },
            "current_evidence": [item.to_dict() for item in candidate.evidence],
            "evidence_audit_constraints": {
                "evidence_audit_sha256": evidence_audit_sha256(audit),
                "evidence_decision": audit.evidence_decision.value,
                "blocking_issues": list(audit.blocking_issues),
                "non_blocking_notes": list(audit.non_blocking_notes),
                "compound_label": audit.compound_label.value,
                "component_assessments": audit_components,
            },
            "taxonomy_subset": [self._taxonomy_node_for_model(item) for item in subset],
            "taxonomy_selection_trace": [
                item.to_dict() for item in audit.taxonomy_selection_trace
            ],
        }

    @staticmethod
    def _component_constraint(
        component: ComponentEvidenceAssessment,
    ) -> dict[str, Any]:
        forbidden_hits: list[str] = []
        for check in component.current_evidence_requirement_checks:
            for hit in check.forbidden_shortcut_hits:
                if hit not in forbidden_hits:
                    forbidden_hits.append(hit)
        return {
            "taxonomy_id": component.taxonomy_id,
            "deterministic_support": component.support.value,
            "missing_requirements": list(component.missing_requirements),
            "strong_qualifier_failures": list(
                component.strong_qualifier_failures),
            "forbidden_shortcut_hits": forbidden_hits,
        }

    @staticmethod
    def _taxonomy_node_for_model(node: TaxonomyNode) -> dict[str, Any]:
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
            "evidence_requirements": node.evidence_requirements.to_dict(),
            "strong_qualifiers": list(node.strong_qualifiers),
            "allowed_compounds": list(node.allowed_compounds),
            "forbidden_inferences": list(node.forbidden_inferences),
        }

    def _parse_response(
        self,
        content: str,
        expected_ids: Sequence[str],
        candidate_by_id: Mapping[str, CandidateAbility],
        audit_by_id: Mapping[str, EvidenceAuditResult],
    ) -> tuple[tuple[ShadowAbilityAssessment, ...], int]:
        if not isinstance(content, str):
            raise ShadowParseError("model content must be a string")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as error:
            raise ShadowParseError("model response must be strict JSON") from error
        root = _strict_root(parsed)
        raw_items = root["assessments"]
        expected_set = set(expected_ids)
        raw_ids: list[str] = []
        for index, item in enumerate(raw_items):
            if not isinstance(item, Mapping):
                raise ShadowContractError(
                    "assessment must be an object",
                    assessment_index=index,
                    invalid_field="assessments",
                )
            candidate_id = item.get("candidate_id")
            if not isinstance(candidate_id, str) or not candidate_id.strip():
                raise ShadowContractError(
                    "assessment candidate_id is missing or invalid",
                    assessment_index=index,
                    invalid_field="candidate_id",
                )
            raw_ids.append(candidate_id)
        duplicates = sorted({item for item in raw_ids if raw_ids.count(item) > 1})
        if duplicates:
            raise ShadowContractError(
                "duplicate assessment candidate_id",
                candidate_id=duplicates[0],
                invalid_field="candidate_id",
            )
        unknown = sorted(set(raw_ids) - expected_set)
        if unknown:
            raise ShadowContractError(
                "assessment references unknown or deterministic-only candidate",
                candidate_id=unknown[0],
                invalid_field="candidate_id",
            )
        missing = sorted(expected_set - set(raw_ids))
        if missing:
            raise ShadowContractError(
                "missing shadow assessment",
                candidate_id=missing[0],
                invalid_field="candidate_id",
            )
        if len(raw_items) != len(expected_ids):
            raise ShadowContractError(
                "assessment count does not match semantic handoff count",
                invalid_field="assessments",
            )
        by_id: dict[str, ShadowAbilityAssessment] = {}
        for index, item in enumerate(raw_items):
            candidate_id = raw_ids[index]
            audit = audit_by_id[candidate_id]
            try:
                assessment = ShadowAbilityAssessment.from_dict(
                    item,
                    taxonomy_scope=audit.taxonomy_subset_ids,
                )
            except ShadowSchemaError as error:
                raise ShadowContractError(
                    f"invalid shadow assessment: {error}",
                    assessment_index=index,
                    candidate_id=candidate_id,
                    invalid_field="assessment",
                    error_code=error.error_code,
                    invalid_field_path=error.invalid_field_path,
                    missing_field_names=error.missing_field_names,
                    unexpected_field_names=error.unexpected_field_names,
                    invalid_enum_field=error.invalid_enum_field,
                    constraint_name=error.constraint_name,
                ) from error
            self._validate_assessment_constraints(
                assessment,
                candidate_by_id[candidate_id],
                audit,
                index,
            )
            by_id[candidate_id] = assessment
        return tuple(by_id[item] for item in expected_ids), len(raw_items)

    def _validate_assessment_constraints(
        self,
        assessment: ShadowAbilityAssessment,
        candidate: CandidateAbility,
        audit: EvidenceAuditResult,
        index: int,
    ) -> None:
        def fail(message: str, field: str, reference: str | None = None) -> None:
            raise ShadowContractError(
                message,
                assessment_index=index,
                candidate_id=assessment.candidate_id,
                invalid_field=field,
                invalid_reference_id=reference,
            )

        if assessment.resume_id != candidate.resume_id:
            fail("assessment resume_id conflicts with input", "resume_id")
        if assessment.taxonomy_version != self.taxonomy.taxonomy_version:
            fail("assessment taxonomy_version conflicts with input", "taxonomy_version")
        expected_hash = evidence_audit_sha256(audit)
        if assessment.evidence_audit_sha256 != expected_hash:
            fail(
                "assessment Evidence Audit hash does not match",
                "evidence_audit_sha256",
            )
        allowed_diagnostics = {"review_scope", "constraint_acknowledged"}
        unknown_diagnostics = sorted(
            set(assessment.diagnostics) - allowed_diagnostics)
        if unknown_diagnostics:
            fail(
                "assessment diagnostics contains non-contract fields",
                "diagnostics",
            )
        if assessment.diagnostics.get("review_scope") not in {
            None, "semantic_only"
        }:
            fail("assessment diagnostics has invalid review scope", "diagnostics")
        if assessment.diagnostics.get("constraint_acknowledged") not in {
            None, True
        }:
            fail("assessment did not acknowledge deterministic constraints", "diagnostics")

        evidence_by_id = {
            item.taxonomy_id: item for item in audit.component_assessments
        }
        for component in assessment.component_assessments:
            deterministic = evidence_by_id.get(component.taxonomy_id)
            if deterministic is None:
                fail(
                    "component is absent from Evidence Auditor constraints",
                    "component_assessments.taxonomy_id",
                    component.taxonomy_id,
                )
            node = self.taxonomy.get_node(component.taxonomy_id)
            if component.canonical_name != node.canonical_name:
                fail(
                    "component canonical_name conflicts with Taxonomy",
                    "component_assessments.canonical_name",
                    component.taxonomy_id,
                )
            if component.evidence_audit_support is not deterministic.support:
                fail(
                    "component rewrites deterministic Evidence Auditor support",
                    "component_assessments.evidence_audit_support",
                    component.taxonomy_id,
                )
            expected_semantic = {
                ComponentSupport.SUPPORTED: SemanticComponentSupport.SUPPORTED,
                ComponentSupport.UNSUPPORTED: SemanticComponentSupport.UNSUPPORTED,
                ComponentSupport.PARTIALLY_SUPPORTED:
                    SemanticComponentSupport.PARTIALLY_SUPPORTED,
            }.get(deterministic.support)
            if expected_semantic is not None and component.support is not expected_semantic:
                fail(
                    "component semantic support overturns deterministic support",
                    "component_assessments.support",
                    component.taxonomy_id,
                )
            if (
                deterministic.strong_qualifier_failures
                and component.support is SemanticComponentSupport.SUPPORTED
            ):
                fail(
                    "unsupported strong qualifier was upgraded to supported",
                    "component_assessments.support",
                    component.taxonomy_id,
                )

        target_ids = tuple(audit.diagnostics.get("target_component_ids", ()))
        target_by_id = {
            item.taxonomy_id: item for item in audit.component_assessments
            if item.taxonomy_id in target_ids
        }
        target_has_unsupported = any(
            item.support is ComponentSupport.UNSUPPORTED
            or bool(item.strong_qualifier_failures)
            for item in target_by_id.values()
        )
        if target_has_unsupported and assessment.ability_validity is AbilityValidity.SUPPORTED:
            fail(
                "ability validity cannot override unsupported target evidence",
                "ability_validity",
            )
        audit_label = audit.compound_label
        output_label = assessment.compound_label
        if audit_label is CompoundAssessmentLabel.COMPOUND_SUPPORTED:
            if (
                output_label is not CompoundAssessmentLabel.COMPOUND_SUPPORTED
                or assessment.split_recommended
            ):
                fail(
                    "legal compound contradicts Evidence Auditor",
                    "compound_label",
                )
        elif audit_label is CompoundAssessmentLabel.COMPOUND_UNSUPPORTED:
            if output_label not in {
                CompoundAssessmentLabel.COMPOUND_UNSUPPORTED,
                CompoundAssessmentLabel.SPLIT_RECOMMENDED,
            }:
                fail(
                    "unsupported compound was upgraded",
                    "compound_label",
                )
        elif audit_label is CompoundAssessmentLabel.SPLIT_RECOMMENDED:
            if (
                output_label is not CompoundAssessmentLabel.SPLIT_RECOMMENDED
                or not assessment.split_recommended
            ):
                fail(
                    "deterministic split shadow label was removed",
                    "compound_label",
                )
        elif audit_label is CompoundAssessmentLabel.NOT_COMPOUND:
            if output_label is not CompoundAssessmentLabel.NOT_COMPOUND:
                fail("non-compound was changed to compound", "compound_label")
        if assessment.split_recommended:
            output_components = {
                item.taxonomy_id for item in assessment.component_assessments
            }
            if not set(assessment.suggested_atomic_taxonomy_ids).issubset(
                output_components
            ):
                fail(
                    "every suggested atomic ability needs a component assessment",
                    "suggested_atomic_taxonomy_ids",
                )
            unsupported = {
                item.taxonomy_id for item in assessment.component_assessments
                if item.support is SemanticComponentSupport.UNSUPPORTED
            }
            if unsupported & set(assessment.suggested_atomic_taxonomy_ids):
                fail(
                    "unsupported component cannot be a suggested atomic ability",
                    "suggested_atomic_taxonomy_ids",
                )

    def _diagnostics(
        self,
        *,
        input_count: int,
        model_count: int,
        deterministic_count: int,
        raw_count: int,
        accepted_count: int,
        request_size: int,
        completion: LLMCompletion | None,
    ) -> dict[str, Any]:
        return {
            "prompt_file": self.prompt_file,
            "prompt_sha256": self.prompt_sha256,
            "taxonomy_version": self.taxonomy.taxonomy_version,
            "taxonomy_sha256": self.taxonomy_sha256,
            "input_candidate_count": input_count,
            "model_review_candidate_count": model_count,
            "deterministic_only_count": deterministic_count,
            "raw_assessment_count": raw_count,
            "accepted_assessment_count": accepted_count,
            "model": None if completion is None else completion.model,
            "elapsed_ms": None if completion is None else completion.elapsed_ms,
            "usage": (
                None if completion is None or completion.usage is None
                else copy.deepcopy(completion.usage)
            ),
            "response_sha256": (
                None if completion is None
                else hashlib.sha256(completion.content.encode("utf-8")).hexdigest()
            ),
            "contract_version": SHADOW_ASSESSMENT_SCHEMA_VERSION,
            "request_size_bytes": request_size,
        }
