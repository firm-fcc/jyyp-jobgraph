"""Low-cost constrained selector for lexical-retrieval fallback in V3.

This component is *retrieval only*.  It receives grounded evidence candidates and
an existing Team Skill registry, then returns a small shortlist of Team Skill IDs
for each evidence candidate.  It cannot assert that a skill is supported; the
normal EvidenceSkillVerifierV3 plus deterministic auditor remain the only path to
positive Team Skill results.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from extractor.agentic_llm_client import LLMCompletion
from extractor.agentic_schema import CandidateAbility
from extractor.team_skill_registry import TeamSkill


class FallbackSelectorError(RuntimeError):
    pass


class FallbackSelectorContractError(FallbackSelectorError):
    pass


class CompletionClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> LLMCompletion:
        ...


@dataclass(frozen=True)
class FallbackSkillSelection:
    source_candidate_ability_id: str
    team_skill_ids: tuple[str, ...]


@dataclass(frozen=True)
class FallbackSelectionResult:
    selections: tuple[FallbackSkillSelection, ...]
    model: str
    elapsed_ms: float
    usage: dict[str, Any] | None
    contract_retry_count: int = 0


_CONTRACT_RETRY_INSTRUCTION = """
上一次输出违反严格 JSON 合同。请重新输出：只输出一个 JSON 对象；
selections 必须与输入 evidence_units 一一对应；不得新增/遗漏/重复 source_candidate_ability_id；
team_skill_ids 只能来自输入 candidate_skills，不能重复，且每个 evidence unit 返回 1 到 max_candidates 个 ID。
""".strip()


class FallbackTeamSkillSelectorV3:
    """Select a small semantic shortlist when deterministic retrieval has no hit."""

    def __init__(self, client: CompletionClient, prompt_path: str | Path | None = None) -> None:
        self.client = client
        root = Path(__file__).resolve().parent.parent
        self.prompt_path = Path(
            prompt_path or root / "config" / "team_skill_fallback_selector_v3.txt"
        )
        self.system_prompt = self.prompt_path.read_text(encoding="utf-8-sig").strip()
        if not self.system_prompt:
            raise ValueError("fallback selector prompt must not be empty")

    def _complete_json(self, system_prompt: str, user_prompt: str) -> LLMCompletion:
        complete_json = getattr(self.client, "complete_json", None)
        if callable(complete_json):
            return complete_json(system_prompt, user_prompt, max_tokens=4096)
        return self.client.complete(system_prompt, user_prompt)

    def select(
        self,
        *,
        candidate_id: str,
        evidence_candidates: Sequence[CandidateAbility],
        candidate_skills: Sequence[TeamSkill],
        max_candidates: int = 6,
    ) -> FallbackSelectionResult:
        if not candidate_id.strip():
            raise ValueError("candidate_id must be non-empty")
        if not evidence_candidates:
            raise ValueError("evidence_candidates must be non-empty")
        if not candidate_skills:
            raise ValueError("candidate_skills must be non-empty")
        if max_candidates <= 0:
            raise ValueError("max_candidates must be positive")

        source_ids = [item.candidate_id for item in evidence_candidates]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("evidence_candidates contains duplicate candidate IDs")
        skill_ids = [item.code for item in candidate_skills]
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("candidate_skills contains duplicate IDs")

        units = []
        for item in evidence_candidates:
            grounded = [
                evidence
                for evidence in item.evidence
                if evidence.start is not None and evidence.end is not None
            ]
            if not grounded:
                raise ValueError(
                    f"fallback evidence candidate has no grounded evidence: {item.candidate_id}"
                )
            units.append(
                {
                    "source_candidate_ability_id": item.candidate_id,
                    "fact_hint": item.fact,
                    "behavior_hint": item.behavior,
                    "ability_hint": item.ability,
                    "grounded_evidence": [
                        {"text": e.text, "start": e.start, "end": e.end}
                        for e in grounded
                    ],
                }
            )

        payload = {
            "candidate_id": candidate_id,
            "max_candidates": min(max_candidates, len(candidate_skills)),
            "evidence_units": units,
            "candidate_skills": [
                {
                    "team_skill_id": skill.code,
                    "name_zh": skill.name_zh,
                    "definition": skill.definition,
                }
                for skill in candidate_skills
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        completion = self._complete_json(self.system_prompt, encoded)
        if not isinstance(completion, LLMCompletion):
            raise FallbackSelectorError("client must return LLMCompletion")

        retries = 0
        try:
            selections = self._parse(
                completion.content,
                expected_source_ids=source_ids,
                allowed_skill_ids=skill_ids,
                max_candidates=payload["max_candidates"],
            )
        except FallbackSelectorContractError:
            retries = 1
            completion = self._complete_json(
                self.system_prompt + "\n\n" + _CONTRACT_RETRY_INSTRUCTION,
                encoded,
            )
            if not isinstance(completion, LLMCompletion):
                raise FallbackSelectorError("client must return LLMCompletion")
            selections = self._parse(
                completion.content,
                expected_source_ids=source_ids,
                allowed_skill_ids=skill_ids,
                max_candidates=payload["max_candidates"],
            )

        return FallbackSelectionResult(
            selections=selections,
            model=completion.model,
            elapsed_ms=completion.elapsed_ms,
            usage=None if completion.usage is None else dict(completion.usage),
            contract_retry_count=retries,
        )

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
    def _parse(
        cls,
        content: str,
        *,
        expected_source_ids: Sequence[str],
        allowed_skill_ids: Sequence[str],
        max_candidates: int,
    ) -> tuple[FallbackSkillSelection, ...]:
        if not isinstance(content, str) or not content.strip():
            raise FallbackSelectorContractError("model output must be non-empty JSON")
        try:
            payload = json.loads(
                content.strip(),
                object_pairs_hook=cls._reject_duplicate_keys,
                parse_constant=cls._reject_constant,
            )
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise FallbackSelectorContractError("model output must be one strict JSON object") from exc
        if not isinstance(payload, Mapping) or set(payload) != {"selections"}:
            raise FallbackSelectorContractError("root must contain only selections")
        raw = payload["selections"]
        if not isinstance(raw, list):
            raise FallbackSelectorContractError("selections must be an array")

        expected = set(expected_source_ids)
        allowed = set(allowed_skill_ids)
        seen: set[str] = set()
        parsed: list[FallbackSkillSelection] = []
        for index, item in enumerate(raw):
            if not isinstance(item, Mapping) or set(item) != {
                "source_candidate_ability_id",
                "team_skill_ids",
            }:
                raise FallbackSelectorContractError(f"selections[{index}] fields are invalid")
            source_id = str(item["source_candidate_ability_id"]).strip()
            if source_id not in expected or source_id in seen:
                raise FallbackSelectorContractError(f"invalid/duplicate source ID: {source_id}")
            values = item["team_skill_ids"]
            if not isinstance(values, list) or not 1 <= len(values) <= max_candidates:
                raise FallbackSelectorContractError(
                    f"team_skill_ids for {source_id} must contain 1..{max_candidates} IDs"
                )
            normalized = tuple(str(value).strip() for value in values)
            if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
                raise FallbackSelectorContractError(f"invalid/duplicate team_skill_ids for {source_id}")
            unknown = set(normalized) - allowed
            if unknown:
                raise FallbackSelectorContractError(
                    f"unknown team_skill_ids for {source_id}: {sorted(unknown)}"
                )
            parsed.append(FallbackSkillSelection(source_id, normalized))
            seen.add(source_id)

        if seen != expected:
            raise FallbackSelectorContractError(
                f"selection source IDs mismatch: missing={sorted(expected-seen)}, extra={sorted(seen-expected)}"
            )
        order = {source_id: index for index, source_id in enumerate(expected_source_ids)}
        parsed.sort(key=lambda item: order[item.source_candidate_ability_id])
        return tuple(parsed)
