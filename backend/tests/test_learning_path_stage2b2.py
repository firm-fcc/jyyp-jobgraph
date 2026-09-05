import json
import unittest
from pathlib import Path

from extractor.learning_path_stage1 import (
    AchievedSubskill,
    CandidateLearningProfile,
    ExplicitSkillMention,
    GroundedEvidence,
    JobLearningTarget,
    JobSkillRequirement,
    LearningPathEngine,
    load_skill_development_graph,
    ObservedTeamSkill,
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


class LearningPathStage2B2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graphs = tuple(load_skill_development_graph(path) for path in GRAPH_PATHS)
        cls.by_id = {graph.team_skill_id: graph for graph in cls.graphs}
        cls.engine = LearningPathEngine(cls.graphs)

    @staticmethod
    def requirement(graph, required_level="P3", required_subskill_ids=()):
        return JobSkillRequirement(
            team_skill_id=graph.team_skill_id,
            requirement_type="core",
            required_level=required_level,
            requirement_evidence=("岗位要求该项能力",),
            required_subskill_ids=tuple(required_subskill_ids),
        )

    @staticmethod
    def target(requirement):
        return JobLearningTarget(job_id="job", job_title="工程师", requirements=(requirement,))

    @staticmethod
    def grounded():
        text = "本人完成了可复现的技术任务并记录输入输出和评价。"
        return GroundedEvidence(text=text, source_id="resume", start=8, end=8 + len(text))

    @classmethod
    def observed(cls, graph, level="P2", achieved_ids=()):
        evidence = cls.grounded()
        return ObservedTeamSkill(
            team_skill_id=graph.team_skill_id,
            team_skill_name=graph.team_skill_name,
            evidence=(evidence,),
            observed_capabilities=("已观察到的行为范围",),
            observed_proficiency=level,
            achieved_subskills=tuple(
                AchievedSubskill(
                    subskill_id=node_id,
                    evidence_refs=(evidence.reference_id,),
                    mapping_basis="direct_behavior",
                )
                for node_id in achieved_ids
            ),
        )

    def build(self, graph, candidate, required_level="P3", required_subskill_ids=()):
        requirement = self.requirement(graph, required_level, required_subskill_ids)
        return self.engine.build(candidate, self.target(requirement))

    def test_compact_schema_and_eight_graph_ids(self):
        self.assertEqual(len(self.graphs), 8)
        for path, graph in zip(GRAPH_PATHS, self.graphs):
            with self.subTest(skill=graph.team_skill_id):
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertTrue({"version", "team_skill_id", "team_skill_name", "scope", "nodes", "verification_task", "capstone_evidence_task", "source_registry"} <= set(payload))
                self.assertEqual(graph.selection_protocol, "core_plus_required_v1")

    def test_unique_node_ids(self):
        for graph in self.graphs:
            with self.subTest(skill=graph.team_skill_id):
                ids = [node.subskill_id for node in graph.subskill_nodes]
                self.assertEqual(len(ids), len(set(ids)))

    def test_prerequisites_exist_and_dag_is_acyclic(self):
        for graph in self.graphs:
            with self.subTest(skill=graph.team_skill_id):
                ids = {node.subskill_id for node in graph.subskill_nodes}
                self.assertTrue(all(set(node.prerequisites) <= ids for node in graph.subskill_nodes))
                self.assertEqual(len(graph.topological_nodes()), len(graph.subskill_nodes))

    def test_each_graph_topological_order_respects_every_edge(self):
        for graph in self.graphs:
            with self.subTest(skill=graph.team_skill_id):
                order = {node.subskill_id: index for index, node in enumerate(graph.topological_nodes())}
                self.assertTrue(
                    all(order[prerequisite] < order[node.subskill_id] for node in graph.subskill_nodes for prerequisite in node.prerequisites)
                )

    def test_source_refs_resolve_to_registry(self):
        for graph in self.graphs:
            with self.subTest(skill=graph.team_skill_id):
                source_ids = {source.source_id for source in graph.source_registry}
                self.assertTrue(source_ids)
                for node in graph.subskill_nodes:
                    self.assertTrue(set(node.source_references) <= source_ids)

    def test_beginner_default_selects_core_only_with_capstone(self):
        for graph in self.graphs:
            with self.subTest(skill=graph.team_skill_id):
                result = self.build(graph, CandidateLearningProfile(candidate_id="beginner"))
                path = result.paths[0]
                self.assertEqual(result.gap_items[0].gap_type.value, "MISSING")
                self.assertEqual(path.mode.value, "LEARN")
                self.assertEqual({step.subskill_id for step in path.ordered_steps}, {node.subskill_id for node in graph.subskill_nodes if node.node_type == "core"})
                self.assertIsNotNone(path.capstone_evidence_task)
                self.assertTrue(path.reassessment_required)

    def test_explicit_specialization_selects_only_target_branch_and_closure(self):
        for graph in self.graphs:
            specializations = [node for node in graph.subskill_nodes if node.node_type == "specialization"]
            if not specializations:
                continue
            target_node = specializations[0]
            with self.subTest(skill=graph.team_skill_id, node=target_node.subskill_id):
                result = self.build(graph, CandidateLearningProfile(candidate_id="targeted"), required_subskill_ids=(target_node.subskill_id,))
                selected = {step.subskill_id for step in result.paths[0].ordered_steps}
                self.assertIn(target_node.subskill_id, selected)
                self.assertTrue({node.subskill_id for node in graph.subskill_nodes if node.node_type == "core"} <= selected)
                self.assertTrue(set(target_node.prerequisites) <= selected)
                unrelated = {node.subskill_id for node in specializations[1:]}
                self.assertTrue(selected.isdisjoint(unrelated))

    def test_multiple_specializations_form_union_with_closure(self):
        for graph in self.graphs:
            specializations = [node for node in graph.subskill_nodes if node.node_type == "specialization"]
            if len(specializations) < 2:
                continue
            target_ids = tuple(node.subskill_id for node in specializations[:2])
            with self.subTest(skill=graph.team_skill_id):
                result = self.build(graph, CandidateLearningProfile(candidate_id="multi"), required_subskill_ids=target_ids)
                selected = {step.subskill_id for step in result.paths[0].ordered_steps}
                self.assertTrue(set(target_ids) <= selected)

    def test_unrelated_specializations_are_excluded(self):
        for graph in self.graphs:
            specializations = [node.subskill_id for node in graph.subskill_nodes if node.node_type == "specialization"]
            if len(specializations) < 2:
                continue
            with self.subTest(skill=graph.team_skill_id):
                result = self.build(graph, CandidateLearningProfile(candidate_id="one_branch"), required_subskill_ids=(specializations[0],))
                selected = {step.subskill_id for step in result.paths[0].ordered_steps}
                self.assertTrue(selected.isdisjoint(specializations[1:]))

    def test_invalid_specialization_id_rejected(self):
        for graph in self.graphs:
            with self.subTest(skill=graph.team_skill_id):
                with self.assertRaises(ValueError):
                    self.build(graph, CandidateLearningProfile(candidate_id="invalid"), required_subskill_ids=("NOT-A-NODE",))

    def test_partial_level_gap_prunes_only_evidence_grounded_achievements(self):
        for graph in self.graphs:
            core_ids = [node.subskill_id for node in graph.topological_nodes() if node.node_type == "core"]
            achieved = tuple(core_ids[:2])
            with self.subTest(skill=graph.team_skill_id):
                candidate = CandidateLearningProfile(candidate_id="partial", supported_team_skills=(self.observed(graph, "P2", achieved),))
                result = self.build(graph, candidate)
                path = result.paths[0]
                remaining = {step.subskill_id for step in path.ordered_steps}
                self.assertEqual(result.gap_items[0].gap_type.value, "LEVEL_GAP")
                self.assertEqual(path.mode.value, "DEEPEN")
                self.assertTrue(remaining.isdisjoint(achieved))
                self.assertIsNotNone(path.capstone_evidence_task)

    def test_parent_support_without_achievement_prunes_nothing(self):
        for graph in self.graphs:
            with self.subTest(skill=graph.team_skill_id):
                candidate = CandidateLearningProfile(candidate_id="parent", supported_team_skills=(self.observed(graph),))
                result = self.build(graph, candidate)
                self.assertEqual(len(result.paths[0].ordered_steps), sum(node.node_type == "core" for node in graph.subskill_nodes))

    def test_achieved_child_does_not_imply_prerequisites(self):
        for graph in self.graphs:
            child = next((node for node in reversed(graph.topological_nodes()) if node.prerequisites and node.node_type == "core"), None)
            if child is None:
                continue
            with self.subTest(skill=graph.team_skill_id):
                candidate = CandidateLearningProfile(candidate_id="child", supported_team_skills=(self.observed(graph, "P2", (child.subskill_id,)),))
                result = self.build(graph, candidate)
                remaining = {step.subskill_id for step in result.paths[0].ordered_steps}
                self.assertTrue(set(child.prerequisites) <= remaining)

    def test_explicit_mention_never_selects_or_prunes_specialization(self):
        for graph in self.graphs:
            specialization = next((node for node in graph.subskill_nodes if node.node_type == "specialization"), None)
            if specialization is None:
                continue
            grounded = self.grounded()
            candidate = CandidateLearningProfile(candidate_id="mention", explicit_mentions=(ExplicitSkillMention(text=specialization.name_zh, evidence=grounded, team_skill_id=graph.team_skill_id),))
            with self.subTest(skill=graph.team_skill_id):
                result = self.build(graph, candidate)
                selected = {step.subskill_id for step in result.paths[0].ordered_steps}
                self.assertNotIn(specialization.subskill_id, selected)

    def test_u_uses_verification_only_for_every_graph(self):
        for graph in self.graphs:
            with self.subTest(skill=graph.team_skill_id):
                candidate = CandidateLearningProfile(candidate_id="u", supported_team_skills=(self.observed(graph, "U"),))
                result = self.build(graph, candidate)
                path = result.paths[0]
                self.assertEqual(result.gap_items[0].gap_type.value, "EVIDENCE_INSUFFICIENT")
                self.assertEqual(path.mode.value, "VERIFY_FIRST")
                self.assertTrue(path.evidence_tasks)
                self.assertIsNone(path.capstone_evidence_task)
                self.assertTrue(path.reassessment_required)

    def test_all_selected_achieved_keeps_level_gap_capstone(self):
        for graph in self.graphs:
            core_ids = tuple(node.subskill_id for node in graph.subskill_nodes if node.node_type == "core")
            with self.subTest(skill=graph.team_skill_id):
                candidate = CandidateLearningProfile(candidate_id="all", supported_team_skills=(self.observed(graph, "P2", core_ids),))
                result = self.build(graph, candidate)
                path = result.paths[0]
                self.assertEqual(len(path.ordered_steps), 0)
                self.assertIsNotNone(path.capstone_evidence_task)
                self.assertTrue(path.reassessment_required)
                self.assertEqual(result.gap_items[0].gap_type.value, "LEVEL_GAP")

    def test_satisfied_returns_none_without_capstone(self):
        for graph in self.graphs:
            with self.subTest(skill=graph.team_skill_id):
                candidate = CandidateLearningProfile(candidate_id="satisfied", supported_team_skills=(self.observed(graph, "P3"),))
                result = self.build(graph, candidate)
                path = result.paths[0]
                self.assertEqual(path.mode.value, "NONE")
                self.assertFalse(path.ordered_steps)
                self.assertIsNone(path.capstone_evidence_task)
                self.assertFalse(path.reassessment_required)

    def test_proficiency_is_never_mutated(self):
        for graph in self.graphs:
            with self.subTest(skill=graph.team_skill_id):
                candidate = CandidateLearningProfile(candidate_id="immutable", supported_team_skills=(self.observed(graph, "P2"),))
                before = candidate.to_dict()
                result = self.build(graph, candidate)
                self.assertEqual(candidate.to_dict(), before)
                self.assertEqual(result.gap_items[0].observed_level, "P2")
                self.assertEqual(result.paths[0].target_level, "P3")

    def test_deterministic_across_100_runs_for_all_graphs(self):
        for graph in self.graphs:
            candidate = CandidateLearningProfile(candidate_id="repeat", supported_team_skills=(self.observed(graph, "P2"),))
            target = self.target(self.requirement(graph))
            expected = self.engine.build(candidate, target).to_dict()
            with self.subTest(skill=graph.team_skill_id):
                self.assertTrue(all(self.engine.build(candidate, target).to_dict() == expected for _ in range(100)))

    def test_rag_controlled_migration(self):
        graph = self.by_id["T-AI-07"]
        by_id = {node.subskill_id: node for node in graph.subskill_nodes}
        self.assertEqual(by_id["RAG-05"].node_type, "specialization")
        self.assertEqual(by_id["RAG-05"].prerequisites, ("RAG-03",))
        self.assertEqual(by_id["RAG-06"].node_type, "core")
        self.assertEqual(by_id["RAG-06"].prerequisites, ("RAG-04",))
        default = self.build(graph, CandidateLearningProfile(candidate_id="rag_default"))
        self.assertEqual([step.subskill_id for step in default.paths[0].ordered_steps], ["RAG-01", "RAG-02", "RAG-03", "RAG-04", "RAG-06"])
        targeted = self.build(graph, CandidateLearningProfile(candidate_id="rag_target"), required_subskill_ids=("RAG-05",))
        self.assertEqual([step.subskill_id for step in targeted.paths[0].ordered_steps], ["RAG-01", "RAG-02", "RAG-03", "RAG-04", "RAG-05", "RAG-06"])


class LearningPathStage2B2SemanticFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graphs = tuple(load_skill_development_graph(path) for path in GRAPH_PATHS)
        cls.by_id = {graph.team_skill_id: graph for graph in cls.graphs}
        cls.engine = LearningPathEngine(cls.graphs)

    @staticmethod
    def requirement(graph, required_subskill_ids=()):
        return JobSkillRequirement(
            team_skill_id=graph.team_skill_id,
            requirement_type="core",
            required_level="P3",
            requirement_evidence=("岗位要求该项能力",),
            required_subskill_ids=tuple(required_subskill_ids),
        )

    @staticmethod
    def target(requirement):
        return JobLearningTarget(job_id="semantic_fix", job_title="工程师", requirements=(requirement,))

    @staticmethod
    def observed(graph, achieved_ids=()):
        text = "本人完成了可复现的训练、实现和评价任务。"
        evidence = GroundedEvidence(text=text, source_id="resume", start=3, end=3 + len(text))
        return ObservedTeamSkill(
            team_skill_id=graph.team_skill_id,
            team_skill_name=graph.team_skill_name,
            evidence=(evidence,),
            observed_capabilities=("已观察到的行为范围",),
            observed_proficiency="P2",
            achieved_subskills=tuple(
                AchievedSubskill(
                    subskill_id=node_id,
                    evidence_refs=(evidence.reference_id,),
                    mapping_basis="direct_behavior",
                )
                for node_id in achieved_ids
            ),
        )

    def build(self, graph, required_subskill_ids=(), observed=None):
        candidate = CandidateLearningProfile(
            candidate_id="semantic_fix_candidate",
            supported_team_skills=((observed,) if observed is not None else ()),
        )
        requirement = self.requirement(graph, required_subskill_ids)
        return self.engine.build(candidate, self.target(requirement))

    @staticmethod
    def extension_ids(result):
        capstone = result.paths[0].capstone_evidence_task
        return tuple(extension.subskill_id for extension in capstone.specialization_extensions)

    def test_01_default_rag_has_no_specialization_extension(self):
        result = self.build(self.by_id["T-AI-07"])
        self.assertEqual(self.extension_ids(result), ())

    def test_02_targeted_rag_has_only_rag_05_extension(self):
        result = self.build(self.by_id["T-AI-07"], ("RAG-05",))
        self.assertEqual(self.extension_ids(result), ("RAG-05",))

    def test_03_default_ml_does_not_leak_specialization_extensions(self):
        result = self.build(self.by_id["T-AI-01"])
        self.assertEqual(self.extension_ids(result), ())

    def test_04_targeted_ml_has_only_selected_branch_extension(self):
        result = self.build(self.by_id["T-AI-01"], ("ML-03",))
        self.assertEqual(self.extension_ids(result), ("ML-03",))

    def test_05_achieved_selected_specialization_keeps_extension_after_pruning(self):
        graph = self.by_id["T-AI-01"]
        result = self.build(graph, ("ML-03",), self.observed(graph, ("ML-03",)))
        self.assertNotIn("ML-03", [step.subskill_id for step in result.paths[0].ordered_steps])
        self.assertEqual(self.extension_ids(result), ("ML-03",))

    def test_06_multiple_selected_specializations_return_only_their_extensions(self):
        result = self.build(self.by_id["T-AI-01"], ("ML-03", "ML-06"))
        self.assertEqual(self.extension_ids(result), ("ML-03", "ML-06"))

    def test_07_unrelated_specialization_extension_never_leaks(self):
        result = self.build(self.by_id["T-AI-10"], ("LLM-04",))
        self.assertEqual(self.extension_ids(result), ("LLM-04",))

    def test_08_all_six_default_core_paths_have_zero_extensions(self):
        for graph in self.graphs:
            with self.subTest(skill=graph.team_skill_id):
                self.assertEqual(self.extension_ids(self.build(graph)), ())

    def test_09_llm_verification_requires_real_parameter_or_adapter_update(self):
        criteria = " ".join(self.by_id["T-AI-10"].verification_task.validation_criteria).casefold()
        self.assertIn("base model", criteria)
        self.assertIn("adapter target", criteria)
        self.assertIn("training data", criteria)
        self.assertIn("参数更新", criteria)
        self.assertIn("checkpoint", criteria)
        self.assertIn("adapter weights", criteria)
        self.assertIn("before/after evaluation", criteria)

    def test_10_llm_verification_excludes_non_parameter_update_experiments(self):
        criteria = " ".join(self.by_id["T-AI-10"].verification_task.validation_criteria).casefold()
        for excluded in ("prompting-only", "api调用", "inference-only", "system prompt", "prompt engineering"):
            with self.subTest(excluded=excluded):
                self.assertIn(excluded, criteria)
        self.assertIn("不能满足", criteria)

    def test_11_stage2b1_branch_selection_is_unchanged(self):
        graph = self.by_id["T-AI-03"]
        result = self.build(graph, ("CV-04",))
        self.assertEqual(
            [step.subskill_id for step in result.paths[0].ordered_steps],
            ["CV-01", "CV-02", "CV-04", "CV-06"],
        )
        self.assertEqual(self.extension_ids(result), ("CV-04",))

    def test_12_stage2b15_all_achieved_reassessment_semantics_are_unchanged(self):
        graph = self.by_id["T-AI-07"]
        selected_core_ids = tuple(
            node.subskill_id for node in graph.subskill_nodes if node.node_type == "core"
        )
        result = self.build(graph, observed=self.observed(graph, selected_core_ids))
        path = result.paths[0]
        self.assertEqual(path.ordered_steps, ())
        self.assertIsNotNone(path.capstone_evidence_task)
        self.assertEqual(self.extension_ids(result), ())
        self.assertTrue(path.reassessment_required)
        self.assertEqual(result.gap_items[0].gap_type.value, "LEVEL_GAP")

    def test_13_filtered_capstone_output_is_deterministic_across_100_runs(self):
        graph = self.by_id["T-AI-08"]
        requirement = self.requirement(graph, ("AG-05",))
        target = self.target(requirement)
        candidate = CandidateLearningProfile(candidate_id="repeat")
        expected = self.engine.build(candidate, target).to_dict()
        for _ in range(100):
            self.assertEqual(self.engine.build(candidate, target).to_dict(), expected)


if __name__ == "__main__":
    unittest.main()
