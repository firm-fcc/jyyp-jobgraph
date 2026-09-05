import unittest

from extractor.team_skill_schema_v3 import EvidenceObservation, TeamSkillAssessment


class TeamSkillSchemaV3Tests(unittest.TestCase):
    def test_supported_skill_requires_evidence(self):
        with self.assertRaises(ValueError):
            TeamSkillAssessment(
                candidate_id="c1",
                team_skill_id="T-AI-01",
                team_skill_name="机器学习与深度学习",
                status="supported",
                inference_mode="direct_behavior",
            )

    def test_valid_supported_assessment(self):
        evidence = EvidenceObservation(
            text="使用PyTorch训练ResNet-18模型",
            source_experience_id="exp1",
            start=0,
            end=18,
            behavior="训练ResNet-18模型",
        )
        result = TeamSkillAssessment(
            candidate_id="c1",
            team_skill_id="T-AI-01",
            team_skill_name="机器学习与深度学习",
            status="supported",
            inference_mode="direct_behavior",
            evidence=(evidence,),
            atomic_abilities=("ResNet图像分类模型训练",),
        )
        self.assertEqual(result.to_dict()["team_skill_id"], "T-AI-01")


if __name__ == "__main__":
    unittest.main()
