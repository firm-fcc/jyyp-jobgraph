import json
import re
from pathlib import Path


class ValidatedResumeExtractor:
    def __init__(self, rule_extractor, validator, min_added_confidence=0.85):
        self.rule_extractor = rule_extractor
        self.validator = validator
        self.normalizer = getattr(rule_extractor, "normalizer", None)
        self.min_added_confidence = min_added_confidence

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

    def compact(self, text):
        return re.sub(r"[\s（）()_\-—/]+", "", text).lower()

    def normalize_item(self, raw_item):
        if isinstance(raw_item, str):
            raw_item = {"ability": raw_item}

        ability = raw_item.get("ability", "").strip()
        if not ability:
            return None

        ability = self.ability_alias.get(ability, ability)
        compact_ability = self.compact(ability)

        for pattern, standard in self.pattern_alias.items():
            if self.compact(pattern) in compact_ability:
                ability = standard
                break

        if ability in self.blocklist:
            return None

        item = dict(raw_item)
        item["ability"] = ability

        if self.normalizer is not None:
            item["category"] = self.normalizer.find_category(ability)

        return item

    def to_float(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def deduplicate(self, items):
        result = {}

        for item in items:
            ability = item.get("ability", "")
            if not ability:
                continue

            if ability not in result:
                result[ability] = item
            elif self.to_float(item.get("confidence")) > self.to_float(
                result[ability].get("confidence")
            ):
                result[ability] = item

        return list(result.values())

    def extract(self, text):
        result = self.rule_extractor.extract(text)
        candidates = result.get("ability_profile", [])

        try:
            review = self.validator.validate(text, candidates)
        except Exception as error:
            print(f"[WARN] 审核失败，回退规则版：{error}")
            result["extraction_mode"] = "rule_fallback"
            return result

        candidate_map = {}

        for candidate in candidates:
            normalized = self.normalize_item(candidate)
            if normalized:
                candidate_map[normalized["ability"]] = normalized

        final_items = []

        # 保留审核通过的规则候选
        for approved in review.get("approved", []):
            normalized = self.normalize_item(approved)
            if not normalized:
                continue

            ability = normalized["ability"]

            if ability not in candidate_map:
                continue

            item = dict(candidate_map[ability])
            source = normalized.get("source_sentence", "").strip()

            if source and source in text:
                item["source_sentence"] = source

            item["confidence"] = max(
                self.to_float(item.get("confidence")),
                self.to_float(normalized.get("confidence"))
            )
            item["extractor"] = "rule+validator"
            item["validation_status"] = "approved"
            final_items.append(item)

        # 接收少量高置信度遗漏能力
        for added in review.get("added", []):
            normalized = self.normalize_item(added)
            if not normalized:
                continue

            confidence = self.to_float(normalized.get("confidence"))
            source = normalized.get("source_sentence", "").strip()
            ability = normalized["ability"]

            if confidence < self.min_added_confidence:
                continue

            if not source or source not in text:
                continue

            if ability in candidate_map:
                continue

            normalized["confidence"] = confidence
            normalized["extractor"] = "validator_added"
            normalized["validation_status"] = "added"
            final_items.append(normalized)

        result["ability_profile"] = self.deduplicate(final_items)
        result["extraction_mode"] = "llm_validated"
        result["validation_summary"] = {
            "candidate_count": len(candidates),
            "approved_count": len([
                item for item in final_items
                if item.get("validation_status") == "approved"
            ]),
            "added_count": len([
                item for item in final_items
                if item.get("validation_status") == "added"
            ])
        }

        return result
