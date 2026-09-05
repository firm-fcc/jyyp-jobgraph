# Builder 模块 — 体系构建 / 热更新层（JD 体系 + 论文 ΔG）

构建 / 更新任务、技能体系，并承接**论文 ΔG 增量层热更新**。

## 功能

- **分层抽样 + IT 过滤**：全量 JD 上跑 LLM 成本过高，故按**岗位大类分层抽样**替代（冷启动 `min_coverage` 确保每类岗位都有覆盖；热更新 `proportional` 按层规模占比）。加载时按 JD 文本**去重**（实测 ~32% 重复行），并经 funtype→jobs_v2 匹配**过滤非 IT JD**（2026-08-21 起，行级保留 ~84.7%）
- **冷启动**：分层采样若干文档，LLM 归纳形成**初始简要任务体系**
- **热更新**：多轮向 LLM 投喂数据，通过 **提案 Agent → 监督 Agent → 应用** 迭代完善任务体系，直至新数据被完全囊括
- **论文 ΔG 增量层热更新**（`run_paper_delta.py`）：论文信号（分类层 `codes/extractor/` 提供）→ 聚合进独立 ΔG 增量层 `classify/DeltaG/papers_delta.json`（新岗位 pending / 新任务 / 新技能 / 技能点 / 对既有体系的增强），不并入基础体系
- **岗位热更新（关联分析）**（`run_job_hot_update.py`）：消费 ΔG 中 `status="pending"` 的新岗位，
  LLM 分析其提及文本 → 抽取任务/技能 → 映射到基层（tasks/skills）或叠层（new_tasks/new_skills，新建则入 ΔG）→
  回填岗位的 `related_tasks`/`related_skills`（新岗位不再是孤立节点）
- **子块化**：每轮 `batch_size`（默认 200）按 `chunk_size`（默认 50）拆成子块分别交给 LLM，
  单次提案上下文从 ~24 万字符降到 ~6 万，缓解长上下文注意力问题（`--chunk` 可调）
- **边界判据（岗位化，v0.3）**：任务收录 = 「信息技术岗位的工作职责」而非「任务是否技术性」——
  IT 岗位职责中的组织管理/人员培养/内部培训/跨部门协同（`prompts.IT_GENERIC_DUTIES`）在范围内，
  传统职能核心业务（`prompts.NON_IT_DOMAINS`）排除（初版一刀切曾把技术经理的组织管理职责一并排除）
- **监督防膨胀**：监督 Agent 按「尽量精简、语义充分接近即视为同一任务」原则拦截不必要新增；
  提案/监督提示词含**任务粒度契约**（任务是抽象类别，非岗位/工种/技术栈），`apply` 同名防重、
  批内提案去重、程序化过滤与体系同名的 add，实测遏制过热新增（曾有任务数失控至 194 的历史教训）
- **无 LLM 预览**：`--dry-run` 输出各层抽样计划，验证覆盖情况，不消耗 token
- **全程跟踪日志**：冷启动/热更新提案/监督/应用/重检 全程落盘（人类可读 md + 结构化 jsonl），
  供人类专家检验 LLM 决策、定位任务膨胀原因
- **断点继续**：热更新每消费一个子块即把已处理文档 md5 落盘（`{taxonomy}_checkpoint.json`），
  中断/停止后重跑 `--action hot` 自动恢复，仅丢失当前未完成的子块、从既有体系继续
- **参数中心**：可调参数（LLM/强度权重/批大小/迭代参数）统一在 `codes/settings.yaml`，本模块 config 为薄读取层
- **任务/技能双模式**：`--mode task`（默认，产出 `classify/Tasks/tasks.json`）或 `--mode skill`
  （产出 `classify/Skills/skills_builder.json`（`config.SKILL_BUILDER_OUTPUT`），`detail` 结构兼容
  Extractor。注意：技能体系**当前标准**为文献版 `skills0821.json`（`config.SKILL_TAXONOMY`，
  只读标签源，2026-08-21 起 v0.5 命名规范化版；三体系基准经 `classify/taxonomy_base.json` 单一开关切换）；Builder 版为归纳产物，可对比或复制为 `--taxonomy` 起点做热更新）

## 核心流程

```
抽样：全量 JD → funtype→jobs_v2 匹配（IT 过滤，未命中行丢弃）→ 按 v2 一级类别（9 类）分层 + 去重
冷启动：分层采样 ~500 条（min_coverage，每层≥3）→ LLM 归纳 → 初始任务体系（约 8-30 个精简任务）

热更新（每轮一批 ~200 条，proportional，按子块分批交 LLM）：
  子块（chunk，默认 50 条）→ 提案 Agent（propose）→ 分析子块 vs 当前体系 → add/merge/modify 提案
  监督 Agent（supervise）→ 判断提案必要性（语义接近即拒绝新增）
  应用（apply）→ 增/并/改 落到体系
  重检 → 本子块已覆盖则下一子块；否则**同子块继续精化**（至多 MAX_RECHECK 次，防丢批）
终止：数据源耗尽 或 达到最大轮数
```

## 目录结构

```
codes/builder/
├── README.md          # 本文档
├── config.py          # 配置（API/路径/采样参数；含 ΔG 路径与强度参数）
├── llm.py             # LLM 调用（JSON 解析、重试、可禁用推理）
├── sampler.py         # 通用分层抽样引擎（源无关：分层策略、去重、preview）
├── data_source.py     # 数据源接口 + JD 分层实现 + 注册表工厂（预留 news/paper）
├── taxonomy_store.py  # 任务体系读写（通用 taxonomy 容器）
├── prompts.py         # 提示词模板（冷启动/提案/监督 分离）
├── cold_start.py      # 冷启动
├── propose.py         # 提案 Agent
├── supervisor.py      # 监督 Agent（含 index/布尔类型容错）
├── apply.py           # 应用提案
├── hot_update.py      # 热更新引擎（可复用）
├── logger.py          # 运行跟踪日志（人类可读 md + 结构化 jsonl）
├── builder.py         # 编排器
├── run_builder.py     # JD 体系构建/热更新 CLI 入口
│
│  # ---- 论文/新闻/JD ΔG 增量层热更新（三源） ----
├── delta_store.py     # ΔG 增量层读写（源无关：papers/news/jd）：幂等证据聚合 + noisy-OR 强度 + 编号生成 + JD 确证（confirm_named）
├── paper_delta.py     # 论文 ΔG 流水线编排（解析←paper_signal，分类←extractor，聚合=本层）
├── paper_logger.py    # 论文/新闻/JD ΔG 运行跟踪日志（jsonl + md）
├── run_paper_delta.py # 论文 ΔG 热更新 CLI 入口
├── paper_prompts.py   # 论文提示词拷贝（新信号提取/体系映射/提及识别；与 extractor 同源）
├── news_delta.py      # 新闻 ΔG 流水线编排（解析←news_signal，分类←extractor，聚合=本层）
├── run_news_delta.py  # 新闻 ΔG 热更新 CLI 入口（LLM 相关性过滤省成本）
├── jd_delta.py        # JD 侧 ΔG 旧抽样流水线（已弃用：生产走 graph/jd_delta_v2.py）
├── run_jd_delta.py    # JD ΔG 旧抽样 CLI（弃用保留，对照用）
│
│  # ---- 叠层生命周期 ----
├── participation.py   # 可见性门控：三源 merge 视图按强度过滤参与实体（遗忘=跌破门槛休眠不删除）
├── promotion.py       # 转正：强信号 + JD 确证 → 写基准体系文件（先备份；T-续号/T-DG组/GJ-岗位）
├── run_promotion.py   # 转正 CLI 入口（--dry-run 评估；收口后自动进入类别归纳）
├── job_categorize.py  # 转正后类别归纳（旁路）：LLM 建议 + 人工确认 → 补 GJ- 岗位 category
│
│  # ---- 岗位热更新（ΔG 后处理） ----
├── job_prompts.py     # 岗位关联提示词（新岗位→任务/技能提取 + 关联候选映射）
├── job_hot_update.py  # 岗位热更新编排（消费 ΔG pending 岗位 → 关联分析 → 回填 related_*）
└── run_job_hot_update.py # 岗位热更新 CLI 入口（三源）
```

## 使用方式

### CLI

```bash
python run_builder.py --dry-run                # 预览分层抽样方案（不调用 LLM，验证覆盖）
python run_builder.py --action cold           # 冷启动（分层采样 500 条，每类岗位≥3）
python run_builder.py --action hot            # 热更新（基于已有任务体系）
python run_builder.py --action full           # 冷启动 + 热更新
# 参数
--action cold|hot|full
--mode task|skill      # 构建任务体系（默认）或技能体系
--source jd            # 数据源类型（预留 news/paper/resume）
--sample N             # 冷启动采样条数
--rounds N             # 热更新最大轮数
--batch N              # 每轮投喂条数（默认 200）
--chunk N              # 单次提案交 LLM 的条数（子块大小，默认 50）
--taxonomy PATH        # 任务体系输出路径（默认 classify/Tasks/tasks.json）
--log PATH             # 跟踪日志前缀（默认 classify/Tasks/builder_log.{jsonl,md}）
--no-resume            # 热更新不恢复断点（从头抽样；默认自动恢复断点继续）
--dry-run              # 只预览抽样计划，不调用 LLM
```

**断点继续**：热更新每消费一批数据即把已处理文档 md5 写入 `{taxonomy}_checkpoint.json`（默认
`classify/Tasks/tasks_checkpoint.json`）。中断后重新 `--action hot` 会自动恢复已消费状态，
从既有体系继续处理未覆盖的新批次；`--action full`（冷启动）会清除旧断点（体系重建，
旧数据不再视为已覆盖）。

### 论文 ΔG 增量层热更新（`run_paper_delta.py`）

对学术论文提取前瞻信号并聚合进**独立 ΔG 增量层**（`classify/DeltaG/papers_delta.json`）——
不并入基础体系（新岗位均打 `pending` 标记，供未来岗位热更新模块消费）：

```bash
cd codes/builder

# 只解析论文 + 预览抽样方案（不调 LLM）
python run_paper_delta.py --tier S --limit 20 --dry-run

# S 档全量：解析 → 信号提取（extractor 分类层）→ 体系映射 → 聚合 ΔG
# （默认断点续跑；含 Stage C 基线提及并入 strengthenings，--no-mention 可跳过）
python run_paper_delta.py --tier S

# 探索运行：只处理 N 篇，写入独立增量文件，不动主断点/主产物
python run_paper_delta.py --tier S --limit 20
```

论文的**解析**来自 `codes/paper_signal/`（处理层）、**分类**来自 `codes/extractor/`
（signal_extractor + taxonomy_mapper，分类层）；本层只做 ΔG **热更新聚合**。

### 新闻 ΔG 增量层热更新（`run_news_delta.py`）

对行业新闻提取前瞻信号并聚合进**独立 ΔG 增量层**（`classify/DeltaG/news_delta.json`）——
新闻为辅助信号（权重 `NEWS_SOURCE_WEIGHT=0.4`，半衰期 180 天，弱于论文）：
**仅处理显式信号**（新闻直接提及 IT 技能/任务/岗位），且先 **LLM 相关性过滤**（title + 导语，
无关键词硬门槛——2026-08-15 方案 B）省成本。

```

逐窗时序运行（推荐）：`--window YYYY-MM` 只处理 pub_date 落在**本窗月份内**的论文
（月度增量：窗口 W 只消费 W 月发表的文档，更早月份属其自身窗口、错过即不入场；
断点自动衔接每篇终身处理一次；参与门标签空间 as-of 窗末，体系后续演进不泄漏进
历史窗口）——`python run_paper_delta.py --window 2022-07`。新闻侧 `run_news_delta.py --window`
同口径。bash
cd codes/builder

# 只解析 + 词表命中统计（不调 LLM；词表命中仅为观察指标）
python run_news_delta.py --limit 20 --dry-run

# 小样本探索：只处理 N 篇，写入独立增量文件
python run_news_delta.py --limit 5

# 全量：解析 → 相关性过滤 → 信号提取/提及 → 体系映射 → 聚合 ΔG（默认断点续跑）
python run_news_delta.py

# 仅处理某公众号 / 不恢复断点
python run_news_delta.py --source 量子位 --no-resume
```

新闻的**解析**来自 `codes/news_signal/`（处理层）、**分类**来自 `codes/extractor/`
（news_filter 相关性过滤 → news_extractor 信号提取/提及 → mention_mapper 提及映射 + taxonomy_mapper 体系映射）；
本层只做 ΔG **热更新聚合**（`delta_store` 已泛化为源无关，source_kind=news）。

### JD 侧 ΔG（市场确证）——生产路径 `graph/jd_delta_v2.py`（旧 run_jd_delta 已弃用）

```bash
python codes/graph/jd_delta_v2.py --window 2022-06 --dry-run   # 零 LLM：残差池统计 + TOP 候选
python codes/graph/jd_delta_v2.py --window 2022-06             # 全流程（发现裁决 + 确证）
# 旧抽样路径（保留对照）：cd codes/builder && python run_jd_delta.py --window 2026-05 ...
```

2026-08-27 起 JD 侧发现/确证改为**全量确定性扫描 + 残差 LLM 裁决**（复用本模块 HotUpdater
引擎注入：propose/supervise/apply 纪律不变，投喂从"抽样 JD 全文"换成"全量差集残差候选"、
落点从体系文件换成 ΔG 叠层）——旧 100 条/窗抽样对 0.1% 出现率的新信号漏检 ~90%。详见
`codes/graph/README.md`「JD ΔG v2」。

JD 是市场当前需求的**确证源**（权重 1.0、半衰期 365 天，settings.yaml → strength/jd）：

- **新信号入叠层**：JD 明确要求的体系外任务/技能/技能点 → `classify/DeltaG/jd_delta.json`
- **确证叠层实体**：JD 提及「参与可见」的叠层实体（论文/新闻前瞻信号）→ 同名条目合并
  `src="jd"` 证据（跨源聚合靠快照 norm 合并；确证文档数 = 转正判据）
- **基线提及跳过**：命中基线体系的提及不入叠层（基图频次域已覆盖，避免与 E_jd 重复计权）
- **不从 JD 发现新岗位**：new_job 候选仅在并入既有叠层岗位（merge_into）时生效
- 抽样按 funtype 分层（`jd.sample_total=100`、`per_funtype=3`）；checkpoint 按 jobid

### 叠层生命周期（`participation.py` + `promotion.py`）

```bash
cd codes/builder
python run_promotion.py --dry-run      # 只评估候选（强度 + JD 确证双门槛），不写任何文件
python run_promotion.py                # 执行转正（先自动备份三基准文件到 classify/backup/）
```

- **可见性**（participation）：三源 merge 视图 strength ≥ `overlay.participate_min_strength`(0.15)
  的实体参与下一次更新（papers/news/jd 管线的跨源 delta_items、JD 提取提示词清单、提及扩展标签）
- **遗忘**：无再现 → 半衰期衰减 → 跌破门槛休眠（不参与但**永不删除**，可被新证据唤醒）
- **转正**：strength ≥ 0.25（岗位 0.30）且 JD 确证文档 ≥ 2（岗位 ≥ 3）→ 写入基准体系
  （任务 `T-{续号}` / 技能 `T-DG`·`F-DG` 前瞻转正组 / 岗位 `GJ-{NNN}`，funtypes=[名称]），
  ΔG 源标 `graduated`（下窗口快照移出叠层），日志 `classify/DeltaG/promotion_log.md`
- **类别归纳**（`job_categorize.py`，2026-08-31 起旁路环节）：转正落盘的新岗位
  `category` 为空（`_write_jobs` 不判类别），收口后自动进入 LLM 归纳 + 人工确认
  （回车接受 / code 改判 / s 跳过）→ 备份 `categorize-{ts}/` → 写回 + bump version +
  记 promotion_log；单独补跑 `python job_categorize.py`，`--suggest-only` 只看不写，
  非 tty 自动降级 suggest-only
- 时序：窗口合成之后收口、作用于下一窗口（见 `docs/loop-design.md` §2.4/§3）

### 岗位热更新（`run_job_hot_update.py`）

**ΔG 后处理**：对 ΔG 增量层中 `status="pending"` 的新岗位做**任务/技能关联分析**并回填——
新岗位不再是孤立节点。对每个新岗位：LLM 分析其提及文本 → 抽取核心任务/技能 →
映射到**基层**（tasks/skills）或**叠层**（new_tasks/new_skills，若为新建则加入 ΔG）→
回填 `related_tasks` / `related_skills`。

```bash
cd codes/builder

# 前置：先生成 ΔG 增量层（论文 + 新闻）
python run_paper_delta.py --tier S
python run_news_delta.py

# 列出各 ΔG 文件的 pending 新岗位及证据规模（不调 LLM）
python run_job_hot_update.py --dry-run

# 只处理指定 ΔG 文件（就地回填）
python run_job_hot_update.py --delta classify/DeltaG/papers_delta.json

# 探索：只处理前 N 个岗位，写入 *_explore.json（不动正式产物）
python run_job_hot_update.py --limit 5

# 全部（papers + news 两个增量文件）
python run_job_hot_update.py
```

**关联链接 schema**：`{"taxonomy": "tasks"|"new_tasks"|"skills"|"new_skills", "code": "T-01"|"PT-001"|..., "name_zh": "..."}`
——`tasks`/`skills` 指向基层体系 code，`new_tasks`/`new_skills` 指向叠层 id；下游图谱合成据此解析目标节点。

**要点**：
- 只处理**显式新岗位**（已由论文/新闻流水线保证），本模块不做启发式岗位推导；
- 映射**复用** `taxonomy_mapper.map_signals`（传 `PROMPT_JOB_MAP`）与 `delta_store.apply`；
- 关联产出的新任务/技能证据按合成键 `job_assoc:{job_id}` **幂等合并**（重跑不重复）；
- 新岗位仍 `pending`（绝不写 jobs0806.json，由未来图谱合成/人工审核消费）；
- **命名标准统一**：关联候选 `name_zh` 遵循多源命名规范（任务/技能 4-12 字上限 14），映射层归一化为简洁规范名；超长名由 `fit_name` 边界截断保留、不丢信号（详见 `docs/algorithm-design-v2.md` §2.5）。

### API（供外部/Agent 调用）

```python
from builder import Builder

builder = Builder(source="jd")
builder.cold_start()          # 冷启动
builder.hot_update()          # 热更新
# 或 builder.full()

# 自定义数据源/输出
Builder(source="jd", taxonomy_path="classify/Tasks/tasks.json",
        csv_dir="data/jd_dataset")
```

## 热更新引擎复用（图谱更新等）

`hot_update.HotUpdater` 是通用引擎，通过注入 propose/supervise/apply 函数，
可用于任何"从数据迭代精化结构化产物"的场景（如后续图谱更新）：

```python
from hot_update import HotUpdater

updater = HotUpdater(
    some_store,
    propose_fn=my_propose,
    supervise_fn=my_supervise,
    apply_fn=my_apply,
)
logs = updater.run(data_source, max_rounds=5, batch_size=30, max_recheck=3)
```

> `max_recheck`：同一批数据 propose→supervise→apply 循环精化的重检上限（默认 `config.MAX_RECHECK`），
> 防止 LLM 判断不一致导致"本批未覆盖但已被消费"的内容静默丢失。

## 数据源与分层抽样

**分层抽样引擎与数据源解耦**：

- `sampler.py` 是**通用分层抽样引擎**（源无关），只消费 `(stratum, text)` 条目，负责按层分配配额（`min_coverage` / `proportional` / `uniform`）、按文本去重、跨批不重复；
- `data_source.py` 提供数据源接口 + 具体实现 + 注册表。`JDDataSource` 复用通用引擎，从 JD CSV 加载，**"层" = 岗位 v2 一级类别**，加载时同步完成 IT 过滤（2026-08-21 起，v2 口径；旧 funtype_it_map→jobs0806 口径随映射文件丢失已退役）：

```
JD.funtype ──(norm_part 规范化)──► jobs_v2.json 岗位 funtypes ──► v2 一级类别（stratum，9 类）
                                └── 未命中任何 v2 岗位 → 非 IT，整行丢弃（行级保留 ~84.7%）
```

- **冷启动** `sample(n)`：策略 `min_coverage`，每层至少 `MIN_PER_STRATUM` 条（预算不足自动降档），其余按层规模比例补足 → 覆盖全部岗位大类
- **热更新** `next_batch(n)`：策略 `proportional`，按各层剩余规模占比抽样，已抽批次标记 `_consumed`，跨批次不重复
- **去重**：加载时按文本 md5 去重（JD 实测全量 50.3 万行 → 34.2 万唯一文本）
- **预览**：`preview(n, strategy)` 返回各层配额，`run_builder.py --dry-run` 可直接查看

**为新闻/简历等数据源预留**（当前 `jd` 已实现；news/resume 预留、**尚未实现**）：各源"层"的语义不同（JD 按岗位大类、新闻按来源/分级），接入时实现一个 DataSource（可直接复用 `StratifiedSampler` 引擎，通过 `items`/`loader` 注入条目），再 `register_data_source(kind, cls)` 注册即可，Builder 逻辑不变。参考分层：

- **新闻**：按来源（公众号）/ 相关度 A-B-C 分级
- **简历**：按岗位/技能画像分层
- 数据目录已预留：`config.NEWS_DIR` / `config.FULLTEXT_DIR`

> 论文数据**不**走本数据源注册表——论文 ΔG 热更新由 `paper_delta.py`（`run_paper_delta.py`）独立流水线承接：
> 解析层 `codes/paper_signal/`（PaperSource 复用 StratifiedSampler 引擎）+ 分类层 `codes/extractor/` + 本层 ΔG 聚合。

## 运行跟踪日志（人类专家检验用）

每次冷启动 / 热更新都会**追加**写入两个文件（默认 `classify/Tasks/builder_log.*`，`--log` 改前缀；已 gitignore 不入库）：

| 文件 | 用途 |
|------|------|
| `builder_log.md` | **人类可读** markdown：逐轮记录投喂量、提案（含理由）、监督裁决（批准/拒绝 + 拒绝理由 + 建议并入）、应用动作、重检结果、**每轮任务总数** |
| `builder_log.jsonl` | **结构化**事件流（每行一个 JSON，`ts/stage/数据`），可程序化统计与分析 |

**检验体系构建 / 膨胀归因**：
- 每轮结束记录任务总数 `round{N} 本轮结束，任务总数：**M**`，任务增长一目了然；
- 每项新增都记录监督 Agent 的批准依据，若任务膨胀可定位「哪一轮、哪个提案、监督为何批准」；
- 拒绝项带 `建议并入 T-XX`，可判断监督「精简原则」是否被正确执行。

示例（热更新一轮的日志片段）：

```
- round1/尝试1 提案：covered=False，5 项更新
  - add 质量管理体系与合规审核 | JD片段[6][8]涉及体系认证…
  - add 嵌入式软件与系统开发 | …
- round1/尝试1 监督：批准 2 / 拒绝 3
  - ✓ add 质量管理体系与合规审核
  - ✗ 嵌入式软件开发属于软件研发范畴，应并入T-08软件研发与维护（建议并入 T-08）
- round1/尝试1 应用 2 项更新：`add T-15:…；add T-16:…`
- round1 本轮结束，任务总数：**17**
```

## 设计要点

- **覆盖全面**：冷启动分层保证各类岗位都有任务样本（这是 LLM 归纳任务类别的基础）
- **精简优先**：冷启动限制任务数（8-30）；监督 Agent 拒绝语义接近的新增
- **成本控制**：分层抽样 + 去重 + 截断文档（`DOC_MAX_CHARS`）；默认禁用推理（防推理烧光 token）
- **可追溯**：全程跟踪日志记录每轮投喂、提案理由、监督裁决（含合并建议）、应用动作与任务数变化
