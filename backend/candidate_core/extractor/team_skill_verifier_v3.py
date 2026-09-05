"""Constrained LLM verifier for grounded Evidence -> Team Skill linking.

The verifier only judges caller-provided Team Skill candidates. It never invents
Team Skill IDs. Only located resume evidence (with start/end offsets) is exposed
as support material. A deterministic auditor validates the result afterwards.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from extractor.agentic_llm_client import LLMCompletion
from extractor.agentic_schema import CandidateAbility
from extractor.team_skill_registry import TeamSkill


_ALLOWED_STATUS = {"supported", "partially_supported", "unsupported"}
_CONTRACT_RETRY_INSTRUCTION = """
上一次输出违反了严格 JSON 合同。请重新输出，并严格遵守以下要求：
- 只输出一个 JSON 对象，不要 Markdown 或额外文字；
- assessments 必须与输入 team_skill_id 一一对应，不得新增、遗漏、重复；
- 不得输出 NaN/Infinity，不得出现重复 JSON key；
- unsupported 的 support_evidence 必须为 [] 且 atomic_ability 必须为 null。
""".strip()


class TeamSkillVerifierError(RuntimeError):
    pass


class TeamSkillVerifierContractError(TeamSkillVerifierError):
    pass


class CompletionClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> LLMCompletion:
        ...


@dataclass(frozen=True)
class ModelTeamSkillAssessment:
    team_skill_id: str
    status: str
    support_evidence: tuple[str, ...]
    reason: str
    confidence: float
    atomic_ability: str | None


@dataclass(frozen=True)
class TeamSkillVerificationResult:
    candidate_id: str
    source_candidate_ability_id: str
    assessments: tuple[ModelTeamSkillAssessment, ...]
    model: str
    elapsed_ms: float
    usage: dict[str, Any] | None
    contract_retry_count: int = 0


class EvidenceSkillVerifierV3:
    def __init__(
        self,
        client: CompletionClient,
        prompt_path: str | Path | None = None,
    ) -> None:
        self.client = client
        root = Path(__file__).resolve().parent.parent
        self.prompt_path = Path(prompt_path or root / "config" / "team_skill_verifier_v3.txt")
        self.system_prompt = self.prompt_path.read_text(encoding="utf-8-sig").strip()
        if not self.system_prompt:
            raise ValueError("team skill verifier prompt must not be empty")

    def _complete_json(self, system_prompt: str, user_prompt: str) -> LLMCompletion:
        complete_json = getattr(self.client, "complete_json", None)
        if callable(complete_json):
            return complete_json(system_prompt, user_prompt, max_tokens=16384)
        return self.client.complete(system_prompt, user_prompt)

    def verify(
        self,
        *,
        candidate_id: str,
        evidence_candidate: CandidateAbility,
        candidate_skills: Sequence[TeamSkill],
    ) -> TeamSkillVerificationResult:
        if not candidate_id.strip():
            raise ValueError("candidate_id must be non-empty")
        if not candidate_skills:
            raise ValueError("candidate_skills must be non-empty")
        ids = [skill.code for skill in candidate_skills]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate_skills contains duplicate ids")

        located_evidence = [
            item for item in evidence_candidate.evidence
            if item.start is not None and item.end is not None
        ]
        if not located_evidence:
            raise ValueError("evidence_candidate has no located resume evidence")

        user_payload = {
            "candidate_id": candidate_id,
            "source_candidate_ability_id": evidence_candidate.candidate_id,
            "source_experience_id": evidence_candidate.project_id,
            "evidence_unit": {
                "fact": evidence_candidate.fact,
                "behavior": evidence_candidate.behavior,
                "ability_hint": evidence_candidate.ability,
                "grounded_evidence": [
                    {
                        "text": item.text,
                        "start": item.start,
                        "end": item.end,
                        "source_experience_id": evidence_candidate.project_id,
                    }
                    for item in located_evidence
                ],
            },
            "candidate_skills": [
                {
                    "team_skill_id": skill.code,
                    "name_zh": skill.name_zh,
                    "definition": skill.definition,
                    "inference_mode": skill.inference_mode,
                }
                for skill in candidate_skills
            ],
        }
        encoded = json.dumps(
            user_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )

        completion = self._complete_json(self.system_prompt, encoded)
        if not isinstance(completion, LLMCompletion):
            raise TeamSkillVerifierError("client must return LLMCompletion")
        contract_retry_count = 0
        try:
            assessments = self._parse(completion.content, ids)
        except TeamSkillVerifierContractError:
            contract_retry_count = 1
            completion = self._complete_json(
                self.system_prompt + "\n\n" + _CONTRACT_RETRY_INSTRUCTION,
                encoded,
            )
            if not isinstance(completion, LLMCompletion):
                raise TeamSkillVerifierError("client must return LLMCompletion")
            assessments = self._parse(completion.content, ids)

        return TeamSkillVerificationResult(
            candidate_id=candidate_id,
            source_candidate_ability_id=evidence_candidate.candidate_id,
            assessments=assessments,
            model=completion.model,
            elapsed_ms=completion.elapsed_ms,
            usage=None if completion.usage is None else dict(completion.usage),
            contract_retry_count=contract_retry_count,
        )

    @classmethod
    def _parse(
        cls,
        content: str,
        expected_ids: Sequence[str],
    ) -> tuple[ModelTeamSkillAssessment, ...]:
        if not isinstance(content, str) or not content.strip():
            raise TeamSkillVerifierContractError("model output must be non-empty JSON text")
        try:
            payload = json.loads(
                content.strip(),
                object_pairs_hook=cls._reject_duplicate_keys,
                parse_constant=cls._reject_json_constant,
            )
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise TeamSkillVerifierContractError("model output must be one strict JSON object") from exc
        if not isinstance(payload, Mapping) or set(payload) != {"assessments"}:
            raise TeamSkillVerifierContractError("root must contain only assessments")
        raw = payload["assessments"]
        if not isinstance(raw, list):
            raise TeamSkillVerifierContractError("assessments must be an array")
        parsed: list[ModelTeamSkillAssessment] = []
        seen: set[str] = set()
        expected = set(expected_ids)
        required_fields = {
            "team_skill_id",
            "status",
            "support_evidence",
            "reason",
            "confidence",
            "atomic_ability",
        }
        for index, item in enumerate(raw):
            if not isinstance(item, Mapping) or set(item) != required_fields:
                raise TeamSkillVerifierContractError(f"assessment[{index}] fields are invalid")
            skill_id = str(item["team_skill_id"]).strip()
            if skill_id not in expected or skill_id in seen:
                raise TeamSkillVerifierContractError(f"invalid/duplicate team_skill_id: {skill_id}")
            status = str(item["status"]).strip()
            if status not in _ALLOWED_STATUS:
                raise TeamSkillVerifierContractError(f"invalid status for {skill_id}: {status}")
            support = item["support_evidence"]
            if not isinstance(support, list) or not all(
                isinstance(value, str) and value.strip() for value in support
            ):
                raise TeamSkillVerifierContractError(
                    f"support_evidence for {skill_id} must be non-empty strings"
                )
            if status == "unsupported" and support:
                raise TeamSkillVerifierContractError(
                    f"unsupported {skill_id} must have empty support_evidence"
                )
            reason = str(item["reason"]).strip()
            if not reason:
                raise TeamSkillVerifierContractError(f"reason for {skill_id} must be non-empty")
            confidence = item["confidence"]
            if (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not math.isfinite(float(confidence))
                or not 0 <= float(confidence) <= 1
            ):
                raise TeamSkillVerifierContractError(f"confidence for {skill_id} must be finite in [0,1]")
            atomic = item["atomic_ability"]
            if atomic is not None:
                if not isinstance(atomic, str) or not atomic.strip():
                    raise TeamSkillVerifierContractError(
                        f"atomic_ability for {skill_id} must be null or text"
                    )
                atomic = atomic.strip()
            if status == "unsupported" and atomic is not None:
                raise TeamSkillVerifierContractError(
                    f"unsupported {skill_id} must have null atomic_ability"
                )
            parsed.append(
                ModelTeamSkillAssessment(
                    team_skill_id=skill_id,
                    status=status,
                    support_evidence=tuple(value.strip() for value in support),
                    reason=reason,
                    confidence=float(confidence),
                    atomic_ability=atomic,
                )
            )
            seen.add(skill_id)
        if seen != expected:
            raise TeamSkillVerifierContractError(
                f"assessment ids mismatch: missing={sorted(expected-seen)}, extra={sorted(seen-expected)}"
            )
        order = {skill_id: index for index, skill_id in enumerate(expected_ids)}
        parsed.sort(key=lambda item: order[item.team_skill_id])
        return tuple(parsed)

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
