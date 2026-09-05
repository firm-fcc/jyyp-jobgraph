/* ============================================================
   算法侧图谱产物的加载层

   读 public/data/ 下由 data-pipeline/build.mjs 生成的六个文件，
   转换过程与口径见 data-pipeline/README.md。

   这一层只做加载与类型标注，不做任何加工：加工在 realGraph.ts。
   两者分开是为了让"哪些数是算法侧给的"这个问题有一处确定的出处，
   凡在本文件出现的字段一律为实测，凡不在的一律不是。

   加载采用顶层 await：数据在模块图求值期间取回，下游各页因而仍以同步方式
   读数，无须逐页引入加载态。本模块因此只被系统内各页的异步分块引用 ——
   封面页只读 `manifest.ts` 那一份小清单，进入系统之前不等这十余兆产物；
   预取在封面挂载时即已发起，见 App.tsx。
   ============================================================ */

/* ---------------- 产物结构 ---------------- */

export interface GraphManifest {
  schema: string;
  generatedAt: string;
  source: { repo: string; package: string; fingerprint: string; createdBy: string };
  taxonomy: { jobs: string; tasks: string; skills: string };
  windows: string[];
  latest: string;
  deltaWindows: string[];
  counts: {
    jobs: number;
    jobsInTaxonomy: number;
    tasks: number;
    skills: number;
    skillpoints: number;
    /** 叠层条目按层分开的计数。封面页只读清单即可出数 */
    overlay: { jobs: number; tasks: number; skills: number; skillpoints: number };
    edges: Record<string, number>;
    deltaEntities: number;
    jdSampled: number;
    jdSummaryRows: number;
    /** 原文表的总行数与其中连接上汇总表的条数 */
    jdRawRows: number;
    jdRawMatched: number;
    cities: number;
    companies: number;
    /** 有档位分布的技能点数，即样本量过阈值的那一部分 */
    skillpointProf: number;
  };
  absent: string[];
  dropped: Record<string, number>;
  seriesSkillpointTop: number;
  /** 职级列的非空比例，按职级分档的能力要求只统计有职级的那一部分 */
  levelCoverage: number;
  /** 数据包内有、但未接入的窗口，逐条注明原因 */
  windowsExcluded: { window: string; reason: string }[];
  /** 窗口序列的断档。时间轴按窗口序号等距排布，此处记明实际的月份跳跃 */
  windowGaps: { after: string; before: string; months: number }[];
}

/** 一条边。w 基图权重，e 合成权重，g 前瞻缺口，o 仅合成层新增边才有 */
export interface RawEdge {
  s: string;
  t: string;
  w: number;
  e: number;
  g: number;
  o?: string;
}

/** 叠层节点独有的三项：入场窗口、信号强度、定义 */
export interface OverlayFields {
  /** 该信号首次被论文或新闻提出的窗口，与基图条目的首现口径不同 */
  born?: string;
  strength?: number;
  def?: string;
}

export interface RawJobNode extends OverlayFields {
  id: string;
  name: string;
  cat: string;
  catCode: string;
  /** 招聘信息条数，岗位层唯一的实测计量 */
  hits: number;
  /** 本窗加权出现量 */
  w: number;
  origin: 'base' | 'overlay';
}

export interface RawTaskNode extends OverlayFields {
  id: string;
  name: string;
  /** 该任务在全部岗位需求中的加权占比 */
  share: number;
  origin: 'base' | 'overlay';
}

export interface RawSkillNode extends OverlayFields {
  id: string;
  name: string;
  group: string;
  groupCode: string;
  dim: string;
  dimCode: string;
  type: 'hard' | 'soft';
  def: string;
  share: number;
  origin: 'base' | 'overlay';
}

export interface RawSkillPointNode {
  id: string;
  /** 基图技能点为招聘统计权重，叠层技能点为信号强度，两者不同量纲 */
  w: number;
  /** 首次进入图谱的窗口 */
  from: string;
  /** 出现过的窗口数 */
  n: number;
  origin: 'base' | 'overlay';
  /** 叠层技能点带定义，基图技能点无 */
  def?: string;
}

/**
 * 叠层新岗位的任务与技能向量（推导，非实测）。
 *
 * 算法侧未产出这一层关联：delta/job_links.json 四十四个叠层窗合计 299 条、
 * 末窗 1 条，且落在 delta 层不进 effective 的 J-T 表。岗位空间关系图按任务
 * 构成算距离，零向量与任一岗位的余弦距离恒等于 1，这些点因而读不出落位。
 *
 * 本节由构建脚本（data-pipeline/jobvec.mjs）从两处已有字段推得：一是 JD 类
 * 证据句里算法侧已抽出、但没有写成边的技能与任务名，二是定义与证据句同任务、
 * 技能锚点文本的相似度。前者是读数，后者是推断，via 逐条注明是哪一种。
 *
 * 与 edges 分列而不并入：四类边是实测，这一份不是，并进去会让口径失真。
 */
export interface InferredLinks {
  /** 算法标识，与 jobvec.mjs 的实现对应 */
  method: string;
  params: Record<string, number>;
  /** 推得的岗位—任务边。w 已按该岗位最高项归一到 1 */
  jobTask: { s: string; t: string; w: number }[];
  jobSkill: { s: string; t: string; w: number }[];
  jobs: Record<
    string,
    {
      /** 定得住才有向量。定不住的仍不给相近岗位清单 */
      anchored: boolean;
      /** signal = 从招聘原文抽出的读数，text = 文本相似度推断 */
      via: 'signal' | 'text';
      /** 解析到结构化信号的证据条数 */
      sigLines: number;
      sigTasks: number;
      sigSkills: number;
      /** 文本锚点的最高相似度 */
      topSim: number;
      nTasks: number;
      nSkills: number;
    }
  >;
}

export interface GraphDoc {
  window: string;
  fingerprint: string;
  alpha: number;
  totalWeight: number;
  nodes: {
    jobs: RawJobNode[];
    tasks: RawTaskNode[];
    skills: RawSkillNode[];
    skillpoints: RawSkillPointNode[];
  };
  edges: {
    jobTask: RawEdge[];
    jobSkill: RawEdge[];
    taskSkill: RawEdge[];
    skillSkillpoint: RawEdge[];
  };
  /** 叠层新岗位没有关联边时为 null */
  inferred: InferredLinks | null;
}

export interface WindowCounts {
  jdScanned: number;
  jdSampled: number;
  jobs: number;
  tasks: number;
  skills: number;
  skillpoints: number;
  edges: Record<string, number>;
  totalWeight: number;
  droppedNearDup: number;
  droppedNonIt: number;
}

export interface SeriesDoc {
  months: string[];
  /** 键为节点 id，长度与 months 对齐；null 表示当月该条目不在图谱内 */
  jobs: Record<string, (number | null)[]>;
  tasks: Record<string, (number | null)[]>;
  skills: Record<string, (number | null)[]>;
  skillpoints: Record<string, (number | null)[]>;
  counts: Record<string, WindowCounts>;
}

/** 一个岗位在某窗口的构成，条目为 [编码, 归一化份额] */
export interface RingWindow {
  skills: [string, number][];
  tasks: [string, number][];
}

export interface RingsDoc {
  months: string[];
  /** 岗位编码 → 窗口 → 该窗构成 */
  jobs: Record<string, Record<string, RingWindow>>;
}

export interface DeltaEvidence {
  doc: string;
  date: string;
  src: string;
  tier: string;
  conf: string;
  lines: string[];
}

/** 一条叠层新实体的静态记录。逐窗只变强度与来源，故这些字段按实体存一份 */
export interface DeltaItem {
  id: string;
  kind: 'job' | 'task' | 'skill' | 'skillpoint';
  name: string;
  nameEn: string;
  def: string;
  born: string;
  firstSeen: string;
  lastSeen: string;
  /** 算法侧给出的关联落点，落在基准体系的编码上；多数条目为空 */
  relTasks: string[];
  relSkills: string[];
  evidence: DeltaEvidence[];
  nEvidence: number;
}

export interface DeltaStrengthening {
  taxonomy: string;
  code: string;
  name: string;
  firstSeen: string;
  lastSeen: string;
  evidence: DeltaEvidence[];
  nEvidence: number;
}

/** 逐窗的一条引用：[实体键, 该窗强度, 该窗来源（以 + 分隔）] */
export type DeltaRef = [string, number, string];

export interface DeltaLink {
  s: string;
  sName: string;
  t: string;
  tName: string;
  rel: string;
  w: number;
}

export interface DeltaDoc {
  windows: string[];
  /** 只建基图不建叠层的基准窗 */
  baselineWindows: string[];
  /** 键为 `${kind}|${id}`，与 byWindow.items 的第一元对应 */
  entities: Record<string, DeltaItem>;
  /** 键为 `${taxonomy}|${code}`，与 byWindow.strengthenings 的第一元对应 */
  strengthenDefs: Record<string, DeltaStrengthening>;
  byWindow: Record<
    string,
    { items: DeltaRef[]; strengthenings: DeltaRef[]; links: DeltaLink[] }
  >;
  newEntities: {
    id: string;
    kind: string;
    name: string;
    strength: number;
    status: string;
    participates: boolean;
    links: number;
  }[];
  graduated: number;
  /**
   * arXiv 编号 → 论文标题。
   *
   * 证据表本身不带标题字段，标题由构建阶段自证据句中以"标题："起头的那一条取得，
   * 其余录于 data-pipeline/paper-titles.json。表内只收产物实际引用到的篇目，
   * 缺标题的编号不入表 —— 界面据键在不在表内决定显示标题还是编号。
   */
  paperTitles: Record<string, string>;
}

export interface ProfDoc {
  months: string[];
  rubric: string;
  byWindow: Record<
    string,
    {
      nJds: number;
      skills: Record<string, { name: string; n: number; levels: Record<string, number> }>;
    }
  >;
}

export interface JdJobStat {
  n: number;
  byWindow: Record<string, number>;
  salaryBands: Record<string, number>;
  levels: Record<string, number>;
  stacks: Record<string, number>;
  salaryN: number;
  medianSalary: number;
  avgSkills: number;
  avgTasks: number;
  avgSkillpoints: number;
  /** 职级 → 该档下各技能被要求的条数，每档保留权重前二十项 */
  levelSkills: Record<string, Record<string, number>>;
  /** 职级 → 该档的招聘信息条数，作上一项的分母 */
  levelN: Record<string, number>;
  /** 技能编码 → 五档计数，即该岗位对该技能要求到什么程度 */
  skillProf: Record<string, Record<string, number>>;
}

export interface JdStatsDoc {
  months: string[];
  byJob: Record<string, JdJobStat>;
  overall: {
    n: number;
    salaryBands: Record<string, number>;
    levels: Record<string, number>;
    stacks: Record<string, number>;
    medianSalary: number;
  };
  /** 档序，与下一项的数组下标对齐 */
  profBands: string[];
  /** 技能点 → 五档计数。样本量不足二十条的技能点不入表 */
  skillpointProf: Record<string, number[]>;
  /** 全样本的职级 × 技能，不按岗位切也不截尾。
      n 为各职级的条目总数，skills 为该职级中提及各技能的条数 */
  levels: {
    n: Record<string, number>;
    skills: Record<string, Record<string, number>>;
  };
}

/* ---------------- 招聘原文的聚合 ---------------- */

/** 一条招聘原文的摘录 */
export interface JdSample {
  id: string;
  /** 所属窗口 */
  w: string;
  title: string;
  company: string;
  city: string;
  salary: string;
  date: string;
  /** 截断后的正文 */
  text: string;
  /** 截断前的字数 */
  full: number;
}

/**
 * 一项能力要求的句级归因。
 *
 * 汇总表逐条给出该条招聘信息要求的技能与各技能下命中的技能点，原文表给出
 * 正文全文，两者按 jobid 连接后由技能点名在正文中的落点反查所在句 —— 该句
 * 即这一项能力要求的支撑句。全批 749,803 个（条，能力项）对中定位到 88.9%。
 */
export interface JdAttribution {
  /** 支撑该项的招聘信息条数 */
  n: number;
  /** 提出过该项的企业数与城市数。清单截断，这两个计数不截断 */
  nCompanies: number;
  nCities: number;
  /**
   * 两维切分，各前八项，格式为 [键, 支撑条数, 该键下该岗位的总条数]。
   *
   * 分母随桶给出而不让下游另查：下游能查到的企业清单是产出时截过尾的那一份，
   * 两份清单的交集之外没有分母可用，跨条件复现的企业一行会整行画不出来。
   */
  byCompany: [string, number, number][];
  byCity: [string, number, number][];
  /** 支撑句，按企业去重后取前三条 */
  quotes: {
    id: string;
    w: string;
    company: string;
    city: string;
    date: string;
    /** 该句里命中的技能点名 */
    points: string[];
    text: string;
  }[];
}

export interface JdRawJob {
  n: number;
  /** 城市 → 条数，保留前十五项 */
  cities: Record<string, number>;
  /** 前十五项之外的合计，作份额的分母时须计入 */
  cityOther: number;
  nCities: number;
  /** [企业名, 条数]，前十项 */
  companies: [string, number][];
  nCompanies: number;
  /** 学历 → 条数。原文表该列整批为空，值由正文的门槛语抽出，见 jdraw.mjs */
  degrees: Record<string, number>;
  /** 学历的分母：正文里谈到学历的条数。全部条数里有一部分通篇不提学历 */
  degreeN: number;
  samples: JdSample[];
  /** 技能名 → 该岗位在这一项上的归因。按支撑条数取前十四项 */
  attrib: Record<string, JdAttribution>;
  /** 归因覆盖到的能力项数，不受上一项的截断影响 */
  nAttrib: number;
}

export interface JdRawDoc {
  schema: string;
  windows: string[];
  perWindow: Record<
    string,
    { rows: number; matched: number; degreeFilled: number; attribHit: number }
  >;
  overall: {
    rows: number;
    matched: number;
    cities: Record<string, number>;
    nCities: number;
    companies: [string, number][];
    nCompanies: number;
    degrees: Record<string, number>;
    degreeN: number;
    /** 归因的（条，能力项）对总数与其中定位到支撑句的对数 */
    attribPairs: number;
    attribHit: number;
  };
  byJob: Record<string, JdRawJob>;
  sampleLimits: { perJob: number; snippet: number };
  attribLimits: { topSkills: number; quotes: number; facets: number; quoteLen: number };
}

import type { JobDefinition } from '@/types/graph';

/* ---------------- 加载 ---------------- */

const BASE = import.meta.env.BASE_URL || '/';

async function load<T>(name: string): Promise<T> {
  const r = await fetch(`${BASE}data/${name}`);
  if (!r.ok) throw new Error(`图谱数据加载失败：${name} ${r.status}`);
  return r.json() as Promise<T>;
}

/** 招聘原文的聚合缺席时不阻断加载：它是构建脚本的可选输入，
    产物不在时相应维度退回演示补齐层，与接入前的形态一致 */
async function loadOptional<T>(name: string): Promise<T | null> {
  try {
    const r = await fetch(`${BASE}data/${name}`);
    return r.ok ? ((await r.json()) as T) : null;
  } catch {
    return null;
  }
}

/** 技能点一层单独成份，见 build.mjs 的写出一节 */
interface GraphSpDoc {
  window: string;
  nodes: RawSkillPointNode[];
  edges: RawEdge[];
}

/** 叠层的逐条证据原文单独成份，键与 delta.json 的两张实体表一一对应 */
interface DeltaEvidenceDoc {
  entities: Record<string, DeltaEvidence[]>;
  strengthenDefs: Record<string, DeltaEvidence[]>;
  paperTitles: Record<string, string>;
}

/**
 * 下一季度的能力构成预测，见 build.mjs 的 6.6 节。
 *
 * 逐条为 [技能编码, 单变量预测, 协变量预测, 前瞻信号, 上季实测份额, 历史季度数]。
 * 前瞻信号为两次预测之差：为正即把论文与新闻的情报占比作为协变量算进来之后，
 * 该项的下季占比被上调。
 */
export interface ForecastDoc {
  quarter: string;
  method: string;
  fields: string[];
  jobs: Record<string, [string, number, number, number, number, number][]>;
}

/** 岗位定义三要素，见 build.mjs 的 6.5 节 */
export interface JobDefDoc {
  window: string;
  method: string;
  params: Record<string, number>;
  jobs: Record<string, JobDefinition>;
}

const [graphTop, graphSp, series, rings, deltaCore, deltaEv, prof, jdstats, jobdef, forecast, jdraw] =
  await Promise.all([
    load<Omit<GraphDoc, 'nodes' | 'edges'> & {
      nodes: Omit<GraphDoc['nodes'], 'skillpoints'>;
      edges: Omit<GraphDoc['edges'], 'skillSkillpoint'>;
    }>('graph.json'),
    load<GraphSpDoc>('graph-skillpoints.json'),
    load<SeriesDoc>('series.json'),
    load<RingsDoc>('rings.json'),
    load<Omit<DeltaDoc, 'paperTitles'>>('delta.json'),
    load<DeltaEvidenceDoc>('delta-evidence.json'),
    load<ProfDoc>('prof.json'),
    load<JdStatsDoc>('jdstats.json'),
    load<JobDefDoc>('jobdef.json'),
    loadOptional<ForecastDoc>('forecast.json'),
    loadOptional<JdRawDoc>('jdraw.json'),
  ]);

/* 拆分只在传输一侧：两对文件在此并回一份，下游读到的形状与拆分前一致，
   凡引用 GRAPH / DELTA 的代码无须知道产物分了几份。 */
const graph: GraphDoc = {
  ...graphTop,
  nodes: { ...graphTop.nodes, skillpoints: graphSp.nodes },
  edges: { ...graphTop.edges, skillSkillpoint: graphSp.edges },
};
for (const [k, ev] of Object.entries(deltaEv.entities)) {
  const e = deltaCore.entities[k];
  if (e) e.evidence = ev;
}
for (const [k, ev] of Object.entries(deltaEv.strengthenDefs)) {
  const e = deltaCore.strengthenDefs[k];
  if (e) e.evidence = ev;
}
/* 证据缺席的条目也要有一个空数组：下游一律直接遍历，不逐处判空 */
for (const e of Object.values(deltaCore.entities)) if (!e.evidence) e.evidence = [];
for (const e of Object.values(deltaCore.strengthenDefs)) if (!e.evidence) e.evidence = [];
const delta: DeltaDoc = { ...deltaCore, paperTitles: deltaEv.paperTitles };

export { MANIFEST, WINDOWS, LATEST_WINDOW } from './manifest';
export const GRAPH = graph;
export const SERIES = series;
export const RINGS = rings;
export const DELTA = delta;
export const PROF = prof;
export const JDSTATS = jdstats;
export const JOBDEF = jobdef;
export const FORECAST = forecast;
export const JDRAW = jdraw;
