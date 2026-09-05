"""Deterministic, offline evidence auditing for the stage 3.7 shadow path.

This module never creates a model client, reads credentials, maps to
``ReviewResult``, executes a controller action, or mutates a candidate.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from extractor.agentic_schema import CandidateAbility
from extractor.ability_taxonomy_v2 import (
    AbilityTaxonomyV2,
    CompoundRule,
    TaxonomyNode,
)
from extractor.evidence_review_agent import (
    EvidenceCatalogSpan,
    build_candidate_relocation_options,
    build_evidence_catalog,
    relocation_catalog_sha256,
)
from extractor.review_assessment_schema import (
    ASSESSMENT_SCHEMA_VERSION,
    ComponentEvidenceAssessment,
    ComponentSupport,
    CompoundAssessmentLabel,
    DeterministicEvidenceDecision,
    EvidenceAuditResult,
    EvidenceExactnessStatus,
    EvidenceSpanAudit,
    RequirementCheck,
    RequirementSupport,
    TaxonomySelectionTraceEntry,
)


AUDITOR_VERSION = "1.0"


class EvidenceAuditError(ValueError):
    """Raised for invalid deterministic auditor inputs."""


@dataclass(frozen=True)
class _RelocationSpan:
    span_id: str
    text: str
    start: int
    end: int
    project_id: str


_ACTION_TERMS = (
    "使用", "采用", "基于", "实现", "开发", "设计", "负责", "编写",
    "记录", "定位", "分析", "校验", "验证", "捕获", "部署", "执行",
    "查询", "保存", "更新", "删除", "迁移", "监控", "处理", "解析",
    "清洗", "加入", "引入", "提供", "轮询", "修复", "构建", "封装",
    "重试", "管理", "排查", "查看", "调用", "创建", "设置", "配置",
    "接入", "提出", "读取", "转换", "结构化", "分段", "提取", "回滚",
    "降级", "跳过",
)
_KNOWLEDGE_TERMS = ("了解", "熟悉", "掌握", "理解", "学习")
_TERM_ALTERNATIVES: dict[str, tuple[str, ...]] = {
    "轮询任务状态": ("轮询任务状态", "状态轮询", "轮询部署任务", "定期轮询"),
    "查询执行进度": ("查询执行进度", "任务进度", "执行进度"),
    "状态查询接口": ("状态查询接口", "状态轮询接口", "任务状态接口"),
    "定期检查任务结果": ("定期检查", "定期轮询"),
    "查询当前状态": ("查询当前状态", "查询任务状态", "查询状态", "查看任务状态"),
    "按需查询状态": ("按需查询", "需要时查询", "查询一次", "单次查询"),
    "每隔": ("每隔",),
    "定期": ("定期", "周期性", "周期查询"),
    "重复查询": ("重复查询", "反复查询", "多次查询"),
    "持续查询": ("持续查询", "持续轮询"),
    "直到完成": ("直到完成", "直至完成", "直到任务结束", "直至任务结束"),
    "任务状态": ("任务状态", "状态查询", "状态轮询"),
    "任务结果": ("任务结果", "任务完成"),
    "文件格式": ("文件格式", "格式校验", "校验格式"),
    "文件大小": ("文件大小", "大小限制", "校验大小"),
    "文件扩展名": ("文件扩展名", "扩展名", "后缀校验"),
    "文件完整性": ("文件完整性", "必填字段", "完整性校验"),
    "文件存在性": ("文件存在性", "文件存在", "存在性校验"),
    "捕获异常": ("捕获异常", "捕获解析异常", "异常捕获"),
    "错误反馈": ("错误反馈", "返回错误", "错误提示", "反馈错误"),
    "失败分支处理": ("失败分支", "异常分支", "失败处理"),
    "字段验证": ("字段验证", "字段校验", "验证字段"),
    "类型验证": ("类型验证", "类型校验", "验证字段类型"),
    "范围验证": ("范围验证", "范围校验"),
    "结构验证": ("结构验证", "结构校验", "JSON Schema", "Schema验证"),
    "业务约束验证": ("业务约束", "业务规则校验", "业务规则验证"),
    "跳过坏数据": ("跳过坏数据", "跳过无效数据", "跳过错误记录"),
    "恢复机制": ("恢复机制", "失败恢复", "错误恢复", "恢复执行"),
    "构建容器镜像": ("构建容器镜像", "构建服务镜像", "制作镜像"),
    "编写Dockerfile": ("编写Dockerfile", "编写 Dockerfile", "Dockerfile"),
    "封装容器": ("封装容器", "容器封装", "用Docker封装", "使用Docker封装"),
    "部署到环境": (
        "部署到测试环境", "部署到生产环境", "部署容器到测试环境",
        "发布到环境", "Linux环境",
    ),
    "执行部署验证": (
        "执行接口测试", "健康检查", "部署验证", "环境测试", "部署测试",
    ),
    "实现状态查询接口": (
        "实现状态查询接口", "创建状态查询接口", "实现任务状态查询端点",
        "创建进度查询接口", "实现状态API", "提供状态轮询接口",
        "设计状态轮询接口",
    ),
    "状态端点": ("状态端点", "状态查询端点", "任务状态查询端点"),
    "进度查询接口": ("进度查询接口", "任务进度接口"),
    "状态API": ("状态API", "状态 API"),
    "设计或实现异步执行": (
        "Celery", "Worker", "后台任务", "任务队列", "消息队列", "协程",
        "调度器", "并发执行",
    ),
    "记录日志": ("记录日志", "记录服务运行日志", "运行日志", "并记录"),
    "记录耗时": ("记录耗时", "运行耗时", "推理的耗时"),
    "记录错误": ("记录错误", "错误日志"),
    "写入运行信息": ("写入运行信息", "运行日志"),
    "配置日志模块": ("配置日志模块", "配置日志组件", "日志配置"),
    "统一日志字段或格式": ("统一日志字段", "统一日志格式", "日志字段和格式"),
    "记录多类日志": (
        "记录多类日志", "请求、错误、耗时", "请求日志和错误日志",
        "耗时和错误日志", "运行日志和错误日志",
    ),
    "建立日志机制": ("建立日志机制", "实现日志机制", "日志记录机制"),
    "接入日志系统": ("接入日志系统", "接入日志平台"),
    "查看日志": ("查看日志", "检查日志"),
    "根据日志检查运行情况": ("根据日志检查", "通过日志检查", "查看日志确认运行"),
    "分析日志": ("分析日志", "根据日志", "根据错误日志"),
    "根据日志定位问题": ("根据日志定位", "根据错误日志定位", "日志问题排查"),
    "排查日志异常": ("排查日志", "日志问题排查"),
    "分析错误日志": ("分析错误日志", "根据错误日志"),
    "分析故障原因": ("分析故障原因", "分析错误原因", "分析异常原因"),
    "完成问题排查": ("完成问题排查", "问题排查", "日志问题排查", "排查出"),
    "提出有效处理方案": ("提出处理方案", "提出解决方案", "调整方案", "修复"),
    "接口实现": ("接口实现", "实现接口", "接口开发", "开发接口", "封装模型推理接口"),
    "定位性能问题": ("定位", "瓶颈"),
    "优化措施": ("优化", "引入缓存", "加入Redis缓存", "调整", "降低延迟"),
    "优化前后指标": ("降至", "提升至", "从"),
    "延迟变化": ("延迟从", "延迟由", "降至"),
    "吞吐变化": ("吞吐", "QPS"),
    "资源变化": ("CPU", "内存", "显存", "资源"),
    "模块边界": ("模块边界", "层边界"),
    "组件关系": ("组件关系", "组件通信"),
    "系统分层": ("系统分层", "服务分层", "接入层", "任务层", "存储层"),
    "权限管理": ("账号权限", "权限管理"),
    "数据库": ("数据库", "MySQL", "PostgreSQL"),
    "关系型数据保存": (
        "使用MySQL保存", "使用PostgreSQL保存", "写入MySQL", "写入PostgreSQL",
        "关系型数据库保存", "关系型数据库持久化",
    ),
    "多类SQL读写": ("多类SQL读写", "SQL读写", "查询、更新", "查询和更新"),
    "查询与更新组合": ("查询、更新", "查询和更新", "查询并更新"),
    "多表操作": ("多表操作", "多表查询", "联表", "JOIN"),
    "事务提交或回滚": ("事务提交", "事务回滚", "提交或回滚", "事务"),
    "完整关系数据库业务操作": ("完整关系数据库业务操作", "完整数据库业务操作"),
    "文档解析": (
        "解析PDF", "解析Word", "解析文档", "提取文档文本", "文档内容解析",
        "文本解析",
    ),
    "文档后续处理": (
        "清洗", "转换", "结构化", "分段", "字段提取", "格式归一化",
    ),
    "编写脚本": (
        "编写脚本", "开发脚本", "实现脚本", "编写批处理脚本",
        "编写简单批处理脚本",
    ),
    "处理多个对象": (
        "批量处理", "多个文件", "多条记录", "目录中的", "多任务",
        "目录级文件处理",
    ),
    "可重复执行": ("可重复执行", "重复执行", "批处理脚本", "脚本批量处理"),
}


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", "", normalized)


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return tuple(result)


def _term_matches(term: str, text: str, template_id: str) -> bool:
    normalized_text = _normalize(text)
    alternatives = list(_TERM_ALTERNATIVES.get(term, (term,)))
    if template_id == "req.polling" and term == "每N秒":
        if re.search(r"每\s*\d+\s*(?:秒|分钟|分|小时)", text, re.I):
            return True
    if template_id == "req.status_polling_interface" and term == "实现状态查询接口":
        if re.search(
            r"(?:实现|创建|开发|提供).{0,12}(?:状态|进度).{0,8}(?:接口|端点|API)",
            text,
            re.I,
        ):
            return True
    if template_id == "req.relational_storage" and term == "关系型数据保存":
        if re.search(
            r"(?:MySQL|PostgreSQL|关系型数据库).{0,12}(?:保存|写入|持久化|存储)",
            text,
            re.I,
        ):
            return True
    if template_id == "req.relational_ops" and term in {
        "多类SQL读写", "查询与更新组合"
    }:
        operation_hits = sum(
            bool(re.search(pattern, text, re.I))
            for pattern in (r"查询|SELECT", r"更新|UPDATE", r"删除|DELETE", r"插入|INSERT")
        )
        if operation_hits >= 2:
            return True
    if template_id == "req.log_record_systematic" and term == "记录多类日志":
        category_hits = sum(
            item in text for item in ("请求", "错误", "耗时", "运行", "状态")
        )
        if category_hits >= 2 and ("日志" in text or "记录" in text):
            return True
    if template_id == "req.direct_activity" and term in {
        "实现", "开发", "处理", "执行", "负责"
    }:
        alternatives.extend(_ACTION_TERMS)
    if template_id == "req.backend_api" and term in {
        "FastAPI", "Flask", "Django", "HTTP"
    }:
        alternatives.extend(("接口开发", "实现接口", "接口实现", "封装模型推理接口"))
    if template_id == "req.backend_api" and term in {
        "实现接口", "开发接口", "定义路由", "处理请求响应"
    }:
        if re.search(r"(?:实现|开发|封装).{0,12}接口|接口.{0,6}(?:实现|开发)", text):
            return True
    if template_id == "req.relational_ops" and term in {
        "MySQL", "PostgreSQL", "关系型数据库"
    }:
        alternatives.append("SQL")
    if term == "优化前后指标":
        if re.search(r"\d+(?:\.\d+)?\s*(?:ms|毫秒|秒|%)?.{0,20}(?:降至|提升至|变为).{0,8}\d+", text, re.I):
            return True
    if term == "接口实现":
        if re.search(r"(?:实现|开发|封装).{0,12}接口|接口.{0,6}(?:实现|开发)", text):
            return True
    return any(_normalize(item) in normalized_text for item in alternatives)


def _matching_sources(
    term: str,
    sources: Sequence[tuple[str, str]],
    template_id: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    texts: list[str] = []
    span_ids: list[str] = []
    for text, span_id in sources:
        if _term_matches(term, text, template_id):
            texts.append(text)
            if span_id:
                span_ids.append(span_id)
    return _unique(texts), _unique(span_ids)


class DeterministicEvidenceAuditor:
    """Pure-rule evidence exactness and taxonomy coverage auditor."""

    def __init__(self, taxonomy: AbilityTaxonomyV2) -> None:
        if not isinstance(taxonomy, AbilityTaxonomyV2):
            raise EvidenceAuditError("taxonomy must be AbilityTaxonomyV2")
        self.taxonomy = taxonomy

    def audit(
        self,
        resume_id: str,
        resume_text: str,
        candidate: CandidateAbility,
        taxonomy_subset: Sequence[TaxonomyNode],
        relocation_options: Mapping[str, Sequence[Any]],
    ) -> EvidenceAuditResult:
        if not isinstance(resume_id, str) or not resume_id.strip():
            raise EvidenceAuditError("resume_id must be a non-empty string")
        if not isinstance(resume_text, str):
            raise EvidenceAuditError("resume_text must be a string")
        if not isinstance(candidate, CandidateAbility):
            raise EvidenceAuditError("candidate must be CandidateAbility")
        if candidate.resume_id != resume_id:
            raise EvidenceAuditError("candidate resume_id does not match audit resume_id")

        subset = self._validate_subset(taxonomy_subset)
        compound_rule = self._find_compound_rule(candidate.ability)
        if compound_rule is not None:
            existing = {node.id for node in subset}
            subset = list(subset)
            for component_id in compound_rule.component_ids:
                if component_id not in existing:
                    subset.append(self.taxonomy.get_node(component_id))
                    existing.add(component_id)

        catalog = build_evidence_catalog(resume_text)
        audits, current_sources = self._audit_current_evidence(
            resume_text, candidate, catalog)
        relocation = self._validate_relocation_options(
            resume_text, candidate, catalog, relocation_options)
        trace = self._selection_trace(candidate, subset)

        assessments = tuple(
            self._assess_component(
                node,
                candidate,
                current_sources,
                relocation,
            )
            for node in subset
        )
        by_id = {item.taxonomy_id: item for item in assessments}
        compound_label = self._compound_label(compound_rule, by_id)
        target_ids = self._target_component_ids(
            candidate, subset, compound_rule
        )
        recommended = self._recommended_relocation_ids(
            target_ids, by_id, audits, relocation
        )
        decision, blocking, notes, semantic_handoff = self._decision(
            audits,
            target_ids,
            by_id,
            compound_rule,
            compound_label,
            recommended,
        )
        if self._behavior_wording_is_stronger(candidate, current_sources):
            notes.append("behavior_wording_too_strong")
        if semantic_handoff:
            notes.append("final_ability_representation_requires_model_review")
        target_component_requires_model_review = any(
            by_id[target_id].requires_model_review
            for target_id in target_ids
            if target_id in by_id
        )
        requires_model_review = (
            decision is DeterministicEvidenceDecision.REQUIRES_MODEL_REVIEW
            or compound_label is CompoundAssessmentLabel.AMBIGUOUS
            or target_component_requires_model_review
            or semantic_handoff
        )
        exact_count = sum(
            item.exactness_status is EvidenceExactnessStatus.EXACT
            for item in audits
        )
        return EvidenceAuditResult(
            schema_version=ASSESSMENT_SCHEMA_VERSION,
            resume_id=resume_id,
            candidate_id=candidate.candidate_id,
            current_evidence_audits=audits,
            taxonomy_subset_ids=tuple(node.id for node in subset),
            taxonomy_selection_trace=trace,
            component_assessments=assessments,
            evidence_decision=decision,
            recommended_relocation_span_ids=recommended,
            compound_label=compound_label,
            blocking_issues=_unique(blocking),
            non_blocking_notes=_unique(notes),
            requires_model_review=requires_model_review,
            diagnostics={
                "auditor_version": AUDITOR_VERSION,
                "taxonomy_version": self.taxonomy.taxonomy_version,
                "catalog_protocol_version": "2.0",
                "catalog_span_count": len(catalog),
                "catalog_sha256": relocation_catalog_sha256(catalog),
                "current_evidence_count": len(candidate.evidence),
                "exact_current_evidence_count": exact_count,
                "relocation_option_count": len(relocation),
                "taxonomy_subset_size": len(subset),
                "target_component_ids": list(target_ids),
                "model_called": False,
                "controller_executed": False,
            },
        )

    def _validate_subset(
        self, taxonomy_subset: Sequence[TaxonomyNode]
    ) -> list[TaxonomyNode]:
        if isinstance(taxonomy_subset, (str, bytes)) or not isinstance(
            taxonomy_subset, Sequence
        ):
            raise EvidenceAuditError("taxonomy_subset must be a node sequence")
        result: list[TaxonomyNode] = []
        seen: set[str] = set()
        for index, node in enumerate(taxonomy_subset):
            if not isinstance(node, TaxonomyNode):
                raise EvidenceAuditError(
                    f"taxonomy_subset[{index}] must be TaxonomyNode")
            canonical = self.taxonomy.get_node(node.id)
            if canonical != node:
                raise EvidenceAuditError(
                    f"taxonomy_subset[{index}] does not belong to loaded taxonomy")
            if node.id in seen:
                raise EvidenceAuditError(f"duplicate taxonomy node: {node.id}")
            seen.add(node.id)
            result.append(node)
        return result

    def _selection_trace(
        self,
        candidate: CandidateAbility,
        subset: Sequence[TaxonomyNode],
    ) -> tuple[TaxonomySelectionTraceEntry, ...]:
        _, raw_trace = self.taxonomy.select_relevant_nodes_with_trace(
            candidate.ability,
            candidate.fact,
            candidate.behavior,
            [item.text for item in candidate.evidence if isinstance(item.text, str)
             and item.text.strip()],
            max_nodes=12,
        )
        by_id = {item.node_id: item for item in raw_trace}
        return tuple(
            TaxonomySelectionTraceEntry(
                taxonomy_id=node.id,
                score=by_id[node.id].score if node.id in by_id else 0,
                reasons=(by_id[node.id].reasons
                         if node.id in by_id else ("safe_fallback",)),
            )
            for node in subset
        )

    def _audit_current_evidence(
        self,
        resume_text: str,
        candidate: CandidateAbility,
        catalog: Sequence[EvidenceCatalogSpan],
    ) -> tuple[tuple[EvidenceSpanAudit, ...], tuple[tuple[str, str], ...]]:
        result: list[EvidenceSpanAudit] = []
        sources: list[tuple[str, str]] = []
        seen: set[tuple[str, str, Any, Any]] = set()
        for index, evidence in enumerate(candidate.evidence):
            text = evidence.text if isinstance(evidence.text, str) else ""
            project_id = (
                evidence.project_id if isinstance(evidence.project_id, str) else ""
            )
            start = evidence.start
            end = evidence.end
            key = (text, project_id, start, end)
            status: EvidenceExactnessStatus
            issues: list[str] = []
            matched_span_id: str | None = None
            if key in seen:
                status = EvidenceExactnessStatus.DUPLICATE
                issues.append("duplicate_evidence")
            elif not text:
                status = EvidenceExactnessStatus.MISSING
                issues.append("empty_evidence_text")
            elif project_id != candidate.project_id:
                status = EvidenceExactnessStatus.WRONG_PROJECT
                issues.append("evidence_project_id_does_not_match_candidate")
            elif (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end > len(resume_text)
                or start >= end
            ):
                status = EvidenceExactnessStatus.INVALID_RANGE
                issues.append("evidence_range_is_invalid")
            elif resume_text[start:end] != text:
                status = EvidenceExactnessStatus.TEXT_MISMATCH
                issues.append("resume_slice_does_not_equal_evidence_text")
            else:
                covering = [
                    span for span in catalog
                    if span.start <= start and end <= span.end
                ]
                if len(covering) == 1:
                    status = EvidenceExactnessStatus.EXACT
                    matched_span_id = covering[0].span_id
                    sources.append((text, matched_span_id))
                else:
                    status = EvidenceExactnessStatus.AMBIGUOUS
                    issues.append("exact_text_is_not_covered_by_one_catalog_span")
            seen.add(key)
            result.append(EvidenceSpanAudit(
                evidence_index=index,
                text=text,
                start=start if isinstance(start, int) and not isinstance(start, bool)
                else None,
                end=end if isinstance(end, int) and not isinstance(end, bool) else None,
                project_id=project_id,
                exactness_status=status,
                matched_catalog_span_id=matched_span_id,
                issues=tuple(issues),
            ))
        return tuple(result), tuple(sources)

    def _validate_relocation_options(
        self,
        resume_text: str,
        candidate: CandidateAbility,
        catalog: Sequence[EvidenceCatalogSpan],
        relocation_options: Mapping[str, Sequence[Any]],
    ) -> tuple[_RelocationSpan, ...]:
        if not isinstance(relocation_options, Mapping):
            raise EvidenceAuditError(
                "relocation_options must be scoped by candidate_id")
        keys = set(relocation_options)
        if keys not in ({candidate.candidate_id}, set()):
            raise EvidenceAuditError(
                "relocation_options contains another candidate_id")
        raw_options = relocation_options.get(candidate.candidate_id, ())
        if isinstance(raw_options, (str, bytes)) or not isinstance(
            raw_options, Sequence
        ):
            raise EvidenceAuditError("candidate relocation options must be a sequence")
        catalog_by_id = {span.span_id: span for span in catalog}
        try:
            official_ids = {
                span.span_id for span in build_candidate_relocation_options(
                    resume_text, candidate, list(catalog)
                )
            }
        except ValueError:
            official_ids = set()
        result: list[_RelocationSpan] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_options):
            if isinstance(raw, EvidenceCatalogSpan):
                span_id = raw.span_id
                text = raw.text
                start = raw.start
                end = raw.end
                project_id = candidate.project_id
            elif isinstance(raw, Mapping):
                allowed = {
                    "span_id", "text", "start", "end", "project_id",
                    "source_type", "line_index",
                }
                required = {"span_id", "text", "start", "end", "project_id"}
                if not required.issubset(raw) or set(raw) - allowed:
                    raise EvidenceAuditError(
                        f"relocation_options[{index}] fields are invalid")
                span_id = raw["span_id"]
                text = raw["text"]
                start = raw["start"]
                end = raw["end"]
                project_id = raw["project_id"]
            else:
                raise EvidenceAuditError(
                    f"relocation_options[{index}] must be catalog span or mapping")
            if not isinstance(span_id, str) or not span_id.strip():
                raise EvidenceAuditError(f"relocation_options[{index}] span_id invalid")
            if span_id in seen:
                raise EvidenceAuditError(f"duplicate relocation span_id: {span_id}")
            seen.add(span_id)
            if span_id not in catalog_by_id or span_id not in official_ids:
                raise EvidenceAuditError(
                    f"relocation span is not available to candidate: {span_id}")
            canonical = catalog_by_id[span_id]
            if (
                isinstance(start, bool) or isinstance(end, bool)
                or not isinstance(start, int) or not isinstance(end, int)
                or text != canonical.text or start != canonical.start
                or end != canonical.end or resume_text[start:end] != text
            ):
                raise EvidenceAuditError(
                    f"relocation span is not exact: {span_id}")
            if project_id != candidate.project_id:
                raise EvidenceAuditError(
                    f"relocation span has wrong project_id: {span_id}")
            result.append(_RelocationSpan(
                span_id=span_id,
                text=text,
                start=start,
                end=end,
                project_id=project_id,
            ))
        return tuple(sorted(result, key=lambda item: (
            item.start, item.end, item.span_id
        )))

    def _evaluate_requirement(
        self,
        node: TaxonomyNode,
        sources: Sequence[tuple[str, str]],
        candidate_ability: str,
    ) -> tuple[tuple[RequirementCheck, ...], ComponentSupport,
               tuple[str, ...], tuple[str, ...]]:
        requirement = node.evidence_requirements
        template_id = requirement.template_id
        checks: list[RequirementCheck] = []
        all_text = " ".join(text for text, _ in sources)
        normalized_all = _normalize(all_text)

        def add_check(
            suffix: str,
            description: str,
            status: RequirementSupport,
            matched_texts: Sequence[str] = (),
            matched_span_ids: Sequence[str] = (),
            missing_items: Sequence[str] = (),
            shortcut_hits: Sequence[str] = (),
            deterministic: bool = True,
        ) -> None:
            checks.append(RequirementCheck(
                requirement_id=f"{template_id}.{suffix}",
                requirement_description=description,
                status=status,
                matched_texts=_unique(matched_texts),
                matched_span_ids=_unique(matched_span_ids),
                missing_items=_unique(missing_items),
                forbidden_shortcut_hits=_unique(shortcut_hits),
                deterministic=deterministic,
            ))

        if template_id == "req.compound_direct":
            add_check(
                "component_coverage",
                "复合节点需要组件级证据覆盖判断",
                RequirementSupport.REQUIRES_MODEL_REVIEW,
                missing_items=("组件级证据与同一工程活动链",),
                deterministic=False,
            )
            return tuple(checks), ComponentSupport.AMBIGUOUS, (), (
                "组件级证据与同一工程活动链",
            )

        action_sources = [
            (text, span_id) for text, span_id in sources
            if any(_normalize(term) in _normalize(text) for term in _ACTION_TERMS)
        ]
        knowledge_only = bool(sources) and not action_sources and any(
            _normalize(term) in normalized_all for term in _KNOWLEDGE_TERMS
        )
        if requirement.direct_action_required:
            add_check(
                "direct_action",
                "需要候选人直接执行行为",
                RequirementSupport.MET if action_sources else RequirementSupport.UNMET,
                matched_texts=[item[0] for item in action_sources],
                matched_span_ids=[item[1] for item in action_sources if item[1]],
                missing_items=() if action_sources else ("直接行动",),
            )
        else:
            add_check(
                "direct_action", "不要求直接行为",
                RequirementSupport.NOT_APPLICABLE,
            )

        if requirement.required_all:
            matched_texts: list[str] = []
            matched_ids: list[str] = []
            missing: list[str] = []
            for term in requirement.required_all:
                texts, ids = _matching_sources(term, sources, template_id)
                if texts:
                    matched_texts.extend(texts)
                    matched_ids.extend(ids)
                else:
                    missing.append(term)
            add_check(
                "required_all", "必须满足全部证据条件",
                RequirementSupport.MET if not missing else RequirementSupport.UNMET,
                matched_texts, matched_ids, missing,
            )
        else:
            add_check(
                "required_all", "没有 all_of 条件",
                RequirementSupport.NOT_APPLICABLE,
            )

        if requirement.required_any:
            any_texts: list[str] = []
            any_ids: list[str] = []
            for term in requirement.required_any:
                texts, ids = _matching_sources(term, sources, template_id)
                any_texts.extend(texts)
                any_ids.extend(ids)
            add_check(
                "required_any", "至少满足一项行为或对象条件",
                RequirementSupport.MET if any_texts else RequirementSupport.UNMET,
                any_texts, any_ids,
                () if any_texts else requirement.required_any,
            )
        else:
            add_check(
                "required_any", "没有 any_of 条件",
                RequirementSupport.NOT_APPLICABLE,
            )

        if requirement.mechanism_any:
            mechanism_texts: list[str] = []
            mechanism_ids: list[str] = []
            for term in requirement.mechanism_any:
                texts, ids = _matching_sources(term, sources, template_id)
                mechanism_texts.extend(texts)
                mechanism_ids.extend(ids)
            add_check(
                "mechanism_any", "至少出现一种明确机制或对象",
                RequirementSupport.MET if mechanism_texts else RequirementSupport.UNMET,
                mechanism_texts, mechanism_ids,
                () if mechanism_texts else requirement.mechanism_any,
            )
        else:
            add_check(
                "mechanism_any", "没有机制条件",
                RequirementSupport.NOT_APPLICABLE,
            )

        if node.id == "ability.cache_application":
            object_texts = [
                text for text, _ in sources if "缓存" in text
            ]
            object_ids = [
                span_id for text, span_id in sources
                if "缓存" in text and span_id
            ]
            add_check(
                "object", "需要明确缓存对象或缓存行为",
                RequirementSupport.MET if object_texts else RequirementSupport.UNMET,
                object_texts, object_ids,
                () if object_texts else ("缓存对象或缓存行为",),
            )
        else:
            add_check(
                "object", "对象要求已由节点行为或机制条件表达",
                RequirementSupport.NOT_APPLICABLE,
            )

        if knowledge_only and not requirement.knowledge_only_sufficient:
            add_check(
                "knowledge_only", "知识陈述不足以证明实践能力",
                RequirementSupport.UNMET,
                matched_texts=[item[0] for item in sources],
                matched_span_ids=[item[1] for item in sources if item[1]],
                missing_items=("实际项目行为",),
            )
        else:
            add_check(
                "knowledge_only", "知识陈述充分性检查",
                RequirementSupport.NOT_APPLICABLE,
            )

        shortcut_hits: list[str] = []
        shortcut_texts: list[str] = []
        shortcut_ids: list[str] = []
        for term in requirement.insufficient_alone:
            texts, ids = _matching_sources(term, sources, template_id)
            if texts:
                shortcut_hits.append(term)
                shortcut_texts.extend(texts)
                shortcut_ids.extend(ids)
        add_check(
            "forbidden_shortcut", "不能以不足提示词替代完整门槛",
            RequirementSupport.UNMET if shortcut_hits else RequirementSupport.NOT_APPLICABLE,
            shortcut_texts, shortcut_ids, (), shortcut_hits,
        )
        add_check(
            "forbidden_inference",
            "禁止推断提示仅作为保守审计信号，不替代完整语义判断",
            RequirementSupport.PARTIALLY_MET if shortcut_hits
            else RequirementSupport.NOT_APPLICABLE,
            shortcut_texts, shortcut_ids, (), shortcut_hits,
        )

        positive_example_hits: list[str] = []
        negative_example_hits: list[str] = []
        for text, _ in sources:
            normalized_text = _normalize(text)
            positive_example_hits.extend(
                example for example in node.examples_positive
                if normalized_text == _normalize(example)
            )
            negative_example_hits.extend(
                example for example in node.examples_negative
                if normalized_text == _normalize(example)
            )
        example_status = (
            RequirementSupport.PARTIALLY_MET
            if positive_example_hits or negative_example_hits
            else RequirementSupport.NOT_APPLICABLE
        )
        add_check(
            "example_hint",
            "正负例仅提供确定性提示，不单独决定组件支持",
            example_status,
            matched_texts=[text for text, _ in sources]
            if positive_example_hits else (),
            missing_items=(),
            shortcut_hits=tuple(
                f"negative_example:{item}" for item in negative_example_hits
            ),
        )

        base_unmet = [
            check for check in checks
            if check.status is RequirementSupport.UNMET
            and check.requirement_id.endswith((
                "direct_action", "required_all", "required_any",
                "mechanism_any", "knowledge_only",
            ))
        ]
        qualifier_failures = tuple(
            qualifier for qualifier in node.strong_qualifiers
            if _normalize(qualifier) in _normalize(candidate_ability) and base_unmet
        )
        add_check(
            "strong_qualifier", "强限定词必须达到节点完整证据门槛",
            RequirementSupport.UNMET if qualifier_failures
            else RequirementSupport.NOT_APPLICABLE,
            missing_items=qualifier_failures,
        )

        model_checks = [
            check for check in checks
            if check.status is RequirementSupport.REQUIRES_MODEL_REVIEW
        ]
        critical_unmet = [
            check for check in checks
            if check.status is RequirementSupport.UNMET
            and not check.requirement_id.endswith("forbidden_shortcut")
            and not check.requirement_id.endswith("forbidden_inference")
        ]
        positive_met = [
            check for check in checks
            if check.status is RequirementSupport.MET
        ]
        if model_checks:
            support = ComponentSupport.AMBIGUOUS
        elif critical_unmet:
            support = ComponentSupport.UNSUPPORTED
        elif positive_met:
            support = ComponentSupport.SUPPORTED
        else:
            support = ComponentSupport.AMBIGUOUS
        missing = _unique([
            item for check in checks for item in check.missing_items
        ])
        return tuple(checks), support, qualifier_failures, missing

    def _assess_component(
        self,
        node: TaxonomyNode,
        candidate: CandidateAbility,
        current_sources: Sequence[tuple[str, str]],
        relocation: Sequence[_RelocationSpan],
    ) -> ComponentEvidenceAssessment:
        current_checks, support, qualifier_failures, missing = (
            self._evaluate_requirement(node, current_sources, candidate.ability)
        )
        supported_relocation: list[str] = []
        best_checks: tuple[RequirementCheck, ...] = ()
        best_rank = -1
        rank = {
            ComponentSupport.UNSUPPORTED: 0,
            ComponentSupport.PARTIALLY_SUPPORTED: 1,
            ComponentSupport.AMBIGUOUS: 2,
            ComponentSupport.SUPPORTED: 3,
        }
        for span in relocation:
            checks, relocation_support, _, _ = self._evaluate_requirement(
                node, ((span.text, span.span_id),), candidate.ability
            )
            if rank[relocation_support] > best_rank:
                best_rank = rank[relocation_support]
                best_checks = checks
            if (
                relocation_support is ComponentSupport.SUPPORTED
                and self._is_candidate_evidence_expansion(span, current_sources)
            ):
                supported_relocation.append(span.span_id)
        matched_current = _unique([
            text for check in current_checks for text in check.matched_texts
        ])
        overall_support = (
            ComponentSupport.SUPPORTED
            if support is not ComponentSupport.SUPPORTED and supported_relocation
            else support
        )
        requires_model = overall_support is ComponentSupport.AMBIGUOUS
        return ComponentEvidenceAssessment(
            taxonomy_id=node.id,
            canonical_name=node.canonical_name,
            support=overall_support,
            current_evidence_requirement_checks=current_checks,
            relocation_requirement_checks=best_checks,
            matched_current_evidence=matched_current,
            matched_relocation_span_ids=_unique(supported_relocation),
            missing_requirements=missing,
            strong_qualifier_failures=qualifier_failures,
            requires_model_review=requires_model,
        )

    @staticmethod
    def _is_candidate_evidence_expansion(
        span: _RelocationSpan,
        current_sources: Sequence[tuple[str, str]],
    ) -> bool:
        span_text = _normalize(span.text)
        return any(
            _normalize(text) in span_text
            for text, _ in current_sources
            if text
        )

    @staticmethod
    def _current_support(
        assessment: ComponentEvidenceAssessment,
    ) -> ComponentSupport:
        checks = assessment.current_evidence_requirement_checks
        if any(check.status is RequirementSupport.REQUIRES_MODEL_REVIEW
               for check in checks):
            return ComponentSupport.AMBIGUOUS
        critical_unmet = [
            check for check in checks
            if check.status is RequirementSupport.UNMET
            and not check.requirement_id.endswith("forbidden_shortcut")
            and not check.requirement_id.endswith("forbidden_inference")
        ]
        if critical_unmet:
            return ComponentSupport.UNSUPPORTED
        if any(check.status is RequirementSupport.MET for check in checks):
            return ComponentSupport.SUPPORTED
        return ComponentSupport.AMBIGUOUS

    def _find_compound_rule(self, ability: str) -> CompoundRule | None:
        key = _normalize(ability)
        matches = [
            rule for rule in self.taxonomy.compound_rules
            if _normalize(rule.canonical_name) == key
        ]
        return matches[0] if len(matches) == 1 else None

    def _target_component_ids(
        self,
        candidate: CandidateAbility,
        subset: Sequence[TaxonomyNode],
        compound_rule: CompoundRule | None,
    ) -> tuple[str, ...]:
        if compound_rule is not None:
            return compound_rule.component_ids
        exact = self.taxonomy.find_by_canonical_name(candidate.ability)
        if exact is None:
            exact = self.taxonomy.find_by_alias(candidate.ability)
        subset_ids = {node.id for node in subset}
        if exact is not None and exact.id in subset_ids:
            return (exact.id,)
        ability_key = _normalize(candidate.ability)
        candidates: list[tuple[int, int, str]] = []
        for order, node in enumerate(subset):
            if node.node_type not in {"ability", "high_level_ability"}:
                continue
            terms = (node.canonical_name, *node.aliases, *node.matching_tags)
            best = max(
                (len(_normalize(term)) for term in terms
                 if _normalize(term) in ability_key),
                default=0,
            )
            if best:
                candidates.append((-best, order, node.id))
        return (sorted(candidates)[0][2],) if candidates else ()

    def _compound_label(
        self,
        rule: CompoundRule | None,
        assessments: Mapping[str, ComponentEvidenceAssessment],
    ) -> CompoundAssessmentLabel:
        if rule is None:
            return CompoundAssessmentLabel.NOT_COMPOUND
        components = [assessments.get(node_id) for node_id in rule.component_ids]
        if any(item is None for item in components):
            return CompoundAssessmentLabel.AMBIGUOUS
        supports = [
            self._current_support(item) for item in components if item is not None
        ]
        if any(item is ComponentSupport.AMBIGUOUS for item in supports):
            return CompoundAssessmentLabel.AMBIGUOUS
        all_supported = all(item is ComponentSupport.SUPPORTED for item in supports)
        if rule.label == "compound_supported":
            return (
                CompoundAssessmentLabel.COMPOUND_SUPPORTED if all_supported
                else CompoundAssessmentLabel.COMPOUND_UNSUPPORTED
            )
        if rule.label == "compound_unsupported":
            return (
                CompoundAssessmentLabel.SPLIT_RECOMMENDED if all_supported
                else CompoundAssessmentLabel.COMPOUND_UNSUPPORTED
            )
        if rule.label == "split_recommended":
            return (
                CompoundAssessmentLabel.SPLIT_RECOMMENDED if all_supported
                else CompoundAssessmentLabel.COMPOUND_UNSUPPORTED
            )
        return CompoundAssessmentLabel.AMBIGUOUS

    @staticmethod
    def _recommended_relocation_ids(
        target_ids: Sequence[str],
        assessments: Mapping[str, ComponentEvidenceAssessment],
        audits: Sequence[EvidenceSpanAudit],
        relocation: Sequence[_RelocationSpan],
    ) -> tuple[str, ...]:
        by_span_id = {item.span_id: item for item in relocation}
        result: list[str] = []
        for target_id in target_ids:
            assessment = assessments.get(target_id)
            if (
                assessment is None
                or DeterministicEvidenceAuditor._current_support(assessment)
                is ComponentSupport.SUPPORTED
            ):
                continue
            for span_id in assessment.matched_relocation_span_ids:
                span = by_span_id.get(span_id)
                no_op = span is not None and any(
                    audit.exactness_status is EvidenceExactnessStatus.EXACT
                    and audit.text == span.text
                    and audit.start == span.start
                    and audit.end == span.end
                    for audit in audits
                )
                if not no_op and span_id not in result:
                    result.append(span_id)
        return tuple(result)

    @staticmethod
    def _decision(
        audits: Sequence[EvidenceSpanAudit],
        target_ids: Sequence[str],
        assessments: Mapping[str, ComponentEvidenceAssessment],
        compound_rule: CompoundRule | None,
        compound_label: CompoundAssessmentLabel,
        recommended: Sequence[str],
    ) -> tuple[DeterministicEvidenceDecision, list[str], list[str], bool]:
        blocking: list[str] = []
        notes: list[str] = []
        exact_count = sum(
            item.exactness_status is EvidenceExactnessStatus.EXACT
            for item in audits
        )
        hard_invalid = [
            item for item in audits
            if item.exactness_status in {
                EvidenceExactnessStatus.INVALID_RANGE,
                EvidenceExactnessStatus.TEXT_MISMATCH,
                EvidenceExactnessStatus.WRONG_PROJECT,
                EvidenceExactnessStatus.AMBIGUOUS,
            }
        ]
        if any(item.exactness_status is EvidenceExactnessStatus.DUPLICATE
               for item in audits):
            notes.append("duplicate_current_evidence_ignored_for_coverage")
        if exact_count == 0:
            blocking.append("no_exact_current_evidence")
            return DeterministicEvidenceDecision.MISSING, blocking, notes, False
        if hard_invalid:
            blocking.append("some_current_evidence_failed_exactness")
            return (
                DeterministicEvidenceDecision.REQUIRES_MODEL_REVIEW,
                blocking, notes, False,
            )
        if not target_ids:
            notes.append("taxonomy_target_is_out_of_vocabulary")
            return (
                DeterministicEvidenceDecision.REQUIRES_MODEL_REVIEW,
                blocking, notes, False,
            )
        targets = [assessments.get(node_id) for node_id in target_ids]
        if any(item is None for item in targets):
            notes.append("taxonomy_target_component_is_missing")
            return (
                DeterministicEvidenceDecision.REQUIRES_MODEL_REVIEW,
                blocking, notes, False,
            )
        if compound_label is CompoundAssessmentLabel.AMBIGUOUS:
            notes.append("compound_semantics_require_model_review")
            return (
                DeterministicEvidenceDecision.REQUIRES_MODEL_REVIEW,
                blocking, notes, False,
            )
        supports = [
            DeterministicEvidenceAuditor._current_support(item)
            for item in targets if item is not None
        ]
        if any(item is ComponentSupport.AMBIGUOUS for item in supports):
            notes.append("component_support_requires_model_review")
            return (
                DeterministicEvidenceDecision.REQUIRES_MODEL_REVIEW,
                blocking, notes, False,
            )
        all_supported = all(item is ComponentSupport.SUPPORTED for item in supports)
        if all_supported:
            semantic_handoff = (
                compound_label is CompoundAssessmentLabel.SPLIT_RECOMMENDED
            )
            return (
                DeterministicEvidenceDecision.SUFFICIENT,
                blocking, notes, semantic_handoff,
            )
        for item in targets:
            if item is not None:
                blocking.extend(
                    f"{item.taxonomy_id}: {missing}"
                    for missing in item.missing_requirements
                )
                blocking.extend(
                    f"{item.taxonomy_id}: strong qualifier {qualifier}"
                    for qualifier in item.strong_qualifier_failures
                )
        if recommended:
            return (
                DeterministicEvidenceDecision.INSUFFICIENT_BUT_RELOCATABLE,
                blocking, notes, False,
            )
        semantic_handoff = compound_rule is not None
        return (
            DeterministicEvidenceDecision.INSUFFICIENT_AND_NOT_RELOCATABLE,
            blocking, notes, semantic_handoff,
        )

    @staticmethod
    def _behavior_wording_is_stronger(
        candidate: CandidateAbility,
        current_sources: Sequence[tuple[str, str]],
    ) -> bool:
        evidence_text = _normalize(" ".join(text for text, _ in current_sources))
        behavior = _normalize(candidate.behavior)
        return any(
            _normalize(term) in behavior and _normalize(term) not in evidence_text
            for term in ("主导", "全面负责", "解决", "优化", "架构设计")
        )
