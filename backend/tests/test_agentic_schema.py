import json
import unittest

from extractor.agentic_schema import (
    CandidateAbility,
    CandidateStatus,
    ControlAction,
    Evidence,
    ReviewResult,
    ReviewStatus,
    SchemaValidationError,
)


def valid_candidate_data():
    return {
        "candidate_id": "candidate-001",
        "resume_id": "resume-001",
        "project_id": "project-001",
        "fact": "完成了训练集清洗",
        "behavior": "编写脚本处理缺失值和异常值",
        "ability": "数据清洗",
        "normalized_ability": "数据清洗",
        "category": {"level1": "数据与工程能力", "level2": "数据处理"},
        "evidence": [
            {
                "text": "编写脚本处理缺失值和异常值",
                "project_id": "project-001",
                "start": 10,
                "end": 25,
            }
        ],
        "reason": "明确实施了数据质量处理",
        "confidence": 0.9,
        "source": "agentic_extractor",
        "revision_round": 0,
        "parent_candidate_id": None,
        "status": "pending_review",
    }


def valid_review_data():
    return {
        "candidate_id": "candidate-001",
        "status": "failed",
        "error_types": ["bad_name"],
        "action": "rename",
        "reason": "名称不够标准",
        "target_ability": "数据清洗",
        "target_evidence": [],
        "merge_target_id": None,
    }


class CandidateAbilitySchemaTests(unittest.TestCase):
    def test_valid_candidate_round_trip(self):
        candidate = CandidateAbility.from_dict(valid_candidate_data())

        self.assertEqual(candidate.candidate_id, "candidate-001")
        self.assertIs(candidate.status, CandidateStatus.PENDING_REVIEW)
        self.assertIsInstance(candidate.evidence[0], Evidence)
        self.assertEqual(candidate.lineage, ["candidate-001"])
        self.assertEqual(
            CandidateAbility.from_dict(candidate.to_dict()).to_dict(),
            candidate.to_dict(),
        )

    def test_missing_required_field_is_rejected(self):
        data = valid_candidate_data()
        del data["candidate_id"]

        with self.assertRaises(SchemaValidationError):
            CandidateAbility.from_dict(data)

    def test_unknown_field_is_rejected(self):
        data = valid_candidate_data()
        data["unexpected"] = "value"

        with self.assertRaises(SchemaValidationError):
            CandidateAbility.from_dict(data)

    def test_invalid_enum_is_rejected(self):
        data = valid_candidate_data()
        data["status"] = "unknown_status"

        with self.assertRaises(SchemaValidationError):
            CandidateAbility.from_dict(data)

    def test_confidence_must_be_between_zero_and_one(self):
        for confidence in (-0.01, 1.01, True, "0.9"):
            with self.subTest(confidence=confidence):
                data = valid_candidate_data()
                data["confidence"] = confidence
                with self.assertRaises(SchemaValidationError):
                    CandidateAbility.from_dict(data)

    def test_revision_round_cannot_exceed_one(self):
        data = valid_candidate_data()
        data["revision_round"] = 2

        with self.assertRaises(SchemaValidationError):
            CandidateAbility.from_dict(data)

    def test_revision_round_rejects_boolean_values(self):
        for revision_round in (True, False):
            with self.subTest(revision_round=revision_round):
                data = valid_candidate_data()
                data["revision_round"] = revision_round
                with self.assertRaises(SchemaValidationError):
                    CandidateAbility.from_dict(data)

    def test_candidate_id_cannot_be_empty(self):
        data = valid_candidate_data()
        data["candidate_id"] = "   "

        with self.assertRaises(SchemaValidationError):
            CandidateAbility.from_dict(data)

    def test_parent_candidate_id_cannot_equal_candidate_id(self):
        data = valid_candidate_data()
        data["parent_candidate_id"] = data["candidate_id"]

        with self.assertRaises(SchemaValidationError):
            CandidateAbility.from_dict(data)

    def test_lineage_rejects_duplicate_and_empty_ids(self):
        invalid_lineages = (
            ["candidate-001", "candidate-001"],
            ["candidate-001", ""],
            ["candidate-001", "   "],
        )
        for lineage in invalid_lineages:
            with self.subTest(lineage=lineage):
                data = valid_candidate_data()
                data["lineage"] = lineage
                with self.assertRaises(SchemaValidationError):
                    CandidateAbility.from_dict(data)

    def test_missing_lineage_is_recovered_but_explicit_missing_id_is_rejected(self):
        recovered = CandidateAbility.from_dict(valid_candidate_data())
        self.assertEqual(recovered.lineage, ["candidate-001"])

        for lineage in ([], ["ancestor-001"]):
            with self.subTest(lineage=lineage):
                data = valid_candidate_data()
                data["lineage"] = lineage
                with self.assertRaises(SchemaValidationError):
                    CandidateAbility.from_dict(data)

    def test_json_serialization_round_trip_is_complete(self):
        candidate = CandidateAbility.from_dict(valid_candidate_data())

        encoded = json.dumps(candidate.to_dict(), ensure_ascii=False)
        restored = CandidateAbility.from_dict(json.loads(encoded))

        self.assertEqual(restored.to_dict(), candidate.to_dict())

    def test_confidence_rejects_non_finite_values(self):
        for confidence in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(confidence=confidence):
                data = valid_candidate_data()
                data["confidence"] = confidence
                with self.assertRaises(SchemaValidationError):
                    CandidateAbility.from_dict(data)

    def test_evidence_rejects_invalid_spans(self):
        invalid_spans = (
            (-1, 2),
            (3, 2),
            (3, 3),
            (True, 2),
            (0, False),
            (0, None),
        )
        for start, end in invalid_spans:
            with self.subTest(start=start, end=end):
                with self.assertRaises(SchemaValidationError):
                    Evidence(
                        text="证据",
                        project_id="project-001",
                        start=start,
                        end=end,
                    )

    def test_evidence_must_be_a_list_of_structured_items(self):
        invalid_values = (
            "not-a-list",
            ("tuple-is-not-accepted",),
            ["plain strings are not evidence objects"],
            [{"text": "missing project id"}],
        )
        for evidence in invalid_values:
            with self.subTest(evidence=evidence):
                data = valid_candidate_data()
                data["evidence"] = evidence
                with self.assertRaises(SchemaValidationError):
                    CandidateAbility.from_dict(data)


class ReviewResultSchemaTests(unittest.TestCase):
    def test_valid_failed_review(self):
        review = ReviewResult.from_dict(valid_review_data())

        self.assertIs(review.status, ReviewStatus.FAILED)
        self.assertIs(review.action, ControlAction.RENAME)

    def test_invalid_review_enums_are_rejected(self):
        for field, value in (
            ("status", "maybe"),
            ("action", "rewrite_everything"),
            ("error_types", ["unknown_error"]),
        ):
            with self.subTest(field=field):
                data = valid_review_data()
                data[field] = value
                with self.assertRaises(SchemaValidationError):
                    ReviewResult.from_dict(data)

    def test_passed_review_must_be_keep_without_errors(self):
        data = valid_review_data()
        data.update(
            {
                "status": "passed",
                "error_types": [],
                "action": "keep",
                "target_ability": None,
            }
        )
        review = ReviewResult.from_dict(data)
        self.assertIs(review.status, ReviewStatus.PASSED)

        data["action"] = "delete"
        with self.assertRaises(SchemaValidationError):
            ReviewResult.from_dict(data)

    def test_failed_review_requires_an_error(self):
        data = valid_review_data()
        data["error_types"] = []

        with self.assertRaises(SchemaValidationError):
            ReviewResult.from_dict(data)

    def test_target_ability_rejects_blank_string(self):
        data = valid_review_data()
        data["target_ability"] = "   "

        with self.assertRaises(SchemaValidationError):
            ReviewResult.from_dict(data)


if __name__ == "__main__":
    unittest.main()
