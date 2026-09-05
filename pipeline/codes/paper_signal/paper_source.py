# -*- coding: utf-8 -*-
"""论文数据源：解析结果 → (stratum, text) 条目 → 抽样/去重/断点。

复用 StratifiedSampler 通用引擎（sampler.py）。stratum=分档（或 分档|主维度）。
sampler 的 text 仅用于去重与断点（紧凑文本）；next_batch 通过反向映射返回 PaperRecord，
实际 LLM 输入使用 PaperRecord 全字段（title/abstract/keywords/evidence/body_excerpt）。
"""
import re

import paper_config as config
from paper_sampler import StratifiedSampler


def compact_text(record, max_chars=None):
    """构造论文紧凑文本（用于去重/断点，非 LLM 输入）。"""
    max_chars = max_chars or config.DOC_MAX_CHARS
    parts = [record.title, record.abstract]
    if record.keywords:
        parts.append("关键词: " + "; ".join(record.keywords))
    for ev in record.evidence_sentences:
        parts.append(f"[{ev['dimension']}] {ev['text']}")
    parts.append(record.body_excerpt)
    text = re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()
    return text[:max_chars]


class PaperSource(StratifiedSampler):
    """论文数据源。

    records: list[PaperRecord]。stratum: "tier" 或 "tier_dim"。
    next_batch() 返回 list[PaperRecord]。
    """

    def __init__(self, records, stratum="tier", seed=42, dedup=True):
        self.stratum_mode = stratum
        self.records = records          # 必须在 super().__init__ 前设置（loader 会立即消费）
        self._by_text = {}
        super().__init__(loader=self._gen, seed=seed, dedup=dedup)

    def _stratum_of(self, rec):
        if self.stratum_mode == "tier_dim":
            dim = rec.dimensions[0] if rec.dimensions else "无维度"
            return f"{rec.tier}|{dim}"
        return rec.tier or ""

    def _gen(self):
        for rec in self.records:
            text = compact_text(rec)
            if not text:
                continue
            self._by_text[text] = rec
            yield self._stratum_of(rec), text

    def next_batch(self, n, strategy="proportional", min_per=None):
        """取下一批 n 篇 PaperRecord（标记已消费）。"""
        texts = super().next_batch(n, strategy=strategy, min_per=min_per)
        return [self._by_text[t] for t in texts if t in self._by_text]
