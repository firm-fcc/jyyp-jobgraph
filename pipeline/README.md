# 图谱构建管线

按月推进的离线批处理管线。输入为招聘岗位说明书、行业新闻与学术论文三类原始数据，
输出为岗位—任务—技能—技能点四层图谱的时间窗口快照，供前端直接读取。

管线不提供在线接口。每个月度窗口独立运行，产物落盘后由 `frontend/data-pipeline/`
转换为前端格式。

## 处理链路

```
招聘 JD ──► A 岗位归类 ──► D0 抄袭过滤 ──► S 分层降采样
                                              │
                                              ▼
                                    B 句级抽取（技能/任务/技能点）
                                              │
                                              ▼
                                    C 熟练度要求评估
                                              │
                                              ▼
学术论文 ──► 解析 ──► 提及识别 ──┐    D 基图聚合（四类边）
                                  │           │
行业新闻 ──► 解析 ──► 相关性过滤 ─┼─► ΔG 增量层 ──► 图谱合成 G_eff = G_base ⊕ ΔG
                                  │                        │
招聘 JD ──► 全量扫描与残差裁决 ───┘                        ▼
                                                   体系热更新与转正
```

基图承载市场当前需求，增量层承载尚未进入招聘要求的前瞻信号。两层独立保存，合成层
不改写基图数值。增量层中的条目经强信号与招聘确证双门槛后转正，写入基准分类体系。

## 模块

| 模块 | 职责 | 产物 |
|---|---|---|
| `codes/jd_fetch/` | 招聘数据获取：数据库读取、职能过滤、导出 CSV | JD 数据集 |
| `codes/timeline/` | 时间线编排：JD 按发布月分文件，新闻与论文建立时间映射表 | 月度 CSV 与映射表 |
| `codes/jd_annotate/` | 岗位归类门与行级标注：关键词快路与模型兜底两路判定，技术栈三层解析 | 归类缓存、标注结果 |
| `codes/extractor/` | 句级抽取层：技能与任务抽取、熟练度评估、论文与新闻的提及识别 | 技能/任务频次向量 |
| `codes/builder/` | 体系构建与热更新：三源增量层聚合、提案与监督、叠层生命周期管理 | 分类体系、增量层文件 |
| `codes/graph/` | 图谱层：预抽样、抄袭过滤、降采样、基图边计算、快照与合成 | 窗口快照与有效图 |
| `codes/paper_signal/` | 论文数据处理：解析为结构化记录 | 内存对象，供上游消费 |
| `codes/news_signal/` | 新闻数据处理：解析为结构化记录 | 内存对象，供上游消费 |
| `codes/job_classify_51job/` | 职能分类源数据的领域判定 | 岗位树 |

各模块的接口与用法示例见其目录内的 README，以及 `docs/code-description.md`。

## 运行

环境要求 Python 3.11 及以上。

```bash
pip install -r requirements.txt
echo "<模型 API key>" > codes/api-key.txt        # 每行一个，支持多账号轮转
```

全部可调参数集中于 `codes/settings.yaml`，包括模型配置、强度权重、半衰期、采样上限
与并发度。调参只改该文件，无须改动代码。

常规月度更新：

```bash
python codes/graph/run_pipeline.py --window 2026-04
```

该编排按序执行预抽样、岗位归类、抄袭过滤、降采样、句级抽取、熟练度评估与基图聚合
七步。各步自带缓存与存在性守卫，重跑只补未完成的部分，`--force-b` 与 `--force-d`
用于显式重建。时序首批需附 `--prev-window none`。

增量层与合成层单独触发：

```bash
python codes/graph/jd_delta_v2.py --window 2026-04           # 招聘侧确证信号
python codes/builder/run_builder.py --action hot --mode task # 体系热更新
cd codes/graph && python run_synthesis.py build --window 2026-04   # 基图与增量层合成
```

原始数据集不随仓库分发，其体量以 GB 计（招聘记录约 580 万条、新闻 28 万篇、论文 1 万篇），
存放路径由 `settings.yaml` 配置。仓库内提供的是完整源码、三套分类体系、评测集与测试套件。

## 分类体系

体系基准由 `classify/taxonomy_base.json` 单点切换，各模块统一从该文件读取标签源。

| 体系 | 当前标准文件 | 规模 | 构建方式 |
|---|---|---|---|
| 岗位 | `classify/Jobs/jobs_v2.json` | 9 个类别、145 个岗位 | 职能分类源数据经领域判定与相近合并，定义由模型阅读真实招聘记录生成 |
| 任务 | `classify/Tasks/tasks.json` | 64 项 | 招聘记录冷启动后多轮热更新，按岗位类别分层采样 |
| 技能 | `classify/Skills/skills0821.json` | 54 项 | O\*NET、ACM CS2023 与 IMDA 三套框架融合，命名规范化 |
| 技术栈 | `classify/TechStacks/techstacks.json` | 8 类，多标签 | 横向分组维度，关键词词表同时用于标注规则 |

岗位条目含定义、判定关键词、边界说明、职能溯源码与招聘信息条数。技能点为开放集合，
由抽取链路产生并经三层归一（版本折叠、别名映射、首见登记），不预先定义。

`classify/eval/jd_parse/` 保存解析准确率的评测集、系统输出、人工裁决与分歧记录。
`classify/docs/` 收录技能体系的构建方案与文献依据。

## 验证

| 手段 | 位置 | 覆盖内容 | 结果 |
|---|---|---|---|
| 单元测试 | `unit-tests/` | 各模块算法与协议：门控、抽样、聚合、熔断、指标 | 178 个用例，语句覆盖率 62.6% |
| 集成测试 | `test-suite/` | 解析全链路的准确率 | 121 条招聘记录，三维度归类准确率 92.5% |
| 重放 | `codes/graph/replay.py` | 组装层的可复现性 | 46 个窗口产物逐字节复现 |

```bash
python unit-tests/run_tests.py      # 单元测试与覆盖率报告
python test-suite/run_test.py       # 解析准确率评测
```

单元测试中 175 例全程离线，无须密钥与网络；另有 3 例针对真实模型端点的小批量冒烟，
无密钥时自动跳过，此时覆盖率为 61.3%。覆盖率口径固化于 `unit-tests/.coveragerc`，
排除项与理由见 `unit-tests/README.md`。

## 文档

| 文档 | 内容 |
|---|---|
| `docs/algorithm-design-v2.md` | 技术设计：基图与增量层双塔结构、多智能体循环工程 |
| `docs/code-description.md` | 代码说明：各模块的功能、接口与用法 |
| `docs/data-description.md` | 数据说明：三类数据的形态、结构与规模 |
| `docs/loop-design.md` | 循环设计：月度窗口的推进方式与生命周期 |
| `docs/cost-estimate.md` | 模型调用的成本测算 |
| `docs/data-migration.md` | 运行数据的打包迁移方式 |
| `introduction/系统介绍.md` | 系统整体介绍 |
| `introduction/岗位分类体系介绍.md` | 岗位体系的类别划分与逐岗定义 |
| `introduction/技能熟练度方案介绍.md` | 熟练度分级的量规与判定方式 |
