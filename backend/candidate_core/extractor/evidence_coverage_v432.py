"""Deterministic uncovered-evidence coverage for r4.3.2.

The LLM extractor is intentionally retained, but its stochastic omissions must
not decide whether a clearly written work/project responsibility ever reaches
the Team Skill linker. This module adds at most one compact grounded coverage
candidate per Work/Project section, using only exact resume lines that are not
already substantially covered by extracted evidence.

No LLM call is made here. Generated hint fields are placeholders only; the V4
verifier never receives them as authoritative evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Sequence

from extractor.agentic_schema import CandidateAbility, CandidateStatus, Evidence


_SECTION_HEADINGS = {
    "求职意向", "教育经历", "工作经历", "项目经历", "期刊论文", "会议论文",
    "授权专利", "荣誉奖项", "校园经历", "资格证书", "专业技能",
}
_ELIGIBLE_SECTIONS = {"简介/优势", "工作经历", "项目经历"}

_ACTION_MARKERS = (
    "负责", "研发", "设计", "实现", "开发", "搭建", "训练", "调优", "优化",
    "测试", "调试", "分析", "处理", "构建", "建立", "撰写", "评估", "评价",
    "控制", "部署", "维护", "参与", "主导", "完成", "提出", "采用", "使用",
    "利用", "制作", "采集", "识别", "检测", "规划", "验证", "迭代", "集成",
    "编程",
)
_TECH_MARKERS = (
    "算法", "代码", "编程", "开发", "系统", "软件", "硬件", "设备", "测试",
    "数据", "模型", "网络", "数据库", "AI", "机器学习", "深度学习", "视觉",
    "电路", "调试", "接口", "仿真", "控制", "信号", "OCR", "RAG", "Python",
    "C++", "Java", "Linux", "线程", "通信", "图像", "EEG", "脑电", "触觉",
    "原型", "嵌入式", "交互",
)
_RESPONSIBILITY_MARKERS = ("主要工作职责", "负责内容", "工作职责", "职责：", "职责:")
_SELF_REPORT_SUBJECT_MARKERS = ("团队", "项目", "人员", "受试者", "客户", "部门", "成员")
_SELF_REPORT_COORDINATION_MARKERS = (
    "管理", "协调", "统筹", "分工", "安排", "计划", "推进", "排期",
)
_SELF_REPORT_MONITORING_MARKERS = ("进度", "汇报", "总结", "复盘", "交付", "目标", "资源")
_RESEARCH_TOPIC_RE = re.compile(r"(?:^|[-•·]\s*)研究方向\s*[:：]")
_RESEARCH_TECH_MARKERS = (
    "AI", "ICL", "机器学习", "深度学习", "提示", "算法", "模型", "计算",
    "RAG", "优化", "网络", "视觉", "数据", "系统",
)

_RESULT_ONLY_RE = re.compile(
    r"(发表.{0,24}(论文|文章)|"
    r"(论文|文章).{0,24}(第一作者|通讯作者|SCI|EI|JCR)|"
    r"(授权|申请|产出|拥有).{0,16}(发明)?专利|"
    r"(一等奖|二等奖|三等奖|获奖|竞赛.{0,12}奖)|"
    r"(资格证书|从业资格|通过.{0,12}考试))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EvidenceCoverageResult:
    candidates: tuple[CandidateAbility, ...]
    added_candidate_count: int
    added_evidence_count: int


def _overlap_ratio(start: int, end: int, other_start: int, other_end: int) -> float:
    overlap = max(0, min(end, other_end) - max(start, other_start))
    return overlap / max(1, end - start)


def _iter_lines(resume_text: str):
    offset = 0
    section = "简介/优势"
    for raw_with_newline in resume_text.splitlines(keepends=True):
        raw = raw_with_newline.rstrip("\r\n")
        stripped = raw.strip()
        leading = len(raw) - len(raw.lstrip())
        start = offset + leading
        end = start + len(stripped)
        if stripped in _SECTION_HEADINGS:
            section = stripped
        elif stripped:
            if section == "简介/优势":
                for match in re.finditer(r"[^。！？；]+[。！？；]?", stripped):
                    unit = match.group(0).strip()
                    if unit:
                        unit_start = start + match.start() + (
                            len(match.group(0)) - len(match.group(0).lstrip())
                        )
                        yield section, unit, unit_start, unit_start + len(unit)
            else:
                yield section, stripped, start, end
        offset += len(raw_with_newline)


def _is_action_rich_self_report(text: str) -> bool:
    return (
        any(marker in text for marker in _SELF_REPORT_SUBJECT_MARKERS)
        and any(marker in text for marker in _TECH_MARKERS)
        and any(marker in text for marker in _SELF_REPORT_COORDINATION_MARKERS)
        and any(marker in text for marker in _SELF_REPORT_MONITORING_MARKERS)
    )


def _is_precise_work_research_topic(section: str, text: str) -> bool:
    if section not in {"工作经历", "项目经历"} or not _RESEARCH_TOPIC_RE.search(text):
        return False
    return sum(marker.casefold() in text.casefold() for marker in _RESEARCH_TECH_MARKERS) >= 2


def _candidate_line_score(text: str, overlap: float) -> float:
    action_count = sum(marker in text for marker in _ACTION_MARKERS)
    tech_count = sum(marker in text for marker in _TECH_MARKERS)
    responsibility_bonus = 5 if any(marker in text for marker in _RESPONSIBILITY_MARKERS) else 0
    return (
        2.0 * action_count
        + 1.0 * tech_count
        + responsibility_bonus
        + 4.0 * (1.0 - overlap)
        + min(len(text) / 100.0, 1.5)
    )


def augment_grounded_coverage_v432(
    evidence_candidates: Sequence[CandidateAbility],
    *,
    candidate_id: str,
    resume_text: str,
    max_lines_per_section: int = 4,
) -> EvidenceCoverageResult:
    """Add compact exact-span coverage candidates for omitted work/project lines."""

    existing_spans = [
        (evidence.start, evidence.end)
        for candidate in evidence_candidates
        for evidence in candidate.evidence
        if evidence.start is not None and evidence.end is not None
    ]

    by_section: dict[str, list[tuple[float, str, int, int]]] = {
        section: [] for section in _ELIGIBLE_SECTIONS
    }
    seen_text: set[str] = set()

    for section, text, start, end in _iter_lines(resume_text):
        if section not in _ELIGIBLE_SECTIONS:
            continue
        if not (8 <= len(text) <= 260):
            continue
        if _RESULT_ONLY_RE.search(text):
            continue
        action_count = sum(marker in text for marker in _ACTION_MARKERS)
        tech_count = sum(marker in text for marker in _TECH_MARKERS)
        action_rich_self_report = (
            section == "简介/优势" and _is_action_rich_self_report(text)
        )
        precise_research_topic = _is_precise_work_research_topic(section, text)
        if not action_rich_self_report and not precise_research_topic and (
            action_count < 1 or tech_count < 1
        ):
            continue

        normalized = re.sub(r"\s+", "", text).lstrip("•-·0123456789.、")
        if not normalized or normalized in seen_text:
            continue

        overlap = max(
            (_overlap_ratio(start, end, other_start, other_end)
             for other_start, other_end in existing_spans),
            default=0.0,
        )
        if overlap >= 0.60:
            continue

        seen_text.add(normalized)
        score = _candidate_line_score(text, overlap)
        if action_rich_self_report or precise_research_topic:
            score += 6.0
        by_section[section].append((score, text, start, end))

    added_candidates: list[CandidateAbility] = []
    added_evidence_count = 0

    for section in ("简介/优势", "工作经历", "项目经历"):
        section_limit = 1 if section == "简介/优势" else max_lines_per_section
        ranked = sorted(
            by_section[section],
            key=lambda item: (-item[0], item[2], item[3]),
        )[:section_limit]
        if not ranked:
            continue

        evidence = [
            Evidence(
                text=text,
                project_id="resume_full",
                start=start,
                end=end,
            )
            for _, text, start, end in ranked
        ]
        digest = hashlib.sha256(
            (candidate_id + "|" + section + "|" + "|".join(item.text for item in evidence))
            .encode("utf-8")
        ).hexdigest()
        source_id = f"coverage_v432_{digest}"

        added_candidates.append(
            CandidateAbility(
                candidate_id=source_id,
                resume_id=candidate_id,
                project_id="resume_full",
                fact=f"{section}未覆盖原文补充",
                behavior=f"{section}未覆盖原文补充",
                ability=f"{section}未覆盖原文补充",
                normalized_ability=f"{section}未覆盖原文补充",
                category={},
                evidence=evidence,
                reason="deterministic_grounded_coverage",
                confidence=1.0,
                source="deterministic_coverage_v432",
                revision_round=0,
                parent_candidate_id=None,
                status=CandidateStatus.PENDING_REVIEW,
                lineage=[source_id],
            )
        )
        added_evidence_count += len(evidence)

    return EvidenceCoverageResult(
        candidates=tuple((*evidence_candidates, *added_candidates)),
        added_candidate_count=len(added_candidates),
        added_evidence_count=added_evidence_count,
    )
