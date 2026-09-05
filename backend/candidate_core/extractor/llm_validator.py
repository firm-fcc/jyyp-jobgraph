import hashlib
import json
import os
import re
from pathlib import Path

import requests
from dotenv import load_dotenv


load_dotenv()


class LLMAbilityValidator:
    def __init__(
        self,
        prompt_path="config/llm_validator_prompt.txt",
        cache_dir="cache/validator"
    ):
        self.api_key = os.getenv("LLM_API_KEY")
        self.api_base = os.getenv("LLM_API_BASE")
        self.model = os.getenv("LLM_MODEL")

        self.prompt_template = Path(prompt_path).read_text(
            encoding="utf-8-sig"
        )

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, resume_text, candidates):
        content = json.dumps(
            {
                "model": self.model,
                "resume_text": resume_text,
                "candidates": candidates,
                "prompt": self.prompt_template
            },
            ensure_ascii=False,
            sort_keys=True
        )

        key = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.json"

    def _extract_json(self, text):
        text = text.strip()
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        match = re.search(r"\{.*\}", text, re.S)

        if not match:
            raise ValueError("大模型返回内容中未找到 JSON")

        return json.loads(match.group(0))

    def validate(self, resume_text, candidates):
        cache_path = self._cache_path(resume_text, candidates)

        if cache_path.exists():
            print("[VALIDATOR CACHE HIT]")
            return json.loads(
                cache_path.read_text(encoding="utf-8")
            )

        candidates_text = json.dumps(
            candidates,
            ensure_ascii=False,
            indent=2
        )

        prompt = (
            self.prompt_template
            .replace("{resume_text}", resume_text)
            .replace("{candidates}", candidates_text)
        )

        response = requests.post(
            self.api_base,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0
            },
            timeout=120
        )

        response.raise_for_status()

        content = response.json()["choices"][0]["message"]["content"]
        result = self._extract_json(content)

        result.setdefault("approved", [])
        result.setdefault("added", [])

        cache_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        return result
