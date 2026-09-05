# -*- coding: utf-8 -*-
"""通用分层抽样引擎：对 (stratum, text) 条目按层抽样。

设计：**数据源无关**。不关心文本来自 JD / 论文 / 新闻，只负责：
- 按层分配抽样量（min_coverage / proportional / uniform），保证总目标不超过 n；
- 冷启动 sample() 覆盖全部层；热更新 next_batch() 按层占比且跨批不重复；
- 加载时按文本去重（重复发布多，实测 JD 场景 ~32% 重复行）。

各数据源把 (层名, 文本) 条目通过 items 或 loader 传入即可；"层"的语义由数据源自行定义
（JD 按岗位大类、论文按专题/分档、新闻按来源/相关度分级等），引擎不做假设。

实现 DataSource 接口（sample / next_batch / remaining），并额外提供 statistics() 与
preview()（不调用 LLM、不消费数据即可查看各层抽样计划）。
"""

import hashlib
import json
import os
import random
import re
from datetime import datetime
from collections import defaultdict

import config


def _truncate(text):
    return re.sub(r"\s+", " ", text or "").strip()[: config.DOC_MAX_CHARS]


class StratifiedSampler:
    """通用分层抽样器。

    参数：
      items   可选的 (stratum, text) 条目迭代器（一次性传入）
      loader  可选的零参调用，返回 (stratum, text) 条目迭代器（用于延迟加载）
      seed    随机种子（保证可复现）
      dedup   是否按文本去重（默认 True）
    """

    def __init__(self, items=None, loader=None, seed=42, dedup=True):
        self.seed = seed
        self.dedup = dedup
        random.seed(seed)
        self.docs = []                  # [text]
        self.strata = defaultdict(list)  # stratum -> [doc_index]
        self.hashes = {}                # doc_index -> md5（断点恢复用）
        self._consumed = set()          # 已消费 doc_index（热更新用）
        self._seen = set()              # 去重用（跨 add_items 持久）
        if items is not None:
            self.add_items(items)
        if loader is not None:
            self.add_items(loader())

    def add_items(self, items):
        """追加 (stratum, text) 条目；统一截断、长度过滤与可选去重。"""
        for stratum, text in items:
            text = _truncate(text)
            if len(text) < 20:          # 过滤过短文本
                continue
            h = hashlib.md5(text.encode("utf-8")).hexdigest()
            if self.dedup:
                if h in self._seen:
                    continue
                self._seen.add(h)
            idx = len(self.docs)
            self.hashes[idx] = h
            self.docs.append(text)
            self.strata[stratum].append(idx)

    # ---------------- 抽样核心（与数据源无关） ----------------
    def _candidates(self, exclude_consumed):
        """返回 (stratum -> [idx])，可排除已消费。"""
        if not exclude_consumed:
            return {s: list(v) for s, v in self.strata.items()}
        return {s: [i for i in v if i not in self._consumed] for s, v in self.strata.items()}

    def _targets(self, cand, n, strategy, min_per):
        """按策略计算每层目标抽样数，保证总目标不超过 n。cand: {stratum: [idx]}"""
        avail = {s: len(v) for s, v in cand.items()}
        nonempty = {s: c for s, c in avail.items() if c > 0}
        total = sum(nonempty.values())
        if total == 0 or n <= 0:
            return {}

        if strategy == "uniform":
            # 每层尽量均匀：先 n//层数，余数分给最大的未达标层
            per, extra = divmod(n, len(nonempty))
            targets = {s: min(per, c) for s, c in nonempty.items()}
            for s in sorted(nonempty, key=lambda s: -nonempty[s])[:extra]:
                targets[s] = min(targets[s] + 1, avail[s])
        elif strategy == "min_coverage":
            # 每层至少 min_per 条；预算不足则逐步降档（每层至少 1 条）
            m = min_per or config.MIN_PER_STRATUM
            targets = {s: min(m, c) for s, c in nonempty.items()}
            while sum(targets.values()) > n and m > 1:
                m -= 1
                targets = {s: min(m, c) for s, c in nonempty.items()}
            # 剩余配额按各层剩余量比例补足
            remain = n - sum(targets.values())
            if remain > 0:
                leftover = {s: c - targets[s] for s, c in nonempty.items()
                            if c > targets[s]}
                ltotal = sum(leftover.values())
                if ltotal > 0:
                    for s, c in leftover.items():
                        targets[s] += int(remain * c / ltotal)
        else:  # proportional
            targets = {s: min(int(n * c / total), c) for s, c in nonempty.items()}

        # 防御裁剪：总目标不得超过 n（覆盖 n < 层数 等极端情况）
        targets = self._cap_targets(targets, n)
        # 补足舍入差额（未达标层优先，大层优先）
        diff = n - sum(targets.values())
        if diff > 0:
            unders = sorted((s for s, c in nonempty.items() if targets[s] < c),
                            key=lambda s: -nonempty[s])
            for s in unders[:diff]:
                targets[s] += 1
        # 每层目标数不得超过该层可用数（防止 over-request 时配额失真）
        return {s: min(t, avail[s]) for s, t in targets.items() if t > 0}

    @staticmethod
    def _cap_targets(targets, n):
        """若总目标超过 n，从配额最小的层开始裁剪，保证总目标 ≤ n。"""
        if sum(targets.values()) <= n:
            return targets
        order = sorted(targets.items(), key=lambda kv: (kv[1], kv[0]))
        result = dict(targets)
        over = sum(result.values()) - n
        for k, v in order:
            if over <= 0:
                break
            cut = min(v, over)
            result[k] = v - cut
            over -= cut
        return {k: v for k, v in result.items() if v > 0}

    def _sample_indices(self, n, strategy, min_per, exclude_consumed):
        cand = self._candidates(exclude_consumed)
        targets = self._targets(cand, n, strategy, min_per)
        picked = []
        for s, t in targets.items():
            pool = cand[s]
            if len(pool) <= t:
                picked.extend(pool)
            else:
                picked.extend(random.sample(pool, t))
        return picked

    # ---------------- DataSource 接口 ----------------
    def sample(self, n, strategy="min_coverage", min_per=None):
        """随机分层采样 n 条（冷启动用，不标记消费）。"""
        idxs = self._sample_indices(n, strategy, min_per, exclude_consumed=False)
        return [self.docs[i] for i in idxs]

    def next_batch(self, n, strategy="proportional", min_per=None):
        """取下一批 n 条（热更新用，标记已消费，避免重复）。"""
        idxs = self._sample_indices(n, strategy, min_per, exclude_consumed=True)
        self._consumed.update(idxs)
        return [self.docs[i] for i in idxs]

    def remaining(self):
        return len(self.docs) - len(self._consumed)

    # ---------------- 断点继续（checkpoint） ----------------
    def consumed_hashes(self):
        """已消费文档的 md5 集合（用于持久化断点）。"""
        return sorted(self.hashes[i] for i in self._consumed if i in self.hashes)

    def restore_consumed(self, hashes):
        """按 md5 集合恢复已消费状态（断点继续：跨进程跳过已处理批次）。"""
        hs = set(hashes or [])
        for idx, h in self.hashes.items():
            if h in hs:
                self._consumed.add(idx)

    def save_checkpoint(self, path):
        """把已消费文档 md5 写入断点文件（热更新消费批次后调用）。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"consumed": self.consumed_hashes(),
                       "saved_at": datetime.now().isoformat(timespec="seconds")},
                      f, ensure_ascii=False, indent=1)

    @staticmethod
    def load_checkpoint(path):
        """读取断点文件，返回已消费 md5 列表；不存在返回 None。"""
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("consumed", [])

    # ---------------- 统计 / 预览 ----------------
    def statistics(self, exclude_consumed=True):
        """各层文档数与已消费情况。"""
        cand = self._candidates(exclude_consumed)
        stats = {"total": len(self.docs), "consumed": len(self._consumed),
                 "strata": {s: len(v) for s, v in cand.items()}}
        return stats

    def preview(self, n, strategy="min_coverage", min_per=None):
        """返回每层目标抽样数（不实际抽样、不消费数据），用于验证覆盖情况。

        例：cold 用 min_coverage 可确认每层都有配额；hot 用 proportional 看分布。
        """
        cand = self._candidates(exclude_consumed=False)
        targets = self._targets(cand, n, strategy, min_per)
        return {s: t for s, t in sorted(targets.items(), key=lambda kv: -kv[1])}
