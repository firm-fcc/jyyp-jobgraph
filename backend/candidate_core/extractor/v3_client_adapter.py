"""Small runtime adapters used by the V3 CLI.

No provider-specific SDK is required. The adapter prefers JSON response mode
when the wrapped OpenAI-compatible client exposes ``complete_json``.
"""

from __future__ import annotations

from extractor.agentic_llm_client import LLMCompletion


class JsonModeCompletionAdapter:
    """Expose a plain ``complete`` method while preferring JSON response mode.

    ``min_output_tokens`` 抬高各调用点自带的输出预算。上游各环节按"这一问的
    答案有多长"写死了预算，该估计在非推理模型下成立；推理型模型把思维链一并
    计入同一预算，实测思维链可达答案本身的数倍，写死值因而普遍偏紧。预算是
    上限而非用量，抬高它不改变正常调用的开销，只在思维链超长时留出余地。
    """

    def __init__(
        self,
        client,
        *,
        max_tokens: int = 16384,
        min_output_tokens: int | None = None,
    ) -> None:
        self.client = client
        self.max_tokens = max_tokens
        self.min_output_tokens = min_output_tokens

    def _budget(self, requested: int | None) -> int:
        budget = requested or self.max_tokens
        if self.min_output_tokens is not None:
            budget = max(budget, self.min_output_tokens)
        return budget

    def complete(self, system_prompt: str, user_prompt: str) -> LLMCompletion:
        complete_json = getattr(self.client, "complete_json", None)
        if callable(complete_json):
            return complete_json(
                system_prompt,
                user_prompt,
                max_tokens=self._budget(None),
            )
        return self.client.complete(system_prompt, user_prompt)

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int | None = None,
    ) -> LLMCompletion:
        complete_json = getattr(self.client, "complete_json", None)
        if callable(complete_json):
            return complete_json(
                system_prompt,
                user_prompt,
                max_tokens=self._budget(max_tokens),
            )
        return self.client.complete(system_prompt, user_prompt)
