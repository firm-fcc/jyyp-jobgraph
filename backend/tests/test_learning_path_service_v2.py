import unittest
from dataclasses import replace

from backend.services import learning_path_service as service
from backend.config import CANDIDATE_CORE
from backend.services.target_job_service import build_target_job_profile
from extractor.candidate_matching_bridge_v1 import CandidateMatchingBridge
from extractor.learning_path_stage1 import LearningPathEngine, load_skill_development_graph
from extractor.target_job_profile_learning_bridge import TargetJobProfileLearningBridge
from extractor.targeted_learning_path_planner_v2 import LearningPathEngineV2
from extractor.team_skill_schema_v3 import CandidateSkillProfile


AI_JD_KEY = "05cf4eaf48d58138011fea774dd57ca9"
CV_JD_KEY = "1fdd3e52a9d1c8f9b727d726a688a31e"
DB_JD_KEY = "00022424db9cf3bdfee21c48d27cd984"
FALLBACK_JOB_ID = "133663124"


def empty_candidate():
    return {
        "candidate_id": "service-v2-empty",
        "skill_registry_version": "0.4",
        "assessments": [],
        "metadata": {"schema_version": "candidate_skill_profile_v4_3_4"},
    }


def supported_ai_candidate():
    text = "独立训练并验证机器学习模型。"
    return {
        "candidate_id": "service-v2-supported",
        "skill_registry_version": "0.4",
        "assessments": [
            {
                "candidate_id": "service-v2-supported",
                "team_skill_id": "T-AI-01",
                "team_skill_name": "机器学习与深度学习",
                "status": "supported",
                "inference_mode": "direct_behavior",
                "evidence": [
                    {
                        "text": text,
                        "source_experience_id": "service-v2-fixture",
                        "start": 0,
                        "end": len(text),
                        "fact": "",
                        "behavior": "",
                        "context": "",
                        "result": "",
                    }
                ],
                "reason": "offline service gate fixture",
                "confidence": 0.95,
                "atomic_abilities": ["训练并验证机器学习模型"],
                "audit_flags": [],
            }
        ],
        "metadata": {"schema_version": "candidate_skill_profile_v4_3_4"},
    }


def skill_path(response, team_skill_id):
    return next(
        path
        for path in response["rendered"]["skill_paths"]
        if path["team_skill_id"] == team_skill_id
    )


# GRAPH_UNAVAILABLE 一档所代表的是“该能力尚无 curated 图谱”这一渲染契约，与具体是
# 哪一项能力无关。config/ 下的图谱逐批补齐，若以某一项恰好尚未收录为前提，图谱一经
# 补齐该用例即失效。故另取一项，在该用例内把它的图谱排除在引擎之外。
GRAPHLESS_SKILL = "T-DA-04"


def engine_without(team_skill_id):
    graphs = [
        load_skill_development_graph(path)
        for path in sorted((CANDIDATE_CORE / "config").glob("skill_development_graph_*.json"))
    ]
    return LearningPathEngineV2([g for g in graphs if g.team_skill_id != team_skill_id])


class LearningPathServiceV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        service._learning_engine.cache_clear()

    def test_service_instantiates_v2_engine(self):
        self.assertIsInstance(service._learning_engine(), LearningPathEngineV2)

    def test_real_ai_target_reaches_service_response_without_unrelated_core(self):
        payload = service.run_learning_path(
            candidate_profile=empty_candidate(),
            jd_key=AI_JD_KEY,
            proficiency_levels={},
            auto_proficiency=False,
        )
        self.assertEqual(
            set(payload),
            {"schema_version", "path_status", "gap_summary", "rendered", "proficiency", "diagnostics"},
        )
        self.assertEqual(payload["schema_version"], "learning_path_api_response_v1")
        path = skill_path(payload, "T-AI-01")
        self.assertEqual(
            [step["node_id"] for step in path["learning_steps"]],
            ["ML-01", "ML-02", "ML-03"],
        )
        resolutions = payload["diagnostics"]["target_bridge"]["required_subskill_resolutions"]
        resolution = next(item for item in resolutions if item["team_skill_id"] == "T-AI-01")
        self.assertEqual(resolution["required_subskill_ids"], ["ML-03"])

    def test_real_db_target_preserves_complete_closure(self):
        payload = service.run_learning_path(
            candidate_profile=empty_candidate(),
            jd_key=DB_JD_KEY,
            proficiency_levels={},
            auto_proficiency=False,
        )
        path = skill_path(payload, "T-DA-02")
        self.assertEqual(
            [step["node_id"] for step in path["learning_steps"]],
            ["DB-01", "DB-02"],
        )

    def test_real_cv_target_excludes_unrelated_core(self):
        payload = service.run_learning_path(
            candidate_profile=empty_candidate(),
            jd_key=CV_JD_KEY,
            proficiency_levels={},
            auto_proficiency=False,
        )
        path = skill_path(payload, "T-AI-03")
        self.assertEqual(
            [step["node_id"] for step in path["learning_steps"]],
            ["CV-01", "CV-02", "CV-03"],
        )

    def test_empty_required_subskills_are_exact_stage1_fallback(self):
        target_profile = build_target_job_profile(job_id=FALLBACK_JOB_ID)
        bridged_target = TargetJobProfileLearningBridge().build(target_profile).target
        ai_requirement = next(
            item for item in bridged_target.requirements if item.team_skill_id == "T-AI-01"
        )
        self.assertEqual(ai_requirement.required_subskill_ids, ())
        fallback_target = replace(
            bridged_target,
            requirements=tuple(
                replace(item, required_subskill_ids=())
                for item in bridged_target.requirements
            ),
        )
        candidate = CandidateMatchingBridge().build(
            CandidateSkillProfile.from_dict(empty_candidate()),
            {},
        ).profile
        v2_result = service._learning_engine().build(candidate, fallback_target)
        graph_paths = sorted(
            (CANDIDATE_CORE / "config").glob("skill_development_graph_*.json")
        )
        stage1_result = LearningPathEngine(
            tuple(load_skill_development_graph(path) for path in graph_paths)
        ).build(candidate, fallback_target)
        self.assertEqual(v2_result.to_dict(), stage1_result.to_dict())

    def test_graph_unavailable_is_unchanged(self):
        engine = engine_without(GRAPHLESS_SKILL)
        original = service._learning_engine
        service._learning_engine = lambda: engine
        try:
            payload = service.run_learning_path(
                candidate_profile=empty_candidate(),
                job_id=FALLBACK_JOB_ID,
                proficiency_levels={},
                auto_proficiency=False,
            )
        finally:
            service._learning_engine = original
        path = skill_path(payload, GRAPHLESS_SKILL)
        self.assertEqual(path["path_status"], "GRAPH_UNAVAILABLE")
        self.assertEqual(path["path_mode"], "LEARN")
        self.assertEqual(path["learning_steps"], [])

    def test_evidence_insufficient_remains_verify_first(self):
        payload = service.run_learning_path(
            candidate_profile=supported_ai_candidate(),
            job_id=FALLBACK_JOB_ID,
            proficiency_levels={"T-AI-01": "U"},
            auto_proficiency=False,
        )
        path = skill_path(payload, "T-AI-01")
        self.assertEqual(path["gap_type"], "EVIDENCE_INSUFFICIENT")
        self.assertEqual(path["path_mode"], "VERIFY_FIRST")
        self.assertIsNotNone(path["verification_guidance"])
        self.assertEqual(path["learning_steps"], [])

    def test_satisfied_remains_no_action(self):
        payload = service.run_learning_path(
            candidate_profile=supported_ai_candidate(),
            job_id=FALLBACK_JOB_ID,
            proficiency_levels={"T-AI-01": "P4"},
            auto_proficiency=False,
        )
        path = skill_path(payload, "T-AI-01")
        self.assertEqual(path["gap_type"], "SATISFIED")
        self.assertEqual(path["path_mode"], "NONE")
        self.assertEqual(path["path_status"], "NO_ACTION")


if __name__ == "__main__":
    unittest.main()
