"""Resilient segment-aware evidence extraction for long internal resumes.

The long-resume path is deliberately stricter than a best-effort batch job:
- every accepted candidate must satisfy the exact extraction contract;
- transport/protocol failures are handled by the shared reliable client first;
- if a whole segment batch still fails, the batch is deterministically split and retried;
- successful strict batch results are checkpointed locally so a later CLI rerun resumes
  from completed work instead of paying for the same batches again;
- malformed candidates are never silently dropped from a "successful" run.

The cache contains only normalized model outputs and hashes. It never stores API keys.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from extractor.agentic_llm_client import AgenticLLMError, LLMCompletion
from extractor.agentic_schema import CandidateAbility, CandidateStatus
from extractor.evidence_extraction_agent import (
    CandidateContractError,
    CandidateIdCollisionError,
    ExtractionAgentError,
    ExtractionParseError,
    ExtractionResult,
)
from extractor.evidence_grounding_v4 import GroundingStats, locate_evidence_conservatively
from extractor.resume_segmentation_v4 import ResumeSegmentV4


class CompletionClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> LLMCompletion:
        ...


_FIELDS = {"segment_id", "fact", "behavior", "ability", "evidence", "reason", "confidence"}
DEFAULT_MAX_BATCH_CHARS = 1200
SEGMENTED_MAX_TOKENS = 8192
SEGMENT_CACHE_VERSION = "segmented_evidence_v4_r4_2_3_v1"
MAX_ADAPTIVE_SPLIT_DEPTH = 8


def _segment_batches(segments: Sequence[ResumeSegmentV4], max_chars: int) -> list[list[ResumeSegmentV4]]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    batches: list[list[ResumeSegmentV4]] = []
    current: list[ResumeSegmentV4] = []
    current_chars = 0
    for segment in segments:
        size = len(segment.text)
        if current and current_chars + size > max_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(segment)
        current_chars += size
    if current:
        batches.append(current)
    return batches


def _balanced_split(batch: Sequence[ResumeSegmentV4]) -> tuple[list[ResumeSegmentV4], list[ResumeSegmentV4]]:
    """Split a failed multi-segment batch near half its character mass."""
    if len(batch) < 2:
        raise ValueError("adaptive split requires at least two segments")
    total = sum(len(item.text) for item in batch)
    target = total / 2.0
    running = 0
    best_index = 1
    best_distance = float("inf")
    for index in range(1, len(batch)):
        running += len(batch[index - 1].text)
        distance = abs(running - target)
        if distance < best_distance:
            best_index = index
            best_distance = distance
    return list(batch[:best_index]), list(batch[best_index:])


def segment_batch_count_v4(
    segments: Sequence[ResumeSegmentV4],
    max_chars: int = DEFAULT_MAX_BATCH_CHARS,
) -> int:
    return len(_segment_batches(segments, max_chars))


def _merge_usage(target: dict[str, int], usage: Mapping[str, Any] | None) -> None:
    if not usage:
        return
    for key in (
        "prompt_tokens", "completion_tokens", "total_tokens",
        "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
    ):
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        target[key] = target.get(key, 0) + int(value)


class SegmentedEvidenceExtractionAgentV4:
    def __init__(
        self,
        client: CompletionClient,
        prompt_path: str | Path | None = None,
        *,
        cache_dir: str | Path | None = None,
        cache_namespace: str = "default",
    ) -> None:
        self.client = client
        root = Path(__file__).resolve().parent.parent
        self.prompt_path = Path(prompt_path or root / "config" / "segmented_evidence_extractor_v4.txt")
        self.system_prompt = self.prompt_path.read_text(encoding="utf-8-sig").strip()
        if not self.system_prompt:
            raise ValueError("segmented extractor prompt must not be empty")
        if not isinstance(cache_namespace, str) or not cache_namespace.strip():
            raise ValueError("cache_namespace must be non-empty text")
        self.cache_namespace = cache_namespace.strip()
        self.cache_dir = None if cache_dir is None else Path(cache_dir)
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._prompt_sha256 = hashlib.sha256(self.system_prompt.encode("utf-8")).hexdigest()

    def _complete_json(self, system_prompt: str, user_prompt: str) -> LLMCompletion:
        complete_json = getattr(self.client, "complete_json", None)
        if callable(complete_json):
            return complete_json(system_prompt, user_prompt, max_tokens=SEGMENTED_MAX_TOKENS)
        return self.client.complete(system_prompt, user_prompt)

    @staticmethod
    def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON field: {key}")
            result[key] = value
        return result

    @staticmethod
    def _reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON number is not allowed: {value}")

    @classmethod
    def _parse(cls, content: str) -> list[Mapping[str, Any]]:
        try:
            payload = json.loads(
                content.strip(),
                object_pairs_hook=cls._reject_duplicate_keys,
                parse_constant=cls._reject_constant,
            )
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise ExtractionParseError("segmented model output must be one strict JSON object") from exc
        if not isinstance(payload, Mapping) or set(payload) != {"candidates"}:
            raise ExtractionParseError("segmented root must contain only candidates")
        candidates = payload["candidates"]
        if not isinstance(candidates, list):
            raise ExtractionParseError("segmented candidates must be an array")
        return candidates

    @staticmethod
    def _non_empty(name: str, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise CandidateContractError(f"{name} must be non-empty text")
        return value.strip()

    @classmethod
    def _validate_candidate(
        cls, raw: Any, index: int, segment_ids: set[str]
    ) -> dict[str, Any]:
        prefix = f"candidates[{index}]"
        if not isinstance(raw, Mapping):
            raise CandidateContractError(f"{prefix} must be an object")
        fields = set(raw)
        if fields != _FIELDS:
            missing = sorted(_FIELDS - fields)
            unknown = sorted(fields - _FIELDS)
            details: list[str] = []
            if missing:
                details.append("missing=" + ",".join(missing))
            if unknown:
                details.append("unknown=" + ",".join(unknown))
            raise CandidateContractError(
                f"{prefix} fields are invalid ({'; '.join(details)})"
            )
        segment_id = cls._non_empty(f"{prefix}.segment_id", raw["segment_id"])
        if segment_id not in segment_ids:
            raise CandidateContractError(f"{prefix}.segment_id is not in input segments")
        result = {"segment_id": segment_id}
        for field in ("fact", "behavior", "ability", "reason"):
            result[field] = cls._non_empty(f"{prefix}.{field}", raw[field])
        evidence = raw["evidence"]
        if not isinstance(evidence, list) or not evidence:
            raise CandidateContractError(f"{prefix}.evidence must be a non-empty array")
        result["evidence"] = [
            cls._non_empty(f"{prefix}.evidence[{i}]", value)
            for i, value in enumerate(evidence)
        ]
        confidence = raw["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise CandidateContractError(f"{prefix}.confidence must be numeric")
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise CandidateContractError(f"{prefix}.confidence must be finite in [0,1]")
        result["confidence"] = confidence
        return result

    @staticmethod
    def _candidate_id(resume_id: str, normalized: Mapping[str, Any], evidence_texts: list[str]) -> tuple[str, str]:
        canonical = {
            "resume_id": resume_id,
            "segment_id": normalized["segment_id"],
            "ability": normalized["ability"],
            "evidence": sorted(set(evidence_texts)),
        }
        fingerprint = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest(), fingerprint

    def _cache_key(self, encoded_payload: str) -> str:
        canonical = json.dumps(
            {
                "version": SEGMENT_CACHE_VERSION,
                "namespace": self.cache_namespace,
                "prompt_sha256": self._prompt_sha256,
                "max_tokens": SEGMENTED_MAX_TOKENS,
                "payload": encoded_payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _cache_path(self, key: str) -> Path | None:
        return None if self.cache_dir is None else self.cache_dir / f"{key}.json"

    def _load_cache(
        self,
        key: str,
        batch_ids: set[str],
    ) -> tuple[str, str, list[dict[str, Any]]] | None:
        path = self._cache_path(key)
        if path is None or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                return None
            if payload.get("version") != SEGMENT_CACHE_VERSION:
                return None
            kind = payload.get("kind")
            if kind == "split":
                return "split", "", []
            if kind != "result":
                return None
            model = payload.get("model")
            raw = payload.get("candidates")
            if not isinstance(model, str) or not model.strip() or not isinstance(raw, list):
                return None
            normalized = [
                self._validate_candidate(item, index, batch_ids)
                for index, item in enumerate(raw)
            ]
            return "result", model.strip(), normalized
        except (OSError, json.JSONDecodeError, CandidateContractError, TypeError, ValueError):
            return None

    def _write_cache_result(
        self,
        key: str,
        model: str,
        normalized_candidates: Sequence[Mapping[str, Any]],
    ) -> None:
        path = self._cache_path(key)
        if path is None:
            return
        payload = {
            "version": SEGMENT_CACHE_VERSION,
            "kind": "result",
            "model": model,
            "candidates": [dict(item) for item in normalized_candidates],
        }
        self._atomic_write_json(path, payload)

    def _write_cache_split(self, key: str) -> None:
        path = self._cache_path(key)
        if path is None:
            return
        self._atomic_write_json(
            path,
            {"version": SEGMENT_CACHE_VERSION, "kind": "split"},
        )

    @staticmethod
    def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
            encoding="utf-8",
        )
        os.replace(tmp, path)

    def extract(
        self,
        *,
        resume_id: str,
        segments: Sequence[ResumeSegmentV4],
        max_batch_chars: int = DEFAULT_MAX_BATCH_CHARS,
    ) -> ExtractionResult:
        if not resume_id.strip():
            raise ValueError("resume_id must be non-empty")
        if not segments:
            raise ValueError("segments must be non-empty")
        by_id = {segment.segment_id: segment for segment in segments}
        if len(by_id) != len(segments):
            raise ValueError("segment IDs must be unique")

        candidates: list[CandidateAbility] = []
        fingerprints: dict[str, str] = {}
        warnings: list[str] = []
        stats = GroundingStats()
        raw_candidate_count = 0
        contract_retry_count = 0
        adaptive_split_count = 0
        technical_split_count = 0
        contract_split_count = 0
        cache_hit_count = 0
        elapsed_ms = 0.0
        aggregate_usage: dict[str, int] = {}
        model = ""
        batches = _segment_batches(segments, max_batch_chars)

        def record_completion(completion: LLMCompletion) -> None:
            nonlocal elapsed_ms, model
            if not isinstance(completion, LLMCompletion):
                raise ExtractionAgentError("client must return LLMCompletion")
            model = model or completion.model
            elapsed_ms += completion.elapsed_ms
            _merge_usage(aggregate_usage, completion.usage)

        def parse_and_validate(
            content: str,
            batch_ids: set[str],
        ) -> tuple[list[Mapping[str, Any]], list[dict[str, Any]]]:
            parsed = self._parse(content)
            normalized_items = [
                self._validate_candidate(raw, index, batch_ids)
                for index, raw in enumerate(parsed)
            ]
            return parsed, normalized_items

        def process_batch(
            batch: Sequence[ResumeSegmentV4],
            *,
            label: str,
            depth: int,
        ) -> list[dict[str, Any]]:
            nonlocal contract_retry_count, adaptive_split_count
            nonlocal technical_split_count, contract_split_count, cache_hit_count
            if depth > MAX_ADAPTIVE_SPLIT_DEPTH:
                raise ExtractionAgentError(
                    f"segmented adaptive split depth exceeded at {label}"
                )
            payload = {
                "resume_id": resume_id,
                "batch_label": label,
                "segments": [segment.to_prompt_dict() for segment in batch],
            }
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            batch_ids = {segment.segment_id for segment in batch}
            cache_key = self._cache_key(encoded)
            cached = self._load_cache(cache_key, batch_ids)
            if cached is not None:
                kind, cached_model, normalized = cached
                cache_hit_count += 1
                if kind == "result":
                    nonlocal_model = cached_model
                    # Populate model for all-cache-hit runs without pretending cached usage is new usage.
                    nonlocal model
                    model = model or nonlocal_model
                    return normalized
                left, right = _balanced_split(batch)
                return (
                    process_batch(left, label=label + "a", depth=depth + 1)
                    + process_batch(right, label=label + "b", depth=depth + 1)
                )

            def split_after_failure(reason: str) -> list[dict[str, Any]]:
                nonlocal adaptive_split_count, technical_split_count, contract_split_count
                if len(batch) <= 1:
                    segment_id = batch[0].segment_id if batch else "<empty>"
                    cache_hint = str(self.cache_dir) if self.cache_dir is not None else "disabled"
                    raise ExtractionAgentError(
                        f"segmented extraction exhausted recovery at {label} "
                        f"(segment={segment_id}, reason={reason}, cache={cache_hint})"
                    )
                adaptive_split_count += 1
                if reason.startswith("technical"):
                    technical_split_count += 1
                else:
                    contract_split_count += 1
                self._write_cache_split(cache_key)
                left, right = _balanced_split(batch)
                return (
                    process_batch(left, label=label + "a", depth=depth + 1)
                    + process_batch(right, label=label + "b", depth=depth + 1)
                )

            try:
                completion = self._complete_json(self.system_prompt, encoded)
            except AgenticLLMError as exc:
                return split_after_failure("technical:" + type(exc).__name__)
            record_completion(completion)

            try:
                raw_candidates, normalized_candidates = parse_and_validate(
                    completion.content, batch_ids
                )
            except (ExtractionParseError, CandidateContractError) as exc:
                contract_retry_count += 1
                compact_error = str(exc).replace("\n", " ")[:500]
                retry_instruction = (
                    "上一次输出违反严格 JSON/候选字段合同，错误为：" + compact_error + "。"
                    "请重新输出当前批次的完整结果。只输出一个 JSON 对象；"
                    "根字段只能是 candidates；每个候选必须且只能包含 segment_id、fact、behavior、"
                    "ability、evidence、reason、confidence；segment_id 必须来自当前输入批次；"
                    "fact、behavior、ability、reason 必须为非空字符串；"
                    "evidence 必须为非空字符串数组；confidence 必须为0到1之间数字。"
                )
                try:
                    completion = self._complete_json(
                        self.system_prompt + "\n\n" + retry_instruction,
                        encoded,
                    )
                except AgenticLLMError as retry_exc:
                    return split_after_failure(
                        "technical_contract_retry:" + type(retry_exc).__name__
                    )
                record_completion(completion)
                try:
                    raw_candidates, normalized_candidates = parse_and_validate(
                        completion.content, batch_ids
                    )
                except (ExtractionParseError, CandidateContractError) as retry_exc:
                    return split_after_failure(
                        "contract:" + type(retry_exc).__name__ + ":" + str(retry_exc)[:180]
                    )

            # Cache only fully strict results. A completed run therefore never hides invalid candidates.
            self._write_cache_result(cache_key, completion.model, normalized_candidates)
            return normalized_candidates

        for batch_index, batch in enumerate(batches, start=1):
            normalized_candidates = process_batch(
                batch,
                label=f"batch{batch_index}",
                depth=0,
            )
            raw_candidate_count += len(normalized_candidates)

            for normalized in normalized_candidates:
                segment = by_id[normalized["segment_id"]]
                evidence, local_stats = locate_evidence_conservatively(
                    normalized["evidence"],
                    segment.text,
                    segment.segment_id,
                    global_offset=segment.start,
                )
                stats = stats + local_stats
                grounded_texts = [item.text for item in evidence if item.start is not None]
                candidate_id, fingerprint = self._candidate_id(
                    resume_id, normalized, grounded_texts or normalized["evidence"]
                )
                existing = fingerprints.get(candidate_id)
                if existing is not None:
                    if existing != fingerprint:
                        raise CandidateIdCollisionError(
                            f"SHA-256 candidate_id collision: {candidate_id}"
                        )
                    warnings.append(
                        f"duplicate candidate removed: candidate_id={candidate_id}"
                    )
                    continue
                fingerprints[candidate_id] = fingerprint
                candidates.append(
                    CandidateAbility(
                        candidate_id=candidate_id,
                        resume_id=resume_id,
                        project_id=segment.segment_id,
                        fact=normalized["fact"],
                        behavior=normalized["behavior"],
                        ability=normalized["ability"],
                        normalized_ability=normalized["ability"],
                        category={
                            "evidence_type": "demonstrated_behavior",
                            "section_type": segment.section_type,
                            "hint_authority": "non_authoritative",
                        },
                        evidence=evidence,
                        reason=normalized["reason"],
                        confidence=normalized["confidence"],
                        source="segmented_extract_agent_v4",
                        revision_round=0,
                        parent_candidate_id=None,
                        status=CandidateStatus.PENDING_REVIEW,
                        lineage=[candidate_id],
                    )
                )

        warnings.append(f"segmented_batch_count={len(batches)}")
        if adaptive_split_count:
            warnings.append(f"segmented_adaptive_split_count={adaptive_split_count}")
        if technical_split_count:
            warnings.append(f"segmented_technical_split_count={technical_split_count}")
        if contract_split_count:
            warnings.append(f"segmented_contract_split_count={contract_split_count}")
        if contract_retry_count:
            warnings.append(f"segmented_contract_retry_count={contract_retry_count}")
        if cache_hit_count:
            warnings.append(f"segmented_cache_hit_count={cache_hit_count}")
        if stats.normalized_count:
            warnings.append(f"normalized_grounding_count={stats.normalized_count}")
        if stats.ambiguous_count:
            warnings.append(f"ambiguous_normalized_grounding_count={stats.ambiguous_count}")
        return ExtractionResult(
            resume_id=resume_id,
            candidates=candidates,
            model=model,
            elapsed_ms=elapsed_ms,
            usage=dict(aggregate_usage),
            raw_candidate_count=raw_candidate_count,
            accepted_candidate_count=len(candidates),
            invalid_candidate_count=0,
            located_evidence_count=stats.exact_count + stats.normalized_count,
            unlocated_evidence_count=stats.unlocated_count,
            warnings=warnings,
        )
