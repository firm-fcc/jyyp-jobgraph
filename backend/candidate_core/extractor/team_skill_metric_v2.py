"""Candidate-level Team Skill concept metrics for V3.

Primary key: (candidate_id, team_skill_id).
Evidence spans and source experience IDs are evaluated separately and MUST NOT
change a concept-level TP into an FP/FN.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from extractor.team_skill_registry import TeamSkillRegistry


@dataclass(frozen=True)
class ConceptKey:
    candidate_id: str
    team_skill_id: str


@dataclass(frozen=True)
class MetricCounts:
    tp: int
    fp: int
    fn: int

    @property
    def precision(self) -> float | None:
        denominator = self.tp + self.fp
        return None if denominator == 0 else self.tp / denominator

    @property
    def recall(self) -> float | None:
        denominator = self.tp + self.fn
        return None if denominator == 0 else self.tp / denominator

    @property
    def f1(self) -> float | None:
        denominator = 2 * self.tp + self.fp + self.fn
        return None if denominator == 0 else 2 * self.tp / denominator

    def to_dict(self) -> dict[str, Any]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
        }


def _iter_records(items: Iterable[Mapping[str, Any]]) -> Iterable[Mapping[str, Any]]:
    for item in items:
        # Accept flat assessment records or V3 profile objects.
        assessments = item.get("assessments")
        if isinstance(assessments, list):
            profile_candidate = str(item.get("candidate_id", "")).strip()
            for assessment in assessments:
                if not isinstance(assessment, Mapping):
                    continue
                if "candidate_id" not in assessment and profile_candidate:
                    assessment = dict(assessment)
                    assessment["candidate_id"] = profile_candidate
                yield assessment
        else:
            yield item


def _supported_keys(
    items: Iterable[Mapping[str, Any]],
    registry: TeamSkillRegistry,
    *,
    include_auxiliary: bool,
) -> tuple[set[ConceptKey], int, Counter[str]]:
    keys: set[ConceptKey] = set()
    duplicates = 0
    raw_positive_by_skill: Counter[str] = Counter()
    for item in _iter_records(items):
        if str(item.get("status", item.get("classification_status", ""))) != "supported":
            continue
        candidate_id = str(item.get("candidate_id", "")).strip()
        skill_id = str(item.get("team_skill_id", item.get("ability_id", ""))).strip()
        if not candidate_id or not skill_id:
            raise ValueError("supported record must contain candidate_id and team_skill_id")
        skill = registry.get(skill_id)
        if not include_auxiliary and skill.metric_role != "primary_skill":
            continue
        key = ConceptKey(candidate_id, skill_id)
        raw_positive_by_skill[skill_id] += 1
        if key in keys:
            duplicates += 1
        keys.add(key)
    return keys, duplicates, raw_positive_by_skill


def _counts(gold: set[ConceptKey], pred: set[ConceptKey]) -> MetricCounts:
    return MetricCounts(
        tp=len(gold & pred),
        fp=len(pred - gold),
        fn=len(gold - pred),
    )


def evaluate_team_skill_concepts(
    gold_records: Iterable[Mapping[str, Any]],
    prediction_records: Iterable[Mapping[str, Any]],
    registry: TeamSkillRegistry,
    *,
    include_auxiliary: bool = False,
) -> dict[str, Any]:
    gold, gold_duplicates, _ = _supported_keys(
        gold_records, registry, include_auxiliary=include_auxiliary
    )
    pred, pred_duplicates, _ = _supported_keys(
        prediction_records, registry, include_auxiliary=include_auxiliary
    )
    overall = _counts(gold, pred)

    skill_ids = sorted({key.team_skill_id for key in gold | pred})
    per_skill: dict[str, Any] = {}
    eligible_f1: list[float] = []
    for skill_id in skill_ids:
        skill_gold = {key for key in gold if key.team_skill_id == skill_id}
        skill_pred = {key for key in pred if key.team_skill_id == skill_id}
        counts = _counts(skill_gold, skill_pred)
        skill = registry.get(skill_id)
        row = {
            "team_skill_name": skill.name_zh,
            "metric_role": skill.metric_role,
            "gold_supported_count": len(skill_gold),
            "prediction_supported_count": len(skill_pred),
            **counts.to_dict(),
        }
        per_skill[skill_id] = row
        if skill_gold and counts.f1 is not None:
            eligible_f1.append(counts.f1)

    macro_f1 = None if not eligible_f1 else sum(eligible_f1) / len(eligible_f1)
    return {
        "metric_version": "evaluation_metrics_spec_v2_project_defined",
        "primary_key": ["candidate_id", "team_skill_id"],
        "positive_status": "supported",
        "scope": "all_team_skills" if include_auxiliary else "primary_skill_only",
        "registry_version": registry.version,
        "overall": overall.to_dict(),
        "macro_f1": macro_f1,
        "gold_positive_count": len(gold),
        "prediction_positive_count": len(pred),
        "gold_duplicate_positive_count": gold_duplicates,
        "prediction_duplicate_positive_count": pred_duplicates,
        "evaluated_skill_count": len(skill_ids),
        "per_skill": per_skill,
        "notes": [
            "Concept matching ignores source_experience_id and evidence span by design.",
            "Evidence quality must be reported as a separate metric family.",
            "Aggregate work-quality nodes are excluded from the primary metric unless include_auxiliary=true.",
        ],
    }
