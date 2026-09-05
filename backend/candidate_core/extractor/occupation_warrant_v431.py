"""Deterministic occupation-warrant support for Evidence Construct v2.

This module never calls an LLM. It only activates pre-frozen occupation warrants
from ``config/evidence_warrant_registry_v0.1.json`` when the signal appears in
an actual Work Experience section. Direct evidence always takes precedence.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Mapping, Sequence

from extractor.team_skill_registry import TeamSkillRegistry
from extractor.team_skill_schema_v3 import (
    CandidateSkillProfile,
    EvidenceObservation,
    TeamSkillAssessment,
)


_HEADINGS = (
    "求职意向", "教育经历", "工作经历", "项目经历", "期刊论文", "会议论文",
    "授权专利", "荣誉奖项", "校园经历", "资格证书", "专业技能",
)


@dataclass(frozen=True)
class ActivatedOccupationWarrant:
    warrant_id: str
    target_team_skill_id: str
    signal_text: str
    start: int
    end: int
    source_section: str = "工作经历"
    applied_to_profile: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "warrant_id": self.warrant_id,
            "signal_type": "occupation",
            "target_team_skill_id": self.target_team_skill_id,
            "signal_text": self.signal_text,
            "start": self.start,
            "end": self.end,
            "source_section": self.source_section,
            "applied_to_profile": self.applied_to_profile,
        }


def _section_ranges(text: str) -> tuple[tuple[int, int, str], ...]:
    marks: list[tuple[int, str]] = []
    for heading in _HEADINGS:
        for match in re.finditer(re.escape(heading), text):
            if match.start() != 0 and text[match.start() - 1] != "\n":
                continue
            if match.end() != len(text) and text[match.end()] != "\n":
                continue
            marks.append((match.start(), heading))
    marks = sorted(set(marks))
    ranges: list[tuple[int, int, str]] = []
    if not marks or marks[0][0] > 0:
        ranges.append((0, marks[0][0] if marks else len(text), "简介/优势"))
    for index, (position, heading) in enumerate(marks):
        start = position + len(heading)
        if start < len(text) and text[start] == "\n":
            start += 1
        end = marks[index + 1][0] if index + 1 < len(marks) else len(text)
        ranges.append((start, end, heading))
    return tuple(ranges)


def _work_ranges(text: str) -> tuple[tuple[int, int], ...]:
    return tuple((start, end) for start, end, section in _section_ranges(text) if section == "工作经历")


def _load_registry(path: str | Path | None = None) -> Mapping[str, object]:
    root = Path(__file__).resolve().parent.parent
    registry_path = Path(path or root / "config" / "evidence_warrant_registry_v0.1.json")
    payload = json.loads(registry_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping) or not isinstance(payload.get("warrants"), list):
        raise ValueError("invalid warrant registry")
    return payload


def _literal_patterns(warrant: Mapping[str, object]) -> tuple[str, ...]:
    """Return conservative literal aliases only.

    Qualified pseudo-patterns such as ``算法工程师（明确AI/机器学习岗位语境）``
    are intentionally not weakened to ``算法工程师``. The broad form is too
    ambiguous for automatic positive support.
    """
    raw = warrant.get("signal_pattern", [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    values: list[str] = []
    for item in raw:
        value = str(item).strip()
        if not value or "（" in value or "(" in value:
            continue
        if value not in values:
            values.append(value)
    return tuple(values)


def resolve_occupation_warrants_v431(
    *,
    candidate_id: str,
    resume_text: str,
    profile: CandidateSkillProfile,
    team_skill_registry: TeamSkillRegistry,
    warrant_registry_path: str | Path | None = None,
) -> tuple[CandidateSkillProfile, tuple[ActivatedOccupationWarrant, ...]]:
    """Apply eligible A/B occupation warrants to a candidate-level profile.

    Preconditions are deliberately strict:
    - signal must appear in ``工作经历``;
    - only registry entries with ``eligible_supported_warrant`` and grade A/B;
    - only conservative literal aliases are matched;
    - direct ``supported`` Team Skill results are never replaced by a warrant.
    """
    payload = _load_registry(warrant_registry_path)
    work_ranges = _work_ranges(resume_text)
    if not work_ranges:
        return profile, ()

    current = {item.team_skill_id: item for item in profile.assessments}
    activations: list[ActivatedOccupationWarrant] = []

    for raw in payload["warrants"]:
        if not isinstance(raw, Mapping):
            continue
        if raw.get("signal_type") != "occupation":
            continue
        if raw.get("decision_effect") != "eligible_supported_warrant":
            continue
        if str(raw.get("grade", "")) not in {"A", "B"}:
            continue
        target_id = str(raw.get("target_team_skill_id") or "").strip()
        warrant_id = str(raw.get("warrant_id") or "").strip()
        if not target_id or not warrant_id:
            continue
        team_skill_registry.get(target_id)  # validate frozen target

        found: tuple[str, int, int] | None = None
        for pattern in _literal_patterns(raw):
            for left, right in work_ranges:
                local = resume_text.find(pattern, left, right)
                if local >= 0:
                    found = (resume_text[local:local + len(pattern)], local, local + len(pattern))
                    break
            if found:
                break
        if not found:
            continue

        signal, start, end = found
        existing = current.get(target_id)
        direct_precedence = existing is not None and existing.status == "supported"
        activations.append(
            ActivatedOccupationWarrant(
                warrant_id=warrant_id,
                target_team_skill_id=target_id,
                signal_text=signal,
                start=start,
                end=end,
                applied_to_profile=not direct_precedence,
            )
        )
        if direct_precedence:
            continue

        skill = team_skill_registry.get(target_id)
        observation = EvidenceObservation(
            text=signal,
            source_experience_id="occupation_warrant",
            start=start,
            end=end,
            fact="",
            behavior="",
            context="工作经历",
            result="",
        )
        current[target_id] = TeamSkillAssessment(
            candidate_id=candidate_id,
            team_skill_id=target_id,
            team_skill_name=skill.name_zh,
            status="supported",
            inference_mode=skill.inference_mode,
            evidence=(observation,),
            reason=(
                f"实际任职「{signal}」命中冻结职业Warrant {warrant_id}，"
                f"仅支持【{skill.name_zh}】的最小核心范围。"
            ),
            confidence=None,
            atomic_abilities=(),
            audit_flags=("supported_warrant", f"warrant:{warrant_id}"),
        )

    ordered = tuple(current[key] for key in sorted(current))
    metadata = {
        **dict(profile.metadata),
        "evidence_construct_version": "v2",
        "warrant_registry_version": str(payload.get("registry_version", "0.1")),
        "warrant_precedence": "supported_direct_over_supported_warrant",
    }
    return CandidateSkillProfile(
        candidate_id=profile.candidate_id,
        skill_registry_version=profile.skill_registry_version,
        assessments=ordered,
        metadata=metadata,
    ), tuple(activations)
