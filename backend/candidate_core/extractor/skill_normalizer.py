import json


class SkillNormalizer:
    def __init__(self, alias_path: str, taxonomy_path: str):
        self.alias_map = self._load_json(alias_path)
        self.taxonomy = self._load_json(taxonomy_path)

    def _load_json(self, path: str):
        # utf-8-sig 可以兼容 PowerShell 写入 JSON 时可能带 BOM 的情况
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)

    def normalize_skill(self, skill: str) -> str:
        """
        技能别名归一化：
        torch -> PyTorch
        cv -> 计算机视觉
        llm -> 大语言模型
        """
        if not skill:
            return ""

        raw = skill.strip()
        key = raw.lower().replace(" ", "")

        return self.alias_map.get(key, raw)

    def find_category(self, skill: str):
        """
        根据标准技能名查找能力分类。
        返回一级分类和二级分类。
        """
        standard_skill = self.normalize_skill(skill)

        for level1, level2_dict in self.taxonomy.items():
            for level2, skills in level2_dict.items():
                if standard_skill in skills:
                    return {
                        "level1": level1,
                        "level2": level2
                    }

        return {
            "level1": "未分类能力",
            "level2": "待人工审核"
        }

    def normalize_skill_list(self, skills):
        """
        输入技能列表，输出去重后的标准化结果。
        """
        result = []
        seen = set()

        for skill in skills:
            standard = self.normalize_skill(skill)

            if not standard or standard in seen:
                continue

            seen.add(standard)

            result.append({
                "skill": standard,
                "category": self.find_category(standard)
            })

        return result
