# 基于 Multi-Agent Loop Engineering 的岗位能力图谱系统

## 0. 设计概要

系统围绕 **基图 + 叠层** 双塔架构和 **Multi-Agent Loop Engineering** 组织。

### 核心思路

```
JD 数据 ──→ 基图 G_base（市场当前需求）
                │
                ├── J-T 边（岗位→任务）
                ├── J-S 边（岗位→能力，JD 直接提取）
                ├── T-S 边（任务→能力）
                └── S-SP 边（能力→技能点，多对多）

论文/新闻 ──→ 叠层 ΔG（前瞻趋势）
                │
                ├── Δw(J-T)：新兴任务信号
                ├── Δw(J-S)：新兴能力需求信号
                ├── Δw(T-S)：能力结构变化信号
                └── Δw(S-SP)：新技术/工具信号

招聘 JD ────→ 叠层 ΔG（市场确证，权重 1.0）＋ 基图频次（统计域）
                ├── 新信号入叠层（JD 明确要求、体系外任务/技能/技能点）
                └── 对叠层实体的确证证据（转正判据；基线体系提及不入叠层，由基图频次覆盖）

最终图谱：G_eff = G_base ⊕ ΔG
```

- **基图**从 JD 数据构建，是"市场现在需要什么"的客观记录
- **叠层**从论文+新闻计算，是"市场将要需要什么"的前瞻修正。论文为主信号（学术萌芽），新闻为辅助增强（产业采纳）
- 两者分离：基图可独立使用，叠层可插拔、可独立调参

### 四层能力体系

```
Job ──CONSISTS_OF──→ Task ──REQUIRES──→ Skill ──COMPRISED_OF──→ SkillPoint
  │                      ↑                    ↑                      ↑
  │                      │                    │                      │
  └──REQUIRES───────────→┘                    │                      │
         (JD直接提取)                         │                      │
NLP算法工程师        大模型应用开发         编程能力              Python, Java
```

- **Skill** = 稳定能力类别（编程能力、数据分析、AI框架），跨越数年不变
- **SkillPoint** = 具体工具/技术（Python、PyTorch、LangChain），随技术迭代快速更替
- **Skill ↔ SkillPoint 是多对多关系**：Python 同时归属于"编程能力"和"数据处理"；"AI框架"包含 PyTorch、TensorFlow 等多个技能点

### Agent 协作架构

```
                        ┌─────────────┐
                        │ Orchestrator│  ← 调度 Agent、管理 Loop
                        └──────┬──────┘
                               │
   ┌──────────┬──────────┬─────┴─────┬──────────┬──────────┐
   │          │          │           │          │          │
┌──▼───┐ ┌───▼───┐ ┌───▼────┐ ┌───▼────┐ ┌───▼────┐ ┌───▼────┐
│Coll- │ │Extrac-│ │ Graph  │ │Evol-   │ │Quality │ │Match-  │
│ector │ │tor    │ │Builder │ │ution   │ │Guardian│ │ing     │
│      │ │       │ │        │ │Analyzer│ │        │ │        │
└──────┘ └───────┘ └────────┘ └────────┘ └────────┘ └────────┘
 采集      抽取      基图构建    演化分析    质量守护    人岗匹配
```

**数据流：** 新批次 → Collector 采集预处理 → Extractor LLM 提取实体和边证据 → 按来源分流：JD 侧送 Graph Builder 更新基图，论文/新闻侧送 Evolution Analyzer 计算叠层修正 → 合成 G_eff → Quality Guardian 质量巡检 → Matching Agent 可供人岗匹配查询。

**共享图谱状态**是 Agent 间唯一的通信媒介——不直接点对点调用。

---

## 1. 图谱构建（Graph Builder Agent）

只消费 JD 侧数据。四类核心边的强度计算。J-T 和 J-S 都是从 JD 直接提取的显式关联（J-T 来自"岗位职责"段落，J-S 来自"技能要求"列表），T-S 和 S-SP 以统计共现为主、显式关联为辅。

每次更新时，对历史频次数据施加衰减，使新数据对边权重的影响更为突出：

```
freq_updated = freq_new_batch + α · freq_historical
```

其中 α ∈ (0, 1) 为历史衰减系数（如 α = 0.85）。每轮更新后，上一轮的 freq 先乘以 α，再累加新批次数据。α 越接近 1，历史数据保留越久；越接近 0，反应越灵敏。

趋势变化已由叠层负责追踪（见 §2），基图只需忠实地反映近期的 JD 统计，因此不再需要独立的边状态机和近期增强项。

### 1.1 J-T 边：岗位由哪些任务构成

```
base_weight(J, T) = freq(J,T) / freq(J)
```

- `freq(J,T)`：岗位 J 的 JD 中提到任务 T 的文档数（薪资加权 + 历史衰减）
- `freq(J)`：岗位 J 的 JD 总数（薪资加权 + 历史衰减）
- 薪资加权：`weight(jd) = log(1 + salary / salary_median)`

### 1.2 J-S 边：岗位直接要求哪些能力

JD 中"技能要求"部分直接列出能力需求（如"精通 Python 编程""熟悉 Linux 操作"），不经过 Task 中转。

```
base_weight(J, S) = freq(J,S) / freq(J)
```

- `freq(J,S)`：岗位 J 的 JD 中直接列出能力 S 的文档数（薪资加权 + 历史衰减）
- 与 J-T 一致的公式结构和衰减机制

**J-S 与 T-S 的关系：** J-S 是 JD 中直接陈述的需求（"本岗位要求编程能力"），T-S 是任务推导出的需求（"因为要开发模型，所以需要编程能力"）。二者独立统计、独立更新，但在岗位向量构建时共同贡献——J→S 一跳直达 + J→T→S 两跳间接 = 完整的岗位能力画像。

### 1.3 T-S 边：任务需要哪些能力

```
base_weight(T, S) = w₁ · Cooccur(T,S)/Cooccur(T) + w₂ · I(存在 explicit_link)
```

- `Cooccur(T,S)`：T 与 S 在同一 JD 中共现的次数（薪资加权 + 历史衰减）
- `I(存在 explicit_link)`：LLM 提取到显式关联（原文明确说"用 X 做 Y"）时为 1
- w₂ > w₁：显式关联是强信号

### 1.4 S-SP 边：能力类别包含哪些技能点

```
base_weight(S, SP) = Cooccur(S, SP) / Cooccur(S)
```

- 共现来自 LLM 提取时的语义归类——Extractor 将"精通Python"归到"编程能力"和"数据处理"
- **多对多**：一个 SP 可属于多个 S，一个 S 包含多个 SP。权重的分母按各 S 独立归一化
- S-SP 边同样适用历史衰减机制

### 1.5 冷启动

- 岗位体系：招聘平台分类标签 + O*NET 骨架
- 任务体系：从首批代表性 JD 中 LLM 迭代归纳 + 人工审核
- 能力体系：沿用现有技能分类框架（O*NET + 文献分类体系），随 JD 数据积累逐步细化
- 技能点体系：初始为空，随 Extractor 处理 JD 逐步填充

---

## 2. 前瞻叠层（Evolution Analyzer Agent）

基于论文和新闻数据，在前述基图的边上叠加修正权重，反映前瞻趋势。

### 2.1 实体强度与分布差距

```
E_X(t) = 时间窗口 t 内数据源 X 中提到实体 E 的文档数 / 该窗口内 X 的总文档数
其中 X ∈ {jd, arxiv, news}，E ∈ {Task, Skill, SkillPoint}
```

```
gap(E) = max(0, E_arxiv_news(E) - E_jd(E))
```

`E_arxiv_news(E)` 是论文+新闻的加权比例（论文权重 0.7，新闻权重 0.3）。gap > 0 意味着该实体在学术/产业圈的讨论热度超过了招聘市场的当前需求——这是前瞻信号的来源。

### 2.2 边权重修正

```
Δw(J, T)   = λ₁ · gap(T) · I(T 属于 J)     // 新兴任务信号
Δw(J, S)   = λ₁ · gap(S) · I(S 可被J直接要求) // 新兴能力需求信号
Δw(T, S)   = λ₂ · gap(T) · gap(S)           // 能力结构变化
Δw(S, SP)  = λ₃ · gap(SP) · I(SP 属于 S)    // 新技术/工具信号
```

λ 按信号源区分：论文信号的 λ 大于新闻信号的 λ。

### 2.3 叠层衰减

叠层修正不永久保留——若前瞻信号长时间未被 JD 确认，应自动消退：

```
Δw_effective(t) = Δw₀ · e^(-γ · Δt_unconfirmed)
```

- `Δt_unconfirmed`：自 gap 首次检测至今，JD 侧仍未出现确认的月数
- 论文信号衰减更慢（γ_paper 较小），新闻信号衰减更快（γ_news 较大）
- JD 一旦出现确认（gap 缩小），衰减计时器重置
- Δw 衰减至初始值 10% 以下时归零移除

### 2.4 粗粒度岗位的下放

> **实现决策（2026-08-22）**：本节机制不再实现——岗位体系 v2 的逐 JD 归类引擎
> （`classify_job.py`：岗位名/词库/LLM 三层 + 非IT显式标签）直接把每条数据分到
> 具体岗位，粗岗位称谓问题由直接分类消解，无需等权下放传播。本节存档为设计备选。

论文/新闻中的岗位称谓较粗（如"AI 工程师"），不进入基图，仅在叠层中沿岗位分类体系等权传播到子岗位：

```
论文提到 "AI工程师" + T="大模型应用开发"
  → 查分类体系: 子岗位 = ["NLP算法工程师", "CV算法工程师", "大模型算法工程师"]
  → 对每个子岗位: Δw(J_child, T) += Δw_base / |children|
```

岗位分类体系从 JD 数据自底向上构建（LLM 归纳 + 人工校验）。由于传播只影响叠层，即使分类不完美，最坏情况是多了一个微弱修正——基图不受影响，且衰减机制会逐步清理未被确认的信号。

### 2.5 新实体的发现、定义与命名标准

叠层的**新实体**（新岗位/新任务/新技能）只来自论文/新闻的**显式提及**——不做启发式推导（JD 侧遵从网站分类，不参与新岗位发现）。三个数据源模块（论文 `signal_extractor` / 新闻 `news_extractor` / 岗位关联 `job_hot_update`）共享同一套**发现—定义—命名**标准：**发现标准（何时提取）**
- 仅显式信号：数据源**直接提及**新技能/任务/岗位才纳入；禁止推断。
- evidence 必为原文句（逐字引用），无证据不输出。
- 宁缺毋滥：0 条或任意条，**不硬编码数量配额**（配额会被 LLM 当任务填满）。
- 粒度契约：任务=抽象工作类别；技能=稳定能力类别；单一工具/框架/语言归技能点层（不作为技能/任务）。

**定义标准（definition 必填）**
- "它是什么"：1-2 句，脱离原文可理解；不是"为什么重要"（那是 rationale）。
- 缺定义或缺证据的候选直接丢弃（这是发现硬约束，与命名无关）。

**命名标准（name_zh / name_en，跨源统一）**
- name_zh **简洁自足**：任务/技能通常 4-12 个汉字（上限 14），岗位 4-10 个汉字；
  优先「动作+对象」/「限定+核心」结构，避免堆砌"的/与/及"、避免重复词；脱离上下文可理解。
- name_en：论文保留原文术语；新闻/岗位原文出现则保留，无则空。
- 映射层（`taxonomy_mapper`）对候选做**归一化**，把名称收敛为与既有体系一致的简洁规范名。

**命名不合格的降级保留（不丢信号）**
- 超长名（> `MAX_NAME_CHARS=20`）：`fit_name` 在连接词边界截断、保留末尾核心概念，候选**继续进入映射层**由 LLM 归一化——新实体不因命名冗长而丢失。
- 仅 < 2 字的退化名丢弃（无可用内容的真垃圾）。
- 校验层是"护栏"，映射层 LLM 才是最终命名修正处。

**JD 侧确证通道（2026-08-17 增补；2026-08-28 v2 重设计）**：招聘 JD 作为市场当前需求的
确证源参与叠层（`graph/jd_delta_v2.py`，源权重 1.0、半衰期 365 天）——① JD 明确要求的
体系外任务/技能/技能点作为新信号入叠层；② JD 对叠层已跟踪实体的提及作为**确证证据**
（`src="jd"`），是 §2.7 转正的判据；③ 命中基线体系的提及不入叠层（基图频次域已覆盖，
避免与 E_jd 重复计权）；④ 不从 JD 从零发现新岗位（岗位体系沿用 51job funtype 分类）。

**v2 发现机制（全量扫描 + 残差裁决，替代 100 条/窗抽样）**：确定性扫描覆盖基面内 100%
IT JD——英文 token 对已知词表（体系名+技能点注册表+L3 缓存+技术栈关键词）做 norm 折叠
差集；中文按 n-gram（2-8 字，Apriori 逐级上卷）文档频做时间差分，经 df 带宽、边缘函数字
过滤、子串归约、右续延检验、语境词修剪后得到残差池。LLM 仅裁决频次过槛的残差候选
（复用 HotUpdater propose→supervise→apply 纪律；结论跨窗缓存，非技术判定构成永久背景，
首窗为一次性"背景学习"）。旧抽样路径（`builder/run_jd_delta.py`）弃用保留。

**确证通道 = Stage B 叠层分类参与（2026-08-30 迁移，恢复原算法设计）**：叠层信号
**临时插入分类体系**——出生窗早于本窗、达参与门的实体（名称+类型+定义）注入 B 阶段
句级分类提示词，与既有技能/任务并列参与分类；任务/技能句级命中按**语义**判定（含同义/
改写表述）；**岗位按 JD 标题级**批量判对应（标题↔角色画像，宁缺毋滥）。命中统一落
require 级证据（`confirm_named` → `jd_delta.json`，按 JD doc_id 幂等；
date=opentime/confidence=high/src="jd"/grade=require），转正判据 `promote_min_jd_docs`
只认 require 级。v2 原有的子串预筛确证通道已退役（全名精确匹配漏语域错配：论文学名
vs JD 俗名，19 实体 9 窗 0 命中；live 冒烟实证语义改写句可命中——"单点能力模型融合与
联合调优"→「机器人技能学习」类语义等价场景）。句级缓存随 PROMPT_MERGED v3 一次性换血。

**叠层命名纪律与处置机制（2026-08-30，同日经用户复审修订）**：①命名一律从**从业者
视角**（应聘者/员工做什么、会什么），不从机器/系统视角——人可"做"仿真数据增强，
不能"做"机器人技能学习（那是机器在学；人做的是**机器人技能示教**）；②任务=职责活动
（动词性）/技能=能力方法（名词性）；③同一名称不得跨任务/技能两类；④命名取精要
（≤10 字、去"与/及"并列与"能力"冗缀）。纪律已注入五处守门提示词（v2 裁决+新颖性
守门、论文/新闻映射终审、新闻抽取）。存量处置两种语义：**重命名=就地改名+回溯传播**
（store 单条新名条目自出生窗起经 replay 全窗重建统一呈现，改名审计链 rename_history
只留 store；连边锚定 id/code 不受改名影响）；**退役=彻底清除+回溯抹除**
（2026-08-30 终裁：同名碰撞/跨kind 冗余侧/类别名岗位/机制残留共 8 条自 store 删除，
全部窗口经 replay 重建后零出现；源文件与情况说明备份于
`classify/backup/overlay-retire-20260830/`。`remapped_window` 生效窗门保留为
延迟退场的可选工具）。

### 2.6 时间截面存储（图谱表示与持久化）

图谱按**时间截面**组织存储（实现：`codes/graph/`），每个时间窗口（月 `YYYY-MM` 或季度 `YYYY-Qn`）一个文件夹：

```
data/graph/{窗口}/
├── meta.json          # 窗口/粒度/period/来源/权重/统计
├── base/              # 基图 G_base
│   ├── jobs.json / tasks.json / skills.json / skillpoints.json   # 节点（体系 JSON）
│   └── job_task / job_skill / task_skill / skill_skillpoint.json # 边（每种连边一个文件）
└── delta/             # 叠层 ΔG（papers+news 合并为单层）
    ├── new_jobs / new_tasks / new_skills / skillpoints.json      # 节点（PJ-/PT-/PS-/PK-）
    └── strengthenings.json + job_links.json                      # 增强修正 + 新岗位关联边
```

- **全量快照自包含**：节点 + 边在截面内完整存储，任意时间点可直接加载，无需重放历史；数据量小，冗余可接受。
- **节点/边分离**：节点文件 = 实体（四层 + 叠层新实体）；边文件 = 关系（每种连边一个文件，header 含 `relation`/`window`/`total`）。
- **叠层为"截至窗口末"的累积视图**：合并两源（`new_*` 按 `norm(name_zh)`、`strengthenings` 按 `(taxonomy,code)`），证据 `date ≤ period_end` 保留，强度用 `now=window_end` 按来源权重重算（论文主信号/新闻辅信号半衰期不同）。
- **基图边先建空 schema**：J-T/J-S/T-S/S-SP 由后续"图谱构建"任务（G_base 边计算）填充；`strengthenings`/`job_links` 为叠层修正与新岗位关联边的落点。
- **G_eff 消费**：图谱合成从某窗口快照读取 `base` + `delta`，合成 effective 权重（见 §1 边公式 + §2 叠层修正），供可视化与人岗匹配使用。

---

### 2.7 叠层生命周期：可见性 / 遗忘 / 确证 / 转正 / 增强（2026-08-17 增补）

叠层实体不是静态清单，而是有生命周期的信号池（实现：`builder/participation.py` +
`builder/promotion.py`，参数 settings.yaml → overlay；细则见 `docs/loop-design.md` §3）：

- **参与门槛（可见性）**：三源合并视图中 strength ≥ 0.15 的实体才**参与下一次更新**——
  进入各 ΔG 管线映射标签的跨源 delta_items、JD 提取提示词的确证目标清单、提及映射的
  扩展标签。叠层前瞻信号由此影响下一次（而非本次）的图谱更新。
- **遗忘 = 降级而非删除**：周期内无再现 → 半衰期衰减使强度下降 → 跌破参与门槛即**休眠**
  （失去可见性、不参与下次更新），但条目与证据完整保留在 ΔG 文件中，可被新证据唤醒。
- **确证**：JD 侧出现（确证文档数）是转正的硬条件——"信号在 JD 数据中出现"意味着市场
  已开始为该前瞻信号付费，才允许它进入基图。
- **转正（唯一自动基图演化通道）**：strength ≥ 0.25（岗位 0.30）且 JD 确证文档数 ≥ 2
  （岗位 ≥ 3）→ 写入基准体系文件（任务 T-续号 / 技能 T-DG·F-DG 前瞻转正组 / 岗位 GJ-），
  写前自动备份；ΔG 源条目标记 graduated（下窗口快照移出叠层视图），作用于下一窗口
  （新条目进入基图标签空间后，J-T/J-S/T-S 边自然产生）。
- **增强**：反复出现 → 证据 noisy-OR 跨文档累积，强度单调上升。

---

## 3. 动态更新（Loop Engineering）

### 3.1 Orchestrator

使用独立调度进程实现（Python + APScheduler），不做 LLM 驱动的"超级 Agent"。

```
单次 Loop:
① 新批次到达 → Collector 采集预处理
② Extractor 提取实体和边证据
③ 分流: JD 侧 → Graph Builder 更新基图
         论文/新闻侧 → Evolution Analyzer 更新叠层
④ 合成 G_eff = G_base ⊕ ΔG
⑤ Quality Guardian 巡检（抄袭检测、噪声过滤、冗余节点检测）
⑥ 记录版本日志，等待下一批次
```

### 3.2 迭代频率

| 触发条件 | 频率 |
|---------|------|
| 新 JD 批次到达 | 季度 |
| 新 ArXiv 论文批次 | 月度 |
| 手动触发 | 按需 |

### 3.3 版本回溯

每次 Loop 变更记入 `version_history`，支持按时间点回溯图谱状态。

---

## 4. 人岗匹配（Matching Agent）

Matching Agent 是图谱的下游消费者，读取 G_eff 计算匹配，不参与图谱更新 Loop。

### 4.1 基本思路

人岗匹配在 **Skill（能力类别）** 层面进行，不深入到 SkillPoint。Skill 层面足够反映能力结构差异，且比 SP 层面更稳定、计算更轻。

- **岗位向量**：从 G_eff 沿两条路径加权聚合到 Skill 空间，得到 `J_vec = {(skill, effective_weight)}`：
  - 路径一（直接）：J → S（J-S 边的 effective_weight）
  - 路径二（间接）：J → T → S（J-T 权重 × T-S 权重的累积）
  - 两路径权重合并为 Skill 的最终权重
- **简历向量**：Extractor 从简历中提取 SP → 沿 S-SP 边反向映射到 Skill 类别，汇总得到 `R_vec = {(skill, proficiency)}`。proficiency 为简历中各 SP 熟练度按 S-SP 边权重加权归并后的结果
- **匹配得分**：`cosine_similarity(J_vec, R_vec)`
- **差距分析**：将岗位要求的 Skill 分为已具备 / 需提升 / 缺失三类，按 effective_weight 降序给出改进建议

岗位向量使用了 G_eff 的 effective_weight（含叠层修正），因此匹配结果自动反映前瞻趋势——新兴能力即使 JD 中尚未普及，也会通过叠层在匹配中体现。

### 4.2 执行时机

用户提交简历时实时触发，不依赖 Loop 节奏。

---

## 5. 端到端示例

```
第 N 轮 Loop（2026 Q3）

Collector: 50 份 JD + 200 篇 ArXiv 论文 + 30 条新闻

Extractor:
  JD 侧 → Job="AI应用开发工程师", Task=["Agent编排"],
           Skill=["AI框架","编程能力"], SP=["LangChain","Python"]
  JD 侧直接 → J-S: "AI应用开发工程师"-[REQUIRES]->"AI框架"
  论文侧 → Task=["Multi-Agent协作"], SP=["CrewAI","AutoGen"]

Graph Builder（仅 JD）:
  "AI应用开发工程师"-[CONSISTS_OF]->"Agent编排": base_weight=0.45
  "AI应用开发工程师"-[REQUIRES]->"AI框架": base_weight=0.70   // J-S 直接边
  "Agent编排"-[REQUIRES]->"AI框架": base_weight=0.60           // T-S 间接边
  "AI框架"-[COMPRISED_OF]->"LangChain": base_weight=0.55

Evolution Analyzer（论文+新闻）:
  gap("CrewAI") = 0.18 (论文高频，JD 未出现)
  → Δw("AI框架", "CrewAI") = 0.054
  gap("AI框架") = 0.05 (论文中受关注度略高于 JD)
  → Δw("AI应用开发工程师", "AI框架") = 0.012               // J-S 叠层修正

合成:
  "AI框架"-[COMPRISED_OF]->"CrewAI": effective = 0.00 + 0.054 = 0.054
  "AI应用开发工程师"-[REQUIRES]->"AI框架": effective = 0.70 + 0.012 = 0.712

Matching Agent（用户提交简历时）:
  简历提取: SP=["Python:0.9", "LangChain:0.7"]
    → 映射到 Skill: 编程能力=0.9, AI框架=0.7
  岗位向量 J_vec(Skill层): AI框架=0.71, 编程能力=0.48
  简历向量 R_vec(Skill层): AI框架=0.7, 编程能力=0.9
  match_score = cosine(J_vec, R_vec) = 0.86
  差距: AI框架 已具备 ✓ | 编程能力 已具备 ✓ | 建议持续关注 AI框架 的前沿技能点
```

---

## 附录：初版实现优先级

| 优先级 | 模块 |
|--------|------|
| P0 | Extractor Agent + Graph Builder（基图必须可用） |
| P1 | Collector + Evolution Analyzer（叠层核心竞争力） |
| P1 | Matching Agent（赛题核心指标） |
| P2 | Quality Guardian + Orchestrator + Loop |
| P3 | 前端可视化 |
