import json
import unittest
from pathlib import Path

from extractor.learning_path_stage1 import (
    AchievedSubskill,
    CandidateLearningProfile,
    DevelopmentNode,
    ExplicitSkillMention,
    GapType,
    GraphVerificationTask,
    GroundedEvidence,
    JobLearningTarget,
    JobSkillRequirement,
    LearningPathEngine,
    ObservedTeamSkill,
    PathMode,
    SkillDevelopmentGraph,
    load_skill_development_graph,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1] / "candidate_core"
FIXTURE_PATH = PROJECT_ROOT / "config" / "skill_development_graph_t_ai_07_rag_mvp_v1.json"


def evidence(text="独立构建并验证了可追溯的RAG流程"):
    return GroundedEvidence(text=text, source_id="resume", start=10, end=10 + len(text))


def observed(level, capabilities=("构建RAG流程",), achieved_subskill_ids=()):
    grounded = evidence()
    return ObservedTeamSkill(
        team_skill_id="T-AI-07",
        team_skill_name="RAG检索增强生成与向量数据库",
        evidence=(grounded,),
        observed_capabilities=tuple(capabilities),
        observed_proficiency=level,
        achieved_subskills=tuple(
            AchievedSubskill(
                subskill_id=subskill_id,
                evidence_refs=(grounded.reference_id,),
                mapping_basis="direct_behavior",
            )
            for subskill_id in achieved_subskill_ids
        ),
    )


def requirement(
    skill_id="T-AI-07",
    *,
    requirement_type="core",
    required_level="P2",
    capabilities=(),
    market_trend_rank=None,
):
    return JobSkillRequirement(
        team_skill_id=skill_id,
        requirement_type=requirement_type,
        required_level=required_level,
        requirement_evidence=(f"岗位要求 {skill_id}",),
        required_capabilities=tuple(capabilities),
        market_trend_rank=market_trend_rank,
    )


def target(*requirements):
    return JobLearningTarget(job_id="job_rag", job_title="RAG Engineer", requirements=tuple(requirements))


def node(node_id, prerequisites=(), node_type="core"):
    return DevelopmentNode(
        subskill_id=node_id,
        name_zh=node_id,
        definition=f"definition {node_id}",
        node_type=node_type,
        prerequisites=tuple(prerequisites),
        learning_outcome=f"learn {node_id}",
        evidence_task=f"verify {node_id}",
        validation_criteria=("artifact is attributable",),
        source_references=("MVP fixture reference",),
    )


def verification_task():
    return GraphVerificationTask(
        task_id="VERIFY",
        name_zh="集成验证",
        evidence_task="produce integrated evidence",
        validation_criteria=("evidence is attributable",),
        source_references=("test citation metadata",),
    )


def branch_graph():
    return SkillDevelopmentGraph(
        graph_version="stage2b1_test",
        team_skill_id="T-BRANCH-01",
        team_skill_name="Branch-aware Test Skill",
        coverage_scope="focused branch-selection test graph",
        subskill_nodes=(
            node("core-01"),
            node("core-02", ("core-01",)),
            node("spec-a-base", ("core-01",), "specialization"),
            node("spec-a", ("spec-a-base",), "specialization"),
            node("spec-b", ("core-02",), "specialization"),
        ),
        verification_task=verification_task(),
    )


def branch_requirement(required_subskill_ids=()):
    return JobSkillRequirement(
        team_skill_id="T-BRANCH-01",
        requirement_type="core",
        required_level="P3",
        requirement_evidence=("branch-aware test requirement",),
        required_subskill_ids=tuple(required_subskill_ids),
    )


def branch_target(required_subskill_ids=()):
    return JobLearningTarget(
        job_id="branch_job",
        job_title="Branch Test",
        requirements=(branch_requirement(required_subskill_ids),),
    )


class LearningPathStage1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = load_skill_development_graph(FIXTURE_PATH)
        cls.engine = LearningPathEngine((cls.graph,))

    def test_fixture_is_curated_six_node_rag_dag(self):
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(self.graph.team_skill_id, "T-AI-07")
        self.assertIn("RAG", self.graph.coverage_scope)
        self.assertEqual(
            [node.subskill_id for node in self.graph.topological_nodes()],
            ["RAG-01", "RAG-02", "RAG-03", "RAG-04", "RAG-05", "RAG-06"],
        )
        self.assertEqual(len(payload["nodes"]), 6)
        self.assertEqual(self.graph.verification_task.task_id, "RAG-VERIFY-01")
        self.assertEqual(self.graph.selection_protocol, "core_plus_required_v1")
        self.assertIsNotNone(self.graph.capstone_evidence_task)
        self.assertEqual(self.graph.capstone_evidence_task.task_id, "RAG-CAPSTONE-01")
        self.assertEqual(
            self.graph.capstone_evidence_task.purpose,
            "generate_behavioral_evidence_for_reassessment",
        )

    def test_missing_skill_produces_learn_path(self):
        result = self.engine.build(CandidateLearningProfile("candidate_missing"), target(requirement()))
        self.assertEqual(result.gap_items[0].gap_type, GapType.MISSING)
        self.assertEqual(result.paths[0].mode, PathMode.LEARN)
        self.assertEqual(len(result.paths[0].ordered_steps), 5)
        self.assertEqual(result.path_status, "READY")

    def test_lower_observed_proficiency_produces_deepen(self):
        profile = CandidateLearningProfile(
            "candidate_lower",
            (observed("P1", achieved_subskill_ids=("RAG-01", "RAG-02")),),
        )
        result = self.engine.build(profile, target(requirement(required_level="P3")))
        self.assertEqual(result.gap_items[0].gap_type, GapType.LEVEL_GAP)
        self.assertEqual(result.paths[0].mode, PathMode.DEEPEN)
        self.assertEqual(
            [step.subskill_id for step in result.paths[0].ordered_steps],
            ["RAG-03", "RAG-04", "RAG-06"],
        )
        self.assertEqual(
            [item.subskill_id for item in result.paths[0].achieved_subskills],
            ["RAG-01", "RAG-02"],
        )

    def test_u_produces_verify_first_not_learn_from_zero(self):
        profile = CandidateLearningProfile("candidate_u", (observed("U"),))
        result = self.engine.build(profile, target(requirement(required_level="P2")))
        self.assertEqual(result.gap_items[0].gap_type, GapType.EVIDENCE_INSUFFICIENT)
        self.assertEqual(result.paths[0].mode, PathMode.VERIFY_FIRST)
        self.assertEqual(len(result.paths[0].ordered_steps), 1)
        self.assertEqual(result.paths[0].ordered_steps[0].subskill_id, "RAG-VERIFY-01")
        self.assertNotIn(
            result.paths[0].ordered_steps[0].subskill_id,
            {node.subskill_id for node in self.graph.subskill_nodes},
        )

        unspecified = self.engine.build(profile, target(requirement(required_level=None)))
        self.assertEqual(unspecified.gap_items[0].gap_type, GapType.EVIDENCE_INSUFFICIENT)
        self.assertEqual(unspecified.paths[0].mode, PathMode.VERIFY_FIRST)

    def test_satisfied_produces_none(self):
        profile = CandidateLearningProfile("candidate_ready", (observed("P3"),))
        result = self.engine.build(profile, target(requirement(required_level="P2")))
        self.assertEqual(result.gap_items[0].gap_type, GapType.SATISFIED)
        self.assertEqual(result.paths[0].mode, PathMode.NONE)
        self.assertEqual(result.paths[0].ordered_steps, ())
        self.assertEqual(result.path_status, "NO_ACTION")

    def test_explicit_mention_alone_does_not_satisfy_skill(self):
        mention = ExplicitSkillMention(text="FAISS", team_skill_id="T-AI-07", evidence=evidence("FAISS"))
        profile = CandidateLearningProfile("candidate_mention", explicit_mentions=(mention,))
        result = self.engine.build(profile, target(requirement()))
        self.assertEqual(result.gap_items[0].gap_type, GapType.MISSING)
        self.assertEqual(result.paths[0].mode, PathMode.LEARN)
        self.assertEqual(len(result.paths[0].ordered_steps), 5)
        self.assertEqual(result.paths[0].achieved_subskills, ())
        self.assertEqual(mention.to_dict()["semantic_role"], "exposure_only")

    def test_parent_support_alone_does_not_prune_nodes(self):
        profile = CandidateLearningProfile("candidate_parent_only", (observed("P1"),))
        result = self.engine.build(profile, target(requirement(required_level="P3")))
        self.assertEqual(result.paths[0].mode, PathMode.DEEPEN)
        self.assertEqual(
            [step.subskill_id for step in result.paths[0].ordered_steps],
            ["RAG-01", "RAG-02", "RAG-03", "RAG-04", "RAG-06"],
        )

    def test_achieved_child_does_not_infer_prerequisite_mastery(self):
        profile = CandidateLearningProfile(
            "candidate_child",
            (observed("P1", achieved_subskill_ids=("RAG-03",)),),
        )
        result = self.engine.build(profile, target(requirement(required_level="P3")))
        step_ids = [step.subskill_id for step in result.paths[0].ordered_steps]
        self.assertNotIn("RAG-03", step_ids)
        self.assertIn("RAG-01", step_ids)
        self.assertIn("RAG-02", step_ids)
        self.assertEqual(result.paths[0].ordered_steps[-1].prerequisites, ("RAG-04",))

    def test_invalid_achievement_mapping_basis_and_evidence_refs_are_rejected(self):
        grounded = evidence()
        with self.assertRaisesRegex(ValueError, "mapping_basis must be direct_behavior"):
            AchievedSubskill("RAG-01", (grounded.reference_id,), "explicit_mention")
        with self.assertRaisesRegex(ValueError, "not grounded"):
            ObservedTeamSkill(
                team_skill_id="T-AI-07",
                team_skill_name="RAG检索增强生成与向量数据库",
                evidence=(grounded,),
                observed_capabilities=("构建RAG流程",),
                observed_proficiency="P1",
                achieved_subskills=(
                    AchievedSubskill("RAG-01", ("resume:invalid:reference",), "direct_behavior"),
                ),
            )

    def test_achievement_must_reference_a_node_in_the_matching_graph(self):
        profile = CandidateLearningProfile(
            "candidate_bad_node",
            (observed("P1", achieved_subskill_ids=("RAG-99",)),),
        )
        with self.assertRaisesRegex(ValueError, "not present in graph"):
            self.engine.build(profile, target(requirement(required_level="P3")))

    def test_cyclic_development_graph_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "acyclic"):
            SkillDevelopmentGraph(
                graph_version="test",
                team_skill_id="T-AI-07",
                team_skill_name="RAG",
                coverage_scope="test graph",
                subskill_nodes=(node("a", ("b",)), node("b", ("a",))),
                verification_task=verification_task(),
            )

    def test_duplicate_node_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "node IDs must be unique"):
            SkillDevelopmentGraph(
                graph_version="test",
                team_skill_id="T-AI-07",
                team_skill_name="RAG",
                coverage_scope="test graph",
                subskill_nodes=(node("same"), node("same")),
                verification_task=verification_task(),
            )

    def test_invalid_prerequisite_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid prerequisite"):
            SkillDevelopmentGraph(
                graph_version="test",
                team_skill_id="T-AI-07",
                team_skill_name="RAG",
                coverage_scope="test graph",
                subskill_nodes=(node("a", ("missing",)),),
                verification_task=verification_task(),
            )

    def test_auxiliary_skill_is_rejected_from_primary_engine(self):
        with self.assertRaisesRegex(ValueError, "auxiliary Team Skill"):
            requirement("F-1-01")
        with self.assertRaisesRegex(ValueError, "auxiliary Team Skill"):
            ObservedTeamSkill(
                team_skill_id="F-4-02",
                team_skill_name="auxiliary",
                evidence=(evidence(),),
                observed_capabilities=("exposure",),
                observed_proficiency="P2",
            )
        with self.assertRaisesRegex(ValueError, "auxiliary Team Skill"):
            SkillDevelopmentGraph(
                graph_version="test",
                team_skill_id="F-3-04",
                team_skill_name="auxiliary",
                coverage_scope="invalid primary graph",
                subskill_nodes=(node("a"),),
                verification_task=verification_task(),
            )

    def test_priority_order_is_stable_and_lexicographic(self):
        requirements_a = (
            requirement("T-AI-07", requirement_type="preferred", market_trend_rank=0),
            requirement("T-AI-05", requirement_type="core", market_trend_rank=2),
            requirement("T-AI-06", requirement_type="core", market_trend_rank=1),
        )
        requirements_b = tuple(reversed(requirements_a))
        profile = CandidateLearningProfile("candidate_priority")
        first = self.engine.build(profile, target(*requirements_a))
        second = self.engine.build(profile, target(*requirements_b))
        expected = ["T-AI-06", "T-AI-05", "T-AI-07"]
        self.assertEqual([item.team_skill_id for item in first.gap_items], expected)
        self.assertEqual([item.team_skill_id for item in second.gap_items], expected)
        self.assertEqual(
            [item.lexicographic_key for item in first.priority_explanations],
            [item.lexicographic_key for item in second.priority_explanations],
        )

    def test_required_capability_scope_is_not_inferred_from_broad_level(self):
        profile = CandidateLearningProfile("candidate_scope", (observed("P4", ("构建基础RAG流程",)),))
        result = self.engine.build(
            profile,
            target(requirement(required_level="P2", capabilities=("验证混合检索质量",))),
        )
        self.assertEqual(result.gap_items[0].gap_type, GapType.EVIDENCE_INSUFFICIENT)
        self.assertEqual(result.paths[0].mode, PathMode.VERIFY_FIRST)

    def test_representative_results_are_deterministic(self):
        profiles = (
            CandidateLearningProfile("beginner"),
            CandidateLearningProfile(
                "partial",
                (observed("P1", achieved_subskill_ids=("RAG-01", "RAG-02")),),
            ),
            CandidateLearningProfile("unknown", (observed("U"),)),
        )
        expected = None
        for _ in range(20):
            actual = tuple(
                self.engine.build(profile, target(requirement(required_level="P3"))).to_dict()
                for profile in profiles
            )
            if expected is None:
                expected = actual
            self.assertEqual(actual, expected)


class LearningPathStage2B1BranchSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = branch_graph()
        cls.engine = LearningPathEngine((cls.graph,))

    @staticmethod
    def step_ids(result):
        return [step.subskill_id for step in result.paths[0].ordered_steps]

    def test_no_required_subskills_selects_core_only(self):
        result = self.engine.build(CandidateLearningProfile("core_only"), branch_target())
        self.assertEqual(self.step_ids(result), ["core-01", "core-02"])

    def test_one_targeted_specialization_selects_its_prerequisite_closure(self):
        result = self.engine.build(
            CandidateLearningProfile("branch_a"),
            branch_target(("spec-a",)),
        )
        self.assertEqual(
            self.step_ids(result),
            ["core-01", "core-02", "spec-a-base", "spec-a"],
        )
        self.assertNotIn("spec-b", self.step_ids(result))
        self.assertEqual(result.gap_items[0].required_subskill_ids, ("spec-a",))

    def test_multiple_targeted_specializations_select_union_and_closure(self):
        result = self.engine.build(
            CandidateLearningProfile("branches_a_b"),
            branch_target(("spec-a", "spec-b")),
        )
        self.assertEqual(
            self.step_ids(result),
            ["core-01", "core-02", "spec-a-base", "spec-a", "spec-b"],
        )

    def test_invalid_required_subskill_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "required subskill is not present in graph"):
            self.engine.build(
                CandidateLearningProfile("invalid_branch"),
                branch_target(("spec-missing",)),
            )

    def test_achieved_targeted_specialization_is_pruned_after_selection(self):
        grounded = GroundedEvidence(
            text="独立实现并验证了 specialization A",
            source_id="branch_resume",
            start=0,
            end=25,
        )
        profile = CandidateLearningProfile(
            "achieved_branch_a",
            supported_team_skills=(
                ObservedTeamSkill(
                    team_skill_id="T-BRANCH-01",
                    team_skill_name="Branch-aware Test Skill",
                    evidence=(grounded,),
                    observed_capabilities=("实现 specialization A",),
                    observed_proficiency="P1",
                    achieved_subskills=(
                        AchievedSubskill(
                            "spec-a",
                            (grounded.reference_id,),
                            "direct_behavior",
                        ),
                    ),
                ),
            ),
        )
        result = self.engine.build(profile, branch_target(("spec-a",)))
        self.assertEqual(result.paths[0].mode, PathMode.DEEPEN)
        self.assertEqual(self.step_ids(result), ["core-01", "core-02", "spec-a-base"])
        self.assertEqual(
            [item.subskill_id for item in result.paths[0].achieved_subskills],
            ["spec-a"],
        )

    def test_explicit_mention_matching_specialization_does_not_select_it(self):
        grounded = GroundedEvidence("Specialization B", "branch_resume", 0, 16)
        profile = CandidateLearningProfile(
            "mention_only",
            explicit_mentions=(
                ExplicitSkillMention("Specialization B", grounded, "T-BRANCH-01"),
            ),
        )
        result = self.engine.build(profile, branch_target())
        self.assertEqual(self.step_ids(result), ["core-01", "core-02"])

    def test_branch_selection_is_deterministic_across_100_runs(self):
        profile = CandidateLearningProfile("deterministic_branch")
        target_value = branch_target(("spec-b", "spec-a"))
        expected = self.engine.build(profile, target_value).to_dict()
        for _ in range(100):
            self.assertEqual(self.engine.build(profile, target_value).to_dict(), expected)


class LearningPathStage2B15ReassessmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = load_skill_development_graph(FIXTURE_PATH)
        cls.engine = LearningPathEngine((cls.graph,))
        cls.all_rag_nodes = tuple(node.subskill_id for node in cls.graph.topological_nodes())

    def test_level_gap_all_selected_nodes_achieved_keeps_capstone(self):
        profile = CandidateLearningProfile(
            "all_achieved_p2",
            (observed("P2", achieved_subskill_ids=self.all_rag_nodes),),
        )
        result = self.engine.build(profile, target(requirement(required_level="P3")))
        path = result.paths[0]
        self.assertEqual(result.gap_items[0].gap_type, GapType.LEVEL_GAP)
        self.assertEqual(path.mode, PathMode.DEEPEN)
        self.assertEqual(path.ordered_steps, ())
        self.assertEqual(path.capstone_evidence_task.task_id, "RAG-CAPSTONE-01")
        self.assertTrue(path.reassessment_required)
        self.assertEqual(path.path_status, "READY")

    def test_level_gap_some_nodes_unachieved_returns_remaining_and_capstone(self):
        profile = CandidateLearningProfile(
            "partial_achieved_p2",
            (observed("P2", achieved_subskill_ids=("RAG-01", "RAG-02", "RAG-03")),),
        )
        result = self.engine.build(profile, target(requirement(required_level="P3")))
        path = result.paths[0]
        self.assertEqual(
            [step.subskill_id for step in path.ordered_steps],
            ["RAG-04", "RAG-06"],
        )
        self.assertEqual(path.capstone_evidence_task.task_id, "RAG-CAPSTONE-01")
        self.assertTrue(path.reassessment_required)

    def test_capstone_does_not_mutate_or_assign_proficiency(self):
        profile = CandidateLearningProfile(
            "no_auto_upgrade",
            (observed("P2", achieved_subskill_ids=self.all_rag_nodes),),
        )
        result = self.engine.build(profile, target(requirement(required_level="P3")))
        path_payload = result.paths[0].to_dict()
        self.assertEqual(profile.supported_team_skills[0].observed_proficiency, "P2")
        self.assertEqual(result.gap_items[0].observed_level, "P2")
        self.assertEqual(result.gap_items[0].gap_type, GapType.LEVEL_GAP)
        self.assertEqual(path_payload["target_level"], "P3")
        self.assertNotIn("assigned_level", path_payload)
        self.assertNotIn("resulting_level", path_payload)

    def test_missing_returns_learn_capstone_and_reassessment(self):
        result = self.engine.build(CandidateLearningProfile("missing"), target(requirement()))
        path = result.paths[0]
        self.assertEqual(path.mode, PathMode.LEARN)
        self.assertEqual(len(path.ordered_steps), 5)
        self.assertEqual(path.capstone_evidence_task.task_id, "RAG-CAPSTONE-01")
        self.assertTrue(path.reassessment_required)
        self.assertEqual(path.target_level, "P2")

    def test_u_uses_verification_task_without_capstone(self):
        result = self.engine.build(
            CandidateLearningProfile("unknown", (observed("U"),)),
            target(requirement(required_level="P3")),
        )
        path = result.paths[0]
        self.assertEqual(path.mode, PathMode.VERIFY_FIRST)
        self.assertEqual([step.subskill_id for step in path.ordered_steps], ["RAG-VERIFY-01"])
        self.assertIsNone(path.capstone_evidence_task)
        self.assertTrue(path.reassessment_required)
        self.assertNotIn(
            self.graph.capstone_evidence_task.task_description,
            path.evidence_tasks,
        )

    def test_satisfied_returns_none_without_capstone_or_reassessment(self):
        result = self.engine.build(
            CandidateLearningProfile("satisfied", (observed("P3"),)),
            target(requirement(required_level="P3")),
        )
        path = result.paths[0]
        self.assertEqual(path.mode, PathMode.NONE)
        self.assertEqual(path.ordered_steps, ())
        self.assertIsNone(path.capstone_evidence_task)
        self.assertFalse(path.reassessment_required)

    def test_reassessment_output_is_deterministic_across_100_runs(self):
        profile = CandidateLearningProfile(
            "deterministic_reassessment",
            (observed("P2", achieved_subskill_ids=self.all_rag_nodes),),
        )
        target_value = target(requirement(required_level="P3"))
        expected = self.engine.build(profile, target_value).to_dict()
        for _ in range(100):
            self.assertEqual(self.engine.build(profile, target_value).to_dict(), expected)


if __name__ == "__main__":
    unittest.main()
