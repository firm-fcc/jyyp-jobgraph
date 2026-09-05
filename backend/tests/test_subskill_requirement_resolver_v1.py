from copy import deepcopy
from pathlib import Path
import unittest

from extractor.learning_path_stage1 import load_skill_development_graph
from extractor.subskill_requirement_resolver_v1 import SubskillRequirementResolverV1
from extractor.target_job_profile_learning_bridge import TargetJobProfileLearningBridge

CONFIG_DIR = Path(__file__).resolve().parent.parent / "candidate_core" / "config"

# NO_GRAPH 一档所代表的是“该能力尚无 curated 图谱”这一渲染契约，与具体是哪一项
# 能力无关。config/ 下的图谱逐批补齐，若以某一项恰好尚未收录为前提，图谱一经补齐
# 该用例即失效。故另取一项，在构造解析器时把它的图谱排除在外。
GRAPHLESS_SKILL = "T-SYS-04"


def graphs_without(team_skill_id):
    graphs = [
        load_skill_development_graph(path)
        for path in sorted(CONFIG_DIR.glob("skill_development_graph_*.json"))
    ]
    return [g for g in graphs if g.team_skill_id != team_skill_id]


class SubskillRequirementResolverV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resolver = SubskillRequirementResolverV1(graphs=graphs_without(GRAPHLESS_SKILL))

    def test_yolo_maps_to_target_detection(self):
        resolution = self.resolver.resolve("T-AI-03", ["YOLO"])
        self.assertEqual(resolution.resolution_status, "MATCHED")
        self.assertEqual(resolution.required_subskill_ids, ("CV-03",))
        self.assertEqual(resolution.matched_terms, ("yolo",))

    def test_sql_maps_to_query_and_data_operations(self):
        resolution = self.resolver.resolve("T-DA-02", ["SQL"])
        self.assertEqual(resolution.resolution_status, "MATCHED")
        self.assertEqual(resolution.required_subskill_ids, ("DB-02",))
        self.assertEqual(resolution.matched_terms, ("sql",))

    def test_multiple_keywords_deduplicate_with_stable_order(self):
        resolution = self.resolver.resolve(
            "T-AI-03", ["object detection", "YOLO", "目标检测"]
        )
        self.assertEqual(resolution.required_subskill_ids, ("CV-03",))
        self.assertEqual(
            resolution.matched_terms,
            ("yolo", "目标检测", "object detection"),
        )

    def test_english_matching_is_case_insensitive(self):
        resolution = self.resolver.resolve("T-AI-10", ["QLoRA", "PEFT"])
        self.assertEqual(resolution.required_subskill_ids, ("LLM-04",))
        self.assertEqual(resolution.matched_terms, ("qlora", "peft"))

    def test_conservative_nlp_keywords_map_to_specific_nodes(self):
        cases = (
            (("BERT", "Transformer", "XLNet"), "NLP-03"),
            (("ASR", "TTS", "Kaldi", "OpenFst", "WFST decoder"), "NLP-05"),
        )
        for terms, expected_node in cases:
            with self.subTest(node=expected_node):
                resolution = self.resolver.resolve("T-AI-02", terms)
                self.assertEqual(resolution.resolution_status, "MATCHED")
                self.assertEqual(resolution.required_subskill_ids, (expected_node,))
                self.assertEqual(
                    resolution.matched_terms,
                    tuple(term.casefold() for term in terms),
                )

    def test_disallowed_nlp_terms_do_not_map(self):
        disallowed = (
            "NLP", "NLTK", "jieba", "spaCy", "CNN", "RNN", "SVM",
            "RDF", "OWL", "Protege", "FastAPI", "Azure ML", "ISO 26262",
        )
        for term in disallowed:
            with self.subTest(term=term):
                resolution = self.resolver.resolve("T-AI-02", [term])
                self.assertEqual(resolution.resolution_status, "NO_MATCH")
                self.assertEqual(resolution.required_subskill_ids, ())

    def test_conservative_software_keywords_map_to_specific_nodes(self):
        cases = (
            (("Java", "C++", "Python"), "SWE-01", ("java", "c++", "python")),
            (("Git", "Maven"), "SWE-02", ("git", "maven")),
            (("Vue", "React"), "SWE-03", ("vue", "react")),
            (("Spring Boot", "MyBatis"), "SWE-04", ("mybatis", "spring boot")),
            (("Qt", "Android"), "SWE-05", ("qt", "android")),
        )
        for terms, expected_node, expected_terms in cases:
            with self.subTest(node=expected_node):
                resolution = self.resolver.resolve("T-SW-01", terms)
                self.assertEqual(resolution.resolution_status, "MATCHED")
                self.assertEqual(resolution.required_subskill_ids, (expected_node,))
                self.assertEqual(
                    resolution.matched_terms,
                    expected_terms,
                )

    def test_disallowed_software_terms_do_not_map(self):
        disallowed = (
            ".NET", "Spring Cloud", "XML", "Visual Studio", "MATLAB",
            "JSON", "Eclipse", "Tomcat", ".NET Core", "MVC", "Dubbo",
            "LabVIEW", "Linux", "SQL", "Redis",
        )
        for term in disallowed:
            with self.subTest(term=term):
                resolution = self.resolver.resolve("T-SW-01", [term])
                self.assertEqual(resolution.resolution_status, "NO_MATCH")
                self.assertEqual(resolution.required_subskill_ids, ())

    def test_no_skill_points_fails_closed(self):
        for skill_points in (None, [], (), "YOLO", ["", "   "], ["YOLO", 1]):
            with self.subTest(skill_points=skill_points):
                resolution = self.resolver.resolve("T-AI-03", skill_points)
                self.assertEqual(resolution.resolution_status, "NO_SKILL_POINTS")
                self.assertEqual(resolution.required_subskill_ids, ())

    def test_ambiguous_tool_is_not_mapped(self):
        resolution = self.resolver.resolve("T-AI-03", ["OpenCV"])
        self.assertEqual(resolution.resolution_status, "NO_MATCH")
        self.assertEqual(resolution.required_subskill_ids, ())

    def test_team_skill_without_graph_returns_no_graph(self):
        resolution = self.resolver.resolve(GRAPHLESS_SKILL, ["TCP/IP"])
        self.assertEqual(resolution.resolution_status, "NO_GRAPH")
        self.assertEqual(resolution.required_subskill_ids, ())

    def test_keyword_map_with_missing_subskill_fails_fast(self):
        invalid_map = {
            "schema_version": "subskill_keyword_map_v1",
            "skills": {"T-AI-03": {"CV-NOT-REAL": ["yolo"]}},
        }
        with self.assertRaisesRegex(ValueError, "missing subskill"):
            SubskillRequirementResolverV1(keyword_map=invalid_map)


class TargetJobProfileLearningBridgeSubskillTests(unittest.TestCase):
    @staticmethod
    def _profile(skill_points=None, techstack="AI/ML 与数据智能"):
        skill = {
            "team_skill_id": "T-AI-03",
            "is_primary": True,
            "requirement_status": "EXPLICIT_LEVEL",
            "required_level": "P3",
            "learning_path_target_eligible": True,
            "requirement_evidence_ref": (
                "structured_jd_summary:w:k:skill_vec_01:T-AI-03"
            ),
        }
        if skill_points is not None:
            skill["skill_points"] = skill_points
        return {
            "schema_version": "target_job_profile_v1.1",
            "taxonomy": {"taxonomy_compatibility": {"status": "PASS"}},
            "job": {
                "jobid": "1",
                "title": "视觉算法工程师",
                "techstack": techstack,
            },
            "skills": [
                skill,
                {
                    "team_skill_id": "T-AI-07",
                    "is_primary": True,
                    "requirement_status": "PROFICIENCY_NOT_AVAILABLE",
                    "required_level": None,
                    "learning_path_target_eligible": False,
                    "requirement_evidence_ref": (
                        "structured_jd_summary:w:k:skill_vec_01:T-AI-07"
                    ),
                    "skill_points": ["RAG"],
                },
            ],
        }

    def test_bridge_populates_subskills_and_preserves_requirement_semantics(self):
        profile = self._profile(["OpenCV", "YOLO"])
        before = deepcopy(profile)
        bridged = TargetJobProfileLearningBridge().build(profile)
        requirement = bridged.target.requirements[0]
        self.assertEqual(requirement.required_subskill_ids, ("CV-03",))
        self.assertEqual(requirement.required_level, "P3")
        self.assertEqual(
            requirement.requirement_evidence,
            ("structured_jd_summary:w:k:skill_vec_01:T-AI-03",),
        )
        self.assertEqual(requirement.requirement_type, "core")
        self.assertEqual(requirement.required_capabilities, ())
        self.assertIsNone(requirement.market_trend_rank)
        self.assertEqual(
            [item.team_skill_id for item in bridged.target.requirements],
            ["T-AI-03"],
        )
        self.assertEqual(
            bridged.diagnostics["excluded_skills"],
            [{"team_skill_id": "T-AI-07", "reason": "PROFICIENCY_NOT_AVAILABLE"}],
        )
        self.assertEqual(
            bridged.diagnostics["required_subskill_policy"],
            "DETERMINISTIC_JD_SKILLPOINT_TO_CURATED_GRAPH_V1",
        )
        self.assertEqual(
            bridged.diagnostics["techstack_policy"],
            "IGNORED_FOR_SUBSKILL_RESOLUTION",
        )
        self.assertEqual(
            bridged.diagnostics["required_subskill_resolutions"],
            [
                {
                    "team_skill_id": "T-AI-03",
                    "resolution_status": "MATCHED",
                    "input_skill_points": ["OpenCV", "YOLO"],
                    "matched_terms": ["yolo"],
                    "required_subskill_ids": ["CV-03"],
                }
            ],
        )
        self.assertEqual(profile, before)

    def test_techstack_alone_does_not_resolve_subskills(self):
        bridged = TargetJobProfileLearningBridge().build(
            self._profile(skill_points=None, techstack="YOLO | object detection")
        )
        self.assertEqual(bridged.target.requirements[0].required_subskill_ids, ())
        diagnostic = bridged.diagnostics["required_subskill_resolutions"][0]
        self.assertEqual(diagnostic["resolution_status"], "NO_SKILL_POINTS")
        self.assertEqual(diagnostic["input_skill_points"], [])

    def test_ambiguous_skillpoint_preserves_core_fallback(self):
        bridged = TargetJobProfileLearningBridge().build(
            self._profile(skill_points=["OpenCV"], techstack="YOLO")
        )
        self.assertEqual(bridged.target.requirements[0].required_subskill_ids, ())
        diagnostic = bridged.diagnostics["required_subskill_resolutions"][0]
        self.assertEqual(diagnostic["resolution_status"], "NO_MATCH")
        self.assertEqual(diagnostic["required_subskill_ids"], [])


if __name__ == "__main__":
    unittest.main()
