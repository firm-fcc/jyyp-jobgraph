import json
import unittest

from extractor.agentic_llm_client import LLMCompletion
from extractor.agentic_schema import CandidateAbility, CandidateStatus, Evidence
from extractor.team_skill_registry import TeamSkillRegistry
from extractor.team_skill_verifier_v3 import EvidenceSkillVerifierV3, TeamSkillVerifierContractError


class FakeClient:
    def __init__(self, payloads):
        self.payloads = payloads if isinstance(payloads, list) else [payloads]
        self.calls = 0

    def complete(self, system_prompt, user_prompt):
        payload = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        return LLMCompletion(
            content=content,
            model="fake",
            usage=None,
            elapsed_ms=1.0,
            raw_response_metadata={},
        )


def make_candidate(located=True):
    text = "使用PyTorch训练ResNet-18模型"
    return CandidateAbility(
        candidate_id="source_1",
        resume_id="candidate_1",
        project_id="exp_1",
        fact=text,
        behavior="训练并评估图像分类模型",
        ability="图像分类模型训练",
        normalized_ability="图像分类模型训练",
        category={},
        evidence=[Evidence(
            text=text,
            project_id="exp_1",
            start=0 if located else None,
            end=len(text) if located else None,
        )],
        reason="直接训练模型",
        confidence=0.9,
        source="test",
        revision_round=0,
        parent_candidate_id=None,
        status=CandidateStatus.PENDING_REVIEW,
        lineage=["source_1"],
    )


def valid_payload(skill_id="T-AI-01"):
    return {
        "assessments": [{
            "team_skill_id": skill_id,
            "status": "supported",
            "support_evidence": ["使用PyTorch训练ResNet-18模型"],
            "reason": "存在直接模型训练行为",
            "confidence": 0.95,
            "atomic_ability": "ResNet图像分类模型训练",
        }]
    }


class TeamSkillVerifierV3Tests(unittest.TestCase):
    def test_strict_candidate_set(self):
        registry = TeamSkillRegistry()
        skill = registry.get("T-AI-01")
        result = EvidenceSkillVerifierV3(FakeClient(valid_payload())).verify(
            candidate_id="candidate_1", evidence_candidate=make_candidate(), candidate_skills=[skill]
        )
        self.assertEqual(result.assessments[0].team_skill_id, "T-AI-01")
        self.assertEqual(result.contract_retry_count, 0)

    def test_rejects_invented_skill_after_retry(self):
        registry = TeamSkillRegistry()
        skill = registry.get("T-AI-01")
        bad = valid_payload("T-AI-99")
        client = FakeClient([bad, bad])
        with self.assertRaises(TeamSkillVerifierContractError):
            EvidenceSkillVerifierV3(client).verify(
                candidate_id="candidate_1", evidence_candidate=make_candidate(), candidate_skills=[skill]
            )
        self.assertEqual(client.calls, 2)

    def test_contract_retry_can_recover(self):
        registry = TeamSkillRegistry()
        skill = registry.get("T-AI-01")
        client = FakeClient(["not-json", valid_payload()])
        result = EvidenceSkillVerifierV3(client).verify(
            candidate_id="candidate_1", evidence_candidate=make_candidate(), candidate_skills=[skill]
        )
        self.assertEqual(result.contract_retry_count, 1)
        self.assertEqual(client.calls, 2)

    def test_strict_json_rejects_duplicate_keys_and_nan(self):
        with self.assertRaises(TeamSkillVerifierContractError):
            EvidenceSkillVerifierV3._parse(
                '{"assessments":[],"assessments":[]}', ["T-AI-01"]
            )
        bad_nan = '{"assessments":[{"team_skill_id":"T-AI-01","status":"unsupported","support_evidence":[],"reason":"x","confidence":NaN,"atomic_ability":null}]}'
        with self.assertRaises(TeamSkillVerifierContractError):
            EvidenceSkillVerifierV3._parse(bad_nan, ["T-AI-01"])

    def test_refuses_candidate_without_located_evidence(self):
        registry = TeamSkillRegistry()
        skill = registry.get("T-AI-01")
        with self.assertRaises(ValueError):
            EvidenceSkillVerifierV3(FakeClient(valid_payload())).verify(
                candidate_id="candidate_1", evidence_candidate=make_candidate(False), candidate_skills=[skill]
            )


if __name__ == "__main__":
    unittest.main()
