"""Deterministic section/experience segmentation for V4 long resumes.

No model is used here. Internal anonymized records already contain section
blocks; this module anchors those blocks back to the full resume and splits
work/project sections at date-range lines. Every segment carries global offsets
so evidence can still be audited against the canonical resume text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence


_DATE_RANGE_RE = re.compile(
    r"^\s*(?:19|20)\d{2}(?:[./-]\d{1,2})?\s*[-–—~～至到]\s*"
    r"(?:(?:19|20)\d{2}(?:[./-]\d{1,2})?|至今|现在|Present)\s*$",
    re.IGNORECASE,
)
_RELEVANT_SECTIONS = ("work_experience", "project_experience", "research")


@dataclass(frozen=True)
class ResumeSegmentV4:
    segment_id: str
    section_type: str
    text: str
    start: int
    end: int

    def to_prompt_dict(self) -> dict[str, object]:
        return {
            "segment_id": self.segment_id,
            "section_type": self.section_type,
            "text": self.text,
        }


def _line_spans(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for line in text.splitlines(keepends=True):
        start = cursor
        cursor += len(line)
        spans.append((start, cursor, line.rstrip("\r\n")))
    if cursor < len(text):
        spans.append((cursor, len(text), text[cursor:]))
    return spans


def _split_date_entries(
    block: str,
    *,
    section_type: str,
    global_start: int,
) -> list[ResumeSegmentV4]:
    lines = _line_spans(block)
    date_indices = [i for i, (_, _, line) in enumerate(lines) if _DATE_RANGE_RE.fullmatch(line.strip())]
    if not date_indices:
        return [
            ResumeSegmentV4(
                segment_id=f"{section_type}_001",
                section_type=section_type,
                text=block,
                start=global_start,
                end=global_start + len(block),
            )
        ]

    starts: list[int] = []
    for date_i in date_indices:
        title_i = date_i - 1
        while title_i >= 0 and not lines[title_i][2].strip():
            title_i -= 1
        if title_i < 0:
            title_i = date_i
        # Work records usually follow company -> role -> date, while project
        # records follow title -> date -> role. Preserve company context when
        # possible without pulling funding-code lines into project entries.
        if section_type == "work_experience":
            company_i = title_i - 1
            while company_i >= 0 and not lines[company_i][2].strip():
                company_i -= 1
            if company_i >= 0 and lines[company_i][2].strip() not in {"工作经历", "项目经历"}:
                title_i = company_i
        starts.append(lines[title_i][0])

    # Deduplicate pathological repeated anchors while retaining order.
    unique_starts = list(dict.fromkeys(starts))
    result: list[ResumeSegmentV4] = []
    for index, local_start in enumerate(unique_starts, start=1):
        local_end = unique_starts[index] if index < len(unique_starts) else len(block)
        text = block[local_start:local_end].strip()
        if not text:
            continue
        # Recover trimmed bounds exactly.
        raw = block[local_start:local_end]
        left_trim = len(raw) - len(raw.lstrip())
        right_trimmed = raw.rstrip()
        start = global_start + local_start + left_trim
        end = global_start + local_start + len(right_trimmed)
        result.append(
            ResumeSegmentV4(
                segment_id=f"{section_type}_{index:03d}",
                section_type=section_type,
                text=block[start-global_start:end-global_start],
                start=start,
                end=end,
            )
        )
    return result


def build_internal_segments_v4(
    resume_text: str,
    sections: Mapping[str, Sequence[str]],
) -> tuple[ResumeSegmentV4, ...]:
    intervals: list[tuple[int, int]] = []
    segments: list[ResumeSegmentV4] = []

    def locate_unique_block(block: str) -> tuple[int, int] | None:
        positions: list[int] = []
        cursor = 0
        while True:
            pos = resume_text.find(block, cursor)
            if pos < 0:
                break
            positions.append(pos)
            cursor = pos + 1
        if not positions:
            return None
        # Prefer an occurrence that does not overlap a block already consumed.
        for pos in positions:
            end = pos + len(block)
            if not any(not (end <= a or pos >= b) for a, b in intervals):
                intervals.append((pos, end))
                return pos, end
        return None

    for section_type in _RELEVANT_SECTIONS:
        blocks = sections.get(section_type, ())
        section_counter = 0
        for block in blocks:
            if not isinstance(block, str) or not block.strip():
                continue
            located = locate_unique_block(block)
            if located is None:
                continue
            block_start, block_end = located
            if section_type in {"work_experience", "project_experience"}:
                split = _split_date_entries(
                    block,
                    section_type=section_type,
                    global_start=block_start,
                )
                segments.extend(split)
            else:
                section_counter += 1
                segments.append(
                    ResumeSegmentV4(
                        segment_id=f"{section_type}_{section_counter:03d}",
                        section_type=section_type,
                        text=block,
                        start=block_start,
                        end=block_end,
                    )
                )

    segments.sort(key=lambda item: (item.start, item.end, item.segment_id))
    # Segment IDs must be unique even if multiple section blocks existed.
    counters: dict[str, int] = {}
    normalized: list[ResumeSegmentV4] = []
    for item in segments:
        counters[item.section_type] = counters.get(item.section_type, 0) + 1
        normalized.append(
            ResumeSegmentV4(
                segment_id=f"{item.section_type}_{counters[item.section_type]:03d}",
                section_type=item.section_type,
                text=item.text,
                start=item.start,
                end=item.end,
            )
        )
    return tuple(normalized)
