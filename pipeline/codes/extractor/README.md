# Extractor 模块 — 分类抽取层（JD + 论文）

从数据中**识别/分类**技能、任务、岗位并计数。覆盖两类数据：
- **招聘 JD**：抽取技能/任务（含技能点），基于已构建的技能/任务分类体系
- **学术论文**：① **提及识别**（识别论文对**既有**技能/任务/岗位的提及）；② **新信号分类**（论文信号提取 + 体系映射，供 `codes/builder/` 做 ΔG 热更新）

## 功能

### JD 抽取（已有）
- **技能抽取**：基于技能体系（49 项，`classify/Skills/skills0821.json`，当前标准，基准经 `classify/taxonomy_base.json` 切换），从 JD 中识别每句涉及哪些技能，统计频次
- **技能点抽取**：识别技能对应的**技能点**（具体技术实体：工具/框架/语言/数据库/平台，如 Python、Spring Cloud、PyTorch），产出**技能→技能点映射字典**；软技能（F 开头）通常无技能点
- **任务抽取**：基于任务体系（27 项 v0.3，`classify/Tasks/tasks.json`），同理提取任务
- 通过 `--mode skill|task` 切换；另有 **`--mode merged`**（一句一次：同一子句一次出 skill+task+skillpoint，替代 skill/task 两次分离调用，句级 LLM 调用减半、不损穷举性——仍逐句扫，连罗列型技能都抓到）。merged 缓存按 prompt 版本隔离（现 `cache/cache_merged_v2.jsonl`；v2=2026-08-25 技能点口径收紧：禁品牌/设备/型号/泛指词，写通用标准名），`Extractor.extract(text)` 无需传 taxonomy（内部加载两套体系）。基图生产（`graph/run_jd_extract.py`，另做 skillpoint 后置清洗兜底）与 `base_builder.make_extractors` 均用 merged 模式

### JD 技能熟练度要求判定（2026-08-21 新增）
- **`jd_proficiency.py`**：对 JD 中明确要求的每个技能，评估岗位对该技能的**用人要求等级**（P1-P4/U，量规 D1-D4），precision-first 三段防线（移植同项目简历侧交接方案）：量规注入提示词 → 严格输出契约校验（重复键/多余字段/非法枚举拒绝，整块重试一次）→ 确定性正则旗标复核（`marker_level_conflict` 等，只打旗不改级）
- **语义与简历侧相反**：JD 中"精通/熟练/熟悉/掌握/了解"是雇主的明确要求表述，属一级证据（简历侧"自称精通不可信"）；但防要求通胀——单一"精通"不足以 P4
- **证据组装零成本**：复用句级分类（共享 `cache/cache_skill.jsonl`）取"句→技能code"证据映射；**词面锚点不作定级快路**，只注入 prompt 提示 + 事后一致性旗标
- 6 个聚合信号技能（F-1-01/F-1-03/F-1-04/F-3-04/F-4-01/F-4-02）跳过定级；无梯度表述的技术栈罗列 → U（U≠低要求）
- 按文本指纹缓存（`output/jd_prof_cache.jsonl`），rubric_version 变更自动失效重算
- **`run_jd_proficiency.py`** CLI：抽样（口径与基图生产路径一致）→ 评估 → 校准报告（等级分布/旗标率/词面锚点×等级交叉表）；2026-05 窗口 200 JD / 986 对校准通过（详见 `output/jd_prof_calibration.md`）。`--from-vectors`（Stage C 生产路径，2026-08-26 两遍式）：串行 prepare（跨 JD 证据去重必须在串行发生，防在飞重复破坏"同证据同判定"）→ 全窗 chunk 并行评估（chunk 保持 JD 内——profile 是该 JD 定级上下文）→ 串行 finalize 顺序写证据缓存
- 消费方：`codes/graph/base_builder.py`（每窗口写 `base/skill_prof.json` 熟练度分布，供演化分析）

### 论文提及识别（新增）
- **`paper_mention.py`**：识别论文文本对**既有**技能/任务/岗位的提及 —— 复用 JD 抽取的分句/缓存/LLM 设施
- 提单元 = 标题 + 关键词 + 摘要分句 + 证据句；按 `--mode skill|task|job` 分类
- 输出每篇论文的提及频次 + **证据**（命中的原文单元）+ 跨论文聚合（`classify/DeltaG/papers_mentions.json`）
- 这是 ΔG 增量层 `strengthenings` 与演化分析的输入（"哪些已有能力被前沿研究引用"）；2026-08-22 起被 `builder/paper_delta.py` Stage C 直接消费（`strengthen_paper_mentions`：skill/task 提及按 tier 权重并入 strengthenings，paper_id 幂等）——独立 CLI `run_paper_mention.py` 仍保留（papers_mentions.json 跨论文聚合产物，供分析侧使用，与 Stage C 共享句级缓存）

### 论文新信号分类（从 paper_signal 迁入）
- **`signal_extractor.py`**（Stage A）：对论文批提取**新信号**（new_job / new_task / new_skill / implied_task / capability_gap / skillpoint）
- **`taxonomy_mapper.py`**（Stage B）：候选信号 × 基础体系（tasks/skills/jobs）+ ΔG 已有条目 → map_to / merge_into / is_new / reject
- 与 `codes/builder/` 的 ΔG 热更新配合使用（`builder/run_paper_delta.py`）

### 行业新闻分类（新增）
- **`news_filter.py`**：**相关性过滤**（Stage 0）——LLM 判别 title + 导语（前 800 字），仅相关新闻进全文处理；**无关键词硬门槛**（2026-08-15 方案 B：新信号天然在词表之外，实测关键词门槛丢弃 44% 语料、其中 23% 实为相关；关键词词表已于 08-17 完全移除）
- **`news_extractor.py`**：新闻**信号提取**（Stage A）——相关新闻 → 新信号（name_zh/name_en/**definition**/**evidence**/confidence）+ 提及名称
- **`mention_mapper.py`**：**提及映射**——提及名称 → 既有体系 code（norm 精确匹配免费 + LLM 兜底）
- 与 `codes/builder/` 的新闻 ΔG 热更新配合使用（`builder/run_news_delta.py`）

## 目录结构

```
codes/extractor/
├── README.md              # 本文档
├── config.py              # 配置（API key/模型/路径/批参数；含 JOB_TAXONOMY）
├── taxonomy.py            # 分类体系加载（技能/任务/岗位）
├── text_split.py          # 文本分句（括号保护、长度过滤）
├── cache.py               # 句级结果缓存（持久化 JSONL）
├── prompts.py             # JD 分类提示词（技能/任务）
├── paper_prompts.py       # 论文提示词（新信号提取 + 体系映射 + 提及识别）
├── llm_client.py          # LLM 调用（批量/重试/禁用推理；classify_with 支持自定义模板）
├── llm.py                 # 函数式 call_llm（论文新信号分类用；与 builder/llm.py 同源）
├── extractor.py           # JD 抽取核心（分句→缓存→分类→计数）
├── run_extractor.py       # JD 抽取 CLI
├── jd_proficiency_prompts.py  # 熟练度量规（JD 版 P1-P4/U + D1-D4）+ 提示词
├── jd_proficiency.py      # 熟练度评估核心（证据组装→量规评估→契约校验→旗标）
├── run_jd_proficiency.py  # 熟练度校准 CLI（抽样→评估→报告）
├── paper_mention.py       # 论文提及识别核心
├── run_paper_mention.py   # 论文提及识别 CLI
├── signal_extractor.py    # 论文新信号提取（Stage A）
├── taxonomy_mapper.py     # 体系映射（Stage B）
└── cache/                 # 缓存目录（自动生成）
```

## 使用方式

### JD 抽取 CLI

```bash
python run_extractor.py --mode skill --input "负责Java后端开发，熟悉Spring Cloud微服务架构"
python run_extractor.py --mode task --input ../../data/jd_dataset/job_2026_05_30.csv --limit 100 --output out.json
```

### 论文提及识别 CLI（在模块目录下运行）

```bash
# 论文数据（走 codes/paper_signal 解析，含去重 + 断点续跑）
python run_paper_mention.py --mode skill --tier S --limit 20
python run_paper_mention.py --mode task --papers-dir PATH --no-resume
python run_paper_mention.py --mode job --tier A --limit 10

# 原始文本（同 JD 抽取）
python run_paper_mention.py --mode skill --input "某论文摘要文本..."

# 只解析论文 + 打印规模（不调 LLM）
python run_paper_mention.py --mode skill --tier S --dry-run
```

输出：`classify/DeltaG/papers_mentions.json`（`--limit` 探索运行写 `*_explore.json`，不动主断点）。

### JD 熟练度校准 CLI

```bash
python run_jd_proficiency.py --window 2026-05 --n 200   # 抽样评估 + 校准报告（真实 LLM）
python run_jd_proficiency.py --n 50 --no-cache          # 默认最新窗口，强制重评
```

输出：`output/jd_prof_results_{window}.json`（逐对明细）+ `output/jd_prof_calibration.md`（校准报告）。

### 论文新信号分类（配合 builder 的 ΔG 热更新）

```bash
cd codes/builder && python run_paper_delta.py --tier S --limit 20   # 详见 builder/README.md
```

### API（供 Agent 调度）

```python
from extractor import Extractor
import taxonomy as tax

tax = tax.load("skill")                    # 或 "task" / "job"
ext = Extractor(mode="skill")              # 可复用，内部持缓存
result = ext.extract(jd_text, tax)
# -> {"skill_counts": {"S-10": 2}, "skillpoint_counts": {"Java": 1}, ...}

from paper_mention import PaperMentionExtractor
pext = PaperMentionExtractor(mode="skill")
paper_result = pext.extract_paper(paper_record, tax)
# -> {"mentions": {"S-02": 5}, "evidence": {"S-02": ["标题：...", ...]}, "skillpoints": {...}}
```

## 优化机制

| 机制 | 说明 |
|------|------|
| **分句 + 句级分类** | JD/论文片段切分为子句后逐句分类（1:1 或 1:n），粒度可控 |
| **句级缓存（持久化）** | 同一子句只调一次 LLM，结果存 `cache/cache_{mode}.jsonl` |
| **批量 LLM** | 每批 15 句送入一次调用，摊销成本 |
| **禁用推理** | deepseek-v4-flash 关闭 thinking（实测提速约 60×） |
| **断点续跑** | 论文提及识别按论文批次存 `{output}_checkpoint.json`，中断后继续 |
| **熟练度指纹缓存** | 熟练度评估按 JD 文本指纹存 `output/jd_prof_cache.jsonl`，rubric 版本变更自动失效 |

## 新实体命名标准与降级保留

论文/新闻/岗位关联三源共享同一套**发现—定义—命名**标准（详见 `docs/algorithm-design-v2.md` §2.5）：

- **发现**：仅显式信号（直接提及新技能/任务/岗位）、evidence 逐字原文、宁缺毋滥、粒度契约（工具/语言归技能点层）。
- **定义**：`definition` = "它是什么"，1-2 句自足；缺定义或证据的候选直接丢弃（发现硬约束）。
- **命名**：`name_zh` 简洁自足（任务/技能通常 4-12 字上限 14，岗位 4-10 字），映射层 LLM 归一化为与既有体系一致的简洁规范名。
- **降级保留（不丢信号）**：超长名（> `MAX_NAME_CHARS=20`）由 `fit_name`（各校验器内置）在连接词边界截断、保留末尾核心概念，候选**继续进入映射层**归一化；仅 < 2 字的退化名丢弃。校验层是护栏，映射层 LLM 才是最终命名修正处。

## 计数语义

- `skill_counts`：`{code: count}`，count = 该技能/任务在文本中**被提及的子句数**
- `skillpoint_counts`：`{技能点: count}`，技能点 = 具体技术实体（原词，不归一化）
- 论文提及识别：`mentions`（每篇句频）+ `evidence`（命中原文单元）+ 跨论文 `mention_paper_counts`（提及该条目的论文数）

## 扩展点

- **自定义分类体系**：`--taxonomy` 传入 JSON（`items` 数组含 `code/name_zh/name_en`）
- **新增抽取类型**：`prompts.py` / `paper_prompts.py` 增加模板、`taxonomy.py` 增加加载函数、`llm_client.py` 已支持任意字段（skills/tasks/jobs）
- **跨模块**：论文解析层 `codes/paper_signal/` 提供 `paper_parser`/`paper_source`；ΔG 热更新层 `codes/builder/` 消费本模块的新信号分类结果
