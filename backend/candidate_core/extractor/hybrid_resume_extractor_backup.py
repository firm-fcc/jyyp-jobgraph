import re


class HybridResumeExtractor:
    def __init__(self, rule_extractor, llm_extractor):
        self.rule_extractor = rule_extractor
        self.llm_extractor = llm_extractor

    def split_sentences(self, text: str):
        sentences = re.split(r"[。；;\n]", text)
        return [
            sentence.strip()
            for sentence in sentences
            if len(sentence.strip()) >= 12
        ]

    def should_use_llm(self, sentence: str):
        keywords = [
            "项目", "负责", "完成", "实现", "开发",
            "训练", "优化", "部署", "处理", "分析",
            "设计", "调试", "参与"
        ]
        return any(word in sentence for word in keywords)

    def merge_abilities(self, rule_items, llm_items):
        result = {}

        for item in rule_items + llm_items:
            ability = item.get("ability", "").strip()

            if not ability:
                continue

            if ability not in result:
                result[ability] = item
                continue

            old_confidence = result[ability].get("confidence", 0)
            new_confidence = item.get("confidence", 0)

            if new_confidence > old_confidence:
                result[ability] = item

        return list(result.values())

    def extract(self, text: str):
        result = self.rule_extractor.extract(text)

        llm_abilities = []

        for sentence in self.split_sentences(text):
            if not self.should_use_llm(sentence):
                continue

            try:
                items = self.llm_extractor.extract_abilities(sentence)
                llm_abilities.extend(items)

            except Exception as error:
                print(f"[WARN] DeepSeek 提取失败：{error}")

        rule_abilities = result.get("ability_profile", [])

        result["ability_profile"] = self.merge_abilities(
            rule_abilities,
            llm_abilities
        )

        result["extraction_mode"] = "rule_and_deepseek"

        return result
