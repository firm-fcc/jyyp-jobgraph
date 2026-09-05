"""Conservative extraction of explicit self-listed skill mentions for diagnostics.

This does not infer a Team Skill and does not affect the primary metric.  It is a
coverage guard: the behavioral EvidenceExtractionAgent intentionally focuses on
actions, so explicit tool/skill lists must remain visible until the project team
freezes whether the 49 Team Skill nodes are leaves or parent categories.
"""

from __future__ import annotations

import re
from typing import Mapping, Sequence, Any


_SKILL_HEADINGS = (
    "专业技能",
    "技能",
    "技术栈",
    "技能清单",
    "专业能力",
    "技术能力",
)
_CERTIFICATE_MARKERS = ("资格证书", "证书", "语言能力")
_SPLIT_RE = re.compile(r"[，,、；;|｜]+")


def _locate_all(text: str, needle: str) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    offset = 0
    while needle:
        index = text.find(needle, offset)
        if index < 0:
            break
        result.append((index, index + len(needle)))
        offset = index + 1
    return result


def extract_explicit_skill_mentions(
    resume_text: str,
    sections: Mapping[str, Sequence[str]] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(resume_text, str) or not resume_text.strip():
        return []

    blocks: list[str] = []
    if sections is not None:
        for block in sections.get("skills", ()):
            if isinstance(block, str) and block.strip():
                blocks.append(block)
    else:
        # Generic best-effort path for document mode: only capture the line after
        # an explicit skill heading.  It is deliberately conservative.
        lines = resume_text.splitlines()
        for index, line in enumerate(lines):
            stripped = line.strip()
            if any(stripped == heading or stripped.startswith(heading + "：") or stripped.startswith(heading + ":") for heading in _SKILL_HEADINGS):
                blocks.append("\n".join(lines[index:index + 3]))

    mentions: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for block in blocks:
        first_line = block.splitlines()[0].strip() if block.splitlines() else ""
        if any(marker in first_line for marker in _CERTIFICATE_MARKERS):
            continue

        content = block
        heading = next((h for h in _SKILL_HEADINGS if h in first_line), None)
        if heading is not None:
            # Remove only the first heading occurrence and punctuation after it.
            pos = content.find(heading)
            content = content[pos + len(heading):]
            content = content.lstrip(" ：:\r\n\t")

        pieces: list[str] = []
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            pieces.extend(part.strip() for part in _SPLIT_RE.split(line) if part.strip())

        for mention in pieces:
            if len(mention) > 80:
                continue
            for start, end in _locate_all(resume_text, mention):
                key = (mention, start, end)
                if key in seen:
                    continue
                seen.add(key)
                mentions.append(
                    {
                        "text": mention,
                        "start": start,
                        "end": end,
                        "source": "explicit_skill_section",
                        "mapping_status": "diagnostic_only_pending_skill_granularity_freeze",
                    }
                )
                # A listed item is normally unique enough; one occurrence is enough
                # for diagnostics and avoids duplicate mentions from repeated blocks.
                break

    mentions.sort(key=lambda item: (item["start"], item["end"], item["text"]))
    return mentions
