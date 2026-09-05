import json
import unittest

from extractor.agentic_llm_client import LLMCompletion
from extractor.agentic_schema import CandidateAbility, CandidateStatus, Evidence
from extractor.proficiency_evaluator import (
    ProficiencyEvaluator,
    ProficiencyParseError,
)
from extractor.review_assessment_schema import EvidenceAuditResult


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def complete(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, json.loads(user_prompt)))
        return LLMCompletion(
            content=json.dumps(self.payload, ensure_ascii=False),
            model="fake-proficiency-model",
            usage={"prompt_tokens": 10, "completion_tokens": 10},
            elapsed_ms=1.0,
            raw_response_metadata={},
        )


def model_payload(level, sufficiency, evidence_reason):
    return {
        "evidence_sufficiency": sufficiency,
        "dimensions": {
            key: {"level": level, "reason": evidence_reason}
            for key in ("D1", "D2", "D3", "D4")
        },
        "final_level": level,
        "reason": evidence_reason,
        "uncertainty": [] if sufficiency == "sufficient" else ["证据细节有限"],
    }


def candidate_and_audit(evidence_text):
    evidence = Evidence(text=evidence_text, project_id="pilot")
    candidate = CandidateAbility(
        candidate_id="pilot-candidate",
        resume_id="pilot-resume",
        project_id="pilot",
        fact="已确认存在机器学习模型训练能力",
        behavior=evidence_text,
        ability="机器学习模型训练",
        normalized_ability="机器学习模型训练",
        category={"ability_id": "pilot.machine_learning_training"},
        evidence=[evidence],
        reason="上游已确认能力存在",
        confidence=1.0,
        source="proficiency_pilot",
        revision_round=0,
        parent_candidate_id=None,
        status=CandidateStatus.APPROVED,
    )
    audit = EvidenceAuditResult.from_dict(
        {
            "schema_version": "evidence_audit_result_v1",
            "resume_id": candidate.resume_id,
            "candidate_id": candidate.candidate_id,
            "current_evidence_audits": [],
            "taxonomy_subset_ids": [],
            "taxonomy_selection_trace": [],
            "component_assessments": [],
            "evidence_decision": "sufficient",
            "recommended_relocation_span_ids": [],
            "compound_label": "not_compound",
            "blocking_issues": [],
            "non_blocking_notes": ["pilot ability existence confirmed upstream"],
            "requires_model_review": False,
            "diagnostics": {"source": "proficiency_pilot"},
        }
    )
    return candidate, [evidence], audit


class ProficiencyEvaluatorTests(unittest.TestCase):
    def evaluate(self, evidence_text, payload):
        candidate, evidence, audit = candidate_and_audit(evidence_text)
        evaluator = ProficiencyEvaluator(FakeClient(payload))
        return evaluator.evaluate(candidate, evidence, audit)

    def test_insufficient_evidence_allows_u(self):
        result = self.evaluate(
            "研究大语言模型微调及持续学习",
            model_payload("U", "insufficient", "只说明研究主题，无法区分熟练度"),
        )
        self.assertEqual(result.final_level, "U")
        self.assertFalse(result.review_required)

    def test_defined_independent_task_supports_p2(self):
        result = self.evaluate(
            "独立完成定义明确的分类模型训练，选择常规模型和评估指标并完成验证。",
            model_payload("P2", "sufficient", "独立完成完整任务并作常规选择"),
        )
        self.assertEqual(result.final_level, "P2")
        self.assertFalse(result.review_required)

    def test_complex_problem_with_judgment_supports_p3(self):
        result = self.evaluate(
            "针对复杂非例行训练问题，比较三种方案，诊断过拟合原因并迭代优化。",
            model_payload("P3", "sufficient", "复杂问题同时具有比较、诊断和优化"),
        )
        self.assertEqual(result.final_level, "P3")
        self.assertFalse(result.review_required)

    def test_multiple_high_level_signals_support_p4(self):
        result = self.evaluate(
            "主导跨系统模型训练，解决高度复杂的瓶颈，制定关键策略并带领团队落地，系统指标提升20%。",
            model_payload("P4", "sufficient", "主导复杂问题、制定策略并产生系统影响"),
        )
        self.assertEqual(result.final_level, "P4")
        self.assertFalse(result.review_required)

    def test_validator_flags_contradiction_without_changing_level(self):
        result = self.evaluate(
            "研究大语言模型微调及持续学习",
            model_payload("P4", "insufficient", "使用 LLM 和 LoRA，判断为高级"),
        )
        self.assertEqual(result.final_level, "P4")
        self.assertTrue(result.review_required)
        self.assertIn(
            "insufficient_evidence_level_conflict", result.validator_flags
        )
        self.assertIn("technology_name_inflation", result.validator_flags)
        self.assertIn(
            "p4_insufficient_high_level_signals", result.validator_flags
        )

    def test_markdown_wrapped_json_is_rejected(self):
        candidate, evidence, audit = candidate_and_audit("独立完成模型训练。")
        payload = model_payload("P2", "sufficient", "独立完成完整任务")
        client = FakeClient(payload)
        completion = client.complete("ignored", "{}")
        client.complete = lambda _system, _user: LLMCompletion(
            content="```json\n" + completion.content + "\n```",
            model=completion.model,
            usage=completion.usage,
            elapsed_ms=completion.elapsed_ms,
            raw_response_metadata=completion.raw_response_metadata,
        )
        with self.assertRaises(ProficiencyParseError):
            ProficiencyEvaluator(client).evaluate(candidate, evidence, audit)


if __name__ == "__main__":
    unittest.main()
