import re


class JDExtractor:
    def __init__(self, normalizer):
        self.normalizer = normalizer

        self.skill_keywords = [
            "Python", "PyTorch", "torch", "OpenCV", "YOLO", "YOLOv8",
            "FastAPI", "Docker", "Linux", "RAG", "Agent", "Transformer",
            "BERT", "MySQL", "Redis"
        ]

        self.ability_rules = {
            "YOLOv8": ["目标检测", "模型训练", "计算机视觉"],
            "YOLO": ["目标检测", "计算机视觉"],
            "OpenCV": ["图像处理", "计算机视觉"],
            "PyTorch": ["深度学习", "模型训练"],
            "RAG": ["知识库构建", "检索增强生成"],
            "Agent": ["工具调用", "任务规划"],
            "FastAPI": ["后端接口开发", "RESTful API"],
            "Docker": ["模型部署", "容器化部署"],
            "Linux": ["部署运维"],
            "数据集": ["数据处理", "数据清洗"],
            "标注": ["数据标注"],
            "可视化": ["结果分析"],
            "模型训练": ["模型训练"],
            "模型部署": ["模型部署"],
            "目标检测": ["目标检测", "计算机视觉"],
            "图像识别": ["图像分类", "计算机视觉"]
        }

    def extract_job_title(self, text: str):
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        for line in lines[:8]:
            if "岗位名称" in line or "职位名称" in line:
                return (
                    line.replace("岗位名称：", "")
                        .replace("岗位名称:", "")
                        .replace("职位名称：", "")
                        .replace("职位名称:", "")
                        .strip()
                )

        for line in lines[:8]:
            if any(word in line for word in ["工程师", "开发", "算法", "实习生", "研究员"]):
                return line.strip()

        return ""

    def split_sentences(self, text: str):
        sentences = re.split(r"[。；;\n]", text)
        return [s.strip() for s in sentences if s.strip()]

    def extract_core_responsibilities(self, text: str):
        responsibilities = []
        sentences = self.split_sentences(text)

        for sentence in sentences:
            if any(word in sentence for word in ["负责", "参与", "完成", "进行", "开发", "优化"]):
                responsibilities.append(sentence)

        return responsibilities

    def extract_skills_with_source(self, text: str):
        result = []
        sentences = self.split_sentences(text)

        for sentence in sentences:
            lower_sentence = sentence.lower()

            for skill in self.skill_keywords:
                if skill.lower() in lower_sentence:
                    standard_skill = self.normalizer.normalize_skill(skill)

                    result.append({
                        "skill": standard_skill,
                        "importance": self.judge_importance(sentence),
                        "source_sentence": sentence
                    })

        return self._deduplicate_skill_items(result)

    def judge_importance(self, sentence: str):
        if any(word in sentence for word in ["优先", "加分", "有经验者优先", "更佳"]):
            return "bonus"

        if any(word in sentence for word in ["熟悉", "掌握", "具备", "要求", "必须", "需要"]):
            return "required"

        return "related"

    def infer_level(self, sentence: str):
        if any(word in sentence for word in ["精通", "深入"]):
            return "深入"
        if any(word in sentence for word in ["掌握", "负责", "独立", "完成", "实现"]):
            return "掌握"
        if any(word in sentence for word in ["熟悉", "使用", "参与"]):
            return "熟悉"
        if any(word in sentence for word in ["了解"]):
            return "了解"
        return "未判断"

    def extract_ability_points(self, text: str):
        sentences = self.split_sentences(text)
        ability_points = []

        for sentence in sentences:
            for key, abilities in self.ability_rules.items():
                if key.lower() in sentence.lower():
                    for ability in abilities:
                        ability_points.append({
                            "ability": ability,
                            "category": self.normalizer.find_category(ability),
                            "importance": self.judge_importance(sentence),
                            "level": self.infer_level(sentence),
                            "source_sentence": sentence,
                            "confidence": 0.85
                        })

        return self._deduplicate_ability_items(ability_points)

    def _deduplicate_skill_items(self, items):
        seen = set()
        result = []

        for item in items:
            key = (item["skill"], item["source_sentence"])
            if key not in seen:
                seen.add(key)
                result.append(item)

        return result

    def _deduplicate_ability_items(self, items):
        seen = set()
        result = []

        for item in items:
            key = (item["ability"], item["source_sentence"])
            if key not in seen:
                seen.add(key)
                result.append(item)

        return result

    def extract(self, text: str):
        skill_items = self.extract_skills_with_source(text)
        ability_points = self.extract_ability_points(text)

        required_skills = []
        bonus_skills = []
        related_skills = []

        for item in skill_items:
            if item["importance"] == "required":
                required_skills.append(item["skill"])
            elif item["importance"] == "bonus":
                bonus_skills.append(item["skill"])
            else:
                related_skills.append(item["skill"])

        return {
            "job_title": self.extract_job_title(text),
            "core_responsibilities": self.extract_core_responsibilities(text),
            "required_skills": self._deduplicate_list(required_skills),
            "bonus_skills": self._deduplicate_list(bonus_skills),
            "related_skills": self._deduplicate_list(related_skills),
            "ability_points": ability_points
        }

    def _deduplicate_list(self, items):
        result = []
        seen = set()

        for item in items:
            if item not in seen:
                seen.add(item)
                result.append(item)

        return result
