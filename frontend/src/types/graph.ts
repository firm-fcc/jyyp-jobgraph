/* ============================================================
   数据契约 —— 严格对齐 algorithm-design v2 与《算法返回数据.md》
   前端所有视图只依赖本文件的类型；接后端时只需实现 api/client.ts
   ============================================================ */

export type NodeKind = 'job' | 'task' | 'skill' | 'skillpoint';

/** 边状态机（算法 §4.2） */
export type EdgeStatus = 'candidate' | 'active' | 'strengthening' | 'weakening';

/** 数据来源类型 */
export type SourceType = 'jd' | 'paper' | 'news' | 'resume';

/**
 * 一级归属标签。
 *
 * 初版把它写成六个技术栈的联合类型，并让四个层级共用同一条轴 ——
 * 那是演示词表时代的产物。算法侧产出的三份分类文件里没有"技术栈"这一维，
 * 而且三层各有各的一级归属（岗位有 15 个大类、能力有 2 个维度 / 9 个组、
 * 任务是扁平的），不存在一条跨三层的公共轴。
 *
 * 因此这里放宽为字符串：每一层存自己的一级归属名。
 * 下面的 TECH_STACKS 只在演示词表（VITE_DATA=mock）下作为轴使用，
 * 真实数据下不再充当任何视觉通道。
 */
export type TechStack = string;

/** 演示词表专用的六个技术栈。真实数据下不作为坐标轴，仅保留给 mock 分支 */
export const TECH_STACKS: TechStack[] = [
  '大模型与AIGC',
  '数据与智能分析',
  '智能系统与感知',
  '物联网与边缘计算',
  '安全与合规',
  'AI基础设施',
];

/**
 * 数据来源等级 —— 决定"这个图元该不该存在"。
 *
 *   measured  算法侧真实产出，可回溯到源文件的某一条记录
 *   derived   由真实字段人工/规则推导，可核验但不是直接观测
 *   synthetic 前端为跑通视图补的，任何情况下都不得当作结论展示
 *
 * 字段级的来源判定在 data/provenance.ts，本字段是实体级的。
 */
export type Provenance = 'measured' | 'derived' | 'synthetic';

/* 技能点的软硬分类。算法侧只给硬 / 软两类，故这里也只有两态。
   skills0821.json 里另有 6 项标着 hybrid（软硬兼具），读取时按所属的
   一级维度并回两类之一，见 realTaxonomy 的 normalizeSkillType。 */
export type SkillType = 'hard' | 'soft';

/** 一条证据（证据链元素）—— 幻觉防控的最小单元 */
export interface EvidenceRef {
  docId: string;
  sourceType: SourceType;
  title: string;
  /** 出处：论文一律为 arXiv，新闻为媒体名。招聘信息一侧不适用，缺省不给 */
  outlet?: string;
  publishedAt: string;
  /** 锚定该边/该实体的原文片段 */
  snippet: string;
  /** 薪资加权 weight(jd) = log(1 + salary / salary_median) */
  salaryWeight: number;
  /** 原创性评分（抄袭检测），1 = 完全原创 */
  originality: number;
  /** 若被判定为副本，指向最早发布的那一条。必须是同一条边的证据里真实存在的 docId ——
      指到一个不存在的文档等于“有个抄袭标记但点不开被抄的原文”，那个标记就不可核验 */
  duplicateOf?: string;
  /** 与 duplicateOf 所指原文的结构相似度，与 PlagiarismCluster.members[].sim 同口径 */
  duplicateSim?: number;
  company?: string;
  city?: string;
  /**
   * 这条片段实际点名的那个实体（node.id）。
   *
   * 它一般不等于所在边的终点：一条“岗位—能力”边的原文写的是“精通 Python”，
   * 点名的是技能点 SP:Python，而边的终点是能力 S:编程能力。
   * “原文 → 抽出的技能点 → 归并到能力 → 权重”这条测量链缺了这一跳就断在中间，
   * 能力权重只能作为结论展示，无法回溯到它是怎么被测出来的。
   */
  extractedNodeId?: string;
  /** 被点名的那串字在 snippet 里的位置 [起点, 长度]，供原文高亮 */
  span?: [number, number];
}

/** 属性分布（地点/学历/经验/薪资/公司类别） */
export interface Distribution {
  [bucket: string]: number;
}

export interface JobAttributes {
  cities: Distribution;
  degrees: Distribution;
  experience: Distribution;
  salaryBands: Distribution;
  /** 技术方向的构成。取自招聘信息汇总表逐条标注的技术栈一列，一条可标多个方向 */
  techStacks: Distribution;
  medianSalary: number;
  postCount: number;
}

/** 图节点 */
/**
 * 岗位定义五要素中的后三项。
 *
 * 既有岗位由招聘信息汇总表推出：覆盖率是提到该技能的条数占比，熟练度是写明
 * 程度词的那部分的平均档位（P1–P4 记 1–4）。叠层新岗位没有招聘投放，改由
 * 推导的能力构成给出，此时只有相对权重 w，via 记作 inferred。
 */
export interface JobDefElement {
  /** 技能的体系编码 */
  code: string;
  name: string;
  /** 覆盖率 0–1，仅既有岗位有 */
  cov?: number;
  /** 平均档位 1–4，仅既有岗位有 */
  lvl?: number;
  /** 支撑条数 */
  n?: number;
  /** 相对权重 0–1，仅叠层新岗位有 */
  w?: number;
}

export interface JobDefinition {
  /** 缺省为招聘统计实测；inferred 表示由推导的能力构成给出 */
  via?: 'inferred';
  /** 该岗位在汇总表内的条数，叠层新岗位为 0 */
  n: number;
  must: JobDefElement[];
  plus: JobDefElement[];
  /** 技术方向及其覆盖占比。一条招聘信息可同时标注多个方向，故各项之和大于一 */
  scenarios: { name: string; share: number; n?: number }[];
  /** 叠层新岗位的场景由这几个最相近的既有岗位加权得出 */
  peers?: { id: string; sim: number }[];
}

export interface GraphNode {
  id: string;
  kind: NodeKind;
  /** 规范名称 */
  name: string;
  aliases: string[];
  /** 领域标签 / 技术栈 */
  category: TechStack;
  /** 职位定义（仅 job） */
  definition?: string;
  /** 核心职责（仅 job，岗位画像五要素之一） */
  coreDuties?: string[];
  /** 必备技能（仅 job） */
  mustSkills?: string[];
  /** 加分技能（仅 job） */
  plusSkills?: string[];
  /** 典型行业应用场景（仅 job） */
  scenarios?: string[];
  /** 岗位定义中由招聘统计推出的三项，逐项带口径量（仅 job） */
  jobDef?: JobDefinition;
  /** 发现时间 */
  firstSeen: string;
  lastConfirmed: string;
  /** 市场占比 0–1 */
  marketShare: number;
  /** 出现频次 */
  frequency: number;
  /** 置信度 0–1 */
  confidence: number;
  status: EdgeStatus;
  /** base = JD 已确认；overlay = 仅前瞻信号支持（幽灵节点） */
  origin: 'base' | 'overlay';
  /** gap(E) = max(0, E_paper_news − E_jd) */
  gap: number;
  /** 职位聚类（仅 job） */
  cluster?: string;
  attrs?: JobAttributes;
  /** 是否为新发现的萌芽岗位 */
  emerging?: boolean;
  /**
   * 层级：SkillPoint 的成熟度级别 1 基础 / 2 进阶 / 3 前沿。
   * @deprecated 真实数据里没有成熟度分档，只在演示词表下有值。
   */
  level?: 1 | 2 | 3;
  /** 岗位在 Skill 空间的二维投影（后端可预计算 UMAP） */
  embedding2d?: [number, number];

  /* ---- 以下为真实数据接入后新增的字段 ---- */

  /** 这个节点该不该存在 —— 见 Provenance 说明 */
  provenance?: Provenance;
  /** 技能点软硬分类（仅 skillpoint，来自 skills0821.json） */
  skillType?: SkillType;
  /**
   * 该节点唯一可计量的真实规模：
   *   岗位   = 招聘信息条数（岗位体系 v2.0 的 hits）
   *   能力组 = 组内技能点数
   * 与 marketShare / frequency 不同，这个数来自源文件本身，不是补的。
   */
  realCount?: number;
  /** 招聘信息条数（仅 job，来自岗位体系 v2.0 的 hits，全库合计 510 万条） */
  posts?: number;
  /** 招聘平台原始职能名（仅 job，v2.0 保留了 255 条用于溯源） */
  funtypes?: string[];
  /** 归类判定关键词（仅 job，v2.0 真实字段，全库 2557 条） */
  keywords?: string[];
  /** 与最易混淆的同侪岗位之间的判据（仅 job，v2.0 真实字段，131 条） */
  boundary?: string;
  /** 一级类别名（仅 job）。无一级归属时记为 ORPHAN_CLUSTER */
  topCategory?: string;
}

export type EdgeKind = 'J-T' | 'J-S' | 'T-S' | 'S-SP' | 'S-S' | 'T-T';

/** 图边 */
export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kind: EdgeKind;
  /** 基图权重（仅 JD 统计） */
  baseWeight: number;
  /** 叠层初始修正 Δw₀ */
  deltaWeight0: number;
  /** 叠层当前修正（已施加 e^(−γΔt) 衰减） */
  deltaWeight: number;
  /** effective = base ⊕ Δ */
  effectiveWeight: number;
  /** 动态置信度 */
  confidence: number;
  /** 共现频率（关系强度） */
  cooccurrence: number;
  /** 原文是否显式表达"用 X 做 Y" */
  explicitLink: boolean;
  firstSeen: string;
  lastConfirmed: string;
  /** Δt_unconfirmed：自 gap 首次检测至今 JD 仍未确认的月数 */
  unconfirmedMonths: number;
  status: EdgeStatus;
  /** 来源构成 → 可信度指纹 */
  sourceMix: { jd: number; paper: number; news: number };
  evidence: EvidenceRef[];
  /**
   * 这条边该不该画。真实数据下只有能力组→技能点这一类是 measured，
   * 岗位—任务、任务—能力、岗位—能力三类映射算法侧尚未产出，一律 synthetic。
   */
  provenance?: Provenance;
}

/** 实体三源强度时间序列（算法 §2.1） */
export interface EntitySignal {
  entityId: string;
  entityName: string;
  kind: NodeKind;
  category: TechStack;
  months: string[];
  /** E_jd(t) */
  jd: number[];
  /** E_arxiv(t) */
  paper: number[];
  /** E_news(t) */
  news: number[];
  /** gap(t) = max(0, 0.7·paper + 0.3·news − jd) */
  gap: number[];
  firstPaperAt?: string;
  firstNewsAt?: string;
  firstJdAt?: string;
  /** 实测领先月数（互相关最优滞后） */
  leadMonths: { paper?: number; news?: number };
  /** 尚未被 JD 确认时，预测的确认时间与区间 */
  predictedJdAt?: string;
  predictedJdRange?: [string, string];
  /**
   * 上面两个预测值是怎么得来的，供界面就地交代口径。
   *
   *   route        外推所依据的那一路信源，也是首现月的取值来源
   *   n            参与取分位数的实测滞后条数
   *   waited       该条目自首现至末窗已等待的月数
   *   beyondSample 已等待月数超出实测滞后的上界，预计时间自末窗起算而非自首现起算
   *
   * 缺这一项则界面只能把预测值当作一个凭空的月份摆出来，读者无从判断它有多可信。
   */
  predictedBasis?: {
    route: 'paper' | 'news';
    n: number;
    waited: number;
    beyondSample: boolean;
  };
  /** 当前 Δw 衰减系数 e^(−γΔt) */
  decayFactor: number;
}

/* ==================== 图谱的时间维 ==================== */

/**
 * 图谱的月度切片 —— 算法侧按月返回的那一份数据。
 *
 * 图谱是一张结构图：岗位—任务—能力组—技能点四层，每层若干条目。
 * 时间维不改结构，只改每一条当月的量，所以这份数据的形状是"逐月的量"，
 * 而不是"逐月的一整张图"。这条约束不是为了省传输：条目的落位（在第几行、
 * 归在哪个组下）一旦跟着月份变，同一个条目每换一个月就换一个位置，
 * 眼睛追不住，时间轴也就读不出变化。后端因此只需按月给出每个节点的量，
 * 落位由前端按末月的结构一次算定。
 *
 * 名字里的 Prism 是历史包袱：这份数据最早只服务能力棱镜，棱镜撤掉之后，
 * 全景图谱的岗位—能力流图读的仍是同一份。
 */
export interface PrismTimeline {
  /** 升序月份，'YYYY-MM' */
  months: string[];
  /**
   * 结构规模的逐月序列，键为 node.id，长度与 months 对齐。
   * 岗位 = 子树叶子数，能力组 = 组内技能点数 —— 都是可数的计量。
   *
   * null 与 0 不是一回事：null = 当月该条目还不在图谱里（不画），
   * 0 = 当月测得为零（画一条贴底的段）。混成一种就读不出某一段是何时长出来的。
   * 只有拿得出真实计数的层需要这条序列，其余层不给键即可。
   */
  series: Record<string, (number | null)[]>;
  /**
   * 要求强度的逐月相对因子，四层（岗位 / 任务 / 能力组 / 技能点）都有。
   *
   * 与 series 的分工：series 是"图谱里有多少东西"（计数），demand 是
   * "岗位对它的要求有多高"（强度）。全景图谱的条长走的是后者 ——
   * 赛题问的是岗位的能力要求，不是体系的规模。
   *
   * 取值为相对末个实测月的比值，末月恒为 1：绝对强度的量纲由前端按
   * 当前口径（岗位范围 × 职级）现算，后端只需给出"这一项在各月分别是
   * 末月的百分之多少"。这样换一个口径不必重取一遍月度数据。
   * null 同上 —— 当月该条目尚未进入图谱。
   *
   * 取值可以大于 1：一项能力在历史某个月比现在更受重视，正是时间轴要看的东西。
   * 但有上界，实测段与外推段同一条 —— 全景图谱按「全时段峰值」给条长定标尺，
   * 上界即那把尺子的量程，没有上界就没有一把定得住的尺子。
   */
  demand?: Record<string, (number | null)[]>;
  /**
   * 每个节点首次被招聘要求确认的月份。缺省 = 至今尚未确认。
   *
   * 与 demand 并存而不是二选一：demand 回答"要求有多高"，
   * 这个字段回答"哪一个月开始算数"—— 一项能力在被招聘要求写进 JD 之前，
   * 强度可以由论文与新闻推出来，但它还不是一条已确认的要求。
   */
  confirmedAt?: Record<string, string>;
  /** 从这个月起（含）为外推，之前为实测。不填表示整段都是实测 */
  forecastFrom?: string;
  /** 这份序列的来源等级 —— synthetic 时界面必须挂演示数据标 */
  provenance: Provenance;
}

/** 图谱版本 */
export interface GraphVersion {
  version: string;
  date: string;
  label: string;
  stats: {
    jobs: number;
    tasks: number;
    skills: number;
    skillPoints: number;
    edges: number;
    overlayEdges: number;
  };
}

export type ChangeOp = 'add' | 'remove' | 'modify' | 'merge';

/** 变更事件 —— 图谱动态演化的最小记录单元 */
export interface ChangeEvent {
  id: string;
  version: string;
  date: string;
  op: ChangeOp;
  /** 变更发生在哪个岗位下 */
  jobId: string;
  target: { kind: NodeKind; id: string; name: string };
  field?: string;
  before?: number;
  after?: number;
  /** 更新说明：这条变更为什么发生 */
  reason: string;
  /** 支撑该变更的数据源 */
  sources: EvidenceRef[];
  reviewState: 'auto' | 'approved' | 'rejected' | 'edited';
  /** merge 专用：冗余判定三项得分 */
  mergeScores?: { nameCosine: number; outJaccard: number; inJaccard: number };
  mergedFrom?: string;
}

/** 岗位在某一版本的能力构成快照 —— 能力年轮的数据源 */
export interface AnnulusRing {
  version: string;
  date: string;
  /** 该版本下该岗位的能力占比（归一化到 1） */
  slices: {
    skillId: string;
    name: string;
    share: number;
    status: EdgeStatus;
    origin: 'base' | 'overlay';
    /** 所属能力组名。年轮按组分色，同组的技能共用一个色相 */
    group?: string;
    /** 所属能力组的编码（T-SW / F-2 …）。配色按它索引，不按组名 */
    groupCode?: string;
  }[];
  /** 是否为越过"今日线"的预测环 */
  predicted?: boolean;
}

export interface JobAnnuli {
  jobId: string;
  jobName: string;
  rings: AnnulusRing[];
  changes: ChangeEvent[];
}

/* ==================== 人岗匹配 ==================== */

/** 简历原文的一行。有 id 才能做“点右边的分析 → 左边原文高亮”这条联动 */
export interface ResumeLine {
  id: string;
  text: string;
}

export interface ResumeSection {
  id: string;
  title: string;
  lines: ResumeLine[];
}

/** 一段可核验的经历：项目 / 工作 / 竞赛 / 科研 */
export interface ResumeExperience {
  id: string;
  title: string;
  org: string;
  period: string;
  /** 起止时间跨度（月），用于时间线一致性核验 */
  months: number;
  kind: 'work' | 'project' | 'competition' | 'research';
  bullets: string[];
  /** 这段经历在简历原文里自述用到的技能点名 */
  claims: string[];
  /** 对应的原文行 id */
  lines: string[];
}

export interface ResumeSkillPoint {
  id: string;
  /** 简历原文里的写法，如 “PyTorch” */
  name: string;
  /** 归并到的技能点规范名。原文写法与能力体系一致时不带这一项 */
  mappedName?: string;
  /** 熟练度 0–1 */
  proficiency: number;
  /** 抽取来源片段 */
  evidence: string;
  /** 抽取置信度，用于人工校正提示 */
  confidence: number;
  /** 抽取自哪几行原文 —— 空数组意味着这一项在简历里找不到落点 */
  anchors: string[];
  /** list = 只出现在技能清单里；experience = 有经历描述支撑。真实性核验的主要依据 */
  from: 'list' | 'experience';
}

export interface ResumeProfile {
  name: string;
  years: number;
  degree: string;
  city: string;
  /** 结构化的简历原文，报告页左栏逐行渲染并支持高亮 */
  sections: ResumeSection[];
  experiences: ResumeExperience[];
  skillPoints: ResumeSkillPoint[];
  /** 沿 S-SP 边反向映射得到的 Skill 层向量 */
  skillVector: Record<string, number>;
}

export interface MatchItem {
  skillId: string;
  name: string;
  /** 岗位要求的 effective_weight */
  required: number;
  /** 路径一：J→S 直达贡献 */
  directPart: number;
  /** 路径二：J→T→S 两跳贡献 */
  viaTaskPart: number;
  /** 两跳贡献的任务级明细 */
  viaTasks: { taskId: string; taskName: string; part: number }[];
  /** 简历侧熟练度 */
  owned: number;
  gap: number;
  band: 'have' | 'improve' | 'missing';
  /** 该项要求中含叠层贡献（前瞻能力） */
  forwardLooking: boolean;
  /** 支撑这项要求的证据构成 → 可信度指纹 */
  mix: { jd: number; paper: number; news: number };
  /** 各条支撑边按贡献量加权后的置信度 */
  confidence: number;
}

export interface LearningStage {
  stage: number;
  title: string;
  weeks: number;
  skills: { name: string; forwardLooking: boolean; gap: number }[];
  resources: { title: string; type: string }[];
}

/** 岗位的一项核心任务，以及简历对它的覆盖情况（P→T→S 的中间层） */
export interface MatchTask {
  taskId: string;
  taskName: string;
  /** 该任务在岗位中的权重 */
  weight: number;
  /** 0–1：该任务所需能力的加权平均掌握程度 */
  coverage: number;
  /** 拖累该任务覆盖度的能力（按缺口从大到小，最多 3 项） */
  weakest: string[];
}

/** 一条针对性改进建议 */
export interface MatchAdvice {
  kind: 'gap' | 'forward' | 'dimension' | 'strength';
  title: string;
  body: string;
}

export interface MatchResult {
  jobId: string;
  jobName: string;
  /** 五个维度按 DIM_WEIGHTS 加权合成的综合匹配度 */
  score: number;
  dims: { skill: number; task: number; experience: number; degree: number; domain: number };
  items: MatchItem[];
  tasks: MatchTask[];
  path: LearningStage[];
  advice: MatchAdvice[];
}

/* ==================== Loop 与质量 ==================== */

export interface AgentRun {
  name: string;
  role: string;
  status: 'done' | 'running' | 'idle';
  durationMs: number;
  input: string;
  output: string;
  metric: string;
}

export interface LoopRun {
  id: string;
  version: string;
  startedAt: string;
  batch: { jd: number; paper: number; news: number };
  agents: AgentRun[];
  deltas: {
    nodesAdded: number;
    edgesAdded: number;
    edgesStrengthened: number;
    edgesWeakened: number;
    edgesRemoved: number;
    overlayApplied: number;
  };
}

export interface QualityMetrics {
  jdParseAccuracy: number;
  resumeExtractAccuracy: number;
  matchAccuracy: number;
  testSetSize: number;
  dedupRate: number;
  noiseFilterRate: number;
  hallucinationBlocked: number;
  crossValidatedRatio: number;
  /** 叠层命中率：历史上检测到的前瞻信号，最终被 JD 确认的比例 */
  foresightHitRate: number;
  lastEvaluatedAt: string;
}

/** 抄袭检测：一个相似 JD 簇 */
export interface PlagiarismCluster {
  id: string;
  canonicalDocId: string;
  canonicalTitle: string;
  company: string;
  publishedAt: string;
  similarity: number;
  members: { docId: string; title: string; company: string; publishedAt: string; sim: number }[];
  /** 结构指纹（节点有序序列哈希） */
  fingerprint: string;
}

/** 噪声样板话术 */
export interface NoisePhrase {
  phrase: string;
  docFreq: number;
  tfidf: number;
  action: 'ignored' | 'downweighted';
}

/** 冗余节点合并候选（Quality Guardian） */
export interface MergeCandidate {
  id: string;
  kind: NodeKind;
  a: { id: string; name: string };
  b: { id: string; name: string };
  nameCosine: number;
  outJaccard: number;
  inJaccard: number;
  verdict: 'pending' | 'merged' | 'kept';
  llmNote: string;
}

/** 幻觉拦截记录 */
export interface HallucinationBlock {
  id: string;
  claim: string;
  reason: string;
  detectedAt: string;
  stage: 'extract' | 'cross-validate' | 'graph-commit';
}

/** 整体数据集 */
export interface Dataset {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /**
   * 叠层新岗位的推导关联边（J-T 与 J-S）。
   *
   * 算法侧未产出这一层，由构建阶段从证据句里的结构化信号与文本锚点相似度
   * 推得（data-pipeline/jobvec.mjs），逐条标 provenance: 'derived'。
   *
   * 与 edges 分列而不并入：那四类是实测，并进去会让全站每一张读 J-T 或 J-S 的
   * 图无声地多出几条推来的连线。取用它的图各自取、各自交代口径。
   */
  inferredEdges: GraphEdge[];
  signals: EntitySignal[];
  /** 能力棱镜的月度切片 */
  prismTimeline: PrismTimeline;
  versions: GraphVersion[];
  changes: ChangeEvent[];
  loops: LoopRun[];
  quality: QualityMetrics;
  plagiarism: PlagiarismCluster[];
  noise: NoisePhrase[];
  merges: MergeCandidate[];
  hallucinations: HallucinationBlock[];
  resumes: ResumeProfile[];
}
