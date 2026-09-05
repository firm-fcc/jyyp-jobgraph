"""Thin orchestration layer for V3 Evidence -> Team Skill linking.

Online path: grounded evidence candidate generation -> constrained candidate
shortlisting -> constrained semantic verification -> deterministic audit ->
candidate-level aggregation. Legacy Semantic Shadow is intentionally not a
runtime dependency.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from extractor.agentic_schema import CandidateAbility
from extractor.team_skill_auditor_v3 import (
    AggregatedTeamSkill,
    AuditedTeamSkillAssessment,
    TeamSkillAuditorV3,
    aggregate_team_skill_assessments,
)
from extractor.team_skill_candidate_generator_v3 import (
    TeamSkillCandidateGeneratorV3,
    TeamSkillCandidatePool,
)
from extractor.team_skill_fallback_selector_v3 import (
    FallbackSelectorContractError,
    FallbackSelectorError,
    FallbackTeamSkillSelectorV3,
)
from extractor.team_skill_verifier_v3 import EvidenceSkillVerifierV3


@dataclass(frozen=True)
class V3LinkDiagnostics:
    evidence_candidate_count: int
    grounded_evidence_candidate_count: int
    ungrounded_candidate_skip_count: int
    verifier_call_count: int
    verifier_contract_retry_count: int
    verifier_usage: Mapping[str, int]
    fallback_candidate_pool_count: int
    fallback_skill_universe_count: int
    fallback_selector_call_count: int
    fallback_selector_contract_retry_count: int
    fallback_selector_failure_count: int
    fallback_selected_skill_count: int
    fallback_selector_usage: Mapping[str, int]
    full_fallback_verifier_call_count: int
    audited_assessment_count: int


@dataclass(frozen=True)
class V3LinkResult:
    candidate_id: str
    aggregated_skills: tuple[AggregatedTeamSkill, ...]
    audited_assessments: tuple[AuditedTeamSkillAssessment, ...]
    diagnostics: V3LinkDiagnostics


def _accumulate_usage(target: dict[str, int], usage: Mapping[str, Any] | None) -> None:
    if not usage:
        return
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        target[key] = target.get(key, 0) + int(value)


class TeamSkillLinkingPipelineV3:
    def __init__(
        self,
        candidate_generator: TeamSkillCandidateGeneratorV3,
        verifier: EvidenceSkillVerifierV3,
        auditor: TeamSkillAuditorV3,
        fallback_selector: FallbackTeamSkillSelectorV3 | None = None,
    ) -> None:
        self.candidate_generator = candidate_generator
        self.verifier = verifier
        self.auditor = auditor
        self.fallback_selector = fallback_selector

    @staticmethod
    def _chunks(values: Sequence, size: int):
        for start in range(0, len(values), size):
            yield values[start:start + size]

    def link(
        self,
        *,
        candidate_id: str,
        evidence_candidates: Sequence[CandidateAbility],
        semantic_scores_by_source: Mapping[str, Mapping[str, float]] | None = None,
        top_k: int = 8,
        include_auxiliary: bool = False,
        max_skills_per_verifier_call: int = 10,
        max_parallel_verifier_calls: int = 1,
        selector_batch_size: int = 0,
        max_parallel_selector_calls: int = 1,
    ) -> V3LinkResult:
        if max_skills_per_verifier_call <= 0:
            raise ValueError("max_skills_per_verifier_call must be positive")
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if max_parallel_verifier_calls <= 0:
            raise ValueError("max_parallel_verifier_calls must be positive")
        if selector_batch_size < 0:
            raise ValueError("selector_batch_size must not be negative")
        if max_parallel_selector_calls <= 0:
            raise ValueError("max_parallel_selector_calls must be positive")

        audited: list[AuditedTeamSkillAssessment] = []
        calls = 0
        contract_retries = 0
        verifier_usage: dict[str, int] = {}
        grounded_count = 0
        skipped_ungrounded = 0

        fallback_count = 0
        fallback_skill_universe_count = 0
        selector_calls = 0
        selector_retries = 0
        selector_failures = 0
        selector_usage: dict[str, int] = {}
        fallback_selected_skill_count = 0
        full_fallback_verifier_calls = 0

        # r4.3: build lexical pools for every grounded evidence unit, then run one
        # semantic selector batch for the whole resume. A lexical hit must no longer
        # suppress a second valid Team Skill.
        lexical_work: list[tuple[CandidateAbility, tuple]] = []
        lexical_miss_work: list[CandidateAbility] = []
        for evidence_candidate in evidence_candidates:
            has_grounded = any(
                item.start is not None and item.end is not None
                for item in evidence_candidate.evidence
            )
            if not has_grounded:
                skipped_ungrounded += 1
                continue
            grounded_count += 1

            semantic_scores = None
            if semantic_scores_by_source is not None:
                semantic_scores = semantic_scores_by_source.get(
                    evidence_candidate.candidate_id
                )
            pool = self.candidate_generator.generate(
                evidence_candidate,
                top_k=top_k,
                semantic_scores=semantic_scores,
                include_auxiliary=include_auxiliary,
                recall_safe_fallback=False,
            )
            lexical_work.append((evidence_candidate, pool.skills))
            if not pool.skills:
                fallback_count += 1
                candidate_universe = (
                    self.candidate_generator.registry.all()
                    if include_auxiliary
                    else self.candidate_generator.registry.primary()
                )
                fallback_skill_universe_count += len(candidate_universe)
                lexical_miss_work.append(evidence_candidate)

        selected_by_source: dict[str, tuple] = {}
        selector_succeeded = False
        if lexical_work and self.fallback_selector is not None:
            candidate_universe = (
                self.candidate_generator.registry.all()
                if include_auxiliary
                else self.candidate_generator.registry.primary()
            )
            all_candidates = [item for item, _ in lexical_work]
            candidate_by_source = {item.candidate_id: item for item in all_candidates}

            def _apply_selector_result(selector_result) -> None:
                nonlocal fallback_selected_skill_count, selector_retries
                selector_retries += selector_result.contract_retry_count
                _accumulate_usage(selector_usage, selector_result.usage)
                for selection in selector_result.selections:
                    evidence_candidate = candidate_by_source[
                        selection.source_candidate_ability_id
                    ]
                    context_check = getattr(self.candidate_generator, "allows_skill", None)
                    selected = []
                    for skill_id in selection.team_skill_ids:
                        skill = self.candidate_generator.registry.get(skill_id)
                        if callable(context_check) and not context_check(
                            evidence_candidate, skill
                        ):
                            continue
                        selected.append(skill)
                    skills = tuple(selected)
                    selected_by_source[selection.source_candidate_ability_id] = skills
                    fallback_selected_skill_count += len(skills)

            def _select(batch):
                return self.fallback_selector.select(
                    candidate_id=candidate_id,
                    evidence_candidates=batch,
                    candidate_skills=candidate_universe,
                    max_candidates=min(4, top_k, len(candidate_universe)),
                )

            if selector_batch_size and len(all_candidates) > selector_batch_size:
                # 主动分批并发。selector 的判定逐条独立：提示词要求为每条
                # evidence unit 各选 1~max_candidates 个 Skill，条与条之间不作
                # 权衡，故分批不改变判定口径。而一次全量调用要在单次推理里
                # 过一遍“证据条数 × Skill 全域”个组合，思维链随之膨胀：实测
                # 12 条一次需八十至三百秒，4 条一批只需十五秒上下。
                #
                # 默认不启用（selector_batch_size 为 0 时走下面的整体调用）：
                # 输入切分后模型所见的上下文毕竟不同，召回结果未必逐条等同，
                # 是否接受这一差异由部署方决定。
                def _try_select(batch):
                    try:
                        return _select(batch), None
                    except FallbackSelectorError as exc:
                        return None, exc

                batches = list(self._chunks(all_candidates, selector_batch_size))
                selector_workers = min(max_parallel_selector_calls, len(batches))
                if selector_workers > 1:
                    with ThreadPoolExecutor(max_workers=selector_workers) as pool:
                        outcomes = list(pool.map(_try_select, batches))
                else:
                    outcomes = [_try_select(batch) for batch in batches]

                recovered_all = True
                for selector_result, exc in outcomes:
                    selector_calls += 1
                    if exc is not None:
                        recovered_all = False
                        if isinstance(exc, FallbackSelectorContractError):
                            selector_retries += 1
                        continue
                    _apply_selector_result(selector_result)
                selector_succeeded = bool(selected_by_source)
                selector_failures = 0 if recovered_all else 1
            else:
                try:
                    selector_calls += 1
                    _apply_selector_result(_select(all_candidates))
                    selector_succeeded = True
                except FallbackSelectorError as exc:
                    if isinstance(exc, FallbackSelectorContractError):
                        selector_retries += 1

                    # r4.3.3 reliability recovery: a failed resume-wide selector must
                    # never trigger all-43 verification. Retry smaller selector batches;
                    # if a batch still fails, that batch degrades to lexical-only.
                    recovered_all = True
                    for batch in self._chunks(all_candidates, 4):
                        try:
                            selector_calls += 1
                            _apply_selector_result(_select(batch))
                        except FallbackSelectorError as batch_exc:
                            recovered_all = False
                            if isinstance(batch_exc, FallbackSelectorContractError):
                                selector_retries += 1
                    selector_succeeded = bool(selected_by_source)
                    selector_failures = 0 if recovered_all else 1


        # 核验任务先排定、后执行。一条证据对一批 Skill 的核验只依赖这两者，
        # 彼此之间没有先后关系，故可并发发出；verifier 与 auditor 均无可变
        # 状态，共享它们是安全的。计数、用量与 audited 一律在收敛阶段按排定
        # 顺序串行累加，因而并发与串行的产物逐字节一致。
        verify_tasks: list[tuple[CandidateAbility, tuple, bool]] = []

        def schedule_verification(
            evidence_candidate: CandidateAbility, skills: Sequence, *, full_fallback: bool
        ) -> None:
            for skill_batch in self._chunks(skills, max_skills_per_verifier_call):
                verify_tasks.append((evidence_candidate, tuple(skill_batch), full_fallback))

        fallback_universe = (
            self.candidate_generator.registry.all()
            if include_auxiliary
            else self.candidate_generator.registry.primary()
        )
        merged_limit = min(
            len(fallback_universe),
            max(top_k, min(max_skills_per_verifier_call, top_k + 2)),
        )

        for evidence_candidate, lexical_skills in lexical_work:
            semantic_skills = (
                selected_by_source.get(evidence_candidate.candidate_id, ())
                if selector_succeeded else ()
            )
            merged = []
            seen_ids: set[str] = set()
            # Semantic shortlist comes first so new recall candidates are not
            # truncated behind a full lexical top-k list.
            for skill in (*semantic_skills, *lexical_skills):
                if skill.code in seen_ids:
                    continue
                seen_ids.add(skill.code)
                merged.append(skill)
                if len(merged) >= merged_limit:
                    break

            if merged:
                schedule_verification(evidence_candidate, merged, full_fallback=False)
            elif evidence_candidate in lexical_miss_work and self.fallback_selector is None:
                # Backward-compatible offline path only. In normal r4.3.3 runtime
                # a selector failure degrades to lexical-only rather than verifying
                # all 43 skills, which was both costly and semantically unstable.
                schedule_verification(evidence_candidate, fallback_universe, full_fallback=True)

        def run_verification(task):
            evidence_candidate, skill_batch, _ = task
            return self.verifier.verify(
                candidate_id=candidate_id,
                evidence_candidate=evidence_candidate,
                candidate_skills=skill_batch,
            )

        workers = min(max_parallel_verifier_calls, len(verify_tasks))
        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                # map 保序且在迭代处重抛首个异常，与串行版的失败语义一致。
                verifications = list(pool.map(run_verification, verify_tasks))
        else:
            verifications = [run_verification(task) for task in verify_tasks]

        for (evidence_candidate, _, full_fallback), verification in zip(
            verify_tasks, verifications
        ):
            calls += 1
            if full_fallback:
                full_fallback_verifier_calls += 1
            contract_retries += verification.contract_retry_count
            _accumulate_usage(verifier_usage, verification.usage)
            for assessment in verification.assessments:
                audited.append(self.auditor.audit(evidence_candidate, assessment))

        aggregated = aggregate_team_skill_assessments(
            audited, self.candidate_generator.registry
        )
        return V3LinkResult(
            candidate_id=candidate_id,
            aggregated_skills=aggregated,
            audited_assessments=tuple(audited),
            diagnostics=V3LinkDiagnostics(
                evidence_candidate_count=len(evidence_candidates),
                grounded_evidence_candidate_count=grounded_count,
                ungrounded_candidate_skip_count=skipped_ungrounded,
                verifier_call_count=calls,
                verifier_contract_retry_count=contract_retries,
                verifier_usage=dict(verifier_usage),
                fallback_candidate_pool_count=fallback_count,
                fallback_skill_universe_count=fallback_skill_universe_count,
                fallback_selector_call_count=selector_calls,
                fallback_selector_contract_retry_count=selector_retries,
                fallback_selector_failure_count=selector_failures,
                fallback_selected_skill_count=fallback_selected_skill_count,
                fallback_selector_usage=dict(selector_usage),
                full_fallback_verifier_call_count=full_fallback_verifier_calls,
                audited_assessment_count=len(audited),
            ),
        )
