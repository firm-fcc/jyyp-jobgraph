import json
import inspect
import unittest
from pathlib import Path

from extractor.learning_path_renderer import LearningPathRenderer
from extractor.learning_path_stage1 import (
    AchievedSubskill,
    CandidateLearningProfile,
    GroundedEvidence,
    JobLearningTarget,
    JobSkillRequirement,
    LearningPathEngine,
    ObservedTeamSkill,
    load_skill_development_graph,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1] / "candidate_core"
GRAPH_PATHS = tuple(sorted((PROJECT_ROOT / "config").glob("skill_development_graph_*.json")))


class LearningPathRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graphs = tuple(load_skill_development_graph(path) for path in GRAPH_PATHS)
        cls.by_id = {graph.team_skill_id: graph for graph in cls.graphs}
        cls.engine = LearningPathEngine(cls.graphs)
        cls.renderer = LearningPathRenderer()

    @staticmethod
    def evidence():
        text = "本人实现并验证了可复现的目标技术行为。"
        return GroundedEvidence(text=text, source_id="resume", start=5, end=5 + len(text))

    @classmethod
    def observed(cls, graph, level, achieved_ids=()):
        evidence = cls.evidence()
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

    @staticmethod
    def selected_ids(graph, required_specializations=()):
        by_id = {node.subskill_id: node for node in graph.subskill_nodes}
        selected = {node.subskill_id for node in graph.subskill_nodes if node.node_type == "core"}
        selected.update(required_specializations)
        pending = list(selected)
        while pending:
            for prerequisite in by_id[pending.pop()].prerequisites:
                if prerequisite not in selected:
                    selected.add(prerequisite)
                    pending.append(prerequisite)
        return tuple(node.subskill_id for node in graph.topological_nodes() if node.subskill_id in selected)

    def plan(self, graph, *, level=None, achieved_ids=(), required_specializations=()):
        supported = (
            (self.observed(graph, level, achieved_ids),)
            if level is not None
            else ()
        )
        candidate = CandidateLearningProfile(
            candidate_id="candidate_renderer",
            supported_team_skills=supported,
        )
        requirement = JobSkillRequirement(
            team_skill_id=graph.team_skill_id,
            requirement_type="core",
            required_level="P3",
            requirement_evidence=("岗位要求该能力",),
            required_subskill_ids=tuple(required_specializations),
        )
        target = JobLearningTarget(
            job_id="job_renderer",
            job_title="目标岗位",
            requirements=(requirement,),
        )
        result = self.engine.build(candidate, target)
        rendered = self.renderer.render(result)
        return result, rendered.skill_paths[0]

    def test_01_missing_renders_learn_nodes_without_negative_capability_claim(self):
        result, rendered = self.plan(self.by_id["T-AI-07"])
        self.assertEqual(rendered.gap_type, "MISSING")
        self.assertEqual(rendered.path_mode, "LEARN")
        self.assertEqual(rendered.current_state, "当前未发现足够行为证据支持该能力。")
        self.assertEqual(
            [step.node_id for step in rendered.learning_steps],
            [step.subskill_id for step in result.paths[0].ordered_steps],
        )
        self.assertIsNotNone(rendered.capstone_guidance)
        for forbidden in ("能力差", "基础薄弱", "不会"):
            self.assertNotIn(forbidden, rendered.current_state)

    def test_02_level_gap_renders_deepen_nodes_and_observed_required_levels(self):
        result, rendered = self.plan(
            self.by_id["T-AI-01"],
            level="P2",
            achieved_ids=("ML-01",),
        )
        self.assertEqual(rendered.gap_type, "LEVEL_GAP")
        self.assertEqual(rendered.path_mode, "DEEPEN")
        self.assertEqual(rendered.observed_level, "P2")
        self.assertEqual(rendered.required_level, "P3")
        self.assertEqual(
            [step.node_id for step in rendered.learning_steps],
            [step.subskill_id for step in result.paths[0].ordered_steps],
        )

    def test_03_level_gap_all_selected_nodes_achieved_renders_capstone_only(self):
        graph = self.by_id["T-AI-07"]
        selected = self.selected_ids(graph)
        _, rendered = self.plan(graph, level="P2", achieved_ids=selected)
        self.assertEqual(rendered.learning_steps, ())
        self.assertIsNotNone(rendered.capstone_guidance)
        self.assertTrue(rendered.reassessment_required)

    def test_04_u_renders_verify_first_only(self):
        _, rendered = self.plan(self.by_id["T-AI-03"], level="U")
        self.assertEqual(rendered.gap_type, "EVIDENCE_INSUFFICIENT")
        self.assertEqual(rendered.path_mode, "VERIFY_FIRST")
        self.assertEqual(rendered.learning_steps, ())
        self.assertIsNotNone(rendered.verification_guidance)
        self.assertIsNone(rendered.capstone_guidance)

    def test_05_satisfied_renders_none_and_no_learning_nodes(self):
        _, rendered = self.plan(self.by_id["T-DA-02"], level="P3")
        self.assertEqual(rendered.gap_type, "SATISFIED")
        self.assertEqual(rendered.path_mode, "NONE")
        self.assertIn("当前证据", rendered.current_state)
        self.assertIn("目标岗位要求", rendered.current_state)
        self.assertIn("无需进入当前优先学习路径", rendered.current_state)
        self.assertEqual(rendered.learning_steps, ())
        self.assertIsNone(rendered.verification_guidance)
        self.assertIsNone(rendered.capstone_guidance)
        self.assertFalse(rendered.reassessment_required)

    def test_06_single_specialization_branch_is_preserved(self):
        _, rendered = self.plan(
            self.by_id["T-AI-07"],
            required_specializations=("RAG-05",),
        )
        self.assertEqual(
            [extension.subskill_id for extension in rendered.specialization_extensions],
            ["RAG-05"],
        )

    def test_07_multiple_specialization_branches_are_preserved(self):
        _, rendered = self.plan(
            self.by_id["T-AI-01"],
            required_specializations=("ML-03", "ML-06"),
        )
        self.assertEqual(
            [extension.subskill_id for extension in rendered.specialization_extensions],
            ["ML-03", "ML-06"],
        )

    def test_08_achieved_selected_specialization_extension_is_retained(self):
        _, rendered = self.plan(
            self.by_id["T-AI-01"],
            level="P2",
            achieved_ids=("ML-03",),
            required_specializations=("ML-03",),
        )
        self.assertNotIn("ML-03", [step.node_id for step in rendered.learning_steps])
        self.assertEqual(
            [extension.subskill_id for extension in rendered.specialization_extensions],
            ["ML-03"],
        )

    def test_09_unrelated_specialization_is_never_rendered(self):
        _, rendered = self.plan(
            self.by_id["T-AI-10"],
            required_specializations=("LLM-04",),
        )
        self.assertEqual(
            [extension.subskill_id for extension in rendered.specialization_extensions],
            ["LLM-04"],
        )
        serialized = rendered.to_dict()
        self.assertNotIn("LLM-03", json.dumps(serialized, ensure_ascii=False))
        self.assertNotIn("LLM-05", json.dumps(serialized, ensure_ascii=False))
        self.assertNotIn("LLM-06", json.dumps(serialized, ensure_ascii=False))

    def test_10_learning_node_order_is_byte_for_byte_preserved(self):
        result, rendered = self.plan(self.by_id["T-AI-07"])
        self.assertEqual(
            tuple(step.node_id for step in rendered.learning_steps),
            tuple(step.subskill_id for step in result.paths[0].ordered_steps),
        )

    def test_11_renderer_neither_invents_nor_deletes_learning_nodes(self):
        for graph in self.graphs:
            with self.subTest(skill=graph.team_skill_id):
                result, rendered = self.plan(graph)
                planner_ids = tuple(step.subskill_id for step in result.paths[0].ordered_steps)
                rendered_ids = tuple(step.node_id for step in rendered.learning_steps)
                self.assertEqual(rendered_ids, planner_ids)

    def test_12_completion_never_claims_automatic_proficiency_upgrade(self):
        _, rendered = self.plan(self.by_id["T-AI-01"], level="P2")
        text = json.dumps(rendered.to_dict(), ensure_ascii=False)
        self.assertIn("不会自动升级", text)
        for forbidden in ("完成后升级为", "自动达到P", "直接获得P"):
            self.assertNotIn(forbidden, text)

    def test_13_u_is_evidence_insufficient_not_low_capability(self):
        _, rendered = self.plan(self.by_id["T-AI-08"], level="U")
        self.assertIn("证据不足", rendered.current_state)
        self.assertIn("不表示能力水平低", rendered.current_state)
        rendered_text = json.dumps(rendered.to_dict(), ensure_ascii=False)
        for forbidden in ("能力较低", "能力差", "基础薄弱", "从零学习"):
            self.assertNotIn(forbidden, rendered_text)

    def test_14_same_input_has_byte_equivalent_canonical_output(self):
        graph = self.by_id["T-AI-08"]
        candidate = CandidateLearningProfile(candidate_id="deterministic")
        requirement = JobSkillRequirement(
            team_skill_id=graph.team_skill_id,
            requirement_type="core",
            required_level="P3",
            requirement_evidence=("岗位要求",),
            required_subskill_ids=("AG-05",),
        )
        target = JobLearningTarget("job", "岗位", (requirement,))
        result = self.engine.build(candidate, target)
        expected = self.renderer.render(result).to_canonical_json()
        for _ in range(100):
            actual = self.renderer.render(result).to_canonical_json()
            self.assertEqual(actual, expected)

    def test_15_public_interface_has_no_external_selected_graph_input(self):
        parameters = inspect.signature(self.renderer.render).parameters
        self.assertNotIn("selected_graph_node_ids_by_skill", parameters)

    def test_16_invented_selected_graph_node_cannot_be_injected_or_rendered(self):
        graph = self.by_id["T-AI-03"]
        result = self.engine.build(
            CandidateLearningProfile(candidate_id="trace"),
            JobLearningTarget(
                "job",
                "岗位",
                (JobSkillRequirement(graph.team_skill_id, "core", "P3", ("要求",)),),
            ),
        )
        with self.assertRaises(TypeError):
            self.renderer.render(
                result,
                selected_graph_node_ids_by_skill={graph.team_skill_id: ("INVENTED-99",)},
            )
        rendered = self.renderer.render(result).to_dict()
        serialized = json.dumps(rendered, ensure_ascii=False)
        self.assertNotIn("INVENTED-99", serialized)
        self.assertNotIn("selected_graph_node_ids", serialized)
        self.assertNotIn("selected_graph_trace", serialized)

    def test_17_achieved_node_ids_are_strictly_copied_from_planner(self):
        graph = self.by_id["T-AI-01"]
        result, rendered = self.plan(
            graph,
            level="P2",
            achieved_ids=("ML-01", "ML-03"),
            required_specializations=("ML-03",),
        )
        self.assertEqual(
            rendered.achieved_node_ids,
            tuple(item.subskill_id for item in result.paths[0].achieved_subskills),
        )

    def test_18_specialization_extensions_are_strictly_copied_from_planner(self):
        result, rendered = self.plan(
            self.by_id["T-AI-01"],
            level="P2",
            achieved_ids=("ML-03",),
            required_specializations=("ML-03",),
        )
        planner_extensions = result.paths[0].capstone_evidence_task.specialization_extensions
        self.assertEqual(
            tuple(extension.subskill_id for extension in rendered.specialization_extensions),
            tuple(extension.subskill_id for extension in planner_extensions),
        )


if __name__ == "__main__":
    unittest.main()
