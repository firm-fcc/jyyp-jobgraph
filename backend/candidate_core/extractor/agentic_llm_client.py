"""Independent OpenAI-compatible client for the stage 2 extraction agent.

Configuration is explicit and no environment file is read at import time.  The
transport can be replaced with a callable for deterministic offline tests.
"""

from __future__ import annotations

import json
import hashlib
import random
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Mapping, Protocol, Sequence


class AgenticLLMError(RuntimeError):
    """Base class for controlled model-client failures."""


class AgenticLLMHTTPError(AgenticLLMError):
    """Raised for HTTP and transport failures."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        provider_error_type: str | None = None,
        provider_error_code: str | None = None,
        provider_error_message: str | None = None,
        retry_after_seconds: float | None = None,
        response_body_sha256: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.provider_error_type = provider_error_type
        self.provider_error_code = provider_error_code
        self.provider_error_message = provider_error_message
        self.retry_after_seconds = retry_after_seconds
        self.response_body_sha256 = response_body_sha256
        self.request_id = request_id

    @property
    def retryable(self) -> bool:
        if self.status_code is None:
            return True
        return self.status_code in {408, 409, 425, 429, 500, 502, 503, 504}

    def safe_diagnostics(self) -> dict[str, Any]:
        return {
            "status_code": self.status_code,
            "provider_error_type": self.provider_error_type,
            "provider_error_code": self.provider_error_code,
            "provider_error_message": self.provider_error_message,
            "retry_after_seconds": self.retry_after_seconds,
            "response_body_sha256": self.response_body_sha256,
            "request_id": self.request_id,
            "retryable": self.retryable,
        }


class AgenticLLMTimeoutError(AgenticLLMError):
    """Raised when the request exceeds the configured timeout."""


class AgenticLLMResponseError(AgenticLLMError):
    """Raised when a response is not valid JSON or violates the protocol."""


class AgenticLLMOutputBudgetError(AgenticLLMResponseError):
    """Raised when the output budget is spent before any answer is produced.

    推理型模型把思维链计入 completion_tokens，因而也计入 max_tokens。预算在
    思维链上耗尽时，服务端以 finish_reason="length" 返回空正文。此种情形不属
    协议违例，重发同一请求亦不会好转：思维链长度由输入决定，重来一次仍在同处
    耗尽。唯一有效的处置是抬高本次调用的输出预算，故单列一类，与其余协议错误
    分开处置。
    """

    def __init__(
        self,
        message: str,
        *,
        finish_reason: str | None = None,
        max_tokens: int | None = None,
        usage: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.finish_reason = finish_reason
        self.max_tokens = max_tokens
        self.usage = None if usage is None else dict(usage)

    @property
    def reasoning_tokens(self) -> int | None:
        if not isinstance(self.usage, Mapping):
            return None
        details = self.usage.get("completion_tokens_details")
        if not isinstance(details, Mapping):
            return None
        value = details.get("reasoning_tokens")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def safe_diagnostics(self) -> dict[str, Any]:
        completion_tokens = None
        if isinstance(self.usage, Mapping):
            raw = self.usage.get("completion_tokens")
            if isinstance(raw, int) and not isinstance(raw, bool):
                completion_tokens = raw
        return {
            "finish_reason": self.finish_reason,
            "max_tokens": self.max_tokens,
            "completion_tokens": completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
        }


@dataclass(frozen=True)
class TransportResponse:
    """Minimal HTTP response returned by an injectable transport."""

    status_code: int
    body: bytes | str
    headers: Mapping[str, str] | None = None


@dataclass(frozen=True)
class LLMCompletion:
    content: str
    model: str
    usage: dict[str, Any] | None
    elapsed_ms: float
    raw_response_metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "model": self.model,
            "usage": None if self.usage is None else dict(self.usage),
            "elapsed_ms": self.elapsed_ms,
            "raw_response_metadata": dict(self.raw_response_metadata),
        }


Transport = Callable[[str, Mapping[str, str], Mapping[str, Any], float], TransportResponse]


class CompletionClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> LLMCompletion:
        ...


@dataclass(frozen=True)
class TechnicalRetryDiagnostics:
    api_response_retry_count: int
    timeout_retry_count: int
    last_error_type: str | None
    transport_attempt_count: int
    retry_after_honored_count: int
    nonretryable_http_count: int
    http_status_counts: dict[str, int]
    provider_error_type_counts: dict[str, int]
    provider_error_code_counts: dict[str, int]
    last_http_error: dict[str, Any] | None
    output_budget_retry_count: int = 0
    last_output_budget_error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_response_retry_count": self.api_response_retry_count,
            "output_budget_retry_count": self.output_budget_retry_count,
            "last_output_budget_error": (
                None
                if self.last_output_budget_error is None
                else dict(self.last_output_budget_error)
            ),
            "timeout_retry_count": self.timeout_retry_count,
            "last_error_type": self.last_error_type,
            "transport_attempt_count": self.transport_attempt_count,
            "retry_after_honored_count": self.retry_after_honored_count,
            "nonretryable_http_count": self.nonretryable_http_count,
            "http_status_counts": dict(self.http_status_counts),
            "provider_error_type_counts": dict(self.provider_error_type_counts),
            "provider_error_code_counts": dict(self.provider_error_code_counts),
            "last_http_error": (
                None if self.last_http_error is None else dict(self.last_http_error)
            ),
        }


class RequestRateLimiter:
    """Thread-safe minimum interval gate shared by all model clients."""

    def __init__(
        self,
        min_interval_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if (
            isinstance(min_interval_seconds, bool)
            or not isinstance(min_interval_seconds, (int, float))
            or float(min_interval_seconds) < 0
        ):
            raise ValueError("min_interval_seconds must be a non-negative number")
        self.min_interval_seconds = float(min_interval_seconds)
        self.clock = clock
        self.sleeper = sleeper
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        with self._lock:
            now = self.clock()
            delay = max(0.0, self._next_allowed - now)
            if delay:
                self.sleeper(delay)
                now = self.clock()
            self._next_allowed = max(now, self._next_allowed) + self.min_interval_seconds


class ReliableCompletionClient:
    """Apply one bounded transport/protocol retry policy to any model client."""

    def __init__(
        self,
        client: CompletionClient,
        *,
        max_technical_retries: int = 2,
        backoff_seconds: Sequence[float] = (1.0, 2.0),
        jitter_ratio: float = 0.0,
        random_source: Callable[[], float] = random.random,
        rate_limiter: RequestRateLimiter | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        output_budget_growth: float = 2.0,
        # 起始预算已按实测抬至 65536，上限须高于它才留得下一次抬额的余地；
        # 实测服务端接受 131072。
        output_budget_cap: int = 131072,
    ) -> None:
        if client is None or not callable(getattr(client, "complete", None)):
            raise TypeError("client must provide complete(system_prompt, user_prompt)")
        if (
            isinstance(max_technical_retries, bool)
            or not isinstance(max_technical_retries, int)
            or max_technical_retries < 0
        ):
            raise ValueError("max_technical_retries must be a non-negative integer")
        delays = tuple(float(item) for item in backoff_seconds)
        if any(item < 0 for item in delays):
            raise ValueError("backoff_seconds must contain non-negative numbers")
        if max_technical_retries and len(delays) < max_technical_retries:
            raise ValueError("backoff_seconds must cover every technical retry")
        if not callable(sleeper):
            raise TypeError("sleeper must be callable")
        if (
            isinstance(jitter_ratio, bool)
            or not isinstance(jitter_ratio, (int, float))
            or not 0 <= float(jitter_ratio) <= 1
        ):
            raise ValueError("jitter_ratio must be between 0 and 1")
        if not callable(random_source):
            raise TypeError("random_source must be callable")
        if rate_limiter is not None and not isinstance(rate_limiter, RequestRateLimiter):
            raise TypeError("rate_limiter must be RequestRateLimiter or null")
        if (
            isinstance(output_budget_growth, bool)
            or not isinstance(output_budget_growth, (int, float))
            or float(output_budget_growth) < 1.0
        ):
            raise ValueError("output_budget_growth must be a number not below 1.0")
        if (
            isinstance(output_budget_cap, bool)
            or not isinstance(output_budget_cap, int)
            or output_budget_cap <= 0
        ):
            raise ValueError("output_budget_cap must be a positive integer")
        self.client = client
        self.output_budget_growth = float(output_budget_growth)
        self.output_budget_cap = output_budget_cap
        self.max_technical_retries = max_technical_retries
        self.backoff_seconds = delays
        self.sleeper = sleeper
        self.jitter_ratio = float(jitter_ratio)
        self.random_source = random_source
        self.rate_limiter = rate_limiter
        self._api_response_retry_count = 0
        self._output_budget_retry_count = 0
        self._timeout_retry_count = 0
        self._last_error_type: str | None = None
        self._transport_attempt_count = 0
        self._retry_after_honored_count = 0
        self._nonretryable_http_count = 0
        self._http_status_counts: dict[str, int] = {}
        self._provider_error_type_counts: dict[str, int] = {}
        self._provider_error_code_counts: dict[str, int] = {}
        self._last_http_error: dict[str, Any] | None = None
        self._last_output_budget_error: dict[str, Any] | None = None

    @staticmethod
    def _increment(counts: dict[str, int], value: Any) -> None:
        if value is None:
            return
        key = str(value)
        counts[key] = counts.get(key, 0) + 1

    def _record_error(self, error: Exception) -> None:
        self._last_error_type = type(error).__name__
        if isinstance(error, AgenticLLMHTTPError):
            details = error.safe_diagnostics()
            self._last_http_error = details
            self._increment(self._http_status_counts, error.status_code)
            self._increment(
                self._provider_error_type_counts, error.provider_error_type
            )
            self._increment(
                self._provider_error_code_counts, error.provider_error_code
            )
        elif isinstance(error, AgenticLLMOutputBudgetError):
            self._last_output_budget_error = error.safe_diagnostics()

    def _delay_for(self, attempt: int, error: Exception) -> float:
        delay = self.backoff_seconds[attempt]
        retry_after = (
            error.retry_after_seconds
            if isinstance(error, AgenticLLMHTTPError)
            else None
        )
        if retry_after is not None:
            delay = max(delay, retry_after)
            self._retry_after_honored_count += 1
        if self.jitter_ratio:
            delay += delay * self.jitter_ratio * self.random_source()
        return delay

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        response_format: Mapping[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> LLMCompletion:
        budget = max_tokens
        for attempt in range(self.max_technical_retries + 1):
            if self.rate_limiter is not None:
                self.rate_limiter.wait()
            self._transport_attempt_count += 1
            try:
                options: dict[str, Any] = {}
                if response_format is not None:
                    options["response_format"] = dict(response_format)
                if budget is not None:
                    options["max_tokens"] = budget
                return self.client.complete(
                    system_prompt,
                    user_prompt,
                    **options,
                )
            except (
                AgenticLLMHTTPError,
                AgenticLLMTimeoutError,
                AgenticLLMResponseError,
            ) as error:
                self._record_error(error)
                if isinstance(error, AgenticLLMHTTPError) and not error.retryable:
                    self._nonretryable_http_count += 1
                    raise
                if isinstance(error, AgenticLLMOutputBudgetError):
                    # 预算耗尽只有抬高预算这一条出路，已至上限时即刻退出：
                    # 同参重发注定同样耗尽，徒然令一次失败的等待成倍延长。
                    raised = self._raised_budget(budget)
                    if raised is None:
                        raise
                    budget = raised
                    self._output_budget_retry_count += 1
                    if attempt >= self.max_technical_retries:
                        raise
                    self.sleeper(self._delay_for(attempt, error))
                    continue
                if attempt >= self.max_technical_retries:
                    raise
                if isinstance(error, AgenticLLMTimeoutError):
                    self._timeout_retry_count += 1
                else:
                    self._api_response_retry_count += 1
                self.sleeper(self._delay_for(attempt, error))
        raise AssertionError("unreachable retry state")

    def _raised_budget(self, budget: int | None) -> int | None:
        """下一次尝试的输出预算；预算已至上限时返回 None。"""
        if budget is None or budget >= self.output_budget_cap:
            return None
        raised = int(budget * self.output_budget_growth)
        return min(max(raised, budget + 1), self.output_budget_cap)

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = 8192,
    ) -> LLMCompletion:
        return self.complete(
            system_prompt,
            user_prompt,
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
        )

    def retry_diagnostics(self) -> TechnicalRetryDiagnostics:
        return TechnicalRetryDiagnostics(
            api_response_retry_count=self._api_response_retry_count,
            output_budget_retry_count=self._output_budget_retry_count,
            last_output_budget_error=(
                None
                if self._last_output_budget_error is None
                else dict(self._last_output_budget_error)
            ),
            timeout_retry_count=self._timeout_retry_count,
            last_error_type=self._last_error_type,
            transport_attempt_count=self._transport_attempt_count,
            retry_after_honored_count=self._retry_after_honored_count,
            nonretryable_http_count=self._nonretryable_http_count,
            http_status_counts=dict(self._http_status_counts),
            provider_error_type_counts=dict(self._provider_error_type_counts),
            provider_error_code_counts=dict(self._provider_error_code_counts),
            last_http_error=(
                None if self._last_http_error is None else dict(self._last_http_error)
            ),
        )


def _urllib_transport(
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    timeout: float,
) -> TransportResponse:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers=dict(headers),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return TransportResponse(
            status_code=int(response.status),
            body=response.read(),
            headers=dict(response.headers.items()),
        )


class AgenticLLMClient:
    """Small, injectable client for a chat-completions compatible endpoint."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 60.0,
        transport: Transport | None = None,
    ) -> None:
        self.api_key = self._non_empty("api_key", api_key)
        self.base_url = self._non_empty("base_url", base_url)
        self.model = self._non_empty("model", model)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout must be a positive number")
        self.timeout = float(timeout)
        if self.timeout <= 0:
            raise ValueError("timeout must be a positive number")
        self._transport = transport or _urllib_transport

    @staticmethod
    def _non_empty(name: str, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _safe_provider_text(value: Any, limit: int = 240) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        text = " ".join(value.split())[:limit]
        text = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer [REDACTED]", text)
        text = re.sub(r"(?i)\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED_KEY]", text)
        return text

    @staticmethod
    def _header_value(
        headers: Mapping[str, str] | None,
        target: str,
    ) -> str | None:
        if headers is None:
            return None
        for key, value in headers.items():
            if key.casefold() == target.casefold():
                return str(value)
        return None

    @classmethod
    def _retry_after(
        cls,
        headers: Mapping[str, str] | None,
    ) -> float | None:
        raw = cls._header_value(headers, "Retry-After")
        if raw is None:
            return None
        try:
            seconds = float(raw.strip())
            return max(0.0, min(seconds, 300.0))
        except ValueError:
            try:
                moment = parsedate_to_datetime(raw)
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=timezone.utc)
                seconds = (moment - datetime.now(timezone.utc)).total_seconds()
                return max(0.0, min(seconds, 300.0))
            except (TypeError, ValueError, OverflowError):
                return None

    @classmethod
    def _http_error(
        cls,
        status_code: int,
        body: bytes | str | None,
        headers: Mapping[str, str] | None,
    ) -> AgenticLLMHTTPError:
        raw = b""
        if isinstance(body, bytes):
            raw = body[:65536]
            text = raw.decode("utf-8", errors="replace")
        elif isinstance(body, str):
            text = body[:65536]
            raw = text.encode("utf-8")
        else:
            text = ""
        error_type = None
        error_code = None
        error_message = None
        try:
            payload = json.loads(text) if text else {}
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, Mapping):
            detail = payload.get("error", payload)
            if isinstance(detail, Mapping):
                error_type = cls._safe_provider_text(detail.get("type"), 80)
                error_code = cls._safe_provider_text(detail.get("code"), 80)
                error_message = cls._safe_provider_text(detail.get("message"))
        return AgenticLLMHTTPError(
            f"model request returned HTTP status {status_code}",
            status_code=status_code,
            provider_error_type=error_type,
            provider_error_code=error_code,
            provider_error_message=error_message,
            retry_after_seconds=cls._retry_after(headers),
            response_body_sha256=(hashlib.sha256(raw).hexdigest() if raw else None),
            request_id=cls._request_id(headers),
        )

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        response_format: Mapping[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> LLMCompletion:
        system_prompt = self._non_empty("system_prompt", system_prompt)
        user_prompt = self._non_empty("user_prompt", user_prompt)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
        }
        if response_format is not None:
            payload["response_format"] = dict(response_format)
        if max_tokens is not None:
            if (
                isinstance(max_tokens, bool)
                or not isinstance(max_tokens, int)
                or max_tokens <= 0
            ):
                raise ValueError("max_tokens must be a positive integer")
            payload["max_tokens"] = max_tokens

        started = time.perf_counter()
        try:
            response = self._transport(
                self.base_url,
                headers,
                payload,
                self.timeout,
            )
        except (TimeoutError, socket.timeout) as error:
            raise AgenticLLMTimeoutError("model request timed out") from None
        except urllib.error.HTTPError as error:
            headers = (
                None if error.headers is None else dict(error.headers.items())
            )
            try:
                error_body = error.read(65536)
            except Exception:
                error_body = None
            raise self._http_error(error.code, error_body, headers) from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise AgenticLLMTimeoutError("model request timed out") from None
            raise AgenticLLMHTTPError(
                "model transport request failed",
                provider_error_type="transport",
                provider_error_code=type(error.reason).__name__,
            ) from None
        except Exception as error:
            # Do not copy arbitrary transport messages: they could contain a key.
            raise AgenticLLMHTTPError(
                f"model transport failed with {type(error).__name__}",
                provider_error_type="transport",
                provider_error_code=type(error).__name__,
            ) from None
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        if not isinstance(response, TransportResponse):
            raise AgenticLLMResponseError(
                "transport must return a TransportResponse"
            )
        if isinstance(response.status_code, bool) or not isinstance(
            response.status_code, int
        ):
            raise AgenticLLMResponseError("response status_code must be an integer")
        if not 200 <= response.status_code < 300:
            raise self._http_error(
                response.status_code,
                response.body,
                response.headers,
            )

        data = self._decode_body(response.body)
        content, finish_reason = self._extract_content(data, max_tokens)
        response_model = data.get("model", self.model)
        if not isinstance(response_model, str) or not response_model.strip():
            raise AgenticLLMResponseError("response model must be a non-empty string")
        usage = data.get("usage")
        if usage is not None and not isinstance(usage, Mapping):
            raise AgenticLLMResponseError("response usage must be an object or null")

        metadata = {
            "status_code": response.status_code,
            "response_id": data.get("id"),
            "created": data.get("created"),
            "finish_reason": finish_reason,
            "system_fingerprint": data.get("system_fingerprint"),
            "request_id": self._request_id(response.headers),
        }
        return LLMCompletion(
            content=content,
            model=response_model.strip(),
            usage=None if usage is None else dict(usage),
            elapsed_ms=elapsed_ms,
            raw_response_metadata=metadata,
        )

    @staticmethod
    def _decode_body(body: bytes | str) -> Mapping[str, Any]:
        if isinstance(body, bytes):
            try:
                text = body.decode("utf-8")
            except UnicodeDecodeError as error:
                raise AgenticLLMResponseError(
                    "response body is not valid UTF-8"
                ) from error
        elif isinstance(body, str):
            text = body
        else:
            raise AgenticLLMResponseError("response body must be bytes or text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise AgenticLLMResponseError(
                "response body is not valid JSON"
            ) from error
        if not isinstance(data, Mapping):
            raise AgenticLLMResponseError("response JSON must be an object")
        return data

    @staticmethod
    def _extract_content(
        data: Mapping[str, Any],
        max_tokens: int | None = None,
    ) -> tuple[str, Any]:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AgenticLLMResponseError(
                "response choices must be a non-empty list"
            )
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise AgenticLLMResponseError("response choice must be an object")
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise AgenticLLMResponseError("response message must be an object")
        if "content" not in message:
            raise AgenticLLMResponseError("response message is missing content")
        content = message["content"]
        finish_reason = choice.get("finish_reason")
        if not isinstance(content, str) or not content.strip():
            if finish_reason == "length":
                usage = data.get("usage")
                raise AgenticLLMOutputBudgetError(
                    "output budget exhausted before the model answered",
                    finish_reason="length",
                    max_tokens=max_tokens,
                    usage=usage if isinstance(usage, Mapping) else None,
                )
            raise AgenticLLMResponseError("response content must be non-empty text")
        return content, finish_reason

    @staticmethod
    def _request_id(headers: Mapping[str, str] | None) -> str | None:
        if headers is None:
            return None
        for key, value in headers.items():
            if key.lower() in {"x-request-id", "request-id"}:
                return value
        return None
