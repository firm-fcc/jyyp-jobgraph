# Timeline 模块 — 时间线编排器（JD / 新闻 / 论文）

按时间戳统一编排三类数据，供**图谱按时间顺序导入**（测试技能/岗位随时间演化）。

- **JD** → 按 `opentime` 月份重新分组 → `data/timeline/jd/{YYYY-MM}.csv`
- **新闻 / 论文**（单篇单文件）→ 生成**文件→时间戳映射表** → `data/timeline/{news,papers}/*_mapping.csv`

纯 stdlib、零 LLM 调用。产物在 `data/`（已 gitignore），可脚本重建。

## 为什么需要

- JD 数据按**抓取批次**分文件（如 `job_2026_1_1.csv`），但每行的真实发布时间在 `opentime` 列，
  批次与发布时间错位——按月份重排后才是真实的时间序列。
- 新闻 / 论文已按篇存储，但时间戳埋在各自头字段里——映射表把「文件 → 时间戳」提出来，
  消费方按表排序后顺序读取即可模拟时间推进。

## 时间戳来源（复用解析层，与下游 ΔG 同一时间戳）

| 源 | 时间戳 | 回退规则 |
|----|--------|----------|
| JD | `opentime` 列 | 无（`_unknown.csv` 兜底，实测 0 行） |
| 新闻 | 头部 `发布时间` | `爬取时间`（`news_parser`，缺日期排映射表末尾） |
| 论文 | 头部 `【发表日期】` | arXiv ID YYMM（`paper_parser`） |

## 目录结构

```
codes/timeline/
├── README.md             # 本文档
├── timeline_config.py    # 路径（JD/NEWS/PAPER 输入 + timeline 输出）+ 输出文件名
├── timeline_builder.py   # 核心：build_jd_timeline / build_news_mapping / build_papers_mapping
└── run_timeline.py       # CLI 入口
```

## 使用方式（在模块目录下运行）

```bash
python run_timeline.py --dry-run              # 只打印规模/时间分布计划（不写文件）
python run_timeline.py --jd                   # 只生成 JD 月度文件
python run_timeline.py --news --papers        # 只生成新闻/论文映射表
python run_timeline.py --limit 100            # 探索：每源只处理 100 条，写入 _explore/（不动正式产物）
python run_timeline.py                        # 全部生成
```

参数：

| 参数 | 说明 |
|------|------|
| `--jd / --news / --papers` | 选择要编排的数据源（默认全部） |
| `--out PATH` | 输出根目录（默认 `data/timeline`） |
| `--jd-dir / --news-dir / --papers-dir` | 覆盖输入数据目录 |
| `--dry-run` | 只打印规模/分布计划，不写文件 |
| `--limit N` | 每源限制处理条数（探索用；写 `_explore/` 子目录，不覆盖正式产物） |

## 产物结构

```
data/timeline/
├── jd/
│   ├── 2024-02.csv … 2026-05.csv   # 月度 JD（统一 schema = 源 CSV 并集，缺列填空；行按 opentime 升序）
│   └── _unknown.csv                # opentime 缺失/不可解析的行（兜底，实测为空）
├── news/
│   └── news_mapping.csv   # source_file, doc_id, source, title, pub_date, crawled_at, file_md5
└── papers/
    └── papers_mapping.csv # source_file, arxiv_id, tier, title, pub_date, file_md5
```

**消费方式**：
- JD：按 `jd/` 目录下文件名（`YYYY-MM`）顺序读取月度文件。
- 新闻/论文：读取映射表（已按 `pub_date` 升序，缺日期在末尾），依序加载 `source_file` 指向的原文件。
  `source_file` 相对各自数据目录（`data/news/news_raw/`、`data/papers/`），
  `doc_id`（新闻）与下游 ΔG 证据幂等键一致。

## 数据规模（JD/论文 2026-08-27、新闻 2026-08-30 重建实测）

| 源 | 条数 | 时间跨度 |
|----|------|----------|
| JD | 5,805,597 行（53 个月度文件） | 2021-06 .. 2026-05 |
| 新闻 | 282,944 行（282,159 有日期） | 2015-04 .. 2026-08 |
| 论文 | 10,370 篇（S:248 / A:10,122，跨专题同文去重后） | 2022-01 .. 2026-07 |

论文含两批：六专题语料（2023-07 起，含全文）+ 2022 全库批次 2,273 篇（2022-01..12，
元数据与摘要，`paper_signal/arxiv_ingest.py` 入库）；与 JD 窗口的覆盖缺口仅剩
2021 年最早 4 窗与 2023-01..06。

新闻 2026-08-30 zip 全量补充入库后重建（`news_signal/import_zip.py`：33 源 302,548 篇 →
277,129 入库 + 5,701 同名跳过 + 19,718 短文拒绝；全库仅 1 个 199 字旧桩文件解析失败在映射表外）。
