import hashlib
import json
import os
from pathlib import Path

from extractor.llm_extractor import LLMAbilityExtractor


class CachedLLMAbilityExtractor:
    def __init__(
        self,
        prompt_path="config/llm_prompt.txt",
        normalizer=None,
        cache_dir="cache/llm"
    ):
        self.inner = LLMAbilityExtractor(
            prompt_path=prompt_path,
            normalizer=normalizer
        )

        self.prompt_text = Path(prompt_path).read_text(
            encoding="utf-8-sig"
        )
        self.model = os.getenv("LLM_MODEL", "unknown")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, sentence):
        content = (
            self.model
            + "\n"
            + self.prompt_text
            + "\n"
            + sentence
        )

        key = hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest()

        return self.cache_dir / f"{key}.json"

    def extract_abilities(self, sentence):
        cache_path = self._cache_path(sentence)

        if cache_path.exists():
            print("[CACHE HIT]")
            return json.loads(
                cache_path.read_text(encoding="utf-8")
            )

        result = self.inner.extract_abilities(sentence)

        cache_path.write_text(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        return result
