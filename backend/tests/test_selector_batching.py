"""语义召回的主动分批。

一次全量调用要在单次推理里过一遍“证据条数 × Skill 全域”个组合，思维链
随之膨胀，实测十余条证据一次需数百秒。selector 的判定本是逐条独立的，切
分输入不改变每条证据各自的选择口径，故留一条主动分批的路径。

分批默认不启用：输入切分后模型所见的上下文毕竟不同，是否接受由部署方决定。
本组用例锁定两侧行为：不启用时与此前逐字节一致（先全量、失败再按四条一批
恢复），启用时按给定批次并发发出，且任一批失败都不得退化成全量 Skill 核验。
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidate_core"))

from extractor.agentic_schema import CandidateAbility, CandidateStatus, Evidence  # noqa: E402
from extractor.team_skill_auditor_v3 import TeamSkillAuditorV3  # noqa: E402
from extractor.team_skill_candidate_generator_v3 import TeamSkillCandidatePool  # noqa: E402
from extractor.team_skill_fallback_selector_v3 import (  # noqa: E402
    FallbackSelectionResult,
    FallbackSelectorError,
    FallbackSkillSelection,
)
from extractor.team_skill_pipeline_v3 import TeamSkillLinkingPipelineV3  # noqa: E402
from extractor.team_skill_registry import TeamSkillRegistry  # noqa: E402
from extractor.team_skill_verifier_v3 import (  # noqa: E402
    ModelTeamSkillAssessment,
    TeamSkillVerificationResult,
)

CALL_LATENCY = 0.15


def candidate(cid: str) -> CandidateAbility:
    text = f"evidence-{cid}"
    return CandidateAbility(
        candidate_id=cid,
        resume_id="candidate_1",
        project_id="resume_full",
        fact=text,
        behavior=text,
        ability=text,
        normalized_ability=text,
        category={},
        evidence=[Evidence(text=text, project_id="resume_full", start=0, end=len(text))],
        reason="test",
        confidence=0.9,
        source="test",
        revision_round=0,
        parent_candidate_id=None,
        status=CandidateStatus.PENDING_REVIEW,
        lineage=[cid],
    )


class MissGenerator:
    """词法召回一律落空，逼出语义召回这条路径。"""

    def __init__(self, registry):
        self.registry = registry

    def generate(self, evidence_candidate, **kwargs):
        return TeamSkillCandidatePool(
            skills=(),
            ranked=(),
            fallback_all=False,
            retrieval_text=evidence_candidate.evidence[0].text,
            located_evidence_count=1,
        )


class RecordingSelector:
    """记录每批的规模与在飞并发数，按固定时延应答。"""

    def __init__(self, fail_ids: set[str] | None = None):
        self.lock = threading.Lock()
        self.batch_sizes: list[int] = []
        self.in_flight = 0
        self.peak_in_flight = 0
        self.fail_ids = fail_ids or set()

    def select(self, *, candidate_id, evidence_candidates, candidate_skills, max_candidates):
        with self.lock:
            self.batch_sizes.append(len(evidence_candidates))
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            time.sleep(CALL_LATENCY)
            if any(item.candidate_id in self.fail_ids for item in evidence_candidates):
                raise FallbackSelectorError("simulated batch failure")
            return FallbackSelectionResult(
                selections=tuple(
                    FallbackSkillSelection(item.candidate_id, ("T-SW-02",))
                    for item in evidence_candidates
                ),
                model="fake",
                elapsed_ms=CALL_LATENCY * 1000,
                usage=None,
                contract_retry_count=0,
            )
        finally:
            with self.lock:
                self.in_flight -= 1


class RecordingVerifier:
    def __init__(self):
        self.calls = 0
        self.skill_ids: list[str] = []

    def verify(self, *, candidate_id, evidence_candidate, candidate_skills):
        self.calls += 1
        self.skill_ids.extend(skill.code for skill in candidate_skills)
        quote = evidence_candidate.evidence[0].text
        return TeamSkillVerificationResult(
            candidate_id=candidate_id,
            source_candidate_ability_id=evidence_candidate.candidate_id,
            assessments=tuple(
                ModelTeamSkillAssessment(
                    team_skill_id=skill.code,
                    status="supported",
                    support_evidence=(quote,),
                    reason="test",
                    confidence=0.9,
                    atomic_ability=quote,
                )
                for skill in candidate_skills
            ),
            model="fake",
            elapsed_ms=0.1,
            usage=None,
            contract_retry_count=0,
        )


def build(registry, selector, verifier):
    return TeamSkillLinkingPipelineV3(
        MissGenerator(registry), verifier, TeamSkillAuditorV3(registry),
        fallback_selector=selector,
    )


class SelectorBatchingTests(unittest.TestCase):
    def test_disabled_by_default_sends_one_full_batch(self):
        registry = TeamSkillRegistry()
        selector = RecordingSelector()
        pipeline = build(registry, selector, RecordingVerifier())
        pipeline.link(
            candidate_id="candidate_1",
            evidence_candidates=[candidate(f"s{i}") for i in range(6)],
            top_k=8,
        )
        self.assertEqual(selector.batch_sizes, [6])
        self.assertEqual(selector.peak_in_flight, 1)

    def test_batching_splits_and_runs_concurrently(self):
        registry = TeamSkillRegistry()
        selector = RecordingSelector()
        verifier = RecordingVerifier()
        pipeline = build(registry, selector, verifier)
        result = pipeline.link(
            candidate_id="candidate_1",
            evidence_candidates=[candidate(f"s{i}") for i in range(6)],
            top_k=8,
            selector_batch_size=2,
            max_parallel_selector_calls=3,
        )
        self.assertEqual(sorted(selector.batch_sizes), [2, 2, 2])
        self.assertGreater(selector.peak_in_flight, 1)
        self.assertEqual(result.diagnostics.fallback_selector_call_count, 3)
        self.assertEqual(result.diagnostics.fallback_selector_failure_count, 0)
        # 每条证据都拿到了语义候选，核验因而逐条发出，未退化成全量 Skill。
        self.assertEqual(verifier.calls, 6)
        self.assertEqual(set(verifier.skill_ids), {"T-SW-02"})
        self.assertEqual(result.diagnostics.full_fallback_verifier_call_count, 0)

    def test_one_failing_batch_degrades_only_itself(self):
        registry = TeamSkillRegistry()
        selector = RecordingSelector(fail_ids={"s2"})
        verifier = RecordingVerifier()
        pipeline = build(registry, selector, verifier)
        result = pipeline.link(
            candidate_id="candidate_1",
            evidence_candidates=[candidate(f"s{i}") for i in range(6)],
            top_k=8,
            selector_batch_size=2,
            max_parallel_selector_calls=3,
        )
        self.assertEqual(result.diagnostics.fallback_selector_failure_count, 1)
        # 失败那一批（s2、s3）退回词法-only，词法又落空，故这两条不再核验；
        # 其余四条照常。任何情形下都不得触发全量 Skill 核验。
        self.assertEqual(verifier.calls, 4)
        self.assertEqual(result.diagnostics.full_fallback_verifier_call_count, 0)

    def test_batch_size_at_or_above_total_keeps_single_call(self):
        registry = TeamSkillRegistry()
        selector = RecordingSelector()
        pipeline = build(registry, selector, RecordingVerifier())
        pipeline.link(
            candidate_id="candidate_1",
            evidence_candidates=[candidate(f"s{i}") for i in range(4)],
            top_k=8,
            selector_batch_size=4,
        )
        self.assertEqual(selector.batch_sizes, [4])

    def test_invalid_batching_arguments_rejected(self):
        registry = TeamSkillRegistry()
        pipeline = build(registry, RecordingSelector(), RecordingVerifier())
        for kwargs in ({"selector_batch_size": -1}, {"max_parallel_selector_calls": 0}):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    pipeline.link(
                        candidate_id="candidate_1",
                        evidence_candidates=[candidate("s0")],
                        **kwargs,
                    )


if __name__ == "__main__":
    unittest.main()
