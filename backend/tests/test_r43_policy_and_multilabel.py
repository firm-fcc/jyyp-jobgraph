import unittest

from extractor.agentic_schema import CandidateAbility, CandidateStatus, Evidence
from extractor.evidence_source_policy_v43 import filter_evidence_candidates_v43
from extractor.team_skill_auditor_v3 import TeamSkillAuditorV3
from extractor.team_skill_candidate_generator_v3 import TeamSkillCandidatePool
from extractor.team_skill_fallback_selector_v3 import FallbackSelectionResult, FallbackSkillSelection
from extractor.team_skill_pipeline_v3 import TeamSkillLinkingPipelineV3
from extractor.team_skill_registry import TeamSkillRegistry
from extractor.team_skill_verifier_v3 import ModelTeamSkillAssessment, TeamSkillVerificationResult


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
        evidence=[Evidence(text=text, project_id="resume_full", start=start, end=start+len(text))],
        reason="test",
        confidence=0.9,
        source="test",
        revision_round=0,
        parent_candidate_id=None,
        status=CandidateStatus.PENDING_REVIEW,
        lineage=[cid],
    )


class FixedGenerator:
    def __init__(self, registry):
        self.registry = registry
    def generate(self, evidence_candidate, **kwargs):
        skill=self.registry.get("T-AI-01")
        return TeamSkillCandidatePool(
            skills=(skill,), ranked=(), fallback_all=False,
            retrieval_text=evidence_candidate.evidence[0].text,
            located_evidence_count=1,
        )


class FixedSelector:
    def __init__(self):
        self.calls=0
    def select(self, *, candidate_id, evidence_candidates, candidate_skills, max_candidates):
        self.calls += 1
        return FallbackSelectionResult(
            selections=tuple(
                FallbackSkillSelection(item.candidate_id, ("T-AI-03",))
                for item in evidence_candidates
            ),
            model="fake", elapsed_ms=0.1, usage=None, contract_retry_count=0,
        )


class RecordingVerifier:
    def __init__(self):
        self.skill_ids=[]
    def verify(self, *, candidate_id, evidence_candidate, candidate_skills):
        self.skill_ids.extend(skill.code for skill in candidate_skills)
        quote=evidence_candidate.evidence[0].text
        assessments=tuple(
            ModelTeamSkillAssessment(
                team_skill_id=skill.code, status="supported",
                support_evidence=(quote,), reason="test",
                confidence=0.9, atomic_ability=quote,
            )
            for skill in candidate_skills
        )
        return TeamSkillVerificationResult(
            candidate_id=candidate_id,
            source_candidate_ability_id=evidence_candidate.candidate_id,
            assessments=assessments,
            model="fake", elapsed_ms=0.1, usage=None, contract_retry_count=0,
        )


class R43Tests(unittest.TestCase):
    def test_lexical_hit_does_not_suppress_semantic_second_skill(self):
        registry=TeamSkillRegistry()
        verifier=RecordingVerifier()
        selector=FixedSelector()
        pipeline=TeamSkillLinkingPipelineV3(
            FixedGenerator(registry), verifier, TeamSkillAuditorV3(registry),
            fallback_selector=selector,
        )
        result=pipeline.link(
            candidate_id="candidate_1",
            evidence_candidates=[make_candidate("source_1","使用PyTorch训练人脸识别模型")],
            top_k=8,
        )
        self.assertEqual(selector.calls,1)
        self.assertIn("T-AI-01",verifier.skill_ids)
        self.assertIn("T-AI-03",verifier.skill_ids)
        supported={x.team_skill_id for x in result.aggregated_skills if x.final_status=="supported"}
        self.assertTrue({"T-AI-01","T-AI-03"}.issubset(supported))

    def test_result_only_and_nontechnical_campus_evidence_are_removed(self):
        resume=(
            "项目经历\n使用Python开发模型\n"
            "期刊论文\nTraffic Forecasting with Transformer\n"
            "校园经历\n统筹共青团事务并策划校级活动\n"
            "校园经历\n负责机器人系统测试与设备调试\n"
        )
        direct="使用Python开发模型"
        paper="Traffic Forecasting with Transformer"
        admin="统筹共青团事务并策划校级活动"
        tech="负责机器人系统测试与设备调试"
        candidate=make_candidate("source_1", direct, resume.index(direct))
        candidate.evidence=[
            Evidence(direct,"resume_full",resume.index(direct),resume.index(direct)+len(direct)),
            Evidence(paper,"resume_full",resume.index(paper),resume.index(paper)+len(paper)),
            Evidence(admin,"resume_full",resume.index(admin),resume.index(admin)+len(admin)),
            Evidence(tech,"resume_full",resume.index(tech),resume.index(tech)+len(tech)),
        ]
        result=filter_evidence_candidates_v43([candidate],resume)
        kept=[e.text for e in result.candidates[0].evidence]
        self.assertIn(direct,kept)
        self.assertIn(tech,kept)
        self.assertNotIn(paper,kept)
        self.assertNotIn(admin,kept)
        self.assertEqual(result.dropped_evidence_count,2)


if __name__=="__main__":
    unittest.main()
