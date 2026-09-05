# -*- coding: utf-8 -*-
"""时间线编排器（timeline）配置：三类数据路径 + 输出目录。

本模块把 JD / 新闻 / 论文 按时间戳统一编排成时间线（供图谱测试时按时间顺序导入）：
- JD → `data/timeline/jd/{YYYY-MM}.csv`（按月分组，统一 schema）
- 新闻 / 论文 → `data/timeline/{news,papers}/*_mapping.csv`（文件→时间戳映射表）

纯 stdlib、零 LLM 调用。`data/` 已 gitignore，产物可脚本重建。
"""
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# ---------------- 输入数据目录 ----------------
JD_DIR = os.path.join(PROJECT_ROOT, "data", "jd_dataset")
NEWS_DIR = os.path.join(PROJECT_ROOT, "data", "news", "news_raw")
PAPER_DIR = os.path.join(PROJECT_ROOT, "data", "papers")  # 全库六专题 S/A（scan_papers 兼容两层布局）

# ---------------- 输出目录（data/timeline） ----------------
TIMELINE_DIR = os.path.join(PROJECT_ROOT, "data", "timeline")
JD_OUT_SUBDIR = "jd"
NEWS_OUT_SUBDIR = "news"
PAPER_OUT_SUBDIR = "papers"

# ---------------- 输出文件名 ----------------
JD_UNKNOWN_FILENAME = "_unknown.csv"       # opentime 缺失/不可解析的行（兜底，实测为空）
NEWS_MAPPING_FILENAME = "news_mapping.csv"
PAPER_MAPPING_FILENAME = "papers_mapping.csv"
