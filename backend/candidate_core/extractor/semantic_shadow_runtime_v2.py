"""Stage-aware, safe runtime diagnostics for the semantic Shadow v2 chain.

The runtime owns no business policy and creates no network client.  Callers
inject the v2 Reviewer, optional acceptance callbacks, and an optional output
serializer.  Every local failure is classified without retaining model text,
resume text, Prompt text, Taxonomy content, or credentials.
"""

from __future__ import annotations

import copy
import hashlib
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from extractor.ability_shadow_schema import ShadowAbilityAssessment, ShadowSchemaError
from extractor.ability_taxonomy_v2 import AbilityTaxonomyV2
from extractor.agentic_schema import CandidateAbility
from extractor.review_assessment_schema import EvidenceAuditResult
from extractor.semantic_shadow_assembler import SemanticShadowAssessmentAssembler
from extractor.semantic_shadow_reviewer_v2 import (
    SemanticShadowContractError,
    SemanticShadowParseError,
    SemanticShadowReviewBatchV2,
    SemanticShadowReviewerError,
    SemanticShadowReviewerV2,
)
from extractor.semantic_shadow_schema_v2 import SemanticShadowAssessment
from extractor.shadow_review_bundle import DecisionSource, ShadowReviewBundle
from extractor.shadow_review_mapper import (
    DeterministicShadowReviewMapper,
    ShadowMappingInputError,
)


SEMANTIC_SHADOW_RUNTIME_VERSION = "semantic_shadow_runtime_v2"


class RuntimeStage(str, Enum):
    RESPONSE_PARSE = "response_parse"
    SEMANTIC_CONTRACT_VALIDATION = "semantic_contract_validation"
    SEMANTIC_ACCEPTANCE = "semantic_acceptance"
    ASSEMBLER = "assembler"
    MAPPER = "mapper"
    FINAL_ACCEPTANCE = "final_acceptance"
    OUTPUT_SERIALIZATION = "output_serialization"


class RuntimeCheckFailure(ValueError):
    """Explicit non-sensitive failure raised by injected acceptance checks."""

    def __init__(
        self,
        *,
        error_code: str,
        candidate_id: str | None = None,
        invalid_field_path: str | None = None,
        constraint_name: str | None = None,
    ) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.candidate_id = candidate_id
        self.invalid_field_path = invalid_field_path
        self.constraint_name = constraint_name


class SemanticShadowRuntimeError(RuntimeError):
    """One safely classified local v2 runtime failure."""

    def __init__(
        self,
        *,
        stage: RuntimeStage,
        error_code: str,
        candidate_id: str | None,
        invalid_field_path: str | None,
        constraint_name: str | None,
        original_exception_type: str,
        prompt_sha256: str,
        response_sha256: str | None,
        semantic_snapshot: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"semantic shadow runtime failed at {stage.value}")
        self.stage = stage
        self.error_code = error_code
        self.candidate_id = candidate_id
        self.invalid_field_path = invalid_field_path
        self.constraint_name = constraint_name
        self.original_exception_type = original_exception_type
        self.prompt_sha256 = prompt_sha256
        self.response_sha256 = response_sha256
        self.semantic_snapshot = (
            None if semantic_snapshot is None
            else copy.deepcopy(dict(semantic_snapshot))
        )

    def diagnostics_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "error_code": self.error_code,
            "candidate_id": self.candidate_id,
            "invalid_field_path": self.invalid_field_path,
            "constraint_name": self.constraint_name,
            "original_exception_type": self.original_exception_type,
            "prompt_sha256": self.prompt_sha256,
            "response_sha256": self.response_sha256,
            "semantic_snapshot": copy.deepcopy(self.semantic_snapshot),
        }


def safe_semantic_snapshot(
    assessment: SemanticShadowAssessment,
) -> dict[str, Any]:
    """Return structural semantic fields without retaining the reason text."""

    if not isinstance(assessment, SemanticShadowAssessment):
        raise TypeError("assessment must be SemanticShadowAssessment")
    confidence = assessment.confidence
    confidence_type_valid = (
        not isinstance(confidence, bool)
        and isinstance(confidence, (int, float))
    )
    confidence_range_valid = (
        confidence_type_valid
        and math.isfinite(float(confidence))
        and 0.0 <= float(confidence) <= 1.0
    )
    reason_bytes = assessment.reason.encode("utf-8")
    return {
        "ability_validity": assessment.ability_validity.value,
        "preferred_taxonomy_id": assessment.preferred_taxonomy_id,
        "representation_labels": [
            item.value for item in assessment.representation_labels],
        "split_recommended": assessment.split_recommended,
        "suggested_atomic_taxonomy_ids": list(
            assessment.suggested_atomic_taxonomy_ids),
        "confidence_type_valid": confidence_type_valid,
        "confidence_range_valid": confidence_range_valid,
        "reason_length": len(assessment.reason),
        "reason_sha256": hashlib.sha256(reason_bytes).hexdigest(),
    }


@dataclass(frozen=True)
class SemanticShadowRuntimeResultV2:
    resume_id: str
    semantic_batch: SemanticShadowReviewBatchV2
    assembled_assessments: tuple[ShadowAbilityAssessment, ...]
    bundles: tuple[ShadowReviewBundle, ...]
    diagnostics: dict[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "assembled_assessments", tuple(
            self.assembled_assessments))
        object.__setattr__(self, "bundles", tuple(self.bundles))
        object.__setattr__(self, "diagnostics", copy.deepcopy(self.diagnostics))

    def to_dict(self) -> dict[str, Any]:
        return {
            "resume_id": self.resume_id,
            "semantic_batch": self.semantic_batch.to_dict(),
            "assembled_assessments": [
                item.to_dict() for item in self.assembled_assessments],
            "bundles": [item.to_dict() for item in self.bundles],
            "diagnostics": copy.deepcopy(self.diagnostics),
        }


SemanticAcceptance = Callable[[tuple[SemanticShadowAssessment, ...]], None]
FinalAcceptance = Callable[[SemanticShadowRuntimeResultV2], None]
OutputSerializer = Callable[[SemanticShadowRuntimeResultV2], Any]


class SemanticShadowRuntimeV2:
    """Execute the local v2 stages with explicit failure classification."""

    def __init__(
        self,
        reviewer: SemanticShadowReviewerV2,
        taxonomy: AbilityTaxonomyV2,
        *,
        mapper: DeterministicShadowReviewMapper | None = None,
    ) -> None:
        if not isinstance(reviewer, SemanticShadowReviewerV2):
            raise TypeError("reviewer must be SemanticShadowReviewerV2")
        if not isinstance(taxonomy, AbilityTaxonomyV2):
            raise TypeError("taxonomy must be AbilityTaxonomyV2")
        self.reviewer = reviewer
        self.taxonomy = taxonomy
        self.assembler = SemanticShadowAssessmentAssembler(taxonomy)
        self.mapper = mapper or DeterministicShadowReviewMapper(taxonomy)
        if not isinstance(self.mapper, DeterministicShadowReviewMapper):
            raise TypeError("mapper must be DeterministicShadowReviewMapper")

    def execute(
        self,
        resume_id: str,
        resume_text: str,
        candidates: Sequence[CandidateAbility],
        audits: Sequence[EvidenceAuditResult],
        *,
        relocation_options_by_candidate_id: Mapping[str, Sequence[Any]] | None = None,
        merge_targets: Mapping[str, str] | None = None,
        semantic_acceptance: SemanticAcceptance | None = None,
        final_acceptance: FinalAcceptance | None = None,
        output_serializer: OutputSerializer | None = None,
    ) -> SemanticShadowRuntimeResultV2:
        try:
            semantic_batch = self.reviewer.review(
                resume_id, candidates, audits)
        except SemanticShadowParseError as error:
            raise self._wrap(
                RuntimeStage.RESPONSE_PARSE, error,
                response_sha256=error.response_sha256) from error
        except SemanticShadowContractError as error:
            raise self._wrap(
                RuntimeStage.SEMANTIC_CONTRACT_VALIDATION, error,
                response_sha256=error.response_sha256) from error
        except SemanticShadowReviewerError as error:
            raise self._wrap(
                RuntimeStage.SEMANTIC_CONTRACT_VALIDATION, error,
                response_sha256=error.response_sha256) from error

        response_sha256 = semantic_batch.diagnostics.get("response_sha256")
        snapshot = self._first_snapshot(semantic_batch.assessments)
        if semantic_acceptance is not None:
            try:
                semantic_acceptance(semantic_batch.assessments)
            except Exception as error:
                raise self._wrap(
                    RuntimeStage.SEMANTIC_ACCEPTANCE,
                    error,
                    response_sha256=response_sha256,
                    semantic_snapshot=snapshot,
                ) from error

        candidate_by_id = {item.candidate_id: item for item in candidates}
        audit_by_id = {item.candidate_id: item for item in audits}
        assembled: list[ShadowAbilityAssessment] = []
        for semantic in semantic_batch.assessments:
            try:
                assembled.append(self.assembler.assemble(
                    candidate_by_id[semantic.candidate_id],
                    audit_by_id[semantic.candidate_id],
                    semantic,
                ))
            except Exception as error:
                raise self._wrap(
                    RuntimeStage.ASSEMBLER,
                    error,
                    candidate_id=semantic.candidate_id,
                    response_sha256=response_sha256,
                    semantic_snapshot=safe_semantic_snapshot(semantic),
                ) from error

        try:
            bundles = self.mapper.map_all(
                resume_id,
                resume_text,
                candidates,
                audits,
                assembled,
                relocation_options_by_candidate_id=(
                    relocation_options_by_candidate_id),
                merge_targets=merge_targets,
            )
        except Exception as error:
            raise self._wrap(
                RuntimeStage.MAPPER,
                error,
                response_sha256=response_sha256,
                semantic_snapshot=snapshot,
            ) from error

        mapping_blocked = sum(
            item.decision_source is DecisionSource.MAPPING_BLOCKED
            for item in bundles)
        result = SemanticShadowRuntimeResultV2(
            resume_id=resume_id,
            semantic_batch=semantic_batch,
            assembled_assessments=tuple(assembled),
            bundles=bundles,
            diagnostics={
                "runtime_version": SEMANTIC_SHADOW_RUNTIME_VERSION,
                "input_candidate_count": len(candidates),
                "deterministic_only_count": len(
                    semantic_batch.deterministic_only_candidate_ids),
                "model_review_count": len(semantic_batch.assessments),
                "assembler_success_count": len(assembled),
                "mapping_success_count": len(bundles) - mapping_blocked,
                "mapping_blocked_count": mapping_blocked,
                "controller_executed": False,
                "split_executed": False,
                "candidate_modified": False,
            },
        )
        if final_acceptance is not None:
            try:
                final_acceptance(result)
            except Exception as error:
                raise self._wrap(
                    RuntimeStage.FINAL_ACCEPTANCE,
                    error,
                    response_sha256=response_sha256,
                    semantic_snapshot=snapshot,
                ) from error
        if output_serializer is not None:
            try:
                output_serializer(result)
            except Exception as error:
                raise self._wrap(
                    RuntimeStage.OUTPUT_SERIALIZATION,
                    error,
                    response_sha256=response_sha256,
                    semantic_snapshot=snapshot,
                ) from error
        return result

    @staticmethod
    def _first_snapshot(
        assessments: Sequence[SemanticShadowAssessment],
    ) -> dict[str, Any] | None:
        if not assessments:
            return None
        return safe_semantic_snapshot(assessments[0])

    def _wrap(
        self,
        stage: RuntimeStage,
        error: Exception,
        *,
        candidate_id: str | None = None,
        response_sha256: str | None = None,
        semantic_snapshot: Mapping[str, Any] | None = None,
    ) -> SemanticShadowRuntimeError:
        if isinstance(error, SemanticShadowRuntimeError):
            return error
        error_code = getattr(error, "error_code", None) or self._error_code(error)
        return SemanticShadowRuntimeError(
            stage=stage,
            error_code=error_code,
            candidate_id=(candidate_id or getattr(error, "candidate_id", None)),
            invalid_field_path=getattr(error, "invalid_field_path", None),
            constraint_name=(
                getattr(error, "constraint_name", None)
                or self._default_constraint(stage, error)
            ),
            original_exception_type=type(error).__name__,
            prompt_sha256=self.reviewer.prompt_sha256,
            response_sha256=(
                response_sha256 or getattr(error, "response_sha256", None)),
            semantic_snapshot=semantic_snapshot,
        )

    @staticmethod
    def _error_code(error: Exception) -> str:
        if isinstance(error, RuntimeCheckFailure):
            return error.error_code
        if isinstance(error, ShadowSchemaError):
            return error.error_code
        if isinstance(error, ShadowMappingInputError):
            return "mapper_input_error"
        if isinstance(error, OSError):
            return "output_io_error"
        if isinstance(error, RuntimeError):
            return "runtime_error"
        return "unexpected_local_error"

    @staticmethod
    def _default_constraint(stage: RuntimeStage, error: Exception) -> str:
        if type(error) is RuntimeError:
            return "unclassified_runtime_assertion"
        return f"{stage.value}_failed"
