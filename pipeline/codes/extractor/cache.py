# -*- coding: utf-8 -*-
"""句级结果缓存：sentence -> 分类结果。

招聘 JD 中大量表述重复，同一子句（规范化后）只调用一次 LLM，
后续重复子句直接命中缓存，显著降低时间/成本。

存储：JSONL 文件（cache/cache_{mode}.jsonl），跨运行持久化。
"""
import json
import os
import re

import config


def _normalize(sentence):
    """规范化子句，用于缓存键：去空白、统一大小写、去标点。"""
    s = re.sub(r"\s+", "", sentence.lower())
    s = re.sub(r"[，。；、！？；,!?;：:·…—\-_\"'（）()【】\[\]]", "", s)
    return s


class ResultCache:
    def __init__(self, mode, cache_dir=None):
        self.mode = mode
        self.cache_dir = cache_dir or config.CACHE_DIR
        os.makedirs(self.cache_dir, exist_ok=True)
        self.path = os.path.join(self.cache_dir, config.CACHE_MODE_FILE.format(mode=mode))
        self._data = {}  # normalized_sentence -> [matched codes]
        self.hits = 0
        self.misses = 0
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                # 向后兼容：旧格式存 "codes"（纯技能码），新格式存 "matches"（含技能点）
                if "matches" in rec:
                    self._data[rec["key"]] = rec["matches"]
                else:
                    self._data[rec["key"]] = [{"code": c, "skillpoints": []} for c in rec.get("codes", [])]

    def get(self, sentence):
        key = _normalize(sentence)
        if key in self._data:
            self.hits += 1
            return self._data[key]
        self.misses += 1
        return None

    def set(self, sentence, matches):
        """写入并追加到磁盘。matches: [{"code": ..., "skillpoints": [...]}]"""
        key = _normalize(sentence)
        self._data[key] = matches
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"key": key, "sentence": sentence, "matches": matches},
                               ensure_ascii=False) + "\n")

    def size(self):
        return len(self._data)

    def stats(self):
        return {"hits": self.hits, "misses": self.misses, "cache_size": self.size()}
