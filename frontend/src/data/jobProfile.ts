/* ============================================================
   岗位能力剖面 —— 能力棱镜的数据源

   ------------------------------------------------------------
   为什么要有这一层

   赛题对全景图谱只有一句规定：“展示领域内岗位的能力要求，颗粒度到
   技能点级别，可以按技术栈和级别切换视图”。这句话的主语是岗位，
   谓语是“要求”—— 图上必须能回答“这个岗位要什么能力、要到什么程度”。

   上一版棱镜画的是四份互不相干的清单（岗位 16 段 / 任务 35 段 /
   能力组 9 段 / 技能点 49 段），环与环之间没有关系，段高编码的是各层
   自己的结构规模（子树叶子数、组内技能点数）。它回答的是“体系里有多少
   东西”，不是“岗位要什么”。本文件把主语换回岗位：给定一个岗位（或一类
   岗位、或整个领域）与一档职级，算出它对每项任务、每个能力组、每个
   技能点的要求强度，三层共用同一个量纲。

   ------------------------------------------------------------
   口径与来源

   要求强度沿两条路径聚合，与匹配页的岗位向量同源（generator.jobSkillWeights）：
     路径一  J → S       JD 里直接列出的能力要求
     路径二  J → T → S   岗位的核心任务反过来要求的能力
   技能点层再沿 S-SP 边把能力组的要求按组内权重分下去。

   图谱产物接入后，四类边均为算法侧实测，各带一个由招聘信息统计出的基图权重
   与一个叠加前瞻修正后的合成权重。上一版四类里只有 S-SP 为实测、其余三类由
   演示补齐层按岗位类别给出的形态已不再走到（VITE_DATA=taxonomy 时仍走）。

   职级倾斜本批已转为实测：招聘信息汇总表逐条带一个职级与一组能力要求，
   按职级切开即得各档的能力构成，某档对某能力组的提及率除以全样本提及率
   即得倾斜系数（realGraph.LEVEL_TILT_MEASURED）。职级列约五成半有值，
   无值的条目不进入本维，故这一维记为实测但覆盖不全，
   界面上的标由“演示数据”改为写明覆盖率。

   演示词表下仍走 LEVEL_TILT ——按能力组性质给的一组确定性系数，
   初级偏执行与工具，高级偏架构、决策与沟通。
   ============================================================ */

import type { GraphEdge, GraphNode, NodeKind } from '@/types/graph';
import { edgesFrom, getDataset, jobSkillWeights, NOW } from './generator';
import { LEVEL_TILT_MEASURED } from './realGraph';
import { ORPHAN_CLUSTER } from './realTaxonomy';

/* ---------------- 职级 ---------------- */

export type JobLevel = 'junior' | 'mid' | 'senior';

export const JOB_LEVELS: { v: JobLevel; label: string; years: string }[] = [
  { v: 'junior', label: '初级', years: '3 年以内' },
  { v: 'mid', label: '中级', years: '3–5 年' },
  { v: 'senior', label: '高级', years: '5 年以上' },
];

export const LEVEL_LABEL: Record<JobLevel, string> = { junior: '初级', mid: '中级', senior: '高级' };

const LEVEL_I: Record<JobLevel, 0 | 1 | 2> = { junior: 0, mid: 1, senior: 2 };

/**
 * 十个能力组在三档职级上的相对倾斜 [初级, 中级, 高级]。
 *
 * 演示词表下的取值，依据是能力组本身的性质，不是拟合出来的：编码与工具类
 * 能力在初级岗位上占比最高，随职级递减；架构、权衡决策、跨方要事沟通反过来。
 * 系数落在 0.55–1.5 之间 —— 再大就会把某一档的图压成只剩两三段，读不出结构。
 *
 * 招聘数据接入后本表不再走到：汇总表逐条带职级与能力要求，各档的提及率
 * 可直接算出（realGraph.LEVEL_TILT_MEASURED）。实测系数与本表方向多数一致，
 * 但幅度小得多，且"软件与算法"一项方向相反 —— 本表按经验假定它随职级递减，
 * 实测为持平略升。
 */
const LEVEL_TILT: Record<string, [number, number, number]> = {
  软件与算法: [1.28, 1.05, 0.82],
  系统与基础设施: [1.12, 1.04, 0.94],
  数据与计算科学: [0.94, 1.05, 1.06],
  人工智能与智能技术: [0.88, 1.05, 1.12],
  学习与思维: [1.06, 1.0, 1.04],
  沟通协作: [0.78, 1.0, 1.28],
  管理与决策: [0.55, 0.94, 1.5],
  职业素养: [1.0, 1.0, 1.0],
  AI通用素养: [0.96, 1.06, 1.04],
  '前瞻新技能': [1.0, 1.0, 1.0],
};

const TILT_FLAT: [number, number, number] = [1, 1, 1];

/* 实测系数优先。某一组因提及量不足未进实测表时按持平处理，
   不回落到演示表 —— 一张图上半数组读实测、半数组读假定，两者不可比 */
const tiltOf = (groupName: string, level: JobLevel) =>
  (LEVEL_TILT_MEASURED
    ? (LEVEL_TILT_MEASURED[groupName] ?? TILT_FLAT)
    : (LEVEL_TILT[groupName] ?? TILT_FLAT))[LEVEL_I[level]];

/** 同一套职级倾斜给全景图谱的流图取数用 —— 两张图必须共用同一组系数，
    否则同一个岗位在两处会显示成两份不同的能力要求 */
export const LEVEL_TILT_OF = tiltOf;

/* ---------------- 输出结构 ---------------- */

/** 剖面上的一个条目：某一层的一项，带它在本岗位下的要求强度 */
export interface ProfileItem {
  id: string;
  name: string;
  kind: NodeKind;
  /** 圆周分组：任务为空串（扁平），能力组为所属能力维，技能点为所属能力组 */
  group: string;
  /** 该岗位对它的要求强度。层内可比，跨层不可比 */
  demand: number;
  /** 要求里由前瞻信号贡献的那一截，占 demand 的比例 0–1 */
  forwardShare: number;
  confidence: number;
  node: GraphNode;
}

export type ScopeKind = 'all' | 'category' | 'job';

export interface ProfileScope {
  kind: ScopeKind;
  /** category 时为大类名，job 时为 node.id，all 时为 null */
  id: string | null;
  label: string;
  /** 这份剖面由几个岗位平均而来 */
  jobCount: number;
}

export interface JobProfile {
  scope: ProfileScope;
  level: JobLevel;
  tasks: ProfileItem[];
  skills: ProfileItem[];
  points: ProfileItem[];
}

/* ---------------- 岗位集合 ---------------- */

/**
 * 一个大类取几个岗位来平均。
 *
 * 同一大类下的岗位共用一份能力骨架（demoJobProfile 按大类给画像，
 * 每个岗位再各加一项），采样 8 个与全取的差别在小数点后两位，
 * 而全取要对 131 个岗位各扫一遍边表。接入真实的逐岗位映射后这个上限
 * 应当撤掉 —— 那时不同岗位之间才有值得算的差异。
 */
const SAMPLE_PER_CATEGORY = 8;

const byCategory = (() => {
  let cache: Map<string, GraphNode[]> | null = null;
  return () => {
    if (cache) return cache;
    const m = new Map<string, GraphNode[]>();
    for (const n of getDataset().nodes) {
      if (n.kind !== 'job') continue;
      const c = n.topCategory || ORPHAN_CLUSTER;
      const arr = m.get(c);
      if (arr) arr.push(n);
      else m.set(c, [n]);
    }
    /* 大类头节点排在最前，其余按子树规模降序 —— 采样截断时留下的是这一类里
       最有代表性的几个，而不是碰巧排在前面的几个。 */
    for (const [c, arr] of m) {
      arr.sort((a, b) => {
        if (a.name === c) return -1;
        if (b.name === c) return 1;
        return (b.realCount ?? 1) - (a.realCount ?? 1);
      });
    }
    cache = m;
    return m;
  };
})();

/* 圆周轴上的大类顺序：按该类的岗位数降序。

   "无一级归属"一档不出：落在这一档里的全是尚未归入体系的新岗位，而这张图读的是
   招聘市场的要求总量，它取自逐岗位的市场需求表，那张表按设计不收新岗位（新岗位
   没有市场读数，一条零向量进去会把中段与右段一并拉平）。这一档因而恒是一张空图，
   下拉里留着它，读者只会以为图出了故障。新岗位另在岗位洞察页的"新岗位发现与定义"
   一节，那里读的是它的证据与推导构成，不是市场读数。 */
export function jobCategories(): { name: string; count: number }[] {
  return [...byCategory().entries()]
    .filter(([name]) => name !== ORPHAN_CLUSTER)
    .map(([name, arr]) => ({ name, count: arr.length }))
    .sort((a, b) => b.count - a.count);
}

function jobsOf(scope: ProfileScope): GraphNode[] {
  const m = byCategory();
  if (scope.kind === 'job') {
    const n = getDataset().nodes.find((x) => x.id === scope.id);
    return n ? [n] : [];
  }
  if (scope.kind === 'category') return (m.get(scope.id ?? '') ?? []).slice(0, SAMPLE_PER_CATEGORY);
  /* 领域整体：每个大类出一个代表。取全部 131 个岗位平均出来的图与这一版
     几乎重合（同类共用骨架），但要多算两个数量级的边遍历。 */
  return [...m.values()].map((arr) => arr[0]).filter(Boolean);
}

/* ---------------- 剖面计算 ---------------- */

const λ = 1; // 前瞻修正系数，与匹配页的岗位向量同口径
const eff = (e: GraphEdge) => e.baseWeight + λ * e.deltaWeight;

interface Acc {
  total: number;
  overlay: number;
  conf: number;
  confW: number;
}
const acc = (): Acc => ({ total: 0, overlay: 0, conf: 0, confW: 0 });
const add = (a: Acc, v: number, overlay: number, confidence: number) => {
  a.total += v;
  a.overlay += overlay;
  a.conf += confidence * Math.max(v, 1e-6);
  a.confW += Math.max(v, 1e-6);
};

const profileCache = new Map<string, JobProfile>();

export function jobProfile(scope: ProfileScope, level: JobLevel): JobProfile {
  const key = `${scope.kind}|${scope.id ?? ''}|${level}`;
  const hit = profileCache.get(key);
  if (hit) return hit;

  const d = getDataset();
  const nodeById = d.nodeById;
  const jobs = jobsOf(scope);

  /* 同一项任务被十余个岗位承担，倾斜只随任务与职级变，逐岗位重算一遍是白费 */
  const tiltOfTask = new Map<string, number>();
  const taskTiltOf = (taskId: string) => {
    let t = tiltOfTask.get(taskId);
    if (t === undefined) {
      t = taskTilt(taskId, level, d.edges, nodeById);
      tiltOfTask.set(taskId, t);
    }
    return t;
  };

  const taskAcc = new Map<string, Acc>();
  const skillAcc = new Map<string, Acc>();
  const bump = (m: Map<string, Acc>, k: string) => {
    let a = m.get(k);
    if (!a) m.set(k, (a = acc()));
    return a;
  };

  for (const job of jobs) {
    // 能力组：两条路径已在 jobSkillWeights 里聚合好
    for (const [sid, w] of jobSkillWeights(job.id, NOW, d.edges, d.signalMap, λ)) {
      const s = nodeById.get(sid);
      if (!s) continue;
      add(bump(skillAcc, sid), w.total * tiltOf(s.name, level), w.overlay, w.confidence);
    }
    // 任务：J-T 边的有效权重；职级倾斜由这项任务所要的能力组反推
    for (const e of edgesFrom(d.edges, 'J-T', job.id)) {
      add(bump(taskAcc, e.target), eff(e) * taskTiltOf(e.target), λ * e.deltaWeight, e.confidence);
    }
  }

  const n = Math.max(jobs.length, 1);
  const item = (id: string, a: Acc, group: string): ProfileItem | null => {
    const node = nodeById.get(id);
    if (!node) return null;
    const demand = a.total / n;
    return {
      id,
      name: node.name,
      kind: node.kind,
      group,
      demand,
      forwardShare: demand > 1e-6 ? Math.min(1, a.overlay / n / demand) : 0,
      confidence: a.confW > 0 ? a.conf / a.confW : node.confidence,
      node,
    };
  };

  const tasks = [...taskAcc.entries()]
    .map(([id, a]) => item(id, a, ''))
    .filter((x): x is ProfileItem => !!x && x.demand > 1e-4)
    .sort((a, b) => b.demand - a.demand);

  const skills = [...skillAcc.entries()]
    .map(([id, a]) => item(id, a, nodeById.get(id)?.category ?? ''))
    .filter((x): x is ProfileItem => !!x && x.demand > 1e-4);

  /* 技能点：把每个能力组的要求按组内 S-SP 边的权重分下去。
     组内权重之和为分母，所以“一个组要求多高”与“组内怎么分”是两件事 ——
     前者由岗位决定，后者由技能体系自身决定。 */
  const spOfSkill = new Map<string, GraphEdge[]>();
  for (const e of d.edges) {
    if (e.kind !== 'S-SP') continue;
    const arr = spOfSkill.get(e.source);
    if (arr) arr.push(e);
    else spOfSkill.set(e.source, [e]);
  }

  const points: ProfileItem[] = [];
  for (const s of skills) {
    const kids = spOfSkill.get(s.id) ?? [];
    const sum = kids.reduce((t, e) => t + Math.max(eff(e), 0.01), 0) || 1;
    for (const e of kids) {
      const node = nodeById.get(e.target);
      if (!node) continue;
      const share = Math.max(eff(e), 0.01) / sum;
      points.push({
        id: node.id,
        name: node.name,
        kind: 'skillpoint',
        group: s.name,
        demand: s.demand * share,
        /* 技能点的前瞻占比取两截：它所属能力组那一截，加上这条 S-SP 边
           自己的前瞻修正 —— 一个还没进 JD 的技能点，边权几乎全在 delta 上。 */
        forwardShare: Math.min(1, s.forwardShare + (eff(e) > 0 ? (λ * e.deltaWeight) / eff(e) : 0)),
        confidence: (s.confidence + e.confidence) / 2,
        node,
      });
    }
  }

  const out: JobProfile = { scope, level, tasks, skills, points };
  profileCache.set(key, out);
  return out;
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
    num += tiltOf(s.name, level) * w;
    den += w;
  }
  return den > 0 ? num / den : 1;
}

/* ---------------- 径向标度 ----------------
   段高的标度必须一次算定，不能按当前剖面的最大值归一 —— 那样每换一个岗位
   所有段一起涨缩，“这项要求变高了”与“别的都变低了”在图上会长得一模一样。

   但标度也不能全局只取一个。领域整体是十几个大类平均出来的，峰值被摊平：
   实测领域整体下最重的一项任务只有单个大类最重那项的 19%，
   共用一套标度的话，默认进页面看到的就是一圈贴着底的矮条，什么都读不出。
   这不是数据的问题 —— 一个大类里八个岗位共享同几项任务，摊到全领域自然分散，
   两者本来就不该放在同一把尺子上量。

   所以分两档，各自在档内一次算定：
     all    领域整体这一张图自己的上界
     focus  各大类 × 高级档里的最大值 —— 高级档倾斜系数最大（1.5），
            是三档的上界，不必把三档都算一遍
   同一档内（十几个大类之间、各岗位之间）完全可比，这也正是实际会做的比较；
   跨档比较则由口径行写明。 */
export type ScaleBand = 'all' | 'focus';

const ALL_SCOPE: ProfileScope = { kind: 'all', id: null, label: '领域整体', jobCount: 0 };

const scaleCache = new Map<ScaleBand, { task: number; skill: number; point: number }>();

export function profileScale(band: ScaleBand) {
  const hit = scaleCache.get(band);
  if (hit) return hit;

  const s = { task: 1e-6, skill: 1e-6, point: 1e-6 };
  const take = (p: JobProfile) => {
    for (const t of p.tasks) s.task = Math.max(s.task, t.demand);
    for (const k of p.skills) s.skill = Math.max(s.skill, k.demand);
    for (const q of p.points) s.point = Math.max(s.point, q.demand);
  };

  if (band === 'all') {
    take(jobProfile(ALL_SCOPE, 'senior'));
  } else {
    for (const c of jobCategories()) {
      take(jobProfile({ kind: 'category', id: c.name, label: c.name, jobCount: c.count }, 'senior'));
    }
  }
  scaleCache.set(band, s);
  return s;
}
