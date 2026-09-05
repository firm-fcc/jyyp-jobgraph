
import unittest

from extractor.agentic_schema import CandidateAbility, CandidateStatus, Evidence
from extractor.team_skill_auditor_v3 import TeamSkillAuditorV3
from extractor.team_skill_candidate_generator_v3 import TeamSkillCandidatePool
from extractor.team_skill_fallback_selector_v3 import (
    FallbackSelectorError,
    FallbackSelectionResult,
    FallbackSkillSelection,
)
from extractor.team_skill_pipeline_v3 import TeamSkillLinkingPipelineV3
from extractor.team_skill_registry import TeamSkillRegistry
from extractor.team_skill_verifier_v3 import ModelTeamSkillAssessment, TeamSkillVerificationResult


def candidate(cid: str) -> CandidateAbility:
    text = f"evidence-{cid}"
    return CandidateAbility(
        candidate_id=cid,
        resume_id="candidate_1",
        project_id="resume_full",
        fact=text,
        behavior=text,
        ability=text,
        normalized_ability=text,
        category={},
        evidence=[Evidence(text=text, project_id="resume_full", start=0, end=len(text))],
        reason="test",
        confidence=0.9,
        source="test",
        revision_round=0,
        parent_candidate_id=None,
        status=CandidateStatus.PENDING_REVIEW,
        lineage=[cid],
    )


class MissGenerator:
    def __init__(self, registry):
        self.registry = registry

    def generate(self, evidence_candidate, **kwargs):
        return TeamSkillCandidatePool(
            skills=(),
            ranked=(),
            fallback_all=False,
            retrieval_text=evidence_candidate.evidence[0].text,
            located_evidence_count=1,
        )


class LexicalGenerator:
    def __init__(self, registry):
        self.registry = registry

    def generate(self, evidence_candidate, **kwargs):
        return TeamSkillCandidatePool(
            skills=(self.registry.get("T-SW-01"),),
            ranked=(),
            fallback_all=False,
            retrieval_text=evidence_candidate.evidence[0].text,
            located_evidence_count=1,
        )


class FailLargeSelector:
    def __init__(self):
        self.calls = 0

    def select(self, *, candidate_id, evidence_candidates, candidate_skills, max_candidates):
        self.calls += 1
        if len(evidence_candidates) > 4:
            raise FallbackSelectorError("simulated resume-wide selector failure")
        return FallbackSelectionResult(
            selections=tuple(
                FallbackSkillSelection(item.candidate_id, ("T-SW-02",))
                for item in evidence_candidates
            ),
            model="fake",
            elapsed_ms=0.1,
            usage=None,
            contract_retry_count=0,
        )


class AlwaysFailSelector:
    def select(self, **kwargs):
        raise FallbackSelectorError("simulated persistent failure")


class RecordingVerifier:
    def __init__(self):
        self.calls = 0
        self.skill_ids = []

    def verify(self, *, candidate_id, evidence_candidate, candidate_skills):
        self.calls += 1
        self.skill_ids.extend(skill.code for skill in candidate_skills)
        quote = evidence_candidate.evidence[0].text
        return TeamSkillVerificationResult(
            candidate_id=candidate_id,
            source_candidate_ability_id=evidence_candidate.candidate_id,
            assessments=tuple(
                ModelTeamSkillAssessment(
                    team_skill_id=skill.code,
                    status="supported",
                    support_evidence=(quote,),
                    reason="test",
                    confidence=0.9,
                    atomic_ability=quote,
                )
                for skill in candidate_skills
            ),
            model="fake",
            elapsed_ms=0.1,
            usage=None,
            contract_retry_count=0,
        )


class R433SelectorRecoveryTests(unittest.TestCase):
    def test_failed_resume_wide_selector_recovers_in_small_batches(self):
        registry = TeamSkillRegistry()
        selector = FailLargeSelector()
        verifier = RecordingVerifier()
        pipeline = TeamSkillLinkingPipelineV3(
            MissGenerator(registry),
            verifier,
            TeamSkillAuditorV3(registry),
            fallback_selector=selector,
        )
        result = pipeline.link(
            candidate_id="candidate_1",
            evidence_candidates=[candidate(f"s{i}") for i in range(5)],
            top_k=8,
        )
        self.assertEqual(selector.calls, 3)  # 1 failed full batch + 2 recovery batches
        self.assertEqual(result.diagnostics.fallback_selector_failure_count, 0)
        self.assertEqual(result.diagnostics.full_fallback_verifier_call_count, 0)
        self.assertEqual(verifier.calls, 5)
        self.assertTrue(verifier.skill_ids)
        self.assertEqual(set(verifier.skill_ids), {"T-SW-02"})

    def test_persistent_selector_failure_never_triggers_all_registry_verification(self):
        registry = TeamSkillRegistry()
        verifier = RecordingVerifier()
        pipeline = TeamSkillLinkingPipelineV3(
            MissGenerator(registry),
            verifier,
            TeamSkillAuditorV3(registry),
            fallback_selector=AlwaysFailSelector(),
        )
        result = pipeline.link(
            candidate_id="candidate_1",
            evidence_candidates=[candidate("s1")],
            top_k=8,
        )
        self.assertEqual(result.diagnostics.fallback_selector_failure_count, 1)
        self.assertEqual(result.diagnostics.full_fallback_verifier_call_count, 0)
        self.assertEqual(verifier.calls, 0)

    def test_persistent_selector_failure_keeps_lexical_hits(self):
        registry = TeamSkillRegistry()
        verifier = RecordingVerifier()
        pipeline = TeamSkillLinkingPipelineV3(
            LexicalGenerator(registry),
            verifier,
            TeamSkillAuditorV3(registry),
            fallback_selector=AlwaysFailSelector(),
        )
        result = pipeline.link(
            candidate_id="candidate_1",
            evidence_candidates=[candidate("s1")],
            top_k=8,
        )
        self.assertEqual(result.diagnostics.full_fallback_verifier_call_count, 0)
        self.assertEqual(verifier.calls, 1)
        self.assertEqual(set(verifier.skill_ids), {"T-SW-01"})


if __name__ == "__main__":
    unittest.main()
