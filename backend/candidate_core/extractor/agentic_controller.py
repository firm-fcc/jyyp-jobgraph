"""Deterministic policy controller for the agentic workflow.

The reviewer proposes an action, but this controller is authoritative: it maps
review errors through ``agentic_policy.json`` and never calls an LLM.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from extractor.agentic_schema import (
    CandidateAbility,
    CandidateStatus,
    ControlAction,
    ErrorType,
    Evidence,
    ReviewResult,
    ReviewStatus,
)


class ControllerPolicyError(ValueError):
    """Raised for invalid policy files or impossible control operations."""


class InvalidReviewError(ControllerPolicyError):
    """Raised when a review contains invalid technical control input."""


class ControllerStateError(ControllerPolicyError):
    """Raised when an operation violates the controller state machine."""


@dataclass(frozen=True)
class ControlResult:
    action: ControlAction
    candidate: CandidateAbility
    requires_repair: bool = False
    merged_candidate_ids: tuple[str, ...] = ()
    rationale: str = ""


class AgenticController:
    def __init__(
        self,
        policy_path: str | Path | None = None,
        policy: Mapping[str, Any] | None = None,
    ) -> None:
        if policy_path is not None and policy is not None:
            raise ControllerPolicyError(
                "provide either policy_path or policy, not both"
            )
        if policy is None:
            if policy_path is None:
                policy_path = (
                    Path(__file__).resolve().parent.parent
                    / "config"
                    / "agentic_policy.json"
                )
            policy = json.loads(
                Path(policy_path).read_text(encoding="utf-8-sig")
            )
        self.policy = self._validate_policy(policy)
        self.error_action_map = {
            ErrorType(error_name): ControlAction(action_name)
            for error_name, action_name in self.policy[
                "error_action_map"
            ].items()
        }
        self.action_priority = {
            ControlAction(action_name): index
            for index, action_name in enumerate(
                self.policy["action_priority"]
            )
        }
        self.allowed_repair_errors = {
            ErrorType(value)
            for value in self.policy["allowed_repair_error_types"]
        }
        self.max_revisions = self.policy["max_revisions"]
        second_failure = self.policy["second_review_failure"]
        self.second_failure_default = ControlAction(
            second_failure["default_action"]
        )
        self.second_failure_delete_errors = {
            ErrorType(value)
            for value in second_failure["delete_on_error_types"]
        }

    def _validate_policy(self, policy: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(policy, Mapping):
            raise ControllerPolicyError("policy must be a mapping")
        required = {
            "policy_version",
            "error_action_map",
            "action_priority",
            "allowed_repair_error_types",
            "max_revisions",
            "second_review_failure",
        }
        unknown = sorted(set(policy) - required)
        missing = sorted(required - set(policy))
        if missing:
            raise ControllerPolicyError(
                f"policy missing fields: {', '.join(missing)}"
            )
        if unknown:
            raise ControllerPolicyError(
                f"policy contains unknown fields: {', '.join(unknown)}"
            )

        policy_version = policy["policy_version"]
        if not isinstance(policy_version, str) or not policy_version.strip():
            raise ControllerPolicyError(
                "policy_version must be a non-empty string"
            )
        policy_version = policy_version.strip()

        error_action_map = policy["error_action_map"]
        if not isinstance(error_action_map, Mapping):
            raise ControllerPolicyError("error_action_map must be a mapping")
        expected_errors = {item.value for item in ErrorType}
        if set(error_action_map) != expected_errors:
            raise ControllerPolicyError(
                "error_action_map must define every ErrorType exactly once"
            )
        try:
            mapped_actions = {
                ControlAction(value) for value in error_action_map.values()
            }
        except ValueError as error:
            raise ControllerPolicyError(
                "error_action_map contains an invalid action"
            ) from error

        priority = policy["action_priority"]
        if not isinstance(priority, list) or not priority:
            raise ControllerPolicyError("action_priority must be a non-empty list")
        try:
            priority_actions = [ControlAction(value) for value in priority]
        except ValueError as error:
            raise ControllerPolicyError(
                "action_priority contains an invalid action"
            ) from error
        if len(priority_actions) != len(set(priority_actions)):
            raise ControllerPolicyError("action_priority cannot contain duplicates")
        expected_actions = set(ControlAction)
        if set(priority_actions) != expected_actions:
            raise ControllerPolicyError(
                "action_priority must contain every ControlAction exactly once"
            )
        if not mapped_actions.issubset(expected_actions):
            raise ControllerPolicyError("error_action_map contains unknown actions")

        repair_errors = policy["allowed_repair_error_types"]
        if not isinstance(repair_errors, list):
            raise ControllerPolicyError(
                "allowed_repair_error_types must be a list"
            )
        try:
            normalized_repair_errors = [
                ErrorType(value) for value in repair_errors
            ]
        except ValueError as error:
            raise ControllerPolicyError(
                "allowed_repair_error_types contains an invalid ErrorType"
            ) from error
        if len(normalized_repair_errors) != len(set(normalized_repair_errors)):
            raise ControllerPolicyError(
                "allowed_repair_error_types cannot contain duplicates"
            )

        max_revisions = policy["max_revisions"]
        if (
            isinstance(max_revisions, bool)
            or not isinstance(max_revisions, int)
            or max_revisions != 1
        ):
            raise ControllerPolicyError("max_revisions must be exactly 1")

        second_failure = policy["second_review_failure"]
        if not isinstance(second_failure, Mapping):
            raise ControllerPolicyError(
                "second_review_failure must be a mapping"
            )
        if set(second_failure) != {
            "default_action",
            "delete_on_error_types",
        }:
            raise ControllerPolicyError(
                "second_review_failure has invalid fields"
            )
        try:
            default_action = ControlAction(second_failure["default_action"])
            delete_errors = [
                ErrorType(value)
                for value in second_failure["delete_on_error_types"]
            ]
        except (TypeError, ValueError) as error:
            raise ControllerPolicyError(
                "second_review_failure contains invalid enum values"
            ) from error
        if default_action not in {
            ControlAction.DELETE,
            ControlAction.LOW_CONFIDENCE,
        }:
            raise ControllerPolicyError(
                "second failure default must be delete or low_confidence"
            )
        if not isinstance(second_failure["delete_on_error_types"], list):
            raise ControllerPolicyError(
                "delete_on_error_types must be a list"
            )

        return {
            "policy_version": policy_version,
            "error_action_map": dict(error_action_map),
            "action_priority": list(priority),
            "allowed_repair_error_types": [
                item.value for item in normalized_repair_errors
            ],
            "max_revisions": max_revisions,
            "second_review_failure": {
                "default_action": default_action.value,
                "delete_on_error_types": [item.value for item in delete_errors],
            },
        }

    def decide_action(self, review: ReviewResult) -> ControlAction:
        """Return the policy action; the reviewer's suggestion is not binding."""
        review_copy = ReviewResult.from_dict(review.to_dict())
        if review_copy.status is ReviewStatus.PASSED:
            return ControlAction.KEEP
        actions = [
            self.error_action_map[error_type]
            for error_type in review_copy.error_types
        ]
        return min(actions, key=lambda action: self.action_priority[action])

    def process(
        self,
        candidate: CandidateAbility,
        review: ReviewResult,
        peer_candidates: Iterable[CandidateAbility] = (),
    ) -> ControlResult:
        """Apply one review without mutating candidate, review, or peers."""
        candidate_copy = CandidateAbility.from_dict(candidate.to_dict())
        review_copy = ReviewResult.from_dict(review.to_dict())
        peers = [
            CandidateAbility.from_dict(item.to_dict())
            for item in peer_candidates
        ]

        if candidate_copy.candidate_id != review_copy.candidate_id:
            raise ControllerPolicyError(
                "review candidate_id does not match the candidate"
            )

        if review_copy.status is ReviewStatus.PASSED:
            return ControlResult(
                action=ControlAction.KEEP,
                candidate=candidate_copy.copy_with(
                    status=CandidateStatus.APPROVED.value
                ),
                rationale="review passed",
            )

        if candidate_copy.revision_round >= self.max_revisions:
            return self._second_failure(candidate_copy, review_copy.error_types)

        action = self.decide_action(review_copy)
        if action is ControlAction.DELETE:
            return self._delete(candidate_copy, review_copy.reason)
        if action in {ControlAction.RENAME, ControlAction.NARROW}:
            return self._change_ability(candidate_copy, review_copy, action)
        if action is ControlAction.RELOCATE:
            return self._relocate(candidate_copy, review_copy)
        if action is ControlAction.MERGE:
            return self._merge(candidate_copy, review_copy, peers)
        if action is ControlAction.REPAIR:
            return self._request_repair(candidate_copy, review_copy)
        if action is ControlAction.LOW_CONFIDENCE:
            return self._low_confidence(candidate_copy, review_copy.reason)
        if action is ControlAction.KEEP:
            return ControlResult(
                action=ControlAction.KEEP,
                candidate=candidate_copy.copy_with(
                    status=CandidateStatus.APPROVED.value
                ),
                rationale=review_copy.reason,
            )
        raise ControllerPolicyError(
            f"no controller dispatch exists for action: {action.value}"
        )

    def apply_repair(
        self,
        original: CandidateAbility,
        repaired: CandidateAbility,
    ) -> ControlResult:
        """Accept one externally produced repair while enforcing lineage/rounds."""
        original_copy = CandidateAbility.from_dict(original.to_dict())
        repaired_copy = CandidateAbility.from_dict(repaired.to_dict())
        if original_copy.status is not CandidateStatus.NEEDS_REPAIR:
            raise ControllerStateError(
                "apply_repair requires status=needs_repair; "
                f"candidate_id={original_copy.candidate_id}, "
                f"status={original_copy.status.value}"
            )
        if original_copy.revision_round != 0:
            raise ControllerStateError(
                "apply_repair requires revision_round=0; "
                f"candidate_id={original_copy.candidate_id}, "
                f"revision_round={original_copy.revision_round}"
            )
        if original_copy.resume_id != repaired_copy.resume_id:
            raise ControllerPolicyError(
                "a repaired candidate must belong to the same resume"
            )
        if (
            repaired_copy.candidate_id != original_copy.candidate_id
            and repaired_copy.parent_candidate_id != original_copy.candidate_id
        ):
            raise ControllerPolicyError(
                "a new repaired candidate_id must reference the original as parent"
            )

        lineage = self._merge_strings(
            original_copy.lineage,
            repaired_copy.lineage,
            [original_copy.candidate_id, repaired_copy.candidate_id],
        )
        parent_id = repaired_copy.parent_candidate_id
        if repaired_copy.candidate_id == original_copy.candidate_id:
            parent_id = original_copy.parent_candidate_id
        revised = repaired_copy.copy_with(
            revision_round=original_copy.revision_round + 1,
            parent_candidate_id=parent_id,
            status=CandidateStatus.PENDING_REVIEW.value,
            lineage=lineage,
        )
        return ControlResult(
            action=ControlAction.REPAIR,
            candidate=revised,
            rationale="one repair applied; candidate requires final review",
        )

    def _change_ability(
        self,
        candidate: CandidateAbility,
        review: ReviewResult,
        action: ControlAction,
    ) -> ControlResult:
        if not review.target_ability:
            raise InvalidReviewError(
                f"invalid {action.value} review: "
                f"candidate_id={candidate.candidate_id}, "
                "target_ability is empty"
            )
        revised = candidate.copy_with(
            ability=review.target_ability,
            normalized_ability=review.target_ability,
            category={},
            revision_round=candidate.revision_round + 1,
            status=CandidateStatus.PENDING_REVIEW.value,
        )
        return ControlResult(
            action=action,
            candidate=revised,
            rationale=review.reason,
        )

    def _relocate(
        self,
        candidate: CandidateAbility,
        review: ReviewResult,
    ) -> ControlResult:
        if not review.target_evidence:
            return self._request_repair(candidate, review)
        revised = candidate.copy_with(
            evidence=[item.to_dict() for item in review.target_evidence],
            revision_round=candidate.revision_round + 1,
            status=CandidateStatus.PENDING_REVIEW.value,
        )
        return ControlResult(
            action=ControlAction.RELOCATE,
            candidate=revised,
            rationale=review.reason,
        )

    def _merge(
        self,
        candidate: CandidateAbility,
        review: ReviewResult,
        peers: list[CandidateAbility],
    ) -> ControlResult:
        peer_ids = [item.candidate_id for item in peers]
        duplicate_peer_ids = sorted(
            {
                candidate_id
                for candidate_id in peer_ids
                if peer_ids.count(candidate_id) > 1
            }
        )
        if duplicate_peer_ids:
            self._raise_invalid_merge(
                candidate,
                review,
                "peer candidates contain duplicate candidate_id values: "
                + ", ".join(duplicate_peer_ids),
            )
        if not review.merge_target_id:
            self._raise_invalid_merge(
                candidate, review, "merge_target_id is empty"
            )
        if review.merge_target_id == candidate.candidate_id:
            self._raise_invalid_merge(
                candidate, review, "merge_target_id points to the source candidate"
            )
        target = next(
            (
                item
                for item in peers
                if item.candidate_id == review.merge_target_id
            ),
            None,
        )
        if target is None:
            self._raise_invalid_merge(
                candidate, review, "merge_target_id does not exist in peer candidates"
            )
        if target.resume_id != candidate.resume_id:
            self._raise_invalid_merge(
                candidate, review, "merge target belongs to a different resume_id"
            )
        if candidate.candidate_id in target.lineage:
            self._raise_invalid_merge(
                candidate,
                review,
                "source candidate_id already appears in target lineage",
            )
        if target.candidate_id in candidate.lineage:
            self._raise_invalid_merge(
                candidate,
                review,
                "target candidate_id already appears in source lineage",
            )
        base_round = max(candidate.revision_round, target.revision_round)
        if base_round >= self.max_revisions:
            return self._second_failure(candidate, review.error_types)

        evidence = self._merge_evidence(target.evidence, candidate.evidence)
        lineage = self._merge_strings(
            target.lineage,
            candidate.lineage,
            [target.candidate_id, candidate.candidate_id],
        )
        target_ability = review.target_ability or target.ability
        normalized_ability = (
            review.target_ability or target.normalized_ability
        )
        merged = target.copy_with(
            fact=self._join_text(target.fact, candidate.fact),
            behavior=self._join_text(target.behavior, candidate.behavior),
            ability=target_ability,
            normalized_ability=normalized_ability,
            evidence=[item.to_dict() for item in evidence],
            reason=self._join_text(target.reason, candidate.reason),
            confidence=max(target.confidence, candidate.confidence),
            source=self._join_text(target.source, candidate.source),
            revision_round=base_round + 1,
            status=CandidateStatus.PENDING_REVIEW.value,
            lineage=lineage,
        )
        return ControlResult(
            action=ControlAction.MERGE,
            candidate=merged,
            merged_candidate_ids=(target.candidate_id, candidate.candidate_id),
            rationale=review.reason,
        )

    @staticmethod
    def _raise_invalid_merge(
        candidate: CandidateAbility,
        review: ReviewResult,
        reason: str,
    ) -> None:
        raise InvalidReviewError(
            "invalid merge review: "
            f"candidate_id={candidate.candidate_id}, "
            f"merge_target_id={review.merge_target_id!r}, "
            f"reason={reason}"
        )

    def _request_repair(
        self,
        candidate: CandidateAbility,
        review: ReviewResult,
    ) -> ControlResult:
        if candidate.revision_round >= self.max_revisions:
            return self._second_failure(candidate, review.error_types)
        if not any(
            error_type in self.allowed_repair_errors
            for error_type in review.error_types
        ):
            return self._delete(
                candidate,
                "policy does not allow repair for these error types",
            )
        return ControlResult(
            action=ControlAction.REPAIR,
            candidate=candidate.copy_with(
                status=CandidateStatus.NEEDS_REPAIR.value
            ),
            requires_repair=True,
            rationale=review.reason,
        )

    def _second_failure(
        self,
        candidate: CandidateAbility,
        error_types: Iterable[ErrorType],
    ) -> ControlResult:
        errors = set(error_types)
        if errors & self.second_failure_delete_errors:
            return self._delete(
                candidate,
                "final review failed with a non-repairable evidence/scope error",
            )
        if self.second_failure_default is ControlAction.DELETE:
            return self._delete(candidate, "final review failed")
        return self._low_confidence(candidate, "final review failed")

    def _delete(self, candidate: CandidateAbility, reason: str) -> ControlResult:
        return ControlResult(
            action=ControlAction.DELETE,
            candidate=candidate.copy_with(status=CandidateStatus.DELETED.value),
            rationale=reason,
        )

    def _low_confidence(
        self, candidate: CandidateAbility, reason: str
    ) -> ControlResult:
        return ControlResult(
            action=ControlAction.LOW_CONFIDENCE,
            candidate=candidate.copy_with(
                status=CandidateStatus.LOW_CONFIDENCE.value
            ),
            rationale=reason,
        )

    @staticmethod
    def _merge_evidence(*groups: Iterable[Evidence]) -> list[Evidence]:
        result: list[Evidence] = []
        seen: set[tuple[str, str, int | None, int | None]] = set()
        for group in groups:
            for item in group:
                key = (item.text, item.project_id, item.start, item.end)
                if key not in seen:
                    seen.add(key)
                    result.append(Evidence.from_dict(item.to_dict()))
        return result

    @staticmethod
    def _merge_strings(*groups: Iterable[str]) -> list[str]:
        result: list[str] = []
        for group in groups:
            for item in group:
                if item not in result:
                    result.append(item)
        return result

    @staticmethod
    def _join_text(left: str, right: str) -> str:
        if left == right:
            return left
        return f"{left} | {right}"
