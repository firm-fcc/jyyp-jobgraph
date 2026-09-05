import copy
import unittest
from pathlib import Path

from extractor.candidate_matching_bridge_v1 import CandidateMatchingBridge
from extractor.matching_engine_v1 import MatchingEngineV1
from extractor.matching_pipeline_v1 import MatchingPipelineV1, _matching_target_view
from extractor.target_job_profile_adapter import TargetJobProfileAdapter
from extractor.target_job_profile_learning_bridge import TargetJobProfileLearningBridge
from extractor.team_skill_schema_v3 import CandidateSkillProfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "job_data" / "2022-10"
AI_JD_KEY = "05cf4eaf48d58138011fea774dd57ca9"


def empty_candidate():
    return CandidateSkillProfile.from_dict(
        {
            "candidate_id": "matching-subskill-compatibility",
            "skill_registry_version": "0.4",
            "assessments": [],
            "metadata": {"schema_version": "candidate_skill_profile_v4_3_4"},
        }
    )


class MatchingPipelineSubskillCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        adapter = TargetJobProfileAdapter.from_paths(
            provider_taxonomy_path=DATA_ROOT / "provider_skills.json",
            canonical_taxonomy_path=PROJECT_ROOT / "candidate_core/config/team_skills_v0.4.json",
            jobs_path=DATA_ROOT / "jobs.json",
            jd_summary_csv=DATA_ROOT / "jd_summary_2022-10.csv",
            job_skill_path=DATA_ROOT / "job_skill_effective.json",
            window="2022-10",
            graph_layer="effective",
        )
        cls.target_profile = adapter.build_single_jd(jd_key=AI_JD_KEY)
        cls.bridged = TargetJobProfileLearningBridge().build(cls.target_profile)
        cls.candidate_profile = empty_candidate()

    def test_matching_view_clears_only_subskill_ids_without_mutation(self):
        original = self.bridged.target
        before = original.to_dict()
        view = _matching_target_view(original)

        original_ml = next(
            item for item in original.requirements if item.team_skill_id == "T-AI-01"
        )
        view_ml = next(
            item for item in view.requirements if item.team_skill_id == "T-AI-01"
        )
        self.assertEqual(original_ml.required_subskill_ids, ("ML-03",))
        self.assertEqual(view_ml.required_subskill_ids, ())
        self.assertEqual(original.to_dict(), before)

        for source, matching in zip(original.requirements, view.requirements):
            self.assertEqual(source.team_skill_id, matching.team_skill_id)
            self.assertEqual(source.requirement_type, matching.requirement_type)
            self.assertEqual(source.required_level, matching.required_level)
            self.assertEqual(source.requirement_evidence, matching.requirement_evidence)
            self.assertEqual(source.required_capabilities, matching.required_capabilities)
            self.assertEqual(source.market_trend_rank, matching.market_trend_rank)

    def test_pipeline_result_equals_explicit_empty_subskill_baseline(self):
        profile_before = copy.deepcopy(self.target_profile)
        pipeline_output = MatchingPipelineV1().run(
            candidate_profile=self.candidate_profile,
            target_job_profile=self.target_profile,
            proficiency_levels={},
        )

        candidate = CandidateMatchingBridge().build(self.candidate_profile, {})
        baseline_target = _matching_target_view(self.bridged.target)
        skill_names = {
            str(item["team_skill_id"]): str(item["team_skill_name"])
            for item in self.target_profile["skills"]
        }
        baseline = MatchingEngineV1().match(
            candidate.profile,
            baseline_target,
            skill_names=skill_names,
        )

        self.assertEqual(pipeline_output.match_result.to_dict(), baseline.to_dict())
        self.assertEqual(self.target_profile, profile_before)

    def test_resolver_diagnostics_and_full_target_remain_available(self):
        output = MatchingPipelineV1().run(
            candidate_profile=self.candidate_profile,
            target_job_profile=self.target_profile,
            proficiency_levels={},
        )
        resolution = next(
            item
            for item in output.target_bridge_diagnostics["required_subskill_resolutions"]
            if item["team_skill_id"] == "T-AI-01"
        )
        self.assertEqual(resolution["resolution_status"], "MATCHED")
        self.assertEqual(resolution["required_subskill_ids"], ["ML-03"])

        rebuilt = TargetJobProfileLearningBridge().build(self.target_profile)
        requirement = next(
            item for item in rebuilt.target.requirements if item.team_skill_id == "T-AI-01"
        )
        self.assertEqual(requirement.required_subskill_ids, ("ML-03",))


if __name__ == "__main__":
    unittest.main()
