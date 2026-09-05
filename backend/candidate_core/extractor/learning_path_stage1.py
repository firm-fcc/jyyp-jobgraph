"""Deterministic learning-path protocol through Stage 2B-2 curated graphs.

This module is intentionally independent from the frozen candidate and JD
pipelines.  It performs no model or network calls and does not infer mastery of
development-graph nodes from a broad Team Skill proficiency level.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence


AUXILIARY_TEAM_SKILL_IDS = frozenset(
    {"F-1-01", "F-1-03", "F-1-04", "F-3-04", "F-4-01", "F-4-02"}
)
PROFICIENCY_LEVELS = frozenset({"P1", "P2", "P3", "P4", "U"})
REQUIRED_PROFICIENCY_LEVELS = frozenset({"P1", "P2", "P3", "P4"})
_PROFICIENCY_ORDER = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}
_REQUIREMENT_TYPE_ORDER = {"core": 0, "preferred": 1}
_ACTIONABLE_GAP_ORDER = {
    "MISSING": 0,
    "LEVEL_GAP": 1,
    "EVIDENCE_INSUFFICIENT": 2,
}
_GRAPH_SELECTION_PROTOCOLS = frozenset({"core_plus_required_v1", "legacy_all_nodes"})
_CAPSTONE_PURPOSE = "generate_behavioral_evidence_for_reassessment"
_VERIFICATION_PURPOSE = "generate_behavioral_evidence_for_current_proficiency_assessment"


class GapType(str, Enum):
    MISSING = "MISSING"
    LEVEL_GAP = "LEVEL_GAP"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
    SATISFIED = "SATISFIED"


class PathMode(str, Enum):
    LEARN = "LEARN"
    DEEPEN = "DEEPEN"
    VERIFY_FIRST = "VERIFY_FIRST"
    NONE = "NONE"


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")


def _reject_auxiliary(team_skill_id: str) -> None:
    if team_skill_id in AUXILIARY_TEAM_SKILL_IDS:
        raise ValueError(f"auxiliary Team Skill is outside the primary path engine: {team_skill_id}")


def _normalized_capability(value: str) -> str:
    return " ".join(value.casefold().split())


@dataclass(frozen=True)
class GroundedEvidence:
    text: str
    source_id: str
    start: int | None = None
    end: int | None = None

    def __post_init__(self) -> None:
        _require_text(self.text, "evidence text")
        _require_text(self.source_id, "evidence source_id")
        if (self.start is None) != (self.end is None):
            raise ValueError("evidence start/end must both be present or both be None")
        if self.start is not None and (self.start < 0 or self.end is None or self.end < self.start):
            raise ValueError("invalid evidence span")

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_ref": self.reference_id,
            "text": self.text,
            "source_id": self.source_id,
            "start": self.start,
            "end": self.end,
        }

    @property
    def reference_id(self) -> str:
        """Stable reference used by explicit subskill-achievement mappings."""
        if self.start is not None and self.end is not None:
            return f"{self.source_id}:{self.start}:{self.end}"
        digest = hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]
        return f"{self.source_id}:text:{digest}"


@dataclass(frozen=True)
class AchievedSubskill:
    """Explicit, evidence-grounded node achievement; never inferred semantically."""

    subskill_id: str
    evidence_refs: tuple[str, ...]
    mapping_basis: str

    def __post_init__(self) -> None:
        _require_text(self.subskill_id, "achieved subskill_id")
        if self.mapping_basis != "direct_behavior":
            raise ValueError("achieved subskill mapping_basis must be direct_behavior")
        references = tuple(value.strip() for value in self.evidence_refs if value.strip())
        if not references:
            raise ValueError("achieved subskill evidence_refs must be non-empty")
        if len(references) != len(set(references)):
            raise ValueError("achieved subskill evidence_refs must be unique")
        object.__setattr__(self, "evidence_refs", references)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subskill_id": self.subskill_id,
            "evidence_refs": list(self.evidence_refs),
            "mapping_basis": self.mapping_basis,
        }


@dataclass(frozen=True)
class ObservedTeamSkill:
    """A supported Team Skill plus its observed, evidence-bounded scope.

    observed_proficiency is a separate observation.  It never implies that all
    subskills represented by a broad Team Skill have been mastered.
    """

    team_skill_id: str
    team_skill_name: str
    evidence: tuple[GroundedEvidence, ...]
    observed_capabilities: tuple[str, ...]
    observed_proficiency: str | None = None
    achieved_subskills: tuple[AchievedSubskill, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_text(self.team_skill_id, "team_skill_id")
        _require_text(self.team_skill_name, "team_skill_name")
        _reject_auxiliary(self.team_skill_id)
        if not self.evidence:
            raise ValueError("supported Team Skill must have grounded evidence")
        capabilities = tuple(value.strip() for value in self.observed_capabilities if value.strip())
        if not capabilities:
            raise ValueError("supported Team Skill must describe its observed capability scope")
        if len({_normalized_capability(value) for value in capabilities}) != len(capabilities):
            raise ValueError("observed_capabilities must be unique")
        if self.observed_proficiency is not None and self.observed_proficiency not in PROFICIENCY_LEVELS:
            raise ValueError(f"invalid observed proficiency: {self.observed_proficiency}")
        achieved_ids = [item.subskill_id for item in self.achieved_subskills]
        if len(achieved_ids) != len(set(achieved_ids)):
            raise ValueError("achieved_subskills must be unique by subskill_id")
        valid_refs = {item.reference_id for item in self.evidence}
        for achieved in self.achieved_subskills:
            invalid_refs = [value for value in achieved.evidence_refs if value not in valid_refs]
            if invalid_refs:
                raise ValueError(
                    f"achieved subskill evidence_ref is not grounded in this Team Skill: {invalid_refs[0]}"
                )
        object.__setattr__(self, "observed_capabilities", capabilities)

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_skill_id": self.team_skill_id,
            "team_skill_name": self.team_skill_name,
            "evidence": [item.to_dict() for item in self.evidence],
            "observed_capabilities": list(self.observed_capabilities),
            "observed_proficiency": self.observed_proficiency,
            "achieved_subskills": [item.to_dict() for item in self.achieved_subskills],
        }


@dataclass(frozen=True)
class ExplicitSkillMention:
    """Source-grounded exposure only; this contract has no mastery field."""

    text: str
    evidence: GroundedEvidence
    team_skill_id: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.text, "explicit mention text")
        if self.team_skill_id is not None:
            _require_text(self.team_skill_id, "explicit mention team_skill_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "team_skill_id": self.team_skill_id,
            "evidence": self.evidence.to_dict(),
            "semantic_role": "exposure_only",
        }


@dataclass(frozen=True)
class CandidateLearningProfile:
    candidate_id: str
    supported_team_skills: tuple[ObservedTeamSkill, ...] = field(default_factory=tuple)
    explicit_mentions: tuple[ExplicitSkillMention, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_text(self.candidate_id, "candidate_id")
        skill_ids = [item.team_skill_id for item in self.supported_team_skills]
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("supported Team Skills must be unique by team_skill_id")

    @property
    def supported_by_id(self) -> Mapping[str, ObservedTeamSkill]:
        return {item.team_skill_id: item for item in self.supported_team_skills}

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "supported_team_skills": [item.to_dict() for item in self.supported_team_skills],
            "explicit_mentions": [item.to_dict() for item in self.explicit_mentions],
        }


@dataclass(frozen=True)
class JobSkillRequirement:
    team_skill_id: str
    requirement_type: str
    required_level: str | None
    requirement_evidence: tuple[str, ...]
    required_capabilities: tuple[str, ...] = field(default_factory=tuple)
    market_trend_rank: int | None = None
    required_subskill_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_text(self.team_skill_id, "team_skill_id")
        _reject_auxiliary(self.team_skill_id)
        if self.requirement_type not in _REQUIREMENT_TYPE_ORDER:
            raise ValueError(f"invalid requirement_type: {self.requirement_type}")
        if self.required_level is not None and self.required_level not in REQUIRED_PROFICIENCY_LEVELS:
            raise ValueError(f"invalid required_level: {self.required_level}")
        evidence = tuple(value.strip() for value in self.requirement_evidence if value.strip())
        if not evidence:
            raise ValueError("requirement_evidence must contain at least one non-empty item")
        capabilities = tuple(value.strip() for value in self.required_capabilities if value.strip())
        if len({_normalized_capability(value) for value in capabilities}) != len(capabilities):
            raise ValueError("required_capabilities must be unique")
        if self.market_trend_rank is not None and self.market_trend_rank < 0:
            raise ValueError("market_trend_rank must be non-negative")
        required_subskill_ids = tuple(value.strip() for value in self.required_subskill_ids if value.strip())
        if len(required_subskill_ids) != len(set(required_subskill_ids)):
            raise ValueError("required_subskill_ids must be unique")
        object.__setattr__(self, "requirement_evidence", evidence)
        object.__setattr__(self, "required_capabilities", capabilities)
        object.__setattr__(self, "required_subskill_ids", required_subskill_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_skill_id": self.team_skill_id,
            "requirement_type": self.requirement_type,
            "required_level": self.required_level,
            "requirement_evidence": list(self.requirement_evidence),
            "required_capabilities": list(self.required_capabilities),
            "market_trend_rank": self.market_trend_rank,
            "required_subskill_ids": list(self.required_subskill_ids),
        }


@dataclass(frozen=True)
class JobLearningTarget:
    job_id: str
    job_title: str
    requirements: tuple[JobSkillRequirement, ...]

    def __post_init__(self) -> None:
        _require_text(self.job_id, "job_id")
        _require_text(self.job_title, "job_title")
        if not self.requirements:
            raise ValueError("job target must contain at least one requirement")
        skill_ids = [item.team_skill_id for item in self.requirements]
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("job requirements must be unique by team_skill_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_title": self.job_title,
            "requirements": [item.to_dict() for item in self.requirements],
        }


@dataclass(frozen=True)
class DevelopmentNode:
    subskill_id: str
    name_zh: str
    definition: str
    node_type: str
    prerequisites: tuple[str, ...]
    learning_outcome: str
    evidence_task: str | None
    validation_criteria: tuple[str, ...]
    source_references: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.subskill_id, "subskill_id")
        _require_text(self.name_zh, "subskill name_zh")
        _require_text(self.definition, "subskill definition")
        if self.node_type not in {"core", "specialization"}:
            raise ValueError(f"invalid subskill type: {self.node_type}")
        _require_text(self.learning_outcome, "learning_outcome")
        if self.evidence_task is not None:
            _require_text(self.evidence_task, "evidence_task")
        criteria = tuple(value.strip() for value in self.validation_criteria if value.strip())
        references = tuple(value.strip() for value in self.source_references if value.strip())
        if not criteria:
            raise ValueError("validation_criteria must be non-empty")
        if not references:
            raise ValueError("source_references must be non-empty")
        if len(self.prerequisites) != len(set(self.prerequisites)):
            raise ValueError("prerequisites must be unique")
        object.__setattr__(self, "validation_criteria", criteria)
        object.__setattr__(self, "source_references", references)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DevelopmentNode":
        return cls(
            subskill_id=str(payload.get("subskill_id", "")),
            name_zh=str(payload.get("name_zh", "")),
            definition=str(payload.get("definition", "")),
            node_type=str(payload.get("type", "")).lower(),
            prerequisites=tuple(str(value) for value in payload.get("prerequisites", [])),
            learning_outcome=str(payload.get("learning_outcome", "")),
            evidence_task=(str(payload["evidence_task"]) if payload.get("evidence_task") is not None else None),
            validation_criteria=tuple(str(value) for value in payload.get("validation_criteria", [])),
            source_references=tuple(
                str(value)
                for value in payload.get("source_refs", payload.get("source_references", []))
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "subskill_id": self.subskill_id,
            "name_zh": self.name_zh,
            "definition": self.definition,
            "type": self.node_type.upper(),
            "prerequisites": list(self.prerequisites),
            "learning_outcome": self.learning_outcome,
            "evidence_task": self.evidence_task,
            "validation_criteria": list(self.validation_criteria),
            "source_refs": list(self.source_references),
        }


@dataclass(frozen=True)
class GraphVerificationTask:
    """Integrated task for resolving U without prescribing a learning path."""

    task_id: str
    name_zh: str
    evidence_task: str
    validation_criteria: tuple[str, ...]
    source_references: tuple[str, ...]
    objective: str = "Generate current proficiency assessment evidence"
    purpose: str = _VERIFICATION_PURPOSE

    def __post_init__(self) -> None:
        _require_text(self.task_id, "verification task_id")
        _require_text(self.name_zh, "verification task name_zh")
        _require_text(self.evidence_task, "verification evidence_task")
        _require_text(self.objective, "verification objective")
        if self.purpose != _VERIFICATION_PURPOSE:
            raise ValueError(f"invalid verification purpose: {self.purpose}")
        criteria = tuple(value.strip() for value in self.validation_criteria if value.strip())
        references = tuple(value.strip() for value in self.source_references if value.strip())
        if not criteria:
            raise ValueError("verification validation_criteria must be non-empty")
        object.__setattr__(self, "validation_criteria", criteria)
        object.__setattr__(self, "source_references", references)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GraphVerificationTask":
        return cls(
            task_id=str(payload.get("task_id", "")),
            name_zh=str(payload.get("name_zh", payload.get("objective", ""))),
            evidence_task=str(payload.get("evidence_task", payload.get("task_description", ""))),
            validation_criteria=tuple(str(value) for value in payload.get("validation_criteria", [])),
            source_references=tuple(
                str(value)
                for value in payload.get("source_refs", payload.get("source_references", []))
            ),
            objective=str(payload.get("objective", payload.get("name_zh", ""))),
            purpose=str(payload.get("purpose", _VERIFICATION_PURPOSE)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "task_description": self.evidence_task,
            "validation_criteria": list(self.validation_criteria),
            "purpose": self.purpose,
        }


@dataclass(frozen=True)
class CapstoneSpecializationExtension:
    subskill_id: str
    task_description: str
    validation_criteria: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.subskill_id, "capstone extension subskill_id")
        _require_text(self.task_description, "capstone extension task_description")
        criteria = tuple(value.strip() for value in self.validation_criteria if value.strip())
        if not criteria:
            raise ValueError("capstone extension validation_criteria must be non-empty")
        object.__setattr__(self, "validation_criteria", criteria)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CapstoneSpecializationExtension":
        return cls(
            subskill_id=str(payload.get("subskill_id", "")),
            task_description=str(payload.get("task_description", "")),
            validation_criteria=tuple(str(value) for value in payload.get("validation_criteria", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "subskill_id": self.subskill_id,
            "task_description": self.task_description,
            "validation_criteria": list(self.validation_criteria),
        }


@dataclass(frozen=True)
class CapstoneEvidenceTask:
    """Graph-level behavioral evidence task; it never assigns proficiency."""

    task_id: str
    objective: str
    task_description: str
    validation_criteria: tuple[str, ...]
    purpose: str = _CAPSTONE_PURPOSE
    specialization_extensions: tuple[CapstoneSpecializationExtension, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_text(self.task_id, "capstone task_id")
        _require_text(self.objective, "capstone objective")
        _require_text(self.task_description, "capstone task_description")
        if self.purpose != _CAPSTONE_PURPOSE:
            raise ValueError(f"invalid capstone purpose: {self.purpose}")
        criteria = tuple(value.strip() for value in self.validation_criteria if value.strip())
        if not criteria:
            raise ValueError("capstone validation_criteria must be non-empty")
        object.__setattr__(self, "validation_criteria", criteria)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CapstoneEvidenceTask":
        return cls(
            task_id=str(payload.get("task_id", "")),
            objective=str(payload.get("objective", "")),
            task_description=str(payload.get("task_description", "")),
            validation_criteria=tuple(str(value) for value in payload.get("validation_criteria", [])),
            purpose=str(payload.get("purpose", "")),
            specialization_extensions=tuple(
                CapstoneSpecializationExtension.from_dict(value)
                for value in payload.get("specialization_extensions", [])
                if isinstance(value, Mapping)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "task_description": self.task_description,
            "specialization_extensions": [
                extension.to_dict() for extension in self.specialization_extensions
            ],
            "validation_criteria": list(self.validation_criteria),
            "purpose": self.purpose,
        }


@dataclass(frozen=True)
class SourceRegistryEntry:
    source_id: str
    title: str
    authors_or_org: str
    source_type: str
    year: int | None
    venue: str | None
    version: str | None
    doi: str | None
    url: str | None
    supports: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.source_id, "source_id")
        _require_text(self.title, "source title")
        _require_text(self.authors_or_org, "source authors_or_org")
        _require_text(self.source_type, "source_type")
        supports = tuple(value.strip() for value in self.supports if value.strip())
        if not supports:
            raise ValueError("source supports must be non-empty")
        object.__setattr__(self, "supports", supports)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SourceRegistryEntry":
        return cls(
            source_id=str(payload.get("source_id", "")),
            title=str(payload.get("title", "")),
            authors_or_org=str(payload.get("authors_or_org", "")),
            source_type=str(payload.get("source_type", "")),
            year=(int(payload["year"]) if payload.get("year") is not None else None),
            venue=(str(payload["venue"]) if payload.get("venue") is not None else None),
            version=(str(payload["version"]) if payload.get("version") is not None else None),
            doi=(str(payload["doi"]) if payload.get("doi") is not None else None),
            url=(str(payload["url"]) if payload.get("url") is not None else None),
            supports=tuple(str(value) for value in payload.get("supports", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "authors_or_org": self.authors_or_org,
            "source_type": self.source_type,
            "year": self.year,
            "venue": self.venue,
            "version": self.version,
            "doi": self.doi,
            "url": self.url,
            "supports": list(self.supports),
        }


@dataclass(frozen=True)
class SkillDevelopmentGraph:
    graph_version: str
    team_skill_id: str
    team_skill_name: str
    coverage_scope: str
    subskill_nodes: tuple[DevelopmentNode, ...]
    verification_task: GraphVerificationTask
    selection_protocol: str = "core_plus_required_v1"
    capstone_evidence_task: CapstoneEvidenceTask | None = None
    source_registry: tuple[SourceRegistryEntry, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_text(self.graph_version, "graph_version")
        _require_text(self.team_skill_id, "team_skill_id")
        _require_text(self.team_skill_name, "team_skill_name")
        _require_text(self.coverage_scope, "coverage_scope")
        _reject_auxiliary(self.team_skill_id)
        if self.selection_protocol not in _GRAPH_SELECTION_PROTOCOLS:
            raise ValueError(f"invalid graph selection_protocol: {self.selection_protocol}")
        if not self.subskill_nodes:
            raise ValueError("development graph must contain nodes")
        node_ids = [node.subskill_id for node in self.subskill_nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("development graph node IDs must be unique")
        node_id_set = set(node_ids)
        source_ids = [source.source_id for source in self.source_registry]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source registry IDs must be unique")
        source_id_set = set(source_ids)
        for node in self.subskill_nodes:
            invalid = [value for value in node.prerequisites if value not in node_id_set]
            if invalid:
                raise ValueError(f"invalid prerequisite for {node.subskill_id}: {invalid[0]}")
            if node.subskill_id in node.prerequisites:
                raise ValueError(f"self prerequisite is invalid: {node.subskill_id}")
            if source_id_set:
                invalid_sources = [value for value in node.source_references if value not in source_id_set]
                if invalid_sources:
                    raise ValueError(f"invalid source reference for {node.subskill_id}: {invalid_sources[0]}")
        if self.capstone_evidence_task is not None:
            by_id = {node.subskill_id: node for node in self.subskill_nodes}
            for extension in self.capstone_evidence_task.specialization_extensions:
                node = by_id.get(extension.subskill_id)
                if node is None:
                    raise ValueError(f"invalid capstone specialization: {extension.subskill_id}")
                if node.node_type != "specialization":
                    raise ValueError(f"capstone extension must reference specialization: {extension.subskill_id}")
        self.topological_nodes()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SkillDevelopmentGraph":
        is_compact = "nodes" in payload
        raw_nodes = payload.get("nodes", payload.get("subskill_nodes", []))
        if not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, (str, bytes)):
            raise ValueError("subskill_nodes must be an array")
        return cls(
            graph_version=str(payload.get("version", payload.get("graph_version", ""))),
            team_skill_id=str(payload.get("team_skill_id", "")),
            team_skill_name=str(payload.get("team_skill_name", "")),
            coverage_scope=str(payload.get("scope", payload.get("coverage_scope", ""))),
            subskill_nodes=tuple(
                DevelopmentNode.from_dict(value)
                for value in raw_nodes
                if isinstance(value, Mapping)
            ),
            verification_task=GraphVerificationTask.from_dict(
                payload.get("verification_task", {})
                if isinstance(payload.get("verification_task", {}), Mapping)
                else {}
            ),
            # Frozen pre-Stage-2B1 graphs preserve their original all-node paths.
            selection_protocol=str(
                payload.get("selection_protocol", "core_plus_required_v1" if is_compact else "legacy_all_nodes")
            ),
            capstone_evidence_task=(
                CapstoneEvidenceTask.from_dict(payload["capstone_evidence_task"])
                if isinstance(payload.get("capstone_evidence_task"), Mapping)
                else None
            ),
            source_registry=tuple(
                SourceRegistryEntry.from_dict(value)
                for value in payload.get("source_registry", [])
                if isinstance(value, Mapping)
            ),
        )

    def topological_nodes(self) -> tuple[DevelopmentNode, ...]:
        """Return a stable curated-order topological sort or reject a cycle."""
        order = {node.subskill_id: index for index, node in enumerate(self.subskill_nodes)}
        by_id = {node.subskill_id: node for node in self.subskill_nodes}
        indegree = {node.subskill_id: len(node.prerequisites) for node in self.subskill_nodes}
        dependents: dict[str, list[str]] = {node.subskill_id: [] for node in self.subskill_nodes}
        for node in self.subskill_nodes:
            for prerequisite in node.prerequisites:
                dependents[prerequisite].append(node.subskill_id)
        ready = sorted((node_id for node_id, count in indegree.items() if count == 0), key=order.get)
        result: list[DevelopmentNode] = []
        while ready:
            node_id = ready.pop(0)
            result.append(by_id[node_id])
            for dependent in sorted(dependents[node_id], key=order.get):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    ready.append(dependent)
                    ready.sort(key=order.get)
        if len(result) != len(self.subskill_nodes):
            raise ValueError("development graph must be acyclic")
        return tuple(result)

    def unlock_value(self) -> int:
        """Count prerequisite relationships; no learned or weighted score is used."""
        return sum(len(node.prerequisites) for node in self.subskill_nodes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.graph_version,
            "team_skill_id": self.team_skill_id,
            "team_skill_name": self.team_skill_name,
            "scope": self.coverage_scope,
            "verification_task": self.verification_task.to_dict(),
            "capstone_evidence_task": (
                self.capstone_evidence_task.to_dict() if self.capstone_evidence_task else None
            ),
            "nodes": [node.to_dict() for node in self.subskill_nodes],
            "source_registry": [source.to_dict() for source in self.source_registry],
        }


def load_skill_development_graph(path: str | Path) -> SkillDevelopmentGraph:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("development graph fixture must be a JSON object")
    return SkillDevelopmentGraph.from_dict(payload)


@dataclass(frozen=True)
class GapItem:
    team_skill_id: str
    team_skill_name: str
    requirement_type: str
    required_level: str | None
    observed_level: str | None
    gap_type: GapType
    path_mode: PathMode
    required_capabilities: tuple[str, ...]
    unverified_capabilities: tuple[str, ...]
    unlock_value: int
    market_trend_rank: int | None
    explanation: str
    required_subskill_ids: tuple[str, ...] = field(default_factory=tuple)
    achieved_subskills: tuple[AchievedSubskill, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_skill_id": self.team_skill_id,
            "team_skill_name": self.team_skill_name,
            "requirement_type": self.requirement_type,
            "required_level": self.required_level,
            "observed_level": self.observed_level,
            "gap_type": self.gap_type.value,
            "path_mode": self.path_mode.value,
            "required_capabilities": list(self.required_capabilities),
            "unverified_capabilities": list(self.unverified_capabilities),
            "unlock_value": self.unlock_value,
            "market_trend_rank": self.market_trend_rank,
            "explanation": self.explanation,
            "required_subskill_ids": list(self.required_subskill_ids),
            "achieved_subskills": [item.to_dict() for item in self.achieved_subskills],
        }


@dataclass(frozen=True)
class PriorityExplanation:
    rank: int | None
    team_skill_id: str
    requirement_type: str
    gap_type: GapType
    prerequisite_unlock_value: int
    market_trend_rank: int | None
    lexicographic_key: tuple[Any, ...] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "team_skill_id": self.team_skill_id,
            "requirement_type": self.requirement_type,
            "gap_type": self.gap_type.value,
            "prerequisite_unlock_value": self.prerequisite_unlock_value,
            "market_trend_rank": self.market_trend_rank,
            "lexicographic_key": list(self.lexicographic_key) if self.lexicographic_key is not None else None,
        }


@dataclass(frozen=True)
class LearningStep:
    order: int
    subskill_id: str
    name_zh: str
    definition: str
    action_mode: PathMode
    prerequisites: tuple[str, ...]
    learning_outcome: str
    evidence_task: str | None
    validation_criteria: tuple[str, ...]
    source_references: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "subskill_id": self.subskill_id,
            "name_zh": self.name_zh,
            "definition": self.definition,
            "action_mode": self.action_mode.value,
            "prerequisites": list(self.prerequisites),
            "learning_outcome": self.learning_outcome,
            "evidence_task": self.evidence_task,
            "validation_criteria": list(self.validation_criteria),
            "source_references": list(self.source_references),
        }


@dataclass(frozen=True)
class LearningPath:
    team_skill_id: str
    team_skill_name: str
    mode: PathMode
    path_status: str
    ordered_steps: tuple[LearningStep, ...]
    evidence_tasks: tuple[str, ...]
    achieved_subskills: tuple[AchievedSubskill, ...] = field(default_factory=tuple)
    capstone_evidence_task: CapstoneEvidenceTask | None = None
    target_level: str | None = None
    reassessment_required: bool = False
    reassessment_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_skill_id": self.team_skill_id,
            "team_skill_name": self.team_skill_name,
            "mode": self.mode.value,
            "path_status": self.path_status,
            "ordered_steps": [step.to_dict() for step in self.ordered_steps],
            "evidence_tasks": list(self.evidence_tasks),
            "achieved_subskills": [item.to_dict() for item in self.achieved_subskills],
            "capstone_evidence_task": (
                self.capstone_evidence_task.to_dict() if self.capstone_evidence_task else None
            ),
            "target_level": self.target_level,
            "reassessment_required": self.reassessment_required,
            "reassessment_reason": self.reassessment_reason,
        }


@dataclass(frozen=True)
class GapSummary:
    total_requirements: int
    missing: int
    level_gap: int
    evidence_insufficient: int
    satisfied: int

    def to_dict(self) -> dict[str, int]:
        return {
            "total_requirements": self.total_requirements,
            "MISSING": self.missing,
            "LEVEL_GAP": self.level_gap,
            "EVIDENCE_INSUFFICIENT": self.evidence_insufficient,
            "SATISFIED": self.satisfied,
        }


@dataclass(frozen=True)
class LearningPathResult:
    candidate_id: str
    target_job_id: str
    gap_summary: GapSummary
    gap_items: tuple[GapItem, ...]
    priority_explanations: tuple[PriorityExplanation, ...]
    paths: tuple[LearningPath, ...]
    path_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "target_job_id": self.target_job_id,
            "gap_summary": self.gap_summary.to_dict(),
            "gap_items": [item.to_dict() for item in self.gap_items],
            "priority_explanations": [item.to_dict() for item in self.priority_explanations],
            "paths": [item.to_dict() for item in self.paths],
            "path_status": self.path_status,
        }


class GapEngine:
    def __init__(self, graphs: Mapping[str, SkillDevelopmentGraph] | None = None) -> None:
        self._graphs = dict(graphs or {})

    def evaluate(
        self,
        candidate: CandidateLearningProfile,
        target: JobLearningTarget,
    ) -> tuple[GapItem, ...]:
        observed_by_id = candidate.supported_by_id
        items: list[GapItem] = []
        for requirement in target.requirements:
            observed = observed_by_id.get(requirement.team_skill_id)
            graph = self._graphs.get(requirement.team_skill_id)
            skill_name = graph.team_skill_name if graph else requirement.team_skill_id
            unlock_value = graph.unlock_value() if graph else 0
            if requirement.required_subskill_ids:
                if graph is None:
                    raise ValueError(
                        f"required_subskill_ids require a development graph for {requirement.team_skill_id}"
                    )
                graph_node_ids = {node.subskill_id for node in graph.subskill_nodes}
                invalid_required_ids = [
                    value for value in requirement.required_subskill_ids if value not in graph_node_ids
                ]
                if invalid_required_ids:
                    raise ValueError(
                        f"required subskill is not present in graph: {invalid_required_ids[0]}"
                    )
            if observed is None:
                items.append(
                    GapItem(
                        team_skill_id=requirement.team_skill_id,
                        team_skill_name=skill_name,
                        requirement_type=requirement.requirement_type,
                        required_level=requirement.required_level,
                        observed_level=None,
                        gap_type=GapType.MISSING,
                        path_mode=PathMode.LEARN,
                        required_capabilities=requirement.required_capabilities,
                        unverified_capabilities=requirement.required_capabilities,
                        unlock_value=unlock_value,
                        market_trend_rank=requirement.market_trend_rank,
                        explanation="No supported Team Skill evidence is present; explicit mentions do not satisfy support.",
                        required_subskill_ids=requirement.required_subskill_ids,
                    )
                )
                continue

            if observed.achieved_subskills:
                if graph is None:
                    raise ValueError(
                        f"achieved_subskills require a development graph for {requirement.team_skill_id}"
                    )
                graph_node_ids = {node.subskill_id for node in graph.subskill_nodes}
                invalid_ids = [
                    item.subskill_id
                    for item in observed.achieved_subskills
                    if item.subskill_id not in graph_node_ids
                ]
                if invalid_ids:
                    raise ValueError(f"achieved subskill is not present in graph: {invalid_ids[0]}")

            observed_capabilities = {_normalized_capability(value) for value in observed.observed_capabilities}
            unverified = tuple(
                value
                for value in requirement.required_capabilities
                if _normalized_capability(value) not in observed_capabilities
            )
            if unverified:
                gap_type = GapType.EVIDENCE_INSUFFICIENT
                mode = PathMode.VERIFY_FIRST
                explanation = "Supported Team Skill evidence does not explicitly cover every stated job capability; verify scope first."
            elif observed.observed_proficiency == "U":
                gap_type = GapType.EVIDENCE_INSUFFICIENT
                mode = PathMode.VERIFY_FIRST
                explanation = "Observed proficiency is U; U means insufficient evidence, not low skill."
            elif requirement.required_level is None:
                gap_type = GapType.SATISFIED
                mode = PathMode.NONE
                explanation = "Supported evidence covers the stated capability scope; no proficiency threshold was specified."
            elif observed.observed_proficiency is None:
                gap_type = GapType.EVIDENCE_INSUFFICIENT
                mode = PathMode.VERIFY_FIRST
                explanation = "The job specifies a level but no observed proficiency is available; verify level first."
            elif _PROFICIENCY_ORDER[observed.observed_proficiency] < _PROFICIENCY_ORDER[requirement.required_level]:
                gap_type = GapType.LEVEL_GAP
                mode = PathMode.DEEPEN
                explanation = "Observed evidence-supported proficiency is below the stated requirement."
            else:
                gap_type = GapType.SATISFIED
                mode = PathMode.NONE
                explanation = "Observed evidence-supported proficiency meets the stated requirement."
            items.append(
                GapItem(
                    team_skill_id=requirement.team_skill_id,
                    team_skill_name=observed.team_skill_name,
                    requirement_type=requirement.requirement_type,
                    required_level=requirement.required_level,
                    observed_level=observed.observed_proficiency,
                    gap_type=gap_type,
                    path_mode=mode,
                    required_capabilities=requirement.required_capabilities,
                    unverified_capabilities=unverified,
                    unlock_value=unlock_value,
                    market_trend_rank=requirement.market_trend_rank,
                    explanation=explanation,
                    required_subskill_ids=requirement.required_subskill_ids,
                    achieved_subskills=observed.achieved_subskills,
                )
            )
        return tuple(items)


class DeterministicPriorityRanker:
    """Lexicographic ranking with no weighted or learned score."""

    @staticmethod
    def _actionable_key(item: GapItem) -> tuple[Any, ...]:
        trend_rank = item.market_trend_rank if item.market_trend_rank is not None else 2**31 - 1
        return (
            _REQUIREMENT_TYPE_ORDER[item.requirement_type],
            _ACTIONABLE_GAP_ORDER[item.gap_type.value],
            -item.unlock_value,
            trend_rank,
            item.team_skill_id,
        )

    def rank(
        self,
        items: Sequence[GapItem],
    ) -> tuple[tuple[GapItem, ...], tuple[PriorityExplanation, ...]]:
        actionable = sorted(
            (item for item in items if item.gap_type is not GapType.SATISFIED),
            key=self._actionable_key,
        )
        satisfied = sorted(
            (item for item in items if item.gap_type is GapType.SATISFIED),
            key=lambda item: (_REQUIREMENT_TYPE_ORDER[item.requirement_type], item.team_skill_id),
        )
        ranked = tuple(actionable + satisfied)
        explanations: list[PriorityExplanation] = []
        actionable_rank = {item.team_skill_id: index + 1 for index, item in enumerate(actionable)}
        for item in ranked:
            key = self._actionable_key(item) if item.gap_type is not GapType.SATISFIED else None
            explanations.append(
                PriorityExplanation(
                    rank=actionable_rank.get(item.team_skill_id),
                    team_skill_id=item.team_skill_id,
                    requirement_type=item.requirement_type,
                    gap_type=item.gap_type,
                    prerequisite_unlock_value=item.unlock_value,
                    market_trend_rank=item.market_trend_rank,
                    lexicographic_key=key,
                )
            )
        return ranked, tuple(explanations)


class DeterministicPathPlanner:
    def __init__(self, graphs: Mapping[str, SkillDevelopmentGraph]) -> None:
        self._graphs = dict(graphs)

    @staticmethod
    def _select_nodes(graph: SkillDevelopmentGraph, item: GapItem) -> tuple[DevelopmentNode, ...]:
        nodes = graph.topological_nodes()
        if graph.selection_protocol == "legacy_all_nodes":
            return nodes
        by_id = {node.subskill_id: node for node in nodes}
        selected_ids = {node.subskill_id for node in nodes if node.node_type == "core"}
        selected_ids.update(item.required_subskill_ids)
        pending = list(selected_ids)
        while pending:
            node = by_id[pending.pop()]
            for prerequisite in node.prerequisites:
                if prerequisite not in selected_ids:
                    selected_ids.add(prerequisite)
                    pending.append(prerequisite)
        return tuple(node for node in nodes if node.subskill_id in selected_ids)

    @staticmethod
    def _reassessment_reason(mode: PathMode) -> str:
        if mode is PathMode.LEARN:
            return (
                "New behavioral evidence must be sent to the frozen proficiency evaluation process; "
                "path completion assigns no proficiency level."
            )
        if mode is PathMode.DEEPEN:
            return (
                "Capstone behavioral evidence must be reassessed against target_level; achieved "
                "subskills do not determine Team Skill proficiency."
            )
        return (
            "Existing evidence is insufficient; integrated verification evidence must be reassessed "
            "before assigning a proficiency level."
        )

    def plan(self, item: GapItem) -> LearningPath:
        if item.path_mode is PathMode.NONE:
            return LearningPath(
                team_skill_id=item.team_skill_id,
                team_skill_name=item.team_skill_name,
                mode=PathMode.NONE,
                path_status="NO_ACTION",
                ordered_steps=(),
                evidence_tasks=(),
                achieved_subskills=item.achieved_subskills,
                capstone_evidence_task=None,
                target_level=item.required_level,
                reassessment_required=False,
                reassessment_reason=None,
            )
        graph = self._graphs.get(item.team_skill_id)
        if graph is None:
            return LearningPath(
                team_skill_id=item.team_skill_id,
                team_skill_name=item.team_skill_name,
                mode=item.path_mode,
                path_status="GRAPH_UNAVAILABLE",
                ordered_steps=(),
                evidence_tasks=(),
                achieved_subskills=item.achieved_subskills,
                capstone_evidence_task=None,
                target_level=item.required_level,
                reassessment_required=True,
                reassessment_reason=self._reassessment_reason(item.path_mode),
            )
        if item.path_mode is PathMode.VERIFY_FIRST:
            verification = graph.verification_task
            step = LearningStep(
                order=1,
                subskill_id=verification.task_id,
                name_zh=verification.name_zh,
                definition="用于补足证据、区分已观察能力范围与熟练度；不是从零学习路径。",
                action_mode=PathMode.VERIFY_FIRST,
                prerequisites=(),
                learning_outcome=(
                    "形成可归因、可复核的目标 Team Skill 行为证据，供当前熟练度重新评估；"
                    "验证任务本身不分配熟练度。"
                ),
                evidence_task=verification.evidence_task,
                validation_criteria=verification.validation_criteria,
                source_references=verification.source_references,
            )
            return LearningPath(
                team_skill_id=item.team_skill_id,
                team_skill_name=item.team_skill_name,
                mode=PathMode.VERIFY_FIRST,
                path_status="READY",
                ordered_steps=(step,),
                evidence_tasks=tuple(value for value in (step.evidence_task,) if value),
                achieved_subskills=item.achieved_subskills,
                capstone_evidence_task=None,
                target_level=item.required_level,
                reassessment_required=True,
                reassessment_reason=self._reassessment_reason(PathMode.VERIFY_FIRST),
            )
        nodes = self._select_nodes(graph, item)
        achieved_ids = {value.subskill_id for value in item.achieved_subskills}
        remaining_nodes = tuple(node for node in nodes if node.subskill_id not in achieved_ids)
        steps = tuple(
            LearningStep(
                order=index + 1,
                subskill_id=node.subskill_id,
                name_zh=node.name_zh,
                definition=node.definition,
                action_mode=item.path_mode,
                prerequisites=node.prerequisites,
                learning_outcome=node.learning_outcome,
                evidence_task=node.evidence_task,
                validation_criteria=node.validation_criteria,
                source_references=node.source_references,
            )
            for index, node in enumerate(remaining_nodes)
        )
        capstone = graph.capstone_evidence_task
        if capstone is not None:
            # Filter against the selected graph before achieved-subskill pruning.
            # A selected specialization therefore keeps its capstone extension
            # even when its learning node has already been achieved.
            selected_specialization_ids = {
                node.subskill_id for node in nodes if node.node_type == "specialization"
            }
            capstone = replace(
                capstone,
                specialization_extensions=tuple(
                    extension
                    for extension in capstone.specialization_extensions
                    if extension.subskill_id in selected_specialization_ids
                ),
            )
        evidence_tasks = tuple(step.evidence_task for step in steps if step.evidence_task)
        if capstone is not None:
            evidence_tasks += (capstone.task_description,)
        return LearningPath(
            team_skill_id=item.team_skill_id,
            team_skill_name=item.team_skill_name,
            mode=item.path_mode,
            path_status="READY",
            ordered_steps=steps,
            evidence_tasks=evidence_tasks,
            achieved_subskills=item.achieved_subskills,
            capstone_evidence_task=capstone,
            target_level=item.required_level,
            reassessment_required=True,
            reassessment_reason=self._reassessment_reason(item.path_mode),
        )


class LearningPathEngine:
    def __init__(self, graphs: Sequence[SkillDevelopmentGraph]) -> None:
        graph_by_skill: dict[str, SkillDevelopmentGraph] = {}
        for graph in graphs:
            if graph.team_skill_id in graph_by_skill:
                raise ValueError(f"duplicate development graph for {graph.team_skill_id}")
            graph_by_skill[graph.team_skill_id] = graph
        self._gap_engine = GapEngine(graph_by_skill)
        self._ranker = DeterministicPriorityRanker()
        self._planner = DeterministicPathPlanner(graph_by_skill)

    def build(
        self,
        candidate: CandidateLearningProfile,
        target: JobLearningTarget,
    ) -> LearningPathResult:
        raw_items = self._gap_engine.evaluate(candidate, target)
        ranked_items, explanations = self._ranker.rank(raw_items)
        paths = tuple(self._planner.plan(item) for item in ranked_items)
        counts = {gap_type: sum(item.gap_type is gap_type for item in raw_items) for gap_type in GapType}
        actionable_paths = [path for path in paths if path.mode is not PathMode.NONE]
        if not actionable_paths:
            status = "NO_ACTION"
        elif any(path.path_status == "GRAPH_UNAVAILABLE" for path in actionable_paths):
            status = "PARTIAL_GRAPH_COVERAGE"
        else:
            status = "READY"
        return LearningPathResult(
            candidate_id=candidate.candidate_id,
            target_job_id=target.job_id,
            gap_summary=GapSummary(
                total_requirements=len(raw_items),
                missing=counts[GapType.MISSING],
                level_gap=counts[GapType.LEVEL_GAP],
                evidence_insufficient=counts[GapType.EVIDENCE_INSUFFICIENT],
                satisfied=counts[GapType.SATISFIED],
            ),
            gap_items=ranked_items,
            priority_explanations=explanations,
            paths=paths,
            path_status=status,
        )
