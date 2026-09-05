# 代码说明文档

> **文档维护说明**：本文档说明 `codes/` 目录下各项目的功能与用法。**每次新增/修改代码项目时，需同步更新本文档**（新增项目在 §3 追加小节，变更在文末「更新日志」登记）。查询代码现状时以本文档为第一入口。

---

## 1. 目录总览

```
codes/
├── api-key.txt                 # DeepSeek API key（敏感，勿提交 git）
├── config.yaml                 # MySQL 连接配置（job51 库）
├── job_classify_51job/         # 51job 岗位分类（IT 判定）
├── jd_fetch/                   # 招聘 JD 数据获取与 funtype 过滤
├── jd_annotate/                # JD 标注与分类体系构建（技术栈/级别标注 + 岗位体系 v2）
└── extractor/                  # JD 技能/任务抽取
```

**数据流**：`job_classify_51job`（构建岗位体系）→ `jd_fetch`（按 funtype 过滤抓取 IT JD）→ `extractor`（从 JD 抽取技能/任务）。

---

## 2. 顶层文件

| 文件 | 说明 |
|------|------|
| `api-key.txt` | DeepSeek API key，格式 `(provider) label: sk-xxx`；LLM 相关脚本默认读取此文件（优先级见 `CLAUDE.md` §LLM API） |

> `config.yaml`（MySQL 连接配置）已随其唯一使用者移入 `jd_fetch/`，见 §3.2。

---

## 3. 子项目

### 3.1 `job_classify_51job/` — 51job 岗位分类（IT 判定）

从 51job 岗位职能全量分类中筛选信息技术相关岗位，输出 IT 岗位判定与分类树。

| 文件 | 功能 |
|------|------|
| `classify_jobs.py` | 主脚本：读取 `classify/docs/51job_classify/dd_funtype_translation.json`，用 `deepseek-v4-flash` 逐节点判定是否 IT（含置信度），构建 IT 岗位树。支持 `--resume` 断点续跑 |
| `repair_jobs.py` | 失败补跑 + 重建输出：对 API 失败的路径用小批量重试并重建最终 JSON |

**输出**：`output/51job_it_jobs_classified.json`（节点级判断 `judgments_by_node` + IT 树 `it_classification`）、`output/progress.jsonl`（逐批缓存）。

**产物**：该结果经 `docs/job_classification.json`（含映射标注）与 `classify/Jobs/jobs0806.json`（255 个唯一岗位）呈现。

### 3.2 `jd_fetch/` — 招聘 JD 获取与 funtype 过滤

从 MySQL 库按 funtype 过滤抓取 IT 相关 JD，导出 CSV 数据集。支持两批数据：远端 `job51` 库（2025-2026 表）与本地 `51job` 库（JD-Origin 早期数据，2022-2024 表，2026-08-15 并入）。

| 文件 | 功能 |
|------|------|
| `config.yaml` | MySQL 连接配置（`jobDescription_TC` 段：ip/port/username/password/db_name + 27 张 `job_*` 表清单）；指向远端 job51 库（2025-2026 表） |
| `config_origin.yaml` | JD-Origin 本地库配置（127.0.0.1 `51job` 库，74 张表清单由 `import_origin.py` 自动回填） |
| `config.py` | 读取配置 yaml（`load_config(path)` 可指定文件），提供 MySQL 连接与表清单 |
| `gather_funtypes.py` | 收集所有 job 表的 distinct funtype（按 `or` 拆分），输出 `output/all_funtype_parts.json` |
| `merge_classify_funtypes.py` | 规则 + LLM 合并：对体系外 funtype 做语义归类/IT 判定，输出 `output/funtype_it_map.json`（依赖原机器生成的 `51job_it_jobs_classified.json`，本机缺失时用 `rebuild_it_map.py` 重建等效映射） |
| `import_origin.py` | **JD-Origin 导入**（一次性）：`.zst` 全库 dump 流式过滤（awk 状态机只保留 job 表族）+ 28 个单表 dump 导入本地库；断点续跑（`output/import_progress.json`）、逐表行数校验（COUNT == dump AUTO_INCREMENT-1）、binlog 会话级禁用 + buffer pool 临时调优 |
| `rebuild_it_map.py` | **funtype IT 映射本地重建**（原 `funtype_it_map.json` 未随仓库同步）：gather 本地库 parts → 黑名单（2026-08-06 修正）/ CSV 种子 / 体系名种子 / 映射记录种子 / 规则合并（复用 `merge_classify_funtypes.rule_merge`）/ LLM 兜底（复用 `classify_jobs.call_api`）→ `output/funtype_it_map_origin.json`；`--skip-llm` 预览覆盖率、`--no-gather` 复用已有 parts |

> **funtype_it_map.json 重建评估（2026-08-22）：无需重建。** 该映射的唯一运行时用途是 `fetch_jd.py` 增量拉取时的 funtype 级 IT 预过滤（数据集 590 万条已全部落库，无拉取任务在排）；采样/分层已切 jobs_v2 直连口径（`builder/data_source.py`），内容级非 IT 排除改由 `classify_job.py` LLM 显式 `非IT相关` 标签承担。将来若需增量拉取，IT funtype 集合可从 `jobs_v2.json` detail 的 funtypes 确定性派生（查表，无需本工具的 LLM 流程）；`rebuild_it_map.py` 保留作历史工具。 |
| `fetch_jd.py` | 按 funtype IT 映射过滤抓取 IT JD，导出 `data/jd_dataset/{table}.csv`（`--limit-per-funtype 0`=全量；`--config`/`--map`/`--out-dir` 可切数据源与映射）；summary.json **合并写入**（保留既有 meta 与表条目，meta 加 `origin_extension`） |
| `verify_origin_export.py` | JD-Origin 导入/导出验证（一次性）：导入完整性汇总 + IT 占比 sanity + 随机抽样比对库内记录 + 跨快照 jobid 去重/重叠/opentime 分布 → `output/import_report.md` |
| `annotate_classification.py` | 把 funtype→岗位映射标注进 `docs/job_classification.json` |
| `enrich_summary.py` | 为 `data/jd_dataset/summary.json` 补充整体元信息 |
| `classify_funtypes.py` | （早期版，被 `merge_classify_funtypes.py` 取代） |

**输出**：`output/funtype_it_map.json`（2025-26 批，原机器）与 `output/funtype_it_map_origin.json`（JD-Origin 批，211/1161 IT）、`data/jd_dataset/`（62 个 CSV：22 个 2025-26 批 50.3 万条 + 40 个 JD-Origin 批 540.6 万条，共约 590 万条 IT JD）。

### 3.3 `extractor/` — 分类抽取（JD + 论文）

从**招聘 JD** 提取技能/任务及其**技能点**并计数；从**学术论文**识别对既有体系的**提及**（提及识别）并做**新信号分类**（供 builder 的 ΔG 热更新消费）。技能体系基于 `skills0821.json`（49 项，v0.5 命名规范化版：仅 20 项 name_zh 更名、编码/定义不变；2026-08-21 起为当前标准，`skills0805.json` 为前版存档，`skills_builder.json` 为 Builder 归纳产物留作对比），任务体系基于 `tasks.json`（27 项 v0.3）。三体系基准统一由 `classify/taxonomy_base.json` 切换（单一开关，环境变量 `TAXONOMY_BASE_{TASKS|SKILLS|JOBS}` 可临时覆盖）。

| 文件 | 功能 |
|------|------|
| `config.py` | 配置（API key、模型、路径、批大小、缓存目录；含 `JOB_TAXONOMY`） |
| `taxonomy.py` | 加载技能/任务/岗位体系 → 统一标签列表（`load` / `load_jobs`） |
| `text_split.py` | 文本分句（括号保护、长度过滤） |
| `cache.py` | 句级结果缓存（持久化 JSONL，重复句复用） |
| `prompts.py` | JD 分类提示词（技能/任务分离；技能点仅限工具/框架/语言实体） |
| `paper_prompts.py` | 论文提示词（新信号提取 + 体系映射 + 提及识别；唯一命名，builder 有同源拷贝） |
| `llm_client.py` | LLM 调用（批量、重试、禁用推理提速；`classify_with` 支持自定义模板） |
| `llm.py` | 函数式 `call_llm`（论文新信号分类用；与 builder/llm.py 同源） |
| `extractor.py` | JD 抽取核心（分句→缓存→分类→计数；`_classify_units` 可复用），供 Agent 调度 |
| `run_extractor.py` | JD 抽取 CLI（`--mode skill|task`） |
| `jd_proficiency_prompts.py` | **JD 熟练度量规**（P1-P4/U + D1-D4 的 JD 版语义：梯度词为雇主要求一级证据、防通胀、罗列→U）+ 提示词模板 |
| `jd_proficiency.py` | **JD 技能熟练度评估核心**（2026-08-21，移植同项目简历侧交接方案）：句级分类缓存取证据 → 分块 LLM 量规评估 → 严格输出契约校验（重复键/多余字段/非法枚举拒绝，整块重试）→ 确定性旗标复核（`marker_level_conflict`/`p4_without_high_signals` 等，只打旗不改级）；指纹缓存 `output/jd_prof_cache.jsonl`（rubric 版本变更自动失效）；6 个聚合信号技能跳过定级 |
| `run_jd_proficiency.py` | 熟练度校准 CLI（`--window/--n/--no-cache`）：抽样（与基图生产口径一致）→ 评估 → 校准报告（等级分布/旗标率/词面锚点×等级交叉表，`output/jd_prof_calibration.md`） |
| `paper_mention.py` | **论文提及识别**：识别论文文本对既有技能/任务/岗位的提及（含证据） |
| `run_paper_mention.py` | 论文提及识别 CLI（`--mode skill|task|job`；走 paper_signal 解析，含断点续跑） |
| `signal_extractor.py` | 论文**新信号提取**（Stage A，迁自 paper_signal）：new_job/new_task/new_skill/implied_task/capability_gap/skillpoint |
| `taxonomy_mapper.py` | 体系映射（Stage B，迁自 paper_signal）：候选 × 基础体系 → map_to / merge_into / is_new / reject |
| `news_prompts.py` | 新闻提示词（相关性过滤 / 信号提取 / 提及映射；唯一命名） |
| `news_filter.py` | 新闻**相关性过滤**（Stage 0）：LLM 判别 title + 导语（前 800 字）全量通过，仅相关进全文；无关键词硬门槛（关键词词表已于 08-17 完全移除） |
| `news_extractor.py` | 新闻**信号提取**（Stage A）：相关新闻 → 新信号（name+定义+证据）+ 提及名称 |
| `mention_mapper.py` | 新闻**提及映射**：提及名称 → 既有体系 code（norm 精确匹配 + LLM 兜底） |

**JD 抽取输出**：`skill_counts`（技能句频）+ `skillpoint_counts`（技能点句频）+ `skill_skillpoint_map`（技能→技能点映射字典）。技能点严格限定为工具/框架/语言等具体技术实体，软技能无技能点。

**JD 熟练度要求判定**（2026-08-21）：对 JD 中明确要求的每个技能（49 体系按 code 连接，与简历侧 team_skills ID 空间逐一对齐），评估岗位的用人要求等级 **P1-P4/U**（量规 D1-D4：自主性/复杂度/技术判断/责任影响；U=仅罗列无梯度表述，≠低要求）。precision-first 三段防线：量规注入 → 严格契约 → 正则旗标；词面锚点（精通/熟练/熟悉/掌握/了解 + 年限）不作定级快路，仅作 prompt 提示与事后一致性旗标。2026-05 窗口 200 JD / 986 对校准：P2 40.7% / P3 28.7% / U 23.9% / P1 4.1% / P4 2.4%（防通胀保守），契约失败仅 0.2%。消费方：`graph/base_builder.py` 每窗口聚合写 `base/skill_prof.json`（演化分析输入）。

**论文提及识别**：提单元 = 标题 + 关键词 + 摘要分句 + 证据句；输出每篇论文 `mentions`（{code: 句频}）+ `evidence`（命中的原文单元）+ 跨论文聚合（`classify/DeltaG/papers_mentions.json`）。这是 ΔG `strengthenings` 与演化分析的输入（"哪些已有能力被前沿研究引用"）。

**新实体发现—定义—命名统一标准**（论文/新闻/岗位关联三源共享，详见 `docs/algorithm-design-v2.md` §2.5）：
- **发现**：仅显式信号（直接提及新技能/任务/岗位）、evidence 逐字原文、宁缺毋滥、粒度契约（工具/语言归技能点层）。
- **定义**：`definition` = "它是什么"，1-2 句自足；缺定义或证据直接丢弃（发现硬约束）。
- **命名**：`name_zh` 简洁自足（任务/技能通常 4-12 字上限 14，岗位 4-10 字），映射层 LLM 归一化为与体系一致的简洁规范名。
- **降级保留（不丢信号）**：超长名（> `MAX_NAME_CHARS=20`）由 `fit_name` 在连接词边界截断、保留末尾核心概念，候选**继续进入映射层**归一化；仅 < 2 字的退化名丢弃。校验层是护栏，映射层 LLM 才是最终命名修正处。

**用法示例**：
```bash
python run_extractor.py --mode skill --input ../../data/jd_dataset/job_2026_05_30.csv --limit 100 --output out.json
python run_paper_mention.py --mode skill --tier S --limit 20     # 论文提及识别（论文数据）
python run_paper_mention.py --mode job --input "某论文摘要文本"    # 论文提及识别（原始文本）
python run_jd_proficiency.py --window 2026-05 --n 200    # JD 熟练度校准（真实 LLM）
```
详见 `extractor/README.md`。

### 3.4 `builder/` — 任务体系构建/更新 + 三源 ΔG 热更新 + 叠层生命周期

从招聘数据构建与更新**任务体系**（冷启动 + 热更新），采用「提案 Agent → 监督 Agent → 应用」迭代精化。全量 JD（50 万+）跑 LLM 成本过高，故用**分层抽样**替代：按岗位大类分层，冷启动 `min_coverage` 保证各类岗位都有覆盖，热更新 `proportional` 按层规模占比，加载时按文本去重。

2026-08-21 任务体系 **v0.2 重建（净化）**：① 分层与过滤改 v2 口径——funtype part（norm_part 与 build_jobs 同口径）匹配 `jobs_v2.json` 岗位 funtypes，未命中即非 IT 丢弃（行级保留 84.7%），层 = v2 一级类别 9 类；修复旧 `funtype_it_map.json` 口径随文件丢失静默退化（全量「其他」层）且无 IT 过滤、非 IT JD 混样的问题（v0.1 因此含 13 个非 IT 任务）；② `prompts.py` 六模板加**非 IT 域边界**（`NON_IT_DOMAINS` 统一排除清单，监督侧「非 IT 域零容忍」），并替换提案示例中「法务可 add」的错误教唆；③ 重建结果：25 个 IT 任务（冷启动直接产出，5 轮热更新零新增即收敛），v0.1 存档 `classify/Tasks/tasks0807.json`（详见 `docs/data-description.md` §6.5）。

2026-08-17 起新增三块：**JD 侧 ΔG**（市场确证源：新信号入叠层 + 对叠层实体的 `src="jd"` 确证证据，权重 1.0/半衰期 365 天；基线提及跳过；不从 JD 发现新岗位。2026-08-28 起生产路径换 `graph/jd_delta_v2.py` 采样基面扫描+残差裁决，旧 `jd_delta.py` 抽样路径弃用保留）、**叠层生命周期**（`participation.py` 可见性门控：参与门槛 0.15，遗忘=跌破门槛休眠不删除；`promotion.py` 转正：强度 + JD 确证双门槛 → 先备份再写基准体系，ΔG 源标 graduated；2026-08-31 起转正收口后接 `job_categorize.py` 类别归纳旁路）。详见 `docs/loop-design.md` §3 与 `docs/algorithm-design-v2.md` §2.7。

| 文件 | 功能 |
|------|------|
| `sampler.py` | **通用分层抽样引擎**（源无关）：消费 `(stratum, text)` 条目，min_coverage/proportional/uniform 策略、按文本去重、`preview()` 无 LLM 预览；**断点支持**（已消费 md5 落盘/恢复，跨进程继续） |
| `data_source.py` | 数据源接口 + `JDDataSource`（JD CSV→**funtype→jobs_v2 匹配的 IT 过滤 + v2 一级类别 9 类分层**，norm_part 镜像 build_jobs 口径，2026-08-21 起；jobs_v2 加载失败快速报错）+ `register_data_source`/`make_data_source` 注册表工厂（预留 news/paper/resume，尚未实现） |
| `taxonomy_store.py` | 体系读写（通用容器，task/skill 双模式：task 扁平 `tasks`；skill 的 `detail` 字典含 definition/skill_type，兼容 Extractor `load_skills()`） |
| `cold_start.py` | 冷启动：LLM 归纳初始体系（`mode` 决定任务/技能提示词，skill 写 definition/skill_type） |
| `propose.py` | 提案 Agent：分析新数据 → add/merge/modify 提案（**粒度契约**：抽象类别、批内去重、单批新增上限；skill 模式剔除工具/框架/语言级 SkillPoint） |
| `apply.py` | 应用提案（增/并/改；skill 模式写入 definition/skill_type） |
| `hot_update.py` | 热更新引擎：每轮 `batch_size` 按 `chunk_size` 拆成子块分别交 LLM（控制上下文长度）；子块内 propose→supervise→apply 循环精化至覆盖（`max_recheck`，防丢批）；每消费一子块即落盘断点（`checkpoint_path`），中断续跑仅丢当前子块；**可复用于图谱更新** |
| `supervisor.py` | 监督 Agent：精简原则判断提案必要性（语义接近即拒绝新增；粒度契约拒绝岗位级/工具级过细提案）；index/布尔值类型容错；拒绝项附 `map_to` 合并建议 |
| `logger.py` | 运行跟踪日志（`RunLogger`）：冷启动/提案/监督/应用/重检全程落盘，人类可读 md + 结构化 jsonl，供专家检验与膨胀归因 |
| `builder.py` | 编排器（`Builder` 类，`mode=task|skill`，供 Agent 调度） |
| `run_builder.py` | CLI 入口（`--action cold|hot|full`；`--mode task|skill`；`--dry-run` 只预览抽样不调用 LLM；`--log` 指定日志前缀；`--no-resume` 禁断点恢复；`--chunk` 子块大小） |
| `delta_store.py` | **论文 ΔG 增量层**读写（迁自 paper_signal）：evidence 按 paper_id 幂等聚合 + noisy-OR 强度 + PJ-/PT-/PS-/PK- 编号生成 |
| `paper_delta.py` | **论文 ΔG 热更新**流水线编排（迁自 paper_signal）：解析←`codes/paper_signal/`，分类←`codes/extractor/`，聚合=本层 `delta_store`；2026-08-22 新增 **Stage C 基线提及并入**（`strengthen_paper_mentions`：`paper_mention` 分类式提及 skill/task 双模式直入 strengthenings，tier 权重、paper_id 幂等、证据封顶 5 句、confidence=medium 与新闻侧同口径；提及识别器经 config 换出习语构建；`--no-mention` 可跳过） |
| `paper_logger.py` | 论文 ΔG 运行跟踪日志（jsonl + md，迁自 paper_signal） |
| `run_paper_delta.py` | 论文 ΔG 热更新 CLI（迁自 run_paper_signal.py；`--tier`/`--limit`/`--dry-run`/`--no-resume`） |
| `paper_prompts.py` | 论文提示词拷贝（与 `codes/extractor/paper_prompts.py` 同源，保证跨模块导入一致性） |
| `news_delta.py` | 新闻 ΔG 热更新编排：parse → 相关性过滤 → 信号提取/提及 → mention-map → signal-map → `delta_store` |
| `run_news_delta.py` | 新闻 ΔG 热更新 CLI（`--source`/`--limit`/`--dry-run`/`--no-resume`/`--filter-tokens`） |
| `job_prompts.py` | 岗位热更新提示词（`PROMPT_JOB_ASSOC` 新岗位→任务/技能提取 + `PROMPT_JOB_MAP` 关联候选映射，唯一命名） |
| `job_hot_update.py` | **岗位热更新编排**（ΔG 后处理）：消费 `status="pending"` 新岗位 → LLM 任务/技能关联分析 → 回填 `related_tasks`/`related_skills`；复用 `map_signals`（传 `PROMPT_JOB_MAP`）+ `delta_store.apply`；关联产物按 `job_assoc:{job_id}` 幂等合并 |
| `run_job_hot_update.py` | 岗位热更新 CLI（`--source papers|news|all`/`--delta`/`--limit`/`--dry-run`；探索写 `*_explore.json`） |
| `job_categorize.py` | **转正后类别归纳（旁路，2026-08-31）**：转正落盘的新岗位 `category` 为空（`_write_jobs` 不判类别），收口后由 `run_promotion.py` 自动进入（或单独 `python job_categorize.py` 补跑）——LLM 归纳（9 类描述 + 同类现有岗位清单 → category/confidence/reason/runner_up）+ **人工确认**（回车接受/code 改判/s 跳过）→ 先备份 `classify/backup/categorize-{ts}/` 再写回 + bump version + 记 promotion_log；非 tty 自动降级 suggest-only；候选条目自带 category 时 `_write_jobs` 直写 |

**用法示例**：
```bash
python run_builder.py --dry-run              # 预览分层抽样方案（不调用 LLM，验证覆盖）
python run_builder.py --action full          # 冷启动 + 热更新
python run_paper_delta.py --tier S --limit 20 --dry-run   # 论文 ΔG：预览（不调 LLM）
python run_paper_delta.py --tier S                        # 论文 ΔG：S 档全量信号 → 增量层
python run_job_hot_update.py --dry-run                    # 岗位热更新：列出 pending 新岗位（不调 LLM）
python run_job_hot_update.py                              # 岗位热更新：新岗位 → 任务/技能关联分析回填
```
产出 `classify/Tasks/tasks.json`（Extractor 优先读取，回退 `tasks_seed.json`）+ `classify/DeltaG/papers_delta.json`（论文 ΔG，独立存在）。岗位热更新在 ΔG 生成后运行，回填 `new_jobs` 的 `related_tasks`/`related_skills`（关联到基层或叠层任务/技能）。详见 `builder/README.md`。

**实测记录：热更新防膨胀（194 → 35）**：

正式一轮实测发现热更新会**失控膨胀**：冷启动 28 个合理类别，3 轮热更新后涨到 194 个任务（含 48 个重复名，粒度崩成岗位名如「汽车维修服务」「服装设计」「商显设备安装调试」）。逐项根因与修复：

| 问题 | 根因 | 修复 |
|------|------|------|
| 过提案 | 提案 Agent 对 200 条 JD 提 180 条 add（约 1 条/JD）；从 JD 内容直接生成任务名，**不把 labels 当硬约束**，重复提已存在的任务 | 提案提示词加**任务粒度契约**（抽象类别，非岗位/工种/技术栈）+ **映射先行**（先映射到现有 code，只有无法映射的新大类才 add；add 前逐条自查是否与现有任务同名/近似/下位） |
| 过批准 | 监督 Agent 只查「语义是否覆盖」，无粒度层级意识，批准率 42~78% | 监督提示词加粒度契约 + 总规模提示，拒绝下位概念；实测批准率降至 0~25% |
| 重复新增 | `apply` 无同名去重；批内提案重复 | `apply` 同名防重 skip；热更新批内提案去重 |
| 重检空转 | recheck 对整批重新提案，反复新增 | 新增全被同名防重 skip（无实质变化）时直接收敛 |

修复后正式一轮结果：冷启动 28 + 热更新 7 = **35 个抽象任务类别**，round2-4 全部收敛、0 新增，监督 Agent 正确拒绝「下位概念」「名称重复」的提案。

**提示词设计原则**：数量指标（如「至多 N 条」）会被 LLM 当**配额填满**（实测给「至多 10」后每轮恰好输出 10 条、全部被监督拒绝），故提案/监督提示词**只给原则性描述**（「add 极其保守」「默认 covered=true」），不硬编码数量。

**重检重复提案的根因**：日志曾出现「刚应用 T-29:半导体工艺与制造，重检又 propose add 半导体工艺与制造」。排查确认**不是数据不可见**——重检提案时 labels 里就含 T-29（尝试2 监督能判「与现有T-29完全重复」即为证据）。真正根因是**注意力分布**：labels 在 prompt 顶部，200 条 JD 在底部紧贴生成点，模型生成时以 JD 内容为准，对远端 labels 的自查规则执行不稳定；且重检对整批重新推导、无「本批刚加了什么」的记忆。修复：`_process_batch` 在提案后**程序化过滤与现有体系同名的 add**（写回 `proposal["updates"]`，监督只看过滤后提案，不依赖 LLM 自觉）+ 提示词末尾（生成点前）加「输出前自查」提醒。

### 3.5 `paper_signal/` — 论文数据处理层（解析）

解析论文 TXT（头块 + 正文）→ **PaperRecord**（arXiv ID / 标题 / 发表日期 / 分档 / 命中维度 /
证据句 / 摘要 / 关键词 / 正文片段），供上层模块消费。本层**不调用 LLM、不产出 ΔG**——论文的
分类（新信号提取 / 提及识别）在 `codes/extractor/`，ΔG 增量层热更新在 `codes/builder/`。

| 文件 | 功能 |
|------|------|
| `paper_parser.py` | ★ 解析机制：TXT 头块 + Abstract/Keywords 双启发式提取 → `PaperRecord`；`scan_papers` 兼容两层布局（单专题档位直挂 / 全库六专题 `专题X/档位/` 嵌套，`_iter_tier_dirs` 自动识别），跨专题同文副本按 (文件名, md5) 去重（一篇论文命中多专题时文件逐字节相同，去重防提及/信号重复计权） |
| `paper_source.py` | 数据源胶水：`(stratum, text)` 条目 → 抽样/去重/断点，`next_batch` 返回 `PaperRecord` |
| `paper_sampler.py` | 通用分层抽样引擎（`StratifiedSampler`，自持拷贝，源无关） |
| `paper_config.py` | 论文路径 + 解析参数（**唯一命名**，避免跨模块 `import config` 冲突） |

**要点**：
- 解析 Abstract 双启发式：显式 `Abstract`/`Abstract—` 标记；无标记时先用论文自身 arXiv 行向后扫描，
  再用跳过标题/作者/机构的前向扫描兜底（实测 117 篇 S 档提取率 113/117，缺摘要的 4 篇是论文/长报告格式，
  其摘要内容仍落在 `body_excerpt`，信号提取不受影响）。
- 本层为**唯一命名**模块（`paper_config`/`paper_sampler`）：被 extractor / builder 跨模块 `sys.path` 导入时，
  与各模块自己的 `config`/`sampler`/`prompts` 不冲突。

**消费方**：`codes/extractor/`（分类：新信号提取 `signal_extractor.py`/`taxonomy_mapper.py`、提及识别
`paper_mention.py`）→ `codes/builder/`（热更新：`paper_delta.py` + `delta_store.py`，产出
`classify/DeltaG/papers_delta.json`）。

### 3.6 `news_signal/` — 新闻数据处理层（解析）

解析新闻 TXT（头块 + 正文）→ **NewsRecord**（doc_id / 标题 / 公众号 / 发布时间 / 正文），供上层模块消费。
本层**不调用 LLM、不产出 ΔG**——新闻的分类（过滤/信号提取/提及映射）在 `codes/extractor/`，
ΔG 增量层热更新在 `codes/builder/`。

| 文件 | 功能 |
|------|------|
| `news_parser.py` | ★ 解析机制：TXT 头块（标题/链接/发布时间/公众号/爬取时间）+ 正文 → `NewsRecord` |
| `news_source.py` | 数据源胶水：`(stratum, text)` 条目 → 抽样/去重/断点，`next_batch` 返回 `NewsRecord` |
| `news_sampler.py` | 通用分层抽样引擎（`StratifiedSampler`，自持拷贝，源无关） |
| `news_config.py` | 新闻路径 + 解析参数（**唯一命名**，避免跨模块 `import config` 冲突） |
| `convert_docx.py` | **docx 批次转换**（一次性，2026-08）：公众号历史文章 .docx（python-docx 生成，第 1 段标题/第 2 段挤行元数据）→ news_raw 约定 TXT（stdlib 解析 word/document.xml，无第三方依赖）；文件夹映射（雷峰网→雷锋网等）+ 重名后缀 + 统计 CSV + 未收录清单 |
| `import_zip.py` | **zip 全量批次入库**（一次性，2026-08-30 已执行）：全量爬取 zip（33 源 302,548 篇 txt）byte 保真拷贝并入 news_raw——同名跳过（幂等）、全文折叠 <200 字拒绝（与解析器同口径）、Windows 非法字符清洗 + 超长路径截断（尾拼 md5 保唯一）；文件夹映射 + 逐源统计 CSV + 拒绝清单 |

**要点**：
- 数据：`data/news/news_raw/{公众号}/*.txt`，33 源 282,945 篇（首批 html 爬取 + 2026-08-16 docx 批次 5,401 + 2026-08-30 zip 全量批次 277,129）；正文长（avg 6.7k 字符）。zip 批次为无分隔线头块变体格式，解析走回退路径。
- `doc_id` = 相对路径（公众号/文件名），作 ΔG 证据幂等键；`pub_date` 缺省回退爬取时间。
- 唯一命名模块（`news_config`/`news_sampler`），被 extractor / builder 跨模块导入不冲突。

**消费方**：`codes/extractor/`（分类：`news_filter` 相关性过滤 → `news_extractor` 信号提取/提及 →
`mention_mapper` 提及映射）→ `codes/builder/`（热更新：`news_delta.py` + `delta_store.py`，产出
`classify/DeltaG/news_delta.json`）。

---

### 3.7 `timeline/` — 时间线编排器（JD / 新闻 / 论文）

把三类数据按时间戳统一编排，供**图谱按时间顺序导入**（测试技能/岗位随时间演化）。
纯 stdlib、零 LLM 调用，产物在 `data/timeline/`（已 gitignore，可脚本重建）：

| 源 | 编排方式 | 时间戳 |
|----|----------|--------|
| JD | 按 `opentime` 月份重新分组 → `data/timeline/jd/{YYYY-MM}.csv`（统一 schema = 源 CSV 并集，行按 opentime 升序） | `opentime` 列（`YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM:SS`） |
| 新闻 | 文件→时间戳映射表 → `data/timeline/news/news_mapping.csv` | 头部 `发布时间` → 回退 `爬取时间`（复用 `news_parser`） |
| 论文 | 文件→时间戳映射表 → `data/timeline/papers/papers_mapping.csv` | 头部 `【发表日期】` → 回退 arXiv YYMM（复用 `paper_parser`） |

| 文件 | 功能 |
|------|------|
| `timeline_config.py` | 输入/输出路径 + 输出文件名 |
| `timeline_builder.py` | 核心：`build_jd_timeline` / `build_news_mapping` / `build_papers_mapping`（跨模块 sys.path 复用解析层） |
| `run_timeline.py` | CLI：`--jd/--news/--papers`、`--dry-run`、`--limit`（探索写 `_explore/` 不动正式产物）、`--out` |

**要点**：
- 时间戳**复用解析层**（`paper_parser`/`news_parser`），保证与下游 ΔG 处理使用同一日期。
- JD 源 CSV 有两种 schema（9 列 / 11 列），输出统一为并集（缺列填空）。
- 数据规模（JD/论文 2026-08-27、新闻 2026-08-30 重建实测）：JD 61 表 5,805,597 行 → 53 个月度文件（2021-06..2026-05；`job.csv` 主表
  按既有 `job_` 前缀过滤排除，103,937 行）；新闻映射 282,944 行（53 个公众号头值，282,159 带日期，2015-04..2026-08，
  zip 全量入库后重建）；论文 10,370 篇（六专题 S/A 8,097 + 2022 全库批次 2,273：S 248 / A 10,122，跨专题同文已去重，全部有日期）。
- 消费方式：JD 按 `jd/` 文件名顺序读月度文件；新闻/论文读映射表（已按 `pub_date` 升序，缺日期在末尾），
  依序加载 `source_file` 指向的原文件。

### 3.8 `graph/` — 图谱层：时间截面快照 + 基图边计算 + 图谱合成

把图谱按**时间截面**组织存储：每个时间窗口（月 `YYYY-MM` 或季度 `YYYY-Qn`）一个文件夹，内含 `base/`（基图）、`delta/`（叠层）与 `effective/`（合成 G_eff，独立层）三个子图；节点用体系 JSON，关系用边 JSON（每种连边一个文件）。三个动作：快照构建（体系节点 + 两源 ΔG 合并）→ 基图边计算（JD→四种边，简版 Graph Builder）→ 图谱合成（G_eff = G_base ⊕ ΔG，只写 effective/）。Loop 顺序设计见 `docs/loop-design.md`。

| 文件 | 功能 |
|------|------|
| `graph_config.py` | 路径常量（`GRAPH_ROOT=data/graph`、基图节点源、叠层源、输出文件名）+ 强度权重透传（复用 `builder/config.py`）+ `graph_base`/`synthesis` 两节参数薄读取（settings.yaml） |
| `snapshot_builder.py` | `parse_window`（月/季度→period）/ `merge_delta`（norm 合并 + 证据日期过滤 + 强度重算）/ `_build_job_links` / `build_snapshot`（`--force` 重建默认保留已非空基图边 `keep_base_edges`，`--reset-base-edges` 重置；`delta_files` 可覆盖两源路径） |
| `base_builder.py` | 简版 Graph Builder：JD 月度 CSV 分层抽样（funtype→岗位 code，" or " 拆分匹配，2026-05 实测覆盖 100%）→ extractor task/skill 双模式抽取 → 薪资加权（`log(1+salary/median)`，万/千/·N薪//年/元每天统一折月）文档级 presence 频次 → 四种边（J-T/J-S `W(J,X)/W(J)`、T-S `w1·共现+w2·显式(预留0)`、S-SP `W(S,SP)/W(S)`）；跨窗口 `freq=freq_new+α·freq_hist`（读上窗 freq.json）；产出 entity_freq.json（E_jd）/freq.json/skill_prof.json（技能熟练度要求分布，2026-08-21 起：可选 "prof" 评估器逐 JD 评估后窗口聚合，mock/旧接口缺省时不写）/build_info.json |
| `synthesis.py` | 图谱合成：`compute_gaps`（`gap(E)=max(0,strength_ΔG−E_jd)`，tasks/skills 分表）/ `synthesize_edges`（基图边 λ 修正 + job_links 新边 + 双端 gap 合成新 T-S 边（上限内按强度降序））/ `synthesize`（写 `effective/`，纯函数可重算）/ `validate_effective`（端点/total/origin 校验） |
| `graph_snapshot.py` | `GraphSnapshot`：`load`（容缺）/ `nodes(layer,kind)`（读取层归一化）/ `base_labels()`（对齐 `load_base_labels`）/ `node_index()` / `edges(kind,layer)` / `strengthenings()` / `job_links()` / `entity_freq()` / `effective_edges(kind)` / `summary()` / `validate()` / `list_slices()` |
| `run_snapshot.py` | CLI：`list` / `build --window --out --dry-run --force --papers-delta --news-delta --reset-base-edges` / `check` |
| `run_jd_extract.py` | **Stage B**：join Stage A 归类（严格门+it_scope）→ 跳 non_it/范围外/非采样键 → **两遍式** merged 句级抽取（2026-08-26：Pass1 扫描分句 → Pass2 全窗唯一句批并发分类，缓存 `cache_merged_v2`，批级容错；Pass3 逐 JD 组装全缓存命中；skillpoint 后置清洗）→ `data/timeline/jd_derived/{窗口}.jd_vectors.jsonl` 源文件 + meta；无技术信号降级 it_related=False |
| `skillpoint_norm.py` + `skillpoint_registry.json` | **技能点三层归一**（2026-08-27）：L1 字面折叠 → L2 人工审定注册表（canonical+aliases+category）→ L3 LLM 首见归一（批 50、merge 须逐字命中已有 canonical、缓存 `output/skillpoint_alias_cache.jsonl` 跨窗复用）；B 阶段 Pass 3.5 在线生效；硬口径只合并同技术命名变体 |
| `skillpoint_backfill.py` | 存量窗口技能点归一回填 CLI（`--windows 2022-05,2022-06`；L1/L2 免费，未知名走 L3；回填后重建该窗基图即可） |
| `jd_sample.py` | **Stage S 降采样**（2026-08-26，零 LLM）：读 A 归类 → 窗口 IT > cap 时分层封顶抽样（岗位层比例分配 + 稀疏岗 floor=30 保底 + 确定性哈希 md5(jd_key+salt) + 逆概率权重 w=N_j/k_j）→ `{窗口}.sample.json`；未触 cap 时 keys=null 只记分母。蒙特卡洛验证见 graph/README |
| `run_pipeline.py` | **逐窗时序编排**（A→S→B→C→D）：模拟数据到达顺序的固定流程（也是后续月度更新流程），各步幂等/断点，重跑只补未完成；--window/--prev-window/--cap/--force-b/--force-d |
| `jd_dedup.py` | **Stage D0 近重复（抄袭）过滤**（2026-08-28，零 LLM）：正文 3-gram simhash64 + 8×8 分块候选（鸽笼 ≤7 不漏）+ 海明 ≤6 + Jaccard ≥0.95 双确认 + 星型聚类保最早发布 → `{窗口}.dedup.json`；S/B/D/jd_delta_v2/jd_summary 五消费方在线过滤（产物缺失=无操作向后兼容）；赛题"抄袭"回应（v1 设计 §4.4.2 轻量实现），跨窗时序抄袭留二期 |
| `jd_delta_v2.py` | **JD ΔG v2**（2026-08-28，替代 builder 100 条/窗抽样；2026-08-29 起数据基面=Stage S 采样选择集）：采样基面确定性扫描（英文 token 词表差集 + 中文 n-gram Apriori 2-8 gram 时间差分；df 带宽/边缘函数字过滤/O(n) 子串归约/右续延检验/语境词修剪/修剪借尸还魂拦截）+ 残差 LLM 裁决（HotUpdater 引擎注入，批 50 减半重试，**只有 task/skill 经新颖性守门落叠层，skillpoint/alias 只入跨窗缓存排水** `output/jd_v2_adjudication.jsonl`）+ 确证预筛（参与实体 as-of 窗末基面内匹配→LLM 确认"要求掌握"，require 级为转正唯一口径） |
| `replay.py` | **组装参数重放**（2026-08-28）：改 A 组参数（strength/overlay/graph_base.alpha\|ts_w\|salary_weight/synthesis）后零 LLM 按时间序重建 D→快照→合成；α 链完整性校验 + 参数指纹核对；幂等已实证（逐字节一致仅时间戳差异） |
| `run_base_build.py` | CLI：`--window --jd-csv --sample --per-job --no-salary-weight --prev-window --dry-run --force` |
| `run_synthesis.py` | CLI：`build --window --dry-run` / `check --window` |
| `fixtures/` | （已迁移）全部自测集中至仓库根 `unit-tests/`（35 文件 175 用例，覆盖四包核心模块，覆盖率 61.5%，见 `unit-tests/README.md`） |
| `README.md` | 使用文档（目录结构/公式/合成规则/加载 API） |

**要点**：
- **全量快照自包含**：任意时间截面可直接加载，无需重放历史。
- **三层互不覆盖**：snapshot 只写 base 节点与 delta；base_builder 只写 base 边与附属产物；synthesis 只写 effective/（合成不修改 base/delta，测试以 md5 断言）。
- **叠层合并语义**：`new_*` 按 `norm(name_zh)` 合并、`strengthenings` 按 `(taxonomy,code)` 合并；证据 `date ≤ period_end` 保留（无日期/解析失败保守保留）；强度按证据逐条判定来源（`"tier" in ev`=论文 `TIER_WEIGHTS×半衰期730`，否则新闻 `0.4×半衰期180`），复用 `delta_store._recency_decay/_noisy_or/norm` + `config` 常量，`now=window_end`。
- **合成规则**（λ 参数 settings.yaml → synthesis，随 effective/meta.json 存档）：J-T/J-S `Δw=λ_j·gap(右端)`；T-S `Δw=λ_ts·gap(T)·gap(S)`；S-SP `Δw=λ_sp·gap(S)`（父技能 gap 降级实现）；`effective=base+delta`（§5 加法）。叠层衰减已在快照重算阶段完成，合成不二次衰减。
- **容缺**：叠层两源缺失/为空 → 空 ΔG（meta 记 `delta_missing`），合法输入；entity_freq 缺失 → E_jd 视为 0 并告警。
- **幂等**：已有窗口默认拒绝覆盖（`--force` 才重写）；基图边已非空默认拒绝覆盖（`--force`）；合成可重复重算。
- 基图节点 = 体系 JSON **原样拷贝**，归一化放读取层（`nodes()` → `[{id,name_zh,name_en,...}]`）。
- 消费方：可视化、人岗匹配（读 effective/ 或快照加载 API）。

**用法示例**：
```bash
cd codes/graph
python run_snapshot.py list                          # 列出已有时间截面
python run_snapshot.py build --window 2026-05        # 构建 2026-05 截面（骨架+叠层）
python run_base_build.py --window 2026-05 --dry-run  # 预览 JD 抽样分布
python run_base_build.py --window 2026-05            # 基图边计算（LLM，抽样 200 条）
python run_synthesis.py build --window 2026-05       # 图谱合成 → effective/
python run_snapshot.py check --window 2026-05        # 快照结构校验
python run_synthesis.py check --window 2026-05       # 合成层校验
```
详见 `graph/README.md`。

---

### 3.9 `jd_annotate/` — JD 标注与分类体系构建（技术栈/级别标注 + 岗位体系 v2 与归类引擎）

本模块承担三类职责：① 为每条 JD 附加筛选维度（赛题"按技术栈和级别切换视图"）：**techstack**（多标签 1-4 类）与 **level**（单标签，纯规则）；② 构建岗位分类体系 v2.0（`classify/Jobs/jobs_v2.json`）；③ **JD → 岗位归类引擎**（`classify_job.py`，词库+LLM+向量比对）。技术栈体系 v2.0（2026-08-20）为**人工确定的八类**（`temp/技术栈分类.docx`），LLM 不参与体系构建。归类逻辑 v2.1：**逐 JD 内容归类**（与 skill/task 分类同型）——**词库快路**（标题/正文命中体系 keywords 即划分，零 LLM）+ **LLM 兜底**（词库未命中的 JD 送 deepseek-v4-flash 多标签归类）；跨 JD 按文本指纹去重（同文只判一次，全量重复文本约 28.6%）。2026-08-22 起非 IT 域 JD 由 LLM 显式输出 `["非IT相关"]` 单标签（缓存 `non_it` 标记，与规则排除表 tier0 同口径；向量阶段据此跳过，`load_non_it_keys()` 供下游内容级过滤复用）。v2.0 之前的"funtype → 固定技术栈查表"方案退役。岗位体系 v2.0 同样**弃用 funtype 映射归类**（`classify_job.py` 已建，机制验证通过、全量运行待做），保留 funtypes 作溯源与抽样。

| 文件 | 说明 |
|------|------|
| `common.py` | 公共层：JD 文本指纹 `jd_text_key`（标题+正文归一化 md5，LLM 缓存与引擎查表的同口径主键）、词库快路 `rule_stacks`（标题/正文关键词命中即划分，生产端与引擎共用保证判定一致）、**必空排除表** `EXCLUDE_TITLE_WORDS` + `is_excluded_title`（词库未命中且标题属非软件域 → 直接空栈不送 LLM；只收几乎必空的物理制造/职能域，电子/半导体/电气等边界域留给 LLM）、体系加载、`StackMatchers` 关键词匹配器（中文子串 / ascii 字母数字边界，防 "java" 命中 "javascript"）、JD funtype 收集缓存 |
| `build_taxonomy.py` | 技术栈体系落盘（v2.0）：人工确定的八类底稿（docx「八类技术栈分类」为准：类别/说明/具体清单原样收录，keywords 另补类别说明范围内的中文同义扩展）→ `--finalize` 写 `classify/TechStacks/techstacks.json`。v1.0 的 `--induct` LLM 归纳流程已移除 |
| `build_jobs.py` | **岗位体系 v2.0 构建**：内置 255 个 v1 节点全量处置骨架（9 类别 + 131 岗位 + 46 剔除 + 9 纯类别退役，`--validate` 校验处置完备性与 funtype 挂载覆盖，`norm_part` 字符规范化匹配）；`--sample` 全量扫描 JD 数据集为每岗采 ≤4 条真实样例（funtype 挂载匹配 + `title_kw` 标题兜底独立执行）；`--define` 逐岗调 LLM（本地 `call_llm_raw` 返回原文，`classify_jobs.call_api` 固定提取数组不适用对象输出）阅读样例生成 definition/keywords/boundary/name_en（断点续跑 `output/jobs_def_cache.jsonl`，失败记录自动重跑）；`--finalize` 合并落盘 `classify/Jobs/jobs_v2.json`（含 from_v1 处置明细与 hits 行支撑）；`--doc` 由体系文件渲染可读介绍文档 `introduction/岗位分类体系介绍.md`（仅岗位与描述，体系变更后重跑即同步） |
| `classify_stacks.py` | **JD 内容 → 技术栈归类**（v2.1）：单遍扫描 → 按指纹去重 → 词库快路判定（`rule_stacks`，命中零成本）→ 必空排除表判空（标题属非软件域）→ 其余未命中 JD（标题 + 正文前 600 字）按 batch=20 送 LLM 多标签归类（1-4 类，progress 断点续跑、整批失败对半重试）→ `output/jd_stack_cache.jsonl`（**只存 LLM 判定**，词库命中由引擎在线重算，排除表判空无条目=引擎自然为空）。`--stats` 零 LLM 预估去重规模与命中率、`--dry-run` 预览、`--files/--limit/--max-batches` 控制范围 |
| `classify_job.py` | **JD → 岗位体系 v2 归类**（2026-08-21）：四路信号——①**岗位名层**（`name_zh` 出现于标题即归类，`build_name_matchers`，缓解跨岗共享关键词的首现排序抢占）；②**词库快路**（jobs_v2 keywords，复用 `rule_stacks`，cap 2）；③**LLM 兜底**（batch=20 多标签 1-2 岗，输入 = 标题全文 + 正文前 600 字，其余列含 funtype 刻意不喂以免带偏内容判定；prompt 含岗位关键词提示 + 非 IT 域必空规则；`jd_job_cache.jsonl` 指纹缓存断点续跑；排除表复用技术栈表但移出产品/项目管理岗——v2 已收录为实体）；④**向量比对观测信号**（不参与归类决策）：`--vectors` LLM 标注 JD 的 0/1 任务+技能多标签 → `jd_vec_cache.jsonl`（batch=10 独立断点）；`--vector-report` 加载基图岗位向量（`data/graph/{窗口}/base/job_task|job_skill.json` 边按 v1→v2 `source_codes` 聚合）计算余弦 top-k 相似岗位，并报告与归类结果的 top-1 一致率——约定用于后续与 LLM 判定结合（向量差距大 → 新岗位探索线索）及岗位任务/技能模式漂移观察。**严格门 `--strict`**（2026-08-25，基图管线默认，`settings.yaml → jd_gate.strict`）：仅①岗位名直收，②关键词命中（标题/正文）泛词误报多（'测试'→通信测试、'监控'→运维、'质检'→数据标注）一律送③LLM 按内容复核；配合 `graph/it_scope.json` 岗位范围（34 岗排除集：硬件/半导体全类、通信现场/设备类、数据标注，判据=49 技能体系对该岗充分适用）在基图管线侧过滤（不改动共享体系）。**批间并发**（2026-08-26，settings llm.concurrency，实测 12 批 4.7s）+ 整批失败对半重试（修复原异常分支死代码——错误记录原本不可达）；跑满后落 **窗口归类缓存** `{窗口}.jobcls.json`（`merged_classification` 规则层+LLM 合并的 PRE-scope 形态，新鲜度=csv mtime/size+jobs_v2 sha256+strict；S/B 阶段免重复扫描大 CSV，it_scope 过滤仍在线应用） |
| `annotate_jd.py` | 行级标注引擎（标注时零 LLM）：技术栈三层解析（①标题词库 ②正文词库（前 4000 字）③LLM 缓存按指纹查表，cap 4）+ 级别规则（work_year 列 > 正文年限正则[阿拉伯+中文数字，取最高要求] > 标题级别词；L0 实习/应届~L4 专家五档；经理/主管属管理序列不参与）；三种运行模式——`--files/--limit/--out-dir` 测试（写副本不碰源文件）、`--report` 只统计、`--in-place` 全量原地加列（temp→jobid 多重集校验→原子替换，断点续跑，summary.json meta 记录） |

**产物链**：`techstacks.json`（八类 v2.0，入库）→ `jd_stack_cache.jsonl`（词库未命中 JD 的 LLM 判定，指纹索引）→ JD CSV 末尾追加 `techstack, level, level_source` 三列 → `timeline_builder.JD_COLUMNS` 已扩展 3 列（未标注源自然填空，向后兼容）。

**状态（2026-08-20，v2.1 机制验证通过）**：`job_2026_1_1.csv`（618 行）端到端验证——分层覆盖：标题词库 4.5% + 正文词库 15.2% + LLM 缓存 80.3%，未识别 0%；LLM 判定 306 条去重 JD（有栈 102 / 空 204，空栈主体为被宽口径 IT 过滤收入 dataset 的非软件类 JD：飞行器设计/工艺/技工等，判空正确）；级别引擎未改动。两文件 2 万行抽样：词库命中率 47.5%（unique 口径）。**全量 LLM 归类 + `--in-place` 加列未运行**（成本见 classify_stacks `--stats` 输出），随后重建 timeline 传播新列。

---

## 4. 项目间数据流

```
51job dd_funtype(1584节点)
    │  job_classify_51job/classify_jobs.py（LLM IT判定）
    ▼
IT 岗位判定(268→255节点) ── annotate_classification ──► docs/job_classification.json
    │                                                        + classify/Jobs/jobs0806.json
    ▼
jd_fetch/gather_funtypes ─► 全量 funtype(1824)
    │  merge_classify_funtypes（规则+LLM 判定 IT）
    ▼
funtype_it_map.json(316 IT) ──► fetch_jd.py ──► data/jd_dataset/*.csv（50.3万条 IT JD）
    │
    ▼
extractor（技能/任务抽取）──► {技能/任务: 频次}  → 图谱构建/人岗匹配
    ▲
builder（构建/更新任务体系）──► classify/Tasks/tasks.json → extractor 任务抽取

──── 论文 ΔG 流水线（分层：处理→分类→热更新）────
data/papers（六专题 S/A 档 TXT）
    ▼
paper_signal（解析 → PaperRecord）            # 处理层
    ├──► extractor/paper_mention（提及识别）──► classify/DeltaG/papers_mentions.json
    │                                            # 分类层：对既有体系的提及
    │
    └──► extractor/signal_extractor + taxonomy_mapper（新信号分类）
                │                                # 分类层：新信号提取 + 体系映射
                ▼
         builder/paper_delta + delta_store（ΔG 热更新）──► classify/DeltaG/papers_delta.json
                                                     # 热更新层：独立增量层（岗位信号 pending）

──── 新闻 ΔG 流水线（对称：处理→分类→热更新）────
data/news/news_raw/{公众号}（TXT）
    ▼
news_signal（解析 → NewsRecord）              # 处理层
    ├──► extractor/news_filter（相关性过滤：LLM 判别 title+导语，无关键词门槛）
    │       ▼  仅相关新闻
    │   extractor/news_extractor（新信号 + 提及，带定义/证据）
    │       ├──► mention_mapper（提及→既有体系 code）──► strengthenings
    │       └──► taxonomy_mapper（新信号体系映射）
    ▼
builder/news_delta + delta_store（source_kind=news，权重 0.4）──► classify/DeltaG/news_delta.json
```

---

## 4.x 配置体系（2026-08-16 起）

| 要调什么 | 去哪里改 |
|------|------|
| LLM（URL/模型/超时/重试/token 上限/禁用推理） | `codes/settings.yaml` → `llm` |
| ΔG 强度（档位/置信权重、半衰期、MIN_STRENGTH） | `codes/settings.yaml` → `strength` |
| 论文链路（提取批大小、摘要/正文截断、扫描行数） | `codes/settings.yaml` → `papers` |
| 新闻链路（过滤导语窗口 800、提取截断 3000） | `codes/settings.yaml` → `news` |
| JD 抽取（批大小、子句长度、计数语义） | `codes/settings.yaml` → `jd_extract` |
| Builder 冷启动/热更新（采样量、轮数、体系条数上下限） | `codes/settings.yaml` → `builder` |
| 任务/技能/岗位**基准体系文件**（标签源） | `classify/taxonomy_base.json`（单一开关；环境变量 `TAXONOMY_BASE_*` 临时覆盖） |
| 模块私有产物路径（ΔG 文件、日志、断点） | 各模块 `config.py`（builder/extractor） |
| 数据格式绑定（头字段名、分隔线、HEADER_MARKER） | `news_signal/news_config.py`、`paper_signal/paper_config.py` |
| DeepSeek API key | `codes/api-key.txt`（gitignored） |
| jd_fetch 数据库凭证 | `codes/jd_fetch/config*.yaml`（含密码，仓库转公开前须处理） |

读取机制：各模块 config 保留原变量名作**薄读取层**，按**文件路径**读 `settings.yaml`（不做 `import config`），
天然免疫跨模块 `sys.modules["config"]` 缓存顺序问题——原"新闻参数须模块内自洽"的限制已解除。
`settings.yaml` 缺失/损坏时各变量回退内置同值默认，系统仍可运行。

## 5. 更新日志

| 日期 | 内容 |
|------|------|
| 2026-08-07 | 初版：梳理 `codes/` 三个子项目（job_classify_51job / jd_fetch / extractor）及顶层文件，绘制数据流 |
| 2026-08-07 | Extractor 增加技能点抽取：`skillpoint_counts` + `skill_skillpoint_map`，技能点仅限工具/框架/语言实体 |
| 2026-08-07 | `config.yaml` 移入 `jd_fetch/`（唯一使用者），更新 `jd_fetch/config.py` 路径 |
| 2026-08-07 | 新增 `builder/`：任务体系构建/更新（冷启动 + 热更新，提案/监督/应用三 Agent）；Extractor 任务体系优先读 `tasks.json` |
| 2026-08-07 | Builder 分层抽样完成：`sampler.py` 按岗位大类分层（冷启动 min_coverage 全覆盖 / 热更新 proportional）、按文本去重（50.3万→34.2万）、修 `_targets` 超量 bug、CLI 新增 `--dry-run` 预览 |
| 2026-08-07 | Builder 健壮性修复：`supervisor.py` index 字符串/布尔类型容错（防误拒提案）；`hot_update.py` 同批循环精化至覆盖（`MAX_RECHECK`，防重检未覆盖内容丢失） |
| 2026-08-07 | Builder 数据源解耦：`sampler.py` 重构为通用分层抽样引擎（源无关）；JD 专属逻辑移入 `data_source.py` 的 `JDDataSource`；新增 `register_data_source` 注册表，预留 news/paper/resume（含论文/新闻目录配置） |
| 2026-08-07 | Builder LLM 小数据实测修复：`run_builder.py` sys 导入；`cold_start.py` LLM 返回类型容错；`llm.py` 推理截断升级重试（默认禁用推理防 reasoning 烧光 max_tokens） |
| 2026-08-07 | Builder 新增运行跟踪日志 `logger.py`：冷启动/热更新提案/监督/应用/重检全程落盘（`classify/Tasks/builder_log.{md,jsonl}`，`--log` 可改），监督拒绝项附 `map_to` 合并建议；日志不入库 |
| 2026-08-07 | Builder 断点继续：`sampler.py` 支持已消费 md5 落盘/恢复；热更新每批消费即写 `{taxonomy}_checkpoint.json`；`--action hot` 自动恢复断点续跑，`--action full` 冷启动清断点；CLI 新增 `--no-resume` |
| 2026-08-07 | Builder 防膨胀加固（正式一轮实测任务数失控至 194 后修复）：提案/监督提示词加**任务粒度契约**（抽象类别、非岗位/工种/技术栈、批内去重、单批新增上限 10）；`apply` 同名防重；热更新批内提案去重 + 全重复即收敛 |
| 2026-08-07 | Builder 提案提示词优化：删除数量硬编码（「至多 N 条」会被 LLM 当配额填满，实测每轮恰好 N 条），改为**原则性描述** + **映射先行**（先映射到现有 code 再决定是否新增）+ 反例 few-shot；正式一轮产物 `tasks.json`（35 个抽象任务类别）入库 |
| 2026-08-07 | Builder 重检重复提案修复：确认根因是**注意力分布**（labels 在 prompt 顶部、长上下文下 LLM 不遵守同名自查，非数据不可见）；`_process_batch` **程序化过滤与现有体系同名的 add** 并写回 `proposal["updates"]`，监督只看过滤后提案；提示词末尾加输出前自查提醒 |
| 2026-08-07 | Builder 热更新**子块化**：每轮 `batch_size`（200）按 `chunk_size`（50，CLI `--chunk`）拆成子块分别交 LLM，单次提案上下文从约 24 万字符降至约 6 万，缓解注意力问题；断点按子块落盘，中断仅丢当前子块；`_process_batch` 更名 `_process_chunk` |
| 2026-08-07 | Builder 热更新**收敛验证**：基于 35 任务体系 + 断点（1040 已消费）续跑 5 轮（20 子块×50 条=1000 条新 JD），**全部子块 covered=true、零新增**，任务数稳定 35，断点推进至 2040。35 个抽象任务类别对 IT JD 样本覆盖充分、体系收敛 |
| 2026-08-07 | Builder 支持**技能体系（skill 模式）**：CLI `--mode skill`；新增技能专用提示词（冷启动/提案/监督，剔除工具/框架/语言级 SkillPoint）；`TaxonomyStore` 双模式（skill 写 `detail` 字典含 definition/skill_type，兼容 Extractor）；输出 `classify/Skills/skills_builder.json`（与文献版 `skills0805.json` 区分，可对比或复制为热更新起点） |
| 2026-08-09 | 新增 `paper_signal/`：论文驱动 ΔG 增量层（解析→信号提取→体系映射→增量聚合），产出 `classify/DeltaG/papers_delta.json`（独立存在不并入基础体系，岗位信号 pending）；解析 Abstract 双启发式 + 映射程序化预过滤 + noisy-OR 强度；断点续跑只消费新增论文 |
| 2026-08-09 | **代码分层重构** + **论文提及识别**：① `paper_signal/` 收缩为**论文数据处理层**（`paper_parser`/`paper_source`/`paper_sampler`/`paper_config` 唯一命名，避免跨模块 `import config` 冲突）；② 论文**分类**迁入 `extractor/`（`signal_extractor`/`taxonomy_mapper` 新信号分类 + 新增 `paper_mention` 提及识别，`--mode skill|task|job`，复用分句/缓存/LLM 设施，产出 `classify/DeltaG/papers_mentions.json`）；③ 论文 ΔG **热更新**迁入 `builder/`（`delta_store`/`paper_delta`/`paper_logger`/`run_paper_delta`，跨模块 sys.path 导入解析层与分类层）；④ Extractor 技能体系切至 `skills_builder.json`（59 项，与 Builder/paper_signal 对齐），新增 `JOB_TAXONOMY` |
| 2026-08-10 | 新增**行业新闻 ΔG 模块**（对称论文流水线）：① 处理层 `codes/news_signal/`（`news_parser` 头块解析 → NewsRecord，`news_source`/`news_sampler` 数据源胶水）；② 分类层 `extractor/` 新增 `news_filter`（标题 IT 关键词预筛免费 + LLM 标题过滤廉价，仅显式信号进全文）、`news_extractor`（新信号 name+定义+证据 + 提及）、`mention_mapper`（提及→体系 code）、`news_prompts`；③ 热更新层 `builder/` 新增 `news_delta.py`/`run_news_delta.py`，`delta_store` 泛化为源无关（source_kind=news 权重 0.4、半衰期 180 天），产出 `classify/DeltaG/news_delta.json`；`.gitignore` 增 `*_explore.json` |
| 2026-08-10 | 新增**时间线编排器** `codes/timeline/`：JD 按 `opentime` 月份重排 → `data/timeline/jd/{YYYY-MM}.csv`（统一 schema、行按时间升序）；新闻/论文生成文件→时间戳映射表 → `data/timeline/{news,papers}/*_mapping.csv`（复用解析层日期逻辑，与下游 ΔG 同一时间戳）；纯 stdlib 零 LLM，`--dry-run` 预览规模、`--limit` 探索写 `_explore/` 不动正式产物 |
| 2026-08-10 | 新增**岗位热更新模块** `codes/builder/`（ΔG 后处理）：消费 `{papers,news}_delta.json` 中 `status="pending"` 的新岗位 → LLM 分析提及文本提取任务/技能 → 复用 `map_signals`（新增可选 `prompt_template` 参数，传 `PROMPT_JOB_MAP`）+ `delta_store.apply` 映射/新建 → 回填 `related_tasks`/`related_skills`；关联产物按 `job_assoc:{job_id}` 幂等合并；防御：映射目标排除新岗位（防任务候选并入岗位）、同名候选丢弃、`assoc_from` 标记防低强度剪枝致链接悬空；`delta_store` 增 `update_job_links`；新岗位仍 pending（不写 jobs0806.json） |
| 2026-08-10 | **统一多源命名标准**：论文/新闻/岗位关联三处提取提示词统一 `name_zh` 简洁规则（任务/技能 4-12 字上限 14，岗位 4-10 字）；PROMPT_MAP/PROMPT_JOB_MAP 归一化明确"简洁规范名"；Builder 冷启动提示词补命名规则；三处校验器统一 `MAX_NAME_CHARS=20` |
| 2026-08-10 | **命名不合格降级保留**：超长名由 `fit_name` 在连接词边界截断、保留末尾核心概念并继续进入映射层归一化（**不丢信号**），仅 < 2 字退化名丢弃；策略写入 `docs/algorithm-design-v2.md` §2.5 |
| 2026-08-12 | 新增**图谱时间截面快照存储机制** `codes/graph/`：每个时间窗口（月/季度）一个文件夹，内含 `base/`（基图）+ `delta/`（叠层）双子图；节点用体系 JSON，关系用边 JSON（每种连边一个文件）；叠层由 papers+news 合并（norm 合并 + 证据日期过滤 + 强度重算，`now=window_end`）；基图边文件先建空 schema（留待图谱构建任务）；`GraphSnapshot` 加载 API + `run_snapshot.py` CLI（list/build/check）；fixtures 自测（合并/过滤/强度手算/幂等/校验） |
| 2026-08-15 | **新闻 Stage 0 过滤改方案 B**：移除标题关键词硬门槛，全量进 LLM 相关性过滤；导语窗口 200→800 字；关键词扫描降级为统计观察（`keyword_hit`，不作丢弃依据）。**实证依据**：401 篇中 177 篇（44%）被门槛静默丢弃，用生产提示词复测其中 40 篇（23%）实为相关（≈全量 10% 信号损失，含 pytorch blog/huggingface/阿里技术优质内容）；且新信号发现天然在词表之外，门槛与 ΔG 使命相悖。改动面：`news_filter.py`（核心）、`news_delta.py`（日志语义）、`run_news_delta.py`（dry-run 输出） |
| 2026-08-15 | **JD-Origin 早期数据导入与 IT 过滤导出**（`jd_fetch/` 三个新脚本 + 两处改造）：① `import_origin.py` 把 `data/JD-Origin/`（19G .zst 全库 dump + 28 个单表 dump）流式导入本地 MySQL `51job` 库——awk 状态机只保留 job 表族（company/crawl_info 丢弃）、断点续跑、逐表 COUNT==AUTO_INCREMENT-1 校验（74 表 4861 万行全一致）；② `rebuild_it_map.py` 本地重建 funtype IT 映射（原 `funtype_it_map.json` 在原机器未同步）：黑名单（08-06 修正 6 项负证据）+ CSV 种子（334）+ 体系名/映射记录种子 + `rule_merge`（复用）+ LLM 兜底 47 项 + 负证据对齐（`质检员/测试员(QC)` 对齐原行为改判非 IT），211/1161 IT；③ `verify_origin_export.py` 验证报告（抽样 20/20 一致、IT 占比 11.1%、跨快照重复率 62.8%、新旧 jobid 重叠 2.9%）；改造 `config.py`（`load_config(path)`）、`fetch_jd.py`（`--config/--map/--out-dir`，summary 合并写入 + `origin_extension`）；新增 `config_origin.yaml`。数据集 62 CSV / 590.9 万条（详见 `docs/data-description.md` §2 更新日志） |
| 2026-08-16 | `news_signal/` 新增 `convert_docx.py`（docx 批次转换，一次性）：`news 2023到2026.zip`（5 来源 5427 篇 .docx）→ news_raw TXT（stdlib 解析 word/document.xml；文件夹映射 雷峰网→雷锋网/美团技术团队→美团/华为云开发者社区→华为/数字生命卡兹克同名/机器之心新增）；成功 5401 篇、未收录 26 篇（正文<200 字，`data/news/docx_convert_rejects.txt`）；统计 `sources_summary_2023_2026_batch.csv`；news_raw 现 29 源 5803 篇 |
| 2026-08-17 | **参数中心改用 YAML**：`codes/settings.json` → `codes/settings.yaml`（PyYAML `safe_load`，与 jd_fetch 同款；原 `_说明` 键改为真正的 `#` 注释，逐参数行内注释）。六个薄读取层（builder/extractor 两份 config、paper/news_signal 两份解析配置、news_filter/news_extractor）同步切换，异常兜底增加 `yaml.YAMLError`；验证：迁移前后全量参数逐值一致、回退兜底/修改传播/图模块自测/两套管线 dry-run 全部通过。注意：管线模块自此依赖 PyYAML（原纯 stdlib；jd_fetch 先例在） |
| 2026-08-17 | **移除新闻标题关键词词表**：方案 B 后词表仅存统计观察角色（`keyword_hit`），因其自身有 ASCII 子串误配（"RAG"∈"storage"）、中文中心等瑕疵且 08-15 迁移对比已完成，整个机制移除——`news_filter.py` 词表与命中统计、`news_delta.py` 两处日志、`run_news_delta.py` dry-run 打印、`settings.yaml` news 节 `title_keywords`（54 词）；`filter_relevant` stats 收敛为 {scanned, llm_relevant} |
| 2026-08-16 | **配置集中化**：新增 `codes/settings.json` 全局参数中心（llm/强度权重/论文/新闻/JD 抽取/Builder 六节；含 54 词统计词表与过滤/提取窗口），builder 与 extractor 两份 config、paper/news_signal 两份解析配置、news_filter/news_extractor 模块内参数统一改为薄读取层（按路径读取，免疫 import 顺序；缺失回退内置默认）；builder config 清理无消费者陈旧路径（`NEWS_DIR`/`PAPER_DIR`/`FULLTEXT_DIR`/`TASK_SEED`）。验证：改造前后全量参数快照逐值一致、图模块自测全绿、论文/新闻管线 dry-run 正常、修改传播与回退兜底实测通过。配置体系总览见 §4.x |
| 2026-08-16 | **技能体系基准切换 + 参数化 + 映射判据校准**：① 标准改为文献版 `skills0805.json`（49 项）——论文试跑发现映射基准为 Builder 版时"大语言模型幻觉防控"与文献版 `T-AI-13 AI幻觉校验与质量控制` 语义等价却被判新技能（结构性盲区）；Builder 写入路径拆分 `SKILL_BUILDER_OUTPUT`（防冷启动覆盖标准文档）；② 新增 `classify/taxonomy_base.json` 单一开关（tasks/skills/jobs 标签源；builder/extractor 两份 config 同源读取，图模块与自测跟随；环境变量 `TAXONOMY_BASE_{TASKS\|SKILLS\|JOBS}` 可临时覆盖）；③ 映射判据校准（`paper_prompts.PROMPT_MAP`，builder 拷贝同步）：逐标签扫描 + 近义措辞（防控/缓解↔校验/质控）与领域前缀变体视为已覆盖 + keep 须点名最接近条目；`taxonomy_mapper` 岗位标签（255 行）仅在批内有 new_job 候选时注入（消注意力稀释）；④ 图模块同步：fixture `S-01→T-AI-01`、基图文件跟随开关、2026-05 快照重建；⑤ 复跑验证（S/A/B×10 篇）：63 候选 → 13 map_to（T-AI-13 汇聚 5 篇证据 strength 0.6669 手算吻合）+ 2 拒绝 + 48 新条目（含首个新岗位 PJ-001） |
| 2026-08-17 | **基图边计算 + 图谱合成 + 图谱 Loop 设计**（`codes/graph/` 三新增一改造，roadmap 双 P0 完成）：① `base_builder.py`+`run_base_build.py` 简版 Graph Builder——JD 月度 CSV 分层抽样（funtype 按 " or " 拆分→jobs0806 detail 映射，2026-05 实测覆盖率 100%）→ extractor task/skill 双模式抽取 → 薪资加权（`log(1+月薪中值/median)`，万/千/·N薪//年/元每天统一折月）文档级 presence 频次 → J-T/J-S `W(J,X)/W(J)`、T-S `w1·共现`（显式项预留 0）、S-SP `W(S,SP)/W(S)`；跨窗口 `freq=freq_new+α·freq_hist`（α=0.85 读上窗 freq.json 链式衰减）；附带 entity_freq.json（E_jd）/freq.json/build_info.json；② `synthesis.py`+`run_synthesis.py` 图谱合成 G_eff=G_base⊕ΔG——`gap(E)=max(0,strength_ΔG−E_jd)`（tasks/skills 分表）、基图边 λ 修正（J-T/J-S `λ_j·gap(右端)`、T-S `λ_ts·gap(T)·gap(S)`、S-SP `λ_sp·gap(S)` 父技能降级）、job_links→PJ- 新边、双端 gap 合成新 T-S 边（上限 100 按强度降序）、`effective=base+delta` 加法；**独立写 `effective/` 层，绝不触碰 base/ 与 delta/**（md5 断言验证）；③ `snapshot_builder` 增 `keep_base_edges`（`--force` 重建保留已非空基图边/技能点，`--reset-base-edges` 重置）+ CLI `--papers-delta/--news-delta` 源路径覆盖；`GraphSnapshot` 增 `entity_freq()`/`effective_edges()`；④ settings.yaml 新增 `graph_base`/`synthesis` 两节（graph_config 薄读取）；⑤ 新增 `docs/loop-design.md`：单窗口 8 步 Loop 固化（timeline→[JD 分类 ‖ 论文/新闻 ΔG]→岗位热更新→快照→基图边→合成→校验）、**分类与热更新可并行原理**（JD 分类用固定基准、演化只进叠层，两路互不干扰）、岗位热更新卡位（晚于 ΔG 早于快照）、体系切换=新纪元不回填；⑥ fixtures 三测全绿：test_base_builder（薪资/边手算/α 衰减，mock 零 LLM）、test_synthesis（gap/Δw 手算/新边上限/空叠层/md5 独立性/幂等）、test_snapshot 增 keep_base_edges 用例 |
| 2026-08-17 | **JD 侧叠层热更新 + 叠层生命周期（可见性/遗忘/确证/转正）**：① 第三信号源 `jd_delta.json`——`builder/jd_delta.py`+`run_jd_delta.py`（timeline 月度 CSV 按 funtype 分层抽样 → `extractor/jd_extractor.py`+`jd_prompts.py` 信号提取（overlay 清单注入提示词做确证目标）→ mention/candidate 双路映射）：新信号入叠层（权重 1.0、半衰期 365 天、`ev.src="jd"` 显式判源标记）、对参与可见叠层实体的提及=确证证据（`delta_store.confirm_named` 按名称落本源文件，跨源聚合靠快照 norm 合并）、基线提及跳过（避免与 E_jd 重复计权）、全新 new_job 丢弃（岗位沿用 51job 分类）；② `builder/participation.py` 可见性门控——三源 merge 视图 strength≥0.15 才参与下一次更新（papers/news/jd 管线 delta_items 跨源注入 + JD 提示词清单 + 提及扩展标签；遗忘=跌破门槛休眠不删除，MIN_STRENGTH 剪枝仍仅限单篇噪声）；③ `builder/promotion.py`+`run_promotion.py` 转正——强度（任务/技能 0.25、岗位 0.30）+ JD 确证文档数（2/3）双门槛 → 先备份 `classify/backup/promotion-{ts}/` 再写基准体系（任务 T-续号 / 技能 T-DG·F-DG 前瞻组 / 岗位 GJ-xxx，版本/日期提升、funtypes=[名称]），ΔG 源标 graduated + promotion_log 可追溯，二次运行收敛；④ 快照三源化（graph）——`merge_delta(papers,news,jd,window_end)`、`_contrib` 三分支判源（src=jd→1.0/365，tier→论文，兜底新闻，旧数据兼容）、graduated 条目移出视图（stats n_graduated_skipped）、条目附 participates 标记、CLI `--jd-delta`；⑤ `run_job_hot_update` 三源化（infer_source_kind 增 jd、--source jd、权重三分支）；⑥ settings.yaml 新增 jd/overlay 两节 + strength 增 jd 权重（builder/graph config 薄读取）；⑦ fixtures 新增 test_jd_source（jd 权重手算/confirm_named 幂等/三分支判源/三源 merge/participation 门控含休眠唤醒）与 test_promotion（门槛拒绝/T-36·T-DG-01·GJ-001 写入/graduated 标记/备份/收敛），既有三测随 merge_delta 签名同步，五测全绿；⑧ 文档：loop-design v2.0（⑥b JD ΔG 并行步骤、⑨ 转正收口、生命周期状态图）、algorithm-design-v2 §2.5 JD 确证通道 + §2.7 叠层生命周期、README/roadmap/builder README 同步 |
| 2026-08-18 | **JD 确证锚定规范名修复 + loop-design v2.1**：① `jd_delta.py` 确证路径改用参与实体的规范名 `it["name_zh"]`（原为 LLM 提取的提及名）——LLM 兜底映射的提及名可能是近似变体，锚定规范名才能保证与跨源同名条目在快照 norm 合并、`jd_docs` 被正确统计（原缺陷后果：转正判断保守漏判）；提及原文仍保留在证据句、ref_id 溯源不变；② fixtures `test_jd_source` 新增 test_confirm_anchor（正例：锚定规范名 → 跨源合并+jd_docs 统计；反例对照：变体名合并失败），六测全绿；③ `docs/loop-design.md` 升级 v2.1：新增 §2.2「跨源同信号：如何识别、如何计权（并行为何安全）」——双层识别（写入层 LLM 尽力合并 + 快照层 norm 权威合并）、可信度与顺序无关的四不变量（doc_id 幂等/证据自身判源/noisy-or 对并集/id 取舍固定）、并行代价与语义近似名边界；§3 生命周期改为「状态图 + 要素表（含对应需求原文）」；新增目录、全文可读性打磨；旧 §2.2/2.3 顺延为 §2.3/2.4（builder README 引用同步） |
| 2026-08-18 | **新增 `jd_annotate/`：JD 双维度标注（技术栈 + 级别，§3.9）**：赛题"按技术栈和级别切换视图"的维度支撑。① `build_taxonomy.py`——LLM 500 样本归纳 + 人工审定 25 栈体系 `classify/TechStacks/techstacks.json`（采纳新栈"工艺/制程"，拒绝产品经理/机械结构等职能类提案，keywords 即规则词表）；② `classify_stacks.py`——funtype part→栈映射（规则种子 156 + LLM 兜底 193，349 part 中 282 有栈，管理/文职类正确无栈），progress 断点续跑；③ `annotate_jd.py`——行级引擎零 LLM：技术栈三层解析（funtype 查表/标题关键词/正文关键词兜底）+ 级别五档规则（work_year 列>正文年限>标题词，取最高年限要求，经理/主管不计级别），三模式（测试写副本/--report/--in-place 原地加列带 jobid 校验）；④ `timeline_builder.JD_COLUMNS` 扩展 3 列（techstack/level/level_source，未标注自然填空）。1 万行抽样验证：栈覆盖 96.1%、定级率 79.5%、正文 vs 结构化列判级一致率 83.2%。**全量 --in-place 运行留待后续**（本轮按用户要求只测不改数据文件） |
| 2026-08-20 | **技术栈 v2.0 体系重构 + v2.1 逐 JD 归类（§3.9 / `docs/data-description.md` §6.4）**：① 体系换为**人工确定的八类技术栈分类**（`temp/技术栈分类.docx`：前端与用户体验/后端开发与业务逻辑/数据存储与管理/中间件与消息通信/基础设施与云原生/安全与合规/DevOps 与自动化/AI·ML 与数据智能；DevOps 周期表/SFIA/ThoughtWorks 雷达/三层架构/UbiStack 融合提炼）——`build_taxonomy.py` 移除 `--induct` LLM 归纳、只做人工底稿落盘，keywords = docx 具体清单（权威）+ 类别说明范围内中文同义扩展，跨类条目按 docx 原文多标签（Docker/K8s 同属 TS-05/07 等）；② **归类逻辑弃用 funtype→固定栈查表**（`funtype_stack_map.json` 退役删除），改为与 skill/task 分类同型的**逐 JD 内容归类**：词库快路（标题/正文关键词命中即划分，`common.rule_stacks` 生产端与引擎共用）+ LLM 兜底（未命中 JD 送 deepseek-v4-flash 多标签归类 1-4 类，`common.jd_text_key` 文本指纹跨 JD 去重同文只判一次、progress 断点续跑、整批失败对半重试）→ `output/jd_stack_cache.jsonl` 只存 LLM 判定；③ `annotate_jd.py` 三层改为 标题词库→正文词库→LLM 缓存指纹查表（cap 4），级别引擎不动；④ 端到端验证 `job_2026_1_1.csv`（618 行）：标题 4.5% + 正文 15.2% + LLM 80.3%、未识别 0%，LLM 306 条（空 204，主体为 dataset 内非软件类 JD，判空正确）；两文件 2 万行抽样词库命中率 47.5%（unique）。**全量 LLM 归类与 --in-place 加列未运行** |
| 2026-08-20 | **归类成本压缩：LLM batch 10→20 + 必空排除表**：① `classify_stacks.py` BATCH 提至 20（调用量减半）；② `common.py` 新增 `EXCLUDE_TITLE_WORDS`（工艺/制程/机械/材料/化工/航空/技工/维修/电力/仪器仪表等物理制造域 + 产品经理/项目经理/文员/客服等职能域）+ `is_excluded_title`——词库未命中且标题命中排除表 → 直接空栈不送 LLM；收录原则为"即使全文无软件关键词也几乎必空"，电子/半导体/电气等边界域（涉及嵌入式 C/PLC）不收留给 LLM；排除只作用于生产端，引擎端无缓存条目自然为空、两端一致；③ 小样本验证（job_2026_1_1.csv 618 行）：排除表过滤 72/306 未命中、LLM 调用 31→12 次；全量测算基线（batch=10 无排除表）：去重 215.3 万、词库命中 37.4%、待 LLM 134.7 万条 ≈ 13.5 万次调用 |
| 2026-08-20 | **论文数据恢复全库六专题 + 解析层适配**：① `文献图书馆_XH-202621.zip`（4GB）选择性解压至 `data/papers/`——六专题各保留 S/A 档（B 档控总量未入库），S+A 共 23,230 TXT / 2.6GB；② `paper_parser.scan_papers` 新增两层布局支持（`_iter_tier_dirs` 自动识别 单专题档位直挂 / 六专题嵌套；`source_file` 含专题目录可追溯）+ 跨专题同文去重（(文件名, file_md5) 判定，保留首个——8,097 篇唯一 / 跳过 15,133 份副本，一篇命中多专题时文件逐字节相同，去重防提及/信号重复计权）；③ `PAPER_DIR` 两处从专题三子目录改为 `data/papers`（paper_config / timeline_config）；④ 验证：scan 89s 出 8,097 篇（S 197 与全库总索引精确一致 / A 7,900），`run_paper_delta --dry-run` 与 `run_timeline --papers --dry-run` 全通（日期覆盖 100%） |
| 2026-08-20 | **岗位体系 v2.0 构建（`build_jobs.py`，`docs/data-description.md` §6.2）**：① 人工骨架内置 255 个 v1 节点全量处置（`--validate` 强校验：保留合并 200 / 剔除低 IT 相关 46 / 纯类别退役 9，funtype 挂载覆盖 83.8%、未挂 part 逐条核对均属剔除域）；② `--sample` 全量扫描为 131 岗位各采 ≤4 条真实 JD（funtype 规范化匹配 + title_kw 标题兜底，鸿蒙等无 funtype 岗位经标题采到）；③ `--define` deepseek-v4-flash 逐岗阅读样例生成 definition/keywords（2,557 词）/boundary/name_en（`call_llm_raw` 本地原文调用，绕开 `call_api` 的固定数组提取；断点续跑）；④ 质量抽检发现 v1「半导体技术」样例定义漂移至制造工艺域 → 移入剔除表（HW-22 编号退役），终版 9 类别 / 131 岗位；⑤ **后续岗位归类弃用 funtype 映射，改逐 JD 关键词+LLM（引擎待建）**；运行时消费者（extractor/builder/graph）未切换，jobs0806.json 保留 v1 存档 |
| 2026-08-20 | **岗位体系介绍文档（`build_jobs.py --doc`）**：新增根目录 `introduction/` 目录与`岗位分类体系介绍.md`——面向阅读的体系文档（总览表 + 9 大类分章 + 131 岗位的定义/边界，不含 v1 历史与内部字段），由 jobs_v2.json 渲染生成、体系变更后重跑即同步；同步 README（目录树/数据流水线/体系产物一览切至 v2 口径）与 roadmap |
| 2026-08-21 | **JD→岗位归类引擎 `classify_job.py`（§3.9）**：四路信号——①岗位名层（标题含岗位名即归类）；②词库快路（jobs_v2 keywords）；③LLM 兜底（batch=20，prompt 含关键词提示 + 非 IT 域必空规则，指纹缓存断点续跑；排除表移出产品/项目管理岗）；④**任务/技能向量比对观测信号**（--vectors LLM 标注 JD 0/1 向量 → --vector-report 与基图岗位向量（v1→v2 聚合）算余弦 top-k + 一致率，约定用于新岗位探索转向与岗位模式漂移观察）。验证 job_2026_1_1.csv：名称 19 + 标题词 45 + 正文词 147 = 54.1% 快路；LLM 141 条经三轮 prompt 调优后非 IT 域（药学 QA/食品研发/电力安装等泛名称岗）正确判空；向量一致率 14%/3%（噪声源：基图向量系 200 JD 小样本、仅 51/131 岗有向量 + 关键词泛词，关键词精简与全量基图重建属后续调优）。全量归类与向量化运行待做 |
| 2026-08-21 | **任务体系 v0.3 边界判据岗位化（§3.4，`docs/data-description.md` §6.5）**：收录判据改为「信息技术岗位的工作职责」而非「任务是否技术性」——`prompts.py` 新增 `IT_GENERIC_DUTIES`（IT 岗位职责中的组织管理/人员培养/内部培训/跨部门协同/供应商技术对接），与 `NON_IT_DOMAINS` 构成双向边界（6 模板 + 4 个 format 调用点），提案/监督示例同步改写（保留法务反例、新增技术经理团队管理正例）；定向诊断（技术管理层 60 条）证实提案 Agent 把团队管理/培训内容吸收进 T-17/T-14，数据驱动发现无法暴露缺口 → 人工审定补 **T-26 技术团队管理 / T-27 技术培训与知识赋能**（tasks.json v0.3，25→27）；判据放宽后重跑 5 轮热更新（1000 条）零新增，未回涌非 IT 任务 |
| 2026-08-21 | **任务体系构建净化 + v0.2 重建（§3.4，`docs/data-description.md` §6.5）**：① `JDDataSource` 分层改 v2 口径——funtype part（norm_part 镜像 build_jobs）→ jobs_v2 funtypes 匹配兼作 IT 过滤（未命中行丢弃，行级保留 84.7%，去重后样本池 175.2 万条），层 = v2 一级类别 9 类；修复 funtype_it_map.json 丢失导致的静默退化（全量「其他」层）并删除死常量 `config.FUNTTYPE_MAP`；② `prompts.py` 六模板加非 IT 域边界（`NON_IT_DOMAINS` 常量统一口径，监督侧增设「非 IT 域零容忍」），替换提案示例中「法务可 add」的错误教唆；冷启动元信息改动态日期 + 版本 0.2；③ 重建运行：冷启动 500 条 → 25 个 IT 任务，5 轮热更新（20 子块 × 50 条）零新增收敛；旧 v0.1（35 项，含 13 个非 IT 任务）存档 `tasks0807.json`；**注意 T-编码同号不同义**——基图快照/ΔG e2e 探索文件/jd_vec_cache 基于旧编码，待全量基图重建与向量重跑时刷新 |
| 2026-08-21 | **技能体系基准切换至 `skills0821.json`（v0.5 命名规范化，`docs/data-description.md` §6.1）**：20 项仅改 name_zh（编码/定义/skill_type/name_en 不变；名称字数 5–18 → 7–10，众数 9），`classify/taxonomy_base.json` 主开关 + builder/extractor 两份 config 兜底默认 + `classify_job.py` SKILLS_PATH 同步切换（promotion 转正写入目标随 config 自动跟随）；extractor 句级缓存按 code 存储不受影响；`skills0805.json` 保留为存档 |
| 2026-08-21 | **JD 技能熟练度要求判定**（§3.3 `extractor/`，移植同项目简历侧熟练度交接方案）：新增 `jd_proficiency_prompts.py`（JD 版量规 P1-P4/U + D1-D4，语义与简历侧相反——梯度词为雇主要求一级证据，防通胀单一精通≠P4，罗列→U）+ `jd_proficiency.py`（证据组装复用句级分类缓存零成本 → 分块 LLM 量规评估 → 严格契约校验 → 确定性旗标 marker_level_conflict/marker_span_ambiguous/p4_without_high_signals 等）+ `run_jd_proficiency.py` 校准 CLI + settings.yaml `jd_proficiency` 节 + fixtures（mock 零 LLM 六组用例）；词面锚点降级为提示+旗标不定级（precision-first）；2026-05 窗口 200 JD/986 对校准通过（P2 40.7%/P3 28.7%/U 23.9%，契约失败 0.2%，锚点×等级交叉表对齐，抽查偏离均为正确的上下文保守判断）；`base_builder` 接线可选 "prof" 评估器 → 每窗口写 `base/skill_prof.json` 熟练度分布（build_info 增 n_prof_jds/n_prof_pairs；评估器缺席不写，旧 fixtures 不破）；技能 ID 与简历侧 team_skills 49/49 逐一对齐（按 code 连接，规避 v0.5 改名差异） |
| 2026-08-22 | **运行时岗位基准切换 v2 + 体系三基准齐至最新 + 非 IT 显式标签**：① `classify/taxonomy_base.json` jobs → `Jobs/jobs_v2.json`（tasks v0.3 / skills v0.5 原已最新）——extractor/builder config 兜底同步，`taxonomy.load_jobs` 以 `category` 兼容作 level（v1 字段回退），graph 基图岗位节点/funtype 映射随单一开关自动切 v2（131 岗位，未挂 v2 funtypes 的非 IT 行自然落 unmatched 丢弃）；② `promotion._write_jobs` 双结构适配（v2：GJ- 入 detail + `graduated` 日期 + meta.n_jobs；v1 简明体系树兼容路径保留），`test_promotion` 断言 v2 化；③ `classify_job.py` 非 IT 域显式 `["非IT相关"]` 标签（区别于"无法判断"的空数组；规则排除表 tier0 记 `non_it`；向量阶段 `load_non_it_keys()` 跳过非 IT 省 LLM 成本，全量归类后可作采样内容级过滤信号）；④ funtype_it_map.json 评估无需重建（§3.1 注记：增量拉取场景可从 jobs_v2 funtypes 确定性派生） |
| 2026-08-22 | **论文 ΔG Stage C：基线提及 → strengthenings 接入（roadmap P1 完成）+ 两项演化分析决策落档**：① `paper_delta.py` 新增 `strengthen_paper_mentions`——论文提及识别是分类式（提单元直接分类到体系 code，复用句级缓存），无需新闻侧的名称→code 映射；skill/task 双模式（岗位类 strengthenings 合成时被跳过，省一遍 job 分类成本）；`make_mention_extractors` 用 config 换出习语构建（paper_mention→cache 链路依赖 extractor 版 CACHE_DIR）；CLI `--no-mention`；② fixtures 新增 `test_paper_delta_mention`（双模式并入/规范名回填/tier×conf×decay 手算/同 paper 幂等/跨论文 noisy-OR/证据封顶/零提及零条目），七测全绿；③ 决策落档：粗粒度岗位下放（v2 §2.4）由 v2 归类引擎逐 JD 直接分类取代、不再实现（algorithm-design §2.4 注记 + loop-design §7 更新）；演化分析机制层收口为已完成，跨窗口趋势报告属时序分析负责线（roadmap 同步） |
| 2026-08-27 | **arXiv 2022 全库批次并入论文语料（新增 `paper_signal/arxiv_ingest.py`）**：源 `arxiv_txt_2022.zip`（arXiv 2022 全量 185,973 篇关键词 v2 分档）TXT 为元数据裸格式（Title/Authors/Published/Abstract，无全文），入库脚本以批次索引 xlsx（分档/得分/命中维度/证据句/直链）为权威字段源转换为标准头块格式写入 `data/papers/arxiv2022/{S档_核心,A档_重点}/`（原始元数据块保留为正文；B/C 档不入库，跨批次同 ID 防线；幂等）；S 51 + A 2,222 = 2,273 篇并入，与既有语料 ID 零重叠；`run_timeline --papers` 重建映射表 8,097 → 10,370 行（S 248 / A 10,122，全部有日期，2022-01..12 月均 112–280 篇），JD 窗口论文空窗 24 → 10（余 2021 最早 4 窗与 2023-01..06）；解析冒烟：头块字段/摘要/证据句零缺失；成本报告论文侧 250 → ~300 元（峰）同步修订 |
| 2026-08-28 | **JD ΔG v2（全量扫描+残差裁决）+ 组装参数重放基础设施**：① 新 `graph/jd_delta_v2.py` 替代 builder 100 条/窗抽样（0.1% 出现率新信号漏检 ~90%）——英文 token 差集 + 中文 n-gram Apriori 时间差分（df 带宽 [5,5%]/边缘函数字/子串归约 O(n) 重写/右续延检验 ≥0.8/语境词修剪/修剪借尸还魂拦截，四类碎片坑全部修复）+ HotUpdater 引擎注入裁决（结论跨窗缓存=永久背景，首窗一次性背景学习）+ 确证预筛（as-of 窗末全量口径，promote_min_jd_docs 升级）；② paper/news ΔG 增 `--window` 逐窗增量（pub_date≤窗末+断点，总量成本不变）+ 三源参与门 as-of 窗末（修复 date.today() 历史不可复现）；③ 薪资加权改 Stage D 组装期现算（窗口 median，重放即切换）；④ 新 `graph/replay.py` + 参数指纹（四节点 sha256+assembly_logic_version 入 build_info/快照/effective meta）+ synthesis 空叠层警告；兜底对齐（GB_SALARY_WEIGHT/chunk_skills）；⑤ 验证：fixtures 46 绿、2022-06 全量零 LLM 冒烟（43,060 IT JD 9s 扫描，池 16.7 万、TOP 干净）、replay 幂等实证（05/06 逐字节一致）；⑥ run_jd_delta 弃用标注；文档全面同步（algorithm-design §2.5 / loop-design v2.2 / roadmap / graph+builder README / 成本报告 / 系统介绍） |
| 2026-08-28 | **Stage D0 近重复（抄袭）过滤上线（赛题"抄袭"回应补全）**：新增 `graph/jd_dedup.py`——正文（_kept_text 去噪、标题不参与）3-gram simhash64 + 8×8 分块 pigeonhole 候选（海明 ≤7 保证不漏）+ 海明 ≤6 + Jaccard ≥0.95 双确认 + 按 opentime 序星型贪心聚类（簇根=最早发布），产 `{窗口}.dedup.json` 变体映射；接线五消费方（jd_sample 采样分母 / run_jd_extract 抽取输入 / base_builder 聚合 / jd_delta_v2 文档池 / jd_summary 汇总），run_pipeline 编排增 A→**D0**→S→B→C→D；存量窗 2022-05/06 经 replay 追溯生效（05：736 条采样内变体剔除，边 J-T 1456→1451/S-SP 3905→3901；06：707 条，J-T 1837→1831/S-SP 5991→5899，采样内变体占比 24.3% 与采样率 24.9% 交叉吻合）；实测变体占比 05 7.4%（541 簇）/06 6.9%（2,164 簇，最大 24 条换皮簇）；fixtures 新增 test_jd_dedup 七测（53 全绿）；文档同步（graph README D0 节 / 系统介绍六阶段+settings 表 / data-description 实测 / roadmap） |
| 2026-08-28 | **jd_delta_v2 带宽上限改按修剪后终名 df（2022-07 启动前核查发现）**：中文通道带宽原在语境词修剪前按源 gram df 判定——中频长变体（如"年以上经验"df 带内）修剪掉语境词后复活为超限短词（"年以上"真实 df 42,906 = 池 5% 上限 4,201 的 10 倍）进池顶行，与"max_df_ratio 砍通用搭配"语义不符且污染首窗裁决配额；修复为归并后按 `raw_df[终名]` 判带宽（源 df ≤ 终名 df 恒成立，检查强度只增不减），顺带拆分 n_cached/n_zh_vocab 统计归因；fixtures 新增 test_trim_resurrection_ceiling（红绿验证：旧代码失败、新代码通过，54 全绿）；graph README 中文通道次序表述同步（2-7→2-8 字、左延展检验已删的陈旧表述一并修正） |
| 2026-08-29 | **JD v2 裁决只产技能点与涵盖式映射 + 转正证据分级（用户质疑驱动，2022-07 首窗实跑暴露）**：① 裁决提示词修正——旧版把 task 定义为"工作职责级表述"且 alias 仅限"别名或变体"，导致 23 任务+10 技能几乎全部为既有体系的语义重复（前端开发/安装部署/PoC/FAE 等，LLM rationale 甚至自己点名了涵盖条目仍判新建，重演 194 任务膨胀的粒度崩坏）；kind 收紧为 skillpoint/alias 两种，alias 扩为涵盖式映射（同物异名/上下位包含/近义指称），task/skill 判定压为非技术缓存（coerced_from 留审计）；② 证据分级——发现通道证据标 grade=scan、确证通道标 grade=require，promotion 的确证文档数只认 require（否则 v2 当窗发现实体"出生即转正"——扫描发现每条自带 5 docs 直接满足门槛，dry-run 三候选强度恰好压线 0.25 即此机制指纹）；③ run_promotion 增 --as-of（缺省今天会把历史窗证据深度衰减错杀，与三源 ΔG 参与门同类泄漏的补修）；④ 存量矫正：2022-07 的 33 条 task/skill 重映射——32/32 全部 alias 到既有任务、keep_new 为零（实证无一真正新增），证据转为 strengthenings（grade=scan），裁决缓存同步改写 36 行，快照/合成重建（叠层任务 1081→1058、技能 316→306），转正 dry-run（--as-of 窗末）候选归零；⑤ snapshot_builder 显式 delta_files 即完整规格（缺键=该源缺席，修测试环境被生产 classify/DeltaG 污染的兜底泄漏）；fixtures 新增 test_task_skill_verdict_coerced/test_scan_grade_not_counted（56 全绿）；loop-design ⑨/2.4、graph README、系统介绍同步 |
| 2026-08-29 | **裁决 kind 恢复四种 + 独立新颖性复核守门（用户复审 b709e48 后修订——一刀切禁 task/skill 属因噎废食，JD 侧本就允许发现体系外新任务/新技能）**：恢复 task/skill 产出，初判提示词 v3 落点优先级 alias→skillpoint→task/skill（后者须给 nearest+why_not）；新增 `_novelty_recheck` 守门（`_NOVELTY_PROMPT` 换"守门员"视角、基调宁严勿宽宁映射勿新增、涵盖尺度从宽；批 25 含证据句）——task/skill 判定被涵盖→确定性改判 alias（code 校验），确无涵盖→维持新实体并在缓存行记 nearest/why_not 审计，复核未决或 covered 但 code 无效→不缓存不应用留待下窗；幻觉 alias code / 未知 kind 仍压非技术缓存（coerced_from）。证据分级 scan/require 与 --as-of 修复保持不变（转正语义与发现语义解耦：新任务/技能落叠层带 scan 证据，转正仍需后续窗口确证通道的 require 证据）。fixtures：test_task_skill_verdict_coerced 重写为 test_task_skill_novelty_gate（涵盖改 alias/真新实体落地/未决跳过/幻觉 code 四路），mock 按提示词"守门员"标记分发两阶段调用（56 全绿）；graph README / 系统介绍同步修订 |
| 2026-08-29 | **数据基面原则落地：采样后环节不再消费基面外数据（用户裁定"图谱就是用降采样后的数据构建"）+ v2 职责收缩为 task/skill 发现**：① scan_window_docs 增 sample.keys 基面过滤（采样未触发时全量即基面；A/D0 全量属入口过滤不受影响）——原全池扫描使基面外证据渗入图谱，违反降采样的数据量限制语义；② apply 收缩：只有 task/skill（经新颖性守门）写 ΔG 叠层，skillpoint（发现权威在 B 阶段句级抽取+三层归一；叠层技能点无父技能不转正属惰性条目，且 v2 弱归一曾产出 17% 与 L3 重复的实体）与 alias（被涵盖短语的市场存在已由基图无偏统计，scan 级增强重复计权）只入裁决缓存排水；③ 2022-07 迁移：旧全池产物备份 temp/ 后重置，样本基面重跑（999 新裁决全部排为非技术/技能点/别名，**零铸造**——守门实战极保守；确证通道首次实战：1,301 参与实体→27 条 require 级确证锚定论文/新闻叠层实体）；快照/合成重建（叠层技能点 720→401、v2 增强清零）；④ 转正 dry-run（--as-of 窗末）首次给出正确语义候选：4 个论文实体凭 require 确证达标（数据库审计 5 篇最强），是否执行待用户裁定（候选含"软技能"等粒度存疑项，论文通道粒度是已知松弛点）；⑤ 测试隔离修复：fixtures 共享 tmp 下两裁决测试改用独立文件名（旧名致 DeltaStore 续读污染）；56 全绿；文档全量表述→采样基面全面同步（graph README/loop-design/系统介绍/本文件） |
| 2026-08-29 | **确证时序修正：出生窗不确证，至少滞后一窗（用户裁定新信号生命周期）**：confirm_channel 的参与口径从窗末改为**窗首日**（window_start_date）——论文/新闻新信号的旅程为"窗 W 出生 → 窗 W+1 起进入 JD 侧响应范围（确证通道目标清单）→ 市场出现'要求掌握'响应（require）→ 双门槛达标转正写入基准体系"，出生即确证等于把同月数据回声当市场响应。同步澄清：merged 生产抽取不注入叠层标签（overlay_labels 注入仅存在于已弃用的旧抽样路径），基图抽取始终用固定体系基准——叠层对 JD 侧的唯一可见入口是确证通道。2022-07 复跑迁移：确证目标 1,301→1,158（排除 143 个窗内出生实体），32 命中/27 确证不变（全部落在 7 月前出生实体——已有确证本就合法），ΔG 内容与迁移前逐条一致；test_confirm_prefilter_dry 增窗初口径断言（56 全绿）；graph README 确证 bullet / loop-design ⑥b / 系统介绍 §八生命周期同步 |
| 2026-08-29 | **确证通道去除 exclude_src="jd"：三源新信号等同处理（用户裁定）**：旧约定（源管线注入标签时剔除本源条目防重复）被误用于确证通道，导致 JD 发现的新任务/技能永远进不了确证目标——出生后只能衰减、永远无法转正的死路，与"JD ΔG 新信号和论文新闻等同：都在后续窗口验证才落地"的语义相反。修复后确证目标=窗初已参与的**全部**叠层实体（任何出生源）；同月回声由窗初口径排除（v2 出生实体带 scan 证据，窗初强度为 0 不参与；跨源锚定条目的 JD 侧证据同理不计入窗初强度），无循环确认风险。2022-07 行为不变（本窗 v2 零铸造、锚定条目窗初强度为 0，目标清单仍是 1,158）；test_confirm_prefilter_dry 增 exclude_src 断言（56 全绿） |
| 2026-08-29 | **出生定义修正：入场窗（体系首次录入窗）而非证据日期（用户质疑首叠层窗出现转正候选驱动）**：压缩回填使 2022-01..06 的论文在 2022-07 首叠层窗一次性入场，证据日期口径把它们当"已存在老信号"当窗确证（27 条 require + 5 个转正候选）——但体系此前从未见过它们，市场响应必须来自入场窗之后的数据。实现：DeltaStore 增 born_window 戳（__init__ 由 now 推导，_create_entry 盖章；跨源合并取最早入场），confirm_channel 目标 = 入场窗严格早于本窗的参与实体。迁移：papers 1,557 + news 216 条存量补戳 2022-07（真实入场窗）；jd_delta 重置重跑（确证空转、ΔG 零写入），快照/合成重建（证据 7,281→5,110），**转正候选归零**——首叠层窗的正确形态；2022-08 起本窗入场的 1,773 个叠层实体开始接受市场确证。test_confirm_prefilter_dry 增同窗出生排除断言 + novelty 测试增 born_window 戳断言（56 全绿）；graph README / loop-design ⑥b / 系统介绍 §八同步 |
| 2026-08-29 | **论文/新闻 --window 改月度增量口径（用户质疑"07 窗怎会用之前的论文"）**：原实现为 pub_date ≤ 窗末的累积口径——首跑把 2022-01 起全部语料一次性并入（1,236 篇/260 条），与"窗口 W 只消费 W 月数据"的逐窗时序不一致。改为只处理 pub_date 落在本窗月份内的文档（更早月份属其自身窗口，错过即不入场；叠层纪元前语料即预史）。迁移：papers/news ΔG 重置（旧全量回填产物备份 temp/），2022-07 重跑仅当月 169 篇论文 + 10 条新闻（叠层 222 实体：145 任务/35 技能/42 技能点，证据 652；gap 44、新 T-S 28）；转正候选仍 0。代价说明：2022-01..06 论文 1,067 篇与 2017..2022-06 新闻约 250 条作为预史不再入场（句级缓存保留，若裁定按真实窗口回补可低成本重放）；56 测试全绿 |
| 2026-08-29 | **论文/新闻新信号双道裁决：映射初判 + 守门终审（用户观察叠层粒度问题驱动）**：用户指出两类病灶——相似内容未合并（脑波标注数据集构建/脑波数据采集）、从属既有体系（数据准备优化⊂数据分析）；日志实证单遍映射在粒度边界不稳定（同一候选凌晨运行拒绝、午后重跑 keep），且一篇工程实践文章可拆出 4-5 个流水线环节任务（CDN 容灾/切换/监控各自成条）。修复：① taxonomy_mapper 新增 `recheck_keeps` 守门终审（第二道独立 LLM，守门员视角、宁严勿宽宁映射勿新增；被涵盖→map_to、兄弟环节→merge_into/同批归簇改名（store 按名去重自然合并）、确无涵盖→保留记 nearest/why_not 审计、无效结论→拒绝），map_signals 自动接线两通道共享；② PROMPT_MAP 规则 3 增兄弟环节合并（采集/标注/构建/预处理、容灾/切换/监控/热备=一个任务的不同侧面）；③ NEWS_EXTRACT 增粒度门槛（可跨雇主复用的抽象类别，单一产品工程环节/数据集操作/硬件操作不构成任务）。2022-07 重跑实证：论文新任务 130→2、新闻 15→1（守门分布 map 178/merge 1），用户三案例全部修正（脑波数据采集拒、数据准备优化抽取端拦截、脑波标注数据集构建幸存属边界——生命周期继续筛）；叠层 222→56 实体（3 任务+53 技能点），strengthenings 53（短语的正确归宿=既有条目提及级增强）；新增 test_mapper_recheck（mock 两阶段，57 全绿） |
| 2026-08-29 | **ASSEMBLY_LOGIC_VERSION v1→v2 + 三窗 replay 刷新**：本轮组装层逻辑变更（快照合并传播 born_window、build_snapshot 显式 delta_files 即完整规格）入版；replay 05/06/07 全部重建，新指纹 c7b63d07ac16c86d 三层产物全一致、零陈旧 |
| 2026-08-30 | **新闻语料 zip 全量入库 + timeline 重建（数据侧，详见 data-description §3.2.2）**：新增 `news_signal/import_zip.py`（33 源 302,548 篇 → news_raw 277,129 入库/5,701 同名跳过/19,718 短文拒绝，byte 保真 + 幂等 + 解析器同口径过滤；新增 4 源目录），news_raw 共 33 源 282,945 篇；timeline 新闻映射重建 282,944 行（282,159 带日期，2015-04..2026-08）。管线代码零改动（zip TXT 走 news_parser 回退路径）；影响：2023 起各月 5-8k 篇，已由同日 edb99f4 月度降采样（cap=800，先抽样再筛选）消化，成本约 5 元/窗（详见成本测算报告 §4 执行口径） |
| 2026-08-31 | **GJ- 转正岗位一级类别补齐（前端反馈驱动，fe1199b）**：前端反馈 5 个转正岗位（GJ-001..005）category 为空——根因 `promotion._write_jobs` v2 分支硬编码空串，且次生影响 `classify_job.job_label_text` 按 category 分组建归类 prompt（空类别岗位缺席清单，prompt 文案硬编码"131 岗位"=136−5）、`builder/data_source` funtype→类别名映射得空。人工拍板补齐：GJ-001 人机协同专家→AID、GJ-002 平台工程师/GJ-003 科研软件工程师/GJ-005 产品工程师→DEV、GJ-004 量子技术从业者→AID（研发人才总称，主体叙事为前沿计算与算法研究）；`jobs_v2.json` v2.5→v2.6（CRLF 字节风格保持，diff 仅 6 处）。存量窗快照 `base/jobs.json` 不回填（前端体系视图读基准单一事实源）；GJ- 五要素（keywords/boundary/name_en）补齐另议 |
| 2026-08-31 | **JD 职级规则 lv2 + 存量 20 窗 patch 重放（前端反馈驱动，c09b963）**：前端反馈职级列仅 55.7% 有值——核验非管线 bug（官方规则复跑 0 判出、与 summary 一致），根因老源 51 个 job CSV 无 work_year 列 + lv1 正则盲区（`temp/level_recheck.py` 按 jd_text_key 对齐三窗空值行分桶实证：经验距离≤10 字太窄/英文年限无模式/"经历"措辞/年限标签/经验前置区间/裸"年以上"，27 条抽样零误报）。`annotate_jd.py` 升 lv2：六类正文年限模式 + 接受应届→L0（置于年限后）+ 标题英文级别词（全词边界）+ **funtype 级别词兜底（level_source 新枚举 funtype）**，`LEVEL_RULES_VERSION` 常量 + run_jd_extract meta 记录。存量重放走 patch（非 Stage B --force，避免清空 Stage C 的 skill_vec_prof）：vectors 仅重算 level/level_source 两字段、逐行 JSON round-trip 字节断言；**回溯校验：20 窗 × 21 非 level 列逐行逐值不一致=0**；193,039 行新增填充 25,291（总体 56.0%→69.1%，老窗 66-70%），已定级变化 3,825（title→text 优先级回归，符合官方"正文年限>标题词"设计），丢失 20=存量与 lv1 重放本就不一致的历史遗留；图结构零影响（base_builder/jd_delta_v2 不消费 JD level）；二次 patch changed=0（幂等），lv1 基准备份 temp/level_patch_backup |
| 2026-08-31 | **转正后类别归纳机制（job_categorize.py，用户拍板"每次转正完成后进行"）**：① 新增 `builder/job_categorize.py` 旁路环节——`find_uncategorized` 扫基准空/非法 category 岗位 → LLM 归纳（9 类描述+同类现有岗位清单→category/confidence/reason/runner_up，输出校验 ∈ 9 类）→ **人工确认**（回车接受/code 改判/s 跳过；assume_yes 供测试）→ 一次性写回（先备份 `categorize-{ts}/`、bump version、记 promotion_log；真实调用验证：提示词工程师→AID conf 0.85，与人工拍板口径一致）；② `run_promotion.py` CLI 收口后检测到新岗位自动进入归纳（非 tty 只提示人工补跑，绝不静默调 LLM/写基准；fixture 走库函数不受影响）；③ `_write_jobs` 候选自带 category 时直写；④ 修老坑：`run_promotion`/`categorize` 增 log_path 注入，test_promotion/test_job_categorize 均写临时日志（正式 promotion_log.md 曾混入 fixture 记录的根源）；fixtures 新增 test_job_categorize（扫描/写回流程含备份·version·日志·字段不动·收敛/suggest-only 不写/prompt 构造，零 LLM）；loop-design ⑨b 步骤、builder README、本表同步 |
| 2026-08-31 | **GJ- 三项收口：五要素补齐 + 快照回填 + 纳入归类 prompt（用户拍板）**：① 基准 v2.7——GJ-001..005 人工补齐 keywords（11-15 个/岗，贴 JD 实战用语，仅作 LLM prompt 提示；classify_job 规则快路只用 name_zh 不受影响）、boundary（与相近岗位区别句式，同 131 岗格式）、GJ-002/005 补 name_en；② 存量窗快照回填——19 窗 base/jobs.json 中 17 窗（v2.5/136 岗，含 GJ-）的 5 个 GJ- 条目整条同步为 v2.7 内容（category+五要素），窗口 version/date/131 老岗位零改动（回溯断言：GJ 条目换回后序列化与原文件一致；原快照备份 temp/snapshot_backfill_backup）；2022-12/2023-01 两窗为 v2.0/131 岗快照（构建时 GJ- 未转正，图产物按 131 岗计算）不回填；build_info 不记 jobs.json 指纹，回填不影响 replay/check；③ 纳入归类 prompt——JOB_PROMPT"9 大类 131 岗位"改 `{n_jobs}` 动态（_llm_batch 按 category 合法口径计数=136），category 补齐后 `job_label_text` 按 category 分组自动带 GJ- 入清单；jd_job_cache key=JD 文本指纹不含 prompt 版本，历史缓存不回算、仅新 JD 生效；`_llm_batch` 的 valid_codes 本含 GJ-（输出侧零改动）；④ 副作用改善：`data_source._build_funtype_top_map` 中 GJ- funtypes 现正确映射到类别名（原空串）；71 fixtures 全绿 |
| 2026-08-31 | **多 api-key 并行轮转 + 总并发放大（用户裁定提速）**：① key 管理——api-key.txt 每行一个 key（正则全量提取，多行/注释兼容），`load_api_keys()` 去重保序 + 环境变量多值（逗号/分号/空白分隔），`active_api_keys()` 取前 `llm.api_keys_parallel` 个；旧 `load_api_key()` 保留为首个 key 兼容；**key 只存 api-key.txt（gitignored），settings.yaml 只放开关**；② 轮转——builder/llm.py 与 extractor/llm.py（同源）各加线程安全 `KeyRing`（round-robin，进程级惰性单例），`call_llm` 显式传 api_key 时固定该 key（测试路径），缺省每请求轮转、**429/截断/异常重试换下一个 key**；llm_client.LLMClient 构造不再绑死单 key（_post 循环内重建 req 换 key）；classify_job._llm_batch 批级经模块自持 `_key_ring()` 取 key（对半重试递归天然换 key）；③ 并发——语义改为「每 key 并发」：总批次并发 = llm.concurrency × 启用 key 数（llm_client/jd_proficiency 用 `config.concurrency_total()`，classify_job 同口径），单 key 时与旧版完全一致；④ 实测：3 key 中第 3 个（尾 b08c）401 鉴权失败（非重试错误，直接抛出），**开关暂设 2**（6f50+3f75 实测有效，总并发 40→80），坏 key 修复后改回 3 即可；16 并发轮转请求全成功、轮转计数=请求数；71 fixtures 全绿 |
| 2026-09-01 | **api-key 启动预检（用户建议：批量运行前轮测可用性，减少中途错误-切换环节）**：`KeyRing` 首次构建（= 本进程第一次 LLM 请求前）对启用 key **并行**发极小探测请求（max_tokens=200、快速失败；401/403 立即判死，429/5xx/网络错重试一次防瞬时抖动误杀），不可用者本进程内剔除并打警告（尾 4 位标识，不泄露 key），全部不可用才中止并列出各 key 失败原因。三处同源接入：builder/llm.py 与 extractor/llm.py 的 `key_ring()`（内嵌 `_probe_key`）、classify_job 自持 `_key_ring()`（复用 call_llm_raw）。开关 `llm.key_probe`（缺省 true，false=全部上环旧行为）；probe 结果仅进程内有效（重启重测，坏 key 修复后无需改配置自动回归）。实测：3 key 启用（含 401 的尾 b08c）→ 预检剔除后 ring=2、请求正常；开关关闭回退 3 key 上环；71 fixtures 全绿；settings `api_keys_parallel` 回设 3（坏 key 由预检自动兜底）。probe 成本：每进程每 key 一次极小请求（批量任务可忽略） |
| 2026-09-01 | **api-key 状态修正：08-31 的"第 3 key 401"实为写入抄错一位；按用户裁定维持 3 账号**：08-31 手工写入 api-key.txt 时把 ...580**44**e25 抄成 ...580**84**e25 导致 401——原始 key 本身有效（修正后实测通过），但按用户裁定弃用；同日用户提供新 key（尾 c09d）实测有效，顶替第 3 席。现 3 个有效账号（尾 6f50/3f75/c09d），`api_keys_parallel` 维持 3（总并发 40→120，key_probe 预检兜底）；24 并发轮转实测全成功、71 fixtures 全绿 |
| 2026-09-01 | **RunLogger.error 关键字冲突修复 + 驱动器断点续跑（2024-10 窗新闻 LLM 瞬断实战暴露）**：① `_json(self, stage, **data)` 首位形参即 stage，`error()` 再传 `stage=` 关键字必 TypeError——LLM 批次失败进错误路径即崩（原应"记日志、跳批次、继续"），paper_logger/logger 两处同修（error_stage= 传子阶段）；② 驱动器 temp/run_window.py git 步自始用 bash 语法 `2>/dev/null`（cmd.exe 下必败，历窗提交实为人工兜底）→ 改 `2>NUL` 后首次自动提交成功；③ 新增 `--resume`：读 window_status/{W}.json 跳过已 ok 阶段（promotion 跳过时重读既有日志提取转正清单），2024-10 实战省 60 分钟；出生盘点纳入 jd_delta store（岗位归类仍只 papers/news——JD 侧岗位走 Pass 3.7 标题确证） |
| 2026-09-02 | **岗位守门（recheck_job_keeps）+ 岗位普适性规则 + 存量清理 13 条（用户裁定：论文侧岗位名过长过具体、近同义须删、加验证防再犯）**：① 病灶——new_job 类 keep 此前无第二道门（recheck_keeps 只审任务/技能）：近同义（数据标注员 vs AID-18 数据标注师）、子岗（GIS数据库管理员⊂GIS工程师/DBA）、场景限定角色（国会AI事务专员）、AI 产品误判为岗位（虚拟面试官/云端编码代理）直接出生；② 修复——`taxonomy_mapper.recheck_job_keeps`（确定性基线同名强制映射（含映射改名碰撞，零 LLM）+ 独立 LLM 终审：同义/子岗/变体→map 基线、叠层同岗→merge、非普适（机构/场景/人群限定、>10 字、AI 产品）→reject、确属普适新岗位→keep 记审计；无效结论拒绝；LLM 失败不推翻初判），map_signals 在任务/技能守门后接线（papers/news 共享）；③ 提示词——PROMPT_EXTRACT/PROMPT_MAP/NEWS_EXTRACT 增岗位普适门槛（脱离原文语境即可出现在招聘市场的头衔才可提取；场景/机构/人群限定角色不提取）；④ 存量清理——新守门审计 20 条活跃叠层岗位 + 人工复核（有 JD require 确证的 3 条豁免 override），删除 13 条（papers 12 + news 1：3 用户点名近同义 + 3 场景限定 + 2 AI 产品误判 + 5 基线涵盖/超长过具体），彻底清除+replay 33 窗回溯抹除；备份+裁定明细+情况说明 classify/backup/job-cleanup-20260902/；保留 7 条（前向部署工程师 1/3、AI测试自动化工程师 2/3、硬件工程总监 2/3 市场确证豁免；3 个专业算法工程师符合基线分岗惯例；虚拟现实工程师基线无对应）；⑤ 新增 test_mapper_job_gate（四路裁定+确定性改名碰撞+LLM 失败信号不丢，mock 零真实调用）；基线勘误：岗位 138（131+GJ×7） |
| 2026-09-02 | **402 熔断机制（用户裁定：资源性故障不得降级保信号）**：2025-06 窗实证——key#1 401/key#2 402 时 map_signals 保守降级（映射失败→整批 keep-new）绕过全部守门，34 条未审实体出生（已另行清除+重跑，备份 classify/backup/news402-20260902/）。三层修复：① llm.py（extractor/builder 双份同源）——新增 `ResourceExhaustedError`；单 key 402 不再直接抛而是**先轮转其他 key**（原实现 402 连换 key 都不换），启用 key 全部 402 → 抛熔断错误（消息含"全部启用 key 不可用"以命中驱动器 sh() 的自愈重跑判定）；重试上限取 max(RETRIES, 2×环大小) 保证 402 轮转可触及全部 key；② 十处降级点前置熔断放行（taxonomy_mapper 四处守门 + map_signals 主映射、signal/news/jd_extractor 批次、news_filter、mention_mapper）——except ResourceExhaustedError: raise，其余瞬时错误仍走"信号不丢"；③ test_llm_402_breaker 三用例（全 402 熔断且每 key 恰试一次/部分 402 轮转到健康 key 成功/map_signals 熔断向上传播不 force-keep；mock 真上下文管理器——SimpleNamespace 实例 dunder 不被 with 识别的坑）。78 fixtures 绿（base_builder 3 数值漂移旧债另册）。**key 现状（2026-09-02）：#1 尾 6f50 401 密钥失效待更换、#2 尾 3f75 402 待充值、#3 尾 c09d 正常** |

| 2026-09-03 | **Stage S0 大窗预抽样（用户裁定：大窗先抽部分样本再分类/降采样，比例授权研判）**：研判先行——timeline 新批次 CSV 的 wc -l 因描述字段内嵌换行虚高 ~14×，真实记录数 2026-01 29k（unique 指纹 25.9k）/ 02 41k（37k）/ 03 101k（91.9k），"史上最大窗"实为 2022-07（27.3 万 unique）以下；真实成本结构=Stage A LLM 兜底随 unique 线性（B/C 已被 S cap 封顶）。实现 `graph/jd_pre_sample.py`（零 LLM）：unique > presample_cap（默认 60k，settings jd_sampling，≤0 关闭）才触发，确定性哈希选 cap 个（md5 升序，单调扩展衔接缓存）+ w0=N/k 逆概率因子；**传播=唯一改写点在 A 门**（classify_job.collect 指纹计算后跳过未选键 → jobcls 只含已选键 → S/D0/B/v2/summary 全链自动受限，D0/B/v2 零改动）；Stage S 复合 w0 进各层权重（N_j/k_j×w0，总量无偏，预抽样窗 keys 恒显式）；run_pipeline 增 S0 步。校准：60k 只让 2026-03 触发（65.3%、w0=1.531，2026-03 干跑实证 A 门 LLM 兜底 6.4 万→4.2 万条判定省 35%），01 及以下全量零行为变化（2025-12 --stats 回归：唯一键/合计/排除表逐项一致，岗位名 +3 系 jobs_v2 新增 GJ-012/013 所致）。口径：预抽样窗 IT 总量为估计值（×w0 复原），转正 jd_docs 按基面硬计数（证据积累按选择率折减）；fixtures 新增 test_jd_pre_sample（选择确定性/单调扩展/collect 过滤/权重复合），81 绿（base_builder 3 旧债另册） |
| 2026-09-03 | **JD 解析完整测试方案上线（赛题可验证性：121 条 JD 测试集，用例制准确率 91.7%≥90% 达标）**：测试语料取自 2026-05（最后一个月）——① `graph/eval_jd_parse.py` 四子命令（build 确定性分层抽样 121 条=IT 池 9 大类 111+非IT 10 / gold 独立标注员 LLM / system 生产路径原样复用 / eval 指标报告），产物+方案文档入 `classify/eval/jd_parse/`（README 含指标定义、复现步骤、已知差距）；② gold 三层构建链（独立 LLM 直标→规则质控 2+31 项留审计→人工裁定 7 条逐条引正文证据，全部文件留档可复核）；③ 官方指标=用例制通过率（A1 岗位域必须过∧技能点/任务/技术栈三断言≥2，阈值 50%/交集——赛题'测试用例'措辞的天然口径），micro 复合分 65.5% 作参考并列示；④ **评测驱动两处生产修复**：A 门泛词岗系统性过含（项目经理/运维/测试/产品经理等泛词名原直收不看正文，2026-05 该流量 ~30% 错判 IT——AMBIGUOUS_JOB_NAMES 改送 LLM 按正文定域+JOB_PROMPT 泛词岗规则，重判 2,379 条翻转 1,147 条非IT；历史窗口缓存未回算待裁定）；解析出口技能点召回 36.5%→~72%（新增 `annotate_jd.extract_tech_mentions` 确定性技术名词层：registry+L3 名录 1.7 万+技术栈词表，词面匹配零 LLM，英文词边界/版本号并入/位置抑制（Spring⊂Spring Boot）/泛概念停用词，只进解析出口图谱口径不变）；⑤ 已知差距记录：T-14 IT技术支持类任务句级少判、实施/支持类技术栈规则常空、细岗 exact 42.9%；81 fixtures 绿 |
| 2026-09-03 | **JD 解析评测口径按用户裁定重构：只评体系内归类质量（IT/非IT 拒判不评分）**——"JD 是否正确分类为 IT 并不重要，有没有正确地归类其 skill、task 以及岗位类别为体系中的适当类别才是关键"。官方指标 = 三维度用例通过率平均：①岗位归类（系统岗位大类=gold 大类，判非IT 即失败，90.1%）②任务归类（gold 任务覆盖≥50%，90.1%）③技能归类（gold 技能覆盖≥50%，94.4%）→ **JD 解析准确率 91.5%（评分集 71/121）≥90% 达标**；参考指标并列：三维全过率 78.9%（最严）、细岗 exact 74.6%、IT 拒判一致率、技能点 F1（软匹配）、micro 复合 65.6%。README 第四节同步 |
| 2026-09-05 | **单元测试套件集中化 + 覆盖率达标（赛题：单测覆盖率≥60%）**：全部自测从 codes/*/fixtures/ 迁移并扩写至仓库根 `unit-tests/`（35 文件 175 用例全绿，~100 秒，零 LLM 零网络）——① 迁移 22 个旧 fixtures 测试并修复全部 8 个历史失败（5 个 config 跨包名冲突致 jd_proficiency 误绑 builder 配置；3 个 base_builder 数值漂移系真实 jd_vectors 消费模式绕过 mock 注入，测试加 `_no_vectors()` 隔离恢复 CSV 注入路径）；② 新增 13 个测试文件补齐未测模块：jd_annotate 门控/级别/技术名词层（泛词岗路由、词边界、版本折叠）、eval_jd_parse 指标核心（软匹配配对/最大余数法）、llm/llm_client（KeyRing 线程安全轮转、402 先换 key 再熔断、finish_reason=length 预算升级、token 记账）、builder 基础设施（分层抽样器配额策略/消费断点、体系存储增改并、监督层类型规整防线、提案应用同名防重）、jd_extractor/mention_mapper/paper_mention 信号通道、jd_summary/replay（非IT 与抄袭变体剔除、α 链校验/窗口洞检测）、make_presample/make_sample/build_variants 阶段产物端到端；③ **跨包导入卫生机制**（ut.py setup/isolate）：平铺包同名 config/llm/prompts 在同进程 pytest 下的确定性解析（实测捕获 participation 误绑 extractor 配置的污染）；④ 覆盖率口径 codified 于 unit-tests/.coveragerc：四包 50 模块 7,800 语句为分母，排除 run_* CLI 入口/提示词常量/日志层/一次性脚本/弃用 jd_delta（各附理由，E2E 分工背书），**实测 61.5%**（graph 69.2/extractor 68.4/builder 56.1/jd_annotate 40.9）；⑤ `run_tests.py` 一键运行+coverage-report.md 自动生成；说明文档 unit-tests/README.md（组件地图/运行方法/设计原则/三层验证分工/口径表）。graph/README 自测节与本文档 fixtures 引用同步指向 unit-tests/ |
| 2026-09-05（二） | **单元测试逐用例明细文档（用户要求：每个测试的输入输出须写入文档）**：① `unit-tests/TEST-CASES.md` 新增——175 例全覆盖的用例明细表，逐例列出输入构造（夹具/注入/参数）与期望输出（关键断言值）+ 属性标签（数学手算/确定性/幂等/容错降级/防线回归/协议契约/边界/读写字环/并发安全/端到端产物，标签语义表头定义），按测试文件分节、节首标注被测组件与职责，行序与源码一致；② 129 个缺失 docstring 的用例函数由目录数据自动注入（单一事实源 temp 生成器，作者文案优先、否则按输入/期望合成），代码内每个用例现均有说明；③ run_tests.py 增 JUnit XML 解析 → `unit-tests/test-results.md` 逐用例通过状态与耗时报告（175/175 通过可复验）；④ README 挂接三份报告的分工说明。终验 175 全绿、口径覆盖率 61.5% 不变 |
| 2026-09-05（三） | **用例汇总 CSV + LLM 真调用冒烟用例（用户要求；授权小批量实付调用）**：① 目录数据入仓 `unit-tests/case_catalog.py`（178 例单一事实源），`run_tests.py` 据此生成 `TEST-CASES.md`（明细表）与 **`test-cases.csv` 汇总表**——列=测试文件/用例/被测组件/输入/期望输出/属性/运行结果/耗时/结果说明，结果与耗时取自 JUnit XML 实跑，结果说明列默认'断言全过'、真调用用例合并实测观察；② 新增 `test_llm_live.py` 3 例真调用（JSON 契约/句级分类 49 技能体系/merged 抽取各 1 次请求，温度 0，合计约数千 token≈几分钱；无 codes/api-key.txt 自动 skip，离线 175 例不受影响）——实测：'熟悉Python开发与数据分析'→[T-SW-01,T-DA-04]、merged 抽取 Python 句→技能 T-SW-01/任务 T-01，协议契约在真实端点成立并记入 CSV 结果说明；③ 修复真调用引入的运行期模块污染（llm_client._post 惰性 import llm 连带绑定 extractor config 毒化 news_delta 新鲜导入——live 加 autouse 恢复夹具 + news 导入助手弹冲突名）；④ 终态 **178/178 全绿，口径覆盖率 62.6%**（extractor 68.4→73.3%，真调用补齐真实路径行） |
