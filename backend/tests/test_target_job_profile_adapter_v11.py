import copy
import json
import os
import unittest
from pathlib import Path

from extractor.target_job_profile_adapter import (
    TargetJobProfileAdapter,
    TargetJobProfileError,
    _parse_skillpoint_map,
    validate_taxonomy_compatibility,
)
from extractor.target_job_profile_learning_bridge import TargetJobProfileLearningBridge
from extractor.learning_path_stage1 import (
    CandidateLearningProfile,
    GroundedEvidence,
    JobLearningTarget,
    LearningPathEngine,
    ObservedTeamSkill,
)


def _env_path(name):
    value = os.environ.get(name)
    return Path(value) if value else None


PROVIDER = _env_path("TARGETJOB_PROVIDER_TAXONOMY")
CANONICAL = _env_path("TARGETJOB_CANONICAL_TAXONOMY")
JOBS = _env_path("TARGETJOB_JOBS")
CSV = _env_path("TARGETJOB_JD_SUMMARY")
JOB_SKILL = _env_path("TARGETJOB_JOB_SKILL")
WINDOW = os.environ.get("TARGETJOB_WINDOW", "2022-10")


def _real_data_available():
    required = (PROVIDER, CANONICAL, JOBS, CSV)
    return all(path is not None and path.exists() for path in required)


def _adapter():
    if not _real_data_available():
        raise unittest.SkipTest("set TARGETJOB_* paths to run real-data adapter tests")
    return TargetJobProfileAdapter.from_paths(
        provider_taxonomy_path=PROVIDER,
        canonical_taxonomy_path=CANONICAL,
        jobs_path=JOBS,
        jd_summary_csv=CSV,
        job_skill_path=JOB_SKILL if JOB_SKILL and JOB_SKILL.exists() else None,
        window=WINDOW,
    )


def _observed(skill_id, name, level):
    text = "完成了可归因的目标技术行为。"
    evidence = GroundedEvidence(text, "resume", 0, len(text))
    return ObservedTeamSkill(
        team_skill_id=skill_id,
        team_skill_name=name,
        evidence=(evidence,),
        observed_capabilities=("已观察能力",),
        observed_proficiency=level,
    )


@unittest.skipUnless(_real_data_available(), "real algorithm-group graph/JD paths not configured")
class AdapterV11RealDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = _adapter().build_single_jd(jobid="133663124")
        cls.skills = {x["team_skill_id"]: x for x in cls.out["skills"]}
        cls.bridged = TargetJobProfileLearningBridge().build(cls.out)
        cls.req = {x.team_skill_id: x for x in cls.bridged.target.requirements}

    def test_real_dual_taxonomy_gate(self):
        self.assertEqual(self.out["taxonomy"]["taxonomy_compatibility"]["status"], "PASS")
        self.assertEqual(self.out["taxonomy"]["identity_rule"], "team_skill_id")

    def test_real_algorithm_engineer_levels(self):
        self.assertEqual(self.skills["T-AI-01"]["required_level"], "P3")
        self.assertEqual(self.skills["T-AI-03"]["required_level"], "P3")
        self.assertEqual(self.skills["T-DA-01"]["required_level"], "P2")
        self.assertEqual(self.skills["T-SW-01"]["required_level"], "P3")
        for sid in ("T-DA-04", "T-SW-03", "T-SW-04"):
            self.assertEqual(self.skills[sid]["requirement_status"], "LEVEL_UNSPECIFIED")
            self.assertIsNone(self.skills[sid]["required_level"])
            self.assertTrue(self.skills[sid]["learning_path_target_eligible"])
            self.assertFalse(self.skills[sid]["level_comparison_eligible"])
        self.assertEqual(self.skills["F-1-01"]["requirement_status"], "AUXILIARY_NOT_GRADED")
        self.assertNotIn("F-1-01", self.req)

    def test_real_skillpoints_are_scoped_to_their_team_skill(self):
        self.assertEqual(
            self.skills["T-AI-01"]["skill_points"],
            ["TensorFlow", "Caffe", "PyTorch", "MXNet", "Keras"],
        )
        self.assertEqual(
            self.skills["T-AI-03"]["skill_points"],
            ["OpenCV", "Halcon"],
        )
        self.assertNotIn("OpenCV", self.skills["T-AI-01"]["skill_points"])
        self.assertNotIn("PyTorch", self.skills["T-AI-03"]["skill_points"])
        self.assertEqual(
            self.skills["T-AI-03"]["skill_point_evidence_ref"],
            "structured_jd_summary:2022-10:2e48512d369aa7c71d7c75bc7f3b4104:"
            "skillpoint_map:T-AI-03",
        )
        self.assertTrue(self.out["source_provenance"]["skillpoint_map_available"])
        self.assertFalse(self.out["source_provenance"]["raw_jd_evidence_available"])
        self.assertIn("market_signal", self.skills["T-AI-03"])
        self.assertIn("requirement_evidence_ref", self.skills["T-AI-03"])
        self.assertEqual(self.out["job"]["techstack"], "AI/ML 与数据智能")

    def test_real_empty_skillpoint_map_adds_empty_lists_only(self):
        out = _adapter().build_single_jd(jobid="115511976")
        self.assertTrue(out["skills"])
        self.assertTrue(all(skill["skill_points"] == [] for skill in out["skills"]))
        self.assertTrue(
            all("skill_point_evidence_ref" not in skill for skill in out["skills"])
        )
        self.assertFalse(out["source_provenance"]["skillpoint_map_available"])
        self.assertFalse(out["source_provenance"]["raw_jd_evidence_available"])

    def test_real_missing_primary_proficiency_is_excluded_not_u(self):
        out = _adapter().build_single_jd(jobid="127281459")
        skills = {x["team_skill_id"]: x for x in out["skills"]}
        for sid in ("T-DA-02", "T-SW-01", "T-SW-04"):
            self.assertEqual(skills[sid]["requirement_status"], "PROFICIENCY_NOT_AVAILABLE")
            self.assertIsNone(skills[sid]["required_level_raw"])
            self.assertFalse(skills[sid]["learning_path_target_eligible"])
        bridged = TargetJobProfileLearningBridge().build(out)
        included = {x.team_skill_id for x in bridged.target.requirements}
        self.assertTrue({"T-DA-02", "T-SW-01", "T-SW-04"}.isdisjoint(included))

    def test_bridge_gap_semantics(self):
        engine = LearningPathEngine(())
        explicit = self.req["T-AI-01"]
        target = JobLearningTarget(self.bridged.target.job_id, self.bridged.target.job_title, (explicit,))
        p2 = CandidateLearningProfile("c", (_observed("T-AI-01", "机器学习与深度学习", "P2"),))
        p3 = CandidateLearningProfile("c", (_observed("T-AI-01", "机器学习与深度学习", "P3"),))
        self.assertEqual(engine.build(p2, target).gap_items[0].gap_type.value, "LEVEL_GAP")
        self.assertEqual(engine.build(p3, target).gap_items[0].gap_type.value, "SATISFIED")

        unspecified = self.req["T-SW-03"]
        target_u = JobLearningTarget(self.bridged.target.job_id, self.bridged.target.job_title, (unspecified,))
        p2_u = CandidateLearningProfile("c", (_observed("T-SW-03", "软件架构与系统设计", "P2"),))
        cand_u = CandidateLearningProfile("c", (_observed("T-SW-03", "软件架构与系统设计", "U"),))
        missing = CandidateLearningProfile("c")
        self.assertEqual(engine.build(p2_u, target_u).gap_items[0].gap_type.value, "SATISFIED")
        self.assertEqual(engine.build(cand_u, target_u).gap_items[0].gap_type.value, "EVIDENCE_INSUFFICIENT")
        self.assertEqual(engine.build(missing, target_u).gap_items[0].gap_type.value, "MISSING")


class AdapterV11SyntheticContractTests(unittest.TestCase):
    def test_semantic_taxonomy_drift_rejected_but_name_drift_allowed(self):
        canonical = {
            "detail": {
                "T-X-01": {"code": "T-X-01", "name_zh": "旧名", "definition": "D", "skill_type": "hard"}
            }
        }
        provider = copy.deepcopy(canonical)
        provider["detail"]["T-X-01"]["name_zh"] = "新名"
        self.assertEqual(validate_taxonomy_compatibility(provider, canonical)["status"], "PASS")
        for field, value in (("definition", "drift"), ("skill_type", "other")):
            bad = copy.deepcopy(provider)
            bad["detail"]["T-X-01"][field] = value
            with self.assertRaises(TargetJobProfileError):
                validate_taxonomy_compatibility(bad, canonical)
        missing = {"detail": {}}
        with self.assertRaises(TargetJobProfileError):
            validate_taxonomy_compatibility(missing, canonical)

    def test_bridge_rejects_unfrozen_or_ineligible_contract(self):
        base = {
            "schema_version": "target_job_profile_v1.1",
            "taxonomy": {"taxonomy_compatibility": {"status": "PASS"}},
            "job": {"jobid": "1", "title": "岗位"},
            "skills": [{
                "team_skill_id": "T-X-01",
                "is_primary": True,
                "requirement_status": "EXPLICIT_LEVEL",
                "required_level": "P3",
                "learning_path_target_eligible": True,
                "requirement_evidence_ref": "structured_jd_summary:w:k:skill_vec_01:T-X-01",
            }],
        }
        bridged = TargetJobProfileLearningBridge().build(base)
        req = bridged.target.requirements[0]
        self.assertEqual(req.requirement_type, "core")
        self.assertEqual(req.required_level, "P3")
        self.assertEqual(req.required_capabilities, ())
        self.assertEqual(req.required_subskill_ids, ())
        self.assertIsNone(req.market_trend_rank)

        bad = copy.deepcopy(base)
        bad["skills"][0]["requirement_status"] = "PROFICIENCY_NOT_AVAILABLE"
        bad["skills"][0]["learning_path_target_eligible"] = True
        with self.assertRaises(Exception):
            TargetJobProfileLearningBridge().build(bad)


class SkillpointMapParserTests(unittest.TestCase):
    BY_NAME = {
        "机器学习与深度学习": "T-AI-01",
        "计算机视觉与多模态": "T-AI-03",
        "数据管理与数据库": "T-DA-02",
    }

    def test_multiple_team_skills_are_parsed_and_points_stay_scoped(self):
        parsed = _parse_skillpoint_map(
            "机器学习与深度学习:TensorFlow,CNN;"
            "计算机视觉与多模态:OpenCV,YOLO;"
            "数据管理与数据库:MySQL,SQL",
            self.BY_NAME,
        )
        self.assertEqual(parsed["T-AI-01"], ("TensorFlow", "CNN"))
        self.assertEqual(parsed["T-AI-03"], ("OpenCV", "YOLO"))
        self.assertEqual(parsed["T-DA-02"], ("MySQL", "SQL"))

    def test_empty_map_and_stable_point_deduplication(self):
        self.assertEqual(_parse_skillpoint_map("", self.BY_NAME), {})
        self.assertEqual(_parse_skillpoint_map(None, self.BY_NAME), {})
        parsed = _parse_skillpoint_map(
            "数据管理与数据库: SQL,MySQL,SQL, ,MySQL ",
            self.BY_NAME,
        )
        self.assertEqual(parsed, {"T-DA-02": ("SQL", "MySQL")})

    def test_unknown_duplicate_and_malformed_maps_fail_closed(self):
        invalid_values = (
            "不存在的技能:YOLO",
            "机器学习与深度学习:CNN;机器学习与深度学习:DNN",
            "机器学习与深度学习",
            "机器学习与深度学习:CNN;;计算机视觉与多模态:YOLO",
        )
        for raw in invalid_values:
            with self.subTest(raw=raw):
                with self.assertRaises(TargetJobProfileError):
                    _parse_skillpoint_map(raw, self.BY_NAME)


if __name__ == "__main__":
    unittest.main()
