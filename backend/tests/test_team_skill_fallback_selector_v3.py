import json
import unittest

from extractor.agentic_llm_client import LLMCompletion
from extractor.agentic_schema import CandidateAbility, CandidateStatus, Evidence
from extractor.team_skill_fallback_selector_v3 import (
    FallbackSelectorContractError,
    FallbackTeamSkillSelectorV3,
)
from extractor.team_skill_registry import TeamSkillRegistry


class FakeClient:
    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = 0

    def complete_json(self, system_prompt, user_prompt, max_tokens=4096):
        content = self.contents[min(self.calls, len(self.contents)-1)]
        self.calls += 1
        return LLMCompletion(
            content=content,
            model="fake",
            usage={"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            elapsed_ms=1.0,
            raw_response_metadata={},
        )


def make_candidate(cid="c1"):
    text = "研究RGB与视触觉融合的物体姿态估计"
    return CandidateAbility(
        candidate_id=cid,
        resume_id="r1",
        project_id="resume_full",
        fact=text,
        behavior="研究物体姿态估计方法",
        ability="多模态物体姿态估计",
        normalized_ability="多模态物体姿态估计",
        category={},
        evidence=[Evidence(text=text, project_id="resume_full", start=0, end=len(text))],
        reason="test",
        confidence=0.8,
        source="test",
        revision_round=0,
        parent_candidate_id=None,
        status=CandidateStatus.PENDING_REVIEW,
        lineage=[cid],
    )


class FallbackSelectorTests(unittest.TestCase):
    def test_valid_selection(self):
        client = FakeClient([
            json.dumps({"selections": [{"source_candidate_ability_id": "c1", "team_skill_ids": ["T-AI-03"]}]})
        ])
        selector = FallbackTeamSkillSelectorV3(client)
        result = selector.select(
            candidate_id="candidate_1",
            evidence_candidates=[make_candidate()],
            candidate_skills=TeamSkillRegistry().primary(),
            max_candidates=6,
        )
        self.assertEqual(result.selections[0].team_skill_ids, ("T-AI-03",))
        self.assertEqual(result.contract_retry_count, 0)

    def test_contract_retry(self):
        client = FakeClient([
            '{"selections": []}',
            json.dumps({"selections": [{"source_candidate_ability_id": "c1", "team_skill_ids": ["T-AI-03"]}]})
        ])
        selector = FallbackTeamSkillSelectorV3(client)
        result = selector.select(
            candidate_id="candidate_1",
            evidence_candidates=[make_candidate()],
            candidate_skills=TeamSkillRegistry().primary(),
            max_candidates=6,
        )
        self.assertEqual(result.contract_retry_count, 1)
        self.assertEqual(client.calls, 2)

    def test_unknown_skill_is_rejected_after_retry(self):
        bad = json.dumps({"selections": [{"source_candidate_ability_id": "c1", "team_skill_ids": ["NOPE"]}]})
        selector = FallbackTeamSkillSelectorV3(FakeClient([bad, bad]))
        with self.assertRaises(FallbackSelectorContractError):
            selector.select(
                candidate_id="candidate_1",
                evidence_candidates=[make_candidate()],
                candidate_skills=TeamSkillRegistry().primary(),
                max_candidates=6,
            )


if __name__ == "__main__":
    unittest.main()
