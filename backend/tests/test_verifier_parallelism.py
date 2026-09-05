"""Evidence-Skill 核验的并发发出。

各次核验只依赖“一条证据”与“一批 Skill”，彼此没有先后关系，串行发出时
整段耗时等于逐次相加：一份简历十余条证据，单次数十秒的推理型模型，这一步
因而占去整条抽取链的大半。本组用例锁定改后的行为：并发发出，且产物与串行
逐项一致：判定结果、计数、用量、乃至 audited 的先后次序均不因并发而变。
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
from extractor.team_skill_pipeline_v3 import TeamSkillLinkingPipelineV3  # noqa: E402
from extractor.team_skill_registry import TeamSkillRegistry  # noqa: E402
from extractor.team_skill_verifier_v3 import (  # noqa: E402
    ModelTeamSkillAssessment,
    TeamSkillVerificationResult,
)

CALL_LATENCY = 0.12
EVIDENCE_COUNT = 8
PARALLELISM = 4


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


class LexicalGenerator:
    def __init__(self, registry):
        self.registry = registry

    def generate(self, evidence_candidate, **kwargs):
        return TeamSkillCandidatePool(
            skills=(self.registry.get("T-SW-01"), self.registry.get("T-SW-02")),
            ranked=(),
            fallback_all=False,
            retrieval_text=evidence_candidate.evidence[0].text,
            located_evidence_count=1,
        )


class SlowVerifier:
    """按固定时延应答，并记录同时在飞的请求数。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.in_flight = 0
        self.peak_in_flight = 0
        self.calls = 0

    def verify(self, *, candidate_id, evidence_candidate, candidate_skills):
        with self.lock:
            self.calls += 1
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            time.sleep(CALL_LATENCY)
        finally:
            with self.lock:
                self.in_flight -= 1
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
            elapsed_ms=CALL_LATENCY * 1000,
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            contract_retry_count=1,
        )


def run(parallelism: int) -> tuple[object, SlowVerifier, float]:
    registry = TeamSkillRegistry()
    verifier = SlowVerifier()
    pipeline = TeamSkillLinkingPipelineV3(
        LexicalGenerator(registry), verifier, TeamSkillAuditorV3(registry)
    )
    began = time.perf_counter()
    result = pipeline.link(
        candidate_id="candidate_1",
        evidence_candidates=[candidate(f"s{i}") for i in range(EVIDENCE_COUNT)],
        top_k=8,
        max_parallel_verifier_calls=parallelism,
    )
    return result, verifier, time.perf_counter() - began


class VerifierParallelismTests(unittest.TestCase):
    def test_parallel_run_matches_serial_output_exactly(self):
        serial, serial_verifier, serial_seconds = run(1)
        parallel, parallel_verifier, parallel_seconds = run(PARALLELISM)

        self.assertEqual(serial_verifier.peak_in_flight, 1)
        self.assertGreater(parallel_verifier.peak_in_flight, 1)
        self.assertEqual(serial_verifier.calls, parallel_verifier.calls)

        # 逐项一致：判定内容、先后次序、计数与用量都不因并发而变。
        self.assertEqual(
            [
                (item.team_skill_id, item.source_candidate_ability_id, item.final_status)
                for item in serial.audited_assessments
            ],
            [
                (item.team_skill_id, item.source_candidate_ability_id, item.final_status)
                for item in parallel.audited_assessments
            ],
        )
        self.assertEqual(
            [item.team_skill_id for item in serial.aggregated_skills],
            [item.team_skill_id for item in parallel.aggregated_skills],
        )
        self.assertEqual(
            serial.diagnostics.verifier_call_count,
            parallel.diagnostics.verifier_call_count,
        )
        self.assertEqual(
            serial.diagnostics.verifier_contract_retry_count,
            parallel.diagnostics.verifier_contract_retry_count,
        )
        self.assertEqual(
            dict(serial.diagnostics.verifier_usage),
            dict(parallel.diagnostics.verifier_usage),
        )
        self.assertEqual(
            serial.diagnostics.audited_assessment_count,
            parallel.diagnostics.audited_assessment_count,
        )
        self.assertLess(parallel_seconds, serial_seconds)

    def test_non_positive_parallelism_rejected(self):
        registry = TeamSkillRegistry()
        pipeline = TeamSkillLinkingPipelineV3(
            LexicalGenerator(registry), SlowVerifier(), TeamSkillAuditorV3(registry)
        )
        with self.assertRaises(ValueError):
            pipeline.link(
                candidate_id="candidate_1",
                evidence_candidates=[candidate("s0")],
                max_parallel_verifier_calls=0,
            )

    def test_verifier_failure_still_propagates_under_parallelism(self):
        registry = TeamSkillRegistry()

        class ExplodingVerifier(SlowVerifier):
            def verify(self, **kwargs):
                super().verify(**kwargs)
                raise RuntimeError("verifier down")

        pipeline = TeamSkillLinkingPipelineV3(
            LexicalGenerator(registry), ExplodingVerifier(), TeamSkillAuditorV3(registry)
        )
        with self.assertRaises(RuntimeError):
            pipeline.link(
                candidate_id="candidate_1",
                evidence_candidates=[candidate(f"s{i}") for i in range(4)],
                max_parallel_verifier_calls=PARALLELISM,
            )


if __name__ == "__main__":
    unittest.main()
