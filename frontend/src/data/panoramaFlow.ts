/* ============================================================
   全景图谱 —— 岗位能力流图的取数层

   ------------------------------------------------------------
   这一层回答的是赛题对全景图谱那一句规定：

     "展示领域内岗位的能力要求，颗粒度到技能点级别，
       可以按技术栈和级别切换视图"

   逐字拆开来看，图上必须同时立得住四件事：
     ① 领域内岗位 —— 复数。一次只讲一个岗位的图回答不了"领域内"，
        所以岗位在这张图上是一整段，不是一个下拉框选出来的单一主语。
     ② 能力要求  —— 条长表达的是"要求有多高"，不是"体系里有多少东西"。
     ③ 到技能点  —— 最细一层是技能点本身，不是它们的归并组。
     ④ 两条切换轴 —— 技术栈落在能力体系自身的两维十组上；
        级别落在岗位职级（初 / 中 / 高）上，两者互不替代。

   编排沿用 JobViz（Wang et al., Visual Informatics 2024）Figure 2(A)
   的三段并排：三列条形图 + 两组连线。换掉的只有各段的对象 ——
   论文是"技能 / 岗位 / 岗位属性"，这里是图谱自己的三层：

     A1 能力体系（能力维度 → 能力组 → 技能点）  条长 = 要求强度
     A2 核心任务                                条长 = 要求强度
     A3 岗位                                    条长 = 该岗位在当前技术栈下的要求总量

   与职业探索页的同一张图形不重复：那一页的方向是"从能力出发能走到哪些
   岗位"，第三段是求职者关心的城市 / 学历 / 薪资；这一页的方向是"岗位
   要求分解成什么"，三段正是图谱的岗位—任务—能力组—技能点四层本身。

   ------------------------------------------------------------
   时间维

   算法侧按月返回岗位 / 任务 / 能力组 / 技能点四层的数据（PrismTimeline）。
   这里把它落成每一行一条逐月序列，而不是"每个月一份模型"：
   行的位置、分组与排序一律按末个实测月一次算定，拖时间轴时只有条长在变。
   否则同一项能力每换一个月就换一行，眼睛追不住，时间轴反而读不出变化。

   ------------------------------------------------------------
   口径与来源

   要求强度沿两条路径聚合，与匹配页的岗位向量、能力剖面同源：
     路径一  J → S       JD 里直接列出的能力要求
     路径二  J → T → S   岗位的核心任务反过来要求的能力
   技能点一层再沿 S-SP 边把能力组的要求按组内权重分下去。

   图谱产物接入后，四类边与月度序列均为算法侧实测：每条边各带一个由招聘信息
   统计出的基图权重与一个叠加前瞻修正后的合成权重，序列取自各观测窗口的逐月份额。

   两条切换轴本批亦已落到实测：职级倾斜由汇总表逐条的职级与能力要求算出
   （LEVEL_TILT_OF → realGraph.LEVEL_TILT_MEASURED），条内分段的要求程度
   由（岗位, 技能）一层的档位分布给出（explore.profBand → realGraph.profShareOf），
   技能点沿用其父技能的构成。两处各有其限：职级列约五成半有值；
   档位分布产出到技能一层，技能点一层的构成系推导而非观测。
   任务一段无对应的档位分布，仍走 TASK_UNSPECIFIED。口径见 pages/Panorama.tsx。
   ============================================================ */

import type { GraphEdge, GraphNode, SkillType } from '@/types/graph';
import { edgesFrom, getDataset } from './generator';
import { exploreBase } from './explore';
import { ORPHAN_CLUSTER, SKILL_GROUPS } from './realTaxonomy';
import { PROF_UNKNOWN, profBandFrom } from './explore';

/** 节点 id 形如 `S:T-SW-01`，档位表按体系编码索引，此处剥掉层前缀 */
const codeOf = (id: string | undefined) => (id ? id.slice(id.indexOf(':') + 1) : undefined);
import { rand01 } from '@/utils/rng';
import { LEVEL_TILT_OF, type JobLevel, type ProfileScope } from './jobProfile';
import { TECH_STACKS, jobInStack, profShareOf, type ProfShare } from './realGraph';

/* ==================== 技术栈切换轴 ==================== */

export interface StackOption {
  /** 取值即汇总表里的技术栈原名，可回源核对 */
  v: string;
  label: string;
  /** 该栈覆盖的岗位数 */
  jobs: number;
  /** 该栈覆盖的招聘信息条数 */
  posts: number;
}

/**
 * 赛题"按技术栈切换视图"的落点。
 *
 * 这一维取自招聘信息汇总表本身：逐条招聘信息标有其技术栈，按岗位归一为占比后
 * 得到"这个岗位的招聘里有多大一份属于该栈"。八个取值与汇总表逐字一致，
 * 拿去核源文件找得到（口径见 realGraph 的技术栈一节）。
 *
 * 此前这一轴落在能力体系的两维九组上。那是能力的分类，不是技术栈：界面上
 * 写作技术栈、筛出来的却是能力组，读者据此答不了"这个技术方向上在招什么岗位"。
 * 切换的因此是岗位这一头 —— 选中一个栈，图上只留该方向上确在招人的岗位，
 * 中段与右段随之只剩这批岗位要求的任务与能力。岗位类别是另一根轴，两者可叠加。
 */
export function stackOptions(scope?: ProfileScope): StackOption[] {
  const all = TECH_STACKS.map((s) => ({ v: s.name, label: s.name, jobs: s.jobs, posts: s.posts }));
  if (!scope) return all;
  /* 只列所选岗位范围内确有岗位的方向。

     两根轴此前各自成立：岗位类别在左段筛岗位，技术栈也在左段筛岗位，但候选项
     取的是全库八个方向。于是"硬件与半导体 × AI/ML 与数据智能"这类组合选得出来
     而交集为空，图整幅空掉，且空的原因只能由图外那句口径去猜。收窄之后，
     下拉里列得出的每一项都至少留得下一个岗位，两根轴叠加不再出空图。 */
  const jobs = jobsInScope({ ...scope }, getDataset().nodes, null);
  const n = new Map<string, number>();
  for (const j of jobs) {
    for (const s of TECH_STACKS) if (jobInStack(j.name, s.name)) n.set(s.name, (n.get(s.name) ?? 0) + 1);
  }
  return all
    .filter((o) => n.has(o.v))
    .map((o) => ({ ...o, jobs: n.get(o.v) ?? 0 }))
    /* 按本范围内的岗位数重排：全库的名次与所选大类内的名次未必一致，
       沿用全库次序时，下拉里排在最前的可能是这一类里只剩两三个岗位的方向 */
    .sort((a, b) => b.jobs - a.jobs || b.posts - a.posts);
}

/* ==================== 行 ==================== */

export type FlowKind = 'skillpoint' | 'skill' | 'task' | 'job';

export interface FlowRow {
  id: string;
  kind: FlowKind;
  name: string;
  /** 括号分组的外层：技能点/能力组为能力维度，岗位为岗位类别，任务为空 */
  dim: string;
  /** 括号分组的内层：技能点为所属能力组，其余为自身 */
  group: string;
  skillType?: SkillType;
  definition?: string;

  /** 末个实测月的要求强度。排序、标度、括号分组全部按它一次算定 */
  demand: number;
  /**
   * 逐月要求强度，长度与 months 对齐。
   * null 与 0 不是一回事：null = 当月该条目还不在图谱里（画成空轨道），
   * 0 = 当月测得为零。混成一种就读不出某一项是何时长出来的。
   */
  series: (number | null)[];
  /** 要求程度四档下各有多少个岗位（了解 / 熟练 / 精通 / 无法确定），口径内计数 */
  prof: [number, number, number, number];
  /** 要求里由前瞻信号贡献的那一截，占 demand 的比例 0–1 */
  forwardShare: number;
  /** 首次进入招聘要求的月份下标；-1 = 至今仅有论文与新闻支持 */
  confirmedAt: number;
  /** 指向下一段的关系：目标行 id → 权重 0–1。岗位段为空 */
  links: Map<string, number>;

  /* ---- 岗位行专有 ---- */
  /** 该岗位能力要求的软硬构成，两项和为 1 */
  mix?: Record<SkillType, number>;
  /** 招聘信息条数 */
  posts?: number;
  /** 招聘平台原始职能名 */
  funtypes?: string[];
}

export interface FlowModel {
  /** 技能点行 —— 赛题点名的最细一层，主图上作为技能行的下钻落点 */
  itemRows: FlowRow[];
  /** 技能行 —— 主图第三段画的就是它 */
  groupRows: FlowRow[];
  /**
   * 技能编码 → 该技能下的技能点行，按需求强度降序。
   * 末位可能是一条合计行（id 以 agg: 起首），代表未单列的其余项。
   */
  itemsBySkill: Map<string, FlowRow[]>;
  taskRows: FlowRow[];
  jobRows: FlowRow[];
  months: string[];
  /** 外推段的起始下标；>= months.length 表示整段都是实测 */
  forecastFrom: number;
  /** 末个实测月的下标 */
  observedLast: number;
  /** 口径内的岗位总数（jobRows 可能因为上限只画了一部分） */
  scopeJobs: number;
  /** 当前口径下画出的技能数 / 技能点数 */
  groupsInStack: number;
  itemsInStack: number;
  /** 按长尾阈值滤掉的行数，图外如实写出 */
  itemsTrimmed: number;
  tasksTrimmed: number;
}

export interface FlowOptions {
  scope: ProfileScope;
  level: JobLevel;
  /** 技术栈，null = 不限 */
  stack: string | null;
  /** 技能点软硬分类筛选；空数组视同全选 */
  skillTypes: SkillType[];
}

/* ==================== 计算 ==================== */

const λ = 1; // 前瞻修正系数，与匹配页的岗位向量同口径
const eff = (e: GraphEdge) => e.baseWeight + λ * e.deltaWeight;

/** 要求程度的分档与职业探索页同一个函数、同一个 key（岗位 id | 条目 id），
    所以同一对关系在两页落在同一档。档位构成的取数见 explore.profBand。 */
const bandOrUnknownAt = (r: number, key: string, share: number): 0 | 1 | 2 | 3 =>
  rand01(`${key}|deg`) < share ? PROF_UNKNOWN : r >= 0.66 ? 2 : r >= 0.33 ? 1 : 0;

/** 任务层没有软硬分类，且档位构成只产出到技能一层，任务一层无从对应，
    未写明的比例取硬 0.18 与软 0.55 之间的一档 */
const TASK_UNSPECIFIED = 0.35;

/**
 * 长尾阈值：一段之内，要求强度不足本段峰值这个比例的行不画。
 *
 * 一个大类几十个岗位聚起来，总会有几项能力是被其中一两个岗位在某个能力组上
 * 的残值带进来的。它们的条在图上只有一两个像素 —— 与"条太短时至少留一像素"
 * 那条兜底画出来的一样长，读者分不出谁强谁弱，只看到一列长短不齐的碴儿。
 * 滤掉的条数由 itemsTrimmed / tasksTrimmed 报给视图层，在图外如实写出，
 * 不让"画出来的"被当成"全部"。
 */
export const TAIL_SHARE = 0.025;

/** 一项技能下钻时展开的技能点条数，超出的合并为一条 */
const SKILL_ITEM_TOP = 12;

function trimTail<T extends { demand: number }>(rows: T[]): { kept: T[]; trimmed: number } {
  const peak = Math.max(...rows.map((r) => r.demand), 1e-9);
  const kept = rows.filter((r) => r.demand >= peak * TAIL_SHARE);
  return { kept, trimmed: rows.length - kept.length };
}

/**
 * 口径内的岗位 —— 这里要全量，不采样。
 *
 * 能力剖面那一层（jobProfile）为了求平均只取每类前八个，因为八个与全取
 * 差在小数点后两位。流图不同：岗位是图上的一整段，段里少画一个岗位，
 * 读者就少看到一个岗位，"展示领域内岗位"这句话直接落空。
 * 图上画多少条由视图层的上限决定，并在图外如实写出总数。
 */
function jobsInScope(scope: ProfileScope, nodes: GraphNode[], stack: string | null): GraphNode[] {
  const all = nodes.filter((x) => x.kind === 'job');
  /* 技术栈按岗位筛，不按能力筛：这一维记的是"这个岗位的招聘里有多大一份属于
     该栈"，取值在岗位这一层。选中一个栈之后，中段与右段随之只剩这批岗位
     要求的任务与能力 —— 那正是"这个技术方向要求什么能力"这一问的答案。
     单选一个岗位时不再叠加：范围已经收到一个岗位，再筛只会筛成空图。 */
  const byStack = (xs: GraphNode[]) => (stack ? xs.filter((x) => jobInStack(x.name, stack)) : xs);
  if (scope.kind === 'job') {
    const one = all.find((x) => x.id === scope.id);
    return one ? [one] : [];
  }
  if (scope.kind === 'category') {
    return byStack(all.filter((x) => (x.topCategory || ORPHAN_CLUSTER) === scope.id));
  }
  return byStack(all);
}

const cache = new Map<string, FlowModel>();

export function buildFlow(o: FlowOptions): FlowModel {
  const types = o.skillTypes.length ? o.skillTypes : (['hard', 'soft'] as SkillType[]);
  const key = [o.scope.kind, o.scope.id ?? '', o.level, o.stack ?? '*', [...types].sort().join('')].join('|');
  const hit = cache.get(key);
  if (hit) return hit;

  const d = getDataset();
  const base = exploreBase();
  const timeline = d.prismTimeline;
  const months = timeline.months;
  const forecastFrom = timeline.forecastFrom
    ? months.indexOf(timeline.forecastFrom)
    : months.length;
  const observedLast = Math.max(0, (forecastFrom > 0 ? forecastFrom : months.length) - 1);

  const nodeById = d.nodeById;
  const jobs = jobsInScope(o.scope, d.nodes, o.stack);

  /* ---- 技能点 → 所属技能 ----
     技能到技能点是多对多：Linux 既是操作系统与系统管理下的条目，也出现在
     系统部署交付与运维下。归属取权重最高的那一条边，读作"这个工具主要属于
     哪一项技能"，与 realGraph 的 SKILL_OF_SP 同口径。
     按遍历顺序覆盖则归属由边的排列先后决定，同一个技能点每次可能落在不同技能下。 */
  const groupOfItem = new Map<string, string>();
  {
    const best = new Map<string, number>();
    for (const e of d.edges) {
      if (e.kind !== 'S-SP') continue;
      const w = e.effectiveWeight;
      if (w > (best.get(e.target) ?? -1)) {
        best.set(e.target, w);
        groupOfItem.set(e.target, e.source);
      }
    }
  }

  /** 该技能点在筛选后留不留 */
  const keptCache = new Map<string, boolean>();
  const itemKept = (iid: string) => {
    let k = keptCache.get(iid);
    if (k === undefined) {
      const n = nodeById.get(iid);
      k = !!n && types.includes(n.skillType ?? 'hard');
      keptCache.set(iid, k);
    }
    return k;
  };

  /* ---- 逐岗位的技能点要求权重（含职级倾斜） ----
     倾斜按能力组给系数、再按该岗位自身最高项重新归一：要求强度三档问的是
     "这项能力在这个岗位的要求里排多高"，换一档职级，排位本来就该跟着变。 */
  /* 倾斜系数只随技能点所属的技能与所选职级变，与岗位无关：先按技能点算一遍，
     否则一百余个岗位各自把上万个技能点的归属再查一遍。 */
  const tiltOfItem = new Map<string, number>();
  const tiltFor = (iid: string) => {
    let t = tiltOfItem.get(iid);
    if (t === undefined) {
      /* 系数按能力组给（十组），而 groupOfItem 上溯到的是技能一层（五十余项）。
         此处原先拿技能名去查表，键一个也对不上，十档系数一律回落到持平 ——
         三档职级因而算出完全相同的一份取数，换职级整张图纹丝不动。
         能力组名在技能节点的 topCategory 上。 */
      t = LEVEL_TILT_OF(nodeById.get(groupOfItem.get(iid) ?? '')?.topCategory ?? '', o.level);
      tiltOfItem.set(iid, t);
    }
    return t;
  };
  const tiltedItems = new Map<string, Map<string, number>>();
  for (const job of jobs) {
    const row = base.jobs.get(job.id);
    if (!row) continue;
    const w = new Map<string, number>();
    let max = 1e-9;
    for (const [iid, v] of row.items) {
      const t = v * tiltFor(iid);
      w.set(iid, t);
      if (t > max) max = t;
    }
    for (const [iid, v] of w) w.set(iid, v / max);
    tiltedItems.set(job.id, w);
  }

  /* ---- A1 技能点行 ---- */
  interface Acc {
    demand: number;
    fwd: number;
    prof: [number, number, number, number];
  }
  /* 技能点行不带档位构成：熟练度的实测粒度止于“某岗位对某技能”，算法侧不产出
     技能点一层的档位读数。此前这一层由父技能的构成推下来，图上一根技能点条因而
     分成四段 —— 那四段说的是父技能的程度分布，读的人却只能把它读成这个技能点
     自己的程度要求。prof 一律留空，图上与浮层各自据此只画量、不画档。 */
  const itemAcc = new Map<string, Acc>();
  for (const [, w] of tiltedItems) {
    for (const [iid, v] of w) {
      if (v <= 0.001 || !itemKept(iid)) continue;
      let a = itemAcc.get(iid);
      if (!a) itemAcc.set(iid, (a = { demand: 0, fwd: 0, prof: [0, 0, 0, 0] }));
      a.demand += v;
    }
  }

  /* 前瞻占比按 S-SP 边的 Δw 取 —— 一项还没进 JD 的能力，边权几乎全在 delta 上 */
  const spEdge = new Map<string, GraphEdge>();
  const spOfGroup = new Map<string, GraphEdge[]>();
  for (const e of d.edges) {
    if (e.kind !== 'S-SP') continue;
    spEdge.set(e.target, e);
    const arr = spOfGroup.get(e.source);
    if (arr) arr.push(e);
    else spOfGroup.set(e.source, [e]);
  }
  /* 组内按边权降序排一次，下钻的取前若干项与连线的取用范围共用这一序 */
  for (const [, arr] of spOfGroup) arr.sort((a2, b2) => eff(b2) - eff(a2));

  const n = Math.max(jobs.length, 1);
  const monthIdx = (m?: string) => (m ? months.indexOf(m) : -1);

  /** 把末月的强度铺成一条逐月序列 —— 因子由算法侧按月给出，末月恒为 1 */
  const seriesOf = (id: string, endValue: number): (number | null)[] => {
    const f = timeline.demand?.[id];
    if (!f) return months.map(() => endValue);
    /* 保留五位小数。此处逐行逐月各算一次，两万余行乘四十六个月，
       toFixed 走的是字符串路径，改用整数舍入 */
    return f.map((v) => (v === null ? null : Math.round(endValue * v * 1e5) / 1e5));
  };

  const mkRow = (
    node: GraphNode,
    kind: FlowKind,
    dim: string,
    group: string,
    a: Acc,
    links: Map<string, number>,
  ): FlowRow => {
    const demand = a.demand / n;
    return {
      id: node.id,
      kind,
      name: node.name,
      dim,
      group,
      skillType: node.skillType,
      definition: node.definition,
      demand,
      series: seriesOf(node.id, demand),
      prof: a.prof,
      forwardShare: demand > 1e-9 ? Math.min(1, a.fwd / n / demand) : 0,
      confirmedAt: monthIdx(timeline.confirmedAt?.[node.id]),
      links,
    };
  };

  /* ---- A2 核心任务行 ----
     任务的要求强度取 J-T 边的有效权重，职级倾斜由这项任务所要的能力组反推。
     先算它，因为技能点那一段的连线要指过来。 */
  const taskAcc = new Map<string, Acc>();
  const jobTaskW = new Map<string, Map<string, number>>(); // taskId → jobId → 权重
  /* 任务的职级倾斜只随任务与职级变：同一项任务被十余个岗位承担，
     不缓存时它要沿 T-S 边重算十余遍 */
  const tiltOfTask = new Map<string, number>();
  const taskTiltOf = (taskId: string) => {
    let t = tiltOfTask.get(taskId);
    if (t === undefined) {
      t = taskTilt(taskId, o.level, d.edges, nodeById);
      tiltOfTask.set(taskId, t);
    }
    return t;
  };
  for (const job of jobs) {
    for (const e of edgesFrom(d.edges, 'J-T', job.id)) {
      const tilt = taskTiltOf(e.target);
      const v = eff(e) * tilt;
      let a = taskAcc.get(e.target);
      if (!a) taskAcc.set(e.target, (a = { demand: 0, fwd: 0, prof: [0, 0, 0, 0] }));
      a.demand += v;
      a.fwd += λ * e.deltaWeight * tilt;
      /* 任务没有软硬分类，未写明的比例走 TASK_UNSPECIFIED */
      a.prof[bandOrUnknownAt(Math.min(1, eff(e)), `${job.id}|${e.target}`, TASK_UNSPECIFIED)] += 1;
      const m = jobTaskW.get(e.target) ?? new Map<string, number>();
      m.set(job.id, v);
      jobTaskW.set(e.target, m);
    }
  }

  /* 任务 → 能力组的边，供两段连线共用 */
  const tsOfTask = new Map<string, GraphEdge[]>();
  for (const e of d.edges) {
    if (e.kind !== 'T-S') continue;
    const arr = tsOfTask.get(e.source);
    if (arr) arr.push(e);
    else tsOfTask.set(e.source, [e]);
  }

  /* 技能点 → 要求它的任务：T-S 边的权重再沿 S-SP 按组内占比分下去 */
  const groupLinks = new Map<string, Map<string, number>>();
  /** 任务 → 该任务对各技能的相对要求（组一级），技能点一级的连线由它分下去 */
  const taskSkillW: { tid: string; gid: string; gw: number }[] = [];
  for (const [tid] of taskAcc) {
    const edges = tsOfTask.get(tid) ?? [];
    const peak = Math.max(...edges.map(eff), 1e-9);
    for (const e of edges) {
      const gw = eff(e) / peak;
      const gm = groupLinks.get(e.target) ?? new Map<string, number>();
      gm.set(tid, gw);
      groupLinks.set(e.target, gm);
      taskSkillW.push({ tid, gid: e.target, gw });
    }
  }

  const itemAll: FlowRow[] = [];
  for (const [iid, a] of itemAcc) {
    const node = nodeById.get(iid);
    const gNode = nodeById.get(groupOfItem.get(iid) ?? '');
    if (!node || !gNode) continue;
    const sp = spEdge.get(iid);
    a.fwd = a.demand * (sp && eff(sp) > 0 ? Math.min(1, (λ * sp.deltaWeight) / eff(sp)) : 0);
    itemAll.push(mkRow(node, 'skillpoint', gNode.category, gNode.name, a, new Map()));
  }
  /* 先裁长尾再汇总能力组：一个组若只剩被裁掉的项，它那道括号也就不该画 */
  const { kept: itemRows, trimmed: itemsTrimmed } = trimTail(itemAll);

  /* ---- 技能点 → 要求它的任务 ----

     一条这样的连线要为「每个技能点 × 要求其父技能的每项任务」各存一份，全库
     两万余项技能点铺开是近百万条。而图上读得到连线的只有两批：入图的技能点行，
     与各技能下钻时展开的前若干项。故先定出这两批，再只为它们分连线 ——
     其余的建了也无人读，这一步此前占整张图取数的三分之一。 */
  const rowIds = new Set(itemAll.map((r) => r.id));
  const linkNeeded = new Set(itemRows.map((r) => r.id));
  for (const [, kids] of spOfGroup) {
    let n2 = 0;
    for (const e of kids) {
      if (!rowIds.has(e.target)) continue;
      linkNeeded.add(e.target);
      if (++n2 >= SKILL_ITEM_TOP) break;
    }
  }
  const itemLinks = new Map<string, Map<string, number>>();
  for (const { tid, gid, gw } of taskSkillW) {
    const kids = spOfGroup.get(gid) ?? [];
    const sum = kids.reduce((t, k) => t + Math.max(eff(k), 0.01), 0) || 1;
    for (const k of kids) {
      if (!linkNeeded.has(k.target) || !itemKept(k.target)) continue;
      let im = itemLinks.get(k.target);
      if (!im) itemLinks.set(k.target, (im = new Map<string, number>()));
      /* 组内占比乘回成员数：均分时正好等于组权重本身，
         这样"任务要这个组要得有多重"与"组里哪一项占得多"两件事都保留下来 */
      im.set(tid, Math.min(1, gw * ((Math.max(eff(k), 0.01) / sum) * kids.length)));
    }
  }
  for (const r of itemAll) {
    const m = itemLinks.get(r.id);
    if (m) r.links = m;
  }

  /* ---- 能力组行：由技能点行汇总，两层的数必须同源 ---- */
  const groupRows: FlowRow[] = [];
  /* 技能点行按所属技能名归拢一次：逐技能各筛一遍全表时，
     六十余项技能乘两万余条技能点行是一百三十万次比较 */
  const itemsByGroupName = new Map<string, FlowRow[]>();
  for (const r of itemRows) {
    const arr = itemsByGroupName.get(r.group);
    if (arr) arr.push(r);
    else itemsByGroupName.set(r.group, [r]);
  }
  /* 各岗位在每个能力组内要求最高的那一项，按岗位一次扫出。
     逐技能各把全部岗位的权重表查一遍时，是「技能 × 岗位 × 组内条目数」三重相乘。 */
  const groupNameOfRow = new Map(itemRows.map((r) => [r.id, r.group]));
  const topByJob = new Map<string, Map<string, { v: number; id: string }>>();
  for (const [jid, w] of tiltedItems) {
    const tops = new Map<string, { v: number; id: string }>();
    for (const [iid, v] of w) {
      const gname = groupNameOfRow.get(iid);
      if (gname === undefined) continue;
      const cur = tops.get(gname);
      if (!cur || v > cur.v) tops.set(gname, { v, id: iid });
    }
    topByJob.set(jid, tops);
  }
  /* 档位构成落在（岗位, 技能）一层，按岗位缓存后每岗只查几十次 */
  const shareCache = new Map<string, ProfShare | null>();
  const profShareOfJob = (jid: string, code: string | undefined) => {
    if (!code) return null;
    const k = `${jid}|${code}`;
    let v = shareCache.get(k);
    if (v === undefined) {
      v = profShareOf(nodeById.get(jid)?.name ?? '', code);
      shareCache.set(k, v);
    }
    return v;
  };
  for (const g of d.nodes) {
    if (g.kind !== 'skill') continue;
    const kids = itemsByGroupName.get(g.name) ?? [];
    if (!kids.length) continue;
    const a: Acc = {
      demand: kids.reduce((s, r) => s + r.demand, 0) * n,
      fwd: kids.reduce((s, r) => s + r.demand * r.forwardShare, 0) * n,
      prof: [0, 0, 0, 0],
    };
    /* 组的三档不是把项的三档加起来 —— 那数出来的是"项 × 岗位"的对数。
       一个岗位对这个组的档位取它在组内最高那一项的档位。 */
    for (const [jid, tops] of topByJob) {
      const t = tops.get(g.name);
      if (!t || t.v <= 0.001) continue;
      a.prof[
        profBandFrom(
          profShareOfJob(jid, codeOf(groupOfItem.get(t.id))),
          t.v,
          `${jid}|${t.id}`,
          nodeById.get(t.id)?.skillType,
        )
      ] += 1;
    }
    /* 技能行的两级归属：内层括号为所属能力组，外层为能力维度。
       写成 g.name 则内层每行各成一组，括号退化为逐行重复行名。 */
    groupRows.push(
      mkRow(g, 'skill', g.category, g.topCategory ?? g.name, a, groupLinks.get(g.id) ?? new Map()),
    );
  }

  /* ---- 技能下的技能点：下钻用 ----

     主图第三段画到技能这一层，技能点作为下钻的落点。两层不并排画的理由是数量：
     技能为封闭体系 54 项，一屏读得完；技能点是随市场文本生长的开放集合，
     本批逾两万项，铺成一列时行高压到一像素以下，条长之间读不出差别。

     量按「经由这项技能的那一份」算，不取该技能点的总需求。

     一个技能点可以挂在多项技能下（Markdown 同时是程序设计与软件工程、
     书面技术文档撰写、信息管理与数字素养的条目），总需求是各条路径之和。
     若按总需求排，通用工具会压过专业工具：实测程序设计与软件工程下
     Markdown 的总需求高于 Java，而算法侧给出的这一层边权是
     Java 0.4175、Markdown 0.1182，相差三倍半。下钻问的是"这项技能里哪几个
     技能点最重"，答案只应由这项技能自己的那条边定。

     裁剪按组内相对量，不按全图峰值：一项技能下最强的那个技能点，
     哪怕在全图里排不进前列，在这项技能内部仍是该看到的第一条。
     超出上限的部分合并为一条，如实写出条数与合计占比，不作静默截断。 */
  const itemsBySkill = new Map<string, FlowRow[]>();
  {
    const rowById = new Map(itemAll.map((r) => [r.id, r]));
    for (const g of groupRows) {
      /* spOfGroup 建好时即按边权降序，此处不再另排 —— 下钻取的前若干项
         与上面定连线范围时取的必须是同一批，两处各排一次就有走偏的余地 */
      const edges = (spOfGroup.get(g.id) ?? []).filter((e) => rowById.has(e.target));
      if (!edges.length) continue;
      const tot = edges.reduce((a2, e) => a2 + Math.max(eff(e), 1e-9), 0) || 1;
      /* 经由本技能的那一份 = 本技能的需求量 × 该技能点在本技能内的边权占比 */
      const share = (e: GraphEdge) => Math.max(eff(e), 1e-9) / tot;
      const kids: FlowRow[] = edges.map((e) => {
        const src = rowById.get(e.target)!;
        const k = share(e);
        return {
          ...src,
          demand: g.demand * k,
          series: src.series.map((v, t) => (v === null ? null : (g.series[t] ?? g.demand) * k)),
        };
      });
      const head = kids.slice(0, SKILL_ITEM_TOP);
      const tail = kids.slice(SKILL_ITEM_TOP);
      const rest = tail.reduce((a2, r) => a2 + r.demand, 0);
      const out = [...head];
      if (tail.length) {
        out.push({
          id: `agg:${g.id}`,
          kind: 'skillpoint',
          /* 只报条数，不报合计占比。技能点的分布极平：一项技能下头部十来个
             主流工具之外，其余数千项的边权几乎并列，合计占比因而恒在九成以上，
             写出来读者会以为列出的这十二项无足轻重，而它们正是这项技能里
             唯一拉开差距的那一批。 */
          name: `另有 ${tail.length} 项未列出`,
          dim: g.dim,
          group: g.name,
          demand: rest,
          series: months.map(() => rest),
          prof: [0, 0, 0, 0],
          forwardShare: 0,
          confirmedAt: -1,
          links: new Map(),
        });
      }
      itemsBySkill.set(g.id, out);
    }
  }

  /* ---- A2 任务行落地 ---- */

  /** 图上还留着技能点的任务。技术栈或技能点类型一旦收窄，中段就按它筛 */
  const taskWithItems = new Set<string>();
  for (const r of itemRows) for (const tid of r.links.keys()) taskWithItems.add(tid);

  const taskAll: FlowRow[] = [];
  for (const [tid, a] of taskAcc) {
    const node = nodeById.get(tid);
    if (!node) continue;
    /* 技术栈或技能点类型收窄后，只留下确实要求那批能力的任务 ——
       否则右段筛掉了一半、中段一行不少，连线就会大片悬空，
       筛到极窄时（如"人工智能与智能技术"里没有软技能）中段还会剩下几条孤条。 */
    if ((o.stack || types.length < 3) && !taskWithItems.has(tid)) continue;
    const links = new Map<string, number>();
    const jw = jobTaskW.get(tid);
    if (jw) {
      const peak = Math.max(...jw.values(), 1e-9);
      for (const [jid, v] of jw) links.set(jid, v / peak);
    }
    taskAll.push(mkRow(node, 'task', '', node.name, a, links));
  }
  /* 任务一段不裁长尾。裁剪那道阈值是为技能点一层设的 —— 那一层两万余项，
     不裁则一列全是一两个像素的碴儿；任务是封闭体系，全库不足百项，
     所选大类关联到几项就画几项，"这个大类要做哪些事"这一问才答得完整。 */
  const taskRows = taskAll;
  const tasksTrimmed = 0;

  /* ---- A3 岗位行 ----
     条长 = 该岗位在当前技术栈下的要求总量。选中一个技术方向后，
     岗位段的排序会跟着变 —— "这个方向上要求最重的是哪些岗位"
     正是赛题那句"按技术栈切换视图"该产生的效果。 */
  const jobRows: FlowRow[] = [];
  const keptItems = new Set(itemRows.map((r) => r.id));
  for (const job of jobs) {
    const w = tiltedItems.get(job.id);
    const row = base.jobs.get(job.id);
    if (!w || !row) continue;
    let total = 0;
    const mix: Record<SkillType, number> = { hard: 0, soft: 0 };
    const prof: [number, number, number, number] = [0, 0, 0, 0];
    for (const [iid, v] of w) {
      if (!keptItems.has(iid)) continue;
      total += v;
      const st = nodeById.get(iid)?.skillType ?? 'hard';
      mix[st] += v;
      prof[
        profBandFrom(profShareOfJob(job.id, codeOf(groupOfItem.get(iid))), v, `${job.id}|${iid}`, st)
      ] += 1;
    }
    if (total <= 1e-6) continue;
    const msum = mix.hard + mix.soft || 1;
    for (const k of Object.keys(mix) as SkillType[]) mix[k] /= msum;
    const a: Acc = { demand: total * n, fwd: 0, prof };
    const r = mkRow(job, 'job', job.topCategory ?? '', job.name, a, new Map());
    r.mix = mix;
    r.posts = job.attrs?.postCount;
    r.funtypes = job.funtypes;
    jobRows.push(r);
  }

  /* ---- 排序 ----
     一律按末个实测月的强度定死。按"当前游标那个月"排的话，
     拖时间轴时行会上下跳，同一项能力追不住，时间轴反倒读不出变化。 */
  const dims = [...new Set(SKILL_GROUPS.map((g) => g.dim))];
  const gRank = new Map([...groupRows].sort((a, b) => b.demand - a.demand).map((r, i) => [r.group, i] as const));
  const cmpSkill = (a: FlowRow, b: FlowRow) =>
    dims.indexOf(a.dim) - dims.indexOf(b.dim) ||
    (gRank.get(a.group) ?? 0) - (gRank.get(b.group) ?? 0) ||
    b.demand - a.demand;
  itemRows.sort(cmpSkill);
  groupRows.sort(cmpSkill);
  taskRows.sort((a, b) => b.demand - a.demand);
  /* 岗位段按招聘信息条数排 —— 与另两段按要求强度排不同，这里换了一把尺子。

     条长读的仍是要求总量（三段同一套语义，跨段可比），但那个量在这批数据上
     区分度很低：一个岗位的要求总量是它涉及的技能点权重之和，各岗位涉及的
     技能点数量相近，实测最长与最短只差一倍半，排出来的先后几乎没有信息。
     招聘信息条数是岗位这一层唯一的实测计量（岗位体系 v2.0 的 hits，全库
     510 万条），差异是数量级的。行序因此交给它：谁排在最上面这件事，
     由市场规模说了算，不由一个补出来的权重和说了算。 */
  jobRows.sort((a, b) => (b.posts ?? 0) - (a.posts ?? 0) || b.demand - a.demand);

  const model: FlowModel = {
    itemRows,
    groupRows,
    itemsBySkill,
    taskRows,
    jobRows,
    months,
    forecastFrom,
    observedLast,
    scopeJobs: jobs.length,
    groupsInStack: groupRows.length,
    itemsInStack: itemRows.length,
    itemsTrimmed,
    tasksTrimmed,
  };
  cache.set(key, model);
  return model;
}

/** 一项任务的职级倾斜 = 它所要的能力组倾斜按边权加权平均 */
function taskTilt(
  taskId: string,
  level: JobLevel,
  edges: GraphEdge[],
  nodeById: Map<string, GraphNode>,
): number {
  let num = 0;
  let den = 0;
  for (const e of edgesFrom(edges, 'T-S', taskId)) {
    const s = nodeById.get(e.target);
    if (!s) continue;
    const w = eff(e);
    num += LEVEL_TILT_OF(s.name, level) * w;
    den += w;
  }
  return den > 0 ? num / den : 1;
}

/* ==================== 时间维的读法 ==================== */

export type ChangeKind = 'new' | 'up' | 'down' | 'flat' | 'gone';

export interface RowAt {
  /** 当月值；null = 当月该条目还不在图谱里 */
  value: number | null;
  /** 基准月值；未设基准时为 null */
  base: number | null;
  change: ChangeKind | null;
  /** 当月是否已被招聘要求确认 */
  confirmed: boolean;
}

/** 变化判定的阈值：相对基准月 ±4% 以内算持平，免得四舍五入的噪声也报成变化 */
const FLAT = 0.04;

export function rowAt(r: FlowRow, cursor: number, baseline: number | null): RowAt {
  const value = r.series[cursor] ?? null;
  const base = baseline === null ? null : (r.series[baseline] ?? null);
  const confirmed = r.confirmedAt >= 0 && r.confirmedAt <= cursor;

  let change: ChangeKind | null = null;
  if (baseline !== null && baseline !== cursor) {
    if (value === null && base === null) change = null;
    else if (value === null) change = 'gone';
    else if (base === null) change = 'new';
    else {
      const d = base > 1e-9 ? (value - base) / base : 0;
      change = d > FLAT ? 'up' : d < -FLAT ? 'down' : 'flat';
    }
  }
  return { value, base, change, confirmed };
}

/** 一段行在某月的合计 —— 时间轴底衬那条曲线读的就是它 */
export function totalsOf(rows: FlowRow[], months: number): (number | null)[] {
  const out: (number | null)[] = [];
  for (let t = 0; t < months; t++) {
    let s = 0;
    let any = false;
    for (const r of rows) {
      const v = r.series[t];
      if (v === null || v === undefined) continue;
      s += v;
      any = true;
    }
    out.push(any ? s : null);
  }
  return out;
}

/** 某月已进入招聘要求的条目数 —— 口径行与时间轴提示共用 */
export function confirmedCount(rows: FlowRow[], cursor: number): number {
  return rows.filter((r) => r.confirmedAt >= 0 && r.confirmedAt <= cursor && r.series[cursor] !== null).length;
}
