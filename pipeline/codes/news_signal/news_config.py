# -*- coding: utf-8 -*-
"""news_signal（新闻数据处理层）配置：新闻数据路径 + 解析参数。

可调参数统一读取全局参数中心 `codes/settings.yaml` → news 节（按路径读文件，与 import 顺序
无关），本文件保留原变量名作薄读取层，内置同值默认兜底。
本层只负责新闻数据的解析与数据源胶水，不涉及 LLM 调用与 ΔG 聚合——
- 新闻**分类**（过滤/信号提取/提及映射）→ `codes/extractor/`
- 新闻 ΔG **热更新**（增量层聚合/流水线）→ `codes/builder/`
"""
import os

import yaml

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

_SETTINGS_PATH = os.path.join(PROJECT_ROOT, "codes", "settings.yaml")


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


# ---------------- 新闻数据目录（本地子集） ----------------
NEWS_DIR = os.path.join(PROJECT_ROOT, "data", "news", "news_raw")

# ---------------- 解析参数（settings.yaml → news） ----------------
MIN_BODY_CHARS = _settings("news", "min_body_chars", default=200)   # 正文过短则跳过（内容为空/仅标题）
# 头字段与分隔线为数据格式绑定（非调参项），随采集格式演进调整
TITLE_FIELD = "标题"
PUB_DATE_FIELD = "发布时间"
CRAWL_FIELD = "爬取时间"
SOURCE_FIELDS = ("公众号", "来源")
BODY_SEP_RE = r"^={10,}\s*$"

# ---------------- 数据源/抽样参数（news_source/news_sampler 用） ----------------
DOC_MAX_CHARS = _settings("news", "sampler_doc_max_chars", default=4000)  # sampler 紧凑文本截断（仅去重/断点，不影响 LLM 输入）
MIN_PER_STRATUM = _settings("news", "min_per_stratum", default=1)
