"""Deterministically assemble minimal v2 semantics into the frozen v1 shape."""

from __future__ import annotations

from typing import Iterable

from extractor.ability_shadow_reviewer import evidence_audit_sha256
from extractor.ability_shadow_schema import (
    SHADOW_ASSESSMENT_SCHEMA_VERSION,
    AbilityValidity,
    SemanticComponentSupport,
    ShadowAbilityAssessment,
    ShadowComponentAssessment,
    ShadowSchemaError,
)
from extractor.ability_taxonomy_v2 import AbilityTaxonomyV2
from extractor.agentic_schema import CandidateAbility
from extractor.review_assessment_schema import (
    ComponentEvidenceAssessment,
    ComponentSupport,
    EvidenceAuditResult,
    RequirementSupport,
)
from extractor.semantic_shadow_schema_v2 import SemanticShadowAssessment


SEMANTIC_SHADOW_ASSEMBLER_VERSION = "semantic_shadow_assembler_v1"


_SUPPORT_MAP = {
    ComponentSupport.SUPPORTED: SemanticComponentSupport.SUPPORTED,
    ComponentSupport.UNSUPPORTED: SemanticComponentSupport.UNSUPPORTED,
    ComponentSupport.PARTIALLY_SUPPORTED: SemanticComponentSupport.PARTIALLY_SUPPORTED,
}


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return tuple(result)


def _component_requirements(
    component: ComponentEvidenceAssessment,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    missing = list(component.missing_requirements)
    satisfied: list[str] = []
    for check in component.current_evidence_requirement_checks:
        if check.status in {RequirementSupport.MET, RequirementSupport.PARTIALLY_MET}:
            satisfied.extend(check.matched_texts)
            if not check.matched_texts and check.requirement_description not in missing:
                satisfied.append(check.requirement_description)
    if (
        component.support is ComponentSupport.PARTIALLY_SUPPORTED
        and not satisfied
    ):
        satisfied.append("deterministic partial support")
    return _ordered_unique(missing), _ordered_unique(satisfied)


class SemanticShadowAssessmentAssembler:
    """Combine model-only semantic choices with immutable Auditor facts."""

    def __init__(self, taxonomy: AbilityTaxonomyV2) -> None:
        if not isinstance(taxonomy, AbilityTaxonomyV2):
            raise TypeError("taxonomy must be AbilityTaxonomyV2")
        self.taxonomy = taxonomy

    def assemble(
        self,
        candidate: CandidateAbility,
        audit: EvidenceAuditResult,
        semantic: SemanticShadowAssessment,
    ) -> ShadowAbilityAssessment:
        if not isinstance(candidate, CandidateAbility):
            raise TypeError("candidate must be CandidateAbility")
        if not isinstance(audit, EvidenceAuditResult):
            raise TypeError("audit must be EvidenceAuditResult")
        if not isinstance(semantic, SemanticShadowAssessment):
            raise TypeError("semantic must be SemanticShadowAssessment")
        if candidate.candidate_id != audit.candidate_id:
            self._conflict("candidate_id", "candidate_audit_identity")
        if semantic.candidate_id != candidate.candidate_id:
            self._conflict("candidate_id", "semantic_candidate_identity")
        if candidate.resume_id != audit.resume_id:
            self._conflict("resume_id", "candidate_audit_resume")
        if not audit.requires_model_review:
            self._conflict("candidate_id", "semantic_handoff_required")

        scope = tuple(audit.taxonomy_subset_ids)
        scope_set = set(scope)
        if semantic.preferred_taxonomy_id is not None:
            if semantic.preferred_taxonomy_id not in scope_set:
                self._conflict(
                    "preferred_taxonomy_id", "semantic_taxonomy_scope")
        if not set(semantic.suggested_atomic_taxonomy_ids).issubset(scope_set):
            self._conflict(
                "suggested_atomic_taxonomy_ids", "semantic_taxonomy_scope")

        audit_by_id = {
            item.taxonomy_id: item for item in audit.component_assessments
        }
        target_ids = tuple(audit.diagnostics.get("target_component_ids", ()))
        relevant = set(target_ids)
        if semantic.preferred_taxonomy_id is not None:
            relevant.add(semantic.preferred_taxonomy_id)
        relevant.update(semantic.suggested_atomic_taxonomy_ids)
        ordered_component_ids = tuple(
            taxonomy_id for taxonomy_id in scope if taxonomy_id in relevant)
        if not ordered_component_ids:
            self._conflict("preferred_taxonomy_id", "component_selection_required")

        components = []
        for taxonomy_id in ordered_component_ids:
            deterministic = audit_by_id.get(taxonomy_id)
            if deterministic is None:
                self._conflict(
                    "suggested_atomic_taxonomy_ids",
                    "deterministic_component_required",
                )
            if deterministic.support is ComponentSupport.AMBIGUOUS:
                self._conflict(
                    "component_assessments",
                    "ambiguous_component_requires_explicit_future_contract",
                )
            missing, satisfied = _component_requirements(deterministic)
            components.append(ShadowComponentAssessment(
                taxonomy_id=taxonomy_id,
                canonical_name=self.taxonomy.get_node(taxonomy_id).canonical_name,
                support=_SUPPORT_MAP[deterministic.support],
                evidence_audit_support=deterministic.support,
                missing_requirements=missing,
                satisfied_requirements=satisfied,
                semantic_reason=(
                    "Component support and requirements were assembled from "
                    "the deterministic EvidenceAuditResult."
                ),
                confidence=1.0,
            ))

        component_by_id = {item.taxonomy_id: item for item in components}
        for taxonomy_id in semantic.suggested_atomic_taxonomy_ids:
            component = component_by_id.get(taxonomy_id)
            if component is None or component.support is not SemanticComponentSupport.SUPPORTED:
                self._conflict(
                    "suggested_atomic_taxonomy_ids",
                    "atomic_components_must_be_deterministically_supported",
                )
            deterministic = audit_by_id[taxonomy_id]
            if deterministic.strong_qualifier_failures:
                self._conflict(
                    "suggested_atomic_taxonomy_ids",
                    "strong_qualifier_constraint",
                )
        if semantic.preferred_taxonomy_id is not None:
            preferred = audit_by_id.get(semantic.preferred_taxonomy_id)
            if preferred is None or preferred.support is ComponentSupport.UNSUPPORTED:
                self._conflict(
                    "preferred_taxonomy_id",
                    "preferred_component_must_not_be_unsupported",
                )
            if preferred.strong_qualifier_failures:
                self._conflict(
                    "preferred_taxonomy_id", "strong_qualifier_constraint")
        if semantic.ability_validity is AbilityValidity.SUPPORTED and any(
            audit_by_id[target_id].support is ComponentSupport.UNSUPPORTED
            for target_id in target_ids
            if target_id in audit_by_id
        ):
            self._conflict(
                "ability_validity", "unsupported_target_prevents_supported_ability")

        try:
            return ShadowAbilityAssessment(
                schema_version=SHADOW_ASSESSMENT_SCHEMA_VERSION,
                resume_id=candidate.resume_id,
                candidate_id=candidate.candidate_id,
                taxonomy_version=self.taxonomy.taxonomy_version,
                evidence_audit_sha256=evidence_audit_sha256(audit),
                ability_validity=semantic.ability_validity,
                preferred_taxonomy_id=semantic.preferred_taxonomy_id,
                allowed_taxonomy_ids=scope,
                representation_labels=semantic.representation_labels,
                component_assessments=tuple(components),
                compound_label=audit.compound_label,
                split_recommended=semantic.split_recommended,
                suggested_atomic_taxonomy_ids=(
                    semantic.suggested_atomic_taxonomy_ids),
                warnings=audit.non_blocking_notes,
                reason=semantic.reason,
                confidence=semantic.confidence,
                diagnostics={
                    "assembler_version": SEMANTIC_SHADOW_ASSEMBLER_VERSION,
                    "semantic_response_schema_version": (
                        "ability_shadow_semantic_response_v2"),
                    "deterministic_component_support": True,
                },
            )
        except ShadowSchemaError as error:
            if error.constraint_name is not None:
                raise
            raise ShadowSchemaError(
                "assembled assessment violates the frozen v1 contract",
                error_code="assembler_conflict",
                invalid_field_path=error.invalid_field_path,
                constraint_name="assembled_v1_contract",
            ) from error

    @staticmethod
    def _conflict(field_path: str, constraint_name: str) -> None:
        raise ShadowSchemaError(
            "semantic result conflicts with deterministic constraints",
            error_code="assembler_conflict",
            invalid_field_path=field_path,
            constraint_name=constraint_name,
        )
