# -*- coding: utf-8 -*-
"""Deterministic JD-skillpoint-to-curated-subskill requirement resolution."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .learning_path_stage1 import SkillDevelopmentGraph, load_skill_development_graph


_RESOLUTION_STATUSES = frozenset({"MATCHED", "NO_SKILL_POINTS", "NO_GRAPH", "NO_MATCH"})


@dataclass(frozen=True)
class SubskillResolution:
    team_skill_id: str
    required_subskill_ids: tuple[str, ...]
    matched_terms: tuple[str, ...]
    resolution_status: str

    def __post_init__(self) -> None:
        if self.resolution_status not in _RESOLUTION_STATUSES:
            raise ValueError(f"invalid subskill resolution status: {self.resolution_status}")


class SubskillRequirementResolverV1:
    """Match configured exact terms only within existing curated graphs."""

    def __init__(
        self,
        *,
        graphs: Mapping[str, SkillDevelopmentGraph] | Sequence[SkillDevelopmentGraph] | None = None,
        keyword_map: Mapping[str, Any] | None = None,
        keyword_map_path: str | Path | None = None,
    ) -> None:
        if keyword_map is not None and keyword_map_path is not None:
            raise ValueError("provide keyword_map or keyword_map_path, not both")
        self._graphs = self._coerce_graphs(graphs)
        payload = keyword_map if keyword_map is not None else self._load_keyword_map(keyword_map_path)
        self._keywords = self._validate_keyword_map(payload)

    @staticmethod
    def _config_dir() -> Path:
        return Path(__file__).resolve().parent.parent / "config"

    @classmethod
    def _coerce_graphs(
        cls,
        graphs: Mapping[str, SkillDevelopmentGraph] | Sequence[SkillDevelopmentGraph] | None,
    ) -> dict[str, SkillDevelopmentGraph]:
        if graphs is None:
            loaded = [
                load_skill_development_graph(path)
                for path in sorted(cls._config_dir().glob("skill_development_graph_*.json"))
            ]
        elif isinstance(graphs, Mapping):
            loaded = list(graphs.values())
        else:
            loaded = list(graphs)
        result: dict[str, SkillDevelopmentGraph] = {}
        for graph in loaded:
            if not isinstance(graph, SkillDevelopmentGraph):
                raise ValueError("graphs must contain SkillDevelopmentGraph values")
            if graph.team_skill_id in result:
                raise ValueError(f"duplicate development graph for {graph.team_skill_id}")
            result[graph.team_skill_id] = graph
        return result

    @classmethod
    def _load_keyword_map(cls, path: str | Path | None) -> Mapping[str, Any]:
        config_path = Path(path) if path is not None else cls._config_dir() / "subskill_keyword_map_v1.json"
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("subskill keyword map must be a JSON object")
        return payload

    @staticmethod
    def _normalize_term(value: str) -> str:
        return value.strip().casefold()

    def _validate_keyword_map(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, dict[str, tuple[str, ...]]]:
        if payload.get("schema_version") != "subskill_keyword_map_v1":
            raise ValueError("unsupported subskill keyword map schema")
        raw_skills = payload.get("skills")
        if not isinstance(raw_skills, Mapping):
            raise ValueError("subskill keyword map skills must be an object")
        validated: dict[str, dict[str, tuple[str, ...]]] = {}
        for raw_team_skill_id, raw_subskills in raw_skills.items():
            team_skill_id = str(raw_team_skill_id).strip()
            graph = self._graphs.get(team_skill_id)
            if graph is None:
                raise ValueError(f"keyword map references missing development graph: {team_skill_id}")
            if not isinstance(raw_subskills, Mapping):
                raise ValueError(f"keyword map skill entry must be an object: {team_skill_id}")
            graph_node_ids = {node.subskill_id for node in graph.subskill_nodes}
            skill_terms: dict[str, tuple[str, ...]] = {}
            for raw_subskill_id, raw_terms in raw_subskills.items():
                subskill_id = str(raw_subskill_id).strip()
                if subskill_id not in graph_node_ids:
                    raise ValueError(
                        f"keyword map references missing subskill: {team_skill_id}/{subskill_id}"
                    )
                if not isinstance(raw_terms, list):
                    raise ValueError(
                        f"keyword map terms must be an array: {team_skill_id}/{subskill_id}"
                    )
                normalized_terms: list[str] = []
                for raw_term in raw_terms:
                    if not isinstance(raw_term, str) or not raw_term.strip():
                        raise ValueError(
                            f"keyword map terms must be non-empty strings: {team_skill_id}/{subskill_id}"
                        )
                    term = self._normalize_term(raw_term)
                    if term not in normalized_terms:
                        normalized_terms.append(term)
                if not normalized_terms:
                    raise ValueError(
                        f"keyword map terms must not be empty: {team_skill_id}/{subskill_id}"
                    )
                skill_terms[subskill_id] = tuple(normalized_terms)
            validated[team_skill_id] = skill_terms
        return validated

    @staticmethod
    def _normalize_skill_points(skill_points: Any) -> tuple[str, ...]:
        if not isinstance(skill_points, Sequence) or isinstance(skill_points, (str, bytes)):
            return ()
        normalized: list[str] = []
        for raw_point in skill_points:
            if not isinstance(raw_point, str):
                return ()
            point = raw_point.strip().casefold()
            if point and point not in normalized:
                normalized.append(point)
        return tuple(normalized)

    def resolve(self, team_skill_id: str, skill_points: Any) -> SubskillResolution:
        normalized_skill_id = str(team_skill_id).strip()
        normalized_skill_points = self._normalize_skill_points(skill_points)
        if not normalized_skill_points:
            return SubskillResolution(normalized_skill_id, (), (), "NO_SKILL_POINTS")
        graph = self._graphs.get(normalized_skill_id)
        if graph is None:
            return SubskillResolution(normalized_skill_id, (), (), "NO_GRAPH")
        configured = self._keywords.get(normalized_skill_id, {})
        point_set = set(normalized_skill_points)
        required_subskill_ids: list[str] = []
        matched_terms: list[str] = []
        for node in graph.subskill_nodes:
            node_matched = False
            for term in configured.get(node.subskill_id, ()):
                if term in point_set:
                    node_matched = True
                    if term not in matched_terms:
                        matched_terms.append(term)
            if node_matched:
                required_subskill_ids.append(node.subskill_id)
        status = "MATCHED" if required_subskill_ids else "NO_MATCH"
        return SubskillResolution(
            team_skill_id=normalized_skill_id,
            required_subskill_ids=tuple(required_subskill_ids),
            matched_terms=tuple(matched_terms),
            resolution_status=status,
        )


__all__ = ["SubskillRequirementResolverV1", "SubskillResolution"]
