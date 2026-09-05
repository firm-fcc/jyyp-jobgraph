"""Formal evaluation wiring over frozen extraction and review components."""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass
from typing import Any, Sequence

from extractor.ability_taxonomy_v2 import AbilityTaxonomyV2
from extractor.agentic_llm_client import AgenticLLMError
from extractor.deterministic_evidence_auditor import DeterministicEvidenceAuditor
from extractor.evidence_extraction_agent import (
    CandidateContractError,
    EvidenceExtractionAgent,
    ExtractionParseError,
    ExtractionResult,
)
from extractor.evidence_review_agent import build_candidate_relocation_options
from extractor.final_decision_agent import (
    FinalDecisionAgent,
    FinalDecisionAgentError,
    FinalDecisionBatch,
    FinalDecisionContext,
    FinalDecisionValidationError,
    ShadowAvailability,
)
from extractor.final_decision_schema import FinalDecisionType
from extractor.review_assessment_schema import EvidenceAuditResult
from extractor.semantic_shadow_pipeline_v2 import (
    SemanticShadowPipelineError,
    SemanticShadowPipelineResultV2,
    SemanticShadowPipelineV2,
)
from extractor.semantic_shadow_reviewer_v2 import (
    SemanticShadowContractError,
    SemanticShadowParseError,
)


FORMAL_PREDICTION_SCHEMA_VERSION = "formal_ability_prediction_v1"


class FormalEvaluationError(RuntimeError):
    """Raised when a formal evaluation stage cannot produce valid records."""


class FormalStageTechnicalError(FormalEvaluationError):
    """Carry a safe stage boundary for an unrecovered technical failure."""

    def __init__(
        self,
        stage: str,
        original_error: Exception,
        *,
        candidate_ability_count: int = 0,
        contract_retry_count: int = 0,
    ) -> None:
        super().__init__(f"formal stage failed: {stage}")
        self.stage = stage
        self.original_error = original_error
        self.candidate_ability_count = candidate_ability_count
        self.contract_retry_count = contract_retry_count


@dataclass(frozen=True)
class FormalPredictionRecord:
    candidate_id: str
    source_experience_id: str
    source_candidate_ability_id: str
    ability_id: str | None
    unmapped_ability: str | None
    classification_status: str
    evidence: str
    review_required: bool
    shadow_status: str
    personal_contribution: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "candidate_id",
            "source_experience_id",
            "source_candidate_ability_id",
            "classification_status",
            "evidence",
            "shadow_status",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise FormalEvaluationError(f"{name} must be non-empty")
        if (self.ability_id is None) == (self.unmapped_ability is None):
            raise FormalEvaluationError(
                "prediction must contain exactly one of ability_id/unmapped_ability"
            )
        if not isinstance(self.review_required, bool):
            raise FormalEvaluationError("review_required must be bool")
        if self.shadow_status not in {item.value for item in ShadowAvailability}:
            raise FormalEvaluationError("shadow_status has an invalid value")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": FORMAL_PREDICTION_SCHEMA_VERSION,
            "candidate_id": self.candidate_id,
            "source_experience_id": self.source_experience_id,
            "source_candidate_ability_id": self.source_candidate_ability_id,
            "ability_id": self.ability_id,
            "unmapped_ability": self.unmapped_ability,
            "classification_status": self.classification_status,
            "evidence": self.evidence,
            "review_required": self.review_required,
            "shadow_status": self.shadow_status,
            "personal_contribution": self.personal_contribution,
        }


@dataclass(frozen=True)
class FormalExperienceResult:
    candidate_id: str
    source_experience_id: str
    extraction: ExtractionResult
    evidence_audits: tuple[EvidenceAuditResult, ...]
    shadow: SemanticShadowPipelineResultV2 | None
    final_decisions: FinalDecisionBatch
    predictions: tuple[FormalPredictionRecord, ...]
    diagnostics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_experience_id": self.source_experience_id,
            "predictions": [item.to_dict() for item in self.predictions],
            "diagnostics": copy.deepcopy(self.diagnostics),
        }


@dataclass(frozen=True)
class FormalExperienceTechnicalFailure:
    candidate_id: str
    source_experience_id: str
    stage: str
    error_type: str
    retry_count: int
    last_error_type: str
    candidate_ability_count: int
    illegal_ability_id: bool
    diagnostics: dict[str, Any]
    status: str = "technical_failure"

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_experience_id": self.source_experience_id,
            "status": self.status,
            "stage": self.stage,
            "error_type": self.error_type,
            "retry_count": self.retry_count,
            "last_error_type": self.last_error_type,
            "candidate_ability_count": self.candidate_ability_count,
            "illegal_ability_id": self.illegal_ability_id,
            "diagnostics": copy.deepcopy(self.diagnostics),
        }


class FormalEvaluationRunner:
    """Run one frozen experience without reading Gold or another experience."""

    def __init__(
        self,
        extractor: EvidenceExtractionAgent,
        taxonomy: AbilityTaxonomyV2,
        auditor: DeterministicEvidenceAuditor,
        semantic_shadow: SemanticShadowPipelineV2,
        final_agent: FinalDecisionAgent,
        *,
        formal_blind_run: bool = False,
    ) -> None:
        if not isinstance(extractor, EvidenceExtractionAgent):
            raise TypeError("extractor must be EvidenceExtractionAgent")
        if not isinstance(taxonomy, AbilityTaxonomyV2):
            raise TypeError("taxonomy must be AbilityTaxonomyV2")
        if not isinstance(auditor, DeterministicEvidenceAuditor):
            raise TypeError("auditor must be DeterministicEvidenceAuditor")
        if not isinstance(semantic_shadow, SemanticShadowPipelineV2):
            raise TypeError("semantic_shadow must be SemanticShadowPipelineV2")
        if not isinstance(final_agent, FinalDecisionAgent):
            raise TypeError("final_agent must be FinalDecisionAgent")
        if not isinstance(formal_blind_run, bool):
            raise TypeError("formal_blind_run must be bool")
        versions = {
            taxonomy.taxonomy_version,
            auditor.taxonomy.taxonomy_version,
            semantic_shadow.taxonomy.taxonomy_version,
            final_agent.taxonomy.taxonomy_version,
        }
        if len(versions) != 1:
            raise ValueError("all formal evaluation components must share taxonomy")
        self.extractor = extractor
        self.taxonomy = taxonomy
        self.auditor = auditor
        self.semantic_shadow = semantic_shadow
        self.final_agent = final_agent
        self.formal_blind_run = formal_blind_run

    def _technical_retry_snapshot(self) -> dict[str, Any]:
        clients = (
            self.extractor.client,
            self.semantic_shadow.reviewer.client,
            self.final_agent.client,
        )
        api_response = 0
        timeout = 0
        transport_attempts = 0
        retry_after_honored = 0
        nonretryable_http = 0
        http_statuses: Counter[str] = Counter()
        provider_types: Counter[str] = Counter()
        provider_codes: Counter[str] = Counter()
        last_error_type = None
        last_http_error = None
        seen: set[int] = set()
        for client in clients:
            identity = id(client)
            if identity in seen:
                continue
            seen.add(identity)
            getter = getattr(client, "retry_diagnostics", None)
            if not callable(getter):
                continue
            snapshot = getter()
            if hasattr(snapshot, "to_dict"):
                values = snapshot.to_dict()
            elif isinstance(snapshot, dict):
                values = snapshot
            else:
                continue
            api_response += int(values.get("api_response_retry_count", 0))
            timeout += int(values.get("timeout_retry_count", 0))
            transport_attempts += int(values.get("transport_attempt_count", 0))
            retry_after_honored += int(
                values.get("retry_after_honored_count", 0)
            )
            nonretryable_http += int(values.get("nonretryable_http_count", 0))
            http_statuses.update(values.get("http_status_counts", {}))
            provider_types.update(values.get("provider_error_type_counts", {}))
            provider_codes.update(values.get("provider_error_code_counts", {}))
            if values.get("last_error_type"):
                last_error_type = str(values["last_error_type"])
            if values.get("last_http_error"):
                last_http_error = copy.deepcopy(values["last_http_error"])
        return {
            "api_response_retry_count": api_response,
            "timeout_retry_count": timeout,
            "transport_attempt_count": transport_attempts,
            "retry_after_honored_count": retry_after_honored,
            "nonretryable_http_count": nonretryable_http,
            "http_status_counts": dict(http_statuses),
            "provider_error_type_counts": dict(provider_types),
            "provider_error_code_counts": dict(provider_codes),
            "last_error_type": last_error_type,
            "last_http_error": last_http_error,
        }

    @staticmethod
    def _retry_delta(
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> dict[str, Any]:
        def count_delta(name: str) -> dict[str, int]:
            before_counts = Counter(before.get(name, {}))
            after_counts = Counter(after.get(name, {}))
            return {
                key: count
                for key, count in (after_counts - before_counts).items()
                if count
            }

        return {
            "api_response_retry_count": (
                after["api_response_retry_count"]
                - before["api_response_retry_count"]
            ),
            "timeout_retry_count": (
                after["timeout_retry_count"] - before["timeout_retry_count"]
            ),
            "transport_attempt_count": (
                after["transport_attempt_count"]
                - before["transport_attempt_count"]
            ),
            "retry_after_honored_count": (
                after["retry_after_honored_count"]
                - before["retry_after_honored_count"]
            ),
            "nonretryable_http_count": (
                after["nonretryable_http_count"]
                - before["nonretryable_http_count"]
            ),
            "http_status_counts": count_delta("http_status_counts"),
            "provider_error_type_counts": count_delta(
                "provider_error_type_counts"
            ),
            "provider_error_code_counts": count_delta(
                "provider_error_code_counts"
            ),
            "last_error_type": after.get("last_error_type"),
            "last_http_error": copy.deepcopy(after.get("last_http_error")),
        }

    def run_experience_safe(
        self,
        candidate_id: str,
        source_experience_id: str,
        source_text: str,
    ) -> FormalExperienceResult | FormalExperienceTechnicalFailure:
        before = self._technical_retry_snapshot()
        try:
            return self.run_experience(
                candidate_id,
                source_experience_id,
                source_text,
            )
        except FormalStageTechnicalError as error:
            retry_delta = self._retry_delta(
                before,
                self._technical_retry_snapshot(),
            )
            original = error.original_error
            technical_retries = (
                retry_delta["api_response_retry_count"]
                + retry_delta["timeout_retry_count"]
            )
            illegal_ability_id = (
                isinstance(original, FinalDecisionAgentError)
                and isinstance(original.__cause__, FinalDecisionValidationError)
                and "ability_id" in str(original.__cause__)
            )
            return FormalExperienceTechnicalFailure(
                candidate_id=candidate_id,
                source_experience_id=source_experience_id,
                stage=error.stage,
                error_type=type(original).__name__,
                retry_count=technical_retries + error.contract_retry_count,
                last_error_type=(
                    retry_delta["last_error_type"] or type(original).__name__
                ),
                candidate_ability_count=error.candidate_ability_count,
                illegal_ability_id=illegal_ability_id,
                diagnostics={
                    "technical_retry_counts": retry_delta,
                    "contract_retry_count": error.contract_retry_count,
                    "unrecovered_technical_failure": True,
                    "formal_blind_run": self.formal_blind_run,
                    "gold_read": False,
                    "controller_executed": False,
                },
            )

    def run_experience(
        self,
        candidate_id: str,
        source_experience_id: str,
        source_text: str,
    ) -> FormalExperienceResult:
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise ValueError("candidate_id must be non-empty")
        if not isinstance(source_experience_id, str) or not source_experience_id.strip():
            raise ValueError("source_experience_id must be non-empty")
        if not isinstance(source_text, str) or not source_text.strip():
            raise ValueError("source_text must be non-empty")

        retry_before = self._technical_retry_snapshot()
        extraction_contract_retry_count = 0
        try:
            extraction = self.extractor.extract(
                resume_id=candidate_id,
                resume_text=source_text,
                project_id=source_experience_id,
            )
        except (ExtractionParseError, CandidateContractError):
            extraction_contract_retry_count = 1
            try:
                extraction = self.extractor.extract(
                    resume_id=candidate_id,
                    resume_text=source_text,
                    project_id=source_experience_id,
                )
            except (AgenticLLMError, ExtractionParseError, CandidateContractError) as error:
                raise FormalStageTechnicalError(
                    "extraction",
                    error,
                    contract_retry_count=extraction_contract_retry_count,
                ) from error
        except AgenticLLMError as error:
            raise FormalStageTechnicalError("extraction", error) from error
        audits: list[EvidenceAuditResult] = []
        relocation_options: dict[str, Sequence[Any]] = {}
        relocation_failures = 0
        for candidate in extraction.candidates:
            subset = self.taxonomy.select_relevant_nodes(
                candidate.ability,
                candidate.fact,
                candidate.behavior,
                [item.text for item in candidate.evidence],
                max_nodes=12,
            )
            try:
                options = build_candidate_relocation_options(
                    source_text, candidate
                )
                scoped_options = {candidate.candidate_id: options}
                relocation_options[candidate.candidate_id] = options
            except ValueError:
                scoped_options = {}
                relocation_options[candidate.candidate_id] = ()
                relocation_failures += 1
            audits.append(
                self.auditor.audit(
                    candidate_id,
                    source_text,
                    candidate,
                    subset,
                    scoped_options,
                )
            )

        semantic_schema_retry_count = 0
        shadow_status = ShadowAvailability.AVAILABLE
        shadow_failure: dict[str, Any] | None = None
        shadow_contract_errors = (
            SemanticShadowParseError,
            SemanticShadowContractError,
            SemanticShadowPipelineError,
        )
        try:
            shadow = self.semantic_shadow.run(
                candidate_id,
                source_text,
                extraction.candidates,
                audits,
                relocation_options_by_candidate_id=relocation_options,
            )
        except shadow_contract_errors:
            semantic_schema_retry_count = 1
            try:
                shadow = self.semantic_shadow.run(
                    candidate_id,
                    source_text,
                    extraction.candidates,
                    audits,
                    relocation_options_by_candidate_id=relocation_options,
                )
            except shadow_contract_errors as error:
                shadow = None
                shadow_status = ShadowAvailability.UNAVAILABLE_INVALID_CONTRACT
                shadow_failure = {
                    "error_type": type(error).__name__,
                    "error_code": getattr(error, "error_code", None),
                    "candidate_id": getattr(error, "candidate_id", None),
                    "invalid_field_path": getattr(error, "invalid_field_path", None),
                    "constraint_name": getattr(error, "constraint_name", None),
                    "response_sha256": getattr(error, "response_sha256", None),
                }
            except AgenticLLMError as error:
                raise FormalStageTechnicalError(
                    "semantic_shadow",
                    error,
                    candidate_ability_count=len(extraction.candidates),
                    contract_retry_count=semantic_schema_retry_count,
                ) from error
        except AgenticLLMError as error:
            raise FormalStageTechnicalError(
                "semantic_shadow",
                error,
                candidate_ability_count=len(extraction.candidates),
            ) from error
        audit_by_id = {item.candidate_id: item for item in audits}
        bundle_by_id = (
            {}
            if shadow is None
            else {item.candidate_id: item for item in shadow.bundles}
        )
        contexts: list[FinalDecisionContext] = []
        for candidate in extraction.candidates:
            audit = audit_by_id[candidate.candidate_id]
            contexts.append(
                FinalDecisionContext(
                    candidate=candidate,
                    frozen_experience_text=source_text,
                    taxonomy_candidates=tuple(
                        self.taxonomy.get_node(item)
                        for item in audit.taxonomy_subset_ids
                    ),
                    evidence_audit=audit,
                    shadow_status=shadow_status,
                    shadow_bundle=bundle_by_id.get(candidate.candidate_id),
                )
            )
        try:
            final_batch = self.final_agent.decide(
                candidate_id,
                source_experience_id,
                contexts,
            )
        except AgenticLLMError as error:
            raise FormalStageTechnicalError(
                "final_decision",
                error,
                candidate_ability_count=len(extraction.candidates),
            ) from error
        except FinalDecisionAgentError as error:
            raise FormalStageTechnicalError(
                "final_decision",
                error,
                candidate_ability_count=len(extraction.candidates),
                contract_retry_count=error.contract_retry_count,
            ) from error
        predictions: list[FormalPredictionRecord] = []
        decision_counts: Counter[str] = Counter()
        status_counts: Counter[str] = Counter()
        for decision in final_batch.decisions:
            decision_counts[decision.decision.value] += 1
            for atom in decision.atomic_records():
                status_counts[atom.classification_status.value] += 1
                if atom.decision is FinalDecisionType.REJECT:
                    continue
                predictions.append(
                    FormalPredictionRecord(
                        candidate_id=candidate_id,
                        source_experience_id=source_experience_id,
                        source_candidate_ability_id=decision.candidate_id,
                        ability_id=atom.ability_id,
                        unmapped_ability=atom.unmapped_ability,
                        classification_status=atom.classification_status.value,
                        evidence=atom.evidence,
                        review_required=atom.review_required,
                        shadow_status=shadow_status.value,
                        personal_contribution=None,
                    )
                )
        retry_delta = self._retry_delta(
            retry_before,
            self._technical_retry_snapshot(),
        )
        return FormalExperienceResult(
            candidate_id=candidate_id,
            source_experience_id=source_experience_id,
            extraction=extraction,
            evidence_audits=tuple(audits),
            shadow=shadow,
            final_decisions=final_batch,
            predictions=tuple(predictions),
            diagnostics={
                "experience_count": 1,
                "candidate_ability_count": len(extraction.candidates),
                "prediction_record_count": len(predictions),
                "decision_counts": dict(decision_counts),
                "classification_status_counts": dict(status_counts),
                "invalid_schema_count": 0,
                "evidence_relocation_failure_count": relocation_failures,
                "semantic_schema_retry_count": semantic_schema_retry_count,
                "extraction_contract_retry_count": extraction_contract_retry_count,
                "final_contract_retry_count": final_batch.diagnostics[
                    "contract_retry_count"
                ],
                "technical_retry_counts": retry_delta,
                "shadow_status": shadow_status.value,
                "shadow_failure": copy.deepcopy(shadow_failure),
                "source_experience_id_attached_by_runner": True,
                "gold_read": False,
                "formal_blind_run": self.formal_blind_run,
                "controller_executed": False,
            },
        )
