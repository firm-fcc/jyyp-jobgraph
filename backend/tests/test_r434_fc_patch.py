import unittest

from extractor.agentic_schema import CandidateAbility, CandidateStatus, Evidence
from extractor.evidence_coverage_v432 import augment_grounded_coverage_v432
from extractor.evidence_source_policy_v43 import filter_evidence_candidates_v43
from extractor.team_skill_candidate_generator_v3 import TeamSkillCandidateGeneratorV3
from extractor.team_skill_registry import TeamSkillRegistry


def make_candidate(cid: str, text: str, start: int = 0) -> CandidateAbility:
    return CandidateAbility(
        candidate_id=cid,
        resume_id="candidate_test",
        project_id="resume_full",
        fact=text,
        behavior=text,
        ability=text,
        normalized_ability=text,
        category={},
        evidence=[Evidence(text, "resume_full", start, start + len(text))],
        reason="test",
        confidence=0.9,
        source="test",
        revision_round=0,
        parent_candidate_id=None,
        status=CandidateStatus.PENDING_REVIEW,
        lineage=[cid],
    )


class R434SourceContextTests(unittest.TestCase):
    def setUp(self):
        self.registry = TeamSkillRegistry()
        self.generator = TeamSkillCandidateGeneratorV3(self.registry)

    def test_campus_admin_tech_nouns_do_not_bypass_source_gate(self):
        resume = (
            "校园经历\n"
            "优化团务管理系统并管理场地申请及设备维护\n"
            "负责机器人系统测试与设备调试\n"
        )
        admin = "优化团务管理系统并管理场地申请及设备维护"
        technical = "负责机器人系统测试与设备调试"
        candidate = make_candidate("source_1", admin, resume.index(admin))
        candidate.evidence = [
            Evidence(admin, "resume_full", resume.index(admin), resume.index(admin) + len(admin)),
            Evidence(
                technical,
                "resume_full",
                resume.index(technical),
                resume.index(technical) + len(technical),
            ),
        ]
        result = filter_evidence_candidates_v43([candidate], resume)
        kept = [item.text for item in result.candidates[0].evidence]
        self.assertNotIn(admin, kept)
        self.assertIn(technical, kept)

    def test_weak_context_is_blocked_only_for_the_affected_skill(self):
        cases = (
            ("T-SW-01", "基于现有代码框架，完成特征的清洗与筛选"),
            ("T-AI-01", "参与分布式机器学习机理研究并撰写科技报告"),
            ("T-AI-01", "擅长图像处理，熟悉机器学习算法"),
            ("F-3-03", "管理场地申请及设备维护"),
            ("T-DA-01", "擅长方向：系统建模与路径规划研究"),
            ("T-SW-01", "优化团务管理系统"),
        )
        for index, (skill_id, text) in enumerate(cases):
            with self.subTest(skill_id=skill_id, text=text):
                candidate = make_candidate(f"source_{index}", text)
                self.assertFalse(
                    self.generator.allows_skill(candidate, self.registry.get(skill_id))
                )

    def test_direct_counterevidence_remains_eligible(self):
        cases = (
            ("T-SW-01", "开发团务管理软件模块并实现接口适配"),
            ("T-AI-01", "设计并训练深度学习模型，完成模型评估"),
            ("F-3-03", "统筹校级活动项目排期并协调人力资源"),
            ("T-DA-01", "研究方向中构建统计优化模型并完成求解"),
        )
        for index, (skill_id, text) in enumerate(cases):
            with self.subTest(skill_id=skill_id):
                candidate = make_candidate(f"positive_{index}", text)
                self.assertTrue(
                    self.generator.allows_skill(candidate, self.registry.get(skill_id))
                )

    def test_strict_coverage_recovers_action_rich_intro_and_precise_work_topic(self):
        intro = "协助管理EEG数据处理团队并协调受试者，定期总结汇报，有计划推进项目进度。"
        topic = "- 研究方向：ICL示例选择、协作式提示学习、端侧AI与边缘计算"
        weak = "熟悉机器学习与数据分析，具备良好的团队合作能力。"
        resume = f"优势：学习能力强。{weak}{intro}\n工作经历\n研究助理\n{topic}\n"
        result = augment_grounded_coverage_v432(
            [], candidate_id="candidate_test", resume_text=resume
        )
        added = [e.text for candidate in result.candidates for e in candidate.evidence]
        self.assertIn(intro, added)
        self.assertIn(topic, added)
        self.assertNotIn(weak, added)
        self.assertLessEqual(result.added_candidate_count, 2)


class R434CandidateRecallTests(unittest.TestCase):
    def test_recall_cooccurrence_must_be_within_one_evidence_item(self):
        registry = TeamSkillRegistry()
        generator = TeamSkillCandidateGeneratorV3(registry)

        interaction = make_candidate("split_interaction", "交互技术研究")
        interaction.evidence.append(
            Evidence("负责算法设计与实现", "resume_full", 20, 29)
        )
        interaction_ids = {
            skill.code
            for skill in generator.generate(
                interaction, top_k=8, recall_safe_fallback=False
            ).skills
        }
        self.assertNotIn("T-SW-05", interaction_ids)

        planning = make_candidate("split_planning", "统筹客户资料与项目安排")
        planning.evidence.append(
            Evidence("完成汇报材料撰写", "resume_full", 20, 28)
        )
        planning_ids = {
            skill.code
            for skill in generator.generate(
                planning, top_k=8, recall_safe_fallback=False
            ).skills
        }
        self.assertNotIn("F-3-03", planning_ids)

    def test_algorithm_recall_uses_generic_behavior_patterns(self):
        generator = TeamSkillCandidateGeneratorV3(TeamSkillRegistry())
        self.assertNotIn(
            "T-SW-02",
            generator._deterministic_recall_ids(["开展普通算法理论研究"]),
        )
        self.assertIn(
            "T-SW-02",
            generator._deterministic_recall_ids(["开展进化优化方法研究"]),
        )

    def test_generalized_recall_rules_are_bounded(self):
        registry = TeamSkillRegistry()
        generator = TeamSkillCandidateGeneratorV3(registry)
        cases = (
            ("T-SW-01", "负责算法实现工作，构建并实现并行计算框架"),
            ("F-3-01", "完成多轮原型机迭代并总结经验原理"),
            ("T-SW-04", "完成新型设备的评价实验"),
            ("T-SW-05", "建立力触觉交互功能"),
            ("T-DA-02", "实现知识库集合创建、插入、导入导出和进度管理"),
            ("T-SW-02", "面向高维数据开展进化优化方法研究"),
            ("F-3-03", "管理技术团队并协调人员，有计划推进项目进度和汇报"),
            ("T-AI-05", "研究方向：ICL示例选择与协作式提示学习"),
        )
        for index, (expected_id, text) in enumerate(cases):
            with self.subTest(expected_id=expected_id):
                pool = generator.generate(make_candidate(f"recall_{index}", text), top_k=4)
                self.assertIn(expected_id, {skill.code for skill in pool.skills})
                self.assertLessEqual(len(pool.skills), 4)
                self.assertFalse(pool.fallback_all)


if __name__ == "__main__":
    unittest.main()
