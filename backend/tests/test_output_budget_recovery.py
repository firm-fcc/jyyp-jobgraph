"""输出预算耗尽的识别与处置。

推理型模型把思维链计入 completion_tokens，因而也计入 max_tokens。预算在
思维链上耗尽时，服务端以 finish_reason="length" 返回空正文。此前该回复被
判为一般协议违例，客户端以同一组参数重发两次；思维链长度由输入决定，重发
注定在同处耗尽，整条抽取链因而必然失败。本组用例锁定改后的行为：识别成
单独一类、重试时抬高预算、预算已至上限时即刻退出。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidate_core"))

from extractor.agentic_llm_client import (  # noqa: E402
    AgenticLLMClient,
    AgenticLLMOutputBudgetError,
    AgenticLLMResponseError,
    LLMCompletion,
    ReliableCompletionClient,
    TransportResponse,
)
from extractor.v3_client_adapter import JsonModeCompletionAdapter  # noqa: E402


def _body(content: str, finish_reason: str, *, reasoning_tokens: int = 0) -> str:
    return json.dumps(
        {
            "id": "resp",
            "model": "reasoning-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 4096,
                "total_tokens": 4196,
                "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
            },
        }
    )


def _client(transport) -> AgenticLLMClient:
    return AgenticLLMClient(
        api_key="k",
        base_url="https://example.invalid/chat/completions",
        model="reasoning-model",
        timeout=5.0,
        transport=transport,
    )


def test_empty_content_with_length_finish_is_an_output_budget_error():
    def transport(url, headers, payload, timeout):
        return TransportResponse(
            status_code=200,
            body=_body("", "length", reasoning_tokens=4096),
        )

    with pytest.raises(AgenticLLMOutputBudgetError) as caught:
        _client(transport).complete("s", "u", max_tokens=4096)

    error = caught.value
    assert error.finish_reason == "length"
    assert error.max_tokens == 4096
    assert error.reasoning_tokens == 4096
    assert error.safe_diagnostics()["completion_tokens"] == 4096
    # 仍属协议错误的一种，既有的捕获点不因新类而漏接
    assert isinstance(error, AgenticLLMResponseError)


def test_empty_content_without_length_finish_stays_a_plain_response_error():
    def transport(url, headers, payload, timeout):
        return TransportResponse(status_code=200, body=_body("", "stop"))

    with pytest.raises(AgenticLLMResponseError) as caught:
        _client(transport).complete("s", "u", max_tokens=4096)
    assert not isinstance(caught.value, AgenticLLMOutputBudgetError)


def test_retry_raises_the_budget_instead_of_repeating_the_same_request():
    budgets: list[int] = []

    def transport(url, headers, payload, timeout):
        budgets.append(payload["max_tokens"])
        if len(budgets) < 3:
            return TransportResponse(
                status_code=200, body=_body("", "length", reasoning_tokens=4096)
            )
        return TransportResponse(status_code=200, body=_body('{"ok":1}', "stop"))

    reliable = ReliableCompletionClient(
        _client(transport),
        max_technical_retries=2,
        backoff_seconds=(0.0, 0.0),
        sleeper=lambda _seconds: None,
    )
    completion = reliable.complete("s", "u", max_tokens=8192)

    assert isinstance(completion, LLMCompletion)
    assert budgets == [8192, 16384, 32768]
    diagnostics = reliable.retry_diagnostics().to_dict()
    assert diagnostics["output_budget_retry_count"] == 2
    # 预算耗尽不再计入一般协议重试，两类失败在诊断里分得开
    assert diagnostics["api_response_retry_count"] == 0
    assert diagnostics["last_output_budget_error"]["finish_reason"] == "length"


def test_budget_at_the_cap_fails_at_once_without_further_attempts():
    attempts: list[int] = []

    def transport(url, headers, payload, timeout):
        attempts.append(payload["max_tokens"])
        return TransportResponse(
            status_code=200, body=_body("", "length", reasoning_tokens=4096)
        )

    reliable = ReliableCompletionClient(
        _client(transport),
        max_technical_retries=2,
        backoff_seconds=(0.0, 0.0),
        sleeper=lambda _seconds: None,
        output_budget_cap=8192,
    )
    with pytest.raises(AgenticLLMOutputBudgetError):
        reliable.complete("s", "u", max_tokens=8192)

    # 预算已至上限时不再空等两轮长调用
    assert attempts == [8192]


def test_growth_stops_at_the_cap():
    budgets: list[int] = []

    def transport(url, headers, payload, timeout):
        budgets.append(payload["max_tokens"])
        return TransportResponse(
            status_code=200, body=_body("", "length", reasoning_tokens=4096)
        )

    reliable = ReliableCompletionClient(
        _client(transport),
        max_technical_retries=2,
        backoff_seconds=(0.0, 0.0),
        sleeper=lambda _seconds: None,
        output_budget_cap=12288,
    )
    with pytest.raises(AgenticLLMOutputBudgetError):
        reliable.complete("s", "u", max_tokens=8192)
    assert budgets == [8192, 12288]


def test_adapter_lifts_a_call_site_budget_to_the_configured_floor():
    seen: list[int] = []

    class Recorder:
        def complete_json(self, system_prompt, user_prompt, *, max_tokens=None):
            seen.append(max_tokens)
            return LLMCompletion("{}", "m", None, 1.0, {})

    adapter = JsonModeCompletionAdapter(
        Recorder(), max_tokens=32768, min_output_tokens=32768
    )
    # 各环节按"答案有多长"写死的预算，在推理模型下一律抬到配置的下限
    adapter.complete_json("s", "u", max_tokens=8192)
    adapter.complete("s", "u")
    assert seen == [32768, 32768]


def test_adapter_without_a_floor_keeps_the_requested_budget():
    seen: list[int] = []

    class Recorder:
        def complete_json(self, system_prompt, user_prompt, *, max_tokens=None):
            seen.append(max_tokens)
            return LLMCompletion("{}", "m", None, 1.0, {})

    adapter = JsonModeCompletionAdapter(Recorder(), max_tokens=16384)
    adapter.complete_json("s", "u", max_tokens=8192)
    assert seen == [8192]
