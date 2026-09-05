# 图谱 Loop 设计：新数据 → 分类 & 热更新 → 基图 / 叠层 → 合成 → 转正

> 状态：v2.2（2026-08-28，②③⑥b 改逐窗时序口径：三源 ΔG `--window` 增量 + 参与门
> as-of 窗末；⑥b 换 `graph/jd_delta_v2.py` 采样基面扫描+残差裁决；新增 ⑩ 参数重放）。
> v2.1（2026-08-18）新增 §2.2 跨源同信号识别与可信度；v2.0 引入 JD 侧热更新与叠层
> 生命周期。本文是 `docs/algorithm-design-v2.md` §2.7/§3 的落地实施细则。
> Orchestrator 自动调度仍是 roadmap P2；当前按本文手动顺序执行即可——各步均幂等/断点化，
> 任意一步失败重跑不产生脏数据。

## 目录

1. 单窗口 Loop 总览——流程图 + 命令表
2. 顺序设计原理——2.1 并行依据 · 2.2 跨源同信号 · 2.3 严格先后 · 2.4 转正与纪元
3. 叠层生命周期——可见性 / 遗忘 / 确证 / 转正 / 增强
4. 各步产物与依赖
5. 失败恢复速查
6. 与 Multi-Agent 的映射
7. 已知边界

## 1. 单窗口 Loop 总览

一个时间窗口（月 `YYYY-MM` / 季 `YYYY-Qn`）内，从新数据落地到合成 G_eff、转正收口的完整闭环：

```
                    ┌────────────────────────────────────────────────┐
                    │  ① timeline 编排（新数据 → 月度桶 + 映射表）      │
                    └───────────────┬────────────────────────────────┘
                                    │
      ┌─────────────────────────────┼──────────────────────────────┐
      ▼（JD·基图统计）    ▼（JD·叠层确证）            ▼（前瞻信号）
  ⑥ 基图边计算      ⑥b JD ΔG 热更新              ② 论文 ΔG   ③ 新闻 ΔG
  （分类→频次）      （新信号+确证，权重 1.0）      （抽取→映射→apply）
      │                   │                        └────┬─────┘
      │                   │                             ▼
      │                   │              ④ 岗位热更新（回填 pending 新岗位关联）
      │                   │                             │
      └─────────┬─────────┴─────────────────────────────┘
                ▼
  ⑤ 快照构建（三源 ΔG 合并 + 体系节点；保留基图边；跳过 graduated；附 participates）
                ▼
  ⑦ 图谱合成 → effective/（G_eff = G_base ⊕ ΔG）
                ▼
  ⑧ 校验（snapshot check + synthesis check）
                ▼
  ⑨ 转正收口（强信号 + JD 确证 → 基准体系文件；作用于下一窗口）
```

**命令序列**（`{W}` 为窗口标签，如 `2026-05`；均在对应模块目录下运行）：

| # | 步骤 | 命令 | LLM | 幂等机制 |
|---|------|------|-----|---------|
| ① | timeline 编排 | `cd codes/timeline && python run_timeline.py --jd --news --papers` | 否 | 按文件重建，重复运行覆盖同产物 |
| ② | 论文 ΔG | `cd codes/builder && python run_paper_delta.py --window {W}` | 是 | **月度增量**：只处理 pub_date 落在本窗月份内的论文（更早月份属其自身窗口，错过即不入场）+ 断点衔接（每篇终身一次）；证据按 arxiv_id 幂等。**新任务/技能双道裁决**：映射初判 + 守门终审（宁严勿宽，涵盖/兄弟合并/粒度拒绝——多数短语落 strengthenings 提及级增强） |
| ③ | 新闻 ΔG | `cd codes/builder && python run_news_delta.py --window {W}` | 是 | **月度增量同 ②**（只处理本窗月份发表的文档）+ **月度降采样**（2026-08-30：语料扩至 28 万篇后 cap=800/月——先抽样再筛选，窗口种子确定性可重演，记录落 `data/timeline/news_derived/{W}.sample.json`；上限内月份不受影响）+ **映射优先惰性解析**（2026-08-31：news_mapping.csv 元数据作窗口池，抽样后仅解析抽中 800 篇——全量走盘 ~20 分钟→~4 秒；scandir 对账失步自动回退全量扫描）；新任务/技能双道裁决同 ②；证据按相对路径 doc_id 幂等 + checkpoint |
| ④ | 岗位热更新 | `cd codes/builder && python run_job_hot_update.py --source all` | 是 | 关联产物按 `job_assoc:{job_id}` 幂等合并 |
| ⑤ | 快照构建 | `cd codes/graph && python run_snapshot.py build --window {W}` | 否 | 拒绝覆盖已有窗口；`--force` 重建且**保留已非空基图边**；叠层三源合并（跳过 graduated、附 participates） |
| ⑥ | 基图边计算 | `python codes/graph/run_pipeline.py --window {W}`（S0→A→D0→S→B→C→D 一键；D0=近重复抄袭过滤；**S0=大窗预抽样（2026-09-03）**：unique 指纹 > 60k 才触发，确定性哈希选 60k 个 + w0=N/k 复合进 S 权重（总量无偏），A 门只归类已选键→jobcls→S/D0/B/v2 全链自动受限，未触发窗零行为变化，详见 graph/README） | 是 | 各步幂等/断点；句级缓存跨运行复用；单独重算 D 用 `run_base_build.py --force` |
| ⑥b | JD ΔG v2 | `python codes/graph/jd_delta_v2.py --window {W}` | 是 | **数据基面 = Stage S 采样选择集**（采样后环节不消费基面外文档）：基面扫描（零 LLM）+ 残差裁决（只有 task/skill 经新颖性守门落叠层；skillpoint/alias 只入缓存排水）；写入 `jd_delta.json`（权重 1.0）。**确证已迁移至 Stage B 叠层分类参与**（2026-08-30，原算法设计恢复：出生窗早于本窗的参与实体注入 B 分类提示词，语义命中 → Pass 4 落 require 证据；born_window=体系首次录入窗，压缩回填不提前确证、首叠层窗必无转正；不分出生源；require 级为转正唯一口径）。旧抽样路径 `run_jd_delta.py` 已弃用；子串预筛确证通道已退役 |
| ⑦ | 图谱合成 | `cd codes/graph && python run_synthesis.py build --window {W}` | 否 | 纯函数式重算，直接覆盖 `effective/` |
| ⑧ | 校验 | `run_snapshot.py check` + `run_synthesis.py check` | 否 | 只读 |
| ⑨ | 转正收口 | `cd codes/builder && python run_promotion.py --as-of {W}-末日 [--dry-run]` | 否 | 写前自动备份 `classify/backup/promotion-{ts}/`；graduated 标记后二次运行收敛。**--as-of 必传窗末日**（缺省按今天衰减，历史窗证据会被深度衰减错杀）；确证计数只认 require 级证据（见 2.4） |
| ⑨b | 转正后类别归纳 | 转正收口后自动进入（`codes/builder/job_categorize.py`，旁路环节；单独补跑 `python job_categorize.py`） | 是 | 转正落盘的新岗位 `category` 为空（`_write_jobs` 不判类别），收口后立即对空类别岗位做 **LLM 归纳 + 人工确认**（9 类描述 + 同类现有岗位清单 → 建议 category/confidence/reason；回车接受 / 输入 code 改判 / s 跳过），确认后先备份 `classify/backup/categorize-{ts}/` 再写回并 bump version、记 promotion_log；非 tty 自动降级 suggest-only 只打印不写。不改 propose→确证→转正主链。背景：2026-08-31 前无此环节，GJ-001..005 空类别由前端反馈暴露后人工补齐（v2.6） |
| ⑩ | 参数重放 | `python codes/graph/replay.py --all` | 否 | 改组装参数（强度/生命周期/α/λ/薪资加权）后零 LLM 整链重建 ⑥⑤⑦；α 链要求从最早窗口起；产物带参数指纹 |

## 2. 顺序设计原理

### 2.1 分类与热更新为什么可以并行（②③⑥⑥b 互无先后约束）

核心原则：**JD 分类永远使用固定体系基准，热更新演化只发生在叠层。**

- JD 侧分类（⑥ 的 task/skill 模式）标签源是 `classify/taxonomy_base.json` 指向的固定基准。
  它不读 ΔG、不受任何热更新结果影响 → 无论叠层热更新进行到哪一步，基图边计算结果都相同。
- 三条 ΔG 管线（②③⑥b）**各写各的源文件**（papers/news/jd_delta.json），互不覆盖，
  也不写任何基准体系文件——体系演化信号全部留在叠层。
- ⑥（基图统计）与 ⑥b（叠层信号）是同一 JD 数据的两条互补通道，互不依赖。
- 因此四步可乱序、可并行（生产上可多进程同时跑），都只依赖 ①。
  「不同源出现相同信号时怎么办」见 §2.2。

### 2.2 跨源同信号：如何识别、如何计权（并行为何安全）

**一句话**：写入层尽力合并、快照层权威合并、可信度按证据逐条判源重算——四步的执行顺序
不影响最终快照的内容与强度。

**双层识别**：

| 层 | 机制 | 合并不成功时的后果 |
|----|------|------------------|
| 写入层（尽力而为） | 各管线把「跨源参与实体」**实时**注入映射提示词（每批重算，读的是其他管线逐批落盘的当前文件），LLM 判语义等价则 `merge_into`；JD 确证锚定叠层**规范名**（而非提及变体名，保证与跨源条目同名） | 同一信号在两源各建一条**同名**条目——外观冗余，非错误 |
| 快照层（权威） | ⑤ 的 `merge_delta` 按 `norm(name_zh)` 跨三源分组、证据按 doc_id 并集去重 | 最终识别处：写入层漏掉的合并在此收敛 |

**可信度与顺序无关的四个不变量**：

1. **证据幂等键 = 文档 id**（arxiv_id / 新闻路径 / jobid），同一文档重复处理不重复计权；
2. **贡献按证据自身标记判源**：`src=="jd"` → 权重 1.0/半衰期 365 天；`tier` 键 → 论文档位
   权重/730 天；无标记 → 新闻 0.4/180 天（旧数据兜底）。与证据落在哪个文件、哪条管线先写无关；
3. **强度 = 证据并集的 noisy-or**：并集相同 → 结果相同，与写入顺序无关；
4. **合并后的 id 取舍固定**（papers > jd > news），不随执行顺序变。

**并行的真实代价与边界**：

- 代价：同窗口可能临时双建条目（快照时合并）；跨文件 `merge_into` 的 id 在本源找不到时
  自动降级为同名新建（同样交给快照合并）。
- 边界：`norm` 是**精确归一名**匹配——字面不同的语义近似名能否合并，取决于写入层 LLM 的
  判断（召回问题，顺序执行同样存在）。若 LLM 未合并，信号拆成两条各自计权：强度不会虚高，
  只是未聚合（对转正是保守方向）。

### 2.3 必须严格遵守的几处先后

1. **④ 晚于 ②③（⑥b）、早于 ⑤**：岗位热更新消费 ΔG 中 `pending` 的新岗位、回填
   `related_tasks/related_skills`；快照构建（⑤）的 `job_links` 边从这些回填转换而来。
2. **⑤⑥⑥b 早于 ⑦（同窗口内）**：合成（⑦）读取快照的 base 边 + delta 修正；基图边（⑥）
   写入快照 `base/`；JD ΔG（⑥b）要在快照构建前落盘才能进入本窗口叠层视图
   （若晚于 ⑤，其确证只影响下窗口快照——不产生脏数据，仅晚一个窗口生效）。
3. **⑦ 早于 ⑨**：转正收口在本窗口合成完成后进行（保持本窗口快照的历史准确性）。
4. **跨窗口的 ⑥ 依赖上一窗口的 ⑥**：历史衰减链 `freq = freq_new + α·freq_hist` 读上一
   窗口 `base/freq.json`。跳月处理用 `--prev-window` 显式指定或 `none` 关闭。

### 2.4 转正 = 受控的基图演化通道（与"体系纪元"的关系）

- `classify/taxonomy_base.json` **手动**切换基准 = 新图谱纪元（旧窗口自包含、不回填）。
- **⑨ 转正是唯一的自动基图演化通道**：双门槛（强度 + JD 侧**确证**文档数）满足才写入基准
  文件（T-续号 / T-DG·F-DG 组 / GJ- 岗位），写前自动备份 + promotion_log 可追溯。
  确证有证据等级之分：只有**确证通道**（⑥b 的 LLM 逐句判定"要求掌握"）写入的
  `grade=require` 证据计入确证文档数；**发现通道**的 `grade=scan` 证据（"该词在 JD 中
  出现过"）只贡献强度、不充当确证——否则 v2 当窗发现的实体"出生即转正"，市场确证
  门槛形同虚设。强度衰减以 `--as-of` 传入的基准日计算（逐窗回放传窗末日）。
- 转正作用于**下一窗口**：本窗口快照保持转正前视图；下窗口 ⑤ 的 merge 跳过 graduated
  条目、⑥ 的标签空间天然包含新条目 → J-T/J-S/T-S 边在下窗口自然产生。

## 3. 叠层生命周期

叠层实体的五态循环（实现：`builder/participation.py` + `builder/promotion.py`，
参数 settings.yaml → overlay）：

```
        新证据（论文/新闻/JD）                再次出现（任意源）
   ┌────────────────────────┐         ┌──────────────────────────┐
   ▼                        ▼         ▼                          │
[诞生] ──► [活跃]（strength ≥ 0.15，参与下一次更新的标签空间）◄────┤ 增强：noisy-OR 累积
             │  ▲                                                  │
   半衰期衰减 │  │ 再现唤醒                                          │
             ▼  │                                                  │
           [休眠]（strength < 0.15：不参与映射/不进提示词清单，       │
             │   但条目保留在 ΔG 文件，永不删除）────────────────────┘
             │ 强度 + JD 侧确证双门槛（⑥b 的 src=jd 证据 ≥ 2/3 篇）
             ▼
           [转正]（写入基准体系文件，status=graduated；快照视图移出叠层）
```

| 要素 | 规则 | 对应需求 |
|------|------|---------|
| 参与门槛（可见性） | 三源 merge 视图 strength ≥ 0.15 才进入：各 ΔG 管线的跨源 delta_items、JD 提取提示词的确证目标清单、提及映射的扩展标签 | "只有强度足够的新信号才能参与下一次更新" |
| 遗忘 | 无再现 → 半衰期衰减（论文 730d / 新闻 180d / JD 365d）→ 跌破门槛即休眠；条目与证据完整保留，可被新证据唤醒 | "降级而非删除" |
| 确证 | ⑥b 把 JD 对叠层实体的提及以 `src="jd"` 证据并入（权重 1.0、锚定规范名、跨源靠快照 norm 合并聚合） | "信号在 JD 侧数据中出现"的硬条件 |
| 转正 | strength ≥ 0.25（岗位 0.30）**且** JD 确证文档数 ≥ 2（岗位 ≥ 3）；skillpoints 不转正（随父技能进入基图抽取） | "适当条件下写入基层" |
| 增强 | 证据 noisy-OR 跨文档累积，强度单调上升 | "反复出现增加强度" |

> `MIN_STRENGTH=0.05` 剪枝仅针对**单篇一次性噪声**——质量护栏，不属于遗忘机制。

## 4. 各步产物与依赖

| 步骤 | 读 | 写 |
|------|----|----|
| ① timeline | `data/jd_dataset/`、`data/news/news_raw/`、`data/papers/` | `data/timeline/{jd,news,papers}/` |
| ② 论文 ΔG | `data/papers/`、基准体系、跨源参与实体 | `classify/DeltaG/papers_delta.json` + checkpoint + 日志 |
| ③ 新闻 ΔG | `data/news/news_raw/`、基准体系、跨源参与实体 | `classify/DeltaG/news_delta.json` + checkpoint + 日志 |
| ④ 岗位热更新 | ΔG 三源 `pending` 新岗位 | 就地回填 ΔG 的 `related_*` / 叠层新条目 |
| ⑤ 快照构建 | 基准体系 + ΔG 三源 | `data/graph/{W}/{base 节点, delta 全部}/` + meta（跳过 graduated、附 participates） |
| ⑥ 基图边计算 | `data/timeline/jd/{W}.csv`、上窗 `freq.json` | `data/graph/{W}/base/` 四种边 + skillpoints + freq/entity_freq/build_info |
| ⑥b JD ΔG v2 | `data/timeline/jd/{W}.csv` + jobcls 缓存、已知词表、参与叠层实体（as-of 窗末） | `classify/DeltaG/jd_delta.json` + 裁决缓存 `graph/output/jd_v2_adjudication.jsonl` + 日志 |
| ⑦ 图谱合成 | 快照 base 边 + delta（strengthenings/job_links）+ entity_freq | `data/graph/{W}/effective/`（独立层，只写此处） |
| ⑧ 校验 | 快照 + effective | 无（只读，报错误清单） |
| ⑨ 转正收口 | ΔG 三源合并视图（强度 + src=jd 证据数） | 基准体系三文件（先备份）+ ΔG 源 graduated 标记 + promotion_log |

## 5. 失败恢复速查

- **LLM 中断**（②③④⑥⑥b）：直接重跑同命令。断点 + doc_id/句级缓存幂等；ΔG 证据按
  doc_id upsert，不会重复计入强度。
- **快照想重建**：`run_snapshot.py build --force`（默认保留基图边与技能点；要重算边加
  `--reset-base-edges` 再重跑 ⑥）。
- **合成参数调整**（settings.yaml → synthesis 的 λ）：只重跑 ⑦，秒级、零 LLM。
- **转正出错回滚**：`classify/backup/promotion-{ts}/` 内是写入前的三基准文件，整目录
  拷回 `classify/{Tasks,Skills,Jobs}/` 即可；ΔG 源的 graduated 标记按 promotion_log
  反向清除（或接受标记、条目退回休眠态）。
- **A/B 换体系实验**：`TAXONOMY_BASE_{TASKS|SKILLS|JOBS}` 环境变量临时覆盖后重跑
  受影响步骤；正式切换走 §2.4 的纪元规则。

## 6. 与 Multi-Agent Loop Engineering 的映射

| Agent（v2 设计 §0） | Loop 步骤 |
|--------------------|----------|
| Collector | ① timeline 编排 |
| Extractor | ②③⑥b 的 Stage A/B（信号提取/体系映射/确证提及）、⑥ 的 JD 分类 |
| Graph Builder | ⑥ 基图边计算（`base_builder`） |
| Evolution Analyzer | ②③④⑥b（三源 ΔG 热更新 + 岗位关联 + JD 确证） |
| 合成（v2 §2.6 G_eff 消费） | ⑦（`synthesis`） |
| Quality Guardian | ⑧ 校验 + 参与门槛/转正门槛/单篇噪声剪枝（生命周期护栏） |
| Matching | 快照/合成层的下游只读消费者，不在 Loop 内 |

## 7. 已知边界（当前简版的刻意取舍）

- T-S 边的显式关联项（w₂·I(explicit)）无数据源，恒 0（参数已预留）。
- S-SP 合成修正按父技能 gap（设计公式的降级）：基图技能点无独立 ΔG 强度、
  叠层 PK- 无父技能挂接。
- 粗粒度岗位下放（v2 §2.4）**不再实现**（2026-08-22 决策）：岗位体系 v2 归类引擎
  （`classify_job.py`，岗位名/词库/LLM + 非IT显式标签）对每条数据**直接分类**到具体岗位，
  论文/新闻中的粗岗位称谓无需再沿体系等权下放传播——§2.4 存档为设计备选；
  当前仅 `job_links` 提供新岗位关联。
- 提及识别 → strengthenings 接入**已完成**（2026-08-22）：论文 ΔG 管线 Stage C
  （`paper_delta.strengthen_paper_mentions`，分类式提及直接并入，skill/task 双模式、
  tier 权重、paper_id 幂等）；新闻侧 mention 路径原有实现不变。gap 覆盖面随之变宽，
  合成规则无需改动（`compute_gaps` 消费的就是 strengthenings）。
- JD 侧不从零发现新岗位（岗位体系沿用 51job funtype 分类）；jd_delta 的 new_job 候选
  仅在能并入既有叠层岗位（merge_into）时生效，全新者丢弃。岗位体系 Agent 分类化改造
  后，此约束可自然解除（流水线仅需放开一处丢弃分支）。
- 转正岗位 GJ- 的基图边依赖 funtype/岗位名同名命中（promotion 写入 `funtypes=[name]`）；
  更软的标题匹配留待后续。
