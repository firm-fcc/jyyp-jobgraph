"""Pure deterministic projection from shadow assessments to ReviewResult.

This module performs no model call and no controller action.  It joins frozen
offline contracts, validates their relationship, and emits immutable shadow
bundles in candidate order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from extractor.agentic_schema import (
    CandidateAbility,
    ControlAction,
    ErrorType,
    Evidence,
    ReviewResult,
    ReviewStatus,
)
from extractor.ability_shadow_schema import (
    AbilityValidity,
    RepresentationLabel,
    SemanticComponentSupport,
    ShadowAbilityAssessment,
)
from extractor.ability_taxonomy_v2 import AbilityTaxonomyV2, TaxonomyV2Error
from extractor.review_assessment_schema import (
    ComponentSupport,
    CompoundAssessmentLabel,
    DeterministicEvidenceDecision,
    EvidenceAuditResult,
    EvidenceExactnessStatus,
)
from extractor.shadow_review_bundle import (
    MAPPER_VERSION,
    SHADOW_REVIEW_BUNDLE_SCHEMA_VERSION,
    DecisionSource,
    ShadowReviewBundle,
    SplitRecommendation,
    ability_assessment_sha256,
    candidate_sha256,
    evidence_audit_sha256,
)


class ShadowMappingInputError(ValueError):
    """Raised for collection-level input errors that cannot form a bundle."""


@dataclass(frozen=True)
class _CatalogSpan:
    span_id: str
    text: str
    start: int
    end: int
    project_id: str


def _ordered_unique(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return tuple(result)


def _empty_split() -> SplitRecommendation:
    return SplitRecommendation(
        split_recommended=False,
        suggested_atomic_taxonomy_ids=(),
        suggested_atomic_abilities=(),
        supported_component_ids=(),
        unsupported_component_ids=(),
        production_action_available=False,
        notes=(),
    )


class DeterministicShadowReviewMapper:
    """Join Evidence Auditor and optional Shadow Reviewer outputs safely."""

    def __init__(self, taxonomy: AbilityTaxonomyV2) -> None:
        if not isinstance(taxonomy, AbilityTaxonomyV2):
            raise TypeError("taxonomy must be AbilityTaxonomyV2")
        self.taxonomy = taxonomy

    def map_all(
        self,
        resume_id: str,
        resume_text: str,
        candidates: Sequence[CandidateAbility],
        audits: Sequence[EvidenceAuditResult],
        assessments: Sequence[ShadowAbilityAssessment] = (),
        *,
        relocation_options_by_candidate_id: Mapping[str, Sequence[Any]] | None = None,
        merge_targets: Mapping[str, str] | None = None,
    ) -> tuple[ShadowReviewBundle, ...]:
        """Map one complete resume batch while preserving candidate order."""

        if not isinstance(resume_id, str) or not resume_id.strip():
            raise ShadowMappingInputError("resume_id must be a non-empty string")
        resume_id = resume_id.strip()
        if not isinstance(resume_text, str):
            raise ShadowMappingInputError("resume_text must be a string")
        candidate_items = self._typed_sequence(
            candidates, CandidateAbility, "candidates")
        audit_items = self._typed_sequence(audits, EvidenceAuditResult, "audits")
        assessment_items = self._typed_sequence(
            assessments, ShadowAbilityAssessment, "assessments")

        candidate_by_id = self._unique_by_id(
            candidate_items, "candidate", "candidate_id")
        audit_by_id = self._unique_by_id(audit_items, "audit", "candidate_id")
        assessment_by_id = self._unique_by_id(
            assessment_items, "assessment", "candidate_id")
        candidate_ids = set(candidate_by_id)

        missing_audits = candidate_ids - set(audit_by_id)
        unknown_audits = set(audit_by_id) - candidate_ids
        if missing_audits:
            raise ShadowMappingInputError(
                "missing audits: " + ", ".join(sorted(missing_audits)))
        if unknown_audits:
            raise ShadowMappingInputError(
                "unknown audit candidate_ids: "
                + ", ".join(sorted(unknown_audits)))
        unknown_assessments = set(assessment_by_id) - candidate_ids
        if unknown_assessments:
            raise ShadowMappingInputError(
                "unknown assessment candidate_ids: "
                + ", ".join(sorted(unknown_assessments)))

        options = {} if relocation_options_by_candidate_id is None else dict(
            relocation_options_by_candidate_id)
        unknown_option_ids = set(options) - candidate_ids
        if unknown_option_ids:
            raise ShadowMappingInputError(
                "unknown relocation option candidate_ids: "
                + ", ".join(sorted(unknown_option_ids)))
        merges = {} if merge_targets is None else dict(merge_targets)
        unknown_merge_ids = set(merges) - candidate_ids
        if unknown_merge_ids:
            raise ShadowMappingInputError(
                "unknown merge source candidate_ids: "
                + ", ".join(sorted(unknown_merge_ids)))

        bundles: list[ShadowReviewBundle] = []
        for candidate in candidate_items:
            if candidate.resume_id != resume_id:
                raise ShadowMappingInputError(
                    f"candidate {candidate.candidate_id} resume_id mismatch")
            bundles.append(self._map_one(
                resume_id=resume_id,
                resume_text=resume_text,
                candidate=CandidateAbility.from_dict(candidate.to_dict()),
                audit=EvidenceAuditResult.from_dict(
                    audit_by_id[candidate.candidate_id].to_dict()),
                assessment=(
                    None
                    if candidate.candidate_id not in assessment_by_id
                    else ShadowAbilityAssessment.from_dict(
                        assessment_by_id[candidate.candidate_id].to_dict(),
                    )
                ),
                relocation_options=options.get(candidate.candidate_id, ()),
                merge_target_id=merges.get(candidate.candidate_id),
                candidate_by_id=candidate_by_id,
            ))
        return tuple(bundles)

    @staticmethod
    def _typed_sequence(value: Any, item_type: type, field: str) -> tuple[Any, ...]:
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ShadowMappingInputError(f"{field} must be a sequence")
        result = tuple(value)
        for index, item in enumerate(result):
            if not isinstance(item, item_type):
                raise ShadowMappingInputError(
                    f"{field}[{index}] must be {item_type.__name__}")
        return result

    @staticmethod
    def _unique_by_id(
        values: Sequence[Any], label: str, attribute: str
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for item in values:
            item_id = getattr(item, attribute)
            if item_id in result:
                raise ShadowMappingInputError(
                    f"duplicate {label} candidate_id: {item_id}")
            result[item_id] = item
        return result

    def _map_one(
        self,
        *,
        resume_id: str,
        resume_text: str,
        candidate: CandidateAbility,
        audit: EvidenceAuditResult,
        assessment: ShadowAbilityAssessment | None,
        relocation_options: Sequence[Any],
        merge_target_id: str | None,
        candidate_by_id: Mapping[str, CandidateAbility],
    ) -> ShadowReviewBundle:
        conflicts = self._relationship_conflicts(
            resume_id, candidate, audit, assessment)
        if audit.requires_model_review and assessment is None:
            conflicts.append("missing_shadow_assessment")
        if not audit.requires_model_review and assessment is not None:
            conflicts.append("unexpected_shadow_assessment")
        if conflicts:
            return self._blocked(candidate, audit, assessment, conflicts)

        warnings = _ordered_unique((
            *audit.non_blocking_notes,
            *((assessment.warnings) if assessment is not None else ()),
        ))
        split, split_conflicts = self._split_recommendation(audit, assessment)
        if split_conflicts:
            return self._blocked(
                candidate, audit, assessment, split_conflicts,
                warnings=warnings)

        exactness_conflicts = self._exactness_conflicts(audit)
        if exactness_conflicts:
            return self._blocked(
                candidate, audit, assessment, exactness_conflicts,
                warnings=warnings, split=split)

        decision = audit.evidence_decision
        if decision is DeterministicEvidenceDecision.MISSING:
            return self._success(
                candidate, audit, assessment,
                self._review(
                    candidate,
                    status=ReviewStatus.FAILED,
                    errors=(ErrorType.EVIDENCE_NOT_FOUND,),
                    action=ControlAction.DELETE,
                    reason="Evidence Auditor confirmed that no exact evidence remains.",
                ),
                split=split,
                warnings=warnings,
                reason="evidence_not_found has deterministic priority",
                priority_rank=2,
            )

        if decision is DeterministicEvidenceDecision.INSUFFICIENT_BUT_RELOCATABLE:
            semantic_action = self._semantic_action_kind(assessment)
            if semantic_action not in {None, ControlAction.KEEP}:
                return self._blocked(
                    candidate, audit, assessment,
                    ("relocation_semantic_action_conflict",),
                    warnings=warnings, split=split)
            evidence, relocation_conflicts = self._relocation_evidence(
                resume_text, candidate, audit, relocation_options)
            if relocation_conflicts:
                return self._blocked(
                    candidate, audit, assessment, relocation_conflicts,
                    warnings=warnings, split=split)
            return self._success(
                candidate, audit, assessment,
                self._review(
                    candidate,
                    status=ReviewStatus.FAILED,
                    errors=(ErrorType.EVIDENCE_INSUFFICIENT,),
                    action=ControlAction.RELOCATE,
                    reason="Current evidence is insufficient; Auditor supplied exact Catalog v2 evidence.",
                    target_evidence=evidence,
                ),
                split=split,
                warnings=warnings,
                reason="exact relocatable evidence has priority over semantic keep",
                priority_rank=3,
            )

        if merge_target_id is not None:
            review_or_conflict = self._merge_review(
                candidate, merge_target_id, candidate_by_id)
            if isinstance(review_or_conflict, tuple):
                return self._blocked(
                    candidate, audit, assessment, review_or_conflict,
                    warnings=warnings, split=split)
            return self._success(
                candidate, audit, assessment, review_or_conflict,
                split=split,
                warnings=warnings,
                reason="explicit deterministic duplicate target was validated",
                priority_rank=7,
            )

        if assessment is not None:
            semantic = self._map_assessment(candidate, audit, assessment, split)
            if not semantic or not isinstance(semantic[0], ReviewResult):
                return self._blocked(
                    candidate, audit, assessment, semantic,
                    warnings=warnings, split=split)
            review, human, reason, rank = semantic
            return self._success(
                candidate, audit, assessment, review,
                split=split,
                warnings=warnings,
                reason=reason,
                priority_rank=rank,
                requires_human_review=human,
            )

        if decision is DeterministicEvidenceDecision.SUFFICIENT:
            if audit.blocking_issues:
                return self._blocked(
                    candidate, audit, assessment,
                    ("sufficient_decision_has_blocking_issues",),
                    warnings=warnings, split=split)
            if audit.compound_label is CompoundAssessmentLabel.SPLIT_RECOMMENDED:
                return self._blocked(
                    candidate, audit, assessment,
                    ("split_requires_shadow_assessment",),
                    warnings=warnings, split=split)
            return self._success(
                candidate, audit, assessment,
                self._review(
                    candidate,
                    status=ReviewStatus.PASSED,
                    errors=(),
                    action=ControlAction.KEEP,
                    reason="Deterministic evidence and representation checks passed.",
                ),
                split=split,
                warnings=warnings,
                reason="no blocking issue remained after deterministic checks",
                priority_rank=9,
            )

        if decision is DeterministicEvidenceDecision.INSUFFICIENT_AND_NOT_RELOCATABLE:
            deterministic = self._map_deterministic_insufficient(candidate, audit)
            if isinstance(deterministic, tuple):
                return self._blocked(
                    candidate, audit, assessment, deterministic,
                    warnings=warnings, split=split)
            return self._success(
                candidate, audit, assessment, deterministic,
                split=split,
                warnings=warnings,
                reason="deterministic unsupported qualifier or inference was projected safely",
                priority_rank=5,
            )

        return self._blocked(
            candidate, audit, assessment,
            ("unresolved_evidence_decision",),
            warnings=warnings, split=split)

    def _relationship_conflicts(
        self,
        resume_id: str,
        candidate: CandidateAbility,
        audit: EvidenceAuditResult,
        assessment: ShadowAbilityAssessment | None,
    ) -> list[str]:
        conflicts: list[str] = []
        if audit.candidate_id != candidate.candidate_id:
            conflicts.append("audit_candidate_mismatch")
        if audit.resume_id != resume_id or audit.resume_id != candidate.resume_id:
            conflicts.append("audit_resume_mismatch")
        taxonomy_version = audit.diagnostics.get("taxonomy_version")
        if taxonomy_version != self.taxonomy.taxonomy_version:
            conflicts.append("audit_taxonomy_version_mismatch")
        for component in audit.component_assessments:
            try:
                node = self.taxonomy.get_node(component.taxonomy_id)
            except TaxonomyV2Error:
                conflicts.append("audit_unknown_taxonomy_id")
                continue
            if node.canonical_name != component.canonical_name:
                conflicts.append("audit_canonical_name_mismatch")

        if assessment is None:
            return list(_ordered_unique(conflicts))
        if assessment.candidate_id != candidate.candidate_id:
            conflicts.append("assessment_candidate_mismatch")
        if assessment.resume_id != resume_id:
            conflicts.append("assessment_resume_mismatch")
        if assessment.evidence_audit_sha256 != evidence_audit_sha256(audit):
            conflicts.append("assessment_audit_sha_mismatch")
        if assessment.taxonomy_version != self.taxonomy.taxonomy_version:
            conflicts.append("assessment_taxonomy_version_mismatch")
        if not set(assessment.allowed_taxonomy_ids).issubset(
            audit.taxonomy_subset_ids
        ):
            conflicts.append("assessment_taxonomy_scope_mismatch")

        evidence_components = {
            item.taxonomy_id: item for item in audit.component_assessments
        }
        for component in assessment.component_assessments:
            deterministic = evidence_components.get(component.taxonomy_id)
            try:
                node = self.taxonomy.get_node(component.taxonomy_id)
            except TaxonomyV2Error:
                conflicts.append("assessment_unknown_taxonomy_id")
                continue
            if component.canonical_name != node.canonical_name:
                conflicts.append("assessment_canonical_name_mismatch")
            if deterministic is None:
                conflicts.append("assessment_component_missing_from_audit")
                continue
            if component.evidence_audit_support is not deterministic.support:
                conflicts.append("assessment_rewrites_audit_support")
            if (
                deterministic.support is ComponentSupport.UNSUPPORTED
                and component.support is SemanticComponentSupport.SUPPORTED
            ):
                conflicts.append("unsupported_overturned_to_supported")
            if (
                deterministic.support is ComponentSupport.SUPPORTED
                and component.support is SemanticComponentSupport.UNSUPPORTED
            ):
                conflicts.append("supported_overturned_to_unsupported")
        return list(_ordered_unique(conflicts))

    @staticmethod
    def _exactness_conflicts(audit: EvidenceAuditResult) -> tuple[str, ...]:
        invalid = {
            EvidenceExactnessStatus.INVALID_RANGE,
            EvidenceExactnessStatus.TEXT_MISMATCH,
            EvidenceExactnessStatus.WRONG_PROJECT,
            EvidenceExactnessStatus.AMBIGUOUS,
        }
        return (
            ("evidence_exactness_contract_failure",)
            if any(item.exactness_status in invalid
                   for item in audit.current_evidence_audits)
            else ()
        )

    def _split_recommendation(
        self,
        audit: EvidenceAuditResult,
        assessment: ShadowAbilityAssessment | None,
    ) -> tuple[SplitRecommendation, tuple[str, ...]]:
        if assessment is None or not assessment.split_recommended:
            return _empty_split(), ()
        semantic_by_id = {
            item.taxonomy_id: item for item in assessment.component_assessments
        }
        supported = tuple(
            item.taxonomy_id for item in assessment.component_assessments
            if item.support is SemanticComponentSupport.SUPPORTED
        )
        unsupported = tuple(
            item.taxonomy_id for item in assessment.component_assessments
            if item.support is SemanticComponentSupport.UNSUPPORTED
        )
        suggested = assessment.suggested_atomic_taxonomy_ids
        if len(suggested) < 2:
            return _empty_split(), ("split_has_fewer_than_two_components",)
        if not set(suggested).issubset(supported):
            return _empty_split(), ("split_contains_unsupported_component",)
        names: list[str] = []
        for taxonomy_id in suggested:
            component = semantic_by_id.get(taxonomy_id)
            if component is None:
                return _empty_split(), ("split_component_missing",)
            try:
                node = self.taxonomy.get_node(taxonomy_id)
            except TaxonomyV2Error:
                return _empty_split(), ("split_unknown_taxonomy_id",)
            if component.canonical_name != node.canonical_name:
                return _empty_split(), ("split_canonical_name_mismatch",)
            names.append(node.canonical_name)
        return SplitRecommendation(
            split_recommended=True,
            suggested_atomic_taxonomy_ids=suggested,
            suggested_atomic_abilities=tuple(names),
            supported_component_ids=supported,
            unsupported_component_ids=unsupported,
            production_action_available=False,
            notes=(
                "shadow recommendation only",
                "no production split action was executed",
            ),
        ), ()

    def _relocation_evidence(
        self,
        resume_text: str,
        candidate: CandidateAbility,
        audit: EvidenceAuditResult,
        raw_options: Sequence[Any],
    ) -> tuple[list[Evidence], tuple[str, ...]]:
        try:
            spans = self._catalog_spans(candidate, raw_options)
        except ShadowMappingInputError:
            return [], ("invalid_relocation_span",)
        by_id = {item.span_id: item for item in spans}
        if not audit.recommended_relocation_span_ids:
            return [], ("relocation_has_no_recommended_span",)
        result: list[Evidence] = []
        current = {
            (item.text, item.project_id, item.start, item.end)
            for item in candidate.evidence
        }
        for span_id in audit.recommended_relocation_span_ids:
            span = by_id.get(span_id)
            if span is None:
                return [], ("relocation_span_not_in_candidate_options",)
            if (
                span.start < 0
                or span.end <= span.start
                or span.end > len(resume_text)
                or resume_text[span.start:span.end] != span.text
            ):
                return [], ("relocation_span_not_exact",)
            key = (span.text, span.project_id, span.start, span.end)
            if key in current:
                return [], ("relocation_is_no_op",)
            result.append(Evidence(
                text=span.text,
                project_id=span.project_id,
                start=span.start,
                end=span.end,
            ))
        return result, ()

    @staticmethod
    def _catalog_spans(
        candidate: CandidateAbility, raw_options: Sequence[Any]
    ) -> tuple[_CatalogSpan, ...]:
        if isinstance(raw_options, (str, bytes)) or not isinstance(
            raw_options, Sequence
        ):
            raise ShadowMappingInputError("relocation options must be a sequence")
        result: list[_CatalogSpan] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_options):
            if isinstance(raw, Mapping):
                allowed = {"span_id", "text", "start", "end", "line_index", "project_id"}
                if not {"span_id", "text", "start", "end"}.issubset(raw):
                    raise ShadowMappingInputError(
                        f"relocation option {index} is missing fields")
                if set(raw) - allowed:
                    raise ShadowMappingInputError(
                        f"relocation option {index} has unknown fields")
                span_id = raw["span_id"]
                text = raw["text"]
                start = raw["start"]
                end = raw["end"]
                project_id = raw.get("project_id", candidate.project_id)
            else:
                try:
                    span_id = raw.span_id
                    text = raw.text
                    start = raw.start
                    end = raw.end
                except AttributeError as error:
                    raise ShadowMappingInputError(
                        f"relocation option {index} is invalid") from error
                project_id = candidate.project_id
            if not isinstance(span_id, str) or not span_id.strip():
                raise ShadowMappingInputError("span_id must be non-empty")
            span_id = span_id.strip()
            if span_id in seen:
                raise ShadowMappingInputError("relocation span_id must be unique")
            seen.add(span_id)
            if not isinstance(text, str) or not text:
                raise ShadowMappingInputError("relocation text must be non-empty")
            if (
                isinstance(start, bool) or isinstance(end, bool)
                or not isinstance(start, int) or not isinstance(end, int)
            ):
                raise ShadowMappingInputError("relocation range must use integers")
            if project_id != candidate.project_id:
                raise ShadowMappingInputError("relocation project_id mismatch")
            result.append(_CatalogSpan(
                span_id=span_id,
                text=text,
                start=start,
                end=end,
                project_id=project_id,
            ))
        return tuple(result)

    def _map_assessment(
        self,
        candidate: CandidateAbility,
        audit: EvidenceAuditResult,
        assessment: ShadowAbilityAssessment,
        split: SplitRecommendation,
    ) -> tuple[ReviewResult, bool, str, int] | tuple[str, ...]:
        labels = set(assessment.representation_labels)
        if RepresentationLabel.DUPLICATE in labels:
            return ("duplicate_missing_merge_target",)
        if assessment.split_recommended:
            preferred = assessment.preferred_taxonomy_id
            if preferred not in split.suggested_atomic_taxonomy_ids:
                preferred = split.suggested_atomic_taxonomy_ids[0]
            target = self.taxonomy.get_node(preferred).canonical_name
            return (
                self._review(
                    candidate,
                    status=ReviewStatus.FAILED,
                    errors=(ErrorType.OVER_INFERENCE,),
                    action=ControlAction.NARROW,
                    reason=(
                        "Shadow analysis found multiple supported atomic abilities; "
                        "the projection narrows to one primary component only."
                    ),
                    target_ability=target,
                ),
                True,
                "split is preserved outside ReviewResult and needs human review",
                5,
            )

        if audit.compound_label is CompoundAssessmentLabel.COMPOUND_SUPPORTED:
            if (
                assessment.ability_validity is not AbilityValidity.SUPPORTED
                or assessment.compound_label
                is not CompoundAssessmentLabel.COMPOUND_SUPPORTED
            ):
                return ("protected_compound_conflict",)
            return (
                self._review(
                    candidate,
                    status=ReviewStatus.PASSED,
                    errors=(),
                    action=ControlAction.KEEP,
                    reason="All protected compound components are supported.",
                ),
                False,
                "legal compound protection has priority over surface conjunctions",
                9,
            )

        if assessment.ability_validity is AbilityValidity.AMBIGUOUS:
            return ("ability_validity_ambiguous",)

        preferred = assessment.preferred_taxonomy_id
        target = None
        if preferred is not None:
            target = self.taxonomy.get_node(preferred).canonical_name

        if assessment.ability_validity is AbilityValidity.PARTIALLY_SUPPORTED:
            if target is None:
                return ("partially_supported_missing_target",)
            return (
                self._review(
                    candidate,
                    status=ReviewStatus.FAILED,
                    errors=(ErrorType.OVER_INFERENCE,),
                    action=ControlAction.NARROW,
                    reason="Only the preferred taxonomy component is supported.",
                    target_ability=target,
                ),
                False,
                "partially supported ability is narrowed to canonical scope",
                5,
            )

        if assessment.ability_validity is AbilityValidity.UNSUPPORTED:
            supported = [
                item for item in assessment.component_assessments
                if item.support is SemanticComponentSupport.SUPPORTED
            ]
            if target is not None and any(
                item.taxonomy_id == preferred for item in supported
            ):
                return (
                    self._review(
                        candidate,
                        status=ReviewStatus.FAILED,
                        errors=(ErrorType.OVER_INFERENCE,),
                        action=ControlAction.NARROW,
                        reason="The full ability is unsupported but one canonical component remains supported.",
                        target_ability=target,
                    ),
                    False,
                    "unsupported compound was narrowed to its supported component",
                    4,
                )
            out_of_scope = "out_of_scope" in assessment.warnings
            return (
                self._review(
                    candidate,
                    status=ReviewStatus.FAILED,
                    errors=((ErrorType.OUT_OF_SCOPE,) if out_of_scope
                            else (ErrorType.OVER_INFERENCE,)),
                    action=ControlAction.DELETE,
                    reason="Shadow assessment found no adequately supported ability.",
                ),
                False,
                "unsupported ability cannot be kept",
                4,
            )

        if audit.evidence_decision is not DeterministicEvidenceDecision.SUFFICIENT:
            return ("semantic_support_conflicts_with_evidence_decision",)
        if RepresentationLabel.ABILITY_NAME_TOO_BROAD in labels:
            if target is None:
                return ("broad_name_missing_target",)
            return (
                self._review(
                    candidate,
                    status=ReviewStatus.FAILED,
                    errors=(ErrorType.OVER_INFERENCE,),
                    action=ControlAction.NARROW,
                    reason="The ability meaning is broader than its supported taxonomy scope.",
                    target_ability=target,
                ),
                False,
                "ability granularity requires narrowing",
                6,
            )
        if RepresentationLabel.ABILITY_NAME_BAD in labels:
            if target is None:
                return ("bad_name_missing_target",)
            return (
                self._review(
                    candidate,
                    status=ReviewStatus.FAILED,
                    errors=(ErrorType.BAD_NAME,),
                    action=ControlAction.RENAME,
                    reason="The supported meaning is retained under its canonical taxonomy name.",
                    target_ability=target,
                ),
                False,
                "canonical rename does not change ability scope",
                6,
            )
        return (
            self._review(
                candidate,
                status=ReviewStatus.PASSED,
                errors=(),
                action=ControlAction.KEEP,
                reason="Evidence and semantic representation are supported.",
            ),
            False,
            "semantic assessment introduced no blocking representation issue",
            9,
        )

    def _map_deterministic_insufficient(
        self, candidate: CandidateAbility, audit: EvidenceAuditResult
    ) -> ReviewResult | tuple[str, ...]:
        target_ids = tuple(audit.diagnostics.get("target_component_ids", ()))
        components = {
            item.taxonomy_id: item for item in audit.component_assessments
        }
        if len(target_ids) != 1 or target_ids[0] not in components:
            return ("insufficient_without_unique_target",)
        component = components[target_ids[0]]
        if component.support is ComponentSupport.SUPPORTED:
            return ("insufficient_but_target_supported",)
        node = self.taxonomy.get_node(component.taxonomy_id)
        if component.strong_qualifier_failures:
            parent = self.taxonomy.parent_of(node.id)
            if parent is not None:
                return self._review(
                    candidate,
                    status=ReviewStatus.FAILED,
                    errors=(ErrorType.OVER_INFERENCE,),
                    action=ControlAction.NARROW,
                    reason="A strong qualifier lacks direct evidence; the parent ability is retained.",
                    target_ability=parent.canonical_name,
                )
        mapped_supported_targets = []
        for source_component in audit.component_assessments:
            if source_component.support is not ComponentSupport.SUPPORTED:
                continue
            for target_node in self.taxonomy.evidence_targets_for(
                source_component.taxonomy_id
            ):
                target_id = target_node.id
                target_component = components.get(target_id)
                if (
                    target_component is not None
                    and target_component.support is ComponentSupport.SUPPORTED
                    and target_id not in mapped_supported_targets
                ):
                    mapped_supported_targets.append(target_id)
        if len(mapped_supported_targets) == 1:
            mapped_node = self.taxonomy.get_node(mapped_supported_targets[0])
            return self._review(
                candidate,
                status=ReviewStatus.FAILED,
                errors=(ErrorType.OVER_INFERENCE,),
                action=ControlAction.NARROW,
                reason=(
                    "The inferred ability is broader than the explicitly mapped "
                    "and independently supported evidence target."
                ),
                target_ability=mapped_node.canonical_name,
            )
        if len(mapped_supported_targets) > 1:
            return ("multiple_supported_evidence_mapping_targets",)
        return self._review(
            candidate,
            status=ReviewStatus.FAILED,
            errors=(ErrorType.OVER_INFERENCE,),
            action=ControlAction.DELETE,
            reason="The inferred ability is unsupported and cannot be relocated safely.",
        )

    def _merge_review(
        self,
        candidate: CandidateAbility,
        target_id: str,
        candidate_by_id: Mapping[str, CandidateAbility],
    ) -> ReviewResult | tuple[str, ...]:
        if not isinstance(target_id, str) or not target_id.strip():
            return ("duplicate_missing_merge_target",)
        target_id = target_id.strip()
        target = candidate_by_id.get(target_id)
        if target is None:
            return ("duplicate_unknown_merge_target",)
        if target_id == candidate.candidate_id:
            return ("duplicate_self_merge_target",)
        if target.resume_id != candidate.resume_id:
            return ("duplicate_cross_resume_target",)
        return self._review(
            candidate,
            status=ReviewStatus.FAILED,
            errors=(ErrorType.SYNONYM_DUPLICATE,),
            action=ControlAction.MERGE,
            reason="An explicit same-resume duplicate target was validated.",
            merge_target_id=target_id,
        )

    @staticmethod
    def _semantic_action_kind(
        assessment: ShadowAbilityAssessment | None,
    ) -> ControlAction | None:
        if assessment is None:
            return None
        labels = set(assessment.representation_labels)
        if RepresentationLabel.DUPLICATE in labels:
            return ControlAction.MERGE
        if assessment.split_recommended:
            return ControlAction.NARROW
        if assessment.ability_validity is AbilityValidity.UNSUPPORTED:
            return ControlAction.DELETE
        if assessment.ability_validity is AbilityValidity.PARTIALLY_SUPPORTED:
            return ControlAction.NARROW
        if RepresentationLabel.ABILITY_NAME_TOO_BROAD in labels:
            return ControlAction.NARROW
        if RepresentationLabel.ABILITY_NAME_BAD in labels:
            return ControlAction.RENAME
        if assessment.ability_validity is AbilityValidity.SUPPORTED:
            return ControlAction.KEEP
        return None

    @staticmethod
    def _review(
        candidate: CandidateAbility,
        *,
        status: ReviewStatus,
        errors: Sequence[ErrorType],
        action: ControlAction,
        reason: str,
        target_ability: str | None = None,
        target_evidence: Sequence[Evidence] = (),
        merge_target_id: str | None = None,
    ) -> ReviewResult:
        return ReviewResult(
            candidate_id=candidate.candidate_id,
            status=status,
            error_types=list(errors),
            action=action,
            reason=reason,
            target_ability=target_ability,
            target_evidence=[Evidence.from_dict(item.to_dict())
                             for item in target_evidence],
            merge_target_id=merge_target_id,
        )

    def _success(
        self,
        candidate: CandidateAbility,
        audit: EvidenceAuditResult,
        assessment: ShadowAbilityAssessment | None,
        review: ReviewResult,
        *,
        split: SplitRecommendation,
        warnings: Sequence[str],
        reason: str,
        priority_rank: int,
        requires_human_review: bool = False,
    ) -> ShadowReviewBundle:
        return ShadowReviewBundle(
            schema_version=SHADOW_REVIEW_BUNDLE_SCHEMA_VERSION,
            resume_id=candidate.resume_id,
            candidate_id=candidate.candidate_id,
            candidate_sha256=candidate_sha256(candidate),
            evidence_audit_sha256=evidence_audit_sha256(audit),
            ability_assessment_sha256=(
                None if assessment is None
                else ability_assessment_sha256(assessment)),
            decision_source=(
                DecisionSource.DETERMINISTIC_ONLY
                if assessment is None
                else DecisionSource.DETERMINISTIC_PLUS_SHADOW
            ),
            mapped_review_result=review,
            split_recommendation=split,
            mapper_reason=reason,
            warnings=tuple(warnings),
            conflicts=(),
            requires_human_review=requires_human_review,
            diagnostics={
                "mapper_version": MAPPER_VERSION,
                "taxonomy_version": self.taxonomy.taxonomy_version,
                "mapping_priority_rank": priority_rank,
                "controller_executed": False,
            },
        )

    def _blocked(
        self,
        candidate: CandidateAbility,
        audit: EvidenceAuditResult,
        assessment: ShadowAbilityAssessment | None,
        conflicts: Sequence[str],
        *,
        warnings: Sequence[str] = (),
        split: SplitRecommendation | None = None,
    ) -> ShadowReviewBundle:
        return ShadowReviewBundle(
            schema_version=SHADOW_REVIEW_BUNDLE_SCHEMA_VERSION,
            resume_id=candidate.resume_id,
            candidate_id=candidate.candidate_id,
            candidate_sha256=candidate_sha256(candidate),
            evidence_audit_sha256=evidence_audit_sha256(audit),
            ability_assessment_sha256=(
                None if assessment is None
                else ability_assessment_sha256(assessment)),
            decision_source=DecisionSource.MAPPING_BLOCKED,
            mapped_review_result=None,
            split_recommendation=_empty_split() if split is None else split,
            mapper_reason="mapping blocked by contract conflict",
            warnings=tuple(warnings),
            conflicts=_ordered_unique(tuple(conflicts)),
            requires_human_review=True,
            diagnostics={
                "mapper_version": MAPPER_VERSION,
                "taxonomy_version": self.taxonomy.taxonomy_version,
                "controller_executed": False,
            },
        )
