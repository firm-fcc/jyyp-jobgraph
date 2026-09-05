# Graph 模块 — 图谱时间截面存储 + 基图边计算 + 图谱合成

把图谱按**时间截面**组织存储：每个时间窗口（月 `YYYY-MM` 或季度 `YYYY-Qn`）一个文件夹，
内含 `base/`（基图）、`delta/`（叠层）与 `effective/`（合成 G_eff，独立存储层）三个子图；
节点用体系 JSON，关系用边 JSON（每种连边一个文件）。

三个动作（Loop 顺序见 `docs/loop-design.md`）：
1. **快照构建**（`run_snapshot.py`，纯 stdlib）：体系节点原样拷贝 + 论文/新闻/JD **三源**
   ΔG 合并为单层（按窗口过滤证据、重算强度、跳过已转正条目、附 `participates` 可见性标记）；
2. **基图边计算**（`run_base_build.py`，LLM）：抽样 JD → extractor 分类 → 频次聚合，
   填充 J-T/J-S/T-S/S-SP 四种边（简版 Graph Builder）；
3. **图谱合成**（`run_synthesis.py`，纯 stdlib）：G_eff = G_base ⊕ ΔG → `effective/`，
   只读 base/ 与 delta/、只写 effective/。

> 叠层第三源 `jd_delta.json`（JD 市场确证，权重 1.0/半衰期 365 天）由
> `codes/graph/jd_delta_v2.py` 生成（采样基面扫描+残差裁决，2026-08-27 起替代旧
> `builder/run_jd_delta.py` 100 条抽样——0.1% 出现率的新信号抽样漏检 ~90%）；
> 叠层生命周期（可见性/遗忘/确证/转正）见 `docs/algorithm-design-v2.md` §2.7 与
> `docs/loop-design.md` §3。

## 目录结构

```
data/graph/                        # GRAPH_ROOT（gitignored，可脚本重建）
├── 2026-05/                       # 时间窗口（month: YYYY-MM；quarter: YYYY-Qn）
│   ├── meta.json                  # 窗口/粒度/period/inputs/weights/stats
│   ├── base/                      # 基图 G_base
│   │   ├── jobs.json              # 节点：岗位（原样拷贝 jobs_v2.json，经 taxonomy_base.json 切换）
│   │   ├── tasks.json             # 节点：任务（原样拷贝 tasks.json）
│   │   ├── skills.json            # 节点：技能（原样拷贝当前基准，经 classify/taxonomy_base.json 切换，现为 skills0821.json）
│   │   ├── skillpoints.json       # 节点：技能点（base_builder 回填发现的 SP）
│   │   ├── job_task.json          # 边 J-T（base_builder 填充）
│   │   ├── job_skill.json         # 边 J-S
│   │   ├── task_skill.json        # 边 T-S
│   │   ├── skill_skillpoint.json  # 边 S-SP
│   │   ├── freq.json              # 累积加权频次（跨窗口 α 衰减链，base_builder 维护）
│   │   ├── entity_freq.json       # 实体文档频率 E_jd（合成 gap 用）
│   │   ├── skill_prof.json        # 技能熟练度要求分布（jd_proficiency 聚合，演化分析用）
│   │   └── build_info.json        # 基图构建的抽样/参数/统计记录
│   ├── delta/                     # 叠层 ΔG（papers+news+jd 三源合并为单层）
│   │   ├── new_jobs.json          # 节点 PJ-（含 related_tasks/related_skills）
│   │   ├── new_tasks.json         # 节点 PT-（graduated 条目已移出视图；条目附 participates）
│   │   ├── new_skills.json        # 节点 PS-
│   │   ├── skillpoints.json       # 节点 PK-
│   │   ├── strengthenings.json    # 修正：对基图条目增强
│   │   └── job_links.json         # 边：新岗位→任务/技能（relation+weight）
│   └── effective/                 # 合成 G_eff = G_base ⊕ ΔG（synthesis 产物，独立层）
│       ├── job_task.json          # 每条边含 origin/base_weight/delta_weight/effective_weight
│       ├── job_skill.json         #   origin ∈ base（基图边，可含 Δw 修正）/ delta（job_links 新边）
│       ├── task_skill.json        #   / synthesized（双端有 gap 合成的新 T-S 边）
│       ├── skill_skillpoint.json
│       ├── new_entities.json      # 进入 G_eff 的叠层实体清单（PJ-/PT-/PS-/PK- + 关联边数）
│       └── meta.json              # λ 参数快照/inputs/stats
```

## 使用方式

在模块目录下运行：

```bash
cd codes/graph
# ① 快照构建（骨架 + 三源叠层；已有窗口默认拒绝覆盖，--force 重建且保留已非空基图边）
python run_snapshot.py list
python run_snapshot.py build --window 2026-05
python run_snapshot.py build --window 2026-05 --force --papers-delta X.json --news-delta Y.json --jd-delta Z.json
python run_snapshot.py check --window 2026-05

# ② 基图边计算（先 dry-run 看抽样分布；LLM = extractor task/skill 双模式）
python run_base_build.py --window 2026-05 --dry-run
python run_base_build.py --window 2026-05            # 默认抽样 200 条（settings.yaml → graph_base）
python run_base_build.py --window 2026-05 --force    # 覆盖已非空的边

# ③ 图谱合成（纯 stdlib；可重复重算，直接覆盖 effective/）
python run_synthesis.py build --window 2026-05 --dry-run
python run_synthesis.py build --window 2026-05
python run_synthesis.py check --window 2026-05
```

- `--window auto`（快照）= 数据最大月份（JD timeline > 叠层证据 > 当前月）
- 快照与合成零 LLM；基图边计算按 `graph_base` 节参数抽样（分层：每岗位 ≤ per_job 条）

## jd_vectors 源文件与基图六阶段流线（Stage A→D0→S→B→C→D，逐窗时序推进）

### Stage S0：大窗预抽样（jd_pre_sample.py，零 LLM，2026-09-03 用户裁定）

窗口 **unique 指纹 > presample_cap（默认 60k）** 才触发：确定性哈希选 precap 个
（md5 升序，与 S 同款：可复现/可审计/cap 上调时已选键集单调扩展），产
`{窗口}.presample.json`（keys + w0=N/k）；未触发窗写 keys=null、行为零变化。
**传播机制（唯一改写点在 A 门）**：classify_job.collect 在指纹计算后跳过未选键 →
jobcls 只含已选键 → S/D0/B/v2/summary 经 load_full_classification 全链自动受限。
Stage S 把 w0 复合进各层逆概率权重（N_j/k_j × w0），窗口总量/边权保持无偏。
**口径注意**：预抽样窗的 IT 总量/岗位构成为估计值（×w0 复原）；转正确证计数
（jd_docs）按基面硬计数——require 证据积累速度按选择率 r=k/N 折减（r≥0.6 影响轻微）。
**校准依据**（真实记录数——新批次 CSV 的 wc -l 因描述字段内嵌换行虚高 ~14×）：
2026-01 29k（unique 25.9k）/ 02 41k（37k）/ 03 101k（91.9k）——60k 恰只让 2026-03
触发（65.3%，w0=1.531，A 门 LLM 兜底 6.4 万→4.2 万条判定省 35%）；A 门 LLM 兜底是
唯一随窗规模增长的成本项（B/C 已被 S cap 封顶）。历史最大 2022-07（27.3 万 unique）
当时全量跑通，未触发窗不受影响。参数 `settings.yaml → jd_sampling.presample_cap`
（≤0 关闭）。

### Stage D0：近重复（抄袭）过滤（jd_dedup.py，零 LLM）

回应赛题"抄袭"（algorithm-design.md §4.4.2 轻量实现）：对全窗唯一 IT JD 的去噪正文
（`_kept_text`，标题不参与——抄袭常改标题不改正文）做字符 3-gram simhash64 →
8×8 位分块候选（鸽笼保证海明 ≤7 不漏）→ 海明 ≤6 + 3-gram Jaccard ≥0.95 双确认 →
星型贪心聚类（按 opentime 序，簇根恒为最早发布）。产物 `{窗口}.dedup.json`
（变体键→代表键），消费方在线过滤：S（采样分母）/ B（抽取输入）/ D（聚合，存量窗
经 replay 追溯）/ jd_delta_v2（发现与确证文档池）。仅窗内去重（跨月重发=逐月在场，
属时序统计语义）；跨窗时序抄袭留作二期（v1 A.4）。实测 2022-05/06：变体占比
7.4%/6.9%，最大簇 31/24 条；06 窗 S-SP 边 -92（模板簇技能点边被去除）。

全量（非抽样）基图构建按**逐窗时序**推进（模拟数据到达顺序，也是后续月度更新的固定流程）：
新一个月 CSV → **S0 大窗预抽样（unique>60k 才触发）** → A 门归类（S0 已选键，未触发=全量）→
**S 按需降采样**（超 cap 才采）→ B/C/D。
`run_pipeline.py --window {月}` 一键串起各步（各步幂等/断点，重跑只补未完成部分）。
每 JD 的分类成果落 `data/timeline/jd_derived/{窗口}.jd_vectors.jsonl`（+ `.meta.json`）为可复用源文件。
**目录约定**：`data/timeline/jd/` 只放源数据月度 CSV；管线附属产物全部落 `data/timeline/jd_derived/`
（`{窗口}.jd_vectors.jsonl` + `.meta.json` + `{窗口}.sample.json` + `{窗口}.jobcls.json`
+ `{窗口}.presample.json` + `{窗口}.dedup.json`）。

| 阶段 | 模块 | 内容 | LLM |
|---|---|---|---|
| S0 预抽样 | `graph/jd_pre_sample.py` | 大窗守门（零 LLM）：unique 指纹 > presample_cap 才触发，确定性哈希选 cap 个 + w0=N/k 逆概率因子；A 门只归类已选键（上游唯一改写点，下游全链经 jobcls 自动受限）；未触发 keys=null 零行为变化 | 否 |
| A 门 | `jd_annotate/classify_job.py --strict` | 全量该窗 JD → jobs_v2 岗位归类 → `jd_job_cache.jsonl`（job_code + non_it + **tier 判定层**：1=标题岗位名直收/3=LLM 内容复核，0=排除表非IT）。**严格门**（`settings.yaml → jd_gate.strict`，默认开）：仅"岗位名出现在标题"直接采信，关键词命中（含正文）泛词误报多（'测试'→通信测试、'监控'→运维、'质检'→数据标注）一律送 LLM 按内容复核；再加 **it_scope 岗位范围**（`graph/it_scope.json` 排除集：硬件/半导体全类、通信现场/设备类、数据标注等 34 岗，判据=49 技能体系对该岗是否充分适用），范围外 → it_related=False | 是 |
| S 降采样 | `graph/jd_sample.py` | 读 A 归类结果（零 LLM）→ 窗口 IT > cap 时**分层封顶抽样**：岗位层内比例分配 + 稀疏岗保底（floor=30 层内全保）、确定性哈希选取（md5(jd_key+salt)，可复现/可审计/扩量嵌套——cap 提高时原采样键全保留，缓存自动衔接）、**逆概率权重** w=N_j/k_j（S0 预抽样窗再 ×w0，见上）→ `{窗口}.sample.json`（keys + 各层分母；未触 cap 且无 S0 时 keys=null 不过滤，S0 触发窗 keys 恒显式）。**A 全量（S0 未触发时）、只采 B/C**：A 相对便宜且换精确时序分母与分层标签 | 否 |
| B 抽取 | `graph/run_jd_extract.py` | join 门 → 跳 non_it/范围外/非采样键 → JD 分段去 other(福利/公司介绍等) → IT JD 跑 Extractor(**merged**：一句一次出 skill+task+skillpoint，句级缓存 `cache_merged_v2`）→ jd_vectors 源文件（`skill_vec_01`/`task_vec_01`/`skillpoint_map`/`evidence_map`/techstack/level/**sample_weight**）+ meta；**无技术信号降级**（技能/任务/技术栈全空 → it_related=False + drop_reason，多为泛词漏网的非 IT JD）；skillpoint 后置清洗（品牌/设备/泛指黑名单 + 厂商前缀改写"西门子PLC"→"PLC" + 全角/大小写归一） | 是 |
| C 熟练度 | `extractor/run_jd_proficiency.py --from-vectors` | 读 evidence_map → 跨 JD 证据去重定级（证据级缓存 `jd_prof_evidence_cache.jsonl`，批 chunk_skills）→ 回填 `skill_vec_prof`（P1-P4/U）到源文件 + meta.rubric_version；**软技能门控**（`jd_proficiency.soft_gate`，默认开：F- 技能无梯度词 → 确定性 U 免 LLM）；`--marker-gated` 全技能无梯度词→U 免 LLM | 是 |
| D 聚合 | `graph/run_base_build.py` | 消费 jd_vectors 源文件（剔除 it_related=False 记录 → 重建 per-JD 集合 → accumulate（**权重 = sample_weight × salary_weight**，降采样窗口的频次对总体无偏）→ α 链 → build_edges），跳过字符串门/抽样/LLM 抽取；skill_prof.json 由 `skill_vec_prof` 按权重聚合；**自动生成 JD 多维汇总 CSV**（`data/graph/data/jd_summary_{window}.csv`，每 JD 一行、各项用**中文名**：岗位/技能/任务/技术栈/级别/熟练度/采样权重，仅 it_related 记录；代号留 jd_vectors 供程序 join） | 否 |

- **句级抽取优化（B）**：merged 模式**一句一次**出 skill+task+skillpoint（替代 skill/task
  两次分离调用，句级 LLM 调用减半、不损穷举性——仍逐句扫，连罗列型技能都抓到）；JD 分段跳过
  福利/公司介绍等 other 段（实测 ~4% 句，干净无损）。熟练度（C）不逐句，per-(JD,技能) 对独立一遍。
- **技能点归一体系（2026-08-27，registry v2）**：三层归一使同一技术不因命名差异分裂——
  L1 字面折叠（norm_key：大小写/分隔符，如 Mybatis→MyBatis）→ L2 人工审定注册表
  `skillpoint_registry.json`（v2：136 条 curated，canonical+aliases+category；category
  13 类含"办公"，判定规则表写在注册表 note）→ L3 LLM 首见归一（未知名批 50 个/次，
  merge 须逐字命中已有 canonical 否则按新实体登记；判定落 `output/skillpoint_alias_cache.jsonl`
  跨窗复用，人工审定层优先于缓存，可复审后提升进注册表；类别判定规则 `CATEGORY_RULES` 注入 L3 prompt（含正反例，标准≠技术规范等防歧义条款）；存量口径变更可用 `skillpoint_retag.py` 批量重判类别（~75 次调用）或 `--min-count N` 按频次过滤）。
  硬口径：只合并同一技术的命名变体，不同代际/组件保持独立（Spring≠Spring Boot、
  C≠C++≠C#、AngularJS≠Angular、MyBatis≠MyBatis-Plus）；**版本号并入母项**（HTML5→HTML、
  CSS3→CSS、ES6/ECMAScript 5→JavaScript，经 retired 重映射，历史缓存判定同步重定向）；
  **书写惯例一对多展开**（C/C++→C、C++ 各计一次，expansions）；实现与抽象招聘语义等价可
  合并（Shell←Bash）。`skillpoint_norm.py` 在 B 阶段 Pass 3.5 在线生效；存量窗口用
  `skillpoint_backfill.py` 回填（口径变更可 --no-llm 零成本重刷）。实测（2022-05/06）：
  唯一 skillpoint 5,022→3,899，TOP100 高频技能点两窗 100% 一致；TOP300 类别分布
  工具68/框架49/协议36/语言31/平台24/库17/方法16/硬件14/数据库12/中间件10/系统9/标准9/办公4。
- **技能点口径（2026-08-25 v2）**：prompt 收紧为"可独立学习/可考核的具体技术载体"并显式
  禁品牌/公司/产品/设备/型号/泛指词（v1 长尾混入"天猫精灵/西门子/ZKT E320/示波器"类），
  写通用标准名（"西门子PLC"→"PLC"）；run_jd_extract 再做后置清洗兜底（黑名单+前缀改写+
  归一化）。缓存按版本隔离（`cache_merged` → `cache_merged_v2`），prompt 改动不污染旧结果。
- **严格岗位筛选（A 门，2026-08-25）**：实测 2025-10 零技能 JD 113 条几乎全为泛词误报放进
  的非 IT JD（键盘厂测试→通信测试、家具品控→运维、食品检验→运维）。三层防线：①岗位范围
  `it_scope.json`（34 岗排除，影响 36% JD）；②严格门关键词命中送 LLM 复核；③B 端无技术
  信号降级。管理型岗位（产品经理/项目经理等）技术栈缺失可解释，保留。
- **降采样（S 门，2026-08-26）**：成本与时序考量下的窗口内抽样。参数 `settings.yaml →
  jd_sampling`（cap=1 万/floor=30/salt 固定）。2025-10 全量基线蒙特卡洛验证（20 种子）：
  rate=10% 时 J-T/J-S/T-S 边 Jaccard ≥0.96、公共边权重 Pearson ≥0.989、漏边权重 ≤0.3%、
  技能分布 TV ≤0.017、top30 重合 99%、岗位层零丢失；无保底纯比例会漏 31% 边权重、稀疏岗
  消失——floor 不可去。S-SP 长尾边最敏感（Jaccard ~0.80，漏失均低权边；大窗绝对样本量
  更大只会更好）。全量成本（峰 ~1.65 万/谷 ~0.95 万）→ cap=1 万后 ~6,650 峰/~3,835 谷
  （B/C 5,167 + A 全量 1,480），见 `introduction/成本测算报告.md`。
- **熟练度 U 的下游约定**：无法判级（U）≠低要求，但人岗匹配时视作**最低档要求**参与
  （技能存在即要求，未写梯度按 P1 对待）；软技能通常无熟练度表述，soft_gate 直接 U 免 LLM。
- **两版本技能向量**：`skill_vec_01`（0/1 present codes，供聚合，全 49 技能含聚合信号）与
  `skill_vec_prof`（P1-P4/U，43 可定级技能；6 聚合信号技能不定级，C 回填）。
- **复用契约**：meta 记 taxonomy sha256 + rubric_version + params，下游校验基准一致方可复用。
- **消费模式**：`base_builder.build_base` 检测 `{窗口}.jd_vectors.jsonl` 存在则走消费路径
  （零 LLM），否则回退旧路径（字符串 funtype 门 + 200 抽样 + LLM 抽取，保 2026-05 兼容）。
- **LLM 并发（2026-08-26 全阶段并行化）**：A/B/C 均按 `settings.yaml → llm.concurrency`
  （默认 20，1=串行）并发——A 门 LLM 批间并发（`classify_job`，2025-11 实测 230 条 12 批
  4.7s，串行需 ~35s）；B 为**两遍式**（Pass1 扫描分句 → Pass2 全窗唯一句批并发分类 →
  Pass3 逐 JD 组装全缓存命中，替代旧逐 JD 单批串行）；C 同为两遍式（Pass1 串行 prepare
  ——跨 JD 证据去重必须在串行阶段发生，防在飞重复破坏"同证据同判定"；Pass2 全窗 chunk
  并行；Pass3 串行 finalize 写缓存）。批级容错：单批失败不中断整窗，未答单元不缓存、
  下次自动重试（2025-10 重跑实测补回旧版漏答句 139 句）。不改变调用数/token。
- **窗口归类缓存（{窗口}.jobcls.json）**：大窗（60 万行）collect 扫描 ~33 分钟，A 跑满后
  落盘"规则层+LLM"合并结果（PRE-scope，it_scope 过滤仍在线应用），S/B 直接读免重复扫描
  （2026-08-26）。新鲜度 = csv mtime/size + jobs_v2 sha256 + strict 口径。
- 成本估算见 `docs/cost-estimate.md`；大窗墙钟预估（cap=1万，按 2026-08-26 实测波延迟外推：
  A 波 ~5s / B 波 ~25s / C 波 ~15s，并发 20）：A ~30min + 扫描 ~33min + B ~2h + C ~2h ≈
  **4-5h/窗**（旧串行路径 >20h；调高 llm.concurrency 可再压）。

## 基图边计算公式（简版 Graph Builder，algorithm-design-v2 §1）

- 岗位映射：旧路径按 JD.funtype `" or "` 拆分 → jobs_v2 detail 的 funtypes/名称 → 岗位 code（字符串门，
  仅消费模式缺 jd_vectors 时回退用）；全量流线 Stage A 由 `classify_job.py` 内容级归类（名/关键词/LLM 兜底）替代
- 薪资加权：`weight(jd) = log(1 + salary_monthly / median)`（万/千/·N薪//年/元每天统一折月）
- 文档级 presence 频次，跨窗口 `freq = freq_new + α·freq_hist`（α=0.85，读上窗 freq.json）：
  - J-T / J-S：`base_weight = W(J,X)/W(J)`
  - T-S：`ts_w1·W(T,S)/W(T) + ts_w2·I(explicit)`（显式项无数据源恒 0，预留）
  - S-SP：`W(S,SP)/W(S)`（多对多，分母按各 S 独立）
- 附带产物：`entity_freq.json`（E_jd = W(含实体)/W(全部)，合成 gap 的分母侧）、
  `freq.json`（衰减链）、`build_info.json`（可追溯性）、
  `skill_prof.json`（2026-08-21 起每技能 P1-P4/U 要求分布 + 旗标计数，
  `extractor/jd_proficiency` 逐 (JD×技能) 量规评估后按窗口聚合；计数为不加权对数，
  与薪资加权频次口径解耦）

## 图谱合成规则（algorithm-design-v2 §2/§5）

- `gap(E) = max(0, strength_ΔG(E) − E_jd(E))`（strength 为快照内按窗口末重算的叠层强度，
  §2.3 衰减已在快照阶段完成；仅 tasks/skills 参与边修正，jobs 类增强计数跳过）
- 基图边修正：J-T/J-S `Δw = λ_j·gap(右端)`；T-S `Δw = λ_ts·gap(T)·gap(S)`；
  S-SP `Δw = λ_sp·gap(S)`（按父技能 gap 的降级实现，见模块注释）
- 合成新边：双端 gap>0 且无基图边的 (T,S) 按强度降序取前 `max_new_ts_edges` 条；
  `job_links`（PJ-→T/S）作为 G_eff 新边（base_weight=0）
- `effective_weight = base_weight + delta_weight`（加法合成）；λ 参数在
  settings.yaml → synthesis，随 `effective/meta.json` 存档保证可复现

## 叠层合并语义

- ΔG 是"截至窗口末"的**累积单层视图**：三源（论文/新闻/JD）`new_*` 按 `norm(name_zh)` 合并、`strengthenings` 按 `(taxonomy, code)` 合并，来源可追溯靠 per-evidence 的 `src`/`tier` 键 + per-entry 的 `sources` 字段。
- **证据日期过滤**：`date ≤ period_end`（含末天）保留；无日期或解析失败**保守保留**。
- **强度重算（三分支判源）**：`ev.src=="jd"` → `JD_SOURCE_WEIGHT×半衰期365`；有 `tier` 键 → 论文 `TIER_WEIGHTS×半衰期730`；否则 → 新闻 `0.4×半衰期180`（旧数据兼容兜底）。复用 `delta_store._recency_decay/_noisy_or/norm` + `config` 权重常量。
- **转正条目移出视图**：`status=="graduated"`（已入基图，`builder/promotion.py` 标记）的条目跳过（stats 记 `n_graduated_skipped`），证据历史保留在 ΔG 源文件。
- **可见性标记**：条目附 `participates`（strength ≥ `overlay.participate_min_strength`=0.15 才参与下一次更新的标签空间；遗忘=跌破门槛休眠不删除）。
- 三源均缺失/为空 → 空 ΔG（meta 记 `delta_missing`），合法输入。

## 自测

测试已集中至仓库根 `unit-tests/`（35 文件 175 用例，覆盖四包核心模块，零 LLM 零网络；
说明与覆盖率口径见 `unit-tests/README.md`）：

```bash
python unit-tests/run_tests.py                 # 一键全量 + 覆盖率报告（61.5%）
python -m pytest unit-tests/test_snapshot.py -q   # 单文件（原 fixtures 各测对应同名 test_*.py）
```

## 加载 API（供可视化/匹配消费）

```python
from graph_snapshot import GraphSnapshot

snap = GraphSnapshot.load("2026-05")
snap.base_labels()        # {tasks, skills, jobs}，对齐 taxonomy_mapper.load_base_labels
snap.nodes("delta", "new_jobs")   # 归一化节点列表 [{id, name_zh, name_en, ...}]
snap.node_index()         # 全局 id → {layer, kind, node}（基图/叠层 id 空间不冲突）
snap.edges("job_task")    # 基图某种边的列表
snap.strengthenings()     # 叠层增强记录
snap.job_links()          # 叠层岗位关联边
snap.entity_freq()        # E_jd 实体文档频率（缺 base_builder 产物时 None）
snap.effective_edges("job_task")   # 合成层边（未合成返回 []）
snap.validate()           # 结构校验（错误清单，空=通过）
```

## JD ΔG v2（采样基面扫描 + 残差裁决，2026-08-27 起）

`jd_delta_v2.py`：**数据基面 = Stage S 降采样后的选择集**（采样是成本所致的数据量限制，
图谱的一切产物只能来自基面内数据；A/D0 全量属入口过滤，采样后的环节不再消费基面外
文档）——确定性部分扫基面内全部文档，LLM 只裁决频次过槛的残差。

- **发现通道① 英文 token 差集**：基面内 IT JD（jobcls 缓存供归类）切句，抽英文词（含 5G 类
  数字前缀、版本号、+/# 后缀）→ norm 折叠后对已知词表差集（skills/tasks/jobs 体系名 +
  技能点注册表 137 + L3 缓存 3,700+ + 八类技术栈关键词，~4.7k 键）；
- **发现通道② 中文 n-gram 时间差分**：CJK 连续段抽 2-8 字片段按文档频统计（Apriori 逐级
  上卷控内存无漏检）→ 边缘函数字过滤 → 子串归约（右续延检验 ≥0.8 判碎片，防"开发经"类
  孤儿）→ 语境词修剪（熟悉/掌握…/经验/能力…）→ 词表/裁决缓存差分 → df 带宽
  [min_docs, max_df_ratio·N]（按修剪后终名的真实 df——中频变体如"年以上经验"修剪出的
  超限短词"年以上"在此封死）→ 要求段亲和度排序（技术词集中任职要求段）；
- **裁决**：`builder/HotUpdater` 引擎注入复用（propose=LLM 批判 50/批含减半重试，
  supervise=契约校验，apply=DeltaStore 写入；store 换 ΔG store、投喂换残差候选、落点换
  叠层而非体系文件）。**四种落点，alias 最优先**：skillpoint（具体技术点，最常见）、
  alias（涵盖式映射：同物异名/上下位包含/近义指称，如 前端开发⊂应用软件开发、
  PoC⊂售前技术支持——先问"是否只是某基线条目的另一种说法或子集"）、task/skill
  （体系外新职责/能力域，初判须给 nearest+why_not）。**task/skill 判定过独立的新颖性
  复核守门**（`_novelty_recheck`：换视角提示词，基调宁严勿宽、宁映射勿新增；被涵盖→
  改判 alias，确无涵盖→维持新实体并记 nearest/why_not 审计，复核未决→不缓存留待下窗）；
  幻觉 alias code 压非技术缓存（coerced_from 留审计）。发现证据标 `grade=scan`。结论落
  `output/jd_v2_adjudication.jsonl` 跨窗缓存——非技术判定是永久"背景"（首窗冷启动=
  一次性背景学习，普通要求搭配涌入带内裁决一遍后逐窗衰减）；
- **确证通道（2026-08-30 起迁移至 Stage B 叠层分类参与，原算法设计恢复）**：确证目标 =
  **入场窗早于本窗**的参与实体（`born_window` 戳 = 体系首次录入该信号的窗口标签，
  **非证据日期**——一次性回填的旧语料不能凭旧日期提前确证；首个叠层窗因此必无确证
  与转正），且不分出生源。实现 = B 阶段（run_jd_extract）把参与实体作为**临时标签注入
  分类提示词**，与既有技能/任务一起在分类任务中运行；句级命中（**语义判定，含同义/
  改写表述**，非字面匹配）→ Pass 4 落 require 级证据（confirm_named → jd_delta.json，
  按 doc_id 幂等；date=opentime/confidence=high/src="jd"/grade=require）——转正的确证
  文档数只认 require 级（scan 级只计强度）。**岗位走 JD 标题级**（Pass 3.7：标题对前瞻
  岗位画像批量判对应，宁缺毋滥——句级实测岗位零命中，句子归技能/任务不关联角色画像；
  live 冒烟 6/6 行业特定标题命中、5/5 通用标题正确不命中）。v2 原有的子串预筛确证通道已退役
  （全名精确匹配漏语域错配：论文学名 vs JD 俗名，19 实体 9 窗 0 命中；句级缓存随
  PROMPT_MERGED v3 一次性换血）；
- **与热更新的分工**：`run_builder`（HotUpdater 原样）= 离线体系构建/大修（直接写体系、
  人工审定）；jd_delta_v2 = 逐窗时序发现/确证（进叠层走生命周期，转正才入体系）。
- **叠层命名纪律与处置（2026-08-30，同日两次修订）**：命名从**从业者视角**（人做什么/会什么，
  非机器视角）；任务=职责活动（动词性）/技能=能力方法（名词性）；禁跨类同名；命名 ≤10 字
  取精要——纪律在五处守门提示词统一执行。存量处置：**重命名=就地改名+回溯传播**（自出生窗
  起全窗快照经 replay 统一呈现新名，审计链留 store；含机器人技能示教等 7 项，其中多技能模型
  融合调优→语言模型技能注入系原名提取偏差据源文正名）；**退役=彻底清除+回溯抹除**（终裁：
  同名碰撞 1 + 跨 kind 冗余侧 3 + 类别名岗位 2 + 机制残留 2 共 8 条自 store 删除、全窗 replay
  重建后零出现；备份与情况说明 `classify/backup/overlay-retire-20260830/`；`remapped_window`
  生效窗门保留为延迟退场可选工具）；既有任务定义已按源文补齐（随重命名回溯生效）。
- 已知边界：纯中文 2 字新词（信创/等保）发现弱——论文前瞻源 + B 阶段叠层分类参与（语义级）补足；
  技能点粒度的开放发现 B 阶段 L3 已覆盖，v2 只做体系级信号。

```bash
python codes/graph/jd_delta_v2.py --window 2022-06 --dry-run   # 零 LLM：池统计 + TOP 候选
python codes/graph/jd_delta_v2.py --window 2022-06             # 全流程（裁决 + 确证 + ΔG）
```

## 参数重放操作面（两层分离，改参零 LLM）

**LLM 层产物 = 证据（一次付费终身复用）**：jd_vectors.jsonl / 三源 ΔG json（原始
date/tier/confidence 证据）/ 句级与证据缓存。**组装层 = 参数化纯函数（随时重算）**：
Stage D 聚合、快照强度重算（tier 权重/半衰期/min_strength 按窗末从原始证据全量重算，不依赖
落盘时的旧参数）、合成。改 A 组参数后一条命令重放：

```bash
python codes/graph/replay.py --all --dry-run   # 计划预览（α 链要求从最早窗口整链重建）
python codes/graph/replay.py --all             # D→快照→合成 按时间序重建，零 LLM
```

| 组 | 参数（settings.yaml） | 改后操作 |
|---|---|---|
| A 组·纯组装 | `strength.*`、`overlay.*`、`graph_base.alpha/ts_w1/ts_w2/salary_weight`、`synthesis.*` | `replay.py`（已有快照的窗口自动含快照+合成） |
| B 组·影响 LLM | `llm.*`、`papers/news/jd.*` 截断与批组织、`jd_proficiency.*`、`jd_gate.strict`、`it_scope.json`、`jd_sampling.cap/floor/salt` | 有 API 成本：需重跑对应 LLM 阶段（salt 改动=键集全变） |

- **参数指纹**：上述 A 组四节点 sha256 + `ASSEMBLY_LOGIC_VERSION`（硬编码组装逻辑变更时
  手工步进）→ 写入 base/build_info、快照 meta、effective/meta；`replay.py` 结尾核对并提示
  陈旧产物——每份图都能看出是哪套参数算出来的。
- **薪资加权**现于 Stage D 组装期计算（窗口 median，log(1+s/median)），改开关重放即生效，
  无需重跑 B/C；**双面参数**注意 `overlay.participate_min_strength`——重放只影响组装面，
  历史运行时 LLM 提示词的标签空间不会（也不应）回溯改变。
- 幂等已验证（2026-08-28）：2022-05/06 重放后边/频次/技能点与正式运行逐字节一致（仅
  created 时间戳差异）。
- promotion（转正）不进自动重放——改体系基准文件属跨窗状态变更，保持手动（备份机制已有）。

## 设计要点

- **全量快照自包含**：任意时间截面可直接加载，无需重放历史（数据量小，冗余可接受）。
- **三层互不覆盖**：snapshot 只写 base 节点与 delta；base_builder 只写 base 边与附属；
  synthesis 只写 effective。快照 `--force` 重建默认**保留**已非空基图边（`--reset-base-edges` 重置）。
- **磁盘原样拷贝、读取层归一化**：基图节点 = 体系 JSON 原样；`nodes()` 统一为 `[{id,name_zh,name_en,...}]`。
- **节点/边分离**：节点文件 = 实体（四层 + 叠层新实体）；边文件 = 关系（每种连边一个文件，header 含 `relation`/`window`/`total`）。
- **meta 含 weights/params 快照**：强度与 λ 参数随截面存档，常量日后改动不影响已建截面的解释。
- 文档约定：同一根目录不混月/季度粒度。
