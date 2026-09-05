"""Conservative evidence grounding utilities for V4.

Grounding is deliberately stricter than semantic matching. We try literal
matching first, then a Unicode-width + whitespace normalized match only when it
is unique. Successful normalized matches are converted back to the exact
substring in the original resume so downstream verification still sees source
text, never a model paraphrase.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from extractor.agentic_schema import Evidence


@dataclass(frozen=True)
class GroundingStats:
    exact_count: int = 0
    normalized_count: int = 0
    ambiguous_count: int = 0
    unlocated_count: int = 0

    def __add__(self, other: "GroundingStats") -> "GroundingStats":
        return GroundingStats(
            exact_count=self.exact_count + other.exact_count,
            normalized_count=self.normalized_count + other.normalized_count,
            ambiguous_count=self.ambiguous_count + other.ambiguous_count,
            unlocated_count=self.unlocated_count + other.unlocated_count,
        )


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Return conservative normalized text and normalized-index -> source-index map."""
    chars: list[str] = []
    mapping: list[int] = []
    in_space = False
    for source_index, ch in enumerate(text):
        expanded = unicodedata.normalize("NFKC", ch)
        for norm_ch in expanded:
            if norm_ch.isspace():
                if chars and not in_space:
                    chars.append(" ")
                    mapping.append(source_index)
                in_space = True
                continue
            in_space = False
            chars.append(norm_ch)
            mapping.append(source_index)
    # Leading/trailing whitespace is not useful for evidence identity.
    while chars and chars[0] == " ":
        chars.pop(0)
        mapping.pop(0)
    while chars and chars[-1] == " ":
        chars.pop()
        mapping.pop()
    return "".join(chars), mapping


def _find_all(haystack: str, needle: str) -> list[int]:
    if not needle:
        return []
    result: list[int] = []
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return result
        result.append(index)
        start = index + 1


def locate_evidence_conservatively(
    evidence_texts: list[str],
    source_text: str,
    project_id: str,
    *,
    global_offset: int = 0,
) -> tuple[list[Evidence], GroundingStats]:
    evidence_objects: list[Evidence] = []
    seen: set[tuple[str, str, int | None, int | None]] = set()
    stats = GroundingStats()

    normalized_source, source_map = _normalize_with_map(source_text)

    for model_text in evidence_texts:
        literal_positions = _find_all(source_text, model_text)
        if literal_positions:
            for local_start in literal_positions:
                local_end = local_start + len(model_text)
                original_text = source_text[local_start:local_end]
                start = global_offset + local_start
                end = global_offset + local_end
                key = (original_text, project_id, start, end)
                if key in seen:
                    continue
                seen.add(key)
                evidence_objects.append(
                    Evidence(text=original_text, project_id=project_id, start=start, end=end)
                )
                stats = stats + GroundingStats(exact_count=1)
            continue

        normalized_quote, _ = _normalize_with_map(model_text)
        normalized_positions = _find_all(normalized_source, normalized_quote)
        if len(normalized_positions) == 1 and normalized_quote:
            n_start = normalized_positions[0]
            n_end = n_start + len(normalized_quote) - 1
            if n_start < len(source_map) and n_end < len(source_map):
                local_start = source_map[n_start]
                local_end = source_map[n_end] + 1
                original_text = source_text[local_start:local_end]
                start = global_offset + local_start
                end = global_offset + local_end
                key = (original_text, project_id, start, end)
                if key not in seen:
                    seen.add(key)
                    evidence_objects.append(
                        Evidence(text=original_text, project_id=project_id, start=start, end=end)
                    )
                    stats = stats + GroundingStats(normalized_count=1)
                    continue

        key = (model_text, project_id, None, None)
        if key not in seen:
            seen.add(key)
            evidence_objects.append(
                Evidence(text=model_text, project_id=project_id, start=None, end=None)
            )
            if len(normalized_positions) > 1:
                stats = stats + GroundingStats(ambiguous_count=1, unlocated_count=1)
            else:
                stats = stats + GroundingStats(unlocated_count=1)

    return evidence_objects, stats
