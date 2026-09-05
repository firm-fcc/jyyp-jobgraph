"""Deterministic evidence-source gate for r4.3.

Purpose: keep Team Skill verification focused on direct resume evidence.
Result-only evidence (papers/patents/awards/certificates) is not deleted from the
resume; it is only withheld from direct Team Skill verification. A later Warrant
resolver may reuse it under explicit rules.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Sequence

from extractor.agentic_schema import CandidateAbility


_HEADINGS = (
    "求职意向", "教育经历", "工作经历", "项目经历", "期刊论文", "会议论文",
    "授权专利", "荣誉奖项", "校园经历", "资格证书", "专业技能",
)
_RESULT_ONLY_SECTIONS = {
    "求职意向", "期刊论文", "会议论文", "授权专利", "荣誉奖项", "资格证书",
}
_CAMPUS_ADMIN_MARKERS = (
    "团委", "班级", "年会", "团务", "学生干部", "校级活动", "学院活动",
    "场地申请", "日常事务", "共青团", "活动策划", "组织活动",
)
_STRONG_CAMPUS_TECH_MARKERS = (
    "算法", "代码", "编程", "开发", "软件", "硬件", "测试", "模型", "数据库",
    "机器学习", "电路", "调试", "接口", "仿真", "控制", "信号", "OCR", "RAG",
)
_RESULT_ONLY_TEXT_RE = re.compile(
    r"(发表.{0,24}(论文|文章)|"
    r"(论文|文章).{0,24}(第一作者|通讯作者|SCI|EI|JCR)|"
    r"(授权|申请|产出|拥有).{0,16}(发明)?专利|"
    r"(一等奖|二等奖|三等奖|获奖|竞赛.{0,12}奖)|"
    r"(资格证书|从业资格|通过.{0,12}考试))",
    re.IGNORECASE,
)



@dataclass(frozen=True)
class EvidenceSourcePolicyResult:
    candidates: tuple[CandidateAbility, ...]
    dropped_evidence_count: int
    dropped_candidate_count: int


def _section_ranges(resume_text: str) -> tuple[tuple[int, int, str], ...]:
    marks: list[tuple[int, str]] = []
    for heading in _HEADINGS:
        for match in re.finditer(re.escape(heading), resume_text):
            if match.start() != 0 and resume_text[match.start() - 1] != "\n":
                continue
            if match.end() != len(resume_text) and resume_text[match.end()] != "\n":
                continue
            marks.append((match.start(), heading))
    marks = sorted(set(marks))
    ranges: list[tuple[int, int, str]] = []
    if not marks or marks[0][0] > 0:
        ranges.append((0, marks[0][0] if marks else len(resume_text), "简介/优势"))
    for index, (position, heading) in enumerate(marks):
        start = position + len(heading)
        if start < len(resume_text) and resume_text[start] == "\n":
            start += 1
        end = marks[index + 1][0] if index + 1 < len(marks) else len(resume_text)
        ranges.append((start, end, heading))
    return tuple(ranges)


def _section_for(start: int, ranges: Sequence[tuple[int, int, str]]) -> str:
    for left, right, section in ranges:
        if left <= start < right:
            return section
    return "未知"


def _is_nontechnical_campus(text: str) -> bool:
    if not any(marker in text for marker in _CAMPUS_ADMIN_MARKERS):
        return False
    # Generic nouns such as “系统” and “设备” do not turn campus administration
    # into technical delivery. Preserve only evidence with a concrete technical
    # action/object marker (for example testing, debugging, code or circuitry).
    return not any(marker in text for marker in _STRONG_CAMPUS_TECH_MARKERS)


def filter_evidence_candidates_v43(
    evidence_candidates: Sequence[CandidateAbility],
    resume_text: str,
) -> EvidenceSourcePolicyResult:
    ranges = _section_ranges(resume_text)
    filtered_candidates: list[CandidateAbility] = []
    dropped_evidence = 0
    dropped_candidates = 0

    for candidate in evidence_candidates:
        kept = []
        original_grounded = 0
        kept_grounded = 0
        for evidence in candidate.evidence:
            if evidence.start is None or evidence.end is None:
                kept.append(evidence)
                continue
            original_grounded += 1
            section = _section_for(evidence.start, ranges)
            if section in _RESULT_ONLY_SECTIONS or _RESULT_ONLY_TEXT_RE.search(evidence.text):
                dropped_evidence += 1
                continue
            if section == "校园经历" and _is_nontechnical_campus(evidence.text):
                dropped_evidence += 1
                continue
            kept.append(evidence)
            kept_grounded += 1

        if original_grounded and kept_grounded == 0:
            dropped_candidates += 1
            continue
        filtered_candidates.append(replace(candidate, evidence=kept))

    return EvidenceSourcePolicyResult(
        candidates=tuple(filtered_candidates),
        dropped_evidence_count=dropped_evidence,
        dropped_candidate_count=dropped_candidates,
    )
