import unittest

from extractor.agentic_llm_client import AgenticLLMResponseError, LLMCompletion
from extractor.team_skill_fallback_selector_v3 import FallbackSelectorError
from extractor.team_skill_fallback_selector_v4 import FallbackTeamSkillSelectorV4


class _FailingJsonClient:
    def __init__(self):
        self.max_tokens = None

    def complete_json(self, system_prompt, user_prompt, *, max_tokens):
        self.max_tokens = max_tokens
        raise AgenticLLMResponseError("response content must be non-empty text")


class _BudgetProbeClient:
    def __init__(self):
        self.max_tokens = None

    def complete_json(self, system_prompt, user_prompt, *, max_tokens):
        self.max_tokens = max_tokens
        return LLMCompletion(
            content='{"selections":[]}',
            model="probe",
            usage=None,
            elapsed_ms=1.0,
            raw_response_metadata={},
        )


class FallbackSelectorV4ReliabilityTests(unittest.TestCase):
    def test_model_response_error_is_normalized_for_pipeline_guard(self):
        client = _FailingJsonClient()
        selector = FallbackTeamSkillSelectorV4(client)
        with self.assertRaises(FallbackSelectorError):
            selector._complete_json("system", "user")
        self.assertEqual(client.max_tokens, 8192)

    def test_selector_uses_larger_reasoning_budget(self):
        client = _BudgetProbeClient()
        selector = FallbackTeamSkillSelectorV4(client)
        completion = selector._complete_json("system", "user")
        self.assertEqual(client.max_tokens, 8192)
        self.assertEqual(completion.model, "probe")


if __name__ == "__main__":
    unittest.main()
