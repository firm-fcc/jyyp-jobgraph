# -*- coding: utf-8 -*-
"""时间线编排器：按时间戳统一编排 JD / 新闻 / 论文，供图谱按时间顺序导入。

- **JD** → 按 `opentime` 月份重新分组 → `data/timeline/jd/{YYYY-MM}.csv`
- **新闻 / 论文**（单篇单文件）→ 生成文件→时间戳映射表 → `data/timeline/{news,papers}/*_mapping.csv`

纯 stdlib、零 LLM 调用；日期逻辑复用解析层（paper_parser / news_parser）。
"""
