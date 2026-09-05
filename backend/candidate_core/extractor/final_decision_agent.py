"""Bounded final classifier for formal ability prediction records."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from extractor.ability_taxonomy_v2 import AbilityTaxonomyV2, TaxonomyNode
from extractor.agentic_llm_client import LLMCompletion
from extractor.agentic_schema import CandidateAbility
from extractor.final_decision_schema import (
    FINAL_DECISION_RESPONSE_VERSION,
    CandidateFinalDecision,
    FinalDecisionResponse,
    FinalDecisionSchemaError,
    FinalDecisionType,
)
from extractor.review_assessment_schema import EvidenceAuditResult
from extractor.shadow_review_bundle import ShadowReviewBundle


FINAL_DECISION_REQUEST_VERSION = "final_decision_request_v1"
DEFAULT_FINAL_DECISION_PROMPT = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "agentic_final_decision_prompt_v1.txt"
)
_FINAL_NODE_TYPES = {"ability", "high_level_ability"}
_CONTRACT_RETRY_INSTRUCTION = (
    "Previous response did not satisfy the required JSON schema. "
    "Return only a valid object matching the provided schema. "
    "Do not change the decision merely because this is a retry."
)


class ShadowAvailability(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE_INVALID_CONTRACT = "unavailable_invalid_contract"


class FinalDecisionAgentError(RuntimeError):
    """Raised when the bounded final decision call fails safely."""

    def __init__(
        self,
        message: str,
        *,
        contract_retry_count: int = 0,
        last_error_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.contract_retry_count = contract_retry_count
        self.last_error_type = last_error_type


class FinalDecisionValidationError(FinalDecisionAgentError):
    """Raised when a parsed decision conflicts with deterministic inputs."""


class FinalDecisionCompletionClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> LLMCompletion:
        ...


@dataclass(frozen=True)
class FinalDecisionContext:
    candidate: CandidateAbility
    frozen_experience_text: str
    taxonomy_candidates: tuple[TaxonomyNode, ...]
    evidence_audit: EvidenceAuditResult
    shadow_status: ShadowAvailability
    shadow_bundle: ShadowReviewBundle | None

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, CandidateAbility):
            raise TypeError("candidate must be CandidateAbility")
        if not isinstance(self.frozen_experience_text, str) or not self.frozen_experience_text.strip():
            raise ValueError("frozen_experience_text must be non-empty")
        nodes = tuple(self.taxonomy_candidates)
        if any(not isinstance(item, TaxonomyNode) for item in nodes):
            raise TypeError("taxonomy_candidates must contain TaxonomyNode")
        if len({item.id for item in nodes}) != len(nodes):
            raise ValueError("taxonomy_candidates must be unique")
        if not isinstance(self.evidence_audit, EvidenceAuditResult):
            raise TypeError("evidence_audit must be EvidenceAuditResult")
        if not isinstance(self.shadow_status, ShadowAvailability):
            raise TypeError("shadow_status must be ShadowAvailability")
        if self.shadow_status is ShadowAvailability.AVAILABLE:
            if not isinstance(self.shadow_bundle, ShadowReviewBundle):
                raise TypeError("available shadow requires ShadowReviewBundle")
        elif self.shadow_bundle is not None:
            raise ValueError("unavailable shadow must not carry a bundle")
        candidate_id = self.candidate.candidate_id
        if self.evidence_audit.candidate_id != candidate_id:
            raise ValueError("evidence_audit candidate_id mismatch")
        if (
            self.shadow_bundle is not None
            and self.shadow_bundle.candidate_id != candidate_id
        ):
            raise ValueError("shadow_bundle candidate_id mismatch")
        object.__setattr__(self, "taxonomy_candidates", nodes)

    def allowed_mapped_ability_ids(self) -> tuple[str, ...]:
        ids = [
            node.id
            for node in self.taxonomy_candidates
            if node.node_type in _FINAL_NODE_TYPES and not node.deprecated
        ]
        if self.shadow_bundle is not None:
            for node_id in self.shadow_bundle.split_recommendation.suggested_atomic_taxonomy_ids:
                if node_id not in ids:
                    ids.append(node_id)
        return tuple(ids)


@dataclass(frozen=True)
class FinalDecisionBatch:
    decisions: tuple[CandidateFinalDecision, ...]
    diagnostics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": FINAL_DECISION_RESPONSE_VERSION,
            "decisions": [item.to_dict() for item in self.decisions],
            "diagnostics": copy.deepcopy(self.diagnostics),
        }


class FinalDecisionValidator:
    """Pure structural validation; it does not rescore semantic quality."""

    def __init__(self, taxonomy: AbilityTaxonomyV2) -> None:
        if not isinstance(taxonomy, AbilityTaxonomyV2):
            raise TypeError("taxonomy must be AbilityTaxonomyV2")
        self.taxonomy = taxonomy

    def validate(
        self,
        contexts: Sequence[FinalDecisionContext],
        decisions: Sequence[CandidateFinalDecision],
    ) -> tuple[CandidateFinalDecision, ...]:
        context_by_id = {item.candidate.candidate_id: item for item in contexts}
        ids = [item.candidate_id for item in decisions]
        expected = [item.candidate.candidate_id for item in contexts]
        if ids != expected:
            raise FinalDecisionValidationError(
                "decision candidate coverage or order does not match input"
            )
        for decision in decisions:
            context = context_by_id[decision.candidate_id]
            allowed_ids = set(context.allowed_mapped_ability_ids())
            seen_targets: set[str] = set()
            if context.shadow_bundle is not None:
                split_supported = (
                    context.shadow_bundle.split_recommendation.split_recommended
                )
                supported_components = set(
                    context.shadow_bundle.split_recommendation.supported_component_ids
                )
            else:
                supported_components = {
                    item.taxonomy_id
                    for item in context.evidence_audit.component_assessments
                    if item.support.value == "supported"
                }
                split_supported = (
                    context.evidence_audit.compound_label.value
                    in {"compound_unsupported", "split_recommended"}
                    and len(supported_components) >= 2
                )
            if decision.decision is FinalDecisionType.SPLIT and not split_supported:
                raise FinalDecisionValidationError(
                    f"candidate {decision.candidate_id} split lacks deterministic support"
                )
            for atom in decision.atomic_records():
                if atom.evidence not in context.frozen_experience_text:
                    raise FinalDecisionValidationError(
                        f"candidate {decision.candidate_id} evidence is not in frozen source"
                    )
                if atom.decision is FinalDecisionType.MAPPED:
                    if atom.ability_id not in allowed_ids:
                        raise FinalDecisionValidationError(
                            f"candidate {decision.candidate_id} ability_id is outside input candidates"
                        )
                    node = self.taxonomy.get_node(atom.ability_id)
                    if node.node_type not in _FINAL_NODE_TYPES or node.deprecated:
                        raise FinalDecisionValidationError(
                            f"candidate {decision.candidate_id} ability_id is not a final node"
                        )
                    if (
                        decision.decision is FinalDecisionType.SPLIT
                        and atom.ability_id not in supported_components
                    ):
                        raise FinalDecisionValidationError(
                            f"candidate {decision.candidate_id} split target is not deterministic-supported"
                        )
                    target = f"mapped:{atom.ability_id}"
                elif atom.decision is FinalDecisionType.UNMAPPED:
                    target = f"unmapped:{atom.unmapped_ability}"
                else:
                    target = "reject"
                if target != "reject" and target in seen_targets:
                    raise FinalDecisionValidationError(
                        f"candidate {decision.candidate_id} contains duplicate output targets"
                    )
                seen_targets.add(target)
        return tuple(decisions)


class FinalDecisionAgent:
    """Make exactly one bounded final-decision request for supplied contexts."""

    def __init__(
        self,
        client: FinalDecisionCompletionClient,
        taxonomy: AbilityTaxonomyV2,
        prompt_path: str | Path | None = None,
    ) -> None:
        if client is None or not callable(getattr(client, "complete", None)):
            raise TypeError("client must provide complete(system_prompt, user_prompt)")
        if not isinstance(taxonomy, AbilityTaxonomyV2):
            raise TypeError("taxonomy must be AbilityTaxonomyV2")
        self.client = client
        self.taxonomy = taxonomy
        self.prompt_path = (
            DEFAULT_FINAL_DECISION_PROMPT
            if prompt_path is None
            else Path(prompt_path)
        )
        if not self.prompt_path.is_file():
            raise ValueError("final decision prompt must be an existing file")
        self.system_prompt = self.prompt_path.read_text(
            encoding="utf-8-sig"
        ).strip()
        if not self.system_prompt:
            raise ValueError("final decision prompt must not be empty")
        self.prompt_sha256 = hashlib.sha256(
            self.system_prompt.encode("utf-8")
        ).hexdigest()
        self.taxonomy_sha256 = hashlib.sha256(
            self.taxonomy.serialize().encode("utf-8")
        ).hexdigest()
        self.validator = FinalDecisionValidator(taxonomy)

    def _complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> LLMCompletion:
        complete_json = getattr(self.client, "complete_json", None)
        if callable(complete_json):
            return complete_json(
                system_prompt,
                user_prompt,
                max_tokens=32768,
            )
        # Compatibility fallback for test/fake clients.
        return self.client.complete(system_prompt, user_prompt)

    def decide(
        self,
        resume_id: str,
        source_experience_id: str,
        contexts: Sequence[FinalDecisionContext],
    ) -> FinalDecisionBatch:
        if not isinstance(resume_id, str) or not resume_id.strip():
            raise ValueError("resume_id must be non-empty")
        if not isinstance(source_experience_id, str) or not source_experience_id.strip():
            raise ValueError("source_experience_id must be non-empty")
        contexts = tuple(contexts)
        if not contexts:
            return FinalDecisionBatch(
                decisions=(),
                diagnostics=self._diagnostics(None, 0, 0),
            )
        ids = [item.candidate.candidate_id for item in contexts]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate contexts must have unique candidate_id")
        for context in contexts:
            if context.candidate.resume_id != resume_id:
                raise ValueError("candidate resume_id mismatch")
            if context.candidate.project_id != source_experience_id:
                raise ValueError("candidate project_id must equal source_experience_id")
            for node in context.taxonomy_candidates:
                if self.taxonomy.get_node(node.id) != node:
                    raise ValueError("taxonomy candidate does not belong to frozen taxonomy")
        request = self.build_request(resume_id, source_experience_id, contexts)
        encoded = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        completion = self._complete_json(self.system_prompt, encoded)
        contract_retry_count = 0
        try:
            decisions = self._parse_and_validate(completion, ids, contexts)
        except (FinalDecisionSchemaError, FinalDecisionValidationError):
            contract_retry_count = 1
            completion = self._complete_json(
                self.system_prompt + "\n\n" + _CONTRACT_RETRY_INSTRUCTION,
                encoded,
            )
            try:
                decisions = self._parse_and_validate(completion, ids, contexts)
            except (FinalDecisionSchemaError, FinalDecisionValidationError) as error:
                raise FinalDecisionAgentError(
                    str(error),
                    contract_retry_count=contract_retry_count,
                    last_error_type=type(error).__name__,
                ) from error
        return FinalDecisionBatch(
            decisions=decisions,
            diagnostics=self._diagnostics(
                completion,
                len(contexts),
                len(encoded.encode("utf-8")),
                contract_retry_count,
            ),
        )

    def _parse_and_validate(
        self,
        completion: LLMCompletion,
        expected_candidate_ids: Sequence[str],
        contexts: Sequence[FinalDecisionContext],
    ) -> tuple[CandidateFinalDecision, ...]:
        if not isinstance(completion, LLMCompletion):
            raise FinalDecisionSchemaError("client must return LLMCompletion")
        response = FinalDecisionResponse.parse_json(
            completion.content,
            expected_candidate_ids=expected_candidate_ids,
        )
        return self.validator.validate(contexts, response.decisions)

    def build_request(
        self,
        resume_id: str,
        source_experience_id: str,
        contexts: Sequence[FinalDecisionContext],
    ) -> dict[str, Any]:
        return {
            "schema_version": FINAL_DECISION_REQUEST_VERSION,
            "resume_id": resume_id,
            "source_experience_id": source_experience_id,
            "taxonomy_version": self.taxonomy.taxonomy_version,
            "candidate_contexts": [self._context_payload(item) for item in contexts],
        }

    @staticmethod
    def _taxonomy_payload(node: TaxonomyNode) -> dict[str, Any]:
        return {
            "id": node.id,
            "canonical_name": node.canonical_name,
            "node_type": node.node_type,
            "description": node.description,
            "includes": list(node.includes),
            "excludes": list(node.excludes),
            "evidence_requirements": node.evidence_requirements.to_dict(),
            "strong_qualifiers": list(node.strong_qualifiers),
            "forbidden_inferences": list(node.forbidden_inferences),
        }

    def _context_payload(self, context: FinalDecisionContext) -> dict[str, Any]:
        return {
            "candidate": context.candidate.to_dict(),
            "frozen_experience_text": context.frozen_experience_text,
            "taxonomy_candidates": [
                self._taxonomy_payload(item) for item in context.taxonomy_candidates
            ],
            "allowed_mapped_ability_ids": list(
                context.allowed_mapped_ability_ids()
            ),
            "evidence_audit_result": context.evidence_audit.to_dict(),
            "shadow_status": context.shadow_status.value,
            "semantic_shadow_bundle": (
                None
                if context.shadow_bundle is None
                else context.shadow_bundle.to_dict()
            ),
        }

    def _diagnostics(
        self,
        completion: LLMCompletion | None,
        candidate_count: int,
        request_size: int,
        contract_retry_count: int = 0,
    ) -> dict[str, Any]:
        return {
            "prompt_file": self.prompt_path.name,
            "prompt_sha256": self.prompt_sha256,
            "taxonomy_sha256": self.taxonomy_sha256,
            "request_contract_version": FINAL_DECISION_REQUEST_VERSION,
            "response_contract_version": FINAL_DECISION_RESPONSE_VERSION,
            "candidate_count": candidate_count,
            "request_size_bytes": request_size,
            "contract_retry_count": contract_retry_count,
            "model": None if completion is None else completion.model,
            "elapsed_ms": None if completion is None else completion.elapsed_ms,
            "usage": (
                None
                if completion is None or completion.usage is None
                else copy.deepcopy(completion.usage)
            ),
            "response_sha256": (
                None
                if completion is None
                else hashlib.sha256(
                    completion.content.encode("utf-8")
                ).hexdigest()
            ),
            "controller_executed": False,
        }
