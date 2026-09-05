"""Conservative extraction for the frozen Explicit Skill Mention display layer.

The 49 Team Skills are the canonical Team Skill layer.  Explicit mentions remain
fine-grained, source-grounded display items: they do not infer, create, or upgrade
Team Skill support and remain outside Team Skill metrics.
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

_TECH_ASCII_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#._/-]*")
_TECH_CN_MARKERS = (
    "编程", "开发", "算法", "数据库", "机器学习", "深度学习", "计算机视觉",
    "数据分析", "数据建模", "建模", "容器", "云平台", "操作系统", "网络",
    "打印", "切割", "渲染", "仿真", "测试", "固件", "嵌入式",
)
_SELF_CLAIM_MARKERS = (
    "丰富的", "较强", "良好", "优秀", "能力", "经验", "阅读", "写作",
    "表达", "沟通", "责任", "抗压", "学习能力", "团队", "意识",
)


def _mention_type(text: str) -> str:
    lowered = text.strip()
    if _TECH_ASCII_RE.search(lowered) or any(marker in lowered for marker in _TECH_CN_MARKERS):
        return "explicit_technical_skill"
    if any(marker in lowered for marker in _SELF_CLAIM_MARKERS):
        return "explicit_self_claim"
    return "explicit_unclassified_claim"


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
                mention_type = _mention_type(mention)
                mentions.append(
                    {
                        "text": mention,
                        "start": start,
                        "end": end,
                        "source": "explicit_skill_section",
                        "mention_type": mention_type,
                        "mapping_status": (
                            "frozen_display_only_no_team_skill_mapping"
                            if mention_type == "explicit_technical_skill"
                            else "frozen_display_only_no_team_skill_support"
                        ),
                    }
                )
                # A listed item is normally unique enough; one occurrence is enough
                # for diagnostics and avoids duplicate mentions from repeated blocks.
                break

    mentions.sort(key=lambda item: (item["start"], item["end"], item["text"]))
    return mentions
