import json
import unittest

from extractor.agentic_llm_client import LLMCompletion
from extractor.proficiency_evaluator import ProficiencyEvaluator
from extractor.team_skill_proficiency_bridge_v434 import (
    build_proficiency_evaluator_inputs,
    is_proficiency_assessable,
)
from extractor.team_skill_schema_v3 import (
    CandidateSkillProfile,
    EvidenceObservation,
    TeamSkillAssessment,
)


def observation(text="独立完成定义明确的软件模块开发并完成验证", start=20):
    return EvidenceObservation(
        text=text,
        source_experience_id="project_001",
        start=start,
        end=start + len(text),
    )


def assessment(
    skill_id,
    *,
    status="supported",
    inference_mode="direct_behavior",
    evidence=None,
    audit_flags=(),
):
    if evidence is None:
        evidence = () if status == "unsupported" else (observation(),)
    return TeamSkillAssessment(
        candidate_id="candidate_bridge",
        team_skill_id=skill_id,
        team_skill_name=f"Skill {skill_id}",
        status=status,
        inference_mode=inference_mode,
        evidence=tuple(evidence),
        reason="frozen assessment",
        confidence=0.9,
        atomic_abilities=() if status == "unsupported" else (f"{skill_id}｜evidence",),
        audit_flags=tuple(audit_flags),
    )


def profile(*assessments):
    return CandidateSkillProfile(
        candidate_id="candidate_bridge",
        skill_registry_version="0.4",
        assessments=tuple(assessments),
        metadata={"schema_version": "candidate_skill_profile_v4_3_4"},
    )


class FakeClient:
    def __init__(self):
        self.calls = []

    def complete(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, json.loads(user_prompt)))
        reason = "独立完成定义明确的任务并完成验证"
        payload = {
            "evidence_sufficiency": "sufficient",
            "dimensions": {
                key: {"level": "P2", "reason": reason}
                for key in ("D1", "D2", "D3", "D4")
            },
            "final_level": "P2",
            "reason": reason,
            "uncertainty": [],
        }
        return LLMCompletion(
            content=json.dumps(payload, ensure_ascii=False),
            model="fake-proficiency-model",
            usage=None,
            elapsed_ms=1.0,
            raw_response_metadata={},
        )


class TeamSkillProficiencyBridgeV434Tests(unittest.TestCase):
    def test_direct_supported_skill_becomes_assessable(self):
        item = assessment("T-SW-01")
        self.assertTrue(is_proficiency_assessable(item))
        prepared = build_proficiency_evaluator_inputs(profile(item))
        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0].team_skill_id, "T-SW-01")
        self.assertEqual(prepared[0].ability.category["ability_id"], "T-SW-01")

    def test_evidence_spans_are_preserved(self):
        first = observation("实现模块A", 30)
        second = EvidenceObservation(
            text="完成模块A验证",
            source_experience_id="project_002",
            start=80,
            end=87,
        )
        prepared = build_proficiency_evaluator_inputs(
            profile(assessment("T-SW-01", evidence=(first, second)))
        )[0]
        self.assertEqual(
            [(item.text, item.project_id, item.start, item.end) for item in prepared.evidence],
            [
                (first.text, first.source_experience_id, first.start, first.end),
                (second.text, second.source_experience_id, second.start, second.end),
            ],
        )
        self.assertEqual(
            [item.to_dict() for item in prepared.ability.evidence],
            [item.to_dict() for item in prepared.evidence],
        )

    def test_partial_unsupported_warrant_and_aggregate_are_skipped(self):
        items = (
            assessment("T-AI-01", status="partially_supported"),
            assessment("T-AI-02", status="unsupported"),
            assessment("T-AI-03", audit_flags=("supported_warrant", "warrant:test")),
            assessment("F-2-03", inference_mode="aggregate_signal"),
        )
        prepared = build_proficiency_evaluator_inputs(profile(*items))
        self.assertEqual(prepared, ())
        self.assertTrue(all(not is_proficiency_assessable(item) for item in items))

    def test_optional_target_filter_does_not_promote_ineligible_skills(self):
        direct = assessment("T-SW-01")
        partial = assessment("T-DA-04", status="partially_supported")
        prepared = build_proficiency_evaluator_inputs(
            profile(direct, partial),
            target_team_skill_ids=("T-DA-04",),
        )
        self.assertEqual(prepared, ())

    def test_existing_evaluator_accepts_bridge_contract_with_fake_client(self):
        prepared = build_proficiency_evaluator_inputs(
            profile(assessment("T-SW-01"))
        )[0]
        client = FakeClient()
        result = ProficiencyEvaluator(client).evaluate(*prepared.evaluator_args())
        self.assertEqual(result.ability_id, "T-SW-01")
        self.assertEqual(result.final_level, "P2")
        self.assertEqual(len(client.calls), 1)
        sent = client.calls[0][1]
        self.assertEqual(sent["evidence"][0]["start"], 20)
        self.assertEqual(sent["evidence"][0]["end"], 20 + len(observation().text))


if __name__ == "__main__":
    unittest.main()
