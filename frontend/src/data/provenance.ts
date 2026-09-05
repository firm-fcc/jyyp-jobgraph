/* ============================================================
   字段级来源判定 —— 每张图开画前问的那一句"这个通道能不能画"

   实体级的 provenance（types/graph.ts）回答"这个节点/这条边该不该存在"，
   但它表达不了字段级的差异：一个岗位节点的 name 是真的，
   它的 marketShare 是补的，两者挂在同一个实体上。

   所以这里再加一层：按字段路径登记来源，视图读某个字段之前先问一句。

   ------------------------------------------------------------
   为什么要分通道级和图元级两档

   逐个图元标注（实心 / 斜纹 / 空心）只在"这个通道里真假混杂"时才有意义。
   真实数据下 16 个被读到的字段通道里，11 个是同质 synthetic（整列都是补的）、
   3 个是同质 measured、只有 3 个真正混杂。

   把 1540 条随机边全部画成斜纹，读者拿到的信息只有一个 bit
   （"这张关系图基本是编的"），却要用 1540 条曲线来传达，
   还顺带把仅有的 49 条真边淹掉。一行图注加通道停画传达同样的一个 bit，
   而且不污染画面。
   ============================================================ */

import type { Provenance } from '@/types/graph';
import { IS_REAL_GRAPH, IS_REAL_TAXONOMY } from './dataSource';

/** 同质阈值：一个通道里 synthetic 占比达到这一档，就整条通道停画而不是逐个标注 */
export const HOMOGENEOUS_THRESHOLD = 0.85;

type Field =
  | 'node.name'
  | 'node.aliases'
  | 'node.category'
  | 'node.topCategory'
  | 'node.realCount'
  | 'node.skillType'
  | 'node.funtypes'
  | 'node.marketShare'
  | 'node.frequency'
  | 'node.gap'
  | 'node.origin'
  | 'node.status'
  | 'node.firstSeen'
  | 'node.confidence'
  | 'node.attrs'
  | 'node.attrs.salary'
  | 'node.attrs.level'
  | 'node.attrs.posts'
  | 'node.attrs.city'
  | 'node.attrs.degree'
  | 'node.attrs.techStacks'
  | 'node.level'
  | 'node.proficiency'
  | 'edge.S-SP'
  | 'edge.J-T'
  | 'edge.T-S'
  | 'edge.J-S'
  | 'edge.weight'
  | 'edge.sourceMix'
  | 'edge.evidence'
  | 'signal.series'
  | 'signal.jd'
  | 'signal.paperNews'
  | 'signal.leadMonths';

/**
 * 只接三份分类文件、关系与时序由演示补齐层生成时的来源表。
 * 这一档留作对照（VITE_DATA=taxonomy），为图谱产物接入之前的形态。
 */
const TAXONOMY_FIELD_PROVENANCE: Record<Field, Provenance> = {
  'node.name': 'measured',
  'node.aliases': 'measured',
  'node.category': 'derived',
  'node.topCategory': 'measured',
  'node.realCount': 'measured',
  'node.skillType': 'measured',
  'node.funtypes': 'measured',
  'node.marketShare': 'synthetic',
  'node.frequency': 'synthetic',
  'node.gap': 'synthetic',
  'node.origin': 'synthetic',
  'node.status': 'synthetic',
  'node.firstSeen': 'synthetic',
  'node.confidence': 'synthetic',
  'node.attrs': 'synthetic',
  'node.attrs.salary': 'synthetic',
  'node.attrs.level': 'synthetic',
  'node.attrs.posts': 'measured',
  'node.attrs.city': 'synthetic',
  'node.attrs.degree': 'synthetic',
  'node.attrs.techStacks': 'synthetic',
  'node.level': 'synthetic',
  'node.proficiency': 'synthetic',
  'edge.S-SP': 'measured',
  'edge.J-T': 'synthetic',
  'edge.T-S': 'synthetic',
  'edge.J-S': 'synthetic',
  'edge.weight': 'synthetic',
  'edge.sourceMix': 'synthetic',
  'edge.evidence': 'synthetic',
  'signal.series': 'synthetic',
  'signal.jd': 'synthetic',
  'signal.paperNews': 'synthetic',
  'signal.leadMonths': 'synthetic',
};

/**
 * 算法侧图谱产物接入后的来源表。
 *
 * 与演示词表相比，改动集中在三处：四类边由一类实测变为四类全实测；
 * 各层的量与时序由补齐改为实测，取自各观测窗口的月度序列；
 * 前瞻信号及其证据由补齐改为实测，逐条可核到论文编号或新闻标题。
 *
 * 本批数据又补上三处：城市分布取自招聘原文的 place 列；按职级分档的能力要求
 * 由汇总表逐条的职级与能力要求算出；要求程度的档位分布产出到
 * “某岗位对某技能”这一粒度。
 *
 * 学历一项此前记为 synthetic —— 招聘原文表的 degree 一列整批为空；现改记
 * derived：本批该列开始有值（占连接条数的一成上下），其余仍由正文的门槛语抽出，
 * 可回到原文核对那一句，但整体不是直接观测。
 *
 * 本轮又结清三处：企业类别一维整条撤除（该维无实测来源，界面上不再出现），
 * 岗位定义的必备技能、加分技能与典型应用场景三项由招聘统计推出，
 * 前瞻信号的领先月数由信号与招聘两条曲线的互相关求出。三项均记 derived 或
 * measured，界面上不再挂演示数据标。
 */
const GRAPH_FIELD_PROVENANCE: Record<Field, Provenance> = {
  'node.name': 'measured',
  'node.aliases': 'measured',
  'node.category': 'measured',
  'node.topCategory': 'measured',
  'node.realCount': 'measured',
  'node.skillType': 'measured',
  'node.funtypes': 'measured',
  'node.marketShare': 'measured',
  'node.frequency': 'measured',
  'node.gap': 'measured',
  'node.origin': 'measured',
  /* 增强还是减弱由末两窗的份额之比判定，系由实测量算出的判断，非观测本身 */
  'node.status': 'derived',
  'node.firstSeen': 'measured',
  /* 置信度按基图权重换算，可核验但不是直接观测 */
  'node.confidence': 'derived',
  /* 属性整体为混杂通道，逐项见下面六条 */
  'node.attrs': 'derived',
  'node.attrs.salary': 'measured',
  'node.attrs.level': 'measured',
  'node.attrs.posts': 'measured',
  /* 城市取自招聘原文表的 place 列，取到市一级；轴外各城并入“其他”一档 */
  'node.attrs.city': 'measured',
  /* 学历：原文表的 degree 一列整批为空，值由正文的门槛语抽出。
     可核验（回到原文即能对上那一句），但不是直接观测，故记 derived */
  'node.attrs.degree': 'derived',
  /* 技术方向取汇总表逐条标注的技术栈一列，按方向拆开后归一 */
  'node.attrs.techStacks': 'measured',
  /* 职级分档的能力要求由汇总表逐条的职级与能力要求算出；职级列约五成半有值 */
  'node.level': 'measured',
  /* 档位分布本批已产出到“某岗位对某技能”这一粒度（realGraph.profShareOf）。
     图上要画的是“某岗位对某技能点要求到什么程度”，仍差一层：技能点一段
     沿用其父技能的构成；且图上一条关系只落一档，而实测给的是一个分布，
     由该分布按确定性哈希取一档，聚合后的四档比例即实测比例。
     两步都是分配而非观测，故按 derived 记。 */
  'node.proficiency': 'derived',
  'edge.S-SP': 'measured',
  'edge.J-T': 'measured',
  'edge.T-S': 'measured',
  'edge.J-S': 'measured',
  'edge.weight': 'measured',
  /* 来源构成由基图权重与叠加权重的比例算出 */
  'edge.sourceMix': 'derived',
  'edge.evidence': 'measured',
  'signal.series': 'measured',
  'signal.jd': 'measured',
  'signal.paperNews': 'measured',
  /* 领先月数由两条实测曲线的互相关求出，是导出量而非观测本身 */
  'signal.leadMonths': 'derived',
};

/** 演示词表下所有字段都是生成的，一处也不例外 */
const MOCK_FIELD_PROVENANCE = Object.fromEntries(
  Object.keys(TAXONOMY_FIELD_PROVENANCE).map((k) => [k, 'synthetic' as Provenance]),
) as Record<Field, Provenance>;

export const FIELD_PROVENANCE: Record<Field, Provenance> = IS_REAL_GRAPH
  ? GRAPH_FIELD_PROVENANCE
  : IS_REAL_TAXONOMY
    ? TAXONOMY_FIELD_PROVENANCE
    : MOCK_FIELD_PROVENANCE;

/** 这个通道现在是什么来源 */
export function channelOf(field: Field): Provenance {
  return FIELD_PROVENANCE[field];
}

/** 这个通道能不能当结论画出来 —— synthetic 一律不画 */
export function canPlot(field: Field): boolean {
  return FIELD_PROVENANCE[field] !== 'synthetic';
}

/** 图元级三态描边预设，供混杂通道逐个标注时取用 */
export const MARK_STYLE: Record<Provenance, { fill: string; dash?: string; hollow: boolean }> = {
  measured: { fill: 'currentColor', hollow: false },
  derived: { fill: 'currentColor', dash: '4 2.5', hollow: false },
  synthetic: { fill: 'none', dash: '2 3', hollow: true },
};

/** 口径短语，界面上凡是要写"这一栏为什么是空的"都从这里取，避免各页说法不一 */
export const PROVENANCE_NOTE: Record<Provenance, string> = {
  measured: '来自原始分类数据，可逐条核验',
  derived: '由实测字段推导，可核验但非直接观测',
  synthetic: '本批数据未包含，图中不以推测值代替',
};

/* ============================================================
   仍由前端补齐的维度 —— 全站演示数据标的唯一出处

   各页的演示数据标说的是"本图这一栏为什么是补的"，措辞随图而异；
   本清单说的是"整套系统还有哪几栏是补的"，供顶栏的总标与封面引用。
   两处的口径须同源，故列在此处，而不是各页各写一遍。

   前两项即 manifest.absent，来自招聘数据未含的字段；
   其余为算法侧产出与界面所需口径之间的差，逐条注明差在何处。

   本批数据结清了三项：城市分布取自招聘原文的 place 列；按职级分档的能力
   要求由汇总表逐条的职级与能力要求算出；熟练度档位已落到“某岗位对某技能”
   这一粒度。三项的余下限制不再是“没有数据”，而是覆盖率与推导层次，
   写在各图自己的口径标里，不再占本清单的位置。

   本轮又结清两项，两项都不是算法侧补了新字段，而是由已有字段推出：

   · 学历分布。原文表的 degree 一列整批为空，但正文多数写有“本科及以上学历”
     一类门槛语，算法侧的职级本就是这么抽的（level_source 记作 text），
     学历沿同一路径由构建阶段抽出，覆盖招聘原文条数的七成。

   · 招聘原文的句级归因。汇总表逐条给出该条要求的技能与各技能下命中的技能点，
     技能点是具体的技术名与工具名，在正文里以原字面出现，据此反查落点即得
     所在句。全批 (条, 能力项) 对中定位到八成九，招聘侧因而进入了证据链的
     锚点表，岗位洞察页的企业表述侧重与跨条件复现两块随之有内容可列。

   两项都是推导而非观测，各图的口径标里逐处写明是怎么推的、覆盖多少。
   ============================================================ */

export interface AbsentDimension {
  /** 维度名，与界面上那一栏的标题一致 */
  name: string;
  /** 这一栏为什么还是补的 */
  why: string;
}

export const ABSENT_DIMENSIONS: AbsentDimension[] = [
  { name: '证据原文全文', why: '证据链只保留可核对的锚点句，完整原文在算法仓库' },
  { name: '简历与技术名对照词表', why: '人岗匹配页的示例简历与技术名归并词表为演示数据' },
];

/* 本轮结清的三项，记在此处备查：

   · 企业类别分布。迁移包未含原始招聘数据集，派生出的原文表只给企业名，
     仅凭企业名判不出类别（人力资源服务企业多为代招方，不是用人单位）。
     该维已整条撤除 —— 职业探索页的属性栏改列省份、学历、经验三组，
     簇内分布图的横轴改用自招聘正文抽出的学历门槛（realGraph.degreeShare），
     两处均为实测。横轴一度取技术方向，与聚类本身同以能力构成为据，
     同一簇内的岗位因而挤在一两列里，整张图摊不开。

   · 岗位定义的三项要素。必备技能与加分技能由汇总表的覆盖率与熟练度两列推出，
     典型应用场景取技术栈一列；叠层新岗位无招聘投放，三项改由推导的能力构成
     与最相近的既有岗位给出，逐条在卡片的口径里写明。见 data-pipeline 6.5 节。

   · 前瞻信号的领先月数。由信号与招聘两条曲线在自然月轴上的互相关求最优滞后
     （realGraph.bestLag）：两条序列先取相邻观测之差再求相关，相关系数低于下限
     时不给值。本批 121 条已被招聘市场确认的信号中判出 45 条，中位十个月。 */
