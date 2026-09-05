"""Data-exposure registry for V3 development and holdout protection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class V3DataSplitRegistry:
    version: str
    exposed_ids: frozenset[str]
    legacy_blind_ids: frozenset[str]
    pilot_ids: frozenset[str]
    holdout_ids: frozenset[str]
    smoke_ids: tuple[str, ...]
    holdout_category_counts: Mapping[str, int]
    final_blind_missing_categories: tuple[str, ...]

    @classmethod
    def load(cls, path: str | Path | None = None) -> "V3DataSplitRegistry":
        if path is None:
            path = Path(__file__).resolve().parent.parent / "config" / "v3_data_split_registry.json"
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            version=str(payload["schema_version"]),
            exposed_ids=frozenset(payload["development_exposed_ids"]),
            legacy_blind_ids=frozenset(payload["legacy_blind_development_ids"]),
            pilot_ids=frozenset(payload["legacy_pilot_development_ids"]),
            holdout_ids=frozenset(payload["holdout_unexposed_ids"]),
            smoke_ids=tuple(payload["recommended_smoke_ids"]),
            holdout_category_counts=dict(payload["holdout_category_counts"]),
            final_blind_missing_categories=tuple(payload["final_blind_missing_categories"]),
        )

    def split_for(self, candidate_id: str) -> str:
        if candidate_id in self.holdout_ids:
            return "holdout_unexposed"
        if candidate_id in self.legacy_blind_ids:
            return "development_legacy_blind"
        if candidate_id in self.pilot_ids:
            return "development_legacy_pilot"
        return "unknown"

    def is_holdout(self, candidate_id: str) -> bool:
        return candidate_id in self.holdout_ids
