"""Strict, deterministic loader for the versioned Ability Taxonomy v2."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


class TaxonomyV2Error(ValueError):
    """Raised when a Taxonomy v2 document violates its offline contract."""


NODE_TYPES = {
    "tool",
    "knowledge",
    "activity",
    "ability",
    "high_level_ability",
}
LEVEL_BY_NODE_TYPE = {
    "tool": 1,
    "knowledge": 1,
    "activity": 2,
    "ability": 3,
    "high_level_ability": 4,
}
COMPOUND_LABELS = {
    "compound_supported",
    "compound_unsupported",
    "split_recommended",
}
PILOT_VERSION = "taxonomy_v2_pilot"
PILOT_STATUS = "pilot_frozen"
PILOT_SCOPE = "computer_software_ai_resume"
PILOT_NODE_COUNT = 37
NODE_DEFINITION_CONFIDENCE = {"high", "medium", "low"}
NODE_REVIEW_STATUSES = {
    "approved_for_pilot",
    "draft_definition_frozen_for_pilot",
}
_NODE_REQUIRED_FIELDS = {
    "id",
    "canonical_name",
    "node_type",
    "level",
    "parent_id",
    "aliases",
    "related_tools",
    "related_knowledge",
    "evidence_requirements",
    "strong_qualifiers",
    "allowed_compounds",
    "forbidden_inferences",
    "examples_positive",
    "examples_negative",
}
_NODE_OPTIONAL_FIELDS = {
    "matching_tags",
    "sibling_ids",
    "deprecated",
    "annotation_notes",
}
_NODE_REQUIRED_FIELDS.update({
    "description",
    "includes",
    "excludes",
    "confidence",
    "review_status",
})


def _strict_fields(
    value: Mapping[str, Any],
    required: set[str],
    optional: set[str],
    prefix: str,
) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required - optional)
    if missing:
        raise TaxonomyV2Error(f"{prefix} missing fields: {', '.join(missing)}")
    if unknown:
        raise TaxonomyV2Error(f"{prefix} unknown fields: {', '.join(unknown)}")


def _non_empty(value: Any, prefix: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaxonomyV2Error(f"{prefix} must be a non-empty string")
    return value.strip()


def _string_tuple(value: Any, prefix: str, *, non_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TaxonomyV2Error(f"{prefix} must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        text = _non_empty(item, f"{prefix}[{index}]")
        if text in result:
            raise TaxonomyV2Error(f"{prefix} contains duplicate value: {text}")
        result.append(text)
    if non_empty and not result:
        raise TaxonomyV2Error(f"{prefix} must not be empty")
    return tuple(result)


def _normalize_lookup(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"\s+", "", normalized)


@dataclass(frozen=True)
class EvidenceRequirement:
    template_id: str
    direct_action_required: bool
    knowledge_only_sufficient: bool
    required_all: tuple[str, ...]
    required_any: tuple[str, ...]
    mechanism_any: tuple[str, ...]
    insufficient_alone: tuple[str, ...]

    @classmethod
    def from_dict(
        cls,
        template_id: str,
        value: Any,
        prefix: str,
    ) -> "EvidenceRequirement":
        if not isinstance(value, Mapping):
            raise TaxonomyV2Error(f"{prefix} must be an object")
        required = {
            "direct_action_required",
            "knowledge_only_sufficient",
            "required_all",
            "required_any",
            "mechanism_any",
            "insufficient_alone",
        }
        _strict_fields(value, required, set(), prefix)
        for field_name in ("direct_action_required", "knowledge_only_sufficient"):
            if not isinstance(value[field_name], bool):
                raise TaxonomyV2Error(f"{prefix}.{field_name} must be boolean")
        requirement = cls(
            template_id=_non_empty(template_id, f"{prefix}.template_id"),
            direct_action_required=value["direct_action_required"],
            knowledge_only_sufficient=value["knowledge_only_sufficient"],
            required_all=_string_tuple(value["required_all"], f"{prefix}.required_all"),
            required_any=_string_tuple(value["required_any"], f"{prefix}.required_any"),
            mechanism_any=_string_tuple(value["mechanism_any"], f"{prefix}.mechanism_any"),
            insufficient_alone=_string_tuple(
                value["insufficient_alone"], f"{prefix}.insufficient_alone"
            ),
        )
        if not (
            requirement.required_all
            or requirement.required_any
            or requirement.mechanism_any
        ):
            raise TaxonomyV2Error(f"{prefix} must contain a positive evidence rule")
        return requirement

    def to_dict(self) -> dict[str, Any]:
        return {
            "direct_action_required": self.direct_action_required,
            "knowledge_only_sufficient": self.knowledge_only_sufficient,
            "required_all": list(self.required_all),
            "required_any": list(self.required_any),
            "mechanism_any": list(self.mechanism_any),
            "insufficient_alone": list(self.insufficient_alone),
        }


@dataclass(frozen=True)
class TaxonomyNode:
    id: str
    canonical_name: str
    node_type: str
    level: int
    parent_id: str | None
    aliases: tuple[str, ...]
    related_tools: tuple[str, ...]
    related_knowledge: tuple[str, ...]
    evidence_requirements: EvidenceRequirement
    strong_qualifiers: tuple[str, ...]
    allowed_compounds: tuple[str, ...]
    forbidden_inferences: tuple[str, ...]
    examples_positive: tuple[str, ...]
    examples_negative: tuple[str, ...]
    description: str
    includes: tuple[str, ...]
    excludes: tuple[str, ...]
    confidence: str
    review_status: str
    matching_tags: tuple[str, ...] = ()
    sibling_ids: tuple[str, ...] = ()
    deprecated: bool = False
    annotation_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "canonical_name": self.canonical_name,
            "node_type": self.node_type,
            "level": self.level,
            "parent_id": self.parent_id,
            "aliases": list(self.aliases),
            "related_tools": list(self.related_tools),
            "related_knowledge": list(self.related_knowledge),
            "evidence_requirements": self.evidence_requirements.template_id,
            "strong_qualifiers": list(self.strong_qualifiers),
            "allowed_compounds": list(self.allowed_compounds),
            "forbidden_inferences": list(self.forbidden_inferences),
            "examples_positive": list(self.examples_positive),
            "examples_negative": list(self.examples_negative),
            "description": self.description,
            "includes": list(self.includes),
            "excludes": list(self.excludes),
            "confidence": self.confidence,
            "review_status": self.review_status,
        }
        if self.matching_tags:
            result["matching_tags"] = list(self.matching_tags)
        if self.sibling_ids:
            result["sibling_ids"] = list(self.sibling_ids)
        if self.deprecated:
            result["deprecated"] = True
        if self.annotation_notes:
            result["annotation_notes"] = self.annotation_notes
        return result


@dataclass(frozen=True)
class EvidenceMapping:
    """A cross-layer evidence relation; it never implies automatic promotion."""

    source_id: str
    relation: str
    target_id: str

    @classmethod
    def from_dict(cls, value: Any, index: int) -> "EvidenceMapping":
        prefix = f"evidence_mappings[{index}]"
        if not isinstance(value, Mapping):
            raise TaxonomyV2Error(f"{prefix} must be an object")
        _strict_fields(
            value,
            {"source_id", "relation", "target_id"},
            set(),
            prefix,
        )
        relation = _non_empty(value["relation"], f"{prefix}.relation")
        if relation != "evidence_for":
            raise TaxonomyV2Error(f"{prefix}.relation must be 'evidence_for'")
        source_id = _non_empty(value["source_id"], f"{prefix}.source_id")
        target_id = _non_empty(value["target_id"], f"{prefix}.target_id")
        if source_id == target_id:
            raise TaxonomyV2Error(f"{prefix} source and target must differ")
        return cls(source_id=source_id, relation=relation, target_id=target_id)

    def to_dict(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "relation": self.relation,
            "target_id": self.target_id,
        }


@dataclass(frozen=True)
class CompoundRule:
    id: str
    label: str
    canonical_name: str
    component_ids: tuple[str, ...]
    required_all_components_supported: bool
    strong_qualifier_policy: str
    split_recommended: bool
    description: str
    examples_positive: tuple[str, ...]
    examples_negative: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Any, index: int) -> "CompoundRule":
        prefix = f"compound_rules[{index}]"
        if not isinstance(value, Mapping):
            raise TaxonomyV2Error(f"{prefix} must be an object")
        required = {
            "id",
            "label",
            "canonical_name",
            "component_ids",
            "required_all_components_supported",
            "strong_qualifier_policy",
            "split_recommended",
            "description",
            "examples_positive",
            "examples_negative",
        }
        _strict_fields(value, required, set(), prefix)
        label = _non_empty(value["label"], f"{prefix}.label")
        if label not in COMPOUND_LABELS:
            raise TaxonomyV2Error(f"{prefix}.label is invalid: {label}")
        for field_name in ("required_all_components_supported", "split_recommended"):
            if not isinstance(value[field_name], bool):
                raise TaxonomyV2Error(f"{prefix}.{field_name} must be boolean")
        policy = _non_empty(
            value["strong_qualifier_policy"],
            f"{prefix}.strong_qualifier_policy",
        )
        if policy not in {"all_requirements_must_pass", "not_applicable"}:
            raise TaxonomyV2Error(f"{prefix}.strong_qualifier_policy is invalid")
        components = _string_tuple(
            value["component_ids"], f"{prefix}.component_ids", non_empty=True
        )
        if len(components) < 2:
            raise TaxonomyV2Error(f"{prefix}.component_ids requires at least two nodes")
        return cls(
            id=_non_empty(value["id"], f"{prefix}.id"),
            label=label,
            canonical_name=_non_empty(value["canonical_name"], f"{prefix}.canonical_name"),
            component_ids=components,
            required_all_components_supported=value["required_all_components_supported"],
            strong_qualifier_policy=policy,
            split_recommended=value["split_recommended"],
            description=_non_empty(value["description"], f"{prefix}.description"),
            examples_positive=_string_tuple(
                value["examples_positive"], f"{prefix}.examples_positive", non_empty=True
            ),
            examples_negative=_string_tuple(
                value["examples_negative"], f"{prefix}.examples_negative", non_empty=True
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "canonical_name": self.canonical_name,
            "component_ids": list(self.component_ids),
            "required_all_components_supported": self.required_all_components_supported,
            "strong_qualifier_policy": self.strong_qualifier_policy,
            "split_recommended": self.split_recommended,
            "description": self.description,
            "examples_positive": list(self.examples_positive),
            "examples_negative": list(self.examples_negative),
        }


@dataclass(frozen=True)
class TaxonomySelectionTrace:
    """One deterministic selection score and its stable reason trail."""

    node_id: str
    score: int
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "taxonomy_id": self.node_id,
            "score": self.score,
            "reasons": list(self.reasons),
        }


class AbilityTaxonomyV2:
    """Immutable in-memory Taxonomy v2 with deterministic lookup and selection."""

    def __init__(
        self,
        taxonomy_version: str,
        version: str,
        status: str,
        frozen_at: str,
        scope: str,
        node_count: int,
        node_types: tuple[str, ...],
        levels: Mapping[str, str],
        evidence_requirement_templates: Mapping[str, EvidenceRequirement],
        nodes: tuple[TaxonomyNode, ...],
        evidence_mappings: tuple[EvidenceMapping, ...],
        compound_rules: tuple[CompoundRule, ...],
    ) -> None:
        self.taxonomy_version = taxonomy_version
        self.version = version
        self.status = status
        self.frozen_at = frozen_at
        self.scope = scope
        self.node_count = node_count
        self.node_types = node_types
        self.levels = dict(levels)
        self.evidence_requirement_templates = dict(evidence_requirement_templates)
        self.nodes = nodes
        self.evidence_mappings = evidence_mappings
        self.compound_rules = compound_rules
        self._by_id = {node.id: node for node in nodes}
        self._by_canonical = {
            _normalize_lookup(node.canonical_name): node for node in nodes
        }
        alias_lookup: dict[str, TaxonomyNode] = {}
        for node in nodes:
            for alias in node.aliases:
                alias_lookup[_normalize_lookup(alias)] = node
        self._by_alias = alias_lookup
        self._order = {node.id: index for index, node in enumerate(nodes)}

    @classmethod
    def load(cls, path: str | Path) -> "AbilityTaxonomyV2":
        source = Path(path)
        if not source.is_file():
            raise TaxonomyV2Error(f"taxonomy file does not exist: {source}")
        try:
            payload = json.loads(source.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as error:
            raise TaxonomyV2Error(f"cannot load taxonomy: {source}") from error
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: Any) -> "AbilityTaxonomyV2":
        if not isinstance(payload, Mapping):
            raise TaxonomyV2Error("taxonomy root must be an object")
        required = {
            "taxonomy_version",
            "version",
            "status",
            "frozen_at",
            "scope",
            "node_count",
            "node_types",
            "levels",
            "evidence_requirement_templates",
            "nodes",
            "evidence_mappings",
            "compound_rules",
        }
        _strict_fields(payload, required, set(), "taxonomy")
        version = _non_empty(payload["taxonomy_version"], "taxonomy.taxonomy_version")
        if version != "2.0":
            raise TaxonomyV2Error("taxonomy_version must be '2.0'")
        pilot_version = _non_empty(payload["version"], "taxonomy.version")
        if pilot_version != PILOT_VERSION:
            raise TaxonomyV2Error(f"taxonomy.version must be '{PILOT_VERSION}'")
        status = _non_empty(payload["status"], "taxonomy.status")
        if status != PILOT_STATUS:
            raise TaxonomyV2Error(f"taxonomy.status must be '{PILOT_STATUS}'")
        frozen_at = _non_empty(payload["frozen_at"], "taxonomy.frozen_at")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", frozen_at) is None:
            raise TaxonomyV2Error("taxonomy.frozen_at must use YYYY-MM-DD")
        scope = _non_empty(payload["scope"], "taxonomy.scope")
        if scope != PILOT_SCOPE:
            raise TaxonomyV2Error(f"taxonomy.scope must be '{PILOT_SCOPE}'")
        node_count = payload["node_count"]
        if isinstance(node_count, bool) or not isinstance(node_count, int):
            raise TaxonomyV2Error("taxonomy.node_count must be an integer")
        if node_count != PILOT_NODE_COUNT:
            raise TaxonomyV2Error(
                f"taxonomy.node_count must be {PILOT_NODE_COUNT}"
            )
        node_types = _string_tuple(
            payload["node_types"], "taxonomy.node_types", non_empty=True
        )
        if set(node_types) != NODE_TYPES or len(node_types) != len(NODE_TYPES):
            raise TaxonomyV2Error("node_types must contain every v2 node type exactly once")
        levels = payload["levels"]
        expected_levels = {
            "1": "tool_or_knowledge",
            "2": "activity",
            "3": "ability",
            "4": "high_level_ability",
        }
        if levels != expected_levels:
            raise TaxonomyV2Error("levels must match the Taxonomy v2 level contract")

        raw_templates = payload["evidence_requirement_templates"]
        if not isinstance(raw_templates, Mapping) or not raw_templates:
            raise TaxonomyV2Error("evidence_requirement_templates must be a non-empty object")
        templates: dict[str, EvidenceRequirement] = {}
        for template_id, raw_template in raw_templates.items():
            normalized_id = _non_empty(template_id, "evidence requirement template id")
            if normalized_id in templates:
                raise TaxonomyV2Error(f"duplicate evidence template: {normalized_id}")
            templates[normalized_id] = EvidenceRequirement.from_dict(
                normalized_id,
                raw_template,
                f"evidence_requirement_templates.{normalized_id}",
            )

        raw_nodes = payload["nodes"]
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raise TaxonomyV2Error("nodes must be a non-empty list")
        nodes: list[TaxonomyNode] = []
        node_ids: set[str] = set()
        canonical_lookup: dict[str, str] = {}
        alias_lookup: dict[str, str] = {}
        for index, raw_node in enumerate(raw_nodes):
            prefix = f"nodes[{index}]"
            if not isinstance(raw_node, Mapping):
                raise TaxonomyV2Error(f"{prefix} must be an object")
            _strict_fields(raw_node, _NODE_REQUIRED_FIELDS, _NODE_OPTIONAL_FIELDS, prefix)
            node_id = _non_empty(raw_node["id"], f"{prefix}.id")
            if node_id in node_ids:
                raise TaxonomyV2Error(f"duplicate node id: {node_id}")
            node_ids.add(node_id)
            canonical_name = _non_empty(
                raw_node["canonical_name"], f"{prefix}.canonical_name"
            )
            canonical_key = _normalize_lookup(canonical_name)
            if canonical_key in canonical_lookup:
                raise TaxonomyV2Error(
                    f"canonical name conflict: {canonical_name}"
                )
            if canonical_key in alias_lookup:
                raise TaxonomyV2Error(
                    f"canonical name conflicts with alias: {canonical_name}"
                )
            canonical_lookup[canonical_key] = node_id
            node_type = _non_empty(raw_node["node_type"], f"{prefix}.node_type")
            if node_type not in NODE_TYPES:
                raise TaxonomyV2Error(f"{prefix}.node_type is invalid: {node_type}")
            level = raw_node["level"]
            if isinstance(level, bool) or not isinstance(level, int):
                raise TaxonomyV2Error(f"{prefix}.level must be an integer")
            if level != LEVEL_BY_NODE_TYPE[node_type]:
                raise TaxonomyV2Error(
                    f"{prefix}.level does not match node_type {node_type}"
                )
            parent_id = raw_node["parent_id"]
            if parent_id is not None:
                parent_id = _non_empty(parent_id, f"{prefix}.parent_id")
            aliases = _string_tuple(raw_node["aliases"], f"{prefix}.aliases")
            node_alias_keys: set[str] = set()
            for alias in aliases:
                key = _normalize_lookup(alias)
                if key in node_alias_keys:
                    raise TaxonomyV2Error(
                        f"{prefix}.aliases contains normalized duplicate: {alias}"
                    )
                node_alias_keys.add(key)
                if key == canonical_key:
                    raise TaxonomyV2Error(
                        f"{prefix}.aliases repeats its canonical name"
                    )
                owner = alias_lookup.get(key) or canonical_lookup.get(key)
                if owner is not None and owner != node_id:
                    raise TaxonomyV2Error(f"alias conflict: {alias}")
                alias_lookup[key] = node_id
            requirement_id = _non_empty(
                raw_node["evidence_requirements"],
                f"{prefix}.evidence_requirements",
            )
            if requirement_id not in templates:
                raise TaxonomyV2Error(
                    f"{prefix}.evidence_requirements does not exist: {requirement_id}"
                )
            deprecated = raw_node.get("deprecated", False)
            if not isinstance(deprecated, bool):
                raise TaxonomyV2Error(f"{prefix}.deprecated must be boolean")
            confidence = _non_empty(
                raw_node["confidence"], f"{prefix}.confidence"
            )
            if confidence not in NODE_DEFINITION_CONFIDENCE:
                raise TaxonomyV2Error(f"{prefix}.confidence is invalid")
            review_status = _non_empty(
                raw_node["review_status"], f"{prefix}.review_status"
            )
            if review_status not in NODE_REVIEW_STATUSES:
                raise TaxonomyV2Error(f"{prefix}.review_status is invalid")
            node = TaxonomyNode(
                id=node_id,
                canonical_name=canonical_name,
                node_type=node_type,
                level=level,
                parent_id=parent_id,
                aliases=aliases,
                related_tools=_string_tuple(
                    raw_node["related_tools"], f"{prefix}.related_tools"
                ),
                related_knowledge=_string_tuple(
                    raw_node["related_knowledge"], f"{prefix}.related_knowledge"
                ),
                evidence_requirements=templates[requirement_id],
                strong_qualifiers=_string_tuple(
                    raw_node["strong_qualifiers"], f"{prefix}.strong_qualifiers"
                ),
                allowed_compounds=_string_tuple(
                    raw_node["allowed_compounds"], f"{prefix}.allowed_compounds"
                ),
                forbidden_inferences=_string_tuple(
                    raw_node["forbidden_inferences"],
                    f"{prefix}.forbidden_inferences",
                    non_empty=True,
                ),
                examples_positive=_string_tuple(
                    raw_node["examples_positive"],
                    f"{prefix}.examples_positive",
                    non_empty=True,
                ),
                examples_negative=_string_tuple(
                    raw_node["examples_negative"],
                    f"{prefix}.examples_negative",
                    non_empty=True,
                ),
                description=_non_empty(raw_node["description"], f"{prefix}.description"),
                includes=_string_tuple(
                    raw_node["includes"], f"{prefix}.includes", non_empty=True
                ),
                excludes=_string_tuple(
                    raw_node["excludes"], f"{prefix}.excludes", non_empty=True
                ),
                confidence=confidence,
                review_status=review_status,
                matching_tags=_string_tuple(
                    raw_node.get("matching_tags", []), f"{prefix}.matching_tags"
                ),
                sibling_ids=_string_tuple(
                    raw_node.get("sibling_ids", []), f"{prefix}.sibling_ids"
                ),
                deprecated=deprecated,
                annotation_notes=(
                    _non_empty(raw_node["annotation_notes"], f"{prefix}.annotation_notes")
                    if "annotation_notes" in raw_node
                    else ""
                ),
            )
            nodes.append(node)

        if len(nodes) != node_count:
            raise TaxonomyV2Error(
                "taxonomy.node_count does not match the nodes array"
            )

        by_id = {node.id: node for node in nodes}
        for node in nodes:
            if node.parent_id is not None and node.parent_id not in by_id:
                raise TaxonomyV2Error(
                    f"node {node.id} parent does not exist: {node.parent_id}"
                )
            for related_id in node.related_tools:
                related = by_id.get(related_id)
                if related is None or related.node_type != "tool":
                    raise TaxonomyV2Error(
                        f"node {node.id} has invalid related tool: {related_id}"
                    )
            for related_id in node.related_knowledge:
                related = by_id.get(related_id)
                if related is None or related.node_type != "knowledge":
                    raise TaxonomyV2Error(
                        f"node {node.id} has invalid related knowledge: {related_id}"
                    )
            for reference in node.allowed_compounds + node.sibling_ids:
                if reference not in by_id:
                    raise TaxonomyV2Error(
                        f"node {node.id} references unknown node: {reference}"
                    )
        cls._validate_parent_cycles(nodes)

        raw_mappings = payload["evidence_mappings"]
        if not isinstance(raw_mappings, list):
            raise TaxonomyV2Error("evidence_mappings must be a list")
        evidence_mappings = tuple(
            EvidenceMapping.from_dict(raw_mapping, index)
            for index, raw_mapping in enumerate(raw_mappings)
        )
        mapping_keys: set[tuple[str, str, str]] = set()
        for index, mapping in enumerate(evidence_mappings):
            key = (mapping.source_id, mapping.relation, mapping.target_id)
            if key in mapping_keys:
                raise TaxonomyV2Error("evidence_mappings contains a duplicate")
            mapping_keys.add(key)
            source = by_id.get(mapping.source_id)
            target = by_id.get(mapping.target_id)
            if source is None or source.node_type != "activity":
                raise TaxonomyV2Error(
                    f"evidence_mappings[{index}].source_id must reference activity"
                )
            if target is None or target.node_type != "ability":
                raise TaxonomyV2Error(
                    f"evidence_mappings[{index}].target_id must reference ability"
                )

        raw_rules = payload["compound_rules"]
        if not isinstance(raw_rules, list) or not raw_rules:
            raise TaxonomyV2Error("compound_rules must be a non-empty list")
        rules = tuple(
            CompoundRule.from_dict(raw_rule, index)
            for index, raw_rule in enumerate(raw_rules)
        )
        rule_ids: set[str] = set()
        for rule in rules:
            if rule.id in rule_ids:
                raise TaxonomyV2Error(f"duplicate compound rule id: {rule.id}")
            rule_ids.add(rule.id)
            for component_id in rule.component_ids:
                if component_id not in by_id:
                    raise TaxonomyV2Error(
                        f"compound rule {rule.id} references unknown node: {component_id}"
                    )
        if {rule.label for rule in rules} != COMPOUND_LABELS:
            raise TaxonomyV2Error(
                "compound_rules must include all three internal labels"
            )
        return cls(
            taxonomy_version=version,
            version=pilot_version,
            status=status,
            frozen_at=frozen_at,
            scope=scope,
            node_count=node_count,
            node_types=node_types,
            levels=levels,
            evidence_requirement_templates=templates,
            nodes=tuple(nodes),
            evidence_mappings=evidence_mappings,
            compound_rules=rules,
        )

    @staticmethod
    def _validate_parent_cycles(nodes: Sequence[TaxonomyNode]) -> None:
        parent_by_id = {node.id: node.parent_id for node in nodes}
        for node in nodes:
            path: set[str] = set()
            current: str | None = node.id
            while current is not None:
                if current in path:
                    raise TaxonomyV2Error(
                        f"parent cycle detected at node: {current}"
                    )
                path.add(current)
                current = parent_by_id.get(current)

    def get_node(self, node_id: str) -> TaxonomyNode:
        key = _non_empty(node_id, "node_id")
        try:
            return self._by_id[key]
        except KeyError as error:
            raise TaxonomyV2Error(f"unknown taxonomy node: {key}") from error

    def find_by_canonical_name(self, name: str) -> TaxonomyNode | None:
        return self._by_canonical.get(_normalize_lookup(_non_empty(name, "name")))

    def find_by_alias(self, alias: str) -> TaxonomyNode | None:
        return self._by_alias.get(_normalize_lookup(_non_empty(alias, "alias")))

    def parent_of(self, node_id: str) -> TaxonomyNode | None:
        node = self.get_node(node_id)
        return None if node.parent_id is None else self.get_node(node.parent_id)

    def children_of(self, node_id: str) -> tuple[TaxonomyNode, ...]:
        self.get_node(node_id)
        return tuple(node for node in self.nodes if node.parent_id == node_id)

    def related_tools(self, node_id: str) -> tuple[TaxonomyNode, ...]:
        node = self.get_node(node_id)
        return tuple(self.get_node(item) for item in node.related_tools)

    def related_knowledge(self, node_id: str) -> tuple[TaxonomyNode, ...]:
        node = self.get_node(node_id)
        return tuple(self.get_node(item) for item in node.related_knowledge)

    def evidence_targets_for(self, activity_id: str) -> tuple[TaxonomyNode, ...]:
        """Return declared ability targets without promoting the activity."""
        self.get_node(activity_id)
        return tuple(
            self.get_node(mapping.target_id)
            for mapping in self.evidence_mappings
            if mapping.source_id == activity_id
        )

    def evidence_sources_for(self, ability_id: str) -> tuple[TaxonomyNode, ...]:
        """Return declared activity evidence sources for one ability."""
        self.get_node(ability_id)
        return tuple(
            self.get_node(mapping.source_id)
            for mapping in self.evidence_mappings
            if mapping.target_id == ability_id
        )

    def select_relevant_nodes(
        self,
        ability_name: str,
        fact: str,
        behavior: str,
        evidence_texts: Sequence[str],
        max_nodes: int = 12,
    ) -> list[TaxonomyNode]:
        nodes, _ = self.select_relevant_nodes_with_trace(
            ability_name,
            fact,
            behavior,
            evidence_texts,
            max_nodes=max_nodes,
        )
        return nodes

    def select_relevant_nodes_with_trace(
        self,
        ability_name: str,
        fact: str,
        behavior: str,
        evidence_texts: Sequence[str],
        max_nodes: int = 12,
    ) -> tuple[list[TaxonomyNode], list[TaxonomySelectionTrace]]:
        """Select a bounded subset and explain every selected node.

        Only the candidate's ability, fact, behavior, and current evidence are
        considered.  The complete resume is deliberately not an input.
        """

        ability = _non_empty(ability_name, "ability_name")
        if not isinstance(fact, str) or not isinstance(behavior, str):
            raise TaxonomyV2Error("fact and behavior must be strings")
        if isinstance(max_nodes, bool) or not isinstance(max_nodes, int) or max_nodes < 1:
            raise TaxonomyV2Error("max_nodes must be a positive integer")
        if not isinstance(evidence_texts, Sequence) or isinstance(evidence_texts, str):
            raise TaxonomyV2Error("evidence_texts must be a sequence of strings")
        evidence = [
            _non_empty(item, f"evidence_texts[{index}]")
            for index, item in enumerate(evidence_texts)
        ]
        ability_key = _normalize_lookup(ability)
        fact_key = _normalize_lookup(fact)
        behavior_key = _normalize_lookup(behavior)
        evidence_keys = [_normalize_lookup(item) for item in evidence]
        scores: dict[str, int] = {}
        reasons: dict[str, list[str]] = {}

        def add(node_id: str, score: int, reason: str) -> None:
            scores[node_id] = max(score, scores.get(node_id, 0))
            trail = reasons.setdefault(node_id, [])
            if reason not in trail:
                trail.append(reason)

        def contains_any(values: Sequence[str], target: str) -> bool:
            return any(len(value) >= 2 and value in target for value in values)

        for node in self.nodes:
            canonical = _normalize_lookup(node.canonical_name)
            aliases = [_normalize_lookup(alias) for alias in node.aliases]
            if canonical == ability_key:
                add(node.id, 100, "exact_canonical")
            if ability_key in aliases:
                add(node.id, 98, "alias")
            if len(canonical) >= 2 and canonical in ability_key:
                add(node.id, 92, "ability_token")
            if contains_any(aliases, ability_key):
                add(node.id, 90, "ability_token")
            names = [canonical, *aliases]
            if contains_any(names, fact_key):
                add(node.id, 82, "fact_token")
            if contains_any(names, behavior_key):
                add(node.id, 80, "behavior_token")
            if any(contains_any(names, key) for key in evidence_keys):
                add(node.id, 78, "evidence_token")
            tags = [_normalize_lookup(tag) for tag in node.matching_tags]
            if contains_any(tags, ability_key):
                add(node.id, 88, "ability_token")
            if contains_any(tags, fact_key):
                add(node.id, 70, "fact_token")
            if contains_any(tags, behavior_key):
                add(node.id, 69, "behavior_token")
            if any(contains_any(tags, key) for key in evidence_keys):
                add(node.id, 68, "evidence_token")
            requirement_terms = (
                node.evidence_requirements.required_all
                + node.evidence_requirements.required_any
                + node.evidence_requirements.mechanism_any
            )
            normalized_terms = [
                _normalize_lookup(term)
                for term in requirement_terms
                if len(_normalize_lookup(term)) >= 3
            ]
            contexts = [ability_key, fact_key, behavior_key, *evidence_keys]
            if any(contains_any(normalized_terms, context) for context in contexts):
                add(node.id, 64, "requirement_trigger")

        initial_ids = sorted(
            scores,
            key=lambda node_id: (-scores[node_id], self._order[node_id]),
        )
        # Only strong name-level matches may fan out through graph relations.
        # Fact/behavior/evidence matches remain selectable but do not recursively
        # introduce an entire neighbouring ability family.
        seed_ids = [node_id for node_id in initial_ids if scores[node_id] >= 88]
        for node_id in seed_ids:
            node = self._by_id[node_id]
            if node.parent_id is not None:
                add(node.parent_id, 58, "parent")
            for child in self.children_of(node_id):
                add(child.id, 50, "child")
            for related_id in node.related_tools:
                add(related_id, 56, "related_tool")
            for related_id in node.related_knowledge:
                add(related_id, 56, "related_knowledge")
            for compound_id in node.allowed_compounds:
                add(compound_id, 54, "allowed_compound")
            for sibling_id in node.sibling_ids:
                add(sibling_id, 46, "safe_fallback")

        ordered = sorted(
            scores,
            key=lambda node_id: (-scores[node_id], self._order[node_id]),
        )
        selected_ids = ordered[:max_nodes]
        nodes = [self._by_id[node_id] for node_id in selected_ids]
        trace = [
            TaxonomySelectionTrace(
                node_id=node_id,
                score=scores[node_id],
                reasons=tuple(reasons[node_id]),
            )
            for node_id in selected_ids
        ]
        return nodes, trace

    def to_dict(self) -> dict[str, Any]:
        return {
            "taxonomy_version": self.taxonomy_version,
            "version": self.version,
            "status": self.status,
            "frozen_at": self.frozen_at,
            "scope": self.scope,
            "node_count": self.node_count,
            "node_types": list(self.node_types),
            "levels": dict(self.levels),
            "evidence_requirement_templates": {
                template_id: requirement.to_dict()
                for template_id, requirement in self.evidence_requirement_templates.items()
            },
            "nodes": [node.to_dict() for node in self.nodes],
            "evidence_mappings": [
                mapping.to_dict() for mapping in self.evidence_mappings
            ],
            "compound_rules": [rule.to_dict() for rule in self.compound_rules],
        }

    def serialize(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
