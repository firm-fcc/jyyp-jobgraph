import json
import unittest
from pathlib import Path

from extractor.occupation_warrant_v431 import resolve_occupation_warrants_v431
from extractor.team_skill_registry import TeamSkillRegistry
from extractor.team_skill_schema_v3 import CandidateSkillProfile, EvidenceObservation, TeamSkillAssessment


class OccupationWarrantV431Tests(unittest.TestCase):
    def setUp(self):
        self.registry = TeamSkillRegistry()

    def empty_profile(self, cid="candidate_0043"):
        return CandidateSkillProfile(cid, self.registry.version, (), {"schema_version":"candidate_skill_profile_v4_3_1"})

    def test_ai_algorithm_engineer_in_work_experience_supports_minimal_ai_skill(self):
        text = "求职意向\n算法工程师\n工作经历\n某研究院\nAI算法工程师\n2023.09 - 至今\n期刊论文\nTransformer论文"
        profile, activations = resolve_occupation_warrants_v431(
            candidate_id="candidate_0043", resume_text=text, profile=self.empty_profile(),
            team_skill_registry=self.registry,
        )
        self.assertEqual(len(activations), 1)
        self.assertEqual(activations[0].warrant_id, "W-OCC-AI-ML-01")
        item = next(x for x in profile.assessments if x.team_skill_id == "T-AI-01")
        self.assertEqual(item.status, "supported")
        self.assertIn("supported_warrant", item.audit_flags)
        self.assertEqual(item.evidence[0].text, "AI算法工程师")

    def test_job_intention_alone_never_activates(self):
        text = "求职意向\nAI算法工程师\n教育经历\n某大学"
        profile, activations = resolve_occupation_warrants_v431(
            candidate_id="c", resume_text=text, profile=self.empty_profile("c"),
            team_skill_registry=self.registry,
        )
        self.assertEqual(activations, ())
        self.assertEqual(profile.assessments, ())

    def test_broad_algorithm_engineer_is_not_weakened_from_qualified_registry_pattern(self):
        text = "工作经历\n某公司\n算法工程师\n2024.01 - 至今"
        profile, activations = resolve_occupation_warrants_v431(
            candidate_id="c", resume_text=text, profile=self.empty_profile("c"),
            team_skill_registry=self.registry,
        )
        self.assertEqual(activations, ())
        self.assertEqual(profile.assessments, ())

    def test_direct_supported_has_precedence(self):
        skill=self.registry.get("T-AI-01")
        direct=TeamSkillAssessment(
            candidate_id="c", team_skill_id=skill.code, team_skill_name=skill.name_zh,
            status="supported", inference_mode=skill.inference_mode,
            evidence=(EvidenceObservation("训练深度学习模型","project_1",0,8),),
            reason="direct", confidence=0.9, atomic_abilities=(), audit_flags=(),
        )
        base=CandidateSkillProfile("c",self.registry.version,(direct,),{})
        text="工作经历\n某研究院\nAI算法工程师\n2023.09 - 至今"
        profile, activations=resolve_occupation_warrants_v431(
            candidate_id="c",resume_text=text,profile=base,team_skill_registry=self.registry,
        )
        self.assertEqual(profile.assessments[0].reason,"direct")
        self.assertEqual(len(activations),1)
        self.assertFalse(activations[0].applied_to_profile)

    def test_frozen_registry_only_allows_eligible_ab_occupation_warrants(self):
        registry_path=Path(__file__).resolve().parent.parent / "candidate_core" / "config" / "evidence_warrant_registry_v0.1.json"
        data=json.loads(registry_path.read_text(encoding="utf-8"))
        eligible=[w for w in data["warrants"] if w.get("signal_type")=="occupation" and w.get("decision_effect")=="eligible_supported_warrant" and w.get("grade") in {"A","B"}]
        self.assertEqual({w["warrant_id"] for w in eligible},{"W-OCC-AI-ML-01","W-OCC-SW-DEV-01","W-OCC-DB-01"})


if __name__ == "__main__":
    unittest.main()
