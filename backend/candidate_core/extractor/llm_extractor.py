from dotenv import load_dotenv
load_dotenv()
import json
import os
import re
import requests
from pathlib import Path


class LLMAbilityExtractor:
    def __init__(self, prompt_path: str, normalizer=None):
        self.prompt_template = Path(prompt_path).read_text(encoding="utf-8-sig")
        self.normalizer = normalizer

        self.api_key = os.getenv("LLM_API_KEY")
        self.api_base = os.getenv("LLM_API_BASE")
        self.model = os.getenv("LLM_MODEL")

        if not self.api_key:
            raise ValueError("未找到环境变量 LLM_API_KEY")

        if not self.api_base:
            raise ValueError("未找到环境变量 LLM_API_BASE")

        if not self.model:
            raise ValueError("未找到环境变量 LLM_MODEL")

    def build_prompt(self, sentence: str) -> str:
        return self.prompt_template.replace("{sentence}", sentence)

    def extract_json_array(self, text: str):
        """
        从模型输出中提取 JSON 数组。
        """
        match = re.search(r"\[.*\]", text, re.S)

        if not match:
            return []

        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return []

    def call_llm(self, prompt: str):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0
        }

        response = requests.post(
            self.api_base,
            headers=headers,
            json=payload,
            timeout=60
        )

        response.raise_for_status()

        data = response.json()
        return data["choices"][0]["message"]["content"]

    def extract_abilities(self, sentence: str):
        prompt = self.build_prompt(sentence)
        content = self.call_llm(prompt)

        raw_items = self.extract_json_array(content)

        cleaned = []

        for item in raw_items:
            ability = str(item.get("ability", "")).strip()
            source_sentence = str(item.get("source_sentence", "")).strip()

            try:
                confidence = float(item.get("confidence", 0.0))
            except Exception:
                confidence = 0.0

            if not ability:
                continue

            # 证据句兜底：如果模型给出的证据句不在原句中，就改回原句
            if not source_sentence or source_sentence not in sentence:
                source_sentence = sentence

            category = {
                "level1": "未分类能力",
                "level2": "待人工审核"
            }

            if self.normalizer is not None:
                category = self.normalizer.find_category(ability)

            cleaned.append({
                "ability": ability,
                "category": category,
                "level": "未判断",
                "source_sentence": source_sentence,
                "confidence": confidence,
                "extractor": "deepseek"
            })

        return self._deduplicate(cleaned)

    def _deduplicate(self, items):
        seen = set()
        result = []

        for item in items:
            key = (item["ability"], item["source_sentence"])
            if key not in seen:
                seen.add(key)
                result.append(item)

        return result
