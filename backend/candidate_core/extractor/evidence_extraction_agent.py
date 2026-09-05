"""Evidence-driven candidate generation for Agentic Workflow stage 2A."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from extractor.agentic_llm_client import LLMCompletion
from extractor.agentic_schema import CandidateAbility, CandidateStatus, Evidence


class ExtractionAgentError(RuntimeError):
    """Base class for controlled extraction failures."""


class ExtractionParseError(ExtractionAgentError):
    """Raised when model output is not one strict JSON object."""


class CandidateContractError(ExtractionAgentError):
    """Raised when a generated candidate violates the extraction contract."""


class CandidateIdCollisionError(ExtractionAgentError):
    """Raised for a real SHA-256 collision between different candidates."""


class CompletionClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> LLMCompletion:
        ...


@dataclass(frozen=True)
class ExtractionResult:
    resume_id: str
    candidates: list[CandidateAbility]
    model: str
    elapsed_ms: float
    usage: dict[str, Any] | None
    raw_candidate_count: int
    accepted_candidate_count: int
    invalid_candidate_count: int
    located_evidence_count: int
    unlocated_evidence_count: int
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "resume_id": self.resume_id,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "model": self.model,
            "elapsed_ms": self.elapsed_ms,
            "usage": None if self.usage is None else dict(self.usage),
            "raw_candidate_count": self.raw_candidate_count,
            "accepted_candidate_count": self.accepted_candidate_count,
            "invalid_candidate_count": self.invalid_candidate_count,
            "located_evidence_count": self.located_evidence_count,
            "unlocated_evidence_count": self.unlocated_evidence_count,
            "warnings": list(self.warnings),
        }

    def diagnostics_dict(self) -> dict[str, Any]:
        result = self.to_dict()
        result.pop("candidates")
        return result


_CANDIDATE_FIELDS = {
    "project_id",
    "fact",
    "behavior",
    "ability",
    "evidence",
    "reason",
    "confidence",
}
_FENCED_JSON = re.compile(
    r"\A\s*```(?:json)?[ \t]*\r?\n(?P<body>.*?)\r?\n?```[ \t]*\s*\Z",
    re.IGNORECASE | re.DOTALL,
)


class EvidenceExtractionAgent:
    def __init__(
        self,
        client: CompletionClient,
        prompt_path: str | Path | None = None,
    ) -> None:
        self.client = client
        if prompt_path is None:
            prompt_path = (
                Path(__file__).resolve().parent.parent
                / "config"
                / "agentic_extractor_prompt.txt"
            )
        self.prompt_path = Path(prompt_path)
        self.system_prompt = self.prompt_path.read_text(encoding="utf-8-sig").strip()
        if not self.system_prompt:
            raise ValueError("agentic extractor prompt must not be empty")

    def extract(
        self,
        resume_id: str,
        resume_text: str,
        project_id: str = "resume_full",
    ) -> ExtractionResult:
        resume_id = self._non_empty("resume_id", resume_id)
        if not isinstance(resume_text, str) or not resume_text.strip():
            raise ValueError("resume_text must be non-empty text")
        project_id = self._non_empty("project_id", project_id)

        user_prompt = json.dumps(
            {
                "resume_id": resume_id,
                "project_id": project_id,
                "resume_text": resume_text,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        completion = self.client.complete(self.system_prompt, user_prompt)
        self._validate_completion(completion)
        payload = self._parse_single_json_object(completion.content)
        raw_candidates = self._validate_root(payload)

        candidates: list[CandidateAbility] = []
        fingerprints_by_id: dict[str, str] = {}
        warnings: list[str] = []
        located_count = 0
        unlocated_count = 0
        for index, raw_candidate in enumerate(raw_candidates):
            normalized = self._validate_candidate(raw_candidate, index, project_id)
            candidate_id, fingerprint = self._candidate_id(
                resume_id,
                normalized,
            )
            existing = fingerprints_by_id.get(candidate_id)
            if existing is not None:
                if existing != fingerprint:
                    raise CandidateIdCollisionError(
                        f"SHA-256 candidate_id collision: {candidate_id}"
                    )
                warnings.append(
                    f"duplicate candidate removed: candidate_id={candidate_id}"
                )
                continue

            evidence, located, unlocated = self._locate_evidence(
                normalized["evidence"],
                resume_text,
                project_id,
            )
            candidate = CandidateAbility(
                candidate_id=candidate_id,
                resume_id=resume_id,
                project_id=project_id,
                fact=normalized["fact"],
                behavior=normalized["behavior"],
                ability=normalized["ability"],
                normalized_ability=normalized["ability"],
                category={},
                evidence=evidence,
                reason=normalized["reason"],
                confidence=normalized["confidence"],
                source="extract_agent",
                revision_round=0,
                parent_candidate_id=None,
                status=CandidateStatus.PENDING_REVIEW,
                lineage=[candidate_id],
            )
            fingerprints_by_id[candidate_id] = fingerprint
            candidates.append(candidate)
            located_count += located
            unlocated_count += unlocated

        return ExtractionResult(
            resume_id=resume_id,
            candidates=candidates,
            model=completion.model,
            elapsed_ms=completion.elapsed_ms,
            usage=None if completion.usage is None else dict(completion.usage),
            raw_candidate_count=len(raw_candidates),
            accepted_candidate_count=len(candidates),
            invalid_candidate_count=0,
            located_evidence_count=located_count,
            unlocated_evidence_count=unlocated_count,
            warnings=warnings,
        )

    @staticmethod
    def _validate_completion(completion: Any) -> None:
        if not isinstance(completion, LLMCompletion):
            raise ExtractionAgentError("client must return LLMCompletion")

    @classmethod
    def _parse_single_json_object(cls, content: str) -> Mapping[str, Any]:
        if not isinstance(content, str) or not content.strip():
            raise ExtractionParseError("model content must be non-empty text")
        fenced = _FENCED_JSON.fullmatch(content)
        json_text = fenced.group("body") if fenced else content.strip()
        try:
            payload = json.loads(
                json_text,
                object_pairs_hook=cls._reject_duplicate_keys,
                parse_constant=cls._reject_json_constant,
            )
        except (json.JSONDecodeError, ValueError) as error:
            raise ExtractionParseError(
                "model output must be exactly one valid JSON object"
            ) from error
        if not isinstance(payload, Mapping):
            raise ExtractionParseError("model output JSON must be an object")
        return payload

    @staticmethod
    def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON field: {key}")
            result[key] = value
        return result

    @staticmethod
    def _reject_json_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON number is not allowed: {value}")

    @staticmethod
    def _validate_root(payload: Mapping[str, Any]) -> list[Any]:
        keys = set(payload)
        if keys != {"candidates"}:
            missing = {"candidates"} - keys
            unknown = keys - {"candidates"}
            details = []
            if missing:
                details.append("missing candidates")
            if unknown:
                details.append(f"unknown fields: {', '.join(sorted(unknown))}")
            raise ExtractionParseError("root object invalid: " + "; ".join(details))
        candidates = payload["candidates"]
        if not isinstance(candidates, list):
            raise ExtractionParseError("candidates must be a list")
        return candidates

    @classmethod
    def _validate_candidate(
        cls,
        value: Any,
        index: int,
        expected_project_id: str,
    ) -> dict[str, Any]:
        prefix = f"candidates[{index}]"
        if not isinstance(value, Mapping):
            raise CandidateContractError(f"{prefix} must be an object")
        keys = set(value)
        missing = _CANDIDATE_FIELDS - keys
        unknown = keys - _CANDIDATE_FIELDS
        if missing:
            raise CandidateContractError(
                f"{prefix} missing fields: {', '.join(sorted(missing))}"
            )
        if unknown:
            raise CandidateContractError(
                f"{prefix} contains unknown fields: {', '.join(sorted(unknown))}"
            )

        project_id = cls._non_empty(f"{prefix}.project_id", value["project_id"])
        if project_id != expected_project_id:
            raise CandidateContractError(
                f"{prefix}.project_id must equal requested project_id"
            )
        result: dict[str, Any] = {"project_id": project_id}
        for field in ("fact", "behavior", "ability", "reason"):
            result[field] = cls._non_empty(f"{prefix}.{field}", value[field])

        evidence_value = value["evidence"]
        if not isinstance(evidence_value, list) or not evidence_value:
            raise CandidateContractError(
                f"{prefix}.evidence must be a non-empty string list"
            )
        evidence: list[str] = []
        for evidence_index, item in enumerate(evidence_value):
            evidence.append(
                cls._non_empty(
                    f"{prefix}.evidence[{evidence_index}]",
                    item,
                )
            )
        result["evidence"] = evidence

        confidence = value["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise CandidateContractError(f"{prefix}.confidence must be a number")
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise CandidateContractError(
                f"{prefix}.confidence must be finite and between 0 and 1"
            )
        result["confidence"] = confidence
        return result

    @staticmethod
    def _non_empty(name: str, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise CandidateContractError(f"{name} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _candidate_id(
        resume_id: str,
        candidate: Mapping[str, Any],
    ) -> tuple[str, str]:
        canonical = {
            "resume_id": resume_id,
            "project_id": candidate["project_id"],
            "ability": candidate["ability"],
            "evidence": sorted(set(candidate["evidence"])),
            "fact": candidate["fact"],
            "behavior": candidate["behavior"],
        }
        fingerprint = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest(), fingerprint

    @staticmethod
    def _locate_evidence(
        evidence_texts: list[str],
        resume_text: str,
        project_id: str,
    ) -> tuple[list[Evidence], int, int]:
        evidence_objects: list[Evidence] = []
        seen: set[tuple[str, str, int | None, int | None]] = set()
        for evidence_text in evidence_texts:
            positions: list[int] = []
            offset = 0
            while True:
                position = resume_text.find(evidence_text, offset)
                if position < 0:
                    break
                positions.append(position)
                offset = position + 1

            if positions:
                for start in positions:
                    key = (
                        evidence_text,
                        project_id,
                        start,
                        start + len(evidence_text),
                    )
                    if key not in seen:
                        seen.add(key)
                        evidence_objects.append(
                            Evidence(
                                text=evidence_text,
                                project_id=project_id,
                                start=start,
                                end=start + len(evidence_text),
                            )
                        )
            else:
                key = (evidence_text, project_id, None, None)
                if key not in seen:
                    seen.add(key)
                    evidence_objects.append(
                        Evidence(
                            text=evidence_text,
                            project_id=project_id,
                            start=None,
                            end=None,
                        )
                    )
        located = sum(item.start is not None for item in evidence_objects)
        unlocated = len(evidence_objects) - located
        return evidence_objects, located, unlocated
