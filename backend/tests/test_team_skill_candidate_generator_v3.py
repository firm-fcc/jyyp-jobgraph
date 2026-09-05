import unittest

from extractor.agentic_schema import CandidateAbility, CandidateStatus, Evidence
from extractor.team_skill_candidate_generator_v3 import TeamSkillCandidateGeneratorV3
from extractor.team_skill_registry import TeamSkillRegistry


def make_candidate(text, hint):
    return CandidateAbility(
        candidate_id="source",
        resume_id="candidate",
        project_id="exp",
        fact=text,
        behavior=text,
        ability=hint,
        normalized_ability=hint,
        category={},
        evidence=[Evidence(text=text, project_id="exp", start=0, end=len(text))],
        reason="test",
        confidence=0.8,
        source="test",
        revision_round=0,
        parent_candidate_id=None,
        status=CandidateStatus.PENDING_REVIEW,
        lineage=["source"],
    )


class TeamSkillCandidateGeneratorV3Tests(unittest.TestCase):
    def test_retrieves_relevant_team_skill(self):
        generator = TeamSkillCandidateGeneratorV3(TeamSkillRegistry())
        pool = generator.generate(
            make_candidate("使用Docker封装服务并部署", "容器化部署"), top_k=5
        )
        ids = {skill.code for skill in pool.skills}
        self.assertIn("T-SYS-06", ids)
        self.assertFalse(pool.fallback_all)

    def test_recall_safe_fallback_does_not_guess_supported_skill(self):
        generator = TeamSkillCandidateGeneratorV3(TeamSkillRegistry())
        pool = generator.generate(
            make_candidate("完成一个完全无法匹配别名的内部任务", "内部任务"), top_k=5
        )
        self.assertTrue(pool.fallback_all)
        self.assertEqual(len(pool.skills), len(generator.registry.primary()))


if __name__ == "__main__":
    unittest.main()
