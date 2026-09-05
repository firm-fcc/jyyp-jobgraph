/* ============================================================
   真实图谱加工层

   把 graphData.ts 加载的算法侧产物，加工成 types/graph.ts 里的契约形状。
   本文件只做形状转换与可直接由源数据算出的派生量，不补任何源数据没有的值：
   源数据缺的维度（学历、企业类别、证据原文全文）留给演示补齐层，
   并在 provenance.ts 里如实登记。城市、企业与逐条原文摘录本批已产出，
   经 data-pipeline/jdraw.mjs 聚合后由本文件读入。

   ------------------------------------------------------------
   四层的口径

   算法侧的四层是 岗位 → 任务 → 技能 → 技能点，其中技能 50 项为封闭体系、
   技能点逾万项为开放集合。前端此前因缺技能点数据，把技能体系的能力组
   当作第三层、技能当作第四层；真实数据接入后按算法侧口径归位，
   能力组降为技能的一级归属，与岗位的 9 个类别同性质。

   ------------------------------------------------------------
   时间口径

   本批数据覆盖 2022-05 至 2026-04，共四十六个观测窗口。窗口的量逐月变化，
   四层结构本身不变，故序列的形状是逐月的量而非逐月的一整张图。
   前两窗为基准窗，只建基图不建叠层。

   窗口序列不连续：2022-12 与 2023-01 两个月算法侧只给了 2022-11 的结转基图，
   不是独立观测，故不接入（见 manifest.windowsExcluded 与 windowGaps）。
   下游凡按下标取数的一律以窗口序为轴，不按自然月铺开。
   ============================================================ */

import type {
  AnnulusRing,
  ChangeEvent,
  EdgeStatus,
  EntitySignal,
  EvidenceRef,
  GraphEdge,
  GraphNode,
  GraphVersion,
  JobAnnuli,
  LoopRun,
  NodeKind,
  PrismTimeline,
} from '@/types/graph';
import type { SkillPointSeed, SkillSeed, TaskSeed } from './taxonomy';
import type { DeltaEvidence, DeltaItem } from './graphData';
import { DELTA, FORECAST, GRAPH, JDRAW, JDSTATS, JOBDEF, MANIFEST, PROF, RINGS, SERIES, WINDOWS } from './graphData';
import { PROVINCES_ALL, PROVINCE_OTHER, isNonGeo, provinceOf } from './provinces';
import jobsDoc from './real/jobs_v2.json';
import { addMonths, monthDiff } from '@/utils/format';

/* 岗位体系文件里逐岗位的释义部分。图谱产物按岗位码索引，与体系文件同码，
   故两份在此按码相接：产物给的是市场读数（条数、权重、序列），
   体系文件给的是这个岗位是什么（定义、判定关键词、与相近岗位的边界、
   平台职能名）。后四项此前没有接进来，岗位详情栏里因而恒为空。 */
const JOB_TAXONOMY = jobsDoc.detail as unknown as Record<
  string,
  {
    definition?: string;
    keywords?: string[];
    boundary?: string;
    funtypes?: string[];
  }
>;

/* ==================== 时间轴 ==================== */

/** 观测窗口，升序。这是本批数据的实测段 */
export const REAL_MONTHS = WINDOWS;
/** 末窗，图谱结构以它为准 */
export const REAL_NOW = MANIFEST.latest;
/** 只建基图不建叠层的基准窗 */
export const BASELINE_WINDOWS = DELTA.baselineWindows;

const LAST = REAL_MONTHS.length - 1;

/** 端点校验剔除的关系条数：端点未落在对应体系内的边，构建阶段整条剔除 */
export const DROPPED_EDGES = Object.values(MANIFEST.dropped ?? {}).reduce((a, b) => a + b, 0);

/* ==================== 节点 ==================== */

const jid = (id: string) => `J:${id}`;
const tid = (id: string) => `T:${id}`;
const sid = (id: string) => `S:${id}`;
const spid = (name: string) => `SP:${name}`;

/** 叠层实体落到哪个节点 id 上。信号序列与证据表两处必须同源，故只此一处 */
const entityIdOf = (it: DeltaItem) =>
  it.kind === 'job'
    ? jid(it.id)
    : it.kind === 'task'
      ? tid(it.id)
      : it.kind === 'skill'
        ? sid(it.id)
        : spid(it.name);

/** 技能编码 → 节点，供边与画像回查 */
export const SKILL_BY_CODE = new Map(GRAPH.nodes.skills.map((s) => [s.id, s]));
export const TASK_BY_CODE = new Map(GRAPH.nodes.tasks.map((t) => [t.id, t]));
export const JOB_BY_CODE = new Map(GRAPH.nodes.jobs.map((j) => [j.id, j]));

/** 基图技能点的权重合计，用于份额归一。叠层技能点的强度不同量纲，不计入 */
const SP_TOTAL =
  GRAPH.nodes.skillpoints.reduce((a, s) => (s.origin === 'overlay' ? a : a + s.w), 0) || 1;

/**
 * 序列取值。窗口内无该条目时为 null，与取值为零区别对待：
 * null 表示当月该条目不在图谱里，零表示当月测得为零。
 */
function seriesOf(bag: Record<string, (number | null)[]>, id: string): (number | null)[] | undefined {
  return bag[id];
}

/** 一条序列里首个非空月份，即该条目进入图谱的窗口 */
function firstMonth(arr: (number | null)[] | undefined): string {
  if (!arr) return REAL_MONTHS[0];
  const i = arr.findIndex((v) => v !== null && v > 0);
  return i >= 0 ? REAL_MONTHS[i] : REAL_MONTHS[0];
}

/** 末窗与前一窗之比，用于判定条目当前处在增强还是减弱 */
function trendOf(arr: (number | null)[] | undefined): EdgeStatus {
  if (!arr) return 'active';
  const a = arr[LAST];
  const b = arr[Math.max(0, LAST - 1)];
  if (a === null || b === null || b <= 0) return 'active';
  const r = a / b;
  return r > 1.03 ? 'strengthening' : r < 0.97 ? 'weakening' : 'active';
}

/** 招聘信息汇总里该岗位的统计，按岗位规范名匹配 */
function jdStatOf(name: string) {
  return JDSTATS.byJob[name];
}

/** 招聘原文里该岗位的城市、企业与摘录，同样按规范名匹配 */
function jdRawOf(name: string) {
  return JDRAW?.byJob[name];
}

/* ---------------- 省份轴 ----------------

   招聘原文的 place 列取到市一级，全批 391 座城市。城市这一维在属性栏上铺不开：
   一栏至多列二十余行，而尾部大量是个位数的条目，读不出差别。按省汇总后落到
   34 个省级行政区，既列得下，也与“在哪儿招人”这一问的既有认知一致。
   对照表见 data/provinces.ts —— 那是行政区划本身，不是从数据里推断出来的量。

   轴上列出全部 34 个省级行政区（含港澳台），条数为零的照列不误：
   零本身是一条读数，省掉它读者无从知道这一维是“没有”还是“没画”。
   place 列里另有“远程办公”“国外”一类取值，它们不落在任何省级行政区内，
   不进这一维（见 provinces.isNonGeo），各省条数之和因而小于岗位的招聘条数。
   末档“其他”收对照表未登记的城市，本批为空，为空时不上轴 —— 一个既无条数、
   又说不出收的是什么的档，列出来只会让人以为省份归类漏了一批。 */

export const PROVINCE_AXIS: string[] = [...PROVINCES_ALL, PROVINCE_OTHER];

/** 该岗位逐座城市的招聘条数。城市级勾选与省份汇总都读它 */
export function cityCountsOf(name: string): Record<string, number> {
  return jdRawOf(name)?.cities ?? {};
}

/** 全样本逐座城市的招聘条数，供城市勾选菜单按条数排序 */
export const CITY_COUNTS: Record<string, number> = JDRAW?.overall.cities ?? {};

/* ---------------- 职级 × 能力 ----------------

   汇总表逐条带一个职级与一组能力要求，按职级切开即得各档的能力构成：
   某档下提及某能力组的条数除以该档的条目总数，即该档对这一组的提及率；
   再除以全样本的提及率，得一个以 1 为基准的倍数，可跨组比较。

   汇总表的职级分五档，界面分三档，按年限区间并档。职级列非空比例见
   manifest.levelCoverage，无值的条目不进入本维。 */

const LEVEL_BAND_OF: Record<string, 0 | 1 | 2> = {
  '实习/应届': 0,
  '初级(0-2年)': 0,
  '中级(3-4年)': 1,
  '高级(5-9年)': 2,
  '专家(10年+)': 2,
};

/** 一个能力组进入职级系数所需的最小提及量。样本过小的组倍数会被个别岗位带偏 */
const TILT_MIN_MENTIONS = 500;

/** 能力组 → [初级, 中级, 高级] 的提及率倍数。招聘数据缺席时为 null */
export const LEVEL_TILT_MEASURED: Record<string, [number, number, number]> | null = (() => {
  const lv = JDSTATS.levels;
  if (!lv || !Object.keys(lv.n).length) return null;
  const groupOf = new Map(GRAPH.nodes.skills.map((s) => [s.id, s.group]));
  const n: [number, number, number] = [0, 0, 0];
  const cnt = new Map<string, [number, number, number]>();
  for (const [band, v] of Object.entries(lv.n)) {
    const i = LEVEL_BAND_OF[band];
    if (i !== undefined) n[i] += v;
  }
  for (const [band, bag] of Object.entries(lv.skills)) {
    const i = LEVEL_BAND_OF[band];
    if (i === undefined) continue;
    for (const [code, c] of Object.entries(bag)) {
      const g = groupOf.get(code);
      if (!g) continue;
      const row = cnt.get(g) ?? [0, 0, 0];
      row[i] += c;
      cnt.set(g, row);
    }
  }
  const total = n[0] + n[1] + n[2];
  if (!total) return null;
  const out: Record<string, [number, number, number]> = {};
  for (const [g, row] of cnt) {
    const mentions = row[0] + row[1] + row[2];
    if (mentions < TILT_MIN_MENTIONS) continue;
    const base = mentions / total;
    out[g] = [0, 1, 2].map((i) =>
      n[i] ? Math.round((row[i] / n[i] / base) * 1e4) / 1e4 : 1,
    ) as [number, number, number];
  }
  return Object.keys(out).length ? out : null;
})();

/** 职级维的覆盖情况，界面上交代口径时读它 */
export const LEVEL_TILT_COVERAGE = {
  /** 职级列非空的条目占比 */
  share: MANIFEST.levelCoverage ?? 0,
  /** 进入系数的能力组数 */
  groups: LEVEL_TILT_MEASURED ? Object.keys(LEVEL_TILT_MEASURED).length : 0,
  n: Object.values(JDSTATS.levels?.n ?? {}).reduce((a, b) => a + b, 0),
};

/**
 * 该岗位的招聘原文侧记录：城市、企业与学历的分布，逐条摘录，以及能力要求的
 * 句级归因。
 *
 * 摘录每岗三条、每条截断至 420 字；归因每岗取支撑条数最多的十四项能力，
 * 各留三条按企业去重的支撑句与两维切分（见 data-pipeline/jdraw.mjs）。
 */
export function jobRawSource(name: string) {
  return jdRawOf(name);
}

/* ---------------- 学历 ----------------

   原文表的 degree 一列四十六窗五百七十九万行里绝大多数为空（本批起该列开始
   有值，占连接条数的一成上下），汇总表亦无此列。算法侧的职级（level）本就是
   从正文抽出的（level_source 记作 text），学历沿同一路径在构建阶段抽出：
   列里有值时以列为准，为空时由正文抽，两者一律归到同一条六档轴上；
   正文写明门槛语时取最低的一档
   （“大专及以上学历，本科优先”的门槛是大专），通篇不提学历的条目不进入本维。

   因此这一维与省份、经验、薪资三项不同：它不是算法侧直接给的读数，
   而是由正文推得的一层，界面上按 derived 交代，不再标演示数据。 */

/** 学历轴。由低到高，末档“学历不限”是一条独立读数，不是最低的一级 */
export const DEGREE_AXIS = ['高中及中专', '大专', '本科', '硕士', '博士', '学历不限'];

/** 该岗位的学历分布，归一为比例。分母是正文里谈到学历的条数 */
export function degreeShare(name: string): Record<string, number> {
  const r = jdRawOf(name);
  if (!r?.degrees) return {};
  const total = Object.values(r.degrees).reduce((a, b) => a + b, 0);
  if (total <= 0) return {};
  const out: Record<string, number> = {};
  /* 按轴序输出，不按条数：学历有天然次序，照条数排会让“本科”跑到“大专”前面，
     属性栏上一列读下来是乱的 */
  for (const k of DEGREE_AXIS) if (r.degrees[k]) out[k] = r.degrees[k] / total;
  return out;
}

/** 学历维的覆盖情况，界面上交代口径时读它 */
export const DEGREE_COVERAGE = JDRAW
  ? {
      /** 判定出学历门槛的条数 */
      n: JDRAW.overall.degreeN ?? 0,
      /** 占连接上汇总表的招聘原文条数之比 */
      share: JDRAW.overall.matched
        ? (JDRAW.overall.degreeN ?? 0) / JDRAW.overall.matched
        : 0,
      dist: JDRAW.overall.degrees ?? {},
    }
  : null;

/* ---------------- 技术栈 ----------------

   全景图谱有两根切换轴，其一是技术栈。此前这一轴落在能力体系的
   两维十组上 —— 那是能力的分类，不是技术栈，界面上写作技术栈而筛出来的
   是能力组，读者据此得不到"这个技术方向上在招什么岗位"。

   汇总表本身带这一维：逐条招聘信息标有其技术栈，多栈条目以竖线分隔
   （jdstats.byJob[岗位].stacks）。全批 378,068 条覆盖百余个岗位，八个取值
   为后端开发与业务逻辑、基础设施与云原生、AI/ML 与数据智能、前端与用户体验、
   数据存储与管理、安全与合规、DevOps 与自动化、中间件与消息通信。
   本节把它按岗位归一为占比，供切换轴取用。

   占比而非计数：岗位的招聘条数相差三个数量级，按计数取阈会让小岗位一个也
   进不来。占比问的是"这个岗位的招聘里有多大一份属于该栈"，与岗位规模无关。 */

/** 岗位规范名 → 技术栈 → 该栈在此岗位招聘信息中所占的比例 */
const STACK_SHARE = new Map<string, Map<string, number>>();
{
  for (const [job, rec] of Object.entries(JDSTATS.byJob)) {
    const n = rec.n || 0;
    if (n <= 0) continue;
    const m = new Map<string, number>();
    for (const [combo, cnt] of Object.entries(rec.stacks ?? {})) {
      /* 一条招聘信息可同时属于多个栈，汇总表以竖线连写。逐个拆开各记一次：
         此处要答的是"这个岗位有多少条招聘涉及该栈"，不是"主栈是哪个"。 */
      for (const one of combo.split('|')) {
        const k = one.trim();
        if (k) m.set(k, (m.get(k) ?? 0) + cnt);
      }
    }
    for (const [k, v] of m) m.set(k, v / n);
    STACK_SHARE.set(job, m);
  }
}

/**
 * 认定一个岗位落在某技术栈内的下限。
 *
 * 多栈标注下几乎每个岗位在每个栈上都有一点残值：后端一栈在 99 个岗位里有 97 个
 * 见得到，取零为界等于不筛。一成是这批数据上区分得开的那一档 —— 该栈占到这个
 * 岗位招聘的十分之一以上，才算这个岗位确在这个方向上招人。
 */
const STACK_FLOOR = 0.1;

/** 该岗位招聘信息里落在这一技术栈的比例，无汇总表样本时为 0 */
export function stackShareOf(jobName: string, stack: string): number {
  return STACK_SHARE.get(jobName)?.get(stack) ?? 0;
}

/** 该岗位算不算落在这一技术栈内 */
export function jobInStack(jobName: string, stack: string): boolean {
  return stackShareOf(jobName, stack) >= STACK_FLOOR;
}

/** 八个技术栈，按覆盖的岗位数降序。label 即汇总表里的原名，可回源核对 */
export const TECH_STACKS: { name: string; jobs: number; posts: number }[] = (() => {
  const jobs = new Map<string, number>();
  const posts = new Map<string, number>();
  for (const [job, m] of STACK_SHARE) {
    const n = JDSTATS.byJob[job]?.n ?? 0;
    for (const [k, v] of m) {
      if (v < STACK_FLOOR) continue;
      jobs.set(k, (jobs.get(k) ?? 0) + 1);
      posts.set(k, (posts.get(k) ?? 0) + Math.round(v * n));
    }
  }
  return [...jobs.entries()]
    .map(([name, n]) => ({ name, jobs: n, posts: posts.get(name) ?? 0 }))
    .sort((a, b) => b.jobs - a.jobs || b.posts - a.posts);
})();

/* ---------------- 句级归因 ----------------

   哪一句招聘原文支撑哪一项能力要求。汇总表逐条给出该条要求的技能与各技能下
   命中的技能点，原文表给出正文全文，两者按 jobid 连接后由技能点名在正文中的
   落点反查所在句。全批 749,803 个（条，能力项）对中定位到 88.9%。 */

/** 该岗位各项能力要求的归因。键为技能名 */
export function jobAttribution(name: string) {
  return jdRawOf(name)?.attrib ?? {};
}

/** 归因的总体覆盖，界面上交代口径时读它 */
export const ATTRIB_SCOPE = JDRAW
  ? {
      pairs: JDRAW.overall.attribPairs ?? 0,
      hit: JDRAW.overall.attribHit ?? 0,
      share: JDRAW.overall.attribPairs
        ? (JDRAW.overall.attribHit ?? 0) / JDRAW.overall.attribPairs
        : 0,
      topSkills: JDRAW.attribLimits?.topSkills ?? 0,
      quotes: JDRAW.attribLimits?.quotes ?? 0,
      facets: JDRAW.attribLimits?.facets ?? 0,
    }
  : null;

/** 招聘原文的总体覆盖，界面上交代口径时读它 */
export const JDRAW_SCOPE = JDRAW
  ? {
      rows: JDRAW.overall.rows,
      matched: JDRAW.overall.matched,
      cities: JDRAW.overall.nCities,
      companies: JDRAW.overall.nCompanies,
      perJob: JDRAW.sampleLimits.perJob,
      snippet: JDRAW.sampleLimits.snippet,
    }
  : null;

/**
 * 该岗位的省份分布，归一为比例。
 *
 * @param allow 非空时只计其中的城市 —— 城市级勾选生效时，被勾掉的那几座
 *   连同它们那部分条数一并退出分母，省份条因而按剩下的城市重算，
 *   而不是先按全量算好再截一段。
 */
export function provinceShare(name: string, allow?: Set<string> | null): Record<string, number> {
  const cities = jdRawOf(name)?.cities;
  if (!cities) return {};
  const acc: Record<string, number> = {};
  let total = 0;
  for (const [c, n] of Object.entries(cities)) {
    if (allow && !allow.has(c)) continue;
    if (isNonGeo(c)) continue;
    const pv = provinceOf(c);
    acc[pv] = (acc[pv] ?? 0) + n;
    total += n;
  }
  if (total <= 0) return {};
  for (const k of Object.keys(acc)) acc[k] = acc[k] / total;
  return acc;
}

/* ---------------- 属性分布的量纲与档序 ----------------

   types/graph.ts 的 Distribution 约定为比例：下游一律按“岗位招聘条数 × 该档比例”
   还原绝对条数（见 explore.attrRowsOf 与 buildPostCells）。招聘汇总表给的是绝对条数，
   直接写入会使该维的量放大一个岗位条数的倍数，故此处先归一。

   档序另行指定。汇总表的键序为统计过程的产出顺序，薪资与年限两维本身有序，
   按键序渲染会得到“10-20k / 30-50k / 20-30k / 10k以下”这样的乱序坐标轴。 */

const SALARY_ORDER = ['10k以下', '10-20k', '20-30k', '30-50k', '50-70k', '70k以上'];
const LEVEL_ORDER = ['实习/应届', '初级(0-2年)', '中级(3-4年)', '高级(5-9年)', '专家(10年+)'];

function toShare(raw: Record<string, number> | undefined, order: string[]): Record<string, number> {
  if (!raw) return {};
  const sum = Object.values(raw).reduce((a, b) => a + b, 0);
  if (sum <= 0) return {};
  const out: Record<string, number> = {};
  /* 先按规定档序排，表里出现而档序未列的档接在其后，不丢数据 */
  for (const k of order) if (raw[k] !== undefined) out[k] = raw[k] / sum;
  for (const [k, v] of Object.entries(raw)) if (out[k] === undefined) out[k] = v / sum;
  return out;
}

/**
 * 技术方向的构成。
 *
 * 汇总表的技术栈一列写作以竖线分隔的组合（"后端开发与业务逻辑|数据存储与管理"），
 * 一条招聘信息可同时属于多个方向。此处按方向拆开后各计一次，再按合计归一，
 * 故读作"该岗位的招聘信息里各方向各占多大一份"，而非互斥的分类占比。
 */
function stackShare(stacks: Record<string, number> | undefined): Record<string, number> {
  const acc: Record<string, number> = {};
  for (const [combo, n] of Object.entries(stacks ?? {})) {
    for (const one of combo.split('|')) {
      const k = one.trim();
      if (k) acc[k] = (acc[k] ?? 0) + n;
    }
  }
  const sum = Object.values(acc).reduce((a, b) => a + b, 0);
  if (sum <= 0) return {};
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(acc).sort((a, b) => b[1] - a[1])) out[k] = v / sum;
  return out;
}

export function buildRealNodes(): GraphNode[] {
  const out: GraphNode[] = [];
  const totalW = GRAPH.totalWeight || 1;

  for (const j of GRAPH.nodes.jobs) {
    const ser = seriesOf(SERIES.jobs, j.id);
    const stat = jdStatOf(j.name);
    /* 岗位定义的后三项：既有岗位由汇总表的覆盖率与熟练度推出，叠层新岗位由
       推导的能力构成与最相近的既有岗位给出，口径见 data-pipeline 的 6.5 节。
       名称数组保留下来供检索与计数取用，结构化的那一份另挂在 jobDef 上。 */
    const def = JOBDEF.jobs[j.id];
    /* 叠层新岗位不在体系文件内，其定义随产物一并给出（j.def）；
       既有岗位的定义、判定关键词与边界取自体系文件。 */
    const tax = JOB_TAXONOMY[j.id];
    out.push({
      id: jid(j.id),
      kind: 'job',
      name: j.name,
      aliases: tax?.funtypes ?? [],
      category: j.cat,
      topCategory: j.cat,
      cluster: j.cat,
      definition: j.def ?? tax?.definition,
      funtypes: tax?.funtypes,
      keywords: tax?.keywords,
      boundary: tax?.boundary,
      jobDef: def,
      mustSkills: def?.must.map((x) => x.name),
      plusSkills: def?.plus.map((x) => x.name),
      scenarios: def?.scenarios.map((x) => x.name),
      /* 叠层岗位的首现取信号入场窗口，基图岗位取序列首个非空窗口 */
      firstSeen: j.origin === 'overlay' ? (j.born ?? REAL_MONTHS[0]) : firstMonth(ser),
      lastConfirmed: REAL_NOW,
      /* 本窗加权出现量占全窗总量的比例，逐窗可比 */
      marketShare: totalW > 0 ? j.w / totalW : 0,
      /* 招聘信息条数为岗位层唯一的实测计量 */
      frequency: j.hits,
      posts: j.hits,
      realCount: j.hits,
      confidence: j.origin === 'overlay' ? 0.6 : 0.95,
      status: j.origin === 'overlay' ? 'candidate' : trendOf(ser),
      origin: j.origin,
      gap: j.origin === 'overlay' ? (j.strength ?? 0) : 0,
      emerging: j.origin === 'overlay',
      provenance: j.origin === 'overlay' ? 'derived' : 'measured',
      attrs: stat
        ? {
            /* 省份由招聘原文的 place 列按行政区划汇总（见 data/provinces.ts）。
               学历一列原文表虽有其名、整批为空，值由正文的门槛语抽出（degreeShare）。
               技术方向取汇总表逐条标注的技术栈一列，按方向拆开后归一 */
            cities: provinceShare(j.name),
            degrees: degreeShare(j.name),
            experience: toShare(stat.levels, LEVEL_ORDER),
            salaryBands: toShare(stat.salaryBands, SALARY_ORDER),
            techStacks: stackShare(stat.stacks),
            /* 界面按千元读薪资，汇总表以元记，此处折算 */
            medianSalary: Math.round(stat.medianSalary / 1000),
            postCount: stat.n,
          }
        : undefined,
    });
  }

  for (const t of GRAPH.nodes.tasks) {
    const ser = seriesOf(SERIES.tasks, t.id);
    out.push({
      id: tid(t.id),
      kind: 'task',
      name: t.name,
      aliases: [],
      category: TASK_CATEGORY,
      definition: t.def,
      firstSeen: t.origin === 'overlay' ? (t.born ?? REAL_MONTHS[0]) : firstMonth(ser),
      lastConfirmed: REAL_NOW,
      marketShare: t.share,
      frequency: Math.round(t.share * totalW),
      confidence: t.origin === 'overlay' ? 0.6 : 0.94,
      status: t.origin === 'overlay' ? 'candidate' : trendOf(ser),
      origin: t.origin,
      gap: t.origin === 'overlay' ? (t.strength ?? 0) : 0,
      emerging: t.origin === 'overlay',
      provenance: t.origin === 'overlay' ? 'derived' : 'measured',
    });
  }

  for (const s of GRAPH.nodes.skills) {
    const ser = seriesOf(SERIES.skills, s.id);
    out.push({
      id: sid(s.id),
      kind: 'skill',
      name: s.name,
      aliases: [],
      /* 技能的一级归属为所属能力维度，与岗位的一级类别同性质 */
      category: s.dim,
      topCategory: s.group,
      definition: s.def,
      skillType: s.type,
      firstSeen: s.origin === 'overlay' ? (s.born ?? REAL_MONTHS[0]) : firstMonth(ser),
      lastConfirmed: REAL_NOW,
      marketShare: s.share,
      frequency: Math.round(s.share * totalW),
      /* 该技能下的技能点数，技能层的实测计量 */
      realCount: SP_COUNT_BY_SKILL.get(s.id) ?? 0,
      confidence: s.origin === 'overlay' ? 0.6 : 0.96,
      status: s.origin === 'overlay' ? 'candidate' : trendOf(ser),
      origin: s.origin,
      gap: s.origin === 'overlay' ? (s.strength ?? 0) : 0,
      emerging: s.origin === 'overlay',
      provenance: s.origin === 'overlay' ? 'derived' : 'measured',
    });
  }

  for (const sp of GRAPH.nodes.skillpoints) {
    const ser = seriesOf(SERIES.skillpoints, sp.id);
    const isOverlay = sp.origin === 'overlay';
    out.push({
      id: spid(sp.id),
      kind: 'skillpoint',
      name: sp.id,
      aliases: [],
      category: SKILL_OF_SP.get(sp.id) ?? '',
      definition: sp.def,
      /* 技能点继承所属技能的软硬分类；叠层技能点尚无归属技能，一律记为硬技能 */
      skillType: SP_TYPE.get(sp.id) ?? 'hard',
      firstSeen: sp.from,
      lastConfirmed: isOverlay ? sp.from : REAL_NOW,
      /* 叠层技能点的强度与基图权重不同量纲，不参与市场占比 */
      marketShare: isOverlay ? 0 : sp.w / SP_TOTAL,
      frequency: isOverlay ? 0 : Math.round(sp.w),
      realCount: sp.n,
      confidence: isOverlay ? Math.min(0.75, 0.45 + sp.w * 0.3) : Math.min(0.98, 0.6 + sp.n * 0.06),
      status: isOverlay ? 'candidate' : trendOf(ser),
      origin: isOverlay ? 'overlay' : 'base',
      emerging: isOverlay,
      gap: isOverlay ? sp.w : 0,
      provenance: isOverlay ? 'derived' : 'measured',
    });
  }

  return out;
}

/** 任务体系为扁平结构，无一级归属 */
const TASK_CATEGORY = '（体系为扁平结构，无一级归属）';

/* 技能点归属：由技能到技能点的边反查。一个技能点可挂在多个技能下，
   取权重最高的那一条作为归属，与"这个工具主要属于哪项技能"的读法一致。 */
const SKILL_OF_SP = new Map<string, string>();
const SP_TYPE = new Map<string, 'hard' | 'soft'>();
const SP_COUNT_BY_SKILL = new Map<string, number>();
{
  const best = new Map<string, number>();
  for (const e of GRAPH.edges.skillSkillpoint) {
    const prev = best.get(e.t) ?? -1;
    if (e.w > prev) {
      best.set(e.t, e.w);
      const sk = SKILL_BY_CODE.get(e.s);
      SKILL_OF_SP.set(e.t, sk?.name ?? e.s);
      SP_TYPE.set(e.t, sk?.type ?? 'hard');
    }
    SP_COUNT_BY_SKILL.set(e.s, (SP_COUNT_BY_SKILL.get(e.s) ?? 0) + 1);
  }
}

export { SKILL_OF_SP, SP_COUNT_BY_SKILL };

/* ==================== 边 ==================== */

/**
 * 权重下限。技能到技能点一层约三成的边在四位小数下取整为零，
 * 系跨窗衰减系数累积所致，不表示该边不存在。低于此值的边不进图，
 * 但计数仍以源数据为准，两者的差额在 manifest 里可查。
 */
export const EDGE_EPS = 1e-4;

/* ---------------- 证据 ----------------

   叠层逐条记录了信号的来源文档、发表日期与原文片段。这些证据锚定的是实体，
   而边的证据要回答"这条关系凭什么成立"，两者落点不同：一条指向某实体的边，
   其前瞻那一截正是由该实体的叠层证据支撑的，故按终点实体挂载。

   论文一侧的标题由构建阶段自证据句中"标题："起头的那一条取得，逐篇存于
   delta.paperTitles；本批引用到的 2297 篇全部有标题，其中 72 篇取自补充表
   （data-pipeline/paper-titles.json，来源为 arXiv 官方接口与迁移包内的论文原文）。
   表内查不到时退回编号，不以空标题示人。新闻一侧的文档标识写作
   `来源\标题`，两段拆开分别用。片段在构建阶段已截断至可核对的长度，
   完整原文在算法仓库。 */

/** arXiv 编号 → 论文标题。查不到时退回“arXiv:编号”，仍可据以回源 */
export const paperTitleOf = (docId: string) => DELTA.paperTitles?.[docId] || `arXiv:${docId}`;

/** 新闻文档标识拆为来源与标题。无分隔符时整串即标题，来源未知 */
export function splitNewsDoc(docId: string): { outlet: string | null; title: string } {
  const i = docId.indexOf('\\');
  if (i <= 0) return { outlet: null, title: docId };
  return { outlet: docId.slice(0, i).trim(), title: docId.slice(i + 1).trim() || docId };
}

/** 一条证据的来源出处：论文一律为 arXiv，新闻取文档标识的前半段 */
export const outletOf = (docId: string, isPaper: boolean) =>
  isPaper ? 'arXiv' : splitNewsDoc(docId).outlet;

/** 实体 id → 该实体的叠层证据，按发表日期倒序 */
/** 条目 id → 该条目的叠层证据。叠层新岗位一条实测边也没有，其证据只挂在
    条目本身，故岗位洞察页取它作数据来源一栏的落点 */
export const EVIDENCE_BY_ENTITY = (() => {
  const bag = new Map<string, EvidenceRef[]>();
  const push = (entityId: string, ev: DeltaEvidence) => {
    const isPaper = ev.src === 'papers';
    const arr = bag.get(entityId) ?? [];
    for (const line of ev.lines) {
      arr.push({
        docId: ev.doc,
        sourceType: isPaper ? 'paper' : 'news',
        title: isPaper ? paperTitleOf(ev.doc) : splitNewsDoc(ev.doc).title,
        outlet: outletOf(ev.doc, isPaper) ?? undefined,
        publishedAt: ev.date,
        snippet: line,
        /* 薪资加权与原创性判定只适用于招聘信息一侧，论文与新闻不参与 */
        salaryWeight: 1,
        originality: 1,
        extractedNodeId: entityId,
      });
    }
    bag.set(entityId, arr);
  };

  /* 按实体读一遍，不逐窗读：一条信号自入场起每窗都会再出现一次，
     逐窗读会把同一句证据推进表十余次 */
  for (const st of Object.values(DELTA.strengthenDefs)) {
    const eid =
      st.taxonomy === 'jobs' ? jid(st.code) : st.taxonomy === 'tasks' ? tid(st.code) : sid(st.code);
    for (const ev of st.evidence) push(eid, ev);
  }
  for (const it of Object.values(DELTA.entities)) {
    for (const ev of it.evidence) push(entityIdOf(it), ev);
  }
  for (const arr of bag.values()) {
    arr.sort((a, b) => (a.publishedAt < b.publishedAt ? 1 : -1));
  }
  return bag;
})();

/** 叠层证据的覆盖情况，界面上交代口径时读它 */
export const EVIDENCE_STATS = {
  entities: EVIDENCE_BY_ENTITY.size,
  refs: [...EVIDENCE_BY_ENTITY.values()].reduce((a, v) => a + v.length, 0),
  papers: new Set(
    [...EVIDENCE_BY_ENTITY.values()].flat().filter((e) => e.sourceType === 'paper').map((e) => e.docId),
  ).size,
  news: new Set(
    [...EVIDENCE_BY_ENTITY.values()].flat().filter((e) => e.sourceType === 'news').map((e) => e.docId),
  ).size,
};

/* ---------------- 行业新闻快报 ----------------

   同一篇报道会作为多个条目的证据反复出现，故按文档标识归拢一次：
   一篇报道锚定到哪几个条目，本身就是"这条消息与图谱有多大关系"的读数。

   证据表里逐条挂在实体上，形状是"条目 → 它的出处"；快报要的是反过来的
   "报道 → 它牵动了哪些条目"，两者不能互相替代，故另立一份。 */

/** 一条行业报道，及其在图谱中的落点 */
export interface NewsBrief {
  /** 文档标识，形如 `来源\标题`，回源时按它查 */
  docId: string;
  outlet: string;
  /** 供展示的标题：剪去落盘编号，分隔符还原 */
  title: string;
  /** 发布日期，'YYYY-MM-DD' */
  date: string;
  /** 该报道锚定到的图谱条目，按层级由高到低 */
  anchors: { id: string; name: string; kind: NodeKind }[];
  /** 逐句原文，构建阶段已截断至可核对的长度 */
  lines: string[];
  /**
   * 标题在落盘时被截断。
   *
   * 抓取按 `标题_编号` 命名文件，整串受文件名长度所限，标题长的那些截在中途
   * （"…on Towards Data S"）。这类标题读起来像半句话，故凡要把标题当作
   * 一句话展示的地方一律跳过它们；条目详情里的逐条原文摘录不受影响 ——
   * 那里标题只作出处标注，与旁边的日期、片段连着读，截断不影响回源。
   */
  truncated: boolean;
}

/**
 * 剪去标题尾部的落盘编号。
 *
 * 抓取时文件名写作 `标题_编号`，编号或为站内 ID、或为 URL 末段的 slug，
 * 都不该出现在界面上。中文标题里的下划线则是原标题的分隔符
 * （"刚刚_DeepSeek 开始频繁更新_Tile Kernels"），剪掉会把标题截半，
 * 故只认末段确为编号的那一类：纯数字、连字符 slug，或十六位以上的随机串。
 */
function stripDocSuffix(title: string): string {
  const i = title.lastIndexOf('_');
  if (i <= 0) return title;
  const tail = title.slice(i + 1);
  const isId =
    /^\d+$/.test(tail) || /^[a-z0-9]+(?:-[a-z0-9]+)+$/i.test(tail) || /^[A-Za-z0-9]{16,}$/.test(tail);
  return isId ? title.slice(0, i).trimEnd() : title;
}

/** 展示用标题：剪去编号后，把落盘时替换掉标点的下划线还原为间隔号 */
const newsTitleOf = (docId: string) =>
  stripDocSuffix(splitNewsDoc(docId).title).replace(/\s*_\s*/g, ' · ').trim();

/** 落盘时文件名的长度上限。标题一段占满这个长度即说明它是被截住的，不是写到这里为止 */
const DOC_NAME_MAX = 78;

/** 标题是否截在中途：占满长度上限，或剪去编号后末尾悬着一个空格 */
const isTitleTruncated = (docId: string) => {
  const raw = splitNewsDoc(docId).title;
  return raw.length >= DOC_NAME_MAX || /\s$/.test(stripDocSuffix(raw));
};

export const NEWS_BRIEFS: NewsBrief[] = (() => {
  const bag = new Map<string, NewsBrief>();
  const push = (entityId: string, name: string, kind: NodeKind, ev: DeltaEvidence) => {
    if (ev.src !== 'news' || !ev.date) return;
    let b = bag.get(ev.doc);
    if (!b) {
      const { outlet } = splitNewsDoc(ev.doc);
      bag.set(
        ev.doc,
        (b = {
          docId: ev.doc,
          outlet: outlet ?? '未标注来源',
          title: newsTitleOf(ev.doc),
          date: ev.date,
          anchors: [],
          lines: [],
          truncated: isTitleTruncated(ev.doc),
        }),
      );
    }
    /* 同一篇报道在不同条目下重复出现时取最早的那个日期：抓取批次不同，
       同一篇的日期偶有出入，取最早的一档与"首现"的口径一致 */
    if (ev.date < b.date) b.date = ev.date;
    if (!b.anchors.some((a) => a.id === entityId)) b.anchors.push({ id: entityId, name, kind });
    for (const line of ev.lines) if (!b.lines.includes(line)) b.lines.push(line);
  };

  for (const st of Object.values(DELTA.strengthenDefs)) {
    const eid =
      st.taxonomy === 'jobs' ? jid(st.code) : st.taxonomy === 'tasks' ? tid(st.code) : sid(st.code);
    const kind: NodeKind =
      st.taxonomy === 'jobs' ? 'job' : st.taxonomy === 'tasks' ? 'task' : 'skill';
    for (const ev of st.evidence) push(eid, st.name, kind, ev);
  }
  for (const it of Object.values(DELTA.entities)) {
    for (const ev of it.evidence) push(entityIdOf(it), it.name, it.kind, ev);
  }

  const RANK: Record<NodeKind, number> = { job: 0, task: 1, skill: 2, skillpoint: 3 };
  const out = [...bag.values()];
  for (const b of out) b.anchors.sort((a, c) => RANK[a.kind] - RANK[c.kind]);
  return out.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
})();

/** 叠层信号在末窗的三源构成，用于边的可信度指纹。
    按末窗一窗计：逐窗累加会把入场早的信号按其在图内的窗数重复计一遍，
    构成比因而偏向早期那一批 */
const DELTA_SOURCE_MIX = (() => {
  let paper = 0;
  let news = 0;
  for (const [, , src] of DELTA.byWindow[REAL_NOW]?.items ?? []) {
    for (const s of src.split('+')) {
      if (s === 'papers') paper++;
      else if (s === 'news') news++;
    }
  }
  const t = paper + news || 1;
  return { paper: paper / t, news: news / t };
})();

function mkEdge(
  kind: GraphEdge['kind'],
  source: string,
  target: string,
  e: { w: number; e: number; g: number; o?: string },
): GraphEdge {
  const base = e.w;
  const eff = e.e;
  const delta = Math.max(0, eff - base);
  /* 边的来源构成：基图权重来自招聘信息，叠加部分来自论文与新闻。
     两段的比例即该边的可信度指纹。 */
  const tot = base + delta || 1;
  const jdShare = base / tot;
  const dShare = delta / tot;
  return {
    id: `${kind}:${source}>${target}`,
    source,
    target,
    kind,
    baseWeight: base,
    deltaWeight0: delta,
    deltaWeight: delta,
    effectiveWeight: eff,
    /* 置信度按基图权重定：招聘统计量越高，这条关系越站得住 */
    confidence: base > 0 ? Math.min(0.98, 0.62 + Math.sqrt(base) * 0.36) : 0.55,
    cooccurrence: base,
    explicitLink: base > 0,
    firstSeen: REAL_MONTHS[0],
    lastConfirmed: REAL_NOW,
    unconfirmedMonths: base > 0 ? 0 : REAL_MONTHS.length,
    status: e.o ? 'candidate' : delta > 1e-6 ? 'strengthening' : 'active',
    sourceMix: {
      jd: jdShare,
      paper: dShare * DELTA_SOURCE_MIX.paper,
      news: dShare * DELTA_SOURCE_MIX.news,
    },
    /* 前瞻那一截由终点实体的叠层证据支撑，逐条可回溯至论文编号或新闻标题。
       招聘一侧的原文不在本批产物内，故基图那一截无证据条目。 */
    evidence: delta > 1e-6 ? (EVIDENCE_BY_ENTITY.get(target) ?? []) : [],
    provenance: e.o ? 'derived' : 'measured',
  };
}

export function buildRealEdges(): GraphEdge[] {
  const out: GraphEdge[] = [];
  for (const e of GRAPH.edges.jobTask) {
    if (e.e < EDGE_EPS) continue;
    out.push(mkEdge('J-T', jid(e.s), tid(e.t), e));
  }
  for (const e of GRAPH.edges.jobSkill) {
    if (e.e < EDGE_EPS) continue;
    out.push(mkEdge('J-S', jid(e.s), sid(e.t), e));
  }
  for (const e of GRAPH.edges.taskSkill) {
    if (e.e < EDGE_EPS) continue;
    out.push(mkEdge('T-S', tid(e.s), sid(e.t), e));
  }
  for (const e of GRAPH.edges.skillSkillpoint) {
    if (e.e < EDGE_EPS) continue;
    out.push(mkEdge('S-SP', sid(e.s), spid(e.t), e));
  }
  return out;
}

/* ==================== 叠层新岗位的推导关联 ====================

   算法侧未产出这一层：叠层新岗位一条 J-T 边也没有，任务向量因而是零向量，
   而岗位空间关系图正是按任务构成算距离的 —— 零向量与任一岗位的余弦距离
   恒等于 1，这几个点一直读不出落位。构建阶段由已有字段推得一份，
   算法与口径见 data-pipeline/jobvec.mjs。

   这批边单独放，不并进 buildRealEdges 的返回值：四类边是实测，这一份是推导。
   并进去，全站每一张读 J-T 或 J-S 的图都会无声地多出几条推来的连线，
   而它们各自的口径标里写的都是“四类边均为实测”。要用的图各自取用，
   界面上按 derived 交代。目前取用它的有两处：岗位空间关系图的距离，
   与岗位定义五要素里的核心职责。 */

/** 一条推导边，取值区间与实测边一致（按该岗位最高项归一到 1） */
function mkInferredEdge(kind: GraphEdge['kind'], source: string, target: string, w: number): GraphEdge {
  return {
    ...mkEdge(kind, source, target, { w: 0, e: w, g: w }),
    id: `${kind}~inf:${source}>${target}`,
    /* 推导而非观测：这一条不是从招聘信息里数出来的，
       是由证据句里的结构化信号与文本锚点相似度推得的 */
    provenance: 'derived',
    /* 基图权重为零 —— 招聘市场还没确认这个岗位，本来就没有统计量可言。
       confidence 随之落在 mkEdge 里那一档 0.55 上，与“候选”的成色相称 */
    status: 'candidate',
    explicitLink: false,
  };
}

/** 叠层新岗位的推导 J-T 与 J-S 边。算法侧未产出这一层时为空数组 */
export const INFERRED_EDGES: GraphEdge[] = (() => {
  const inf = GRAPH.inferred;
  if (!inf) return [];
  return [
    ...inf.jobTask.map((e) => mkInferredEdge('J-T', jid(e.s), tid(e.t), e.w)),
    ...inf.jobSkill.map((e) => mkInferredEdge('J-S', jid(e.s), sid(e.t), e.w)),
  ];
})();

/**
 * 逐个叠层新岗位的推导成色，界面上据此交代口径并决定给不给相近岗位清单。
 *
 * 键为图上的岗位节点 id（带 `J:` 前缀）。
 */
export const INFERRED_JOBS: Map<string, {
  anchored: boolean;
  via: 'signal' | 'text';
  sigLines: number;
  topSim: number;
  nTasks: number;
  nSkills: number;
}> = new Map(
  Object.entries(GRAPH.inferred?.jobs ?? {}).map(([code, v]) => [jid(code), v]),
);

/* ==================== 月度序列 ==================== */

/**
 * 图谱的月度切片。
 *
 * demand 取各条目在同层内的份额，与算法侧 entity_freq 的口径一致；
 * 岗位一层取本窗加权出现量占全窗总量的比例。取值统一折算为相对末窗的比值，
 * 末窗恒为 1，量纲由界面按当前口径现算。
 *
 * series 为结构规模的计数，只有拿得出真实计数的两层给：岗位给招聘信息条数，
 * 技能给组内技能点数。任务与技能点两层无对应计数，不给键。
 */
export function buildRealPrism(): PrismTimeline {
  const demand: Record<string, (number | null)[]> = {};
  const series: Record<string, (number | null)[]> = {};
  const confirmedAt: Record<string, string> = {};

  const relTo = (arr: (number | null)[] | undefined): (number | null)[] | undefined => {
    if (!arr) return undefined;
    const end = arr[LAST];
    if (end === null || end <= 0) return undefined;
    return arr.map((v) => (v === null ? null : Number((v / end).toFixed(4))));
  };

  for (const [code, arr] of Object.entries(SERIES.jobs)) {
    const r = relTo(arr);
    if (r) demand[jid(code)] = r;
    confirmedAt[jid(code)] = firstMonth(arr);
  }
  for (const [code, arr] of Object.entries(SERIES.tasks)) {
    const r = relTo(arr);
    if (r) demand[tid(code)] = r;
    confirmedAt[tid(code)] = firstMonth(arr);
  }
  for (const [code, arr] of Object.entries(SERIES.skills)) {
    const r = relTo(arr);
    if (r) demand[sid(code)] = r;
    confirmedAt[sid(code)] = firstMonth(arr);
  }
  for (const [name, arr] of Object.entries(SERIES.skillpoints)) {
    const r = relTo(arr);
    if (r) demand[spid(name)] = r;
    confirmedAt[spid(name)] = firstMonth(arr);
  }

  /* 岗位的结构规模逐月给招聘信息条数按份额折算；
     技能的结构规模为组内技能点数，逐窗由该窗技能点集合数出。 */
  for (const j of GRAPH.nodes.jobs) {
    const arr = SERIES.jobs[j.id];
    if (!arr || !j.hits) continue;
    const end = arr[LAST];
    if (end === null || end <= 0) continue;
    series[jid(j.id)] = arr.map((v) => (v === null ? null : Math.round((v / end) * j.hits)));
  }
  for (const s of GRAPH.nodes.skills) {
    const n = SP_COUNT_BY_SKILL.get(s.id) ?? 0;
    if (!n) continue;
    const arr = SERIES.skills[s.id];
    if (!arr) continue;
    const end = arr[LAST];
    if (end === null || end <= 0) continue;
    series[sid(s.id)] = arr.map((v) => (v === null ? null : Math.max(1, Math.round((v / end) * n))));
  }

  return {
    months: REAL_MONTHS,
    series,
    demand,
    confirmedAt,
    provenance: 'measured',
  };
}

/* ==================== 三源信号 ==================== */

/**
 * 实体的三源强度序列。
 *
 * 招聘一路取自基图的逐月份额，论文与新闻两路取自叠层：叠层按窗口记录每条信号
 * 的强度与来源，逐窗累加即得两路的月度序列。基准窗（2022-05/06）不建叠层，
 * 两路在这两个月为零，这是时序设计下的正常状态而非缺测。
 *
 * 叠层的两类记录都要读：`strengthenings` 是对体系内既有条目的增强，
 * `items` 是叠层自己发现的新实体。上一版只读前者，于是四个新岗位与其余叠层
 * 新条目三路全空 —— 它们不在基图序列里（招聘一路本就为零），又拿不到前瞻两路，
 * 合成出来的确认强度恒为零。相图上这批点因此一律贴在纵轴底端，尾迹缩成一个点，
 * 而 `items` 里逐窗的强度读数（如 PJ-001 的 0.69 → 0.81 → 0.79）一个都没有用上。
 *
 * gap 的口径与算法侧一致：max(0, 0.7·paper + 0.3·news − jd)。
 */
export function buildRealSignals(nodes: GraphNode[]): EntitySignal[] {
  /* 叠层逐窗的实体强度：新实体按 id 记，增强按体系编码记 */
  const paperByWin = new Map<string, number[]>();
  const newsByWin = new Map<string, number[]>();

  const bump = (bag: Map<string, number[]>, key: string, wi: number, v: number) => {
    let arr = bag.get(key);
    if (!arr) bag.set(key, (arr = new Array(REAL_MONTHS.length).fill(0)));
    arr[wi] = Math.max(arr[wi], v);
  };

  /** 一条叠层记录的强度按其来源分流。同窗同实体多条时取最大值，不累加 */
  const spread = (key: string, wi: number, strength: number, sources: readonly string[]) => {
    for (const s of sources) {
      if (s === 'papers') bump(paperByWin, key, wi, strength);
      else if (s === 'news') bump(newsByWin, key, wi, strength);
    }
  };

  for (let wi = 0; wi < REAL_MONTHS.length; wi++) {
    const w = REAL_MONTHS[wi];
    const d = DELTA.byWindow[w];
    if (!d) continue;
    for (const [k, strength, src] of d.strengthenings) {
      const st = DELTA.strengthenDefs[k];
      if (!st) continue;
      const key =
        st.taxonomy === 'jobs' ? jid(st.code) : st.taxonomy === 'tasks' ? tid(st.code) : sid(st.code);
      spread(key, wi, strength, src.split('+'));
    }
    /* 新实体的 id 映射与证据表同源（见 entityIdOf），两处必须一致：
       不一致时信号挂在一个 id 上、证据挂在另一个上，图上就成了
       “有强度没出处”与“有出处没强度”两个都读不通的实体。 */
    for (const [k, strength, src] of d.items) {
      const it = DELTA.entities[k];
      if (!it) continue;
      spread(entityIdOf(it), wi, strength, src.split('+'));
    }
  }

  /* 招聘一路按层内最大值归一。
     四层的份额量纲各不相同（任务与技能是同层占比，岗位是全窗加权占比，
     技能点是权重绝对值），而叠层强度统一落在 [0,1]，两者必须同量纲才谈得上
     相减。层内归一之后，jd 读作"该条目在本层里的相对需求高度"，
     与叠层强度"论文与新闻对它的提及高度"可以直接比较，gap 因而有意义。 */
  const layerPeak = (bag: Record<string, (number | null)[]>) => {
    let m = 0;
    for (const arr of Object.values(bag)) {
      for (const v of arr) if (v !== null && v > m) m = v;
    }
    return m || 1;
  };
  const PEAK = {
    job: layerPeak(SERIES.jobs),
    task: layerPeak(SERIES.tasks),
    skill: layerPeak(SERIES.skills),
    skillpoint: layerPeak(SERIES.skillpoints),
  };

  /* ==================== 提前量与信号时效 ====================

     两项都由已有的三源序列现算，不引入源数据以外的量。

     提前量 —— 前瞻曲线相对招聘曲线的最优滞后。把前瞻曲线整体右移 k 个自然月后
     与招聘曲线求相关系数，取相关最高的那个 k 即"论文与新闻比招聘市场早说了多久"。
     只由两个首现月相减是不够的：首现是单点，一条早期只被提过一次的信号会给出
     一个偏大的提前量，而互相关看的是两条曲线整段的形状。

     观测窗口不连续（2022-12 与 2023-01 未接入），故先把两条序列摊到自然月轴上，
     缺测的月份留空并在求相关时成对剔除 —— 直接按窗口序移位会在断档处错位两个月。

     信号时效 —— 末窗的前瞻强度相对该条目自身历史峰值的比例。算法侧的叠层强度
     按月衰减（α=0.85），一条提出后再无人跟进的信号会逐窗走低，该比例即读作
     "这条信号现在还有多新"。此前此处恒置 1，整列因而一律显示 100%，读不出差别。 */

  /** 观测窗口摊到自然月轴上的下标，缺测的月份不占位 */
  const MONTH_SPAN = monthDiff(REAL_MONTHS[0], REAL_MONTHS[LAST]) + 1;
  const MONTH_SLOT = REAL_MONTHS.map((m) => monthDiff(REAL_MONTHS[0], m));

  /** 成对剔除缺测月后的皮尔逊相关系数；有效对不足或某一侧为常量时不给值 */
  function pearson(a: (number | null)[], b: (number | null)[]): number | null {
    let n = 0;
    let sa = 0;
    let sb = 0;
    for (let i = 0; i < a.length; i++) {
      const x = a[i];
      const y = b[i];
      if (x === null || y === null) continue;
      n += 1;
      sa += x;
      sb += y;
    }
    if (n < 8) return null;
    const ma = sa / n;
    const mb = sb / n;
    let num = 0;
    let da = 0;
    let db = 0;
    for (let i = 0; i < a.length; i++) {
      const x = a[i];
      const y = b[i];
      if (x === null || y === null) continue;
      num += (x - ma) * (y - mb);
      da += (x - ma) ** 2;
      db += (y - mb) ** 2;
    }
    if (da <= 1e-12 || db <= 1e-12) return null;
    return num / Math.sqrt(da * db);
  }

  /** 相关系数的下限。低于它时两条曲线谈不上同形，滞后取到的那个 k 没有意义 */
  const LEAD_MIN_R = 0.35;
  /** 滞后的搜索上限，取观测跨度的一半 —— 再长时重叠段不足以支撑一个相关系数 */
  const LEAD_MAX = Math.floor(MONTH_SPAN / 2);

  /** 把一条按窗口取值的序列摊到自然月轴上，缺测月留空 */
  const onMonths = (v: number[]): (number | null)[] => {
    const out2: (number | null)[] = new Array(MONTH_SPAN).fill(null);
    for (let i = 0; i < v.length; i++) out2[MONTH_SLOT[i]] = v[i];
    return out2;
  };

  /**
   * 相邻观测之间的变化量。互相关取变化量而非水平值：
   * 一条自首窗起就居高不下的技能，其水平序列与任何一条同样走高的曲线都高度相关，
   * 滞后取到哪个 k 全由两条曲线各自的长期趋势定，与"谁先动"无关。
   * 改看变化量之后，问的才是"论文这一头抬起来之后，招聘那一头隔多久跟着抬"。
   */
  const diffOf = (v: (number | null)[]): (number | null)[] => {
    const out2: (number | null)[] = new Array(v.length).fill(null);
    let prev: number | null = null;
    let prevAt = -1;
    for (let i = 0; i < v.length; i++) {
      const x = v[i];
      if (x === null) continue;
      if (prev !== null && i - prevAt === 1) out2[i] = x - prev;
      prev = x;
      prevAt = i;
    }
    return out2;
  };

  /** 前瞻曲线右移多少个自然月与招聘曲线最贴合 */
  function bestLag(fore: number[], jd: number[]): number | undefined {
    if (!fore.some((v) => v > 0) || !jd.some((v) => v > 0)) return undefined;
    const f = diffOf(onMonths(fore));
    const j = diffOf(onMonths(jd));
    let bestK: number | undefined;
    let bestR = LEAD_MIN_R;
    for (let k = 1; k <= LEAD_MAX; k++) {
      const shifted: (number | null)[] = new Array(MONTH_SPAN).fill(null);
      for (let t = 0; t + k < MONTH_SPAN; t++) shifted[t + k] = f[t];
      const r = pearson(shifted, j);
      if (r !== null && r > bestR) {
        bestR = r;
        bestK = k;
      }
    }
    return bestK;
  }

  const out: EntitySignal[] = [];
  for (const n of nodes) {
    const bag =
      n.kind === 'job'
        ? SERIES.jobs
        : n.kind === 'task'
          ? SERIES.tasks
          : n.kind === 'skill'
            ? SERIES.skills
            : SERIES.skillpoints;
    const code = n.id.slice(n.id.indexOf(':') + 1);
    const raw = bag[code];
    const peak = PEAK[n.kind];
    const jd = REAL_MONTHS.map((_, i) => {
      const v = raw?.[i];
      if (v === null || v === undefined) return 0;
      return Number(Math.min(1, v / peak).toFixed(4));
    });

    const paper = paperByWin.get(n.id) ?? new Array(REAL_MONTHS.length).fill(0);
    const news = newsByWin.get(n.id) ?? new Array(REAL_MONTHS.length).fill(0);
    const gap = REAL_MONTHS.map((_, i) =>
      Number(Math.max(0, 0.7 * paper[i] + 0.3 * news[i] - jd[i]).toFixed(4)),
    );

    const firstOf = (a: number[]) => {
      const i = a.findIndex((v) => v > 0);
      return i >= 0 ? REAL_MONTHS[i] : undefined;
    };

    out.push({
      entityId: n.id,
      entityName: n.name,
      kind: n.kind,
      category: n.category,
      months: REAL_MONTHS,
      jd,
      paper,
      news,
      gap,
      firstPaperAt: firstOf(paper),
      firstNewsAt: firstOf(news),
      firstJdAt: firstOf(jd),
      leadMonths: {
        paper: bestLag(paper, jd),
        news: bestLag(news, jd),
      },
      decayFactor: (() => {
        const fore = REAL_MONTHS.map((_, i) => 0.7 * paper[i] + 0.3 * news[i]);
        let pk = 0;
        for (const v of fore) if (v > pk) pk = v;
        if (pk <= 0) return 0;
        return Number(Math.min(1, Math.max(0, fore[LAST] / pk)).toFixed(3));
      })(),
    });
  }

  /* ==================== 预计进入招聘要求的时间 ====================

     叠层记录的是"论文与新闻已经在说、招聘市场还没说"这一状态，本身不含
     何时会说。预计时间因而只能由已确认的那一批现算：把实测滞后（前瞻曲线
     抬起到招聘曲线跟着抬之间的月数，即上文的 bestLag）当作一个分布，
     未确认的条目按这个分布外推。此前这两个字段只有演示词表一侧产出，
     真实数据下恒为空，界面上"预计"二字后面跟着一个破折号。

     外推不把分布的中位数直接加在首现月上：一条已等 w 个月仍未确认的条目，
     其滞后必然大于 w，用无条件中位数会算出一个早已过去的月份 —— 预计时间
     落在过去，比留一个破折号更坏。故取条件分布，只在大于 w 的那些实测滞后里
     取分位数，预计时间因而必落在末窗之后，区间也是实测分位而非写死的偏移量。

     w 超出实测滞后的上界时条件样本为空。此时改自末窗起算同一套分位数，
     读作"按现有样本，这类条目此后仍需这么久"；其依据是该分布的中位数与
     均值接近，近似无记忆。这一档与前一档在界面上分别注明，不混为一谈。 */

  /** 升序样本的分位数，取下标法，不在样本点之间插值 */
  const qtl = (arr: number[], p: number) =>
    arr[Math.max(0, Math.min(arr.length - 1, Math.floor(arr.length * p)))];

  /** 一路信源的实测滞后样本。口径与首页"实测中位提前 N 个月"同源，
      故对照面板报的预计时间与首页报的提前量必然对得上 */
  const lagSample = (route: 'paper' | 'news') =>
    out
      .filter(
        (s) =>
          s.kind !== 'job' &&
          s.firstJdAt &&
          (route === 'paper' ? s.firstPaperAt : s.firstNewsAt),
      )
      .map((s) => s.leadMonths[route])
      .filter((v): v is number => typeof v === 'number' && v > 0)
      .sort((a, b) => a - b);

  const LAG = { paper: lagSample('paper'), news: lagSample('news') };
  /** 分位数在样本过薄时读不出分布，此时宁可不给预计时间 */
  const LAG_MIN_N = 8;

  for (const s of out) {
    if (s.firstJdAt) continue;
    /* 两路都有时以论文一路为准：论文是三源中最早的一路，其实测样本也更厚 */
    const route = s.firstPaperAt ? 'paper' : s.firstNewsAt ? 'news' : null;
    if (!route) continue;
    const sample = LAG[route];
    if (sample.length < LAG_MIN_N) continue;
    const base = (route === 'paper' ? s.firstPaperAt : s.firstNewsAt)!;
    const waited = monthDiff(base, REAL_MONTHS[LAST]);
    const cond = sample.filter((v) => v > waited);
    const anchor = cond.length ? base : REAL_MONTHS[LAST];
    const pool = cond.length ? cond : sample;
    s.predictedJdAt = addMonths(anchor, qtl(pool, 0.5));
    s.predictedJdRange = [addMonths(anchor, qtl(pool, 0.25)), addMonths(anchor, qtl(pool, 0.75))];
    s.predictedBasis = {
      route,
      n: pool.length,
      waited,
      beyondSample: cond.length === 0,
    };
  }

  return out;
}

/* ==================== 版本与变更 ==================== */

/**
 * 一个窗口即一个版本。
 *
 * 版本号取窗序而非语义版本号：这批数据的每一版就是一个自然月的观测窗口，
 * 编一个 v1.0 到 v2.1 的版本序列会让读者以为版本之间发生过体系变更，
 * 而各窗的体系是同一套（任务 35 项、技能 50 项，全程未变）。
 */
export const REAL_VERSION_DEFS: { version: string; date: string; label: string }[] = REAL_MONTHS.map(
  (w, i) => ({
    version: `w${i + 1}`,
    date: w,
    label: BASELINE_WINDOWS.includes(w) ? `${w} 基准窗` : `${w} 观测窗`,
  }),
);

/** 一个窗口即一个版本，每窗的规模读自该窗的构建记录 */
export function buildRealVersions(): GraphVersion[] {
  return REAL_VERSION_DEFS.map((v) => {
    const w = v.date;
    const c = SERIES.counts[w];
    return {
      version: v.version,
      date: v.date,
      label: v.label,
      stats: {
        jobs: c?.jobs ?? 0,
        tasks: c?.tasks ?? 0,
        skills: c?.skills ?? 0,
        skillPoints: c?.skillpoints ?? 0,
        edges: Object.values(c?.edges ?? {}).reduce((a, b) => a + b, 0),
        overlayEdges: 0,
      },
    };
  });
}

/**
 * 跨窗变更。
 *
 * 逐窗比对每个条目的份额：从无到有记为新增，份额变动超过阈值记为修改。
 * 变更由份额差分直接算出，与图上读到的量同源，不另立一套记录。
 */
const CHANGE_EPS = 0.08;

export function buildRealChanges(nodes: GraphNode[]): ChangeEvent[] {
  const nameOf = new Map(nodes.map((n) => [n.id, n.name]));
  const kindOf = new Map(nodes.map((n) => [n.id, n.kind]));
  const out: ChangeEvent[] = [];

  const scan = (bag: Record<string, (number | null)[]>, mk: (c: string) => string) => {
    for (const [code, arr] of Object.entries(bag)) {
      const nodeId = mk(code);
      const name = nameOf.get(nodeId);
      const kind = kindOf.get(nodeId);
      if (!name || !kind) continue;
      for (let i = 1; i < arr.length; i++) {
        const a = arr[i - 1];
        const b = arr[i];
        if (b === null) continue;
        if (a === null || a === 0) {
          if (b > 0) {
            out.push({
              id: `chg:${nodeId}:${REAL_MONTHS[i]}:add`,
              version: `w${i + 1}`,
              date: REAL_MONTHS[i],
              op: 'add',
              jobId: '',
              target: { kind, id: nodeId, name },
              field: 'share',
              before: 0,
              after: b,
              reason: `${REAL_MONTHS[i]} 窗首次在招聘信息中测得该条目`,
              sources: [],
              reviewState: 'auto',
            });
          }
          continue;
        }
        if (a <= 0) continue;
        const r = b / a - 1;
        if (Math.abs(r) < CHANGE_EPS) continue;
        out.push({
          id: `chg:${nodeId}:${REAL_MONTHS[i]}`,
          version: `w${i + 1}`,
          date: REAL_MONTHS[i],
          op: 'modify',
          jobId: '',
          target: { kind, id: nodeId, name },
          field: 'share',
          before: a,
          after: b,
          reason:
            r > 0
              ? `份额较上一窗上升 ${(r * 100).toFixed(1)}%`
              : `份额较上一窗下降 ${(-r * 100).toFixed(1)}%`,
          sources: [],
          reviewState: 'auto',
        });
      }
    }
  };

  scan(SERIES.tasks, tid);
  scan(SERIES.skills, sid);
  scan(SERIES.jobs, jid);

  return out.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
}

/* ==================== 能力年轮 ==================== */

/**
 * 岗位的能力构成随时间的变化。
 *
 * 一环一个季度，不是一个窗口。窗口按月，本批四十六窗；数十环并置时环宽压到
 * 六像素以下，同一项技能的色带在相邻两环之间的角宽差已小于一个像素，
 * "这一项在涨还是在跌"读不出来，而年轮这张图要回答的正是这一问。
 * 按季度并环之后环数降到八，环宽回到十五像素上下，季度之间的构成变动
 * 也大于月度之间，色带的进退因而看得见。
 *
 * 一个季度内各窗的份额取算术平均，再在季内归一 —— 季内窗数不等
 * （2022Q4 只有两窗，2023Q3 有三窗），取平均而非求和，否则窗多的季度整环偏大。
 *
 * 环数另有上限（ANNULI_MAX_RINGS）：数据窗口只增不减，环数不设上限时
 * 这张图会回到数十环的处境 —— 本批四十六窗即十七季，已超出上限。
 * 超出时保留最近的若干季。
 *
 * 与跨季变更清单同源：两者都由同一份季度份额算出，图与清单因而不会各说各话。
 */
/** 一个窗口落在哪个季度。键形如 `2022Q3` */
function quarterOf(w: string): string {
  const y = w.slice(0, 4);
  const q = Math.floor((Number(w.slice(5, 7)) - 1) / 3) + 1;
  return `${y}Q${q}`;
}

/** 环标签：`22Q3`。整年四字在环上标不下，年份取后两位 */
/** 2024Q3 → 24Q3。预测一路的季度键写作 2026-Q3，故先去掉中间那个短横 */
const quarterLabel = (q: string) => {
  const k = q.replace('-', '');
  return `${k.slice(2, 4)}${k.slice(4)}`;
};

/** 观测窗口按季度分组，升序 */
const QUARTERS: { key: string; windows: string[] }[] = (() => {
  const m = new Map<string, string[]>();
  for (const w of REAL_MONTHS) {
    const q = quarterOf(w);
    const arr = m.get(q);
    if (arr) arr.push(w);
    else m.set(q, [w]);
  }
  return [...m.entries()].map(([key, windows]) => ({ key, windows }));
})();

/** 年轮最多画几环。超出时保留最近的若干季 */
const ANNULI_MAX_RINGS = 8;

/** 实际入图的季度，取末尾若干个 */
const RING_QUARTERS = QUARTERS.slice(-ANNULI_MAX_RINGS);

/** 年轮的时间口径，界面上交代时读它 */
export const ANNULI_SCOPE = {
  quarters: RING_QUARTERS.length,
  quartersTotal: QUARTERS.length,
  windows: RING_QUARTERS.reduce((a, q) => a + q.windows.length, 0),
  from: RING_QUARTERS[0]?.windows[0] ?? '',
  to: RING_QUARTERS[RING_QUARTERS.length - 1]?.windows.slice(-1)[0] ?? '',
  maxRings: ANNULI_MAX_RINGS,
};

/* ==================== 下一季度的能力构成预测 ====================

   算法侧另一路时序预测（Chronos-2 零样本）对每个（岗位, 技能）的季度份额序列
   做两遍下一季度预测：一遍只用份额序列自身，一遍另把该技能当季的论文与新闻
   情报占比作为仅过去协变量加入。两者之差即前瞻信号在预测一侧的落点 ——
   为正表示把论文与新闻算进来之后，这一项的下季占比被上调。

   口径与产出见 data-pipeline/build.mjs 的 6.6 节。 */

export interface ForecastItem {
  skillId: string;
  name: string;
  group?: string;
  groupCode?: string;
  /** 单变量预测的下季份额 */
  uni: number;
  /** 加入论文与新闻协变量后的下季份额 */
  cov: number;
  /** 两者之差，即前瞻信号 */
  signal: number;
  /** 上一季的实测份额 */
  lastShare: number;
  /** 该序列可用的历史季度数，少于四季时预测的可信度另作交代 */
  nHist: number;
}

/** 预测覆盖的季度与算法，界面上交代口径时读它 */
export const FORECAST_SCOPE = FORECAST
  ? { quarter: FORECAST.quarter, method: FORECAST.method, jobs: Object.keys(FORECAST.jobs).length }
  : null;

/** 某岗位下一季度各技能的预测份额，按协变量预测降序 */
export function jobForecast(jobCode: string): ForecastItem[] {
  const rows = FORECAST?.jobs[jobCode];
  if (!rows) return [];
  return rows.map(([code, uni, cov, signal, lastShare, nHist]) => {
    const sk = SKILL_BY_CODE.get(code);
    return {
      skillId: sid(code),
      name: sk?.name ?? code,
      group: sk?.group || undefined,
      groupCode: sk?.groupCode || undefined,
      uni,
      cov,
      signal,
      lastShare,
      nHist,
    };
  });
}

export function buildRealAnnuli(): JobAnnuli[] {
  const out: JobAnnuli[] = [];
  const changesByJob = new Map<string, ChangeEvent[]>();

  for (const [jobCode, byWin] of Object.entries(RINGS.jobs)) {
    const job = JOB_BY_CODE.get(jobCode);
    if (!job) continue;

    const rings: AnnulusRing[] = [];
    let prev: Map<string, number> | null = null;
    const changes: ChangeEvent[] = [];

    for (const { key: q, windows } of RING_QUARTERS) {
      /* 季内各窗的份额取算术平均，分母为季内有该岗位记录的窗数，
         再在季内归一：季内窗数不等，求和会让窗多的季度整环偏大 */
      const sum = new Map<string, number>();
      let n = 0;
      for (const w of windows) {
        const rec = byWin[w];
        if (!rec) continue;
        n++;
        for (const [code, share] of rec.skills) sum.set(code, (sum.get(code) ?? 0) + share);
      }
      if (!n) continue;
      const total = [...sum.values()].reduce((a, b) => a + b, 0) || 1;
      const skills: [string, number][] = [...sum.entries()]
        .map(([code, v]) => [code, v / total] as [string, number])
        .sort((a, b) => b[1] - a[1]);
      const w = windows[windows.length - 1];
      const cur = new Map(skills);
      rings.push({
        version: quarterLabel(q),
        date: windows.length > 1 ? `${windows[0]} — ${w}` : w,
        slices: skills.map(([code, share]) => {
          const sk = SKILL_BY_CODE.get(code);
          const before = prev?.get(code);
          const status: EdgeStatus =
            before === undefined
              ? 'candidate'
              : share > before * 1.03
                ? 'strengthening'
                : share < before * 0.97
                  ? 'weakening'
                  : 'active';
          return {
            skillId: sid(code),
            name: sk?.name ?? code,
            share,
            status,
            origin: (sk?.origin ?? 'base') as 'base' | 'overlay',
            /* 能力组随切片带出：年轮按组分色相，而组的归属只有体系知道。
               叠层技能（PS-）在体系内尚无归属，两字段为空，画法另有一档中性色。 */
            group: sk?.group || undefined,
            groupCode: sk?.groupCode || undefined,
          };
        }),
      });

      if (prev) {
        for (const [code, share] of cur) {
          const before = prev.get(code);
          const sk = SKILL_BY_CODE.get(code);
          const name = sk?.name ?? code;
          if (before === undefined) {
            changes.push({
              id: `ann:${jobCode}:${code}:${q}:add`,
              version: quarterLabel(q),
              date: w,
              op: 'add',
              jobId: jid(jobCode),
              target: { kind: 'skill', id: sid(code), name },
              field: 'share',
              before: 0,
              after: share,
              reason: `${q} 该项进入本岗位技能构成的前 ${RING_TOP} 位`,
              sources: [],
              reviewState: 'auto',
            });
          } else if (Math.abs(share / before - 1) >= CHANGE_EPS) {
            const r = share / before - 1;
            changes.push({
              id: `ann:${jobCode}:${code}:${q}`,
              version: quarterLabel(q),
              date: w,
              op: 'modify',
              jobId: jid(jobCode),
              target: { kind: 'skill', id: sid(code), name },
              field: 'share',
              before,
              after: share,
              reason:
                r > 0
                  ? `在本岗位构成中的份额较上一季上升 ${(r * 100).toFixed(1)}%`
                  : `在本岗位构成中的份额较上一季下降 ${(-r * 100).toFixed(1)}%`,
              sources: [],
              reviewState: 'auto',
            });
          }
        }
        for (const [code, before] of prev) {
          if (cur.has(code)) continue;
          const sk = SKILL_BY_CODE.get(code);
          changes.push({
            id: `ann:${jobCode}:${code}:${q}:rm`,
            version: quarterLabel(q),
            date: w,
            op: 'remove',
            jobId: jid(jobCode),
            target: { kind: 'skill', id: sid(code), name: sk?.name ?? code },
            field: 'share',
            before,
            after: 0,
            reason: `${q} 该项退出本岗位技能构成的前 ${RING_TOP} 位`,
            sources: [],
            reviewState: 'auto',
          });
        }
      }
      prev = cur;
    }

    if (!rings.length) continue;

    /* 最外再加一环预测环：下一季度的构成由协变量预测给出，按同一口径在环内归一，
       条目数与实测环取齐。predicted 一置，年轮上这一环画成虚线圈，图注里另有一行
       —— 它不是观测，不能与内圈同样读。 */
    const fc = jobForecast(jobCode);
    if (fc.length) {
      const top = fc.slice(0, RING_TOP);
      const tot = top.reduce((a, x) => a + x.cov, 0) || 1;
      const last = prev;
      rings.push({
        version: quarterLabel(FORECAST!.quarter),
        date: FORECAST!.quarter,
        predicted: true,
        slices: top.map((x) => {
          const share = x.cov / tot;
          const before = last?.get(x.skillId.slice(x.skillId.indexOf(':') + 1));
          return {
            skillId: x.skillId,
            name: x.name,
            share,
            status: (before === undefined
              ? 'candidate'
              : share > before * 1.03
                ? 'strengthening'
                : share < before * 0.97
                  ? 'weakening'
                  : 'active') as EdgeStatus,
            origin: 'base' as const,
            group: x.group,
            groupCode: x.groupCode,
          };
        }),
      });
    }

    changesByJob.set(jid(jobCode), changes);
    out.push({ jobId: jid(jobCode), jobName: job.name, rings, changes });
  }

  return out.sort((a, b) => (JOB_BY_CODE.get(b.jobId.slice(2))?.hits ?? 0) - (JOB_BY_CODE.get(a.jobId.slice(2))?.hits ?? 0));
}

/** 年轮取的是各岗位技能构成的前若干位，与 data-pipeline 的截断口径一致 */
const RING_TOP = 15;

/* ==================== 熟练度 ==================== */

/** 各技能在末窗的要求档位分布，P1 至 P4 与 U 五档 */
export const SKILL_PROFICIENCY = (() => {
  const w = PROF.byWindow[REAL_NOW];
  if (!w) return new Map<string, { name: string; n: number; levels: Record<string, number> }>();
  return new Map(Object.entries(w.skills));
})();

/** 熟练度覆盖情况：入图的各项技能中有多少项拿得到档位分布 */
export const PROFICIENCY_COVERAGE = {
  total: GRAPH.nodes.skills.length,
  covered: SKILL_PROFICIENCY.size,
  rubric: PROF.rubric,
  nJds: PROF.byWindow[REAL_NOW]?.nJds ?? 0,
  /** 有岗位一层档位分布的（岗位, 技能）对数 */
  jobSkillPairs: Object.values(JDSTATS.byJob).reduce(
    (a, r) => a + Object.keys(r.skillProf ?? {}).length,
    0,
  ),
  /** 有档位分布的技能点数，即样本量过阈值的那一部分 */
  skillpoints: Object.keys(JDSTATS.skillpointProf ?? {}).length,
};

/* ---------------- 要求程度的档位构成 ----------------

   算法侧按 P1–P4 记要求深度，另设 U 档：原文点到该项，但没写要到什么程度。
   界面分三档加一档"无法确定"，两者按深度对齐 —— P1 记了解，P2 记熟练，
   P3 与 P4 同记精通（P4 全样本占比不足百分之一，单列一档读不出差别），
   U 记无法确定。

   档位分布有两个粒度，按可得的细一级取：
     岗位 × 技能   汇总表逐条给出，实测；下同一岗位下的技能点沿用其父技能的构成
     技能         prof.json 按窗给出，实测；某岗位在该技能上无样本时回落到它

   界面上一条（岗位，技能点）关系只落一档，而实测给的是一个分布，
   故按确定性哈希从该分布中取一档：同一对关系每次刷新落在同一档，
   聚合起来的四档比例即实测比例。这一步是分配而非观测，登记为 derived。 */

/** 算法五档在界面四档上的落点，下标同 explore.PROF_LEVELS */
const PROF_BAND_OF: Record<string, 0 | 1 | 2 | 3> = { P1: 0, P2: 1, P3: 2, P4: 2, U: 3 };

export type ProfShare = [number, number, number, number];

function toProfShare(levels: Record<string, number> | undefined): ProfShare | null {
  if (!levels) return null;
  const out: ProfShare = [0, 0, 0, 0];
  let sum = 0;
  for (const [band, v] of Object.entries(levels)) {
    const i = PROF_BAND_OF[band];
    if (i === undefined || !(v > 0)) continue;
    out[i] += v;
    sum += v;
  }
  if (sum <= 0) return null;
  return out.map((v) => v / sum) as ProfShare;
}

/** 技能一层的档位构成，末窗口径 */
const PROF_BY_SKILL = new Map<string, ProfShare>();
for (const [code, v] of SKILL_PROFICIENCY) {
  const s = toProfShare(v.levels);
  if (s) PROF_BY_SKILL.set(code, s);
}

/** （岗位, 技能）一层的档位构成 */
const PROF_BY_JOB_SKILL = new Map<string, ProfShare>();
for (const [job, rec] of Object.entries(JDSTATS.byJob)) {
  for (const [code, levels] of Object.entries(rec.skillProf ?? {})) {
    const s = toProfShare(levels);
    if (s) PROF_BY_JOB_SKILL.set(`${job}|${code}`, s);
  }
}

/**
 * 某岗位对某技能的要求档位构成。岗位一层无样本时回落到技能一层，
 * 两层都无则返回 null，由调用方决定怎么标。
 */
export function profShareOf(jobName: string, skillCode: string): ProfShare | null {
  return PROF_BY_JOB_SKILL.get(`${jobName}|${skillCode}`) ?? PROF_BY_SKILL.get(skillCode) ?? null;
}

/* （岗位, 技能）一层的覆盖率：该岗位的招聘信息里写到这项技能的条数占其全部条数
   的比例。分子取档位分布各档之和 —— 与要求程度同出一份 skillProf，故两者的
   分母、口径与观测窗口一致；分母取该岗位的招聘信息条数。

   这一份是岗位与技能之间唯一逐条统计出来的关联强度。岗位在各技能上的构成占比
   （JobRow.vector）由岗位—任务—技能两跳的边权汇总得到，五十余项技能摊下来
   每项都落在百分之一到三点五之间，跨岗位比较时读不出高下；覆盖率则从零点几
   到零点九九，"这个岗位是否要求这项技能"因而有分得开的读数。 */
const COV_BY_JOB_SKILL = new Map<string, number>();
for (const [job, rec] of Object.entries(JDSTATS.byJob)) {
  const n = rec.n ?? 0;
  if (n <= 0) continue;
  for (const [code, levels] of Object.entries(rec.skillProf ?? {})) {
    let sum = 0;
    for (const v of Object.values(levels)) sum += v;
    if (sum > 0) COV_BY_JOB_SKILL.set(`${job}|${code}`, Math.min(1, sum / n));
  }
}

/** 某岗位的招聘信息里写到某技能的条数占比。无实测记录时返回 0 */
export function skillCoverageOf(jobName: string, skillCode: string): number {
  return COV_BY_JOB_SKILL.get(`${jobName}|${skillCode}`) ?? 0;
}

/* ==================== 构建批次 ==================== */

/**
 * 逐窗的构建批次。
 *
 * 各环节的输入输出量取自该窗的构建记录：招聘信息的扫描量与采样量、
 * 四类边的条数、技能点的增量、叠层信号的条数，均为实测。
 * 各环节耗时算法侧未记录，故不给。
 */
export function buildRealLoops(): LoopRun[] {
  return REAL_MONTHS.slice()
    .reverse()
    .map((w, revIdx) => {
      const wi = REAL_MONTHS.length - 1 - revIdx;
      const c = SERIES.counts[w];
      const d = DELTA.byWindow[w];
      const prevC = wi > 0 ? SERIES.counts[REAL_MONTHS[wi - 1]] : undefined;
      /* 叠层证据的来源文档数：论文与新闻各自去重后的篇数。
         算法侧的逐窗叠层是累积的 —— 入场过的信号在其后每一窗都仍在表内，
         故此处读到的是截至该窗的累计篇数，而非该窗新增 */
      const docs = { papers: new Set<string>(), news: new Set<string>() };
      const countDocs = (ev: DeltaEvidence) => {
        if (ev.src === 'papers') docs.papers.add(ev.doc);
        else if (ev.src === 'news') docs.news.add(ev.doc);
      };
      for (const [k] of d?.items ?? []) {
        for (const ev of DELTA.entities[k]?.evidence ?? []) countDocs(ev);
      }
      for (const [k] of d?.strengthenings ?? []) {
        for (const ev of DELTA.strengthenDefs[k]?.evidence ?? []) countDocs(ev);
      }
      const edgeTotal = Object.values(c?.edges ?? {}).reduce((a, b) => a + b, 0);
      const prevEdgeTotal = Object.values(prevC?.edges ?? {}).reduce((a, b) => a + b, 0);
      const spDelta = (c?.skillpoints ?? 0) - (prevC?.skillpoints ?? 0);
      const isBaseline = BASELINE_WINDOWS.includes(w);

      return {
        id: `W-${w}`,
        version: `w${wi + 1}`,
        startedAt: `${w}-01`,
        batch: {
          jd: c?.jdScanned ?? 0,
          paper: docs.papers.size,
          news: docs.news.size,
        },
        agents: [
          {
            name: 'Collector',
            role: '采集与降采样',
            status: 'done' as const,
            durationMs: 0,
            input: `当窗招聘信息 ${(c?.jdScanned ?? 0).toLocaleString()} 条`,
            output: `按成本预算降采样得 ${(c?.jdSampled ?? 0).toLocaleString()} 条数据基面`,
            metric: `剔除非信息技术岗 ${c?.droppedNonIt ?? 0} 条`,
          },
          {
            name: 'Extractor',
            role: '句级抽取与熟练度定级',
            status: 'done' as const,
            durationMs: 0,
            input: `数据基面 ${(c?.jdSampled ?? 0).toLocaleString()} 条`,
            output: `岗位命中 ${c?.jobs ?? 0} 个 · 任务 ${c?.tasks ?? 0} 项 · 技能 ${c?.skills ?? 0} 项`,
            metric: `技能点累计 ${(c?.skillpoints ?? 0).toLocaleString()} 个`,
          },
          {
            name: 'Graph Builder',
            role: '基图边计算',
            status: 'done' as const,
            durationMs: 0,
            input: '当窗抽取结果与上一窗基图',
            output: `四类边合计 ${edgeTotal.toLocaleString()} 条`,
            metric: `较上一窗 ${prevEdgeTotal ? (edgeTotal - prevEdgeTotal >= 0 ? '+' : '') + (edgeTotal - prevEdgeTotal) : '基准窗'} · 跨窗衰减 α=${GRAPH.alpha}`,
          },
          {
            name: 'Evolution Analyzer',
            role: '叠层信号计算',
            status: isBaseline ? ('idle' as const) : ('done' as const),
            durationMs: 0,
            input: isBaseline
              ? '基准窗只建基图，不建叠层'
              : `论文 ${docs.papers.size} 篇 · 新闻 ${docs.news.size} 条`,
            output: isBaseline
              ? '本窗无叠层产物'
              : `新实体 ${d?.items.length ?? 0} 项 · 既有条目增强 ${d?.strengthenings.length ?? 0} 条`,
            metric: isBaseline ? '——' : `岗位关联边 ${d?.links.length ?? 0} 条`,
          },
          {
            name: 'Quality Guardian',
            role: '生命周期门控',
            status: isBaseline ? ('idle' as const) : ('done' as const),
            durationMs: 0,
            input: isBaseline ? '——' : `叠层实体 ${d?.items.length ?? 0} 项`,
            output: isBaseline
              ? '——'
              : `参与合成 ${DELTA.newEntities.filter((e) => e.participates).length} 项`,
            metric: `本窗转正 ${DELTA.graduated} 项`,
          },
        ],
        deltas: {
          nodesAdded: Math.max(0, spDelta),
          edgesAdded: Math.max(0, edgeTotal - prevEdgeTotal),
          edgesStrengthened: d?.strengthenings.length ?? 0,
          edgesWeakened: 0,
          edgesRemoved: Math.max(0, prevEdgeTotal - edgeTotal),
          overlayApplied: d?.links.length ?? 0,
        },
      };
    });
}

/* ==================== 词表种子 ====================

   下游若干处（人岗匹配的技能点对齐、简历真实性核验、搜索）读的是词表种子
   而非图谱节点。图谱产物接入后，种子须与图谱同源，否则简历里写的 Python
   在图谱里查得到、在种子里查不到，对齐环节会把它判成"未落入体系"。

   两层的规模在此归位：技能层为体系内的各项技能（此前为能力组一层），
   技能点层为算法侧的开放集合（此前为技能一层）。 */

/** 图谱里的技能点名，供简历抽取判断某个写法是否已在体系内 */
export const SKILLPOINT_NAMES = new Set(GRAPH.nodes.skillpoints.map((s) => s.id));

/** 技能名 → 编码。简历里的写法若归并到某项技能而非技能点，落点即该技能 */
export const SKILL_CODE_BY_NAME = new Map(GRAPH.nodes.skills.map((s) => [s.name, s.id]));

/** 任务 → 它要求的技能名，按权重降序。任务到技能的映射由算法侧产出 */
const TASK_SKILLS = (() => {
  const by = new Map<string, { name: string; w: number }[]>();
  for (const e of GRAPH.edges.taskSkill) {
    const sk = SKILL_BY_CODE.get(e.t);
    if (!sk) continue;
    const arr = by.get(e.s) ?? [];
    arr.push({ name: sk.name, w: e.e });
    by.set(e.s, arr);
  }
  const out = new Map<string, string[]>();
  for (const [k, arr] of by) {
    out.set(
      k,
      arr.sort((a, b) => b.w - a.w).map((x) => x.name),
    );
  }
  return out;
})();

/** 技能层种子：入图的各项技能，一级归属取其所属能力维度 */
export const GRAPH_SKILL_SEEDS: SkillSeed[] = GRAPH.nodes.skills.map((s) => ({
  name: s.name,
  category: s.dim,
  realCount: SP_COUNT_BY_SKILL.get(s.id) ?? 0,
}));

/** 技能点层种子：算法侧的开放集合，归属其权重最高的那项技能 */
export const GRAPH_SKILLPOINT_SEEDS: SkillPointSeed[] = GRAPH.nodes.skillpoints.map((sp) => ({
  name: sp.id,
  skills: [SKILL_OF_SP.get(sp.id) ?? ''],
  category: SKILL_OF_SP.get(sp.id) ?? '',
  /* 成熟度分档算法侧没有这一维，留 1 只为满足契约，界面上已不读它 */
  level: 1,
  firstSeen: sp.from,
  emerging: sp.origin === 'overlay',
  skillType: SP_TYPE.get(sp.id) ?? 'hard',
  definition: sp.def,
}));

/** 任务层种子：27 项基准任务与叠层新任务 */
export const GRAPH_TASK_SEEDS: TaskSeed[] = GRAPH.nodes.tasks.map((t) => ({
  name: t.name,
  category: TASK_CATEGORY,
  /* 该任务要求的技能，按任务到技能边的权重降序 */
  skills: TASK_SKILLS.get(t.id) ?? [],
  firstSeen: t.origin === 'overlay' ? (t.born ?? REAL_MONTHS[0]) : REAL_MONTHS[0],
  emerging: t.origin === 'overlay',
  definition: t.def,
}));

/* ==================== 规模读数 ==================== */

/** 一层里基准与叠层各有多少。两者不可混报：基准条目已被招聘市场确认，
    叠层条目只有论文与新闻支持，尚在等待市场确证 */
const countBase = <T extends { origin?: string }>(rows: T[]) =>
  rows.filter((r) => r.origin !== 'overlay').length;

/** 界面上凡陈述本批数据规模，一律从这里取，避免各页说法不一 */
export const REAL_GRAPH_STATS = {
  windows: REAL_MONTHS.length,
  from: REAL_MONTHS[0],
  to: REAL_NOW,
  /* 首末窗之间的自然月数。窗口序列不连续时它大于窗口数，
     界面上写区间跨度时读它，写观测次数时读 windows */
  spanMonths:
    (Number(REAL_NOW.slice(0, 4)) - Number(REAL_MONTHS[0].slice(0, 4))) * 12 +
    (Number(REAL_NOW.slice(5, 7)) - Number(REAL_MONTHS[0].slice(5, 7))) +
    1,
  /** 区间内没有独立观测的月份 */
  gapMonths: MANIFEST.windowGaps?.reduce((a, g) => a + g.months, 0) ?? 0,
  jobs: GRAPH.nodes.jobs.length,
  jobsInTaxonomy: MANIFEST.counts.jobsInTaxonomy,
  tasks: GRAPH.nodes.tasks.length,
  skills: GRAPH.nodes.skills.length,
  skillpoints: GRAPH.nodes.skillpoints.length,
  /* 基准体系的规模：已被招聘市场确认的那一批 */
  base: {
    jobs: countBase(GRAPH.nodes.jobs),
    tasks: countBase(GRAPH.nodes.tasks),
    skills: countBase(GRAPH.nodes.skills),
    skillpoints: countBase(GRAPH.nodes.skillpoints),
  },
  /* 叠层的规模：论文与新闻提出、尚未被市场确证的那一批 */
  overlay: {
    jobs: GRAPH.nodes.jobs.length - countBase(GRAPH.nodes.jobs),
    tasks: GRAPH.nodes.tasks.length - countBase(GRAPH.nodes.tasks),
    skills: GRAPH.nodes.skills.length - countBase(GRAPH.nodes.skills),
    skillpoints: GRAPH.nodes.skillpoints.length - countBase(GRAPH.nodes.skillpoints),
  },
  edges: MANIFEST.counts.edges,
  edgeTotal: Object.values(MANIFEST.counts.edges).reduce((a, b) => a + b, 0),
  jdSampled: MANIFEST.counts.jdSampled,
  jdSummaryRows: MANIFEST.counts.jdSummaryRows,
  deltaEntities: MANIFEST.counts.deltaEntities,
  graduated: DELTA.graduated,
  fingerprint: MANIFEST.source.fingerprint,
  taxonomy: MANIFEST.taxonomy,
  absent: MANIFEST.absent,
  dropped: MANIFEST.dropped,
};
