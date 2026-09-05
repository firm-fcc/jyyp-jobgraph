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
            config_dir / "ability_alias.json", {}
        )
        self.pattern_alias = self.load_json(
            config_dir / "ability_pattern_alias.json", {}
        )
        self.blocklist = set(
            self.load_json(config_dir / "ability_blocklist.json", [])
        )

    def load_json(self, path, default):
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def split_sentences(self, text):
        sentences = re.split(r"[。；;\n]", text)
        return [
            s.strip() for s in sentences
            if len(s.strip()) >= 12
        ]

    def should_use_llm(self, sentence):
        keywords = [
            "项目", "负责", "完成", "实现", "开发",
            "训练", "优化", "部署", "处理", "分析",
            "设计", "调试", "参与"
        ]
        return any(word in sentence for word in keywords)

    def compact(self, text):
        return re.sub(
            r"[\s（）()_\-—/]+", "", text
        ).lower()

    def normalize_item(self, item):
        original = item.get("ability", "").strip()

        if not original:
            return None

        ability = self.ability_alias.get(original, original)
        compact_ability = self.compact(ability)

        for pattern, standard in self.pattern_alias.items():
            if self.compact(pattern) in compact_ability:
                ability = standard
                break

        if original in self.blocklist or ability in self.blocklist:
            return None

        new_item = dict(item)
        new_item["ability"] = ability

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

            old = result[ability]

            if old.get("extractor") != item.get("extractor"):
                old["extractor"] = "rule+deepseek"
                old["confidence"] = max(
                    old.get("confidence", 0),
                    item.get("confidence", 0)
                )
            elif item.get("confidence", 0) > old.get("confidence", 0):
                result[ability] = item

        return list(result.values())

    def extract(self, text):
        result = self.rule_extractor.extract(text)
        llm_items = []

        for sentence in self.split_sentences(text):
            if not self.should_use_llm(sentence):
                continue

            try:
                items = self.llm_extractor.extract_abilities(sentence)

                for item in items:
                    item["extractor"] = "deepseek"

                llm_items.extend(items)

            except Exception as error:
                print(f"[WARN] DeepSeek 提取失败：{error}")

        rule_items = result.get("ability_profile", [])

        for item in rule_items:
            item.setdefault("extractor", "rule")

        result["ability_profile"] = self.merge_abilities(
            rule_items, llm_items
        )
        result["extraction_mode"] = "rule_and_deepseek"

        return result
