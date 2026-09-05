import unittest

from extractor.agentic_schema import CandidateAbility, CandidateStatus, Evidence
from extractor.evidence_coverage_v432 import augment_grounded_coverage_v432
from extractor.evidence_source_policy_v43 import filter_evidence_candidates_v43
from extractor.team_skill_auditor_v4 import TeamSkillAuditorV4
from extractor.team_skill_registry import TeamSkillRegistry
from extractor.team_skill_verifier_v4 import ModelTeamSkillAssessment


def make_candidate(cid, text, start=0):
    return CandidateAbility(
        candidate_id=cid,
        resume_id="candidate_1",
        project_id="resume_full",
        fact=text,
        behavior=text,
        ability=text,
        normalized_ability=text,
        category={},
        evidence=[Evidence(text=text, project_id="resume_full", start=start, end=start + len(text))],
        reason="test",
        confidence=0.9,
        source="test",
        revision_round=0,
        parent_candidate_id=None,
        status=CandidateStatus.PENDING_REVIEW,
        lineage=[cid],
    )


class R432CoverageAndBoundaryTests(unittest.TestCase):
    def test_coverage_adds_uncovered_work_and_project_responsibilities(self):
        resume = (
            "工作经历\n"
            "使用C++程序实现马达控制\n"
            "主要工作职责 ：新型触觉设备以及算法的研发，涵盖原型机设计、电路及控制系统实装以及评价实验。\n"
            "项目经历\n"
            "3.负责内容：变形物体的实时仿真与力触觉的交互功能的建立\n"
            "期刊论文\n"
            "发表多篇专业论文\n"
        )
        covered = "使用C++程序实现马达控制"
        base = make_candidate("source_1", covered, resume.index(covered))
        result = augment_grounded_coverage_v432(
            [base], candidate_id="candidate_1", resume_text=resume
        )
        added = [
            e.text
            for candidate in result.candidates
            if candidate.source == "deterministic_coverage_v432"
            for e in candidate.evidence
        ]
        self.assertTrue(any("主要工作职责" in text for text in added))
        self.assertTrue(any("力触觉的交互功能" in text for text in added))
        self.assertFalse(any("发表多篇专业论文" in text for text in added))
        self.assertLessEqual(result.added_candidate_count, 2)

    def test_result_only_publication_summary_is_blocked_outside_publication_section(self):
        resume = "优势：海外学术成果丰硕，发表多篇专业论文。\n工作经历\n使用Python训练分类模型\n"
        publication = "发表多篇专业论文"
        direct = "使用Python训练分类模型"
        candidate = make_candidate("source_1", publication, resume.index(publication))
        candidate.evidence = [
            Evidence(publication, "resume_full", resume.index(publication), resume.index(publication) + len(publication)),
            Evidence(direct, "resume_full", resume.index(direct), resume.index(direct) + len(direct)),
        ]
        result = filter_evidence_candidates_v43([candidate], resume)
        kept = [e.text for e in result.candidates[0].evidence]
        self.assertNotIn(publication, kept)
        self.assertIn(direct, kept)

    def test_ai01_application_only_is_not_machine_learning_support(self):
        registry = TeamSkillRegistry()
        auditor = TeamSkillAuditorV4(registry)
        text = "利用AI模型(HapticGen)生成多频带的逼真震感"
        candidate = make_candidate("source_1", text, 0)
        assessment = ModelTeamSkillAssessment(
            team_skill_id="T-AI-01",
            status="supported",
            support_evidence=(text,),
            reason="model said supported",
            confidence=0.8,
            atomic_ability="AI模型应用",
        )
        audited = auditor.audit(candidate, assessment)
        self.assertEqual(audited.final_status, "unsupported")
        self.assertIn("ai01_application_only_not_ml_dl_evidence", audited.audit_flags)

    def test_ai01_training_evidence_remains_supported(self):
        registry = TeamSkillRegistry()
        auditor = TeamSkillAuditorV4(registry)
        text = "使用PyTorch训练ResNet模型并进行模型评估"
        candidate = make_candidate("source_1", text, 0)
        assessment = ModelTeamSkillAssessment(
            team_skill_id="T-AI-01",
            status="supported",
            support_evidence=(text,),
            reason="direct training",
            confidence=0.9,
            atomic_ability="模型训练",
        )
        audited = auditor.audit(candidate, assessment)
        self.assertEqual(audited.final_status, "supported")


if __name__ == "__main__":
    unittest.main()
