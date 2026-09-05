/* ============================================================
   职业探索 —— 取数层

   这一层把图谱数据整理成 JobViz（Wang et al., Visual Informatics 2024）
   三视图所需的形状。论文里的对象是"技能框架 × 岗位 × 属性"，
   本系统的对象是"能力维度 → 能力组 → 技能点"与"岗位—任务—能力"，
   换掉的只有这一层的取数口径，视图的编码方式与交互原样保留。

   对应关系：
     论文 3 级技能框架（2 类 → 8 组 → 23 项）  →  2 个能力维度 → 10 个能力组 → 54 项技能
     论文 job post（岗位）                      →  岗位节点（真实体系 145 个 + 新发现）
     论文 skill vector（技能向量）              →  岗位在 10 个能力组上的构成占比（和为 1）
     论文 affinity propagation 聚类             →  同一算法，作用在上面这个向量上
     论文属性栏（地点/学历/经验/行业）           →  省份 / 学历 / 经验（行业一条无对应字段，撤除）

   本文件不产出任何界面文案，只产出数。哪些量是实测、哪些是补的，
   由调用方在界面上交代（见 pages/Explore.tsx 的演示数据标）。
   ============================================================ */

import type { Distribution, GraphEdge, GraphNode, JobAttributes, SkillType } from '@/types/graph';
import { getDataset, jobSkillWeights, NOW } from './generator';
import {
  DEGREE_AXIS,
  PROVINCE_AXIS,
  cityCountsOf,
  profShareOf,
  provinceShare,
  skillCoverageOf,
  type ProfShare,
} from './realGraph';
import { PROVINCE_OTHER } from './provinces';
import { rand01 } from '@/utils/rng';

/** 节点 id 形如 `S:T-SW-01`，档位表按体系编码索引，此处剥掉层前缀 */
const codeOf = (id: string | undefined) => (id ? id.slice(id.indexOf(':') + 1) : undefined);

/* ==================== 常量 ==================== */

/** 属性栏的三条轴。

    论文的属性栏是"地点 / 学历 / 经验 / 行业"，前三条在本系统各有实测来源：
    省份由招聘原文的 place 列按行政区划汇总，学历由正文的门槛语抽出（原文表的
    degree 一列此前整批为空、本批约一成有值，其余由正文的门槛语抽出，
   与算法侧的职级同一路径），
    经验取自招聘信息汇总表的职级列。

    第四条"行业"对应的企业类别一维已整条撤除：迁移包按 include_jd_dataset=false
    打包，派生出的原文表与汇总表都没有这一列，仅凭企业名判不出类别
    （人力资源服务企业多为代招方，不是用人单位）。撤除而不补齐 ——
    与另三条并排、共用同一种条长读法的一组补出来的分布，读者无从分辨。 */
export const ATTR_KINDS = [
  { v: 'cities', label: '省份' },
  { v: 'degrees', label: '学历' },
  { v: 'experience', label: '经验' },
] as const;

export type AttrKind = (typeof ATTR_KINDS)[number]['v'];

/** 要求程度四档。前三档"了解 / 熟练 / 精通"与论文同名同序，由要求强度分箱得出；
    第四档"无法确定"不是强度更低的一级，而是另一件事：招聘原文里没写程度词。
    把它摊进三档里去，等于把"没测到"说成"测到了是了解"。 */
export const PROF_LEVELS = ['了解', '熟练', '精通', '无法确定'] as const;

/** 第四档在数组里的下标。三档循环写 0..2 的地方一律用它划界 */
export const PROF_UNKNOWN = 3;

/** 未写明程度词的比例，按技能点的软硬类型给。
    硬技能的 JD 常写"熟悉/精通 Spring"，软技能几乎不写程度词 ——
    "具备良好的沟通能力"到底算了解还是精通，原文没有给出判据。

    这一组常数只在演示词表下走到。招聘数据接入后档位构成逐（岗位，技能）
    实测（realGraph.profShareOf），实测的未写明比例比这里假定的悬殊得多：
    "团队协作与协调"一项九成半未写明，"程序设计与软件工程"不足一成。 */
export const UNSPECIFIED_SHARE: Record<SkillType, number> = {
  hard: 0.18,
  soft: 0.55,
};

/**
 * 这一条（岗位，技能点）关系落在要求程度的哪一档。
 *
 * 实测的档位构成是一个分布，而图上一条关系只落一档，故按确定性哈希从该
 * 分布中取一档：同一对每次刷新落在同一档，聚合起来的四档比例即实测比例。
 * 取不到实测构成时退回演示口径 —— 前三档由要求强度分箱，第四档按软硬类型
 * 给一个占比。两条路径都不摊平"没写明"这一档：把它摊进三档里去，
 * 等于把"没测到"说成"测到了是了解"。
 *
 * @param jobName 岗位规范名，实测构成按名匹配
 * @param skillCode 该技能点所属技能的编码，实测构成落在技能一层
 */
export function profBand(
  r: number,
  key: string,
  type: SkillType | undefined,
  jobName?: string,
  skillCode?: string,
): 0 | 1 | 2 | 3 {
  return profBandFrom(
    jobName && skillCode ? profShareOf(jobName, skillCode) : null,
    r,
    key,
    type,
  );
}

/**
 * 已取到实测构成时的分档。
 *
 * 与 profBand 同一算法，差别只在构成由调用方传入。总览图要对每一对
 * （岗位，技能点）各判一次，本批是两百万对；构成落在技能一层，一个岗位下
 * 至多五十余种，由调用方按岗位缓存后可省下同一数量级的查表与拼串。
 */
export function profBandFrom(
  share: ProfShare | null,
  r: number,
  key: string,
  type: SkillType | undefined,
): 0 | 1 | 2 | 3 {
  const u = rand01(`${key}|deg`);
  if (share) {
    let acc = 0;
    for (let i = 0; i < 4; i++) {
      acc += share[i];
      if (u < acc) return i as 0 | 1 | 2 | 3;
    }
    return PROF_UNKNOWN;
  }
  return u < UNSPECIFIED_SHARE[type ?? 'hard'] ? PROF_UNKNOWN : bandOfStrength(r);
}

/** 要求强度三档的分界：相对该岗位自身最高项。仅演示口径下走到 */
const bandOfStrength = (r: number): 0 | 1 | 2 => (r >= 0.66 ? 2 : r >= 0.33 ? 1 : 0);

/** 要求程度的配色。
    前三档是一条青色阶，由浅到深对应了解 → 精通，同一色相只变明度 ——
    有序的量就该用有序的编码。第四档换成中性灰并在图上加点阵纹理：
    它不在这条序上，给它色阶里的任何一档都会被读成"比精通低两级"。

    这四个值跑过 dataviz 的配色校验（顺序色阶 + 中性档，浅色底）：
    相邻档在常视与三种色觉缺陷模拟下均可分，最浅的一档对白底
    对比度 2.1:1，高于 2:1 的下限 —— 上一版最浅档是 #dcf0ee（1.18:1），
    "了解"那一段在图上等于没画。 */
export const PROF_COLORS = ['#64c2b7', '#178e82', '#06564e', '#7d8798'];

/** 软硬两态。论文的岗位条分技术 / 基础两段，能力体系的软硬分类同样是两类 */
export const SKILL_TYPES: { v: SkillType; label: string }[] = [
  { v: 'hard', label: '硬技能' },
  { v: 'soft', label: '软技能' },
];

/** 岗位条里软硬两态的配色，同样两页共用。
    同一色相的两个明度档 —— 软硬是一条从硬到软的谱，不是两个无序的类。

    浅的那一档取自原三档阶的中间档 #4a7fe0（对白底 3.4:1）：
    两段之间只差一档时，原来最浅的 #93b4f5（2.03:1）与深档拉不开，
    一根条的右半截看着像是褪了色而不是换了一类。 */
export const MIX_COLORS: Record<SkillType, string> = {
  hard: '#1e40af',
  soft: '#4a7fe0',
};

/**
 * 聚类配色 —— 十个定性色，全部取自系统既有色族的深色档。
 * 字形要压在浅底上并且相邻两个必须能分开，所以不用浅色。
 */
export const CLUSTER_COLORS = [
  '#2563eb',
  '#0ea5b7',
  '#7c5cfc',
  '#d97706',
  '#16a34a',
  '#be185d',
  '#0f8f83',
  '#4a66a0',
  '#92400e',
  '#dc2626',
];

/** 薪资分档由低到高的六个色阶（论文用灰—蓝渐变表达薪资高低，此处沿用同一思路）。
    取紫罗兰色相（与四层色的任务层 --lay-task 同族），不取蓝：
    总览图三段并排，中段的岗位条已是蓝色的软硬两档，末段的属性条再用蓝阶，
    两列的分段在同一屏里读作同一种量。换一个色相之后，条属于哪一段一眼可分。
    六档的明度阶与原蓝阶逐档对齐（白底 1.20 → 9.60，相邻档 1.26 → 1.69），
    深浅读法因此不变。 */
export const SALARY_COLORS = ['#ece7ff', '#d7cbfd', '#b9a2fa', '#9573f0', '#6d47d8', '#4a2ba6'];

/** 沿半径方向的分箱数 —— 论文的地平线图把每个扇区切成等距区间后压平叠色 */
export const RADIAL_BINS = 14;

/* ==================== 基础量（全量岗位，算一次） ==================== */

export interface JobRow {
  id: string;
  name: string;
  /** 顶层大类 */
  cluster: string;
  /** 该岗位对应的招聘信息条数 */
  posts: number;
  /** 岗位在入图的各项技能上的构成占比，和为 1。总览图与相似度重排读它 */
  vector: number[];
  /**
   * 同一份构成按能力组汇总，10 维，和为 1。
   *
   * 聚类那一屏读这一档而不是上面那五十余维：字形上一条轴一项技能，
   * 五十余条轴的扇区在一个 60px 见方的字形里各占七度，轴名一律标不下，
   * 地平线图的分箱压到一像素以下 —— 论文的原图是 8 条轴。
   * 能力组是体系里现成的上一级（2 维度 → 10 组 → 54 项技能），
   * 汇总不引入任何新口径。
   */
  groupVector: number[];
  /** 技能点级要求权重：技能点 id → 权重（已按该岗位自身最大项归一）。
      归一后不足 ITEM_FLOOR 的尾项不入表 —— 下游按同一道阈值筛 */
  items: Map<string, number>;
  /** 软硬构成占比，两项和为 1 */
  mix: Record<SkillType, number>;
  attrs: JobAttributes;
  emerging: boolean;
}

export interface SkillAxis {
  id: string;
  name: string;
  /** 一级归属：能力维度（技术技能 / 基础通用技能） */
  dim: string;
  /** 该维度对应的软硬两态。维度与软硬分类等价，此处存下来只为定维度的次序 */
  type: SkillType;
  /** 二级归属：能力组，十个之一 */
  group: string;
  /** 图上用的短名：十个能力组名最长八个字，够用时不缩 */
  short: string;
}

/**
 * 能力维度的排列次序：技术技能在前，基础通用技能在后。
 *
 * 此前直接用体系文件里各维度的首次出现顺序，落到图上是基础通用一支排在最上面。
 * 那一支九成以上的岗位都要求，逐条的需求条长彼此接近，读者要先翻过一整段
 * 没有区分度的内容，才看得到真正把岗位区分开的技术那一支。
 *
 * 次序按软硬两态取，不写死维度名：软硬是体系里现成的一维，维度改名不影响这里。
 */
export const dimRank = (t: SkillType | undefined) =>
  SKILL_TYPES.findIndex((x) => x.v === (t ?? 'hard'));

interface Base {
  axes: SkillAxis[];
  /** 按能力组归并后的轴，10 条。聚类那一屏的字形用它 */
  groupAxes: SkillAxis[];
  /** 能力组 id → 其下技能点 id（按边权降序） */
  itemsOfGroup: Map<string, string[]>;
  /** 技能点 → 它归属的技能 id，取权重最高的那条边 */
  skillOfItem: Map<string, string>;
  jobs: Map<string, JobRow>;
  nodeById: Map<string, GraphNode>;
  /** 单个岗位在任一项技能上的最大构成占比 */
  maxShare: number;
  /** 同上，按能力组汇总之后的 —— 聚类字形半径方向的定义域 */
  groupMaxShare: number;
  /** 因属前瞻叠层而不入本图的技能，供图注写明撤了几条 */
  overlaySkills: { id: string; name: string }[];
}

let _base: Base | null = null;

/** 技能点级权重的入表下限，与下游筛选同一道阈值 */
const ITEM_FLOOR = 0.001;

/** 体系内缺维度或能力组归属时的落点。叠层新技能已在下面整支剔除，
    此处留给基准体系内万一缺归属的条目，免得图上又冒出无名的一支 */
const UNPLACED_DIM = '尚未归入体系';
const UNPLACED_GROUP = '未归组';

/** 六个字以内不缩写；更长的取前四字加省略号会读不出，改为按顿号/与字断开取前段 */
const shortName = (s: string) => (s.length <= 6 ? s : s.replace(/[与和及]/, '\n'));

export function exploreBase(): Base {
  if (_base) return _base;

  const d = getDataset();
  const nodeById = d.nodeById;

  /* 叠层新技能（PS- 起首）不进这张图。
     这一批是算法侧的前瞻叠层，用于生成下一批阶段数据的中间量，不属于本期
     实际生效的能力体系：它们在体系文件里没有维度与能力组归属，本图第一列的
     需求量（Σ 岗位要求权重 × 该岗位招聘条数）实测下来亦为零 —— 关联岗位零个、
     要求程度四档全为零。留在图上要为它们多长出一整支无名的枝，聚类那一屏
     还要多一条对每个岗位恒为零的死轴。

     这批条目本来的位置在全景图谱页的前瞻分析：那里问的是“论文提出了什么、
     市场跟上没有”，正是它们唯一有内容可读的地方。撤掉的项数由 unplacedSkills
     报出，图注据此写明撤了几条、去哪儿看，不作无声删除。

     注意与 T-DG「前瞻新技能」一组区分：那一组已写进 skills.json 的
     正式体系，底下的条目有完整的实测需求量，照常入图。 */
  const overlaySkills = d.nodes.filter((n) => n.kind === 'skill' && n.origin === 'overlay');
  const groups = d.nodes.filter((n) => n.kind === 'skill' && n.origin !== 'overlay');
  const axes: SkillAxis[] = groups.map((g) => ({
    id: g.id,
    name: g.name,
    dim: g.category || UNPLACED_DIM,
    type: g.skillType ?? 'hard',
    group: g.topCategory || g.category || UNPLACED_GROUP,
    short: shortName(g.name),
  }));
  const axisIdx = new Map(axes.map((a, i) => [a.id, i]));

  /* 按能力组归并出第二套轴。次序沿用技能轴里各组的首次出现顺序，
     即体系文件里的次序（技术四组在前、基础通用五组在后），不按份额重排 ——
     聚类的字形要在几十个之间互相比对，一条轴在甲字形上是第三根、
     在乙字形上是第七根，形状就没法比了。 */
  const groupAxes: SkillAxis[] = [];
  /** 技能轴下标 → 它所属能力组在 groupAxes 中的下标 */
  const groupOfAxis: number[] = new Array(axes.length).fill(-1);
  {
    const seen = new Map<string, number>();
    axes.forEach((a, i) => {
      let gi = seen.get(a.group);
      if (gi === undefined) {
        gi = groupAxes.length;
        seen.set(a.group, gi);
        groupAxes.push({
          id: `G:${a.group}`,
          name: a.group,
          dim: a.dim,
          type: a.type,
          group: a.group,
          short: shortName(a.group),
        });
      }
      groupOfAxis[i] = gi;
    });
  }

  /* 能力组 → 技能点。组权重要沿这一跳摊到项上：
     招聘信息写的是具体技能点，"能力组"这一层是体系归并出来的。 */
  const itemsOfGroup = new Map<string, string[]>();
  const spWeight = new Map<string, GraphEdge[]>();
  for (const e of d.edges) {
    if (e.kind !== 'S-SP') continue;
    const arr = spWeight.get(e.source);
    if (arr) arr.push(e);
    else spWeight.set(e.source, [e]);
  }
  for (const [gid, arr] of spWeight) {
    arr.sort((a, b) => b.effectiveWeight - a.effectiveWeight);
    itemsOfGroup.set(
      gid,
      arr.map((e) => e.target),
    );
  }
  const skillOfItem = new Map<string, string>();
  {
    const best = new Map<string, number>();
    for (const [gid, arr] of spWeight) {
      for (const e of arr) {
        if (e.effectiveWeight > (best.get(e.target) ?? -1)) {
          best.set(e.target, e.effectiveWeight);
          skillOfItem.set(e.target, gid);
        }
      }
    }
  }

  /* ---- 技能 → 其下技能点的分配表 ----

     岗位的技能点级权重 = 该岗位对某项技能的要求量 × 该技能点在这项技能内的边权
     占比。占比与硬技能占比两者都只随技能变，与岗位无关，故先按技能各算一遍：
     否则一百余个岗位各自把三万条 S-SP 边重算一遍，仅这一段就是数百万次除法。

     技能点另编一套连续下标，逐岗位的累加落在定长数组上 —— 以字符串为键逐条
     写 Map 时，同一批累加要慢一个数量级，且一百余个岗位各留一份两万项的 Map，
     内存也吃不消。 */
  const itemIds: string[] = [];
  const itemIdx = new Map<string, number>();
  const idxOfItem = (id: string) => {
    let i = itemIdx.get(id);
    if (i === undefined) {
      i = itemIds.length;
      itemIds.push(id);
      itemIdx.set(id, i);
    }
    return i;
  };
  /** 技能 id → [各技能点的下标, 各技能点的占比, 其中硬技能占比之和] */
  const spreadOf = new Map<string, { idx: Int32Array; part: Float64Array; hard: number }>();
  for (const [gid, arr] of spWeight) {
    const tot = arr.reduce((a, e) => a + e.effectiveWeight, 0);
    const idx = new Int32Array(arr.length);
    const part = new Float64Array(arr.length);
    let hard = 0;
    for (let k = 0; k < arr.length; k++) {
      const e = arr[k];
      idx[k] = idxOfItem(e.target);
      part[k] = tot > 0 ? e.effectiveWeight / tot : 1 / Math.max(arr.length, 1);
      if ((nodeById.get(e.target)?.skillType ?? 'hard') === 'hard') hard += part[k];
    }
    spreadOf.set(gid, { idx, part, hard });
  }

  const jobs = new Map<string, JobRow>();
  let maxShare = 0;
  let groupMaxShare = 0;

  for (const n of d.nodes) {
    if (n.kind !== 'job' || !n.attrs) continue;
    /* 叠层新岗位不进这张图。

       本页的三段全部落在市场读数上：中段是招聘信息条数，右段是省份、学历、
       经验与薪资四组分档，聚类作用于由实测关联边算出的能力构成。新岗位尚未
       进入招聘市场，这几项一概没有实测值；其末窗仅有的一条任务关联边还会让
       它的构成向量由单一分量定，在聚类里成为一个与谁都不像的点。
       新岗位自有其去处 —— 岗位洞察页的新岗位发现与定义，那里读的是它的证据
       与推导构成，不是市场读数。 */
    if (n.emerging) continue;

    const w = jobSkillWeights(n.id, NOW, d.edges, d.signalMap, 1);

    const vector = new Array(axes.length).fill(0);
    const acc = new Float64Array(itemIds.length);
    const mix: Record<SkillType, number> = { hard: 0, soft: 0 };

    for (const [gid, v] of w) {
      const ai = axisIdx.get(gid);
      if (ai === undefined || v.total <= 0) continue;
      vector[ai] += v.total;

      const sp = spreadOf.get(gid);
      if (!sp) continue;
      for (let k = 0; k < sp.idx.length; k++) acc[sp.idx[k]] += v.total * sp.part[k];
      mix.hard += v.total * sp.hard;
      mix.soft += v.total * (1 - sp.hard);
    }

    /* 一条实测边也没有的岗位不进这张图。

       本图的三段全部由该岗位的能力构成推出，而叠层新岗位尚未进入招聘市场，
       构成向量整条为零：它在总览图上是一行空条，在聚类里是一个与任何岗位都不像
       的零向量 —— 后者会实实在在地拉偏簇心。新岗位自有其去处（岗位洞察页的
       新岗位发现与定义），那里读的是它的证据与推导构成，不是市场读数。 */
    const vsum = vector.reduce((a, b) => a + b, 0);
    if (vsum <= 0) continue;
    for (let i = 0; i < vector.length; i++) vector[i] /= vsum;
    maxShare = Math.max(maxShare, ...vector);

    /* 组级构成：归一化之后再汇总，两档因而同为占比，和同为 1 */
    const groupVector = new Array(groupAxes.length).fill(0);
    for (let i = 0; i < vector.length; i++) {
      const gi = groupOfAxis[i];
      if (gi >= 0) groupVector[gi] += vector[i];
    }
    groupMaxShare = Math.max(groupMaxShare, ...groupVector);

    const msum = mix.hard + mix.soft;
    if (msum > 0) for (const k of Object.keys(mix) as SkillType[]) mix[k] /= msum;

    /* 技能点权重按该岗位自身的最大项归一：要求强度分档问的是
       "这项能力在这个岗位的要求里排多高"，不是跨岗位比绝对值。
       归一后低于 ITEM_FLOOR 的不入表：下游一律按这道阈值筛，
       留下来只是让每个岗位多背两万条读不出差别的尾项。 */
    let imax = 1e-9;
    for (let i = 0; i < acc.length; i++) if (acc[i] > imax) imax = acc[i];
    const items = new Map<string, number>();
    for (let i = 0; i < acc.length; i++) {
      const v = acc[i] / imax;
      if (v > ITEM_FLOOR) items.set(itemIds[i], v);
    }

    jobs.set(n.id, {
      id: n.id,
      name: n.name,
      cluster: n.topCategory ?? n.cluster ?? '未分类',
      posts: n.attrs.postCount,
      vector,
      groupVector,
      items,
      mix,
      attrs: n.attrs,
      emerging: !!n.emerging,
    });
  }

  _base = {
    axes,
    groupAxes,
    itemsOfGroup,
    skillOfItem,
    jobs,
    nodeById,
    maxShare: Math.max(maxShare, 0.001),
    groupMaxShare: Math.max(groupMaxShare, 0.001),
    overlaySkills: overlaySkills.map((s) => ({ id: s.id, name: s.name })),
  };
  return _base;
}

/* ==================== 视图一：能力—岗位总览 ==================== */

export interface SkillRow {
  id: string;
  kind: 'group' | 'item';
  name: string;
  dim: string;
  /** 技能点所属的能力组名（能力组行为自身） */
  group: string;
  skillType?: SkillType;
  definition?: string;
  /** 需求量：Σ 岗位要求权重 × 该岗位招聘条数 */
  demand: number;
  /** 需求量在同层内的占比 0–1 */
  share: number;
  /** 要求程度四档的条数（了解 / 熟练 / 精通 / 无法确定） */
  prof: [number, number, number, number];
  /** 要求这项能力的岗位：jobId → 权重 */
  jobs: Map<string, number>;
}

export interface AttrRow {
  bucket: string;
  /** 落在该分档下的招聘条数 */
  posts: number;
  /** 六个薪资档各自的条数 */
  salary: number[];
  /** 各岗位在该分档下的条数，供连线取宽度 */
  byJob: Map<string, number>;
}

/** 属性行由 buildAttrGroups 单独产出：它统计的岗位范围与前两列不同（只算选中的那批） */
export interface OverviewModel {
  /** 技能点级的行，按维度 → 能力组 → 需求量排好序 */
  itemRows: SkillRow[];
  /** 能力组级的行 */
  groupRows: SkillRow[];
  jobRows: (JobRow & { share: number })[];
  salaryBands: string[];
  totalPosts: number;
}

/**
 * @param keepOrder 保留传入的岗位顺序。右键"按相似度重排"就是靠它生效的 ——
 *   这里若一律按招聘条数重排，上游排好的顺序会被悄悄丢掉，
 *   界面上说了"已重排"而图没动，是最难查的一类不一致。
 */
export function buildOverview(jobIds: string[], keepOrder = false): OverviewModel {
  const base = exploreBase();
  const rows = jobIds.map((id) => base.jobs.get(id)).filter((j): j is JobRow => !!j);
  const totalPosts = rows.reduce((a, j) => a + j.posts, 0) || 1;

  /* ---- 技能点行 ---- */
  const itemAcc = new Map<string, SkillRow>();
  /* 技能到技能点是多对多，归属取权重最高的那一条边，读作"这个工具主要属于
     哪一项技能"。按遍历顺序覆盖则归属由边的排列先后决定。 */
  const groupOfItem = base.skillOfItem;

  /* 技能点 → 其父技能的体系编码。档位构成按这个编码查，而查表的对数是
     岗位数乘技能点数（本批两百万对）；编码本身只随技能点变，故先算一遍存下来，
     内层循环里不再重复取父节点、剥前缀、拼串。 */
  const codeOfItem = new Map<string, string | undefined>();
  const codeFor = (iid: string) => {
    let c = codeOfItem.get(iid);
    if (c === undefined && !codeOfItem.has(iid)) {
      c = codeOf(base.nodeById.get(groupOfItem.get(iid) ?? '')?.id);
      codeOfItem.set(iid, c);
    }
    return c;
  };

  for (const j of rows) {
    /* 档位构成落在（岗位, 技能）一层，一个岗位下至多五十余项技能，
       按岗位缓存后每岗只查这几十次，不必逐技能点各查一次 */
    const shareCache = new Map<string, ProfShare | null>();
    for (const [iid, w] of j.items) {
      if (w <= ITEM_FLOOR) continue;
      let r = itemAcc.get(iid);
      if (!r) {
        const n = base.nodeById.get(iid);
        const g = base.nodeById.get(groupOfItem.get(iid) ?? '');
        r = {
          id: iid,
          kind: 'item',
          name: n?.name ?? iid,
          dim: g?.category ?? '',
          group: g?.name ?? '',
          skillType: n?.skillType,
          definition: n?.definition,
          demand: 0,
          share: 0,
          prof: [0, 0, 0, 0],
          jobs: new Map(),
        };
        itemAcc.set(iid, r);
      }
      r.demand += w * j.posts;
      const code = codeFor(iid);
      let share = code === undefined ? null : shareCache.get(code);
      if (share === undefined) {
        share = code === undefined ? null : profShareOf(j.name, code);
        if (code !== undefined) shareCache.set(code, share);
      }
      r.prof[profBandFrom(share, w, `${j.id}|${iid}`, r.skillType)] += j.posts;
      r.jobs.set(j.id, w);
    }
  }

  const itemRows = [...itemAcc.values()];
  const itemMax = Math.max(...itemRows.map((r) => r.demand), 1e-9);
  for (const r of itemRows) r.share = r.demand / itemMax;

  /* ---- 能力组行：由项行汇总，两层的数必须同源 ---- */
  const groupAcc = new Map<string, SkillRow>();
  for (const a of base.axes) {
    groupAcc.set(a.id, {
      id: a.id,
      kind: 'group',
      name: a.name,
      dim: a.dim,
      group: a.name,
      demand: 0,
      share: 0,
      prof: [0, 0, 0, 0],
      jobs: new Map(),
    });
  }
  for (const r of itemRows) {
    const g = groupAcc.get(groupOfItem.get(r.id) ?? '');
    if (!g) continue;
    g.demand += r.demand;
    for (let i = 0; i < g.prof.length; i++) g.prof[i] += r.prof[i];
  }
  const groupRows = [...groupAcc.values()];

  /* ---- 技能行连到哪些岗位 ----
     取该岗位的招聘信息里写到这项技能的条数占比（实测覆盖率）。

     此处原先由技能点行的权重逐项取最大值汇总上来。技能点权重是按岗位自身
     最强项归一的岗位内相对量，取最大值再跨岗位相比，等于把各岗位的"内部排名"
     并列成一列绝对值：本批数据上每一项技能对四十个岗位的这个数都落在同一档内
     （如"创造力与创新思维"在四十个岗位上一律为 1.000），两条阈值因而恒为真，
     点任一项技能整列岗位一并点亮，图上读不出这项技能究竟由谁要求。

     覆盖率是（岗位, 技能）这一层唯一逐条统计出来的量，值域自零点几到零点九九，
     与图上要求程度的分档同出一份 skillProf、分母与观测窗口一致。
     六项纯素养类技能（终身学习、创造力、归纳演绎等）在招聘信息里未被逐条标注，
     其覆盖率为空，故这几行不连岗位 —— 它们的需求量条来自技能点一层的关联边，
     那一层的证据不构成"某个岗位要求这项技能"。 */
  for (const g of groupRows) {
    const code = codeOf(g.id);
    if (code === undefined) continue;
    for (const j of rows) {
      const cov = skillCoverageOf(j.name, code);
      if (cov > 0) g.jobs.set(j.id, cov);
    }
  }
  const gMax = Math.max(...groupRows.map((r) => r.demand), 1e-9);
  for (const r of groupRows) r.share = r.demand / gMax;

  /* 排序：维度 → 组内需求量降序。论文里技能条按占比自上而下排，
     这里多一层维度分组，否则技术与通用两类会交替出现。
     维度之间按 dimRank 定次序（技术在前），与左侧的能力体系树同序。 */
  const dimOrder = new Map(base.axes.map((a) => [a.dim, a.dim]));
  const typeOfDim = new Map(base.axes.map((a) => [a.dim, a.type]));
  const dims = [...new Set(base.axes.map((a) => a.dim))].sort(
    (a, b) => dimRank(typeOfDim.get(a)) - dimRank(typeOfDim.get(b)),
  );
  const gRank = new Map(
    [...groupRows].sort((a, b) => b.demand - a.demand).map((r, i) => [r.name, i] as const),
  );
  const cmp = (a: SkillRow, b: SkillRow) =>
    dims.indexOf(a.dim) - dims.indexOf(b.dim) ||
    (gRank.get(a.group) ?? 0) - (gRank.get(b.group) ?? 0) ||
    b.demand - a.demand;
  itemRows.sort(cmp);
  groupRows.sort(cmp);
  void dimOrder;

  /* ---- 岗位行 ---- */
  const jobRows = rows.map((j) => ({ ...j, share: j.posts / totalPosts }));
  if (!keepOrder) jobRows.sort((a, b) => b.posts - a.posts);

  return {
    itemRows,
    groupRows,
    jobRows,
    salaryBands: Object.keys(rows[0]?.attrs.salaryBands ?? {}),
    totalPosts,
  };
}

/* -------------------- 属性行 --------------------
   一个岗位的招聘条数按该岗位在该属性上的分布摊到各分档，
   再按薪资分布摊到六个薪资档。两个分布相互独立是这里唯一的假设。 */
function attrRowsOf(rows: JobRow[], attr: AttrKind, cityAllow?: Set<string> | null): AttrRow[] {
  const salaryBands = Object.keys(rows[0]?.attrs.salaryBands ?? {});
  const bucketAcc = new Map<string, AttrRow>();
  for (const j of rows) {
    /* 城市级勾选生效时逐岗位重算省份分布：被勾掉的城市连同它那部分条数
       一并退出分母，而不是先按全量算好再截一段 —— 后者会让剩下各省的
       占比之和小于一，条长读作“占该岗位的百分之多少”即不成立 */
    const dist: Distribution =
      attr === 'cities' && cityAllow
        ? (provinceShare(j.name, cityAllow) as Distribution)
        : (j.attrs[attr] as Distribution);
    for (const [b, p] of Object.entries(dist)) {
      let r = bucketAcc.get(b);
      if (!r) {
        r = { bucket: b, posts: 0, salary: new Array(salaryBands.length).fill(0), byJob: new Map() };
        bucketAcc.set(b, r);
      }
      const n = j.posts * p;
      r.posts += n;
      r.byJob.set(j.id, n);
      salaryBands.forEach((sb, k) => {
        r!.salary[k] += n * (j.attrs.salaryBands[sb] ?? 0);
      });
    }
  }
  /* 省份：有条数的按条数降序排在前，为零的按行政区划次序排在后，“其他”垫底。
     全按行政区划排会让有数的十来个省被零散的空行隔开，一列读下来断断续续；
     全按条数排则把零行也卷进排序，换一批数据时空行的先后无谓地跳动。
     为零的省仍照列不误 —— 零本身是一条读数，省掉它读者无从知道
     这一维是没有还是没画。
     学历与经验有天然次序，按词表顺序排；省份无天然次序，按条数排。 */
  if (attr === 'cities') {
    for (const p of PROVINCE_AXIS) {
      if (!bucketAcc.has(p)) {
        bucketAcc.set(p, {
          bucket: p,
          posts: 0,
          salary: new Array(salaryBands.length).fill(0),
          byJob: new Map(),
        });
      }
    }
    const order = new Map(PROVINCE_AXIS.map((p, i) => [p, i]));
    /* 末档“其他”只在有条数时上轴：它收的不是省，一个空行既无读数、
       又说不出收的是什么，列出来只会让人以为省份归类漏了一批 */
    return PROVINCE_AXIS.filter((p) => p !== PROVINCE_OTHER || (bucketAcc.get(p)?.posts ?? 0) > 0)
      .map((p) => bucketAcc.get(p)!)
      .sort((a, b) => {
        const za = a.posts <= 0 ? 1 : 0;
        const zb = b.posts <= 0 ? 1 : 0;
        if (za !== zb) return za - zb;
        if (za) return (order.get(a.bucket) ?? 0) - (order.get(b.bucket) ?? 0);
        return b.posts - a.posts;
      });
  }
  /* 学历有天然次序，按学历轴排 —— 不按第一个岗位的键序，那一份缺哪档就漏哪档。
     末档“学历不限”留在最后：它不是最低的一级，是一条独立的读数 */
  if (attr === 'degrees') {
    const order = new Map(DEGREE_AXIS.map((d, i) => [d, i]));
    return [...bucketAcc.values()].sort(
      (a, b) => (order.get(a.bucket) ?? 99) - (order.get(b.bucket) ?? 99),
    );
  }
  return [...bucketAcc.values()].sort((a, b) => {
    // 经验有天然次序，按词表顺序排；省份没有，按条数排
    if (attr !== 'experience') return b.posts - a.posts;
    const src = Object.keys(rows[0]?.attrs[attr] ?? {});
    return src.indexOf(a.bucket) - src.indexOf(b.bucket);
  });
}

/* -------------------- 四维属性同屏 --------------------
   论文的属性栏一次只显示一维，靠一组单选按钮换维；换维即换掉整栏，
   于是"北京 + 本科"这类跨维的条件无从表达 —— 每一维只在轮到自己时才存在。
   四维在纵向上共 25 个分档，按 27px 的行距合计 675px，落在这张图 930px 的
   可用高度内，因此改为四组一次画完：维度不再是显示模式，而是四个并列的分组。 */

export interface AttrGroup {
  kind: AttrKind;
  label: string;
  rows: AttrRow[];
}

/**
 * 三维分档各算一遍。三组共用同一条量纲（落在该分档的招聘条数），故可共用标尺。
 *
 * @param cityAllow 省份一组的城市白名单。缺省即全部城市入账
 */
export function buildAttrGroups(jobIds: string[], cityAllow?: Set<string> | null): AttrGroup[] {
  const base = exploreBase();
  const rows = jobIds.map((id) => base.jobs.get(id)).filter((j): j is JobRow => !!j);
  return ATTR_KINDS.map((k) => ({
    kind: k.v,
    label: k.label,
    rows: attrRowsOf(rows, k.v, k.v === 'cities' ? cityAllow : null),
  }));
}

/**
 * 给定这批岗位，逐座城市的招聘信息条数。
 *
 * 与省份条同一口径：一个岗位的条数按它自己的城市分布摊到各座城，再逐岗位相加。
 * 城市下拉此前直接读全样本的逐城条数，而省份条只统计当前列出的那批岗位 ——
 * 于是一列上下两个数出自两个分母，某省的下拉里会出现比该省条数还大的城市。
 */
export function cityCountsIn(jobIds: string[]): Record<string, number> {
  const base = exploreBase();
  const acc: Record<string, number> = {};
  for (const id of jobIds) {
    const j = base.jobs.get(id);
    if (!j) continue;
    const cities = cityCountsOf(j.name);
    let total = 0;
    for (const n of Object.values(cities)) total += n;
    if (total <= 0) continue;
    for (const [c, n] of Object.entries(cities)) acc[c] = (acc[c] ?? 0) + (j.posts * n) / total;
  }
  return acc;
}

/** 三维各自的已选分档 */
export type AttrPicks = Record<AttrKind, Set<string>>;

export const emptyPicks = (): AttrPicks => ({
  cities: new Set(),
  degrees: new Set(),
  experience: new Set(),

});

export const countPicks = (p: AttrPicks): number =>
  ATTR_KINDS.reduce((s, k) => s + p[k.v].size, 0);

/**
 * 按四维分档筛岗位。
 *
 * 单维的判据沿用原来那一条：所选分档上的概率之和不低于均匀水平
 * （所选档数 ÷ 该维总档数）—— 聚合数据下等价于"这个岗位确实偏向这个条件"，
 * 而不是"它有一点点落在这里"。
 *
 * 多维之间取交集：同维内的分档是同一个问题的几个答案（在北京，或在上海），
 * 不同维问的是不同的问题（在哪座城市，要什么学历），两者不能并成一列来判。
 */
export function filterByPicks(jobIds: string[], picks: AttrPicks): string[] {
  const base = exploreBase();
  const active = ATTR_KINDS.filter((k) => picks[k.v].size > 0);
  if (!active.length) return jobIds;
  return jobIds.filter((id) => {
    const j = base.jobs.get(id);
    if (!j) return false;
    return active.every((k) => {
      const dist = j.attrs[k.v] as Distribution;
      const sel = picks[k.v];
      const floor = sel.size / (Object.keys(dist).length || 1);
      let s = 0;
      for (const b of sel) s += dist[b] ?? 0;
      return s >= floor;
    });
  });
}

/* ==================== 视图二：岗位聚类 ==================== */

export interface ClusterInfo {
  id: number;
  /** 代表岗位（affinity propagation 的样本中心） */
  exemplar: string;
  label: string;
  jobIds: string[];
  posts: number;
  color: string;
  /** 各轴的平均构成占比 */
  mean: number[];
  /** 各轴的地平线图数据：dist[axis][bin] = 落在该分箱的招聘条数 */
  dist: number[][];
  /** 二维投影坐标（0–1 归一化后） */
  xy: [number, number];
}

export interface ClusterModel {
  clusters: ClusterInfo[];
  /** 地平线图每加深一层代表多少条招聘信息 */
  levelStep: number;
  /** 半径方向的定义域上界（构成占比） */
  domainMax: number;
  iterations: number;
  /* 本次聚类实际耗费的毫秒数。这一屏在浏览器内实时算完，换一批岗位就重算一遍，
     没有预置的簇；此数与迭代轮数一并挂到界面上，是为让"点下去即刻出结果"
     这件事可核对 —— 岗位至多百余个、构成向量十维，近邻传播在这个规模上
     本就是毫秒量级。同一批岗位第二次进来直接取缓存，故此处记的是首次算出它的耗时。 */
  ms: number;
}

const clusterCache = new Map<string, ClusterModel>();

/**
 * Affinity propagation —— 论文选它的理由是不必预先给定簇数。
 * 相似度取负的欧氏距离平方；偏好（对角线）取相似度分布的某个分位数：
 * 取中位数得到较多的小簇，取最小值得到较少的大簇。
 */
function affinityPropagation(vecs: number[][], prefQ = 0.5, damping = 0.6, maxIter = 90) {
  const n = vecs.length;
  const S: number[][] = Array.from({ length: n }, () => new Array(n).fill(0));
  const flat: number[] = [];
  for (let i = 0; i < n; i++) {
    for (let k = 0; k < n; k++) {
      if (i === k) continue;
      let s = 0;
      for (let t = 0; t < vecs[i].length; t++) {
        const d = vecs[i][t] - vecs[k][t];
        s += d * d;
      }
      S[i][k] = -s;
      if (i < k) flat.push(-s);
    }
  }
  flat.sort((a, b) => a - b);
  const pref = flat.length ? flat[Math.min(flat.length - 1, Math.floor(prefQ * flat.length))] : -1;
  for (let i = 0; i < n; i++) S[i][i] = pref;

  const R: number[][] = Array.from({ length: n }, () => new Array(n).fill(0));
  const A: number[][] = Array.from({ length: n }, () => new Array(n).fill(0));
  let last = '';
  let iter = 0;

  for (iter = 0; iter < maxIter; iter++) {
    // responsibility
    for (let i = 0; i < n; i++) {
      let max1 = -Infinity;
      let max2 = -Infinity;
      let arg1 = -1;
      for (let k = 0; k < n; k++) {
        const v = A[i][k] + S[i][k];
        if (v > max1) {
          max2 = max1;
          max1 = v;
          arg1 = k;
        } else if (v > max2) max2 = v;
      }
      for (let k = 0; k < n; k++) {
        const r = S[i][k] - (k === arg1 ? max2 : max1);
        R[i][k] = damping * R[i][k] + (1 - damping) * r;
      }
    }
    // availability
    for (let k = 0; k < n; k++) {
      let sum = 0;
      for (let i = 0; i < n; i++) if (i !== k) sum += Math.max(0, R[i][k]);
      for (let i = 0; i < n; i++) {
        const a =
          i === k ? sum : Math.min(0, R[k][k] + sum - Math.max(0, R[i][k]) - Math.max(0, R[k][k]));
        A[i][k] = damping * A[i][k] + (1 - damping) * a;
      }
    }
    // 连续两轮样本中心不变即收敛
    const ex: number[] = [];
    for (let k = 0; k < n; k++) if (R[k][k] + A[k][k] > 0) ex.push(k);
    const sig = ex.join(',');
    if (sig && sig === last) break;
    last = sig;
  }

  let exemplars: number[] = [];
  for (let k = 0; k < n; k++) if (R[k][k] + A[k][k] > 0) exemplars.push(k);
  // 一个样本中心都没浮出来时退回"离全体质心最近的那一个"，不让视图空掉
  if (exemplars.length === 0) exemplars = [0];

  const assign = new Array(n).fill(0);
  for (let i = 0; i < n; i++) {
    let best = exemplars[0];
    let bs = -Infinity;
    for (const k of exemplars) {
      if (S[i][k] > bs) {
        bs = S[i][k];
        best = k;
      }
    }
    assign[i] = best;
  }
  return { exemplars, assign, iterations: iter + 1 };
}

/**
 * 簇数要落在能读的范围里。AP 不预设簇数是它的优点，但簇数完全由
 * 偏好取值决定：同一个偏好，266 个岗位聚出 8 簇是好读的，
 * 筛到 15 个岗位就只剩 1 簇 —— 一个字形的"聚类视图"什么也没说。
 * 因此按分位数逐档试，取第一个落在 [lo, hi] 内的结果；都不落在里面时
 * 取离区间中点最近的那一档。档位固定、顺序固定，结果仍然是确定性的。
 */
function clusterWithinRange(vecs: number[][], lo = 3, hi = 12) {
  const target = (lo + hi) / 2;
  let best: ReturnType<typeof affinityPropagation> | null = null;
  for (const q of [0.5, 0.25, 0.08, 0, 0.75, 0.92]) {
    const r = affinityPropagation(vecs, q);
    const k = r.exemplars.length;
    if (k >= lo && k <= hi) return r;
    if (!best || Math.abs(k - target) < Math.abs(best.exemplars.length - target)) best = r;
  }
  return best!;
}

/** 二维投影：对簇中心做经典 MDS，簇少时退化为一维摆开 */
function projectCenters(centers: number[][]): [number, number][] {
  const n = centers.length;
  if (n === 1) return [[0.5, 0.5]];
  const D2: number[][] = Array.from({ length: n }, () => new Array(n).fill(0));
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      let s = 0;
      for (let t = 0; t < centers[i].length; t++) {
        const d = centers[i][t] - centers[j][t];
        s += d * d;
      }
      D2[i][j] = D2[j][i] = s;
    }
  }
  const rowMean = D2.map((r) => r.reduce((a, b) => a + b, 0) / n);
  const grand = rowMean.reduce((a, b) => a + b, 0) / n;
  const B = D2.map((r, i) => r.map((v, j) => -0.5 * (v - rowMean[i] - rowMean[j] + grand)));

  const power = (exclude: number[][] | null) => {
    let v = Array.from({ length: n }, (_, i) => Math.sin(i * 12.9898) % 1 || 0.5);
    let lam = 0;
    for (let it = 0; it < 120; it++) {
      let nv = B.map((row) => row.reduce((a, b, j) => a + b * v[j], 0));
      if (exclude) {
        for (const e of exclude) {
          const dot = nv.reduce((a, b, i) => a + b * e[i], 0);
          nv = nv.map((x, i) => x - dot * e[i]);
        }
      }
      const norm = Math.sqrt(nv.reduce((a, b) => a + b * b, 0)) || 1;
      v = nv.map((x) => x / norm);
      lam = norm;
    }
    return { v, lam };
  };
  const e1 = power(null);
  const e2 = power([e1.v]);
  const xs = e1.v.map((x) => x * Math.sqrt(Math.max(e1.lam, 1e-9)));
  const ys = e2.v.map((x) => x * Math.sqrt(Math.max(e2.lam, 1e-9)));

  const norm = (arr: number[]) => {
    const lo = Math.min(...arr);
    const hi = Math.max(...arr);
    const span = hi - lo || 1;
    return arr.map((v) => (v - lo) / span);
  };
  const nx = norm(xs);
  const ny = norm(ys);
  return nx.map((x, i) => [x, ny[i]] as [number, number]);
}

export function buildClusters(jobIds: string[]): ClusterModel {
  const key = jobIds.join('|');
  const hit = clusterCache.get(key);
  if (hit) return hit;

  const t0 = performance.now();
  const base = exploreBase();
  const rows = jobIds.map((id) => base.jobs.get(id)).filter((j): j is JobRow => !!j);
  /* 这一屏自上而下一律走组级构成：聚类的输入、字形的轴、半径的定义域、
     地平线图的分箱，四处同源。混用两档会得出“按 49 维聚出来的簇，
     用 9 条轴画出的形状”，簇内为何相像便无从对照。 */
  const domainMax = base.groupMaxShare;

  if (rows.length === 0) {
    const empty: ClusterModel = { clusters: [], levelStep: 1, domainMax, iterations: 0, ms: 0 };
    clusterCache.set(key, empty);
    return empty;
  }

  const { exemplars, assign, iterations } = clusterWithinRange(rows.map((r) => r.groupVector));

  const byEx = new Map<number, number[]>();
  assign.forEach((k, i) => {
    const arr = byEx.get(k);
    if (arr) arr.push(i);
    else byEx.set(k, [i]);
  });

  const ordered = [...byEx.entries()].sort(
    (a, b) =>
      b[1].reduce((s, i) => s + rows[i].posts, 0) - a[1].reduce((s, i) => s + rows[i].posts, 0),
  );

  const centers = ordered.map(([, idxs]) => {
    const m = new Array(base.groupAxes.length).fill(0);
    let w = 0;
    for (const i of idxs) {
      for (let t = 0; t < m.length; t++) m[t] += rows[i].groupVector[t] * rows[i].posts;
      w += rows[i].posts;
    }
    return m.map((v) => v / (w || 1));
  });
  const xy = projectCenters(centers);

  let maxBin = 0;
  const clusters: ClusterInfo[] = ordered.map(([ex, idxs], ci) => {
    const dist = base.groupAxes.map(() => new Array(RADIAL_BINS).fill(0));
    for (const i of idxs) {
      rows[i].groupVector.forEach((v, t) => {
        const b = Math.min(RADIAL_BINS - 1, Math.max(0, Math.round((v / domainMax) * (RADIAL_BINS - 1))));
        dist[t][b] += rows[i].posts;
      });
    }
    for (const row of dist) for (const v of row) maxBin = Math.max(maxBin, v);
    void ex;
    return {
      id: ci,
      exemplar: rows[exemplars.includes(ex) ? ex : idxs[0]].id,
      label: rows[exemplars.includes(ex) ? ex : idxs[0]].name,
      jobIds: idxs.map((i) => rows[i].id),
      posts: idxs.reduce((s, i) => s + rows[i].posts, 0),
      color: CLUSTER_COLORS[ci % CLUSTER_COLORS.length],
      mean: centers[ci],
      dist,
      xy: xy[ci],
    };
  });

  /* 地平线图的层高：论文固定每 20 条一层。这里的条数量级差得远，
     改为按最高分箱现算，让最深的分箱恰好落在 5 层左右 —— 层数太多会糊成一片。 */
  const raw = Math.max(maxBin / 5, 1);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const levelStep = Math.max(1, Math.round(raw / mag) * mag);

  const model: ClusterModel = {
    clusters,
    levelStep,
    domainMax,
    iterations,
    ms: performance.now() - t0,
  };
  clusterCache.set(key, model);
  return model;
}

/* ==================== 视图三：岗位分布图 ==================== */

export interface PostCell {
  jobId: string;
  jobName: string;
  /** 学历门槛（横轴） */
  cc: string;
  /** 薪资档（纵轴） */
  band: string;
  /** 落在该格的招聘条数 */
  posts: number;
  vector: number[];
  emerging: boolean;
}

/**
 * 把一个簇里的岗位摊到"学历门槛 × 薪资档"的格子上。
 * 论文的做法是把簇内每一条招聘信息按其两个属性落点，同格内随机散开；
 * 这里没有逐条广告的记录，只有两个边缘分布，因此按两者的乘积拆分，
 * 低于阈值的格不画 —— 画出来只是一个读不出数的点。
 *
 * 横轴此前取技术方向。那一维与这张图的纵轴问的是同一类问题 ——
 * 技术方向说的是"做哪一摊活"，簇本身已经是按能力构成聚出来的，
 * 同一簇内的岗位在技术方向上本就聚在一两列里，整张图因而摊不开。
 * 换成学历门槛之后，两条轴一条问"要什么学历"、一条问"给多少钱"，
 * 二者正是求职者据以取舍的那一对，格子也铺得开。
 *
 * 学历分布由招聘正文的门槛语抽出（realGraph.degreeShare），本批覆盖
 * 七成以上的招聘条数，与薪资一样是实测读数，不是补齐的一层。
 *
 * 每格只画条数最多的前 perCell 个岗位。不设这个上限的话，
 * 五十个岗位铺满三十六个格子会画出一千多个字形：字形本身还在，
 * 但"看形状"这件事已经做不到了，而看形状正是这张图存在的理由。
 * 省掉了几个如实回报给调用方，写在图外。
 */
export function buildPostCells(
  jobIds: string[],
  perDegree = 2,
  perCell = 5,
  degreeFloor = 0.04,
): { cells: PostCell[]; jobsShown: number; hiddenJobs: number; coverage: number } {
  const base = exploreBase();
  const byCell = new Map<string, PostCell[]>();
  const covers: number[] = [];

  for (const id of jobIds) {
    const j = base.jobs.get(id);
    if (!j) continue;
    /* 逐个学历档各取该岗位最主要的几个薪资档，而不是在三十六个格子里
       统一取乘积最大的前几个。

       学历分布高度集中：本批七成落在本科一档，大专两成，硕士以上不足一成。
       按乘积统一排名时，前几名一律是"本科 × 各薪资档"，大专以上的档一格
       也进不来 —— 横轴上只剩一两列，而这张图的横轴正是学历。
       逐档取之后，凡占该岗位四个百分点以上的学历档都在图上有落点，
       列数因而由学历分布本身决定。 */
    const degs = Object.entries(j.attrs.degrees)
      .filter(([, p]) => p >= degreeFloor)
      .sort((a, b) => b[1] - a[1]);
    const bands = Object.entries(j.attrs.salaryBands)
      .sort((a, b) => b[1] - a[1])
      .slice(0, perDegree);
    const keep: PostCell[] = [];
    for (const [cc, pc] of degs) {
      for (const [band, pb] of bands) {
        keep.push({
          jobId: j.id,
          jobName: j.name,
          cc,
          band,
          posts: j.posts * pc * pb,
          /* 与聚类同一档：格子里的小字形与簇字形要能并排比形状 */
          vector: j.groupVector,
          emerging: j.emerging,
        });
      }
    }
    covers.push(keep.reduce((s, c) => s + c.posts, 0) / (j.posts || 1));
    for (const cell of keep) {
      const k = `${cell.cc}|${cell.band}`;
      const arr = byCell.get(k);
      if (arr) arr.push(cell);
      else byCell.set(k, [cell]);
    }
  }

  const cells: PostCell[] = [];
  for (const arr of byCell.values()) {
    arr.sort((a, b) => b.posts - a.posts);
    cells.push(...arr.slice(0, perCell));
  }
  const shown = new Set(cells.map((c) => c.jobId));
  covers.sort((a, b) => a - b);
  return {
    cells: cells.sort((a, b) => b.posts - a.posts),
    jobsShown: shown.size,
    hiddenJobs: jobIds.length - shown.size,
    coverage: covers.length ? covers[Math.floor(covers.length / 2)] : 0,
  };
}

/* ==================== 相似岗位重排 ==================== */

/* 论文里右键一个岗位即按技能相似度重排整列，相似度取两条能力构成向量的
   欧氏距离（"calculated by the Euclidean distance between two skill vectors"），
   基准岗位本身置顶。这是论文里唯一的重排入口 —— 选中一项能力只做高亮连线，
   不动岗位列的次序。 */
export function sortBySimilarity(jobIds: string[], anchor: string): string[] {
  const base = exploreBase();
  const a = base.jobs.get(anchor);
  if (!a) return jobIds;
  const dist = (x: number[], y: number[]) => {
    let s = 0;
    for (let i = 0; i < x.length; i++) {
      const d = x[i] - y[i];
      s += d * d;
    }
    return Math.sqrt(s);
  };
  return [...jobIds].sort((p, q) => {
    if (p === anchor) return -1;
    if (q === anchor) return 1;
    const bp = base.jobs.get(p);
    const bq = base.jobs.get(q);
    if (!bp || !bq) return 0;
    return dist(a.vector, bp.vector) - dist(a.vector, bq.vector);
  });
}

/** 与目标岗位能力构成最接近的若干个岗位（用于详情栏的"能力构成相近"） */
export function nearestJobs(jobIds: string[], anchor: string, top = 5) {
  return sortBySimilarity(jobIds, anchor)
    .filter((id) => id !== anchor)
    .slice(0, top);
}
