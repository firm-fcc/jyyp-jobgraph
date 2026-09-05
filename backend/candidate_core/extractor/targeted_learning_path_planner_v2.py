"""Targeted deterministic learning-path selection built on frozen Stage1 semantics."""

from __future__ import annotations

from typing import Mapping, Sequence

from .learning_path_stage1 import (
    DeterministicPathPlanner,
    DevelopmentNode,
    GapItem,
    LearningPathEngine,
    SkillDevelopmentGraph,
)


class TargetedDeterministicPathPlannerV2(DeterministicPathPlanner):
    """Select only explicit targets and their prerequisite closure when available."""

    @staticmethod
    def _select_nodes(
        graph: SkillDevelopmentGraph,
        item: GapItem,
    ) -> tuple[DevelopmentNode, ...]:
        if not item.required_subskill_ids:
            return DeterministicPathPlanner._select_nodes(graph, item)

        nodes = graph.topological_nodes()
        by_id = {node.subskill_id: node for node in nodes}
        invalid_ids = [
            subskill_id
            for subskill_id in item.required_subskill_ids
            if subskill_id not in by_id
        ]
        if invalid_ids:
            raise ValueError(
                f"required subskill is not present in graph: {invalid_ids[0]}"
            )

        selected_ids = set(item.required_subskill_ids)
        pending = list(selected_ids)
        while pending:
            node = by_id[pending.pop()]
            for prerequisite in node.prerequisites:
                if prerequisite not in selected_ids:
                    selected_ids.add(prerequisite)
                    pending.append(prerequisite)

        return tuple(node for node in nodes if node.subskill_id in selected_ids)


class LearningPathEngineV2(LearningPathEngine):
    """Stage1 engine with only its deterministic node selector replaced."""

    def __init__(self, graphs: Sequence[SkillDevelopmentGraph]) -> None:
        graph_tuple = tuple(graphs)
        super().__init__(graph_tuple)
        graph_by_skill: Mapping[str, SkillDevelopmentGraph] = {
            graph.team_skill_id: graph for graph in graph_tuple
        }
        self._planner = TargetedDeterministicPathPlannerV2(graph_by_skill)


__all__ = (
    "LearningPathEngineV2",
    "TargetedDeterministicPathPlannerV2",
)
