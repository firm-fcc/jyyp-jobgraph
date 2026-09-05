# -*- coding: utf-8 -*-
"""JD 文本分句：将招聘 JD 切分为子句，供逐句技能/任务分类。

切分策略：
1. 按中英文句末标点 / 换行切分（。；！？；换行）
2. 括号/引号内不切分（保护"（如：A；B）"这类结构）
3. 清洗空白，过滤过短/过长句子（config.SENTENCE_MIN_LEN / MAX_LEN）
"""
import re

import config

# 句子边界：中英文句号、分号、感叹号、问号、换行
_BOUNDARY = re.compile(r"(?<=[。；;！？!?])|(?<=\n)")
# 括号内保护：成对括号/引号内部不作为切分点（简单状态机处理）
_OPEN_CLOSE = [("（", "）"), ("(", ")"), ("“", "”"), ('"', '"')]


def _protect_paren(text):
    """把括号/引号内容替换为占位符，避免在其内部切分。返回 (masked_text, restore_list)。"""
    restores = []
    buf = []
    masked = text
    # 简单方法：逐个字符扫描，遇到开括号压栈，闭括号出栈
    stack = []
    protected_ranges = []
    pairs = {o: c for o, c in _OPEN_CLOSE}
    close_to_open = {c: o for o, c in _OPEN_CLOSE}
    for i, ch in enumerate(text):
        if ch in pairs:
            stack.append((ch, i))
        elif ch in close_to_open and stack:
            o, start = stack.pop()
            if pairs[o] == ch:
                protected_ranges.append((start, i + 1))
    # 合并重叠区间
    protected_ranges.sort()
    merged = []
    for s, e in protected_ranges:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    # 替换为占位
    parts = []
    last = 0
    for s, e in merged:
        parts.append(text[last:s])
        restores.append(text[s:e])
        parts.append(f"__P{len(restores)-1}__")
        last = e
    parts.append(text[last:])
    return "".join(parts), restores


def _restore(text, restores):
    for i, r in enumerate(restores):
        text = text.replace(f"__P{i}__", r)
    return text


def split_sentences(text):
    """将 JD 文本切分为子句列表。"""
    if not text:
        return []
    masked, restores = _protect_paren(text)
    # 先按边界切分
    raw_parts = _BOUNDARY.split(masked)
    out = []
    for part in raw_parts:
        p = _restore(part, restores) if restores else part
        p = re.sub(r"\s+", " ", p).strip()
        # 过滤长度
        if len(p) < config.SENTENCE_MIN_LEN:
            continue
        if len(p) > config.SENTENCE_MAX_LEN:
            p = p[: config.SENTENCE_MAX_LEN]
        if p:
            out.append(p)
    return out


def dedupe_preserve_order(items):
    """保序去重。"""
    seen = set()
    result = []
    for it in items:
        if it not in seen:
            seen.add(it)
            result.append(it)
    return result
