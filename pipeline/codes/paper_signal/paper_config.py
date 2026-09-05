# -*- coding: utf-8 -*-
"""paper_signal（论文数据处理层）配置：论文数据路径 + 解析参数。

可调参数统一读取全局参数中心 `codes/settings.yaml` → papers 节（按路径读文件，与 import 顺序
无关），本文件保留原变量名作薄读取层，内置同值默认兜底。
本层只负责论文数据的解析与数据源胶水，不涉及 LLM 调用与 ΔG 聚合——
- 论文**分类**（信号提取/提及识别）→ `codes/extractor/`
- 论文 ΔG **热更新**（增量层聚合/流水线）→ `codes/builder/`
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


# ---------------- 论文数据目录（2026-08-20 起：全库六专题，仅保留 S/A 档） ----------------
# 布局：data/papers/专题X_…/{S档_核心,A档_重点}/xxx.txt（+ 各专题索引 xlsx 与全库总索引）。
# 2026-08-27 起另有全库批次目录：data/papers/arxiv2022/{S档_核心,A档_重点}/
# （元数据裸格式经 arxiv_ingest.py 转换的标准头块 TXT，无全文正文）。
# scan_papers 兼容旧单专题布局（档位目录直挂本目录）；跨专题同文副本自动去重。
PAPER_DIR = os.path.join(PROJECT_ROOT, "data", "papers")

# ---------------- 解析参数（settings.yaml → papers；截断类与 TXT 头块/正文格式耦合） ----------------
ABSTRACT_MAX_CHARS = _settings("papers", "abstract_max_chars", default=1500)     # 摘要截断
ABSTRACT_MIN_CHARS = _settings("papers", "abstract_min_chars", default=200)      # 无标记 fallback 时要求段落最小长度
KEYWORDS_MAX = _settings("papers", "keywords_max", default=12)                   # 关键词上限
PAPER_CONTEXT_CHARS = _settings("papers", "context_chars", default=2500)         # body_excerpt 截断（喂 LLM 的正文片段）
MIN_CONTENT_CHARS = _settings("papers", "min_content_chars", default=200)        # 正文过短则跳过该论文
BODY_SCAN_LINES = _settings("papers", "body_scan_lines", default=80)             # 正文前 N 行内寻找 Abstract 标记
KEYWORD_SCAN_LINES = _settings("papers", "keyword_scan_lines", default=200)      # 正文前 N 行内寻找 Keywords
HEADER_MARKER = "（以下为论文全文正文）"          # 数据格式绑定，非调参项

# ---------------- 数据源/抽样参数（paper_source/paper_sampler 用） ----------------
DOC_MAX_CHARS = _settings("papers", "sampler_doc_max_chars", default=4000)       # sampler 紧凑文本截断（仅去重/断点，不影响 LLM 输入）
MIN_PER_STRATUM = _settings("papers", "min_per_stratum", default=1)
