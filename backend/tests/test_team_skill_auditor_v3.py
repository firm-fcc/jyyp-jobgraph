import unittest

from extractor.agentic_schema import CandidateAbility, CandidateStatus, Evidence
from extractor.team_skill_auditor_v3 import TeamSkillAuditorV3, aggregate_team_skill_assessments
from extractor.team_skill_registry import TeamSkillRegistry
from extractor.team_skill_verifier_v3 import ModelTeamSkillAssessment


def candidate(cid, text="使用PyTorch训练ResNet-18模型", project_id=None, located=True):
    project_id = project_id or cid
    start = 0 if located else None
    end = len(text) if located else None
    return CandidateAbility(
        candidate_id=cid,
        resume_id="candidate_1",
        project_id=project_id,
        fact=text,
        behavior="执行相关技术行为",
        ability="能力提示",
        normalized_ability="能力提示",
        category={},
        evidence=[Evidence(text=text, project_id=project_id, start=start, end=end)],
        reason="test",
        confidence=0.8,
        source="test",
        revision_round=0,
        parent_candidate_id=None,
        status=CandidateStatus.PENDING_REVIEW,
        lineage=[cid],
    )


class TeamSkillAuditorV3Tests(unittest.TestCase):
    def setUp(self):
        self.registry = TeamSkillRegistry()
        self.auditor = TeamSkillAuditorV3(self.registry)

    def test_missing_quote_downgrades_supported(self):
        model = ModelTeamSkillAssessment(
            team_skill_id="T-AI-01",
            status="supported",
            support_evidence=("模型从未见过的伪造证据",),
            reason="test",
            confidence=0.9,
            atomic_ability="模型训练",
        )
        audited = self.auditor.audit(candidate("e1"), model)
        self.assertEqual(audited.final_status, "unsupported")
        self.assertIn("support_evidence_not_found", audited.audit_flags)

    def test_unlocated_quote_cannot_self_validate(self):
        text = "使用PyTorch训练ResNet-18模型"
        model = ModelTeamSkillAssessment(
            team_skill_id="T-AI-01",
            status="supported",
            support_evidence=(text,),
            reason="test",
            confidence=0.9,
            atomic_ability="模型训练",
        )
        audited = self.auditor.audit(candidate("e1", text, located=False), model)
        self.assertEqual(audited.final_status, "unsupported")
        self.assertIn("support_evidence_unlocated", audited.audit_flags)
        self.assertIn("no_located_resume_evidence", audited.audit_flags)
        self.assertIsNone(audited.atomic_ability)

    def test_aggregate_signal_requires_two_distinct_experiences(self):
        model = ModelTeamSkillAssessment(
            team_skill_id="F-4-01",
            status="supported",
            support_evidence=("持续解决技术困难",),
            reason="test",
            confidence=0.8,
            atomic_ability="持续攻坚",
        )
        # Two generated candidate IDs from the same project are not independent contexts.
        c1 = candidate("e1", "持续解决技术困难", project_id="same_project")
        c2 = candidate("e2", "持续解决技术困难", project_id="same_project")
        a1 = self.auditor.audit(c1, model)
        a2 = self.auditor.audit(c2, model)
        agg_same = aggregate_team_skill_assessments([a1, a2], self.registry)
        self.assertEqual(agg_same[0].final_status, "partially_supported")

        c3 = candidate("e3", "持续解决技术困难", project_id="other_project")
        a3 = self.auditor.audit(c3, model)
        agg_cross = aggregate_team_skill_assessments([a1, a3], self.registry)
        self.assertEqual(agg_cross[0].final_status, "supported")
        self.assertEqual(set(agg_cross[0].source_experience_ids), {"same_project", "other_project"})


if __name__ == "__main__":
    unittest.main()
