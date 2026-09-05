"""Structured evidence reviewer for Agentic Workflow stage 3A."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from extractor.agentic_llm_client import LLMCompletion
from extractor.agentic_schema import (
    CandidateAbility,
    ControlAction,
    ErrorType,
    Evidence,
    ReviewResult,
    ReviewStatus,
    SchemaValidationError,
)


class ReviewAgentError(RuntimeError):
    """Base class for controlled Reviewer-stage failures."""

    def __init__(
        self,
        message: str,
        *,
        review_index: int | None = None,
        candidate_id: str | None = None,
        invalid_field: str | None = None,
        invalid_reference_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.model: str | None = None
        self.elapsed_ms: float | None = None
        self.usage: dict[str, Any] | None = None
        self.response_sha256: str | None = None
        self.review_index = review_index
        self.candidate_id = candidate_id
        self.invalid_field = invalid_field
        self.invalid_reference_id = invalid_reference_id

    def add_review_context(
        self,
        *,
        review_index: int | None = None,
        candidate_id: str | None = None,
    ) -> None:
        if self.review_index is None:
            self.review_index = review_index
        if self.candidate_id is None:
            self.candidate_id = candidate_id

    def attach_completion(self, completion: LLMCompletion) -> None:
        self.model = completion.model
        self.elapsed_ms = completion.elapsed_ms
        self.usage = (
            None if completion.usage is None else dict(completion.usage)
        )
        self.response_sha256 = hashlib.sha256(
            completion.content.encode("utf-8")
        ).hexdigest()

    def diagnostics_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "elapsed_ms": self.elapsed_ms,
            "usage": None if self.usage is None else dict(self.usage),
            "response_sha256": self.response_sha256,
            "review_index": self.review_index,
            "candidate_id": self.candidate_id,
            "invalid_field": self.invalid_field,
            "invalid_reference_id": self.invalid_reference_id,
        }

    def __str__(self) -> str:
        details = [self.message]
        for name, value in self.diagnostics_dict().items():
            if value is not None:
                rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
                details.append(f"{name}={rendered}")
        return "; ".join(details)


class ReviewParseError(ReviewAgentError):
    """Raised when model output is not one strict JSON object."""


class ReviewContractError(ReviewAgentError):
    """Raised when reviews violate the Reviewer output contract."""


class CompletionClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> LLMCompletion:
        ...


@dataclass(frozen=True)
class EvidenceCatalogSpan:
    """One stable, exact, line-based span offered to the Reviewer model."""

    span_id: str
    text: str
    start: int
    end: int
    source_type: str
    line_index: int

    def to_model_dict(self) -> dict[str, str]:
        return {"span_id": self.span_id, "text": self.text}

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "source_type": self.source_type,
            "line_index": self.line_index,
        }


def build_evidence_catalog(resume_text: str) -> list[EvidenceCatalogSpan]:
    """Build stable exact spans from every non-empty original text line."""

    if not isinstance(resume_text, str):
        raise ValueError("resume_text must be text")
    spans: list[EvidenceCatalogSpan] = []
    offset = 0
    for line_index, raw_line in enumerate(resume_text.splitlines(keepends=True)):
        text = raw_line
        if text.endswith("\r\n"):
            text = text[:-2]
        elif text.endswith(("\r", "\n")):
            text = text[:-1]
        if text.strip():
            spans.append(
                EvidenceCatalogSpan(
                    span_id=f"span_{len(spans) + 1:04d}",
                    text=text,
                    start=offset,
                    end=offset + len(text),
                    source_type="line",
                    line_index=line_index,
                )
            )
        offset += len(raw_line)
    return spans


RELOCATION_CATALOG_V1_PROTOCOL_VERSION = "1.0"
RELOCATION_CATALOG_V2_PROTOCOL_VERSION = "2.0"
DEFAULT_RELOCATION_OPTION_LIMIT = 5


def relocation_catalog_sha256(catalog: list[EvidenceCatalogSpan]) -> str:
    """Hash the complete deterministic catalog without exposing it in output."""

    canonical = [span.to_canonical_dict() for span in catalog]
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _evidence_anchor_indexes(
    resume_text: str,
    evidence: Evidence,
    catalog: list[EvidenceCatalogSpan],
) -> list[int]:
    if evidence.start is not None and evidence.end is not None:
        if resume_text[evidence.start:evidence.end] != evidence.text:
            raise ValueError(
                "candidate evidence offsets do not match exact resume text"
            )
        return [
            index
            for index, span in enumerate(catalog)
            if span.start <= evidence.start and evidence.end <= span.end
        ]

    positions: list[tuple[int, int]] = []
    offset = 0
    while True:
        start = resume_text.find(evidence.text, offset)
        if start < 0:
            break
        positions.append((start, start + len(evidence.text)))
        offset = start + 1
    return [
        index
        for index, span in enumerate(catalog)
        if any(span.start <= start and end <= span.end for start, end in positions)
    ]


def build_candidate_relocation_options(
    resume_text: str,
    candidate: CandidateAbility,
    catalog: list[EvidenceCatalogSpan] | None = None,
    *,
    max_options: int = DEFAULT_RELOCATION_OPTION_LIMIT,
) -> list[EvidenceCatalogSpan]:
    """Build bounded, stable relocation options around current evidence lines."""

    if isinstance(max_options, bool) or not isinstance(max_options, int):
        raise ValueError("max_options must be an integer")
    if max_options < 1:
        raise ValueError("max_options must be positive")
    spans = build_evidence_catalog(resume_text) if catalog is None else list(catalog)
    for span in spans:
        if resume_text[span.start:span.end] != span.text:
            raise ValueError(f"catalog span is not exact: {span.span_id}")

    anchor_indexes: set[int] = set()
    for evidence in candidate.evidence:
        anchors = _evidence_anchor_indexes(resume_text, evidence, spans)
        if not anchors:
            raise ValueError(
                f"candidate {candidate.candidate_id} evidence is not covered "
                "by a catalog line"
            )
        anchor_indexes.update(anchors)
    if not anchor_indexes:
        return []
    if len(anchor_indexes) > max_options:
        raise ValueError(
            f"candidate {candidate.candidate_id} has more evidence lines than "
            "the relocation option limit"
        )

    ranked_indexes = sorted(
        range(len(spans)),
        key=lambda index: (
            min(abs(index - anchor) for anchor in anchor_indexes),
            index,
        ),
    )
    selected = set(ranked_indexes[:max_options])
    selected.update(anchor_indexes)
    return [spans[index] for index in sorted(selected)]


def build_candidate_relocation_options_map(
    resume_text: str,
    candidates: list[CandidateAbility],
    catalog: list[EvidenceCatalogSpan] | None = None,
    *,
    max_options: int = DEFAULT_RELOCATION_OPTION_LIMIT,
) -> dict[str, list[EvidenceCatalogSpan]]:
    spans = build_evidence_catalog(resume_text) if catalog is None else list(catalog)
    return {
        candidate.candidate_id: build_candidate_relocation_options(
            resume_text,
            candidate,
            spans,
            max_options=max_options,
        )
        for candidate in candidates
    }


DEFAULT_REVIEWER_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "agentic_reviewer_prompt.txt"
)


def load_reviewer_prompt(
    prompt_path: str | Path | None = None,
) -> tuple[Path, str, str]:
    """Load the exact prompt text sent to the model and its stable SHA-256."""

    path = DEFAULT_REVIEWER_PROMPT_PATH if prompt_path is None else Path(prompt_path)
    if not path.exists() or not path.is_file():
        raise ValueError(f"reviewer prompt must be an existing regular file: {path}")
    try:
        content = path.read_text(encoding="utf-8-sig").strip()
    except OSError as error:
        raise ValueError(f"cannot read reviewer prompt file: {path}") from error
    if not content:
        raise ValueError(f"reviewer prompt must not be empty: {path}")
    prompt_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return path, content, prompt_sha256


@dataclass(frozen=True)
class ReviewExtractionResult:
    resume_id: str
    reviews: list[ReviewResult]
    model: str
    elapsed_ms: float
    usage: dict[str, Any] | None
    prompt_file: str
    prompt_sha256: str
    input_candidate_count: int
    raw_review_count: int
    accepted_review_count: int
    invalid_review_count: int
    approved_count: int
    revise_count: int
    rejected_count: int
    error_type_counts: dict[str, int]
    action_counts: dict[str, int]
    warnings: list[str]
    evidence_reference_mode: str = "text"
    relocation_catalog_protocol_version: str | None = None
    relocation_catalog_span_count: int = 0
    relocation_catalog_sha256: str | None = None
    candidate_relocation_option_counts: dict[str, int] = field(
        default_factory=dict
    )
    total_relocation_option_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "resume_id": self.resume_id,
            "reviews": [review.to_dict() for review in self.reviews],
            "model": self.model,
            "elapsed_ms": self.elapsed_ms,
            "usage": None if self.usage is None else dict(self.usage),
            "prompt_file": self.prompt_file,
            "prompt_sha256": self.prompt_sha256,
            "input_candidate_count": self.input_candidate_count,
            "raw_review_count": self.raw_review_count,
            "accepted_review_count": self.accepted_review_count,
            "invalid_review_count": self.invalid_review_count,
            "approved_count": self.approved_count,
            "revise_count": self.revise_count,
            "rejected_count": self.rejected_count,
            "error_type_counts": dict(self.error_type_counts),
            "action_counts": dict(self.action_counts),
            "warnings": list(self.warnings),
            "evidence_reference_mode": self.evidence_reference_mode,
            "relocation_catalog_protocol_version": (
                self.relocation_catalog_protocol_version
            ),
            "relocation_catalog_span_count": self.relocation_catalog_span_count,
            "relocation_catalog_sha256": self.relocation_catalog_sha256,
            "candidate_relocation_option_counts": dict(
                self.candidate_relocation_option_counts
            ),
            "total_relocation_option_count": self.total_relocation_option_count,
        }

    def diagnostics_dict(self) -> dict[str, Any]:
        result = self.to_dict()
        result.pop("reviews")
        return result


_REVIEW_FIELDS = {
    "candidate_id",
    "status",
    "error_types",
    "action",
    "reason",
    "target_ability",
    "target_evidence",
    "merge_target_id",
}
_FENCED_JSON = re.compile(
    r"\A\s*```(?:json)?[ \t]*\r?\n(?P<body>.*?)\r?\n?```[ \t]*\s*\Z",
    re.IGNORECASE | re.DOTALL,
)


class EvidenceReviewAgent:
    """Review all candidates together and return suggestions, never actions."""

    def __init__(
        self,
        client: CompletionClient,
        prompt_path: str | Path | None = None,
        evidence_reference_mode: str = "text",
    ) -> None:
        if evidence_reference_mode not in {"text", "catalog", "catalog_v2"}:
            raise ValueError(
                "evidence_reference_mode must be 'text', 'catalog', or "
                "'catalog_v2'"
            )
        self.client = client
        self.evidence_reference_mode = evidence_reference_mode
        (
            self.prompt_path,
            self.system_prompt,
            self.prompt_sha256,
        ) = load_reviewer_prompt(prompt_path)
        self.prompt_file = self.prompt_path.name

    def review(
        self,
        resume_id: str,
        resume_text: str,
        candidates: list[CandidateAbility],
    ) -> ReviewExtractionResult:
        resume_id = self._non_empty("resume_id", resume_id)
        if not isinstance(resume_text, str) or not resume_text.strip():
            raise ValueError("resume_text must be non-empty text")
        candidate_by_id = self._validate_candidates(resume_id, candidates)

        request_payload: dict[str, Any]
        catalog: list[EvidenceCatalogSpan] = []
        options_by_candidate_id: dict[str, list[EvidenceCatalogSpan]] = {}
        if self.evidence_reference_mode == "catalog":
            request_payload = {
                "resume_id": resume_id,
                "resume_text": resume_text,
                "candidates": [candidate.to_dict() for candidate in candidates],
            }
            catalog = build_evidence_catalog(resume_text)
            request_payload["evidence_reference_mode"] = "catalog"
            request_payload["evidence_catalog"] = [
                span.to_model_dict() for span in catalog
            ]
            options_by_candidate_id = {
                candidate.candidate_id: list(catalog) for candidate in candidates
            }
        elif self.evidence_reference_mode == "catalog_v2":
            catalog = build_evidence_catalog(resume_text)
            options_by_candidate_id = build_candidate_relocation_options_map(
                resume_text,
                candidates,
                catalog,
            )
            request_payload = {
                "resume_id": resume_id,
                "resume_text_for_ability_context": resume_text,
                "evidence_reference_mode": "catalog_v2",
                "relocation_catalog_protocol_version": (
                    RELOCATION_CATALOG_V2_PROTOCOL_VERSION
                ),
                "candidate_review_contexts": [
                    self._candidate_review_context(
                        candidate,
                        options_by_candidate_id[candidate.candidate_id],
                    )
                    for candidate in candidates
                ],
            }
        else:
            request_payload = {
                "resume_id": resume_id,
                "resume_text": resume_text,
                "candidates": [candidate.to_dict() for candidate in candidates],
            }
        user_prompt = json.dumps(
            request_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        completion = self.client.complete(self.system_prompt, user_prompt)
        if not isinstance(completion, LLMCompletion):
            raise ReviewAgentError("client must return LLMCompletion")
        try:
            reviews, raw_review_count = self._convert_completion_reviews(
                completion.content,
                candidate_by_id,
                resume_text,
                catalog,
                options_by_candidate_id,
                len(candidates),
            )
        except (ReviewParseError, ReviewContractError) as error:
            error.attach_completion(completion)
            raise

        error_counts = {error.value: 0 for error in ErrorType}
        action_counts = {action.value: 0 for action in ControlAction}
        for review in reviews:
            for error_type in review.error_types:
                error_counts[error_type.value] += 1
            action_counts[review.action.value] += 1
        approved_count = sum(
            review.status is ReviewStatus.PASSED for review in reviews
        )
        rejected_count = sum(
            review.action is ControlAction.DELETE for review in reviews
        )
        revise_count = sum(
            review.status is ReviewStatus.FAILED
            and review.action is not ControlAction.DELETE
            for review in reviews
        )
        return ReviewExtractionResult(
            resume_id=resume_id,
            reviews=reviews,
            model=completion.model,
            elapsed_ms=completion.elapsed_ms,
            usage=None if completion.usage is None else dict(completion.usage),
            prompt_file=self.prompt_file,
            prompt_sha256=self.prompt_sha256,
            input_candidate_count=len(candidates),
            raw_review_count=raw_review_count,
            accepted_review_count=len(reviews),
            invalid_review_count=0,
            approved_count=approved_count,
            revise_count=revise_count,
            rejected_count=rejected_count,
            error_type_counts=error_counts,
            action_counts=action_counts,
            warnings=[],
            evidence_reference_mode=self.evidence_reference_mode,
            relocation_catalog_protocol_version=(
                RELOCATION_CATALOG_V2_PROTOCOL_VERSION
                if self.evidence_reference_mode == "catalog_v2"
                else RELOCATION_CATALOG_V1_PROTOCOL_VERSION
                if self.evidence_reference_mode == "catalog"
                else None
            ),
            relocation_catalog_span_count=len(catalog),
            relocation_catalog_sha256=(
                relocation_catalog_sha256(catalog) if catalog else None
            ),
            candidate_relocation_option_counts={
                candidate_id: len(options)
                for candidate_id, options in options_by_candidate_id.items()
            },
            total_relocation_option_count=sum(
                len(options) for options in options_by_candidate_id.values()
            ),
        )

    @staticmethod
    def _candidate_review_context(
        candidate: CandidateAbility,
        options: list[EvidenceCatalogSpan],
    ) -> dict[str, Any]:
        return {
            "candidate": {
                "candidate_id": candidate.candidate_id,
                "fact": candidate.fact,
                "behavior": candidate.behavior,
                "ability": candidate.ability,
                "normalized_ability": candidate.normalized_ability,
                "reason": candidate.reason,
                "confidence": candidate.confidence,
            },
            "current_evidence": [
                evidence.to_dict() for evidence in candidate.evidence
            ],
            "relocation_options": [
                span.to_model_dict() for span in options
            ],
        }

    def _convert_completion_reviews(
        self,
        content: str,
        candidate_by_id: Mapping[str, CandidateAbility],
        resume_text: str,
        catalog: list[EvidenceCatalogSpan],
        options_by_candidate_id: Mapping[str, list[EvidenceCatalogSpan]],
        candidate_count: int,
    ) -> tuple[list[ReviewResult], int]:
        payload = self._parse_single_json_object(content)
        raw_reviews = self._validate_root(payload)
        catalog_by_id = {span.span_id: span for span in catalog}
        option_maps = {
            candidate_id: {span.span_id: span for span in options}
            for candidate_id, options in options_by_candidate_id.items()
        }

        reviews: list[ReviewResult] = []
        seen_review_ids: set[str] = set()
        for index, raw_review in enumerate(raw_reviews):
            try:
                review = self._convert_review(
                    raw_review,
                    index,
                    candidate_by_id,
                    resume_text,
                    self.evidence_reference_mode,
                    catalog_by_id,
                    option_maps,
                )
            except ReviewContractError as error:
                candidate_id = None
                if isinstance(raw_review, Mapping):
                    raw_candidate_id = raw_review.get("candidate_id")
                    if isinstance(raw_candidate_id, str) and raw_candidate_id.strip():
                        candidate_id = raw_candidate_id.strip()
                error.add_review_context(
                    review_index=index,
                    candidate_id=candidate_id,
                )
                raise
            if review.candidate_id in seen_review_ids:
                raise ReviewContractError(
                    f"duplicate review candidate_id: {review.candidate_id}",
                    review_index=index,
                    candidate_id=review.candidate_id,
                    invalid_field="candidate_id",
                )
            seen_review_ids.add(review.candidate_id)
            reviews.append(review)

        expected_ids = set(candidate_by_id)
        missing_ids = sorted(expected_ids - seen_review_ids)
        unknown_ids = sorted(seen_review_ids - expected_ids)
        if missing_ids or unknown_ids or len(reviews) != candidate_count:
            details: list[str] = []
            if missing_ids:
                details.append("missing reviews: " + ", ".join(missing_ids))
            if unknown_ids:
                details.append("unknown candidate_ids: " + ", ".join(unknown_ids))
            if len(reviews) != candidate_count:
                details.append(
                    f"review count {len(reviews)} does not match candidate count "
                    f"{candidate_count}"
                )
            raise ReviewContractError("; ".join(details))
        return reviews, len(raw_reviews)

    @staticmethod
    def _non_empty(name: str, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _validate_candidates(
        resume_id: str,
        candidates: Any,
    ) -> dict[str, CandidateAbility]:
        if not isinstance(candidates, list):
            raise ValueError("candidates must be a list")
        result: dict[str, CandidateAbility] = {}
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, CandidateAbility):
                raise ValueError(
                    f"candidates[{index}] must be a CandidateAbility"
                )
            if candidate.resume_id != resume_id:
                raise ValueError(
                    f"candidate {candidate.candidate_id} belongs to another resume"
                )
            if candidate.candidate_id in result:
                raise ValueError(
                    f"duplicate input candidate_id: {candidate.candidate_id}"
                )
            result[candidate.candidate_id] = candidate
        return result

    @classmethod
    def _parse_single_json_object(cls, content: str) -> Mapping[str, Any]:
        if not isinstance(content, str) or not content.strip():
            raise ReviewParseError("model content must be non-empty text")
        fenced = _FENCED_JSON.fullmatch(content)
        json_text = fenced.group("body") if fenced else content.strip()
        try:
            payload = json.loads(
                json_text,
                object_pairs_hook=cls._reject_duplicate_keys,
                parse_constant=cls._reject_json_constant,
            )
        except (json.JSONDecodeError, ValueError) as error:
            raise ReviewParseError(
                "model output must be exactly one valid JSON object"
            ) from error
        if not isinstance(payload, Mapping):
            raise ReviewParseError("model output JSON must be an object")
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
        if keys != {"reviews"}:
            missing = {"reviews"} - keys
            unknown = keys - {"reviews"}
            details: list[str] = []
            if missing:
                details.append("missing reviews")
            if unknown:
                details.append("unknown fields: " + ", ".join(sorted(unknown)))
            raise ReviewParseError("root object invalid: " + "; ".join(details))
        reviews = payload["reviews"]
        if not isinstance(reviews, list):
            raise ReviewParseError("reviews must be a list")
        return reviews

    @classmethod
    def _convert_review(
        cls,
        value: Any,
        index: int,
        candidate_by_id: Mapping[str, CandidateAbility],
        resume_text: str,
        evidence_reference_mode: str = "text",
        catalog_by_id: Mapping[str, EvidenceCatalogSpan] | None = None,
        option_maps: Mapping[
            str, Mapping[str, EvidenceCatalogSpan]
        ] | None = None,
    ) -> ReviewResult:
        prefix = f"reviews[{index}]"
        if not isinstance(value, Mapping):
            raise ReviewContractError(f"{prefix} must be an object")
        keys = set(value)
        missing = _REVIEW_FIELDS - keys
        unknown = keys - _REVIEW_FIELDS
        if missing:
            raise ReviewContractError(
                f"{prefix} missing fields: {', '.join(sorted(missing))}"
            )
        if unknown:
            raise ReviewContractError(
                f"{prefix} contains unknown fields: {', '.join(sorted(unknown))}"
            )

        candidate_id = value["candidate_id"]
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise ReviewContractError(
                f"{prefix}.candidate_id must be a non-empty string"
            )
        candidate_id = candidate_id.strip()
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None:
            raise ReviewContractError(
                f"{prefix} contains unknown candidate_id: {candidate_id}"
            )

        if evidence_reference_mode in {"catalog", "catalog_v2"}:
            available_spans = (
                {} if catalog_by_id is None else catalog_by_id
            )
            if evidence_reference_mode == "catalog_v2":
                available_spans = (
                    {}
                    if option_maps is None
                    else option_maps.get(candidate_id, {})
                )
            target_evidence = cls._convert_catalog_target_evidence(
                value["target_evidence"],
                value["action"],
                prefix,
                candidate.project_id,
                available_spans,
                candidate.evidence if evidence_reference_mode == "catalog_v2" else None,
            )
        else:
            target_evidence = cls._convert_target_evidence(
                value["target_evidence"],
                prefix,
                candidate.project_id,
                resume_text,
            )
        review_payload = dict(value)
        review_payload["candidate_id"] = candidate_id
        review_payload["target_evidence"] = [
            evidence.to_dict() for evidence in target_evidence
        ]
        try:
            review = ReviewResult.from_dict(review_payload)
        except SchemaValidationError as error:
            raise ReviewContractError(f"{prefix} is invalid: {error}") from error

        if len(review.error_types) != len(value["error_types"]):
            raise ReviewContractError(f"{prefix}.error_types contains duplicates")
        if review.action is ControlAction.MERGE:
            if review.merge_target_id is None:
                raise ReviewContractError(
                    f"{prefix} merge requires merge_target_id"
                )
            if review.merge_target_id == candidate_id:
                raise ReviewContractError(
                    f"{prefix} merge_target_id cannot point to itself"
                )
            if review.merge_target_id not in candidate_by_id:
                raise ReviewContractError(
                    f"{prefix} merge_target_id does not exist: "
                    f"{review.merge_target_id}"
                )
        if review.action in {ControlAction.RENAME, ControlAction.NARROW}:
            if review.target_ability is None:
                raise ReviewContractError(
                    f"{prefix} {review.action.value} requires target_ability"
                )
        if review.action is ControlAction.RELOCATE and not review.target_evidence:
            raise ReviewContractError(
                f"{prefix} relocate requires target_evidence"
            )
        return review

    @staticmethod
    def _convert_catalog_target_evidence(
        value: Any,
        action: Any,
        prefix: str,
        project_id: str,
        catalog_by_id: Mapping[str, EvidenceCatalogSpan],
        current_evidence: list[Evidence] | None = None,
    ) -> list[Evidence]:
        if not isinstance(value, list):
            raise ReviewContractError(
                f"{prefix}.target_evidence must be a span_id list",
                invalid_field="target_evidence",
            )
        if action != ControlAction.RELOCATE.value:
            if value:
                raise ReviewContractError(
                    f"{prefix}.target_evidence must be empty for non-relocate action",
                    invalid_field="target_evidence",
                )
            return []
        if not value:
            raise ReviewContractError(
                f"{prefix} relocate requires at least one target_evidence span_id",
                invalid_field="target_evidence",
            )

        result: list[Evidence] = []
        seen_ids: set[str] = set()
        for index, item in enumerate(value):
            field = f"target_evidence[{index}]"
            if (
                not isinstance(item, str)
                or not item.strip()
                or item != item.strip()
            ):
                raise ReviewContractError(
                    f"{prefix}.{field} must be a non-empty exact span_id",
                    invalid_field=field,
                    invalid_reference_id=item if isinstance(item, str) else None,
                )
            if item in seen_ids:
                raise ReviewContractError(
                    f"{prefix}.{field} contains duplicate span_id",
                    invalid_field=field,
                    invalid_reference_id=item,
                )
            seen_ids.add(item)
            span = catalog_by_id.get(item)
            if span is None:
                raise ReviewContractError(
                    f"{prefix}.{field} references unknown span_id",
                    invalid_field=field,
                    invalid_reference_id=item,
                )
            result.append(
                Evidence(
                    text=span.text,
                    project_id=project_id,
                    start=span.start,
                    end=span.end,
                )
            )
        if current_evidence is not None:
            current_keys = {
                (item.text, item.project_id, item.start, item.end)
                for item in current_evidence
            }
            target_keys = {
                (item.text, item.project_id, item.start, item.end)
                for item in result
            }
            if target_keys == current_keys:
                raise ReviewContractError(
                    f"{prefix}.target_evidence is identical to current evidence",
                    invalid_field="target_evidence",
                )
        return result

    @staticmethod
    def _convert_target_evidence(
        value: Any,
        prefix: str,
        project_id: str,
        resume_text: str,
    ) -> list[Evidence]:
        if not isinstance(value, list):
            raise ReviewContractError(
                f"{prefix}.target_evidence must be a string list",
                invalid_field="target_evidence",
            )
        result: list[Evidence] = []
        seen: set[tuple[str, str, int, int]] = set()
        for index, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                raise ReviewContractError(
                    f"{prefix}.target_evidence[{index}] must be non-empty text",
                    invalid_field=f"target_evidence[{index}]",
                )
            text = item.strip()
            positions: list[int] = []
            offset = 0
            while True:
                position = resume_text.find(text, offset)
                if position < 0:
                    break
                positions.append(position)
                offset = position + 1
            if not positions:
                raise ReviewContractError(
                    f"{prefix}.target_evidence[{index}] is not an exact "
                    "resume substring",
                    invalid_field=f"target_evidence[{index}]",
                )
            for start in positions:
                key = (text, project_id, start, start + len(text))
                if key not in seen:
                    seen.add(key)
                    result.append(
                        Evidence(
                            text=text,
                            project_id=project_id,
                            start=start,
                            end=start + len(text),
                        )
                    )
        return result
