import re


class ResumeExtractor:
    def __init__(self, normalizer):
        self.normalizer = normalizer

        self.skill_keywords = [
            "Python", "PyTorch", "torch", "OpenCV", "YOLO", "YOLOv8",
            "FastAPI", "Docker", "Linux", "RAG", "Agent", "Transformer",
            "BERT", "MySQL", "Redis", "Java", "Spring Boot",
            "向量数据库", "数据清洗", "数据可视化"
        ]

        self.implicit_rules = {
            "YOLOv8": ["目标检测", "模型训练", "计算机视觉"],
            "YOLO": ["目标检测", "计算机视觉"],
            "OpenCV": ["图像处理", "计算机视觉"],
            "PyTorch": ["深度学习", "模型训练"],

            "RAG": ["知识库构建", "检索增强生成"],
            "向量数据库": ["向量数据库", "检索增强生成"],
            "知识库": ["知识库构建"],
            "大模型": ["知识库构建", "检索增强生成"],

            "FastAPI": ["后端接口开发"],
            "Spring Boot": ["后端开发"],
            "Java": ["后端开发"],
            "MySQL": ["数据库设计"],
            "Redis": ["数据库设计"],
            "数据库": ["数据库设计"],

            "Docker": ["模型部署", "工程部署"],
            "Linux": ["工程部署"],
            "部署": ["模型部署", "工程部署"],
            "工程部署": ["工程部署"],

            "数据集": ["数据处理", "数据清洗"],
            "标注": ["数据标注"],
            "数据清洗": ["数据清洗", "数据处理"],
            "可视化": ["结果分析", "数据可视化"],
            "数据可视化": ["数据可视化"],
            "统计": ["结果分析"],
            "技能需求": ["技能需求分析"],

            "相机标定": ["相机标定", "计算机视觉"],
            "RoboMaster": ["计算机视觉"],
            "目标检测调试": ["目标检测"],

            "订单管理": ["订单管理"],
            "用户登录": ["后端开发"],
            "商品发布": ["后端开发"],
            "后端开发": ["后端开发"],
            "项目": ["项目开发"]
        }

    def extract_basic_info(self, text: str):
        email_match = re.search(
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
            text
        )
        phone_match = re.search(r"1[3-9]\d{9}", text)

        return {
            "name": self._guess_name(text),
            "email": email_match.group(0) if email_match else "",
            "phone": phone_match.group(0) if phone_match else "",
            "school": self._find_first(text, ["华中科技大学", "某高校", "武汉大学", "清华大学", "北京大学"]),
            "major": self._find_first(text, ["人工智能", "计算机科学与技术", "软件工程", "自动化", "数据科学与大数据技术"]),
            "degree": self._find_first(text, ["本科", "硕士", "博士"])
        }

    def _guess_name(self, text: str):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ""

        first_line = lines[0]
        if 2 <= len(first_line) <= 10:
            return first_line

        return ""

    def _find_first(self, text: str, candidates):
        for item in candidates:
            if item in text:
                return item
        return ""

    def split_sentences(self, text: str):
        sentences = re.split(r"[。；;\n]", text)
        return [s.strip() for s in sentences if s.strip()]

    def extract_explicit_skills(self, text: str):
        found = []
        lower_text = text.lower()

        for skill in self.skill_keywords:
            if skill.lower() in lower_text:
                found.append(skill)

        return self.normalizer.normalize_skill_list(found)

    def extract_implicit_abilities(self, text: str):
        sentences = self.split_sentences(text)
        ability_items = []

        for sentence in sentences:
            for key, abilities in self.implicit_rules.items():
                if key.lower() in sentence.lower():
                    for ability in abilities:
                        ability_items.append({
                            "ability": ability,
                            "category": self.normalizer.find_category(ability),
                            "level": self.infer_level(sentence),
                            "source_sentence": sentence,
                            "confidence": 0.85
                        })

        return self._deduplicate_abilities(ability_items)

    def infer_level(self, sentence: str):
        if any(word in sentence for word in ["主导", "设计", "优化", "落地"]):
            return "深入"
        if any(word in sentence for word in ["负责", "独立完成", "完成", "实现"]):
            return "掌握"
        if any(word in sentence for word in ["熟悉", "使用", "参与"]):
            return "熟悉"
        if any(word in sentence for word in ["了解", "学习", "接触"]):
            return "了解"
        return "未判断"

    def _deduplicate_abilities(self, ability_items):
        seen = set()
        result = []

        for item in ability_items:
            key = (item["ability"], item["source_sentence"])
            if key not in seen:
                seen.add(key)
                result.append(item)

        return result

    def extract_projects(self, text: str):
        projects = []

        if "项目经历" not in text:
            return projects

        project_part = text.split("项目经历", 1)[-1]
        sentences = self.split_sentences(project_part)

        for sentence in sentences:
            if any(keyword.lower() in sentence.lower() for keyword in self.skill_keywords) or "项目" in sentence:
                projects.append({
                    "project_name": "",
                    "description": sentence,
                    "explicit_skills": self.extract_explicit_skill_names(sentence),
                    "implicit_abilities": [
                        item["ability"]
                        for item in self.extract_implicit_abilities(sentence)
                    ],
                    "source_sentences": [sentence]
                })

        return projects

    def extract_explicit_skill_names(self, text: str):
        skills = self.extract_explicit_skills(text)
        return [item["skill"] for item in skills]

    def extract(self, text: str):
        basic_info = self.extract_basic_info(text)
        explicit_skills = self.extract_explicit_skills(text)
        implicit_abilities = self.extract_implicit_abilities(text)
        projects = self.extract_projects(text)

        return {
            "basic_info": basic_info,
            "skills": explicit_skills,
            "projects": projects,
            "ability_profile": implicit_abilities
        }
