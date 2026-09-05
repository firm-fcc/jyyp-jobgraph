"""Team Skill registry for the V3 evidence-first capability pipeline.

The registry is deliberately deterministic and contains no model/network code.
Aliases are retrieval hints only.  They must never be treated as evidence that a
candidate actually possesses a skill.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


_ALLOWED_SKILL_TYPES = {"hard", "soft", "hybrid"}
_ALLOWED_INFERENCE_MODES = {"direct_behavior", "aggregate_signal"}
_ALLOWED_METRIC_ROLES = {"primary_skill", "auxiliary_work_quality"}


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", "", value)


def _phrase_occurs(text: str, phrase: str) -> bool:
    """Match retrieval phrases without short-ASCII substring accidents.

    Chinese/mixed phrases retain the historical whitespace-insensitive matching.
    Short pure ASCII aliases (ML, CV, NLP, SFT, K8s...) must occur as tokens,
    so e.g. ``ML`` no longer fires inside ``HTML``.  This remains retrieval-only
    and does not decide skill support.
    """
    raw_text = unicodedata.normalize("NFKC", text).casefold()
    raw_phrase = unicodedata.normalize("NFKC", phrase).casefold().strip()
    if re.fullmatch(r"[a-z0-9]+", raw_phrase) and len(raw_phrase) <= 5:
        pattern = rf"(?<![a-z0-9]){re.escape(raw_phrase)}(?![a-z0-9])"
        return re.search(pattern, raw_text) is not None
    return _normalize(phrase) in _normalize(text)


@dataclass(frozen=True)
class TeamSkill:
    code: str
    name_zh: str
    name_en: str
    definition: str
    skill_type: str
    aliases: tuple[str, ...]
    inference_mode: str
    metric_role: str
    evidence_policy: str
    notes: str = ""

    def retrieval_phrases(self) -> tuple[str, ...]:
        values = [self.name_zh, self.name_en, *self.aliases]
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            value = value.strip()
            normalized = _normalize(value)
            if value and normalized not in seen:
                result.append(value)
                seen.add(normalized)
        return tuple(result)


@dataclass(frozen=True)
class RankedTeamSkill:
    skill: TeamSkill
    lexical_score: float
    semantic_score: float | None
    combined_score: float
    matched_phrases: tuple[str, ...]


class TeamSkillRegistry:
    """Load and validate the shared 49-node Team Skill vocabulary plus V3 adapter."""

    def __init__(
        self,
        skill_path: str | Path | None = None,
        adapter_path: str | Path | None = None,
    ) -> None:
        root = Path(__file__).resolve().parent.parent
        self.skill_path = Path(skill_path or root / "config" / "team_skills_v0.4.json")
        self.adapter_path = Path(adapter_path or root / "config" / "team_skill_adapter_v1.json")
        raw_skills = self._load_json(self.skill_path)
        raw_adapter = self._load_json(self.adapter_path)
        self.version = str(raw_skills.get("version", ""))
        self.system_name = str(raw_skills.get("system_name", ""))
        self.adapter_version = str(raw_adapter.get("adapter_version", ""))
        self.status = str(raw_adapter.get("status", ""))
        self._skills = self._build_skills(raw_skills, raw_adapter)
        declared_total = raw_skills.get("total")
        if declared_total is not None and int(declared_total) != len(self._skills):
            raise ValueError(
                f"team skill total mismatch: declared={declared_total}, actual={len(self._skills)}"
            )

    @staticmethod
    def _load_json(path: Path) -> Mapping[str, object]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError as exc:
            raise ValueError(f"missing team skill config: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON config: {path}") from exc
        if not isinstance(payload, Mapping):
            raise ValueError(f"config root must be an object: {path}")
        return payload

    @staticmethod
    def _build_skills(
        raw_skills: Mapping[str, object], raw_adapter: Mapping[str, object]
    ) -> dict[str, TeamSkill]:
        detail = raw_skills.get("detail")
        adapter_skills = raw_adapter.get("skills")
        if not isinstance(detail, Mapping) or not isinstance(adapter_skills, Mapping):
            raise ValueError("team skill detail and adapter skills must both be objects")
        detail_ids = set(detail)
        adapter_ids = set(adapter_skills)
        if detail_ids != adapter_ids:
            raise ValueError(
                "adapter coverage mismatch: "
                f"missing={sorted(detail_ids - adapter_ids)}, "
                f"unknown={sorted(adapter_ids - detail_ids)}"
            )
        result: dict[str, TeamSkill] = {}
        for code, raw in detail.items():
            if not isinstance(raw, Mapping):
                raise ValueError(f"team skill {code} must be an object")
            adapter = adapter_skills[code]
            if not isinstance(adapter, Mapping):
                raise ValueError(f"adapter entry {code} must be an object")
            if raw.get("code") != code:
                raise ValueError(f"team skill key/code mismatch: {code}")
            skill_type = str(raw.get("skill_type", ""))
            if skill_type not in _ALLOWED_SKILL_TYPES:
                raise ValueError(f"unsupported skill_type for {code}: {skill_type}")
            inference_mode = str(adapter.get("inference_mode", ""))
            metric_role = str(adapter.get("metric_role", ""))
            if inference_mode not in _ALLOWED_INFERENCE_MODES:
                raise ValueError(f"unsupported inference_mode for {code}: {inference_mode}")
            if metric_role not in _ALLOWED_METRIC_ROLES:
                raise ValueError(f"unsupported metric_role for {code}: {metric_role}")
            aliases = adapter.get("aliases", [])
            if not isinstance(aliases, Sequence) or isinstance(aliases, (str, bytes)):
                raise ValueError(f"aliases for {code} must be an array")
            result[code] = TeamSkill(
                code=code,
                name_zh=str(raw.get("name_zh", "")).strip(),
                name_en=str(raw.get("name_en", "")).strip(),
                definition=str(raw.get("definition", "")).strip(),
                skill_type=skill_type,
                aliases=tuple(str(item).strip() for item in aliases if str(item).strip()),
                inference_mode=inference_mode,
                metric_role=metric_role,
                evidence_policy=str(adapter.get("evidence_policy", "")).strip(),
                notes=str(adapter.get("notes", "")).strip(),
            )
        return result

    def __len__(self) -> int:
        return len(self._skills)

    def get(self, code: str) -> TeamSkill:
        try:
            return self._skills[code]
        except KeyError as exc:
            raise KeyError(f"unknown team skill id: {code}") from exc

    def all(self) -> tuple[TeamSkill, ...]:
        return tuple(self._skills.values())

    def primary(self) -> tuple[TeamSkill, ...]:
        return tuple(skill for skill in self._skills.values() if skill.metric_role == "primary_skill")

    def auxiliary(self) -> tuple[TeamSkill, ...]:
        return tuple(
            skill for skill in self._skills.values() if skill.metric_role == "auxiliary_work_quality"
        )

    def primary_ids(self) -> frozenset[str]:
        return frozenset(skill.code for skill in self.primary())

    def auxiliary_ids(self) -> frozenset[str]:
        return frozenset(skill.code for skill in self.auxiliary())

    def rank_lexically(
        self,
        text: str,
        *,
        top_k: int | None = 8,
        include_auxiliary: bool = True,
        semantic_scores: Mapping[str, float] | None = None,
    ) -> tuple[RankedTeamSkill, ...]:
        """Produce a deterministic candidate ranking.

        This is a *candidate-generation* helper, not a classifier.  Exact name/alias
        hits raise recall candidates only. Optional semantic scores can be injected
        later by an embedding model without changing this registry contract.
        """
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if top_k is not None and top_k <= 0:
            raise ValueError("top_k must be positive or None")
        normalized_text = _normalize(text)
        ranked: list[RankedTeamSkill] = []
        for skill in self._skills.values():
            if not include_auxiliary and skill.metric_role != "primary_skill":
                continue
            matches: list[str] = []
            lexical_score = 0.0
            for phrase in skill.retrieval_phrases():
                normalized_phrase = _normalize(phrase)
                if len(normalized_phrase) < 2:
                    continue
                if _phrase_occurs(text, phrase):
                    matches.append(phrase)
                    # Canonical Chinese name is strongest; aliases/English are retrieval hints.
                    lexical_score += 8.0 if phrase == skill.name_zh else 4.0
                    lexical_score += min(len(normalized_phrase), 12) / 12.0
            semantic_score: float | None = None
            if semantic_scores is not None and skill.code in semantic_scores:
                semantic_score = float(semantic_scores[skill.code])
                if not 0.0 <= semantic_score <= 1.0:
                    raise ValueError(f"semantic score for {skill.code} must be in [0,1]")
            combined = lexical_score + (5.0 * semantic_score if semantic_score is not None else 0.0)
            if combined > 0:
                ranked.append(
                    RankedTeamSkill(
                        skill=skill,
                        lexical_score=lexical_score,
                        semantic_score=semantic_score,
                        combined_score=combined,
                        matched_phrases=tuple(matches),
                    )
                )
        ranked.sort(key=lambda item: (-item.combined_score, item.skill.code))
        return tuple(ranked if top_k is None else ranked[:top_k])

    def validate_skill_ids(self, values: Iterable[str]) -> None:
        unknown = sorted({value for value in values if value not in self._skills})
        if unknown:
            raise ValueError(f"unknown team skill ids: {unknown}")
