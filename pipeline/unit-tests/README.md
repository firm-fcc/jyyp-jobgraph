# 系统单元测试说明（unit-tests/）

本目录是系统的完整单元测试套件：**36 个测试文件、178 个用例**——其中 175 例全程离线（零 LLM、零网络），另含 3 例**真实 API 端点的小批量冒烟**（经授权实付调用，每次运行约 3 次请求、合计约数千 token；无密钥环境自动跳过）——全程约 80 秒跑完，对 `codes/` 四个核心包的 50 个模块取得 **62.6% 的语句覆盖率**（赛题要求 ≥60%，口径见 §五）。本文档说明三件事：这套测试验证了系统的哪些组件、如何复现运行结果、以及覆盖率数字的统计口径——使评审者无需通读代码即可确信：**系统的各组件运行正常，且这一结论可以一键复验。**配套三份明细文档：**[TEST-CASES.md](TEST-CASES.md) 逐用例列出输入构造与期望输出**（178 例全覆盖，本 README §一表格的"放大版"）；**[test-cases.csv](test-cases.csv) 单表汇总全部用例的输入/期望输出/属性 + 运行结果/耗时/结果说明**（真调用用例的"结果说明"含实测模型输出，由 `run_tests.py` 自动合并）；**test-results.md 记录逐用例通过状态与耗时**。三份均由 `run_tests.py` 在每次运行后自动重生成。

## 一、测试范围总览

系统是一条按月推进的图谱构建管线（详见仓库 `docs/loop-design.md`）：原始 JD 每月汇入时间线，经岗位归类门（A）进入管线，大窗口先做预抽样（S0），窗口内做抄袭过滤（D0）与分层降采样（S），随后逐句抽取技能/任务（B）、评估熟练度要求（C），最终聚合成基图（D）、叠加论文/新闻/JD 三源演化信号（ΔG）并合成全景图谱；体系本身的演化（新任务/技能/岗位的发现、确证、转正）由热更新引擎完成。本套件按这条数据流逐段设置测试：

| 管线阶段 | 被测模块（codes/…） | 职责一句话 | 测试文件 | 关键验证点 |
| --- | --- | --- | --- | --- |
| **A 岗位归类门** | jd_annotate/`classify_job`、`common`、`classify_stacks` | 词库快路+LLM 兜底两路归类；文本指纹去重 | `test_jd_annotate_gate`、`test_classify_cache`、`test_extractor_classify` | 泛词岗路由（项目经理/运维等按正文定域）、指纹去重、预抽样过滤、严格门口径、缓存新鲜度守卫（CSV/体系/口径任一变更即失效） |
| **A 行级标注** | jd_annotate/`annotate_jd` | 技术栈三层解析+级别四级回退+确定性技术名词层 | `test_annotate_levels_tech` | work_year>正文>标题>funtype 优先级；词边界（java⊄javascript）、版本折叠（MySQL8→MySQL）、位置抑制（Spring Boot 不重复计 Spring）、停用词 |
| **S0 大窗预抽样** | graph/`jd_pre_sample` | 超 6 万指纹的窗口确定性选样+逆概率因子 | `test_jd_pre_sample`、`test_stage_helpers` | 选样确定性、触发/未触发两态、w0=N/k 权重复原 |
| **D0 抄袭过滤** | graph/`jd_dedup` | simhash+Jaccard 双确认的星型聚类 | `test_jd_dedup`、`test_stage_helpers` | 近重敏感/远距不误杀、聚类保最早发布、变体表与消费方同口径 |
| **S 分层降采样** | graph/`jd_sample` | 岗位分层抽样+逆概率权重 | `test_jd_sample`、`test_stage_helpers` | cap/floor 分配数学、同盐可复现、嵌套性（cap 扩大只增不减）、全保时 keys=null |
| **B/C 句级抽取与熟练度** | extractor/`extractor`、`jd_proficiency`、`text_split`、`taxonomy`、`llm_client`、`cache` | 分句→批量分类→句级缓存→量规评估 | `test_extractor_classify`、`test_jd_proficiency`、`test_llm_client_cache`、`test_llm_client_post` | 同句跨运行只判一次（缓存）、按句频聚合、严格契约（重复键/非法枚举拒收）、批级容错 |
| **D 基图/快照/合成** | graph/`base_builder`、`snapshot_builder`、`graph_snapshot`、`synthesis`、`jd_summary`、`replay` | 聚合边权→时间截面→三源叠加→合成有效图 | `test_base_builder`、`test_snapshot`、`test_synthesis`、`test_jd_source`、`test_jd_summary_replay` | 四种边权手算复核、α 衰减跨窗累积、force 守卫拒绝覆盖、合成层绝不触碰基线（md5 断言）、重放计划校验（α 链完整/窗口洞检测） |
| **ΔG 三源演化** | builder/`paper_delta`、`news_delta`、`jd_delta_v2`、`participation` | 论文/新闻/JD 信号→叠层增量 | `test_paper_delta_mention`、`test_news_lazy_parse`、`test_news_sampling`、`test_jd_delta_v2`、`test_jd_source` | 提及确证幂等、跨源 noisy-or、参与可见性门控（遗忘/唤醒）、发现-确证通道 |
| **体系热更新** | builder/`promotion`、`hot_update`、`job_categorize`、`job_hot_update`、`sampler`、`taxonomy_store`、`supervisor`、`apply` | 提案→监督→落库→转正 | `test_promotion`、`test_job_categorize`、`test_job_hot_update`、`test_builder_infra` | 转正双门槛+备份+二次运行收敛、监督层类型规整（字符串 index/"true" 不误拒）、同名防重、抽样器消费断点 |
| **LLM 基础设施** | extractor/`llm`、`llm_client`、builder/`llm` | 多 key 轮转/重试退避/402 熔断/稳健解析 | `test_llm_call`、`test_llm_402_breaker`、`test_llm_client_post` | KeyRing 线程安全轮转、402 先换 key 再熔断、finish_reason=length 预算升级、token 记账 |
| **技能点归一** | graph/`skillpoint_norm` | 三层归一（折叠/别名/LLM 首见） | `test_skillpoint_norm` | Mybatis→MyBatis、K8s→Kubernetes、retired 重映射 |
| **评测指标自身** | graph/`eval_jd_parse` | JD 解析三维度准确率口径 | `test_eval_metric` | 指标配对规则（软匹配/最大余数法配额）可复现、无口径漂移 |
| **LLM 真调用冒烟** | extractor/`llm`、`llm_client`、`extractor`（merged） | 协议契约在真实端点的实付验证 | `test_llm_live` | JSON 契约/句级分类/merged 抽取各 1 次真调用；实测观察写入 CSV 结果说明；无密钥自动跳过 |

数据接入层（`jd_fetch`、`timeline`、`news_signal`、`paper_signal`）负责采集与格式转换，不含算法分支，由端到端运行验证（见 §四"与其他验证手段的分工"）。

## 二、如何运行

环境要求：Python 3.11+，`pytest`、`pytest-cov`、`coverage`（`pip install pytest pytest-cov coverage`）。无需 API 密钥，无需数据集——所有夹具均为临时目录中的合成数据，**测试不读取、不写入任何正式产物**。

```bash
# 方式一（推荐）：一键运行 + 生成覆盖率报告
python unit-tests/run_tests.py

# 方式二：直接跑 pytest（不含覆盖率）
python -m pytest unit-tests/ -q

# 单个文件 / 单个用例
python -m pytest unit-tests/test_jd_sample.py -q
python -m pytest unit-tests/test_base_builder.py::test_decay_chain -q
```

退出码 0 即全部通过；`run_tests.py` 同时生成四份报告（均随仓库提交，可重跑核对）：`coverage-report.md` 覆盖率表、`test-results.md` 逐用例状态与耗时、`TEST-CASES.md` 用例明细、`test-cases.csv` 汇总表。注意 `test_llm_live.py` 的 3 例需 `codes/api-key.txt`（与生产同一密钥文件），无密钥时自动跳过、离线 175 例不受影响。

## 三、测试设计原则

**1. LLM 边界全部注入，核心逻辑全离线；另配 3 例真调用冒烟。** 离线用例中，所有需要调用大模型的环节一律以桩替换：LLM 客户端注入固定应答、`urllib.request.urlopen` 打桩模拟成功/限速/余额耗尽、监督 Agent 注入预设裁决、既有 LLM 判定以缓存文件喂入——因此 175 例可在无密钥、无网络的环境确定性通过。在此之上，`test_llm_live.py` 用 3 次真实调用（JSON 契约 / 句级分类 / merged 抽取各一次）验证同一套协议在真实模型输出上同样成立，并把实测输出记入 `test-cases.csv` 的"结果说明"列（例："熟悉Python开发与数据分析→[T-SW-01, T-DA-04]"）——离线桩保证确定性，真调用保证协议没写错。

**2. 数学口径用手算数值断言。** 凡是公式，测试里都有一笔"手算账"直接对答案：四种边权（J-T/J-S/T-S/S-SP）逐条按分数断言、α 衰减链 `W = 1 + α·W_prev`、降采样逆概率权重 `k×w = n`、技能点软匹配配对与 P/R/F1、分层配额最大余数法。这保证的不是"代码跑了"，而是"算得对"。

**3. 确定性与幂等是硬指标。** 同盐同种子必须得到同一采样（`test_jd_sample`、`test_builder_infra`）；转正/提词落库二次运行必须收敛不重复（`test_promotion`）；预抽样选样随 cap 扩大单调扩展、缓存可衔接（`test_jd_pre_sample`）。

**4. 历史缺陷固化为回归防线。** 多个用例对应开发中真实修过的缺陷，防止复发：泛词岗按标题直收导致约 30% 非 IT 误判（`AMBIGUOUS_JOB_NAMES` 路由）；CSV 字段内嵌换行造成行数虚高约 14 倍（`iter_jd_rows` 以 CSV 解析为准）；监督层 LLM 返回字符串 index/"true" 导致提案全部误拒；推理模型 `finish_reason=length` 截断；余额 402 须先轮转后熔断。

**5. 跨包导入卫生（本仓库特有约束）。** 各 code 包采用平铺导入（包内模块直接 `import config`），而 `config.py`/`llm.py`/`prompts.py` 在 builder 与 extractor 两包各有一份。pytest 在同一进程收集全部测试文件时，若不加管理，先收集的文件会把同名模块"锁"成错误实现。为此每个测试文件头部通过共享助手 `ut.py` 声明依赖：`ut.setup("graph", "builder")` 按序装配 `sys.path`（后声明者优先解析），`ut.isolate()` 弹出跨包冲突模块名，使每个文件的被测依赖都按其声明的包目录解析。这一机制本身经过了实测驱动（迁移时捕获过 `participation` 被错误 config 绑定的污染），是套件稳定性的前提。

## 四、与其他验证手段的分工

单元测试覆盖**确定性核心**；完整的正确性证据链由三层构成：

| 层 | 手段 | 覆盖内容 |
| --- | --- | --- |
| 单元（本目录） | 175 用例离线跑，61.5% 覆盖 | 各模块算法与协议：门控、抽样、聚合、熔断、指标 |
| 集成 | `test-suite/`（121 条真实 JD + Ground Truth 实跑） | JD 解析全链路（A→B→出口）准确率 **92.5%**（赛题要求 ≥90%） |
| 重放 | `codes/graph/replay.py`（零 LLM 全窗重建） | 组装层参数化：46 个窗口产物逐字节可复现 |

因此覆盖率分母中排除的 CLI 入口与批处理编排（见 §五），并非未经验证：`run_jd_extract`（Stage B 主链）的抽取正确性由 test-suite 的 92.5% 集成结果背书；`run_pipeline`/`run_snapshot` 等编排入口由 46 个窗口的真实运行与重放幂等背书。

## 五、覆盖率口径与统计

**分母**：`codes/graph`、`codes/builder`、`codes/extractor`、`codes/jd_annotate` 四包全部 `.py` 模块（50 个、7,800 语句）。**排除**以下五类不属于确定性单测对象的部分（口径固化在 `.coveragerc`，可审计）：

| 排除项 | 理由 |
| --- | --- |
| `run_*.py`（15 个 CLI 入口/编排脚本） | 薄胶水层，由端到端运行与 test-suite 集成验证（§四） |
| `*prompts*.py`（提示词常量） | 无逻辑分支 |
| `logger.py`、`paper_logger.py` | 日志装饰层 |
| `skillpoint_backfill/retag`、`build_jobs/build_taxonomy` | 一次性历史修补/体系初始化脚本，产物入版本库被下游消费 |
| `builder/jd_delta.py` | 已废弃的旧 JD ΔG 抽样（标注 deprecated，由 `graph/jd_delta_v2` 取代，后者在测） |

**结果**（`coverage-report.md` 全量表，2026-09-05 实测）：

| 包 | 覆盖率 |
| --- | --- |
| **口径总计** | **62.6%**（4,886/7,800） |
| codes/graph | 69.2% |
| codes/extractor | 73.3%（真调用用例补齐 llm/llm_client 真实路径） |
| codes/builder | 56.1% |
| codes/jd_annotate | 40.9% |

jd_annotate 偏低的主因：`classify_job.py` 中约 400 语句是批量 LLM 循环与向量比对驱动（属集成路径，LLM 桩只能覆盖协议而无法代表真实链路，由 test-suite 实跑背书）；该包的确定性部分（门控、指纹、缓存、级别、技术名词层）均在测且覆盖充分。

## 六、目录一览

```
unit-tests/
├── README.md               本说明文档
├── run_tests.py            一键运行 + 覆盖率报告生成
├── .coveragerc             覆盖率口径（分母与排除清单，附理由）
├── coverage-report.md      覆盖率报告（自动生成，可重跑核对）
├── conftest.py             pytest 配置（路径装配 + 共享夹具）
├── ut.py                   跨包导入卫生助手（setup/isolate）
├── case_catalog.py         用例目录（单一事实源）：各用例的输入/期望输出/属性数据
├── TEST-CASES.md           逐用例明细：178 例的输入构造与期望输出（§一映射表的放大版）
├── test-cases.csv          汇总表：输入/期望输出/属性 + 运行结果/耗时/结果说明（含真调用实测）
├── test-results.md         逐用例运行结果与耗时（run_tests.py 自动生成）
├── news_delta.json         三源夹具样本（ΔG 产物样例）
├── papers_delta.json       同上
└── test_*.py × 36          测试文件（35 离线 + test_llm_live 真调用；每个用例均有 docstring 说明输入/期望）
```
