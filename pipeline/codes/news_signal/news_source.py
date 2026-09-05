# -*- coding: utf-8 -*-
"""新闻数据源：解析结果 → (stratum, text) 条目 → 抽样/去重/断点。

复用 StratifiedSampler 通用引擎（news_sampler.py）。stratum=公众号（来源）。
sampler 的 text 仅用于去重与断点（紧凑文本）；next_batch 通过反向映射返回 NewsRecord，
实际 LLM 输入使用 NewsRecord 全字段（title/body）。
"""
import re

import news_config as config
from news_sampler import StratifiedSampler


def compact_text(record, max_chars=None):
    """构造新闻紧凑文本（用于去重/断点，非 LLM 输入）。"""
    max_chars = max_chars or config.DOC_MAX_CHARS
    text = re.sub(r"\s+", " ", " ".join(p for p in (record.title, record.body) if p)).strip()
    return text[:max_chars]


class NewsSource(StratifiedSampler):
    """新闻数据源。records: list[NewsRecord]。next_batch() 返回 list[NewsRecord]。"""

    def __init__(self, records, seed=42, dedup=True):
        self.records = records          # 必须在 super().__init__ 前设置（loader 会立即消费）
        self._by_text = {}
        super().__init__(loader=self._gen, seed=seed, dedup=dedup)

    def _stratum_of(self, rec):
        return rec.source or ""

    def _gen(self):
        for rec in self.records:
            text = compact_text(rec)
            if not text:
                continue
            self._by_text[text] = rec
            yield self._stratum_of(rec), text

    def next_batch(self, n, strategy="proportional", min_per=None):
        """取下一批 n 篇 NewsRecord（标记已消费）。"""
        texts = super().next_batch(n, strategy=strategy, min_per=min_per)
        return [self._by_text[t] for t in texts if t in self._by_text]
