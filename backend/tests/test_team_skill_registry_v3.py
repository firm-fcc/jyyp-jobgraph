import json
import unittest
from pathlib import Path

from extractor.team_skill_registry import TeamSkillRegistry


ROOT = Path(__file__).resolve().parent.parent / "candidate_core"


class TeamSkillRegistryV3Tests(unittest.TestCase):
    def setUp(self):
        self.registry = TeamSkillRegistry()

    def test_loads_exact_shared_skill_set(self):
        source = json.loads((ROOT / "config" / "team_skills_v0.4.json").read_text(encoding="utf-8"))
        self.assertEqual(len(self.registry), source["total"])
        self.assertEqual({skill.code for skill in self.registry.all()}, set(source["detail"]))

    def test_metric_roles_partition_registry(self):
        primary = self.registry.primary_ids()
        auxiliary = self.registry.auxiliary_ids()
        self.assertTrue(primary)
        self.assertTrue(auxiliary)
        self.assertFalse(primary & auxiliary)
        self.assertEqual(len(primary | auxiliary), len(self.registry))
        self.assertEqual(len(primary), 43)
        self.assertEqual(len(auxiliary), 6)

    def test_aliases_are_candidate_retrieval_only(self):
        ranked = self.registry.rank_lexically(
            "使用 PyTorch 完成模型训练，并使用 Docker 封装服务",
            top_k=10,
        )
        ids = {item.skill.code for item in ranked}
        self.assertIn("T-AI-01", ids)
        self.assertIn("T-SYS-06", ids)
        # Retrieval ranking produces candidates only; no status is present.
        self.assertTrue(all(not hasattr(item, "status") for item in ranked))

    def test_semantic_scores_can_be_injected_without_network_dependency(self):
        ranked = self.registry.rank_lexically(
            "完全没有直接命中词",
            semantic_scores={"T-AI-02": 0.9},
            top_k=3,
        )
        self.assertEqual(ranked[0].skill.code, "T-AI-02")
        self.assertAlmostEqual(ranked[0].semantic_score, 0.9)

    def test_short_ascii_alias_does_not_match_inside_longer_token(self):
        ranked = self.registry.rank_lexically(
            "熟悉 HTML/CSS 前端开发", top_k=10, include_auxiliary=False
        )
        ids = {item.skill.code for item in ranked}
        self.assertNotIn("T-AI-01", ids)

    def test_generic_model_evaluation_does_not_retrieve_llm_selection_skill(self):
        ranked = self.registry.rank_lexically(
            "使用机器学习模型评估分类准确率", top_k=10, include_auxiliary=False
        )
        ids = {item.skill.code for item in ranked}
        self.assertIn("T-AI-01", ids)
        self.assertNotIn("T-AI-06", ids)


if __name__ == "__main__":
    unittest.main()
