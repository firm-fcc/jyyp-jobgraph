import unittest
from pathlib import Path

from extractor.learning_path_stage1 import (
    AchievedSubskill,
    CandidateLearningProfile,
    GapItem,
    GapType,
    GroundedEvidence,
    JobLearningTarget,
    JobSkillRequirement,
    LearningPathEngine,
    ObservedTeamSkill,
    PathMode,
    load_skill_development_graph,
)
from extractor.targeted_learning_path_planner_v2 import (
    LearningPathEngineV2,
    TargetedDeterministicPathPlannerV2,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1] / "candidate_core"
GRAPH_PATHS = (
    PROJECT_ROOT / "config" / "skill_development_graph_t_ai_01_ml_dl_curated_v1.json",
    PROJECT_ROOT / "config" / "skill_development_graph_t_ai_02_nlp_curated_v1.json",
    PROJECT_ROOT / "config" / "skill_development_graph_t_ai_03_cv_multimodal_curated_v1.json",
    PROJECT_ROOT / "config" / "skill_development_graph_t_ai_07_rag_mvp_v1.json",
    PROJECT_ROOT / "config" / "skill_development_graph_t_ai_08_agents_curated_v1.json",
    PROJECT_ROOT / "config" / "skill_development_graph_t_ai_10_llm_finetuning_curated_v1.json",
    PROJECT_ROOT / "config" / "skill_development_graph_t_da_02_data_database_curated_v1.json",
    PROJECT_ROOT / "config" / "skill_development_graph_t_sw_01_software_engineering_curated_v1.json",
)


class TargetedLearningPathPlannerV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graphs = tuple(load_skill_development_graph(path) for path in GRAPH_PATHS)
        cls.by_id = {graph.team_skill_id: graph for graph in cls.graphs}
        cls.stage1 = LearningPathEngine(cls.graphs)
        cls.v2 = LearningPathEngineV2(cls.graphs)

    @staticmethod
    def requirement(team_skill_id, required_subskill_ids=(), required_capabilities=()):
        return JobSkillRequirement(
            team_skill_id=team_skill_id,
            requirement_type="core",
            required_level="P3",
            requirement_evidence=("岗位要求该项能力",),
            required_capabilities=tuple(required_capabilities),
            required_subskill_ids=tuple(required_subskill_ids),
        )

    @staticmethod
    def target(requirement):
        return JobLearningTarget(
            job_id="job",
            job_title="工程师",
            requirements=(requirement,),
        )

    @staticmethod
    def step_ids(result):
        return tuple(step.subskill_id for step in result.paths[0].ordered_steps)

    @staticmethod
    def grounded():
        text = "本人完成了可复现的技术任务并记录输入输出和评价。"
        return GroundedEvidence(text=text, source_id="resume", start=8, end=8 + len(text))

    @classmethod
    def observed(cls, graph, level="P2", achieved_ids=(), capabilities=("已观察能力",)):
        evidence = cls.grounded()
        return ObservedTeamSkill(
            team_skill_id=graph.team_skill_id,
            team_skill_name=graph.team_skill_name,
            evidence=(evidence,),
            observed_capabilities=tuple(capabilities),
            observed_proficiency=level,
            achieved_subskills=tuple(
                AchievedSubskill(
                    subskill_id=subskill_id,
                    evidence_refs=(evidence.reference_id,),
                    mapping_basis="direct_behavior",
                )
                for subskill_id in achieved_ids
            ),
        )

    def build_v2(self, requirement, candidate=None):
        return self.v2.build(
            candidate or CandidateLearningProfile(candidate_id="candidate"),
            self.target(requirement),
        )

    def test_ml_target_contains_only_required_node_and_prerequisite_closure(self):
        result = self.build_v2(self.requirement("T-AI-01", ("ML-03",)))
        self.assertEqual(self.step_ids(result), ("ML-01", "ML-02", "ML-03"))
        self.assertTrue({"ML-04", "ML-05", "ML-06"}.isdisjoint(self.step_ids(result)))

    def test_nlp_transformer_target_contains_only_its_closure(self):
        result = self.build_v2(self.requirement("T-AI-02", ("NLP-03",)))
        self.assertEqual(self.step_ids(result), ("NLP-01", "NLP-02", "NLP-03"))
        self.assertTrue({"NLP-04", "NLP-05", "NLP-06"}.isdisjoint(self.step_ids(result)))

    def test_nlp_speech_target_contains_only_its_closure(self):
        result = self.build_v2(self.requirement("T-AI-02", ("NLP-05",)))
        self.assertEqual(self.step_ids(result), ("NLP-01", "NLP-02", "NLP-05"))
        self.assertTrue({"NLP-03", "NLP-04", "NLP-06"}.isdisjoint(self.step_ids(result)))

    def test_cv_specialization_target_contains_only_its_closure(self):
        result = self.build_v2(self.requirement("T-AI-03", ("CV-03",)))
        self.assertEqual(self.step_ids(result), ("CV-01", "CV-02", "CV-03"))

    def test_cv_core_target_does_not_add_unrelated_nodes(self):
        result = self.build_v2(self.requirement("T-AI-03", ("CV-02",)))
        self.assertEqual(self.step_ids(result), ("CV-01", "CV-02"))

    def test_multiple_targets_use_stable_deduplicated_closure_union(self):
        expected = ("CV-01", "CV-02", "CV-03", "CV-04")
        first = self.build_v2(self.requirement("T-AI-03", ("CV-04", "CV-03")))
        second = self.build_v2(self.requirement("T-AI-03", ("CV-03", "CV-04")))
        self.assertEqual(self.step_ids(first), expected)
        self.assertEqual(self.step_ids(second), expected)

    def test_software_targets_use_only_required_closure(self):
        cases = (
            ("SWE-01", ("SWE-01",)),
            ("SWE-02", ("SWE-01", "SWE-02")),
            ("SWE-03", ("SWE-01", "SWE-03")),
            ("SWE-04", ("SWE-01", "SWE-04")),
            ("SWE-05", ("SWE-01", "SWE-05")),
        )
        for required_id, expected in cases:
            with self.subTest(required_id=required_id):
                result = self.build_v2(self.requirement("T-SW-01", (required_id,)))
                self.assertEqual(self.step_ids(result), expected)
                self.assertNotIn("SWE-06", self.step_ids(result))

    def test_software_multiple_targets_use_only_closure_union(self):
        result = self.build_v2(
            self.requirement("T-SW-01", ("SWE-04", "SWE-03"))
        )
        self.assertEqual(self.step_ids(result), ("SWE-01", "SWE-03", "SWE-04"))
        self.assertTrue(
            {"SWE-02", "SWE-05", "SWE-06"}.isdisjoint(self.step_ids(result))
        )

    def test_software_empty_targets_use_core_stage1_fallback(self):
        requirement = self.requirement("T-SW-01")
        target = self.target(requirement)
        candidate = CandidateLearningProfile(candidate_id="software-fallback")
        v2_result = self.v2.build(candidate, target)
        self.assertEqual(v2_result.to_dict(), self.stage1.build(candidate, target).to_dict())
        self.assertEqual(self.step_ids(v2_result), ("SWE-01", "SWE-02", "SWE-06"))

    def test_candidate_mastery_skip_matches_stage1_semantics(self):
        graph = self.by_id["T-AI-01"]
        candidate = CandidateLearningProfile(
            candidate_id="experienced",
            supported_team_skills=(
                self.observed(graph, achieved_ids=("ML-01", "ML-02")),
            ),
        )
        result = self.build_v2(self.requirement("T-AI-01", ("ML-03",)), candidate)
        self.assertEqual(result.paths[0].mode, PathMode.DEEPEN)
        self.assertEqual(self.step_ids(result), ("ML-03",))

    def test_empty_targets_are_exact_stage1_fallback(self):
        candidate = CandidateLearningProfile(candidate_id="fallback")
        for graph in self.graphs:
            with self.subTest(skill=graph.team_skill_id):
                requirement = self.requirement(graph.team_skill_id)
                target = self.target(requirement)
                self.assertEqual(
                    self.v2.build(candidate, target).to_dict(),
                    self.stage1.build(candidate, target).to_dict(),
                )

    def test_evidence_insufficient_keeps_verify_first_semantics(self):
        graph = self.by_id["T-AI-03"]
        candidate = CandidateLearningProfile(
            candidate_id="verify",
            supported_team_skills=(self.observed(graph, level="U"),),
        )
        result = self.build_v2(self.requirement("T-AI-03", ("CV-03",)), candidate)
        self.assertEqual(result.gap_items[0].gap_type, GapType.EVIDENCE_INSUFFICIENT)
        self.assertEqual(result.paths[0].mode, PathMode.VERIFY_FIRST)
        self.assertEqual(len(result.paths[0].ordered_steps), 1)
        self.assertEqual(result.paths[0].ordered_steps[0].subskill_id, graph.verification_task.task_id)

    def test_graph_unavailable_is_preserved(self):
        result = self.build_v2(self.requirement("T-SYS-04"))
        self.assertEqual(result.paths[0].path_status, "GRAPH_UNAVAILABLE")
        self.assertEqual(result.paths[0].ordered_steps, ())

    def test_invalid_required_id_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "required subskill is not present in graph"):
            self.build_v2(self.requirement("T-AI-01", ("NOT-A-NODE",)))

    def test_planner_itself_rejects_invalid_required_id(self):
        graph = self.by_id["T-AI-01"]
        item = GapItem(
            team_skill_id=graph.team_skill_id,
            team_skill_name=graph.team_skill_name,
            requirement_type="core",
            required_level="P3",
            observed_level=None,
            gap_type=GapType.MISSING,
            path_mode=PathMode.LEARN,
            required_capabilities=(),
            unverified_capabilities=(),
            unlock_value=0,
            market_trend_rank=None,
            explanation="test",
            required_subskill_ids=("NOT-A-NODE",),
        )
        planner = TargetedDeterministicPathPlannerV2({graph.team_skill_id: graph})
        with self.assertRaisesRegex(ValueError, "required subskill is not present in graph"):
            planner.plan(item)

    def test_capstone_extensions_are_limited_to_selected_specializations(self):
        result = self.build_v2(self.requirement("T-AI-01", ("ML-03",)))
        capstone = result.paths[0].capstone_evidence_task
        self.assertIsNotNone(capstone)
        self.assertEqual(
            tuple(extension.subskill_id for extension in capstone.specialization_extensions),
            ("ML-03",),
        )
        core_only = self.build_v2(self.requirement("T-AI-03", ("CV-02",)))
        core_capstone = core_only.paths[0].capstone_evidence_task
        self.assertIsNotNone(core_capstone)
        self.assertEqual(core_capstone.specialization_extensions, ())


if __name__ == "__main__":
    unittest.main()
