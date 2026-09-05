from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any

from ..bootstrap import bootstrap_candidate_core
from ..config import OUTPUT_DIR
from ..runtime_observability import RuntimeTrace

bootstrap_candidate_core()
from extractor.agentic_llm_client import AgenticLLMClient, ReliableCompletionClient  # noqa: E402
from extractor.proficiency_evaluator import ProficiencyEvaluator  # noqa: E402
from extractor.team_skill_proficiency_bridge_v434 import build_proficiency_evaluator_inputs  # noqa: E402
from extractor.team_skill_schema_v3 import CandidateSkillProfile  # noqa: E402
from extractor.v3_client_adapter import JsonModeCompletionAdapter  # noqa: E402


def _env_positive_number(name: str, default: float) -> float:
    """读取正数型环境变量；缺失、非数或非正一律退回默认值。"""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _parallelism() -> int:
    """定级的并发请求数。

    每一项能力单独送模型判定，各项之间没有先后关系，串行发出时整段耗时等于
    逐项相加。推理型模型单项动辄数十秒，项数又随简历厚薄浮动，串行下这一步
    没有可预期的上界。置 1 即回到逐项串行。
    """
    return max(1, int(_env_positive_number("LLM_PROFICIENCY_PARALLELISM", 6)))


def _timing_path() -> str | None:
    """定级阶段的计时落点。

    抽取一侧由父进程指定计时文件，定级此前只认 BACKEND_PROFICIENCY_TIMING_FILE，
    该变量不设时整段不留任何记录：一次比对久候不归，无从判断是停在哪一项、
    还是根本没发出请求。故在未指定时给出默认落点，与抽取的计时同置一处。
    """
    configured = os.getenv("BACKEND_PROFICIENCY_TIMING_FILE", "").strip()
    if configured:
        return configured
    if os.getenv("BACKEND_PROFICIENCY_TIMING_DISABLED", "").strip():
        return None
    return str(OUTPUT_DIR / f"proficiency_{uuid.uuid4().hex}.timing.jsonl")


def _resolve_api_url() -> str:
    direct = os.getenv("LLM_API_URL", "").strip().rstrip("/")
    if direct:
        return direct
    base = os.getenv("LLM_API_BASE", "").strip().rstrip("/")
    if not base:
        raise RuntimeError("missing LLM_API_URL or LLM_API_BASE")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/chat/completions"


def llm_is_configured() -> bool:
    """Return deployment readiness without making an external request."""
    if not os.getenv("LLM_API_KEY", "").strip():
        return False
    if not os.getenv("LLM_MODEL", "").strip():
        return False
    try:
        resolved = _resolve_api_url()
        parsed = urlsplit(resolved)
    except (RuntimeError, ValueError):
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _build_evaluator(trace: RuntimeTrace | None = None) -> ProficiencyEvaluator:
    trace = trace or RuntimeTrace(None)
    api_key = os.getenv("LLM_API_KEY", "").strip()
    model = os.getenv("LLM_MODEL", "").strip()
    if not api_key or not model:
        raise RuntimeError("auto_proficiency requires LLM_API_KEY and LLM_MODEL")
    timeout = _env_positive_number("LLM_TIMEOUT", 90.0)
    # 与抽取链同一口径：推理型模型的思维链计入输出预算，写死的 8192 会在
    # 思维链上耗尽，服务端以空正文加 finish_reason="length" 返回。
    budget = int(_env_positive_number("LLM_MAX_OUTPUT_TOKENS", 32768))
    base = AgenticLLMClient(
        api_key=api_key,
        base_url=_resolve_api_url(),
        model=model,
        timeout=timeout,
    )
    base.complete = trace.wrap('proficiency_llm_transport', base.complete, llm=True)
    reliable = ReliableCompletionClient(base, max_technical_retries=2, backoff_seconds=(1.0, 2.0))
    reliable.complete = trace.wrap('proficiency_llm_logical', reliable.complete, llm=True)
    return ProficiencyEvaluator(
        JsonModeCompletionAdapter(reliable, max_tokens=budget, min_output_tokens=budget)
    )


def infer_proficiency_levels(
    profile: CandidateSkillProfile,
    *,
    target_team_skill_ids: list[str],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    # Job requirements may be absent from this candidate. The frozen bridge
    # accepts candidate IDs only; missing Job skills remain GapEngine MISSING.
    present_ids = {item.team_skill_id for item in profile.assessments}
    requested_ids = list(dict.fromkeys(
        sid for sid in target_team_skill_ids if sid in present_ids
    ))
    inputs = build_proficiency_evaluator_inputs(profile, requested_ids)
    if not inputs:
        return {}, []

    # 共用一个 trace：各次调用的时间偏移因而落在同一基准上，能据以看出整段
    # 停在哪一项。此前每项各建一个，偏移一律从零起算，记录之间无从比对。
    trace = RuntimeTrace(_timing_path())
    # 先建一个，模型未配置时照旧在此抛出，不至于沦为逐项失败。
    primary = _build_evaluator(trace)
    workers = min(_parallelism(), len(inputs))

    def evaluate_one(indexed):
        index, item = indexed
        # 每个并发任务各持一个 evaluator：ReliableCompletionClient 的重试计数
        # 并非为并发共享而设。trace 自带锁，共享无碍。
        evaluator = primary if workers == 1 or index == 0 else _build_evaluator(trace)
        try:
            return item, trace.wrap('proficiency', evaluator.evaluate)(*item.evaluator_args()), None
        except (RuntimeError, ValueError) as exc:
            return item, None, exc

    indexed_inputs = list(enumerate(inputs))
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(evaluate_one, indexed_inputs))
    else:
        outcomes = [evaluate_one(pair) for pair in indexed_inputs]

    levels: dict[str, str] = {}
    details: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for item, result, exc in outcomes:
        if exc is not None:
            # 定级逐项调模型，偶有一项拿不到可用回复（空正文、超时、格式不合）。
            # 此前一项失败即整批失败，于是一次比对里其余各项已经算出来的档位
            # 也一并作废。改为记下这一项、继续往下走：拿不到档位的项在下游按
            # 「已具备但未定级」处理，与模型未配置时的退路一致，不会被当成缺失。
            failures.append({
                "team_skill_id": item.team_skill_id,
                "error": str(exc),
            })
            continue
        levels[item.team_skill_id] = result.final_level
        details.append(result.to_dict())
    if failures and not levels:
        # 一项都没成，说明不是偶发而是模型侧不可用，照旧交由调用方走不定级的退路
        raise RuntimeError(
            "proficiency evaluation failed for every requested skill: "
            + "; ".join(f"{f['team_skill_id']}: {f['error']}" for f in failures)
        )
    for failure in failures:
        details.append({
            "team_skill_id": failure["team_skill_id"],
            "final_level": None,
            "status": "EVALUATION_FAILED",
            "error": failure["error"],
        })
    return levels, details
