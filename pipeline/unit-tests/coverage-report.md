# 单元测试覆盖率报告（自动生成，勿手改）

- 生成时间：2026-09-05 18:45
- 用例：`python unit-tests/run_tests.py`（pytest 全量，零 LLM / 零网络）
- 口径：codes/ 四包 50 个核心模块（排除清单见 `.coveragerc` 与 README §五）

## 总览

| 指标 | 数值 |
| --- | --- |
| 覆盖率（语句） | **62.6%**（4,886/7,800） |
| codes/builder | 56.1%（1,094/1,949） |
| codes/extractor | 73.3%（1,359/1,853） |
| codes/graph | 69.2%（1,951/2,820） |
| codes/jd_annotate | 40.9%（482/1,178） |

## 分模块明细

| 模块 | 语句 | 未覆盖 | 覆盖率 |
| --- | ---: | ---: | ---: |
| `codes/builder/apply.py` | 32 | 2 | 93.8% |
| `codes/builder/builder.py` | 49 | 49 | 0.0% |
| `codes/builder/cold_start.py` | 36 | 36 | 0.0% |
| `codes/builder/config.py` | 117 | 22 | 81.2% |
| `codes/builder/data_source.py` | 88 | 88 | 0.0% |
| `codes/builder/delta_store.py` | 242 | 89 | 63.2% |
| `codes/builder/hot_update.py` | 100 | 26 | 74.0% |
| `codes/builder/job_categorize.py` | 103 | 28 | 72.8% |
| `codes/builder/job_hot_update.py` | 226 | 137 | 39.4% |
| `codes/builder/llm.py` | 142 | 73 | 48.6% |
| `codes/builder/news_delta.py` | 170 | 129 | 24.1% |
| `codes/builder/paper_delta.py` | 134 | 94 | 29.9% |
| `codes/builder/participation.py` | 66 | 18 | 72.7% |
| `codes/builder/promotion.py` | 176 | 10 | 94.3% |
| `codes/builder/propose.py` | 15 | 15 | 0.0% |
| `codes/builder/sampler.py` | 132 | 29 | 78.0% |
| `codes/builder/supervisor.py` | 38 | 4 | 89.5% |
| `codes/builder/taxonomy_store.py` | 83 | 6 | 92.8% |
| `codes/extractor/cache.py` | 46 | 2 | 95.7% |
| `codes/extractor/config.py` | 69 | 6 | 91.3% |
| `codes/extractor/extractor.py` | 128 | 23 | 82.0% |
| `codes/extractor/jd_extractor.py` | 116 | 18 | 84.5% |
| `codes/extractor/jd_proficiency.py` | 369 | 114 | 69.1% |
| `codes/extractor/llm.py` | 142 | 33 | 76.8% |
| `codes/extractor/llm_client.py` | 165 | 24 | 85.5% |
| `codes/extractor/mention_mapper.py` | 58 | 22 | 62.1% |
| `codes/extractor/news_extractor.py` | 116 | 90 | 22.4% |
| `codes/extractor/news_filter.py` | 37 | 16 | 56.8% |
| `codes/extractor/paper_mention.py` | 40 | 13 | 67.5% |
| `codes/extractor/signal_extractor.py` | 104 | 48 | 53.8% |
| `codes/extractor/taxonomy.py` | 42 | 4 | 90.5% |
| `codes/extractor/taxonomy_mapper.py` | 359 | 79 | 78.0% |
| `codes/extractor/text_split.py` | 62 | 2 | 96.8% |
| `codes/graph/base_builder.py` | 428 | 118 | 72.4% |
| `codes/graph/eval_jd_parse.py` | 342 | 259 | 24.3% |
| `codes/graph/graph_config.py` | 76 | 3 | 96.1% |
| `codes/graph/graph_snapshot.py` | 161 | 52 | 67.7% |
| `codes/graph/jd_dedup.py` | 171 | 25 | 85.4% |
| `codes/graph/jd_delta_v2.py` | 597 | 184 | 69.2% |
| `codes/graph/jd_pre_sample.py` | 72 | 14 | 80.6% |
| `codes/graph/jd_sample.py` | 103 | 22 | 78.6% |
| `codes/graph/jd_summary.py` | 64 | 18 | 71.9% |
| `codes/graph/replay.py` | 88 | 40 | 54.5% |
| `codes/graph/skillpoint_norm.py` | 178 | 36 | 79.8% |
| `codes/graph/snapshot_builder.py` | 399 | 84 | 78.9% |
| `codes/graph/synthesis.py` | 141 | 14 | 90.1% |
| `codes/jd_annotate/annotate_jd.py` | 389 | 179 | 54.0% |
| `codes/jd_annotate/classify_job.py` | 564 | 411 | 27.1% |
| `codes/jd_annotate/classify_stacks.py` | 147 | 85 | 42.2% |
| `codes/jd_annotate/common.py` | 78 | 21 | 73.1% |
