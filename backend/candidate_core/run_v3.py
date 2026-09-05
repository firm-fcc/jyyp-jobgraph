"""Command-line entry point for the V3 pre-use audited resume skill pipeline.

This entry point intentionally runs the candidate-side skill extraction module
only. Job graph matching and proficiency bridging are separate downstream steps.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from extractor.agentic_llm_client import (
    AgenticLLMClient,
    AgenticLLMError,
    ReliableCompletionClient,
)
from extractor.document_parser_v3 import (
    DocumentParseResult,
    assess_text_quality,
    parse_file_v3,
)
from extractor.evidence_extraction_agent import EvidenceExtractionAgent, ExtractionAgentError
from extractor.extraction_reground_v4 import reground_full_resume_extraction_v4
from extractor.occupation_warrant_v431 import resolve_occupation_warrants_v431
from extractor.evidence_source_policy_v43 import filter_evidence_candidates_v43
from extractor.evidence_coverage_v432 import augment_grounded_coverage_v432
from extractor.segmented_evidence_extraction_v4 import (
    SegmentedEvidenceExtractionAgentV4,
    segment_batch_count_v4,
)
from extractor.resume_segmentation_v4 import build_internal_segments_v4
from extractor.internal_resume_record_v3 import (
    InternalResumeRecordError,
    load_internal_resume_record,
)
from extractor.team_skill_auditor_v4 import TeamSkillAuditorV4
from extractor.team_skill_candidate_generator_v3 import TeamSkillCandidateGeneratorV3
from extractor.team_skill_fallback_selector_v4 import FallbackTeamSkillSelectorV4
from extractor.team_skill_pipeline_v3 import TeamSkillLinkingPipelineV3
from extractor.team_skill_profile_v4 import build_candidate_skill_profile
from extractor.grounded_capability_trace_v4 import build_grounded_capability_trace
from extractor.explicit_skill_mentions_v4 import extract_explicit_skill_mentions
from extractor.team_skill_registry import TeamSkillRegistry
from extractor.team_skill_verifier_v4 import EvidenceSkillVerifierV4, TeamSkillVerifierError
from extractor.v3_client_adapter import JsonModeCompletionAdapter
from extractor.v3_data_split_registry import V3DataSplitRegistry


BASE_DIR = Path(__file__).resolve().parent
LONG_INTERNAL_RESUME_THRESHOLD = 2500


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(BASE_DIR / ".env", override=False)


def _env_positive_number(name: str, default: float) -> float:
    """读取正数型环境变量；缺失、非数或非正一律退回默认值。

    模型侧配置由部署方在 .env 里给定，取值不合法时以默认值继续跑，
    好过因为一处笔误让整条解析链在启动时就断掉。
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def resolve_api_url(api_url: str | None, api_base: str | None) -> str:
    """Resolve a full OpenAI-compatible chat/completions endpoint."""
    direct = (api_url or "").strip()
    if direct:
        return direct.rstrip("/")
    base = (api_base or "").strip().rstrip("/")
    if not base:
        raise ValueError("missing LLM_API_URL (or backward-compatible LLM_API_BASE)")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/chat/completions"


def _save_json(payload: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="V3 Evidence-first 候选人标准能力提取（Team Skill 49 节点）"
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--resume", help="普通简历路径：txt / docx / pdf")
    source_group.add_argument(
        "--resume-record",
        help=(
            "项目内部匿名简历 JSON（仅接受 real_resume_dataset_v1；"
            "不会接受原始 raw_visible_text 平台快照）"
        ),
    )
    parser.add_argument(
        "--candidate-id",
        default=None,
        help="候选人ID；默认使用简历文件名 stem",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="输出 JSON 路径；默认按 candidate_id 写入 outputs/v3_single/",
    )
    parser.add_argument(
        "--include-auxiliary",
        action="store_true",
        help="同时评估辅助工作品质节点。未做经历分段时仅建议用于开发观察。",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=8,
        help="有词汇召回时最多验证的 Team Skill 数量，默认 8",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Verifier 单次最多验证的 Skill 数量；Selector 失败时也限制全量 fallback 批大小，默认 10",
    )
    parser.add_argument(
        "--segment-batch-chars",
        type=int,
        default=1200,
        help="长简历分段抽取每个 LLM 批次的最大字符数；较小更稳健，默认 1200",
    )
    parser.add_argument(
        "--no-segment-cache",
        action="store_true",
        help="禁用长简历批次断点缓存。默认开启；正式首次评估无需关闭。",
    )
    parser.add_argument(
        "--allow-low-quality-parser",
        action="store_true",
        help="解析质量门失败时仍继续。默认拒绝继续，避免垃圾文本进入能力判断。",
    )
    parser.add_argument("--api-url", default=None, help="完整 chat/completions URL")
    parser.add_argument("--model", default=None, help="模型名；默认读取 LLM_MODEL")
    parser.add_argument(
        "--timeout",
        type=float,
        default=_env_positive_number("LLM_TIMEOUT", 90.0),
        help="单次模型请求的读写超时秒数；默认读取 LLM_TIMEOUT",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=int(_env_positive_number("LLM_MAX_OUTPUT_TOKENS", 32768)),
        help=(
            "单次模型请求的输出预算；默认读取 LLM_MAX_OUTPUT_TOKENS。"
            "推理型模型把思维链计入该预算，故须显著高于回答本身的长度"
        ),
    )
    parser.add_argument(
        "--verifier-parallelism",
        type=int,
        default=int(_env_positive_number("LLM_VERIFIER_PARALLELISM", 6)),
        help=(
            "Evidence-Skill 核验的并发请求数；默认读取 LLM_VERIFIER_PARALLELISM。"
            "各次核验互不依赖，串行发出时整段耗时等于逐次相加。置 1 即回到串行"
        ),
    )
    parser.add_argument(
        "--selector-batch-size",
        type=int,
        default=int(_env_positive_number("LLM_SELECTOR_BATCH_SIZE", 0)),
        help=(
            "语义召回每批处理的证据条数；默认读取 LLM_SELECTOR_BATCH_SIZE，"
            "为 0 时一次送全部证据。一次全量调用要在单次推理里过一遍"
            "“证据条数 × Skill 全域”个组合，思维链随之膨胀"
        ),
    )
    parser.add_argument(
        "--selector-parallelism",
        type=int,
        default=int(_env_positive_number("LLM_SELECTOR_PARALLELISM", 4)),
        help="语义召回分批后的并发请求数；仅在 --selector-batch-size 大于 0 时生效",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="只检查配置、49 Skill 与文件解析，不调用模型",
    )
    parser.add_argument(
        "--unlock-holdout",
        action="store_true",
        help=(
            "显式解锁未暴露 Holdout。开发阶段不要使用；仅在最终冻结测试方案后"
            "运行正式 Holdout/Blind 时使用。"
        ),
    )
    return parser


def main() -> int:
    _load_dotenv()
    args = build_arg_parser().parse_args()

    split_registry = V3DataSplitRegistry.load()
    source_category: str | None = None
    input_mode: str
    input_record_sha256: str | None = None
    sections_for_explicit = None

    if args.resume_record:
        record_path = Path(args.resume_record)
        if not record_path.is_absolute():
            record_path = (BASE_DIR / record_path).resolve()
        if not record_path.exists():
            print(f"[V3] 匿名简历记录不存在：{record_path}", file=sys.stderr)
            return 2

        # Canonical internal records are named candidate_XXXX.json. Guard the
        # holdout before doing any model work so accidental development leakage
        # becomes a visible, deliberate action rather than a silent foot-gun.
        path_candidate_id = record_path.stem
        if split_registry.is_holdout(path_candidate_id) and not args.unlock_holdout:
            print(
                f"[V3] HOLDOUT_GUARD：{path_candidate_id} 属于未暴露 Holdout，"
                "开发阶段默认禁止运行。请改用旧 Pilot/Blind 候选人；只有最终测试"
                "冻结后才使用 --unlock-holdout。",
                file=sys.stderr,
            )
            return 5
        try:
            record = load_internal_resume_record(record_path)
        except (InternalResumeRecordError, ValueError) as exc:
            print(f"[V3] 内部匿名简历 JSON 不合法：{exc}", file=sys.stderr)
            return 2

        if split_registry.is_holdout(record.candidate_id) and not args.unlock_holdout:
            print(
                f"[V3] HOLDOUT_GUARD：{record.candidate_id} 属于未暴露 Holdout，"
                "开发阶段默认禁止运行。",
                file=sys.stderr,
            )
            return 5
        if args.candidate_id and args.candidate_id.strip() != record.candidate_id:
            print(
                "[V3] --candidate-id 与内部匿名记录 candidate_id 不一致；"
                "为避免样本身份错位，内部记录模式禁止改名。",
                file=sys.stderr,
            )
            return 2
        candidate_id = record.candidate_id
        source_category = record.source_category
        input_record_sha256 = record.file_sha256
        sections_for_explicit = record.sections
        quality = assess_text_quality(record.resume_text)
        parsed = DocumentParseResult(
            path=str(record_path),
            parser="internal_resume_record_v1",
            text=record.resume_text,
            quality=quality,
        )
        resume_path = record_path
        input_mode = "internal_anonymized_record"
    else:
        resume_path = Path(args.resume)
        if not resume_path.is_absolute():
            resume_path = (BASE_DIR / resume_path).resolve()
        if not resume_path.exists():
            print(f"[V3] 简历文件不存在：{resume_path}", file=sys.stderr)
            return 2
        parsed = parse_file_v3(resume_path)
        candidate_id = (args.candidate_id or resume_path.stem).strip()
        input_mode = "document_file"

    if (
        args.top_k <= 0
        or args.batch_size <= 0
        or args.segment_batch_chars <= 0
        or args.verifier_parallelism <= 0
        or args.selector_parallelism <= 0
        or args.selector_batch_size < 0
    ):
        print(
            "[V3] --top-k、--batch-size、--segment-batch-chars、"
            "--verifier-parallelism 和 --selector-parallelism 必须为正整数，"
            "--selector-batch-size 不得为负",
            file=sys.stderr,
        )
        return 2

    registry = TeamSkillRegistry()
    if not candidate_id:
        print("[V3] candidate_id 不能为空", file=sys.stderr)
        return 2

    prefix = (
        f"[V3] input=internal_record candidate={candidate_id} "
        f"category={source_category} "
        if input_mode == "internal_anonymized_record"
        else "[V3] "
    )
    print(
        prefix
        + f"parser={parsed.parser} chars={parsed.quality.char_count} "
        + f"quality={'PASS' if parsed.quality.passed else 'FALLBACK_REQUIRED'}"
    )
    if parsed.quality.flags:
        print("[V3] parser flags:", ", ".join(parsed.quality.flags))
    if parsed.quality.fallback_required and not args.allow_low_quality_parser:
        print(
            "[V3] 解析质量门未通过。当前预使用版不会把低质量文本静默送入 LLM。"
            "复杂/扫描 PDF 请先转换为可检索 PDF/DOCX，或确认后使用 "
            "--allow-low-quality-parser。",
            file=sys.stderr,
        )
        return 3

    precomputed_segments = ()
    if (
        input_mode == "internal_anonymized_record"
        and len(parsed.text) >= LONG_INTERNAL_RESUME_THRESHOLD
        and sections_for_explicit is not None
    ):
        precomputed_segments = build_internal_segments_v4(parsed.text, sections_for_explicit)

    if args.preflight:
        split_name = split_registry.split_for(candidate_id)
        preflight_mode = "segment_aware_batched" if precomputed_segments else "full_resume"
        preflight_batches = (
            segment_batch_count_v4(precomputed_segments, args.segment_batch_chars)
            if precomputed_segments else 0
        )
        print(
            f"[V3] registry={registry.version} skills={len(registry)} "
            f"split={split_name} extraction_mode={preflight_mode} "
            f"segments={len(precomputed_segments)} batches={preflight_batches} preflight=PASS"
        )
        return 0

    api_key = os.getenv("LLM_API_KEY", "").strip()
    model = (args.model or os.getenv("LLM_MODEL", "")).strip()
    try:
        api_url = resolve_api_url(
            args.api_url or os.getenv("LLM_API_URL"),
            os.getenv("LLM_API_BASE"),
        )
    except ValueError as exc:
        print(f"[V3] LLM 配置错误：{exc}", file=sys.stderr)
        return 4
    if not api_key or not model:
        print(
            "[V3] 缺少 LLM_API_KEY 或 LLM_MODEL。复制 .env.example 为 .env 后填写。",
            file=sys.stderr,
        )
        return 4

    base_client = AgenticLLMClient(
        api_key=api_key,
        base_url=api_url,
        model=model,
        timeout=args.timeout,
    )
    reliable = ReliableCompletionClient(
        base_client,
        max_technical_retries=2,
        backoff_seconds=(1.0, 2.0),
    )
    json_client = JsonModeCompletionAdapter(
        reliable,
        max_tokens=args.max_output_tokens,
        min_output_tokens=args.max_output_tokens,
    )

    # Stage A: open evidence candidate discovery. Long internal records use
    # deterministic experience segmentation and bounded small-batch LLM calls;
    # short/ordinary inputs keep the proven full-resume fast path.
    extraction_mode = "full_resume"
    extraction_segments = precomputed_segments
    try:
        if extraction_segments:
            extraction_mode = "segment_aware_batched"
            segment_cache_dir = None if args.no_segment_cache else BASE_DIR / "cache" / "segmented_evidence_v4"
            extractor = SegmentedEvidenceExtractionAgentV4(
                json_client,
                cache_dir=segment_cache_dir,
                cache_namespace=f"r4_2_3:{model}",
            )
            extraction = extractor.extract(
                resume_id=candidate_id,
                segments=extraction_segments,
                max_batch_chars=args.segment_batch_chars,
            )
        else:
            extractor = EvidenceExtractionAgent(json_client)
            extraction = extractor.extract(
                resume_id=candidate_id,
                resume_text=parsed.text,
                project_id="resume_full",
            )
            extraction = reground_full_resume_extraction_v4(extraction, parsed.text)
    except (ExtractionAgentError, AgenticLLMError) as exc:
        print(f"[V3] evidence extraction failed safely: {exc}", file=sys.stderr)
        if extraction_segments and not args.no_segment_cache:
            print(
                "[V3] 已成功完成的长简历子批次会保存在 cache\\segmented_evidence_v4；"
                "修复网络后直接重复同一命令即可断点复用。",
                file=sys.stderr,
            )
        return 5
    coverage = augment_grounded_coverage_v432(
        extraction.candidates,
        candidate_id=candidate_id,
        resume_text=parsed.text,
    )
    evidence_policy = filter_evidence_candidates_v43(coverage.candidates, parsed.text)
    linking_candidates = evidence_policy.candidates
    grounded_candidates = [
        candidate for candidate in linking_candidates
        if any(item.start is not None and item.end is not None for item in candidate.evidence)
    ]

    # Stage B: constrained linking to the shared 49-node Team Skill registry.
    generator = TeamSkillCandidateGeneratorV3(registry)
    verifier = EvidenceSkillVerifierV4(json_client)
    auditor = TeamSkillAuditorV4(registry)
    fallback_selector = FallbackTeamSkillSelectorV4(json_client)
    pipeline = TeamSkillLinkingPipelineV3(
        generator, verifier, auditor, fallback_selector=fallback_selector
    )
    try:
        link_result = pipeline.link(
            candidate_id=candidate_id,
            evidence_candidates=linking_candidates,
            top_k=args.top_k,
            include_auxiliary=args.include_auxiliary,
            max_skills_per_verifier_call=args.batch_size,
            max_parallel_verifier_calls=args.verifier_parallelism,
            selector_batch_size=args.selector_batch_size,
            max_parallel_selector_calls=args.selector_parallelism,
        )
    except (TeamSkillVerifierError, AgenticLLMError) as exc:
        print(f"[V3] Team Skill linking failed safely: {type(exc).__name__}", file=sys.stderr)
        print(
            "[V3] 长简历 Evidence Extraction 的严格批次缓存已保留；直接重复同一命令不会重新请求已缓存抽取批次。",
            file=sys.stderr,
        )
        return 6
    profile = build_candidate_skill_profile(
        candidate_id=candidate_id,
        evidence_candidates=linking_candidates,
        audited_assessments=link_result.audited_assessments,
        registry=registry,
        metadata={
            "runtime_version": "v3_run_ready_r4_3_4",
            "input_mode": input_mode,
            "resume_path": str(resume_path),
            "source_category": source_category,
            "data_split": split_registry.split_for(candidate_id),
            "input_record_sha256": input_record_sha256,
            "parser": parsed.parser,
            "parser_quality_flags": list(parsed.quality.flags),
            "include_auxiliary": bool(args.include_auxiliary),
            "proficiency_status": "not_run_in_preuse_entrypoint",
            "evidence_extraction_mode": extraction_mode,
            "experience_segmentation_active": bool(extraction_segments),
        },
    )

    profile, activated_warrants = resolve_occupation_warrants_v431(
        candidate_id=candidate_id,
        resume_text=parsed.text,
        profile=profile,
        team_skill_registry=registry,
    )

    grounded_capability_trace = build_grounded_capability_trace(
        linking_candidates, link_result.audited_assessments
    )
    explicit_skill_mentions = extract_explicit_skill_mentions(
        parsed.text, sections_for_explicit
    )

    unlocated_evidence_diagnostics = [
        {
            "source_candidate_ability_id": candidate.candidate_id,
            "source_experience_id": candidate.project_id,
            "ability_hint_non_authoritative": candidate.ability,
            "model_evidence_text": evidence.text,
        }
        for candidate in extraction.candidates
        for evidence in candidate.evidence
        if evidence.start is None or evidence.end is None
    ]

    output_path = Path(
        args.output or f"outputs/v3_single/{candidate_id}_v3.json"
    )
    if not output_path.is_absolute():
        output_path = (BASE_DIR / output_path).resolve()
    output = {
        "schema_version": "resume_capability_v3_run_ready_r4_3_4",
        "candidate_skill_profile": profile.to_dict(),
        "grounded_capability_candidates": grounded_capability_trace,
        "explicit_skill_mentions": explicit_skill_mentions,
        "warrant_support": [item.to_dict() for item in activated_warrants],
        "diagnostics": {
            "document": {
                "parser": parsed.parser,
                "quality": {
                    "passed": parsed.quality.passed,
                    "fallback_required": parsed.quality.fallback_required,
                    "flags": list(parsed.quality.flags),
                    "char_count": parsed.quality.char_count,
                    "nonempty_line_count": parsed.quality.nonempty_line_count,
                    "readable_char_ratio": parsed.quality.readable_char_ratio,
                    "page_count": parsed.quality.page_count,
                    "empty_page_count": parsed.quality.empty_page_count,
                    "empty_page_ratio": parsed.quality.empty_page_ratio,
                },
            },
            "evidence_extraction": {
                **extraction.diagnostics_dict(),
                "mode": extraction_mode,
                "segment_count": len(extraction_segments),
                "segments": [
                    {
                        "segment_id": item.segment_id,
                        "section_type": item.section_type,
                        "start": item.start,
                        "end": item.end,
                        "char_count": len(item.text),
                    }
                    for item in extraction_segments
                ],
                "unlocated_evidence_details": unlocated_evidence_diagnostics,
            },
            "grounded_candidate_count": len(grounded_candidates),
            "deterministic_evidence_coverage": {
                "added_candidate_count": coverage.added_candidate_count,
                "added_evidence_count": coverage.added_evidence_count,
            },
            "warrant_support": {
                "activated_count": len(activated_warrants),
                "applied_count": sum(1 for item in activated_warrants if item.applied_to_profile),
            },
            "evidence_source_policy": {
                "dropped_evidence_count": evidence_policy.dropped_evidence_count,
                "dropped_candidate_count": evidence_policy.dropped_candidate_count,
            },
            "team_skill_linking": {
                "evidence_candidate_count": link_result.diagnostics.evidence_candidate_count,
                "grounded_evidence_candidate_count": link_result.diagnostics.grounded_evidence_candidate_count,
                "ungrounded_candidate_skip_count": link_result.diagnostics.ungrounded_candidate_skip_count,
                "verifier_call_count": link_result.diagnostics.verifier_call_count,
                "verifier_contract_retry_count": link_result.diagnostics.verifier_contract_retry_count,
                "verifier_usage": dict(link_result.diagnostics.verifier_usage),
                "fallback_candidate_pool_count": link_result.diagnostics.fallback_candidate_pool_count,
                "fallback_skill_universe_count": link_result.diagnostics.fallback_skill_universe_count,
                "fallback_selector_call_count": link_result.diagnostics.fallback_selector_call_count,
                "fallback_selector_contract_retry_count": link_result.diagnostics.fallback_selector_contract_retry_count,
                "fallback_selector_failure_count": link_result.diagnostics.fallback_selector_failure_count,
                "fallback_selected_skill_count": link_result.diagnostics.fallback_selected_skill_count,
                "fallback_selector_usage": dict(link_result.diagnostics.fallback_selector_usage),
                "full_fallback_verifier_call_count": link_result.diagnostics.full_fallback_verifier_call_count,
                "audited_assessment_count": link_result.diagnostics.audited_assessment_count,
            },
            "transport_retry": reliable.retry_diagnostics().to_dict(),
        },
    }
    _save_json(output, output_path)

    supported = [
        item for item in profile.assessments
        if item.status == "supported" and item.inference_mode == "direct_behavior"
    ]
    print(
        f"[V3] extraction_mode={extraction_mode} segments={len(extraction_segments)} "
        f"evidence candidates={len(extraction.candidates)} grounded={len(grounded_candidates)}"
    )
    if extraction.warnings:
        print("[V3] extraction warnings=" + "; ".join(extraction.warnings))
    print(
        f"[V3] fallback pools={link_result.diagnostics.fallback_candidate_pool_count} "
        f"selector_calls={link_result.diagnostics.fallback_selector_call_count} "
        f"verifier_calls={link_result.diagnostics.verifier_call_count}"
    )
    print(f"[V3] explicit skill mentions={len(explicit_skill_mentions)} (diagnostic only)")
    extraction_tokens = (extraction.usage or {}).get("total_tokens")
    selector_tokens = link_result.diagnostics.fallback_selector_usage.get("total_tokens")
    verifier_tokens = link_result.diagnostics.verifier_usage.get("total_tokens")
    print(
        f"[V3] tokens extraction={extraction_tokens if extraction_tokens is not None else 'n/a'} "
        f"selector={selector_tokens if selector_tokens is not None else 0} "
        f"verifier={verifier_tokens if verifier_tokens is not None else 0}"
    )
    print(f"[V3] supported primary/direct skills={len(supported)}")
    print(f"[V3] output={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
