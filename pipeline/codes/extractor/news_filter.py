# -*- coding: utf-8 -*-
"""新闻相关性过滤（Stage 0）：LLM 判别 title + 导语，全量通过（无关键词硬门槛）。

成本控制：新闻正文长（avg 6.7k 字符），不可对每篇都喂全文——过滤只看 title + 前 N
字符导语（1 call/篇，max_tokens 小），仅相关新闻进全文提取。

2026-08-15 决策（方案 B）：移除"标题 IT 关键词预筛，未命中直接跳过"的硬门槛——
- 增量层的使命是发现词表之外的新岗位/技能/任务，关键词门槛与新信号发现目标相悖；
- 实测 401 篇中 177 篇（44%）被门槛静默丢弃，其中 40 篇（23%）经 LLM 判定为相关
  （≈全量 10% 信号损失，含 pytorch blog / huggingface / 阿里技术等优质内容）；
- 门槛省下的仅是廉价过滤调用（小 token、1 call/篇），远不抵信号损失。
词表机制已于 2026-08-17 完全移除（08-15 曾降级为统计观察 keyword_hit，因词表自身有
ASCII 子串误配等瑕疵且迁移对比已完成，统计观察角色亦不再保留）。

LLM 失败 → 保守视为相关（不丢信号，宁多处理）。
"""
import os
import re

import yaml

import news_prompts
from llm import ResourceExhaustedError, call_llm

# 词表与导语窗口统一在全局参数中心 codes/settings.yaml → news 节；按路径读取（不经 import config，
# 跨模块导入时不会命中消费方的 config——原"参数须模块内自洽"的限制由此解除）。
_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "settings.yaml")


def _settings(*keys, default):
    """从全局参数中心读取（逐级下钻）；文件缺失/损坏/键不存在回退 default。"""
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            node = yaml.safe_load(f)
        for k in keys:
            node = node[k]
        return node
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        return default


# 过滤导语窗口统一在全局参数中心 codes/settings.yaml → news 节；按路径读取（不经 import config，
# 跨模块导入时不会命中消费方的 config——原"参数须模块内自洽"的限制由此解除）。
NEWS_TITLE_GUIDE_CHARS = _settings("news", "filter_guide_chars", default=800)  # 过滤导语截断；
                              # 2026-08-15 验证：800 字比 200 字对标题党文章多救回 10/177，token 增量可忽略


def _title_guide(record):
    body = re.sub(r"\s+", " ", record.body or "").strip()
    return body[: NEWS_TITLE_GUIDE_CHARS]


def _llm_relevant(record, api_key=None, max_tokens=None, logger=None):
    prompt = (news_prompts.NEWS_FILTER_PROMPT
              .replace("{title}", (record.title or "").strip())
              .replace("{guide}", _title_guide(record)))
    try:
        raw = call_llm(prompt, parse_json=True, max_tokens=max_tokens or 300, api_key=api_key)
        return bool(raw.get("relevant")) if isinstance(raw, dict) else False
    except ResourceExhaustedError:
        raise  # 402 熔断：资源性故障中止运行，不降级保信号（2026-09-02 用户裁定）
    except Exception as e:
        if logger:
            logger.error("filter", f"标题过滤失败 {record.doc_id}: {e}")
        return True  # 保守：过滤失败视为相关（不丢信号）


def filter_relevant(records, api_key=None, max_tokens=None, logger=None):
    """LLM 相关性过滤（全量，无关键词门槛）→ 返回 (relevant_records, stats)。

    stats: {"scanned", "llm_relevant"}。
    """
    relevant, stats = [], {"scanned": len(records), "llm_relevant": 0}
    for rec in records:
        if _llm_relevant(rec, api_key=api_key, max_tokens=max_tokens, logger=logger):
            stats["llm_relevant"] += 1
            relevant.append(rec)
    return relevant, stats
