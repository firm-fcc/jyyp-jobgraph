"""熟练度定级的并发发出与计时落盘。

定级逐项调模型，各项之间没有先后关系。此前逐项串行，一次比对的耗时等于
逐项相加，且整段不留任何计时：久候不归时无从判断是停在哪一项、还是根本
没发出请求。本组用例锁定改后的行为：并发发出、产物与串行一致、单项失败
仍只作废该项，以及默认落一份阶段计时。
"""

from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "candidate_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.services import proficiency_service as ps  # noqa: E402
from extractor.team_skill_schema_v3 import CandidateSkillProfile  # noqa: E402

CALL_LATENCY = 0.12
SKILL_IDS = ("T-AI-01", "T-AI-03", "T-SW-01", "T-SW-02", "T-DA-02", "T-AI-08")


def _profile_with(skill_ids):
    """一份只含 supported 能力的候选人画像，定级会逐项覆盖到它们。"""
    return CandidateSkillProfile.from_dict(
        {
            "candidate_id": "demo_candidate",
            "skill_registry_version": "0.4",
            "assessments": [
                {
                    "candidate_id": "demo_candidate",
                    "team_skill_id": sid,
                    "team_skill_name": sid,
                    "status": "supported",
                    "inference_mode": "direct_behavior",
                    "evidence": [
                        {
                            "text": "独立训练并优化深度学习模型，完成部署验证。",
                            "source_experience_id": "demo_project",
                            "start": 0,
                            "end": 22,
                            "fact": "",
                            "behavior": "",
                            "context": "",
                            "result": "",
                        }
                    ],
                    "reason": "demo fixture",
                    "confidence": 0.95,
                    "atomic_abilities": ["训练并优化深度学习模型"],
                    "audit_flags": [],
                }
                for sid in skill_ids
            ],
            "metadata": {"schema_version": "candidate_skill_profile_v4_3_4"},
        }
    )


class SlowEvaluator:
    """按固定时延应答，并记录同时在飞的请求数。"""

    lock = threading.Lock()
    in_flight = 0
    peak_in_flight = 0
    calls = 0

    @classmethod
    def reset(cls):
        cls.in_flight = 0
        cls.peak_in_flight = 0
        cls.calls = 0

    def evaluate(self, ability, evidence, audit):
        cls = type(self)
        with cls.lock:
            cls.calls += 1
            cls.in_flight += 1
            cls.peak_in_flight = max(cls.peak_in_flight, cls.in_flight)
        try:
            time.sleep(CALL_LATENCY)
        finally:
            with cls.lock:
                cls.in_flight -= 1
        sid = ability.category["team_skill_id"]
        return SimpleNamespace(
            final_level="P2",
            to_dict=lambda: {"team_skill_id": sid, "final_level": "P2"},
        )


class ProficiencyParallelismTests(unittest.TestCase):
    def setUp(self):
        self._saved = ps._build_evaluator
        ps._build_evaluator = lambda _trace=None: SlowEvaluator()
        self._env_saved = {
            name: __import__("os").environ.get(name)
            for name in ("LLM_PROFICIENCY_PARALLELISM", "BACKEND_PROFICIENCY_TIMING_FILE")
        }

    def tearDown(self):
        import os

        ps._build_evaluator = self._saved
        for name, value in self._env_saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _run(self, parallelism, timing_path):
        import os

        os.environ["LLM_PROFICIENCY_PARALLELISM"] = str(parallelism)
        os.environ["BACKEND_PROFICIENCY_TIMING_FILE"] = str(timing_path)
        SlowEvaluator.reset()
        began = time.perf_counter()
        levels, details = ps.infer_proficiency_levels(
            _profile_with(SKILL_IDS), target_team_skill_ids=list(SKILL_IDS)
        )
        return levels, details, time.perf_counter() - began, SlowEvaluator.peak_in_flight

    def test_parallel_grading_matches_serial_output(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            serial_levels, serial_details, serial_s, serial_peak = self._run(
                1, Path(tmp) / "serial.jsonl"
            )
            par_levels, par_details, par_s, par_peak = self._run(
                4, Path(tmp) / "parallel.jsonl"
            )

            self.assertEqual(serial_peak, 1)
            self.assertGreater(par_peak, 1)
            self.assertEqual(serial_levels, par_levels)
            self.assertEqual(serial_details, par_details)
            self.assertEqual(len(par_levels), len(SKILL_IDS))
            self.assertLess(par_s, serial_s)

            # 计时须逐项落盘，且各项的偏移落在同一基准上（严格递增）。
            rows = [
                json.loads(line)
                for line in (Path(tmp) / "parallel.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            starts = [r for r in rows if r["stage"] == "proficiency" and r["event"] == "start"]
            ends = [r for r in rows if r["stage"] == "proficiency" and r["event"] == "end"]
            self.assertEqual(len(starts), len(SKILL_IDS))
            self.assertEqual(len(ends), len(SKILL_IDS))
            self.assertEqual(len({r["call_id"] for r in starts}), len(SKILL_IDS))
            self.assertTrue(any(r["offset_seconds"] > 0 for r in ends))

    def test_single_item_failure_does_not_void_the_rest(self):
        import os
        import tempfile

        class PartlyFailing(SlowEvaluator):
            def evaluate(self, ability, evidence, audit):
                if ability.category["team_skill_id"] == "T-SW-01":
                    raise RuntimeError("simulated single-item failure")
                return super().evaluate(ability, evidence, audit)

        ps._build_evaluator = lambda _trace=None: PartlyFailing()
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["LLM_PROFICIENCY_PARALLELISM"] = "4"
            os.environ["BACKEND_PROFICIENCY_TIMING_FILE"] = str(Path(tmp) / "t.jsonl")
            SlowEvaluator.reset()
            levels, details = ps.infer_proficiency_levels(
                _profile_with(SKILL_IDS), target_team_skill_ids=list(SKILL_IDS)
            )
        self.assertNotIn("T-SW-01", levels)
        self.assertEqual(len(levels), len(SKILL_IDS) - 1)
        failed = [d for d in details if d.get("status") == "EVALUATION_FAILED"]
        self.assertEqual([d["team_skill_id"] for d in failed], ["T-SW-01"])

    def test_timing_defaults_to_a_file_when_unset(self):
        import os

        os.environ.pop("BACKEND_PROFICIENCY_TIMING_FILE", None)
        self.assertIsNotNone(ps._timing_path())
        os.environ["BACKEND_PROFICIENCY_TIMING_DISABLED"] = "1"
        try:
            self.assertIsNone(ps._timing_path())
        finally:
            os.environ.pop("BACKEND_PROFICIENCY_TIMING_DISABLED", None)


if __name__ == "__main__":
    unittest.main()
