"""Strict offline tests for the versioned shadow assessment schema."""

from __future__ import annotations

import copy
import json
import unittest

from extractor.review_assessment_schema import (
    ASSESSMENT_SCHEMA_VERSION,
    AssessmentSchemaError,
    ComponentEvidenceAssessment,
    ComponentSupport,
    CompoundAssessmentLabel,
    DeterministicEvidenceDecision,
    EvidenceAuditResult,
    EvidenceExactnessStatus,
    EvidenceSpanAudit,
    RequirementCheck,
    RequirementSupport,
    TaxonomySelectionTraceEntry,
)


def valid_requirement() -> RequirementCheck:
    return RequirementCheck(
        requirement_id="req.direct_activity.direct_action",
        requirement_description="需要直接行动",
        status=RequirementSupport.MET,
        matched_texts=("实现接口",),
        matched_span_ids=("span_0001",),
        missing_items=(),
        forbidden_shortcut_hits=(),
        deterministic=True,
    )


def valid_component(taxonomy_id: str = "ability.backend_api_development"):
    return ComponentEvidenceAssessment(
        taxonomy_id=taxonomy_id,
        canonical_name="后端API开发",
        support=ComponentSupport.SUPPORTED,
        current_evidence_requirement_checks=(valid_requirement(),),
        relocation_requirement_checks=(),
        matched_current_evidence=("实现接口",),
        matched_relocation_span_ids=(),
        missing_requirements=(),
        strong_qualifier_failures=(),
        requires_model_review=False,
    )


def valid_result() -> EvidenceAuditResult:
    taxonomy_id = "ability.backend_api_development"
    return EvidenceAuditResult(
        schema_version=ASSESSMENT_SCHEMA_VERSION,
        resume_id="resume-1",
        candidate_id="candidate-1",
        current_evidence_audits=(EvidenceSpanAudit(
            evidence_index=0,
            text="实现接口",
            start=0,
            end=4,
            project_id="resume_full",
            exactness_status=EvidenceExactnessStatus.EXACT,
            matched_catalog_span_id="span_0001",
            issues=(),
        ),),
        taxonomy_subset_ids=(taxonomy_id,),
        taxonomy_selection_trace=(TaxonomySelectionTraceEntry(
            taxonomy_id=taxonomy_id,
            score=100,
            reasons=("exact_canonical",),
        ),),
        component_assessments=(valid_component(),),
        evidence_decision=DeterministicEvidenceDecision.SUFFICIENT,
        recommended_relocation_span_ids=(),
        compound_label=CompoundAssessmentLabel.NOT_COMPOUND,
        blocking_issues=(),
        non_blocking_notes=(),
        requires_model_review=False,
        diagnostics={"model_called": False},
    )


class ReviewAssessmentSchemaTests(unittest.TestCase):
    def test_enum_values_match_shadow_contract(self):
        self.assertEqual(
            {item.value for item in EvidenceExactnessStatus},
            {"exact", "missing", "invalid_range", "text_mismatch",
             "wrong_project", "duplicate", "ambiguous"},
        )
        self.assertEqual(
            {item.value for item in RequirementSupport},
            {"met", "unmet", "partially_met", "not_applicable",
             "requires_model_review"},
        )
        self.assertEqual(
            {item.value for item in ComponentSupport},
            {"supported", "unsupported", "partially_supported", "ambiguous"},
        )
        self.assertEqual(
            {item.value for item in DeterministicEvidenceDecision},
            {"sufficient", "insufficient_but_relocatable",
             "insufficient_and_not_relocatable", "missing",
             "requires_model_review"},
        )
        self.assertEqual(
            {item.value for item in CompoundAssessmentLabel},
            {"not_compound", "compound_supported", "compound_unsupported",
             "split_recommended", "ambiguous"},
        )

    def test_schema_version_is_strict(self):
        data = valid_result().to_dict()
        data["schema_version"] = "v2"
        with self.assertRaises(AssessmentSchemaError):
            EvidenceAuditResult.from_dict(data)

    def test_unknown_and_missing_result_fields_are_rejected(self):
        data = valid_result().to_dict()
        data["unknown"] = True
        with self.assertRaises(AssessmentSchemaError):
            EvidenceAuditResult.from_dict(data)
        data = valid_result().to_dict()
        del data["candidate_id"]
        with self.assertRaises(AssessmentSchemaError):
            EvidenceAuditResult.from_dict(data)

    def test_unknown_nested_fields_are_rejected(self):
        data = valid_result().to_dict()
        data["current_evidence_audits"][0]["extra"] = 1
        with self.assertRaises(AssessmentSchemaError):
            EvidenceAuditResult.from_dict(data)
        data = valid_result().to_dict()
        data["component_assessments"][0][
            "current_evidence_requirement_checks"][0]["extra"] = 1
        with self.assertRaises(AssessmentSchemaError):
            EvidenceAuditResult.from_dict(data)

    def test_invalid_enum_is_rejected(self):
        data = valid_result().to_dict()
        data["evidence_decision"] = "approved"
        with self.assertRaises(AssessmentSchemaError):
            EvidenceAuditResult.from_dict(data)

    def test_stable_serialization_and_json_round_trip(self):
        result = valid_result()
        self.assertEqual(result.serialize(), result.serialize())
        round_tripped = EvidenceAuditResult.from_dict(
            json.loads(result.serialize()))
        self.assertEqual(round_tripped.serialize(), result.serialize())

    def test_from_dict_does_not_modify_input(self):
        data = valid_result().to_dict()
        original = copy.deepcopy(data)
        EvidenceAuditResult.from_dict(data)
        self.assertEqual(data, original)

    def test_duplicate_component_id_is_rejected(self):
        data = valid_result().to_dict()
        data["component_assessments"].append(
            copy.deepcopy(data["component_assessments"][0]))
        with self.assertRaises(AssessmentSchemaError):
            EvidenceAuditResult.from_dict(data)

    def test_duplicate_relocation_span_is_rejected(self):
        data = valid_result().to_dict()
        data["recommended_relocation_span_ids"] = ["span_0002", "span_0002"]
        with self.assertRaises(AssessmentSchemaError):
            EvidenceAuditResult.from_dict(data)

    def test_trace_must_cover_subset_with_unique_ids(self):
        data = valid_result().to_dict()
        data["taxonomy_selection_trace"] = []
        with self.assertRaises(AssessmentSchemaError):
            EvidenceAuditResult.from_dict(data)
        data = valid_result().to_dict()
        data["taxonomy_selection_trace"].append(
            copy.deepcopy(data["taxonomy_selection_trace"][0]))
        with self.assertRaises(AssessmentSchemaError):
            EvidenceAuditResult.from_dict(data)

    def test_requires_model_review_consistency(self):
        data = valid_result().to_dict()
        data["requires_model_review"] = True
        with self.assertRaises(AssessmentSchemaError):
            EvidenceAuditResult.from_dict(data)

        data = valid_result().to_dict()
        data["evidence_decision"] = "requires_model_review"
        data["requires_model_review"] = True
        parsed = EvidenceAuditResult.from_dict(data)
        self.assertTrue(parsed.requires_model_review)

    def test_semantic_handoff_is_explicit_and_consistent(self):
        data = valid_result().to_dict()
        data["non_blocking_notes"] = [
            "final_ability_representation_requires_model_review"]
        data["requires_model_review"] = True
        parsed = EvidenceAuditResult.from_dict(data)
        self.assertTrue(parsed.requires_model_review)

    def test_non_target_ambiguous_component_does_not_force_model_review(self):
        data = valid_result().to_dict()
        extra = valid_component("ability.unrelated_high_level").to_dict()
        extra["support"] = "ambiguous"
        extra["requires_model_review"] = True
        extra["current_evidence_requirement_checks"] = []
        data["taxonomy_subset_ids"].append("ability.unrelated_high_level")
        data["taxonomy_selection_trace"].append({
            "taxonomy_id": "ability.unrelated_high_level",
            "score": 1,
            "reasons": ["exact_canonical"],
        })
        data["component_assessments"].append(extra)
        data["diagnostics"]["target_component_ids"] = [
            "ability.backend_api_development"]
        parsed = EvidenceAuditResult.from_dict(data)
        self.assertFalse(parsed.requires_model_review)

    def test_target_ambiguous_component_still_requires_model_review(self):
        data = valid_result().to_dict()
        data["component_assessments"][0]["support"] = "ambiguous"
        data["component_assessments"][0]["requires_model_review"] = True
        data["component_assessments"][0][
            "current_evidence_requirement_checks"] = []
        data["diagnostics"]["target_component_ids"] = [
            "ability.backend_api_development"]
        data["requires_model_review"] = True
        parsed = EvidenceAuditResult.from_dict(data)
        self.assertTrue(parsed.requires_model_review)

    def test_blocking_and_non_blocking_issue_cannot_overlap(self):
        data = valid_result().to_dict()
        data["blocking_issues"] = ["same"]
        data["non_blocking_notes"] = ["same"]
        with self.assertRaises(AssessmentSchemaError):
            EvidenceAuditResult.from_dict(data)

    def test_non_deterministic_requirement_must_require_model(self):
        with self.assertRaises(AssessmentSchemaError):
            RequirementCheck(
                requirement_id="x", requirement_description="x",
                status=RequirementSupport.MET, matched_texts=(),
                matched_span_ids=(), missing_items=(),
                forbidden_shortcut_hits=(), deterministic=False,
            )

    def test_model_review_component_must_be_ambiguous(self):
        data = valid_component().to_dict()
        data["requires_model_review"] = True
        with self.assertRaises(AssessmentSchemaError):
            ComponentEvidenceAssessment.from_dict(data)

    def test_exact_evidence_requires_range_and_catalog_id(self):
        data = valid_result().to_dict()["current_evidence_audits"][0]
        data["start"] = None
        with self.assertRaises(AssessmentSchemaError):
            EvidenceSpanAudit.from_dict(data)
        data = valid_result().to_dict()["current_evidence_audits"][0]
        data["matched_catalog_span_id"] = None
        with self.assertRaises(AssessmentSchemaError):
            EvidenceSpanAudit.from_dict(data)

    def test_empty_subset_is_valid_for_oov_model_handoff(self):
        data = valid_result().to_dict()
        data["taxonomy_subset_ids"] = []
        data["taxonomy_selection_trace"] = []
        data["component_assessments"] = []
        data["evidence_decision"] = "requires_model_review"
        data["requires_model_review"] = True
        result = EvidenceAuditResult.from_dict(data)
        self.assertEqual(result.taxonomy_subset_ids, ())

    def test_diagnostics_are_deep_copied(self):
        data = valid_result().to_dict()
        parsed = EvidenceAuditResult.from_dict(data)
        data["diagnostics"]["model_called"] = True
        self.assertFalse(parsed.diagnostics["model_called"])

    def test_partially_met_requirement_is_supported_by_contract(self):
        data = valid_requirement().to_dict()
        data["status"] = "partially_met"
        parsed = RequirementCheck.from_dict(data)
        self.assertEqual(parsed.status, RequirementSupport.PARTIALLY_MET)

    def test_partially_supported_component_is_supported_by_contract(self):
        data = valid_component().to_dict()
        data["support"] = "partially_supported"
        parsed = ComponentEvidenceAssessment.from_dict(data)
        self.assertEqual(parsed.support, ComponentSupport.PARTIALLY_SUPPORTED)

    def test_unknown_trace_reason_is_rejected(self):
        data = valid_result().to_dict()["taxonomy_selection_trace"][0]
        data["reasons"] = ["model_guess"]
        with self.assertRaises(AssessmentSchemaError):
            TaxonomySelectionTraceEntry.from_dict(data)

    def test_duplicate_issue_is_rejected(self):
        data = valid_result().to_dict()["current_evidence_audits"][0]
        data["issues"] = ["warning", "warning"]
        with self.assertRaises(AssessmentSchemaError):
            EvidenceSpanAudit.from_dict(data)

    def test_non_json_diagnostics_are_rejected(self):
        data = valid_result().to_dict()
        data["diagnostics"] = {"bad": {1, 2}}
        with self.assertRaises(AssessmentSchemaError):
            EvidenceAuditResult.from_dict(data)


if __name__ == "__main__":
    unittest.main()
