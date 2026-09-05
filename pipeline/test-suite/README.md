# JD 解析测试套件（自包含：模拟"下一窗口正常运行"并对照 GroundTruth 报告准确率）

对应赛题 XH-202621 可验证性："完整测试方案（含至少 100 条岗位 JD 及测试用例），
JD 解析准确率 ≥90%"。**本文件夹自包含**：假定下一窗口的 JD 数据就是本套件的 121 条，
按正常图谱流程运行即可得到优秀的分类结果；`run_test.py` 模拟该过程并与 GT 对照。

## 快速开始

```bash
python test-suite/run_test.py        # 仓库根运行；exit 0 = ≥90% 达标
```

实测结果（生产缓存命中后近乎零成本，全程 1 次 LLM 调用）：

```
评分集 71/121（IT 拒判不评分——2026-09-03 用户裁定口径）
① 岗位归类（大类）        64/71 = 90.1%
② 任务归类（覆盖≥50%）    65/71 = 91.5%
③ 技能归类（覆盖≥50%）    68/71 = 95.8%
JD 解析准确率（三维平均）= 92.5% ≥ 90% 达标 ✓
```

失败用例明细在 `results.json`（含岗位 gold→system 对照与覆盖率）。

## 文件夹结构

| 文件 | 对应要求 | 说明 |
|---|---|---|
| `jd_corpus.csv` | 数据（≥100 JD） | **121 条 JD，标准 timeline CSV 格式**（原始列全保留），确定性分层抽样自 2026-05（IT 池 9 大类分层 111 + 非IT 10；seed 固定） |
| `ground_truth.jsonl` | GroundTruth | 每条含 it_related/岗位 code+大类/任务/技能（评分字段）与技能点/技术栈（参考字段）；`gold_source` 记录三层链来源（llm+qc / human_adjudicated） |
| `run_test.py` | 测试专门代码 | 生产代码路径原样运行（A 门归类 + merged 句级抽取 + 技术栈规则 + 确定性名词层），对照 GT 报告三维准确率，exit code 表达达标与否 |
| `config/` | 必要文件 | `settings-jd-parse.yaml`（生产 jd_gate/jd_sampling/jd_extract/llm 节点快照；密钥不入库）+ `it_scope.json`（岗位范围） |
| `prompts/` | 必要文件（提示词） | 生产提示词快照：A 门岗位归类（泛词岗内容定域版）+ B 句级 merged 抽取；**运行走生产源**（快照供评审与漂移 diff） |
| `prev_window/` | 上一窗口的必要数据 | `overlay_participants.json`：上一窗口终态（as-of 2026-06-01）的叠层参与实体清单（47 个，B 阶段叠层确证通道输入）；基线体系终态=任务 64/技能 54/岗位 145（classify/ 体系文件即生产源） |
| `README.md` / `results.json` | 说明文档 / 结果 | 本文件 / 最近一次运行的完整指标 |

## 指标定义（官方口径，2026-09-03 用户裁定）

**IT/非IT 拒判不评分**——"是否正确分类为 IT 并不重要，有没有正确地归类其 skill、
task 以及岗位类别为体系中的适当类别才是关键"。评分集 = GT 有体系内归类的 71 条。

**JD 解析准确率 = 三维度用例通过率平均**：
① **岗位归类**：系统岗位大类（9 类）= GT 岗位大类（系统判非IT 即失败）；
② **任务归类**：GT 任务（64 任务体系 code 空间）被系统命中 ≥50%（双空通过）；
③ **技能归类**：GT 技能（54 技能体系 code 空间）被系统命中 ≥50%（双空通过）。

参考口径（`classify/eval/jd_parse/report_2026-05.json` 同源数据并列）：三维全过率
（最严）、细岗 exact、IT 拒判一致率、技能点 F1、micro 复合分。

## GT 的构建（三层链，全部留档可复核）

独立 LLM 标注员（与生产提示词零共享、全部体系清单可见）→ 规则质控（it_related
自洽 + 技能点类目，`classify/eval/jd_parse/qc_audit_2026-05.jsonl`）→ 人工裁定
（7 条逐条引正文证据，`human_adjudication_2026-05.jsonl`）。

## 这些数据用于正常窗口运行（同数据双用途）

`jd_corpus.csv` 就是标准窗口输入格式，可直接作为任一窗口跑正式管线（数量少而已）：

```bash
cp test-suite/jd_corpus.csv data/timeline/jd/2026-06.csv    # 任取窗口名
python codes/graph/run_pipeline.py --window 2026-06          # S0/A/D0/S/B/C/D 一键
# 后续（正式窗口完整链）：jd_delta_v2 → papers/news → snapshot → synthesis
#   → promotion → replay，见 docs/loop-design.md §1
```

说明：① 121 条远小于 cap（S 降采样/S0 预抽样均不触发，全量处理）；② Stage D 基图
聚合需上一窗口基图（`data/graph/2026-05/base`）与体系/缓存等运行数据——在主仓库
或数据迁移包（graph_data_bundle_*.zip）中；③ 语料 opentime 为原 2026-05 日期，
窗口名可任取（各阶段不按窗口名过滤 opentime）；④ 叠层参与清单（prev_window/）
在正式运行中由 classify/DeltaG 三源 store 在线计算，与本套件快照同源。

## 复现性与成本

确定性：抽样 seed 固定；A 门指纹缓存与 B 句级缓存与生产共享（同 JD 永远同判定）。
成本：本机已有生产缓存时全程近乎零成本；全新环境首跑约 100 次 A 兜底 + 300 次
句级调用（≈5-10 元，DeepSeek 谷价更低）。LLM 密钥配置见 `codes/api-key.txt`
（gitignored，不入本套件）。
