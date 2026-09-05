"""Offline wiring for semantic Shadow v2 through the existing Shadow Mapper."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from extractor.ability_shadow_schema import ShadowAbilityAssessment, ShadowSchemaError
from extractor.ability_taxonomy_v2 import AbilityTaxonomyV2
from extractor.agentic_schema import CandidateAbility
from extractor.review_assessment_schema import EvidenceAuditResult
from extractor.semantic_shadow_assembler import SemanticShadowAssessmentAssembler
from extractor.semantic_shadow_reviewer_v2 import (
    SemanticShadowReviewBatchV2,
    SemanticShadowReviewerV2,
)
from extractor.shadow_review_bundle import (
    DecisionSource,
    ShadowReviewBundle,
)
from extractor.shadow_review_mapper import DeterministicShadowReviewMapper


SEMANTIC_SHADOW_PIPELINE_VERSION = "semantic_shadow_pipeline_v2"


class SemanticShadowPipelineError(RuntimeError):
    """Raised before mapping when v2 semantics cannot be assembled safely."""

    def __init__(
        self,
        message: str,
        *,
        candidate_id: str | None = None,
        constraint_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.candidate_id = candidate_id
        self.constraint_name = constraint_name


@dataclass(frozen=True)
class SemanticShadowPipelineResultV2:
    resume_id: str
    semantic_batch: SemanticShadowReviewBatchV2
    assembled_assessments: tuple[ShadowAbilityAssessment, ...]
    bundles: tuple[ShadowReviewBundle, ...]
    diagnostics: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.resume_id, str) or not self.resume_id.strip():
            raise SemanticShadowPipelineError("resume_id must be non-empty")
        if not isinstance(self.semantic_batch, SemanticShadowReviewBatchV2):
            raise TypeError("semantic_batch must be SemanticShadowReviewBatchV2")
        assessments = tuple(self.assembled_assessments)
        bundles = tuple(self.bundles)
        if any(not isinstance(item, ShadowAbilityAssessment) for item in assessments):
            raise TypeError("assembled_assessments must contain v1 assessments")
        if any(not isinstance(item, ShadowReviewBundle) for item in bundles):
            raise TypeError("bundles must contain ShadowReviewBundle")
        object.__setattr__(self, "assembled_assessments", assessments)
        object.__setattr__(self, "bundles", bundles)
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


class SemanticShadowPipelineV2:
    """Assemble v2 semantics, then perform shadow-only deterministic mapping."""

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
        if reviewer.taxonomy.taxonomy_version != taxonomy.taxonomy_version:
            raise ValueError("reviewer and pipeline taxonomy versions must match")
        self.reviewer = reviewer
        self.taxonomy = taxonomy
        self.assembler = SemanticShadowAssessmentAssembler(taxonomy)
        self.mapper = (
            DeterministicShadowReviewMapper(taxonomy)
            if mapper is None else mapper
        )
        if not isinstance(self.mapper, DeterministicShadowReviewMapper):
            raise TypeError("mapper must be DeterministicShadowReviewMapper")

    def run(
        self,
        resume_id: str,
        resume_text: str,
        candidates: Sequence[CandidateAbility],
        audits: Sequence[EvidenceAuditResult],
        *,
        relocation_options_by_candidate_id: Mapping[str, Sequence[Any]] | None = None,
        merge_targets: Mapping[str, str] | None = None,
    ) -> SemanticShadowPipelineResultV2:
        semantic_batch = self.reviewer.review(resume_id, candidates, audits)
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
            except ShadowSchemaError as error:
                raise SemanticShadowPipelineError(
                    "semantic assessment conflicts with deterministic constraints",
                    candidate_id=semantic.candidate_id,
                    constraint_name=error.constraint_name,
                ) from error

        bundles = self.mapper.map_all(
            resume_id,
            resume_text,
            candidates,
            audits,
            assembled,
            relocation_options_by_candidate_id=relocation_options_by_candidate_id,
            merge_targets=merge_targets,
        )
        mapping_blocked = sum(
            item.decision_source is DecisionSource.MAPPING_BLOCKED
            for item in bundles)
        return SemanticShadowPipelineResultV2(
            resume_id=resume_id,
            semantic_batch=semantic_batch,
            assembled_assessments=tuple(assembled),
            bundles=bundles,
            diagnostics={
                "pipeline_version": SEMANTIC_SHADOW_PIPELINE_VERSION,
                "input_candidate_count": len(candidates),
                "deterministic_only_count": len(
                    semantic_batch.deterministic_only_candidate_ids),
                "v2_model_review_count": len(semantic_batch.assessments),
                "v2_assessment_count": len(semantic_batch.assessments),
                "assembler_success_count": len(assembled),
                "assembler_blocked_count": 0,
                "mapped_success_count": len(bundles) - mapping_blocked,
                "mapping_blocked_count": mapping_blocked,
                "contract_violation_count": 0,
                "controller_executed": False,
                "candidate_modified": False,
                "split_executed": False,
            },
        )
