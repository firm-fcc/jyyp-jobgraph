import json
import re
from pathlib import Path


class HybridResumeExtractor:
    def __init__(self, rule_extractor, llm_extractor):
        self.rule_extractor = rule_extractor
        self.llm_extractor = llm_extractor
        self.normalizer = getattr(rule_extractor, "normalizer", None)

        config_dir = Path(__file__).resolve().parent.parent / "config"

        self.ability_alias = self.load_json(
            config_dir / "ability_alias.json",
            {}
        )

        self.ability_blocklist = set(
            self.load_json(
                config_dir / "ability_blocklist.json",
                []
            )
        )

    def load_json(self, path, default):
        if not path.exists():
            return default

        try:
            return json.loads(
                path.read_text(encoding="utf-8-sig")
            )
        except Exception as error:
            print(f"[WARN] 配置文件读取失败：{path.name}，原因：{error}")
            return default

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

    def normalize_item(self, item):
        ability = item.get("ability", "").strip()

        if not ability:
            return None

        # 别名归一化
        ability = self.ability_alias.get(ability, ability)

        # 过滤不属于标准能力的结果
        if ability in self.ability_blocklist:
            return None

        new_item = dict(item)
        new_item["ability"] = ability

        # 重新计算能力分类
        if self.normalizer is not None:
            new_item["category"] = self.normalizer.find_category(ability)

        return new_item

    def merge_abilities(self, rule_items, llm_items):
        result = {}

        for raw_item in rule_items + llm_items:
            item = self.normalize_item(raw_item)

            if item is None:
                continue

            ability = item["ability"]

            if ability not in result:
                result[ability] = item
                continue

            old_item = result[ability]

            old_source = old_item.get("extractor", "")
            new_source = item.get("extractor", "")

            # 同时被规则和大模型识别
            if old_source != new_source:
                old_item["extractor"] = "rule+deepseek"
                old_item["confidence"] = max(
                    old_item.get("confidence", 0),
                    item.get("confidence", 0)
                )
                continue

            # 同来源时保留置信度更高的结果
            if item.get("confidence", 0) > old_item.get("confidence", 0):
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

                for item in items:
                    item["extractor"] = "deepseek"

                llm_abilities.extend(items)

            except Exception as error:
                print(f"[WARN] DeepSeek 提取失败：{error}")

        rule_abilities = result.get("ability_profile", [])

        for item in rule_abilities:
            item.setdefault("extractor", "rule")

        result["ability_profile"] = self.merge_abilities(
            rule_abilities,
            llm_abilities
        )

        result["extraction_mode"] = "rule_and_deepseek"

        return result
