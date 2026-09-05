/* ============================================================
   确定性数据生成器
   —— 完全按 algorithm-design v2 的机制生成，而非随手造数：
      · 三源强度 E_X(t) 带真实的相位滞后（论文 → 新闻 → JD）
      · gap(E) = max(0, 0.7·paper + 0.3·news − jd)
      · Δw = λ·gap·I(...)，并施加 e^(−γ·Δt_unconfirmed) 衰减
      · 年轮各环由同一套信号驱动 → 变更清单与年轮天然一致
   ============================================================ */

import type {
  AnnulusRing,
  ChangeEvent,
  Dataset,
  Distribution,
  EdgeKind,
  EdgeStatus,
  EntitySignal,
  EvidenceRef,
  GraphEdge,
  GraphNode,
  GraphVersion,
  HallucinationBlock,
  JobAnnuli,
  LoopRun,
  MergeCandidate,
  NodeKind,
  NoisePhrase,
  PlagiarismCluster,
  PrismTimeline,
  ResumeExperience,
  ResumeProfile,
  ResumeSection,
  TechStack,
} from '@/types/graph';
import {
  CITIES,
  COMPANIES,
  DEGREES,
  EXPERIENCE,
  NEWS_OUTLETS,
  NOISE_PHRASES,
  PAPER_VENUES,
  SALARY_BANDS,
} from './taxonomy';
import {
  IS_REAL_GRAPH,
  IS_REAL_TAXONOMY,
  SEED_JOBS as JOBS,
  SEED_SKILLS as SKILLS,
  SEED_SKILL_POINTS as SKILL_POINTS,
  SEED_TASKS as TASKS,
} from './seeds';
import { REAL_MERGES } from './realTaxonomy';
import {
  buildRealAnnuli,
  buildRealChanges,
  buildRealEdges,
  buildRealLoops,
  buildRealNodes,
  buildRealPrism,
  buildRealSignals,
  buildRealVersions,
  INFERRED_EDGES,
  REAL_GRAPH_STATS,
  REAL_MONTHS,
  REAL_VERSION_DEFS,
  SKILLPOINT_NAMES,
  SKILL_CODE_BY_NAME,
} from './realGraph';
import { demoFirstSeen, demoLevel, DEMO_SKILL_EXTRACTION } from './demoFill';
import { provinceOf } from './provinces';
import { clamp, hashStr, logistic, pick, rand01, randInt, randRange } from '@/utils/rng';
import { addMonths, monthDiff, monthIndex, monthRange } from '@/utils/format';

/* ==================== 时间轴 ==================== */

/* 时间轴随取数开关切换。
   接入算法侧图谱产物后，观测窗口即该批数据实际覆盖的那几个窗口；
   演示词表与仅体系两档下沿用原先的四年半区间。
   下游凡按月份下标取数的代码一律读本段，两套数据因而不会错位。 */
export const START_MONTH = IS_REAL_GRAPH ? REAL_MONTHS[0] : '2022-01';
export const END_MONTH = IS_REAL_GRAPH ? REAL_MONTHS[REAL_MONTHS.length - 1] : '2026-07';
/* 真实数据下取观测窗口本身，不取首末窗之间的自然月区间：观测窗口未必连续
   —— 本批的 2022-12 与 2023-01 两个月算法侧只给了结转基图，不是独立观测，
   不接入（见 manifest.windowsExcluded）。按自然月铺开会多出两格空档，
   而各条序列的长度等于窗口数，凡按下标取数的地方都会错位一格以上，
   末窗更会读到 undefined。断档本身记在 manifest.windowGaps，由界面另行交代。 */
export const MONTHS = IS_REAL_GRAPH ? REAL_MONTHS : monthRange(START_MONTH, END_MONTH);
export const NOW = END_MONTH;

/* 图谱版本。接入算法侧图谱产物后，一个版本即一个观测窗口（见 realGraph）；
   演示词表与仅体系两档下沿用原先按季度铺开的版本序列。 */
const DEMO_VERSION_DEFS: { version: string; date: string; label: string }[] = [
  { version: 'v1.0', date: '2024-09', label: '冷启动基线' },
  { version: 'v1.1', date: '2024-12', label: 'Agent 任务族入图' },
  { version: 'v1.2', date: '2025-03', label: '推理优化能力显性化' },
  { version: 'v1.3', date: '2025-06', label: '安全对齐要求上升' },
  { version: 'v1.4', date: '2025-09', label: '上下文工程首次确认' },
  { version: 'v1.5', date: '2025-12', label: '具身方向分化' },
  { version: 'v2.0', date: '2026-03', label: '协议层能力成型' },
  { version: 'v2.1', date: '2026-06', label: '当前基线' },
];
export const VERSION_DEFS = IS_REAL_GRAPH ? REAL_VERSION_DEFS : DEMO_VERSION_DEFS;

/* 越过"今日线"的预测环。图谱产物只给实测窗口，真实数据下不画预测环：
   外推段没有观测可核，与实测环并排会被读成同一性质的一环。 */
export const PREDICTED_VERSION: { version: string; date: string; label: string } | null =
  IS_REAL_GRAPH ? null : { version: 'v2.2⁺', date: '2026-09', label: '叠层预测' };

/** 版本序列，末位为预测环（若有）。凡需把预测环并入版本列表的一律读它 */
export const VERSION_DEFS_ALL: { version: string; date: string; label: string }[] =
  PREDICTED_VERSION ? [...VERSION_DEFS, PREDICTED_VERSION] : VERSION_DEFS;

/* 棱镜时间轴越过今日线之后再画几个月。这一段是外推，不是观测，轴上画成斜纹区。
   算法侧图谱产物只给实测窗口，外推段因而为零：外推出来的趋势没有观测可核。 */
export const PRISM_FORECAST_MONTHS = IS_REAL_GRAPH ? 0 : 6;
/** 棱镜时间轴的完整月份序列：实测窗口 + 外推段 */
export const PRISM_MONTHS = IS_REAL_GRAPH
  ? MONTHS
  : monthRange(START_MONTH, addMonths(END_MONTH, PRISM_FORECAST_MONTHS));

/* ==================== 工具 ==================== */

const λ1 = 0.34; // J-T / J-S 叠层系数
const λ2 = 0.62; // T-S
const λ3 = 0.55; // S-SP
const γ = 0.075; // 叠层衰减常数

const id = (kind: string, name: string) => `${kind}:${name}`;

function seriesFirstMonth(arr: number[], thr: number): string | undefined {
  const i = arr.findIndex((v) => v >= thr);
  return i >= 0 ? MONTHS[i] : undefined;
}

/** 互相关最优滞后（月），限定在 0..36 */
function bestLag(lead: number[], base: number[]): number {
  let best = 0;
  let bestScore = -Infinity;
  for (let lag = 0; lag <= 36; lag++) {
    let s = 0;
    let n = 0;
    for (let t = lag; t < base.length; t++) {
      s += lead[t - lag] * base[t];
      n++;
    }
    if (n < 8) break;
    const score = s / n;
    if (score > bestScore) {
      bestScore = score;
      best = lag;
    }
  }
  return best;
}

/* ==================== 1. 节点 ==================== */

/* 学历与经验不能纯随机 ——
   随机分布会生出“数据治理工程师最常见学历是大专”“NLP 算法工程师大专”
   这种一眼假的展示。两者都按岗位属性定一个期望档位，再围绕它铺高斯，
   最后叠一点确定性扰动保持自然：同一个岗位每次生成的结果仍然完全一致。 */

/** 各职位族的期望学历档位，落在 DEGREES 的下标空间（0 大专 · 1 本科 · 2 硕士 · 3 博士） */
const DEGREE_BASE: Record<string, number> = {
  算法研发: 1.9,
  智能系统: 1.6,
  安全: 1.45,
  质量与评测: 1.3,
  数据分析: 1.25,
  基础设施: 1.15,
  数据工程: 1.1,
  应用工程: 1.05,
  嵌入式: 1.0,
};

/** 城市分布折到省份。补齐层的城市名与实测一侧同走一张行政区划对照表 */
function toProvinces(byCity: Distribution): Distribution {
  const out: Distribution = {};
  for (const [c, v] of Object.entries(byCity)) {
    const p = provinceOf(c);
    out[p] = (out[p] ?? 0) + v;
  }
  return out;
}

/** 期望经验档位，落在 EXPERIENCE 的下标空间（0 应届 · 1 1-3年 · 2 3-5年 · 3 5-10年 · 4 10年以上） */
const EXP_BASE = 1.65;

function jobAttributes(name: string, salary: [number, number], cluster: string, emerging?: boolean,
  realPosts?: number,
) {
  const dist = (buckets: readonly string[], key: string, skew = 1) => {
    const out: Record<string, number> = {};
    let sum = 0;
    buckets.forEach((b, i) => {
      const v = Math.pow(rand01(`${name}|${key}|${b}|${i}`), skew) + 0.05;
      out[b] = v;
      sum += v;
    });
    for (const k of Object.keys(out)) out[k] = out[k] / sum;
    return out;
  };

  /** 围绕 center 档位的高斯分布（下标空间），扰动幅度小到不会翻转众数 */
  const around = (buckets: readonly string[], key: string, center: number, sigma: number) => {
    const out: Record<string, number> = {};
    let sum = 0;
    buckets.forEach((b, i) => {
      const d = i - center;
      const v = Math.exp(-(d * d) / (2 * sigma * sigma)) * (0.86 + 0.28 * rand01(`${name}|${key}|${b}|${i}`)) + 0.02;
      out[b] = v;
      sum += v;
    });
    for (const k of Object.keys(out)) out[k] = out[k] / sum;
    return out;
  };

  const mid = (salary[0] + salary[1]) / 2;
  const salaryBands: Record<string, number> = {};
  let ssum = 0;
  SALARY_BANDS.forEach((b, i) => {
    const centers = [8, 15, 25, 40, 60, 85];
    const d = Math.abs(centers[i] - mid);
    const v = Math.exp(-(d * d) / 700) + 0.02;
    salaryBands[b] = v;
    ssum += v;
  });
  for (const k of Object.keys(salaryBands)) salaryBands[k] /= ssum;

  // 给得越高，门槛越高；萌芽方向多从研究侧长出来，学历再抬一点、但要不到长年限经验
  const payLift = (mid - 40) / 45;
  const degCenter = clamp((DEGREE_BASE[cluster] ?? 1.15) + payLift + (emerging ? 0.18 : 0), 0.55, 3);
  const expCenter = clamp(EXP_BASE + payLift * 0.9 - (emerging ? 0.4 : 0), 0.65, 3.2);

  return {
    /* 地域一维在真实数据下取到省级（招聘原文的 place 列按行政区划汇总），
       补齐层若照城市名铺，属性栏上"主要省份"一格会显示"西安"——
       口径与栏名对不上。故这里先按城市铺，再折到所属省份：
       演示分布的形状不变，读数落在与实测同一级行政区划上。 */
    cities: toProvinces(dist(CITIES, 'city', 1.6)),
    degrees: around(DEGREES, 'degree', degCenter, 0.72),
    experience: around(EXPERIENCE, 'exp', expCenter, 0.82),
    salaryBands,
    techStacks: {},
    medianSalary: Math.round(mid),
    /* 招聘信息条数：真实体系下取岗位自己的 hits，这一维不再是补的。
       萌芽岗位没有 hits（它们还没进体系），仍按哈希铺一个演示值。 */
    postCount: realPosts && realPosts > 0 ? realPosts : randInt(`${name}|posts`, 180, 2400),
  };
}

/* 真实分类文件是一次快照，没有时间维。种子里 firstSeen 留空的，
   由演示补齐层按名称哈希铺开 —— 凡是读到这一维的图表都挂「演示数据」标。 */
const firstSeenOf = (v: string | undefined, key: string) => (v && v.length ? v : demoFirstSeen(key));
/** 种子来自真实分类文件时，节点存在与名称是 measured；演示词表下整份都是 synthetic */
const SEED_PROV: GraphNode['provenance'] = IS_REAL_TAXONOMY ? 'measured' : 'synthetic';

function buildNodes(): GraphNode[] {
  const nodes: GraphNode[] = [];

  for (const j of JOBS) {
    nodes.push({
      id: id('J', j.name),
      kind: 'job',
      name: j.name,
      aliases: j.aliases ?? [],
      category: j.category,
      definition: j.definition,
      coreDuties: j.coreDuties,
      mustSkills: j.mustSkills,
      plusSkills: j.plusSkills,
      scenarios: j.scenarios,
      firstSeen: firstSeenOf(j.firstSeen, j.name),
      provenance: j.emerging && IS_REAL_TAXONOMY ? 'synthetic' : SEED_PROV,
      realCount: j.realCount,
      posts: j.posts,
      funtypes: j.funtypes,
      keywords: j.keywords,
      boundary: j.boundary,
      topCategory: j.topCategory,
      lastConfirmed: NOW,
      marketShare: 0,
      frequency: 0,
      confidence: j.emerging ? randRange(`${j.name}|conf`, 0.52, 0.78) : randRange(`${j.name}|conf`, 0.82, 0.97),
      status: j.emerging ? 'candidate' : 'active',
      origin: 'base',
      gap: 0,
      cluster: j.cluster,
      emerging: j.emerging,
      attrs: jobAttributes(j.name, j.salary, j.cluster, j.emerging, j.posts),
    });
  }

  for (const t of TASKS) {
    nodes.push({
      id: id('T', t.name),
      kind: 'task',
      name: t.name,
      aliases: [],
      category: t.category,
      definition: t.definition,
      provenance: SEED_PROV,
      firstSeen: firstSeenOf(t.firstSeen, t.name),
      lastConfirmed: NOW,
      marketShare: 0,
      frequency: 0,
      confidence: t.emerging ? randRange(`${t.name}|conf`, 0.55, 0.8) : randRange(`${t.name}|conf`, 0.8, 0.96),
      status: 'active',
      origin: 'base',
      gap: 0,
    });
  }

  for (const s of SKILLS) {
    nodes.push({
      id: id('S', s.name),
      kind: 'skill',
      name: s.name,
      aliases: [],
      category: s.category,
      provenance: SEED_PROV,
      realCount: s.realCount,
      firstSeen: '2019-01',
      lastConfirmed: NOW,
      marketShare: 0,
      frequency: 0,
      confidence: randRange(`${s.name}|conf`, 0.88, 0.99),
      status: 'active',
      origin: 'base',
      gap: 0,
    });
  }

  for (const sp of SKILL_POINTS) {
    nodes.push({
      id: id('SP', sp.name),
      kind: 'skillpoint',
      name: sp.name,
      aliases: [],
      category: sp.category,
      definition: sp.definition,
      provenance: SEED_PROV,
      skillType: sp.skillType,
      firstSeen: firstSeenOf(sp.firstSeen, sp.name),
      lastConfirmed: NOW,
      marketShare: 0,
      frequency: 0,
      confidence: sp.emerging ? randRange(`${sp.name}|conf`, 0.48, 0.75) : randRange(`${sp.name}|conf`, 0.78, 0.97),
      status: 'active',
      origin: 'base',
      gap: 0,
      /* 真实体系没有成熟度分档，演示补齐层按首现年份给一档，
         界面上凡是显示"基础 / 进阶 / 前沿"的地方都挂演示数据标 */
      level: IS_REAL_TAXONOMY ? demoLevel(firstSeenOf(sp.firstSeen, sp.name)) : sp.level,
    });
  }

  return nodes;
}

/* ==================== 2. 三源信号 ==================== */

function buildSignals(nodes: GraphNode[]): EntitySignal[] {
  const T0 = monthIndex(START_MONTH);
  const out: EntitySignal[] = [];

  for (const n of nodes) {
    const key = n.id;
    /* 成熟度决定三条曲线的相对高度：前沿项论文高、招聘低，前瞻热度才有正值。
       真实体系没有这一维，一律按首现年份分档（见 demoFill.demoLevel）。 */
    const level = IS_REAL_TAXONOMY
      ? demoLevel(n.firstSeen)
      : (n.level ?? (n.kind === 'skill' ? 1 : 2));
    const emerging = IS_REAL_TAXONOMY
      ? level === 3
      : (SKILL_POINTS.find((s) => s.name === n.name)?.emerging ??
        TASKS.find((t) => t.name === n.name)?.emerging ??
        JOBS.find((j) => j.name === n.name)?.emerging ??
        false);

    const paperOnset = monthIndex(n.firstSeen) - T0;
    const newsLag = Math.round(randRange(`${key}|nl`, 5, 15));
    const jdLag = Math.round(
      emerging ? randRange(`${key}|jl`, 18, 40) : randRange(`${key}|jl`, 8, 22),
    );

    const pop = randRange(`${key}|pop`, 0.25, 1);
    const peakPaper = 0.05 + pop * (level === 3 ? 0.46 : level === 2 ? 0.24 : 0.11);
    const peakNews = peakPaper * randRange(`${key}|pn`, 0.4, 0.85);
    const peakJd = 0.03 + pop * (level === 1 ? 0.52 : level === 2 ? 0.32 : 0.19);

    const paper: number[] = [];
    const news: number[] = [];
    const jd: number[] = [];
    const gap: number[] = [];

    for (let t = 0; t < MONTHS.length; t++) {
      const noise = (k: string) => 1 + (rand01(`${key}|${k}|${t}`) - 0.5) * 0.16;
      const p = peakPaper * logistic(t, paperOnset + 4, 3.2) * noise('p');
      const nw = peakNews * logistic(t, paperOnset + newsLag, 3.0) * noise('n');
      const j = peakJd * logistic(t, paperOnset + jdLag, 4.2) * noise('j');
      paper.push(clamp(p, 0, 1));
      news.push(clamp(nw, 0, 1));
      jd.push(clamp(j, 0, 1));
      gap.push(clamp(Math.max(0, 0.7 * p + 0.3 * nw - j), 0, 1));
    }

    const THR = 0.022;
    const firstPaperAt = seriesFirstMonth(paper, THR);
    const firstNewsAt = seriesFirstMonth(news, THR);
    const firstJdAt = seriesFirstMonth(jd, THR);

    let predictedJdAt: string | undefined;
    let predictedJdRange: [string, string] | undefined;
    if (!firstJdAt && firstPaperAt) {
      const est = Math.round(jdLag);
      predictedJdAt = addMonths(firstPaperAt, est);
      predictedJdRange = [addMonths(predictedJdAt, -4), addMonths(predictedJdAt, 5)];
    }

    // Δt_unconfirmed：gap 首次显著至今仍未被 JD 确认的月数
    const gapOnsetIdx = gap.findIndex((v) => v > 0.03);
    const unconfirmed = firstJdAt || gapOnsetIdx < 0 ? 0 : MONTHS.length - 1 - gapOnsetIdx;
    const decayFactor = Math.exp(-γ * unconfirmed);

    out.push({
      entityId: n.id,
      entityName: n.name,
      kind: n.kind,
      category: n.category,
      months: MONTHS,
      jd,
      paper,
      news,
      gap,
      firstPaperAt,
      firstNewsAt,
      firstJdAt,
      leadMonths: {
        paper: firstPaperAt && firstJdAt ? monthDiff(firstPaperAt, firstJdAt) : bestLag(paper, jd) || undefined,
        news: firstNewsAt && firstJdAt ? monthDiff(firstNewsAt, firstJdAt) : undefined,
      },
      predictedJdAt,
      predictedJdRange,
      decayFactor,
    });
  }

  return out;
}

/** 把信号回写到节点：market share / frequency / gap / origin / status */
function applySignalsToNodes(nodes: GraphNode[], signals: Map<string, EntitySignal>) {
  const last = MONTHS.length - 1;
  const byKind: Record<string, number> = {};

  for (const n of nodes) {
    const s = signals.get(n.id)!;
    const jdNow = s.jd[last];
    const gapNow = s.gap[last];
    n.gap = gapNow;
    n.frequency = Math.round(jdNow * 12000 + gapNow * 800);
    n.origin = jdNow < 0.022 && gapNow > 0.02 ? 'overlay' : 'base';
    if (n.origin === 'overlay') {
      n.status = 'candidate';
      n.confidence = Math.min(n.confidence, 0.68);
    } else {
      const prev = s.jd[Math.max(0, last - 12)];
      const d = jdNow - prev;
      n.status = d > 0.02 ? 'strengthening' : d < -0.006 ? 'weakening' : 'active';
    }
    n.lastConfirmed = s.firstJdAt ? NOW : (s.firstNewsAt ?? s.firstPaperAt ?? n.firstSeen);
    /* 招聘样本量跟着 JD 信号走：招聘侧还没起量的新岗位，样本量必须同样是小数，
       否则“尚未起量”和“上千条招聘样本”会在同一屏里自相矛盾。

       但这一步只对补出来的样本量成立。真实体系下已进体系的岗位带着实测的
       招聘信息条数（岗位体系 v2.0 的 hits），拿信号曲线去缩放它，界面上写的
       “在招 N 条”就不再是任何地方能核到的数 —— 实测软件工程师 122 万条会被
       改写成 50 万条。所以有实测值的一概跳过，只有萌芽岗位继续跟着信号走。 */
    if (n.kind === 'job' && n.attrs && !(n.posts && n.posts > 0)) {
      n.attrs.postCount = Math.round(40 + jdNow * (4200 + n.attrs.postCount));
    }
    byKind[n.kind] = (byKind[n.kind] ?? 0) + jdNow;
  }
  for (const n of nodes) {
    const s = signals.get(n.id)!;
    n.marketShare = byKind[n.kind] > 0 ? s.jd[last] / byKind[n.kind] : 0;
  }
}

/* ==================== 2.5 棱镜月度切片 ====================

   能力棱镜的时间维。算法侧按月产出后，这一段整体删掉换成一次取数即可 ——
   下游组件只认 PrismTimeline 这个形状，不认它是从哪来的。

   三条约束决定了这里怎么补：

   ① 末月必须等于现在图上的那个数。棱镜今天画的是 realCount（岗位的子树叶子数、
      能力组的组内技能点数），时间游标停在"最新"时整张图必须与加时间维之前
      逐像素一致 —— 否则等于借着加功能顺手改了一张已经在用的图。
   ② 历史形状跟着三源信号走，不另撒一套随机数。E_jd(t) 是本页其它几张图共用的
      那条曲线，棱镜的"当月规模"取它的相对形状，于是"棱镜上这一类 2024 年长起来"
      与"信号传导时间线上它 2024 年进入招聘要求"讲的是同一件事。
   ③ 起量之前返回 null 而不是 0：那是"当月还不在图谱里"，不是"当月测得为零"。 */

/**
 * 历史份额相对末月的上界。
 *
 * 这个数不是随便定的，它同时是全景图谱主图的标度余量：主图那三段的条长按
 * 「全时段峰值」定标尺，峰值就是这里的上界，于是默认停在末月时条长只占列宽的
 * 1/DEMAND_PEAK。定得越高，历史起伏越大，默认那一屏的条越短 —— 1.35 是这两头
 * 折中之后的取值，默认屏仍占七成半列宽，往回拖也还看得出"当年更重"。
 */
const DEMAND_PEAK = 1.35;

/**
 * 把大于 1 的份额比值压进 [1, DEMAND_PEAK)。
 *
 * 用软压而不是 Math.min 硬截：早期月份里同层条目还没起来，分母偏小，一批老条目的
 * 份额比值会一起顶到上界；硬截之后它们在图上长得一样长，读出来是"这十几项当年
 * 一样重"，而它们其实各不相同。软压保序、在 r=1 处一阶连续，且永远到不了上界。
 */
const softCap = (r: number) =>
  r <= 1 ? r : 1 + (DEMAND_PEAK - 1) * (1 - Math.exp((1 - r) / (DEMAND_PEAK - 1)));

function buildPrismTimeline(nodes: GraphNode[], signals: Map<string, EntitySignal>): PrismTimeline {
  const obs = MONTHS.length - 1; // 实测窗口的末月下标
  const series: Record<string, (number | null)[]> = {};
  const demand: Record<string, (number | null)[]> = {};
  const confirmedAt: Record<string, string> = {};

  /** 能力组 → 组内技能点。能力组的规模是一个计数，它的逐月值另有算法，见下 */
  const kidsOf = new Map<string, GraphNode[]>();
  for (const n of nodes) {
    if (n.kind !== 'skillpoint') continue;
    const l = kidsOf.get(n.category) ?? [];
    l.push(n);
    kidsOf.set(n.category, l);
  }

  /* 相对形状 = 招聘强度 + 前瞻信号的折价。
     只用 E_jd 的话，招聘市场尚未跟进的条目整条历史全是 null，
     时间轴一拖它就凭空跳出来，看不到"论文已经在讲、招聘还没写"的那一段。 */
  const shapeOf = (s: EntitySignal) => (t: number) =>
    s.jd[t] + 0.35 * Math.max(0, 0.7 * s.paper[t] + 0.3 * s.news[t] - s.jd[t]);

  /* ---- 逐层逐月的形状合计 ----
     要求强度走的是"份额"而不是绝对强度，所以要先知道同一层当月一共有多少。

     为什么是份额：一个岗位的能力要求是有限的一份注意力，写进 JD 的条目此消彼长。
     若按绝对强度算，三源信号都是单调上行的 logistic，得到的每一项都只涨不跌 ——
     图上"要求下降"与"技能点被移除"这两种状态永远不会出现，而它们正是赛题
     ②"明确标注岗位新增、删除、修改的技能点"要看的东西。按份额算之后，
     新兴项份额上行、老项被挤占而下行，两个方向都从同一条数据里自然长出来。 */
  const layerSum = new Map<NodeKind, number[]>();
  for (const n of nodes) {
    const s = signals.get(n.id);
    if (!s) continue;
    const shape = shapeOf(s);
    let arr = layerSum.get(n.kind);
    if (!arr) layerSum.set(n.kind, (arr = new Array(obs + 1).fill(0)));
    for (let t = 0; t <= obs; t++) arr[t] += shape(t);
  }

  for (const n of nodes) {
    const s = signals.get(n.id);
    if (!s) continue;
    if (s.firstJdAt) confirmedAt[n.id] = s.firstJdAt;

    /* ---- 要求强度的逐月因子：四层都有 ----
       这一条与下面的 series 是两件事。series 是计数（只有岗位与能力组数得出来），
       demand 是"岗位对它的要求相对末月是多少"—— 一个比值，任何一层都定义得出，
       量纲由前端按当前口径现算。全景图谱的条长走这一条。 */
    {
      const shape = shapeOf(s);
      const sum = layerSum.get(n.kind)!;
      const denom = shape(obs);
      /** 该项在同层里当月占的份额 */
      const share = (t: number) => (sum[t] > 1e-9 ? shape(t) / sum[t] : 0);
      const shareEnd = share(obs);
      const f: (number | null)[] = [];
      for (let t = 0; t <= obs; t++) {
        // null 判的是"这一项当月在不在图谱里"，看的是它自己的绝对强度，与份额无关
        const own = denom > 1e-6 ? shape(t) / denom : t === obs ? 1 : 0;
        if (own < 0.02) {
          f.push(null);
          continue;
        }
        const r = shareEnd > 1e-9 ? share(t) / shareEnd : 1;
        /* 早期同层条目太少造成的分母效应要压掉：那是分母的事，不是这一项当年
           真有那么重。压到 DEMAND_PEAK 以内，主图的条就不会越出自己那一列。 */
        f.push(Number(softCap(r).toFixed(4)));
      }
      f[obs] = 1;
      /* 外推段按最近 12 个月的份额变动往前推，增量每月折价 12%。
         这是外推不是预测模型，所以只推 6 个月，轴上单独画成斜纹区。
         上界与实测段同一条：外推不该比历史任何一个月更能撑破列宽。 */
      const back = f[Math.max(0, obs - 12)] ?? 1;
      let step = (1 - back) / 12;
      let cur = 1;
      for (let k = 0; k < PRISM_FORECAST_MONTHS; k++) {
        cur += step;
        step *= 0.88;
        f.push(Math.max(0.02, Number(softCap(cur).toFixed(4))));
      }
      demand[n.id] = f;
    }

    /* 结构规模只有拿得出真实计数的两层有。任务与技能点在源文件里只有名称
       与定义，给它们编一条"当月规模"就是把没有的东西画成有；
       这两层的时间通道走上面的 demand 与 confirmedAt。 */
    const end = n.realCount;
    if (end === undefined || (n.kind !== 'job' && n.kind !== 'skill')) continue;

    /* 能力组走计数，不走强度缩放。
       "组内技能点数"本来就是一个计数，把它乘上一条招聘强度曲线，
       得到的是"12.7 项能力"这种读不出意思的数；而组里的技能点是逐个
       被发现的，当月已进图谱的成员数才是这一格真正的值。
       末月自然等于成员总数，与 realCount 对得上，不用另外钉。 */
    if (n.kind === 'skill') {
      const kids = kidsOf.get(n.name) ?? [];
      const arr: (number | null)[] = [];
      for (let t = 0; t <= obs; t++) {
        const c = kids.filter((k) => monthDiff(k.firstSeen, MONTHS[t]) >= 0).length;
        arr.push(c > 0 ? c : null);
      }
      arr[obs] = end;
      // 外推段保持末值：说"未来半年还会新增几项能力"没有依据，不如不说
      for (let k = 0; k < PRISM_FORECAST_MONTHS; k++) arr.push(end);
      series[n.id] = arr;
      continue;
    }

    const shape = shapeOf(s);
    const denom = shape(obs);

    /* 比值钳在 1 以内 —— 历史值一律不超过现值。
       gap 那一项是个驼峰（论文先起、招聘后跟），不钳的话某些大类会在
       2024 年鼓出一个高点，读出来就是"这一类当年有 35 个岗位、现在只剩 30"。
       源数据是一次快照，一个字都没说它缩过；图谱本身的机制也是逐批纳入、
       只长不缩。钳掉之后还有一个附带好处：径向标度的最大值仍是现值，
       游标停在实测末月时，整张图与没有时间维的那一版逐像素一致。 */
    const arr: (number | null)[] = [];
    for (let t = 0; t <= obs; t++) {
      const r = denom > 1e-6 ? shape(t) / denom : t === obs ? 1 : 0;
      arr.push(r < 0.02 ? null : Math.max(1, Math.round(end * Math.min(r, 1))));
    }
    arr[obs] = end; // 末月钉死在现值上，不受上面取整的影响

    /* 外推段：按最近 12 个月的月均增量往前推，增量每月折价 12%。
       这是外推不是预测模型，所以只推 6 个月，轴上单独画成斜纹区。 */
    const back = arr[Math.max(0, obs - 12)] ?? arr[obs]!;
    let step = (arr[obs]! - back) / 12;
    let cur = arr[obs]!;
    for (let k = 0; k < PRISM_FORECAST_MONTHS; k++) {
      cur += step;
      step *= 0.88;
      arr.push(Math.max(1, Math.round(cur)));
    }

    series[n.id] = arr;
  }

  return {
    months: PRISM_MONTHS,
    series,
    demand,
    confirmedAt,
    forecastFrom: PRISM_MONTHS[obs + 1],
    provenance: 'synthetic',
  };
}

/* ==================== 3. 证据链 ==================== */

const JD_TEMPLATES = [
  (a: string, b: string) => `岗位职责：负责${a}相关工作，要求熟练掌握${b}，具备完整项目落地经验。`,
  (a: string, b: string) => `任职要求：精通${b}，能独立完成${a}的方案设计与实现。`,
  (a: string, b: string) => `我们期望你在${a}方向有深入积累，并对${b}有扎实理解与实践。`,
  (a: string, b: string) => `工作内容包含${a}；要求候选人具备${b}相关的工程能力，有大规模生产环境经验优先。`,
];
const PAPER_TEMPLATES = [
  (a: string, b: string) => `We present an approach to ${a} built upon ${b}, achieving consistent gains across benchmarks.`,
  (a: string, b: string) => `This work studies how ${b} affects downstream ${a}, and proposes a scalable training recipe.`,
  (a: string, b: string) => `A systematic study of ${b} for ${a}: design space, trade-offs, and open problems.`,
];
/* 论文标题。上一版拼的是 `${venue} · ${b} for ${a}`，中文实体名中间夹一个英文
   介词，既不是中文标题也不是英文标题，而首页“高关注度论文”榜直接展示它。
   改成与下面新闻标题同一形态的中文标题，三选一以免整张榜看起来像同一句话。 */
const PAPER_TITLES = [
  (a: string, b: string) => `${b}在${a}中的应用研究`,
  (a: string, b: string) => `面向${a}的${b}方法综述`,
  (a: string, b: string) => `${b}对${a}能力要求的影响分析`,
];
const NEWS_TEMPLATES = [
  (a: string, b: string) => `多家头部厂商已在${a}场景规模化引入${b}，相关团队规模在过去一个季度显著扩张。`,
  (a: string, b: string) => `业内人士指出，${b}正成为${a}的标准配置，企业侧的人才储备明显滞后于技术演进。`,
  (a: string, b: string) => `随着${a}需求爆发，掌握${b}的工程师在市场上供不应求。`,
];

/** 一条证据在原文里点名的实体：`id` 是图谱节点 id，`w` 是被点名的相对概率 */
interface EvidenceAnchor {
  id: string;
  name: string;
  w: number;
}

/** 由边键派生一个稳定短码，用来构造边内唯一、且可被 duplicateOf 指到的 docId */
function edgeHash(edgeKey: string): string {
  return hashStr(edgeKey).toString(36).slice(0, 6).padStart(6, '0');
}

/* 证据是生成的。生成的证据一旦挂上实名企业与实名媒体，
   界面上就会出现"华为技术 · XX 方向招聘"这种条目 ——
   以一家真实企业的名义发布一条我们编出来的招聘信息。
   这比其它任何一处生成数据都更不该出现在真实数据模式下。
   演示模式（VITE_DATA=mock）保留原词表，用于规模对照。 */
const ANON = <T>(real: string[], mock: T[]): (T | string)[] => (IS_REAL_TAXONOMY ? real : mock);
const EV_COMPANIES = ANON(
  Array.from({ length: 12 }, (_, i) => `企业 ${String.fromCharCode(65 + i)}`),
  COMPANIES,
) as string[];
const EV_CITIES = ANON(['华东某市', '华北某市', '华南某市', '华中某市', '西部某市'], CITIES) as string[];
const EV_PAPER_VENUES = ANON(['学术会议 A', '学术会议 B', '期刊 C', '期刊 D'], PAPER_VENUES) as string[];
const EV_NEWS_OUTLETS = ANON(['行业媒体 A', '行业媒体 B', '行业媒体 C'], NEWS_OUTLETS) as string[];

function makeEvidence(
  edgeKey: string,
  aName: string,
  bName: string,
  mix: { jd: number; paper: number; news: number },
  count: number,
  lastMonth: string,
  /**
   * 这条边的原文实际会点名的东西，按权重抽样。
   *
   * 招聘信息不会写“要求具备编程能力”，它写的是“精通 Python”；
   * 归并到“编程能力”是系统做的事，不是原文说的话。所以 J-S / T-S 边的证据
   * 要落到该能力下的某个技能点上，`extractedNodeId` 记的就是这一跳。
   * 其余层（J-T、S-SP）的原文本来就直接点名边的终点，锚点即终点。
   */
  anchors: EvidenceAnchor[],
): EvidenceRef[] {
  const out: EvidenceRef[] = [];
  const total = mix.jd + mix.paper + mix.news || 1;
  // 期望条数取整时按小数部分做伯努利，避免"每条边都恰好三源俱全"的失真：
  // 成熟实体的边本就应当只有 JD 证据，单源未交叉验证是真实存在的状态
  const draw = (share: number, tag: string) => {
    const exact = (share / total) * count;
    const base = Math.floor(exact);
    return base + (rand01(`${edgeKey}|cnt|${tag}`) < exact - base ? 1 : 0);
  };
  let nJd = draw(mix.jd, 'jd');
  const nPaper = draw(mix.paper, 'paper');
  const nNews = draw(mix.news, 'news');
  if (nJd + nPaper + nNews === 0) nJd = 1;

  /** 按 w 加权抽一个锚点 —— 抽中概率与 S-SP 边权一致，剖面各段宽度才有据可依 */
  const wsum = anchors.reduce((a, b) => a + b.w, 0);
  const anchorAt = (k: string): EvidenceAnchor => {
    if (anchors.length === 0) return { id: '', name: bName, w: 1 };
    let r = rand01(k + '|anchor') * (wsum || anchors.length);
    for (const a of anchors) {
      r -= wsum ? a.w : 1;
      if (r <= 0) return a;
    }
    return anchors[anchors.length - 1];
  };
  /** 被点名的那串字在片段里的位置，供原文高亮 */
  const spanOf = (snippet: string, name: string): [number, number] | undefined => {
    const i = snippet.indexOf(name);
    return i < 0 ? undefined : [i, name.length];
  };

  const jd: EvidenceRef[] = [];
  const rest: EvidenceRef[] = [];

  for (let i = 0; i < nJd; i++) {
    const k = `${edgeKey}|jd|${i}`;
    const company = pick(k + '|co', EV_COMPANIES);
    const salary = randRange(k + '|sal', 12, 78);
    const an = anchorAt(k);
    const snippet = pick(k + '|tpl', JD_TEMPLATES)(aName, an.name);
    jd.push({
      docId: `JD-${edgeHash(edgeKey)}-${String(i).padStart(2, '0')}`,
      sourceType: 'jd',
      title: `${company} · ${an.name}方向招聘`,
      publishedAt: addMonths(lastMonth, -randInt(k + '|back', 0, 26)),
      snippet,
      salaryWeight: Number(Math.log(1 + salary / 32).toFixed(2)),
      originality: 1,
      company,
      city: pick(k + '|city', EV_CITIES),
      extractedNodeId: an.id || undefined,
      span: spanOf(snippet, an.name),
    });
  }

  /* 第二趟才判模板复制。
     抄袭是“照抄了某一条”，所以副本必须指向本边内真实存在的一条原文，
     并且连片段一起照抄 —— 上一版给的是一个凭空生成的 docId，
     界面上有抄袭标记却点不开被抄的那条，这个标记就没法核验。 */
  for (let i = 0; i < jd.length; i++) {
    const k = `${edgeKey}|jd|${i}`;
    if (rand01(k + '|dup') >= 0.18) continue;
    const src = jd.find((x, m) => m !== i && !x.duplicateOf);
    if (!src) continue;
    const sim = Number(randRange(k + '|sim', 0.951, 0.998).toFixed(3));
    jd[i] = {
      ...jd[i],
      snippet: src.snippet,
      span: spanOf(src.snippet, src.snippet.slice(src.span?.[0] ?? 0, (src.span?.[0] ?? 0) + (src.span?.[1] ?? 0))),
      extractedNodeId: src.extractedNodeId,
      duplicateOf: src.docId,
      duplicateSim: sim,
      originality: Number((1 - sim).toFixed(3)),
    };
  }

  for (let i = 0; i < nPaper; i++) {
    const k = `${edgeKey}|paper|${i}`;
    const an = anchorAt(k);
    const snippet = pick(k + '|tpl', PAPER_TEMPLATES)(aName, an.name);
    rest.push({
      docId: `arXiv:${randInt(k, 2401, 2607)}.${randInt(k + '|n', 1000, 9999)}`,
      sourceType: 'paper',
      /* 模板选择挂上被点名实体，而不只是边键：首页只取每条边的第一篇论文，
         若只按边键选，榜单三条很容易落到同一个句式上 */
      title: `${pick(k + '|v', EV_PAPER_VENUES)} · ${pick(`${k}|t|${an.name}`, PAPER_TITLES)(aName, an.name)}`,
      publishedAt: addMonths(lastMonth, -randInt(k + '|back', 0, 26)),
      snippet,
      salaryWeight: 1,
      originality: 1,
      extractedNodeId: an.id || undefined,
      span: spanOf(snippet, an.name),
    });
  }
  for (let i = 0; i < nNews; i++) {
    const k = `${edgeKey}|news|${i}`;
    const an = anchorAt(k);
    const snippet = pick(k + '|tpl', NEWS_TEMPLATES)(aName, an.name);
    rest.push({
      docId: `NEWS-${edgeHash(edgeKey)}-${String(i).padStart(2, '0')}`,
      sourceType: 'news',
      title: `${pick(k + '|o', EV_NEWS_OUTLETS)} · ${an.name}加速渗透${aName}`,
      publishedAt: addMonths(lastMonth, -randInt(k + '|back', 0, 26)),
      snippet,
      salaryWeight: 1,
      originality: 1,
      extractedNodeId: an.id || undefined,
      span: spanOf(snippet, an.name),
    });
  }

  out.push(...jd, ...rest);
  out.sort((a, b) => (a.publishedAt < b.publishedAt ? 1 : -1));
  return out;
}

/* ==================== 4. 边 ==================== */

function buildEdges(nodes: GraphNode[], signals: Map<string, EntitySignal>): GraphEdge[] {
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const edges: GraphEdge[] = [];
  const last = MONTHS.length - 1;

  /* ---- 能力 → 技能点的归属与相对权重 ----
     先算出来，后面三处都要用：S-SP 边本身、各条边的证据锚点、以及能力层的前瞻缺口。 */
  const bySkill = new Map<string, string[]>();
  for (const sp of SKILL_POINTS) {
    for (const s of sp.skills) {
      if (!bySkill.has(s)) bySkill.set(s, []);
      bySkill.get(s)!.push(sp.name);
    }
  }
  /** S → 该能力下各技能点的相对权重（归一化到 1） */
  const spShare = new Map<string, { id: string; name: string; w: number }[]>();
  for (const [skillName, sps] of bySkill) {
    const sid = id('S', skillName);
    const raws = sps.map((spn) => ({
      spn,
      raw: signals.get(id('SP', spn))!.jd[last] + 0.02 + rand01(`${sid}|${spn}|r`) * 0.05,
    }));
    const sum = raws.reduce((a, b) => a + b.raw, 0) || 1;
    spShare.set(
      sid,
      raws.map(({ spn, raw }) => ({ id: id('SP', spn), name: spn, w: raw / sum })),
    );
  }

  /* 能力层的前瞻缺口 = 该能力下各技能点缺口的加权平均。
     直接读 gap(S) 恒为 0 —— “编程能力”这种稳定类别本来就没人专门写论文论证，
     它的 jd 曲线早已饱和。前瞻信号真正发生在技能点层（Python → LangChain → MCP协议），
     所以能力层的前瞻只能从下一层归并上来。这与四层体系的设定是一致的：
     S 跨年不变、SP 快速更替。不这样算，前瞻滑杆对全部 J-S 与 T-S 边都是恒等函数。 */
  /* 缺口与论文/新闻强度必须一起归并，不能只归并缺口。
     只归并缺口的话，能力层的边会得到一个非零的 Δ，而 mix 仍按 gap(S)≈0 算出
     “论文 0 · 新闻 0”—— 屏幕上就出现了一个有前瞻修正、却拿不出任何前瞻原文的数。
     首页写的是“拿不出原文的说法不写入图谱”，那这三个量就得同源。 */
  const foreOf = (sid: string): { gap: number; paper: number; news: number } => {
    const sps = spShare.get(sid);
    const own = signals.get(sid);
    if (!sps || sps.length === 0)
      return { gap: own?.gap[last] ?? 0, paper: own?.paper[last] ?? 0, news: own?.news[last] ?? 0 };
    let g = 0;
    let p = 0;
    let n = 0;
    let den = 0;
    for (const sp of sps) {
      const s = signals.get(sp.id);
      g += sp.w * (s?.gap[last] ?? 0);
      p += sp.w * (s?.paper[last] ?? 0);
      n += sp.w * (s?.news[last] ?? 0);
      den += sp.w;
    }
    return den > 0 ? { gap: g / den, paper: p / den, news: n / den } : { gap: 0, paper: 0, news: 0 };
  };
  const skillGap = (sid: string): number => foreOf(sid).gap;

  const make = (
    source: string,
    target: string,
    kind: GraphEdge['kind'],
    baseWeight: number,
    delta0: number,
    explicit: boolean,
    /**
     * 生成 delta0 的那个前瞻缺口（delta0 = λ × foreGap）。
     *
     * 证据里有没有论文与新闻，由它决定 —— 而不是由边终点自己的 gap 决定。
     * 两者一旦分家，就会造出“有前瞻修正、却一条前瞻原文都拿不出”的边，
     * 或者反过来“论文证据一堆、权重却纹丝不动”。绑在同一个数上，
     * “Δ 不为零 ⟺ 有前瞻原文”在四类边上都成立。
     */
    foreGap: number,
  ) => {
    const sN = nodeById.get(source);
    const tN = nodeById.get(target);
    if (!sN || !tN) return;
    const sig = signals.get(target)!;
    const key = `${source}->${target}`;

    const decay = sig.decayFactor;
    const deltaWeight = delta0 * decay;
    const effectiveWeight = clamp(baseWeight + deltaWeight, 0, 1);

    // 前瞻因子：只有当实体确实存在前瞻缺口时，论文/新闻才构成这条边的证据来源。
    // 成熟实体（gap≈0）的边由 JD 单源支撑 —— 这正是可信度指纹上"环缺口"要表达的状态。
    // 强度取自生成 Δ 的同一个缺口；论文/新闻的相对构成，能力层从技能点归并上来
    // ——“编程能力”不会有人专门写论文，写的是它下面的 LangChain 与 MCP协议。
    const foreSrc = kind === 'J-S' || kind === 'T-S' ? foreOf(target) : { paper: sig.paper[last], news: sig.news[last] };
    const foresight = clamp(foreGap * 7, 0, 1);
    const jdShare = sig.jd[last];
    const paperShare = foreSrc.paper * foresight;
    const newsShare = foreSrc.news * foresight;
    const tot = jdShare + paperShare + newsShare || 1;
    const mix = {
      jd: baseWeight === 0 ? 0 : jdShare / tot,
      paper: paperShare / tot,
      news: newsShare / tot,
    };
    /* 证据条数与边权挂钩。
       原来是一律 3–9 条，于是把证据按城市（10 个）或企业（22 家）切开之后，
       每个子样本只剩 0–2 条 —— 那只能表达“有/无”，表达不了强度，
       “同一条关系在不同条件下是否都成立”这件事就没法看。
       强关系本来也确实会被更多广告写到，两件事是同一个道理。 */
    /* 真实数据模式下这些证据一条也不是测量结果，只是为了让"证据链"这条
       交互路径可演示。按演示规模生成 4 万条（edges JSON 约 11MB、首屏同步
       构建 880ms）纯属为一批注定不进图的数据付账，这里降到十分之一。 */
    const evCount = IS_REAL_TAXONOMY
      ? randInt(key + '|ev', 2, 4)
      : randInt(key + '|ev', 9, 16) + Math.round(baseWeight * 22);

    /* 证据在原文里点名谁：能力层的边落到该能力下的技能点上，其余层就是边的终点自己。 */
    const anchors =
      (kind === 'J-S' || kind === 'T-S') && spShare.has(target)
        ? spShare.get(target)!
        : [{ id: target, name: tN.name, w: 1 }];
    const evidence = makeEvidence(key, sN.name, tN.name, mix, evCount, NOW, anchors);
    const realMix = { jd: 0, paper: 0, news: 0 };
    for (const e of evidence) {
      if (e.sourceType === 'jd') realMix.jd++;
      else if (e.sourceType === 'paper') realMix.paper++;
      else realMix.news++;
    }

    const jdPrev = sig.jd[Math.max(0, last - 12)];
    let status: EdgeStatus;
    if (baseWeight === 0) status = 'candidate';
    else if (jdShare - jdPrev > 0.02) status = 'strengthening';
    else if (jdShare - jdPrev < -0.006) status = 'weakening';
    else status = 'active';

    const sourcesUsed = (realMix.jd > 0 ? 1 : 0) + (realMix.paper > 0 ? 1 : 0) + (realMix.news > 0 ? 1 : 0);
    const confidence = clamp(
      0.32 + 0.2 * sourcesUsed + 0.28 * effectiveWeight + (explicit ? 0.1 : 0) - (baseWeight === 0 ? 0.22 : 0),
      0.08,
      0.99,
    );

    edges.push({
      id: key,
      source,
      target,
      kind,
      baseWeight: Number(baseWeight.toFixed(3)),
      deltaWeight0: Number(delta0.toFixed(3)),
      deltaWeight: Number(deltaWeight.toFixed(3)),
      effectiveWeight: Number(effectiveWeight.toFixed(3)),
      confidence: Number(confidence.toFixed(3)),
      cooccurrence: Math.round(baseWeight * randRange(key + '|co', 400, 2600) + 12),
      explicitLink: explicit,
      firstSeen: sig.firstPaperAt ?? tN.firstSeen,
      lastConfirmed: sig.firstJdAt ? NOW : (sig.firstNewsAt ?? sig.firstPaperAt ?? tN.firstSeen),
      unconfirmedMonths: sig.firstJdAt ? 0 : Math.round(-Math.log(Math.max(decay, 1e-6)) / γ),
      status,
      sourceMix: realMix,
      evidence,
      /* 真实分类文件里唯一存在的关联是能力组→技能点（49 条，来自技能体系
         自身的三级结构）。岗位—任务、任务—能力、岗位—能力三类映射算法侧
         尚未产出，这里生成的 1540 条全部是推测，默认不进图。 */
      provenance: IS_REAL_TAXONOMY ? (kind === 'S-SP' ? 'measured' : 'synthetic') : 'synthetic',
    });
  };

  const gapOf = (nid: string) => signals.get(nid)?.gap[last] ?? 0;
  /* T-S 的前瞻要两头都成立：这项任务本身正在往前走，且它要的这类能力下面
     确实有技能点在被论文推着。任一头为零，这条边就没有前瞻可言。 */
  const tsGap = (tid: string, sid: string) => gapOf(tid) * skillGap(sid) * 6;

  // J-T：岗位由哪些任务构成
  for (const j of JOBS) {
    const jid = id('J', j.name);
    j.tasks.forEach((tn, i) => {
      const tid = id('T', tn);
      const w = clamp(randRange(`${jid}|${tid}|w`, 0.3, 0.82) * (1 - i * 0.06), 0.12, 0.92);
      make(jid, tid, 'J-T', Number(w.toFixed(3)), λ1 * gapOf(tid), true, gapOf(tid));
    });
  }

  // J-S：JD 中直接列出的能力要求
  for (const j of JOBS) {
    const jid = id('J', j.name);
    j.directSkills.forEach((sn, i) => {
      const sid = id('S', sn);
      const w = clamp(randRange(`${jid}|${sid}|w`, 0.48, 0.9) * (1 - i * 0.05), 0.2, 0.95);
      make(jid, sid, 'J-S', Number(w.toFixed(3)), λ1 * skillGap(sid), true, skillGap(sid));
    });
  }

  // T-S：任务需要哪些能力
  for (const t of TASKS) {
    const tid = id('T', t.name);
    t.skills.forEach((sn, i) => {
      const sid = id('S', sn);
      const explicit = rand01(`${tid}|${sid}|ex`) > 0.42;
      const w = clamp(
        0.55 * randRange(`${tid}|${sid}|co`, 0.3, 0.9) * (1 - i * 0.08) + (explicit ? 0.22 : 0),
        0.1,
        0.9,
      );
      make(tid, sid, 'T-S', Number(w.toFixed(3)), λ2 * tsGap(tid, sid), explicit, tsGap(tid, sid));
    });
  }

  // S-SP：能力类别包含哪些技能点（多对多，按各 S 独立归一化）
  // 相对份额在函数开头已按同一套种子算好（spShare），这里只做缩放与幽灵判定，
  // 保证“证据锚点抽中某技能点的概率”与“该技能点在能力里的权重”是同一个数。
  for (const [sid, sps] of spShare) {
    for (const sp of sps) {
      const w = sp.w * randRange(`${sid}|${sp.id}|k`, 0.85, 1.15);
      const isGhost = (signals.get(sp.id)?.jd[last] ?? 0) < 0.022;
      make(sid, sp.id, 'S-SP', isGhost ? 0 : Number(clamp(w * 2.4, 0.03, 0.85).toFixed(3)), λ3 * gapOf(sp.id), false, gapOf(sp.id));
    }
  }

  // S-S：能力共现（辅助边，技能簇）
  const skillNames = SKILLS.map((s) => s.name);
  for (let i = 0; i < skillNames.length; i++) {
    for (let k = i + 1; k < skillNames.length; k++) {
      const r = rand01(`SS|${skillNames[i]}|${skillNames[k]}`);
      if (r > 0.86) {
        // 能力共现是纯 JD 统计出来的辅助边，没有前瞻修正，也就没有前瞻原文
        make(id('S', skillNames[i]), id('S', skillNames[k]), 'S-S', Number((0.25 + r * 0.4).toFixed(3)), 0, false, 0);
      }
    }
  }

  return edges;
}

/* ==================== 边索引 ====================

   下游多数取数是「给定一个起点，沿某一类边走一跳」，而边表本批已达四万余条。
   逐次 filter 整表时，一次全站取数的边访问量是上亿级：职业探索页要对每个岗位
   各算一遍能力权重，其中两跳路径又要按任务再扫一遍，单页实测九秒余。

   索引按 `类型|起点` 归拢，随边表本身缓存 —— 键取数组自身，故同一张边表只建
   一次，调用方传入过滤后的子集时自动另建一份，不会串号。 */
const EDGE_SRC_INDEX = new WeakMap<GraphEdge[], Map<string, GraphEdge[]>>();
const NO_EDGES: GraphEdge[] = [];

/** 由 source 出发的某一类边。返回的数组为索引内部持有，调用方不得改写 */
export function edgesFrom(edges: GraphEdge[], kind: EdgeKind, source: string): GraphEdge[] {
  let ix = EDGE_SRC_INDEX.get(edges);
  if (!ix) {
    ix = new Map();
    for (const e of edges) {
      const k = `${e.kind}|${e.source}`;
      const arr = ix.get(k);
      if (arr) arr.push(e);
      else ix.set(k, [e]);
    }
    EDGE_SRC_INDEX.set(edges, ix);
  }
  return ix.get(`${kind}|${source}`) ?? NO_EDGES;
}

const EDGE_TGT_INDEX = new WeakMap<GraphEdge[], Map<string, GraphEdge[]>>();

/** 指向 target 的某一类边。索引与 edgesFrom 各自独立，按需建立 */
export function edgesTo(edges: GraphEdge[], kind: EdgeKind, target: string): GraphEdge[] {
  let ix = EDGE_TGT_INDEX.get(edges);
  if (!ix) {
    ix = new Map();
    for (const e of edges) {
      const k = `${e.kind}|${e.target}`;
      const arr = ix.get(k);
      if (arr) arr.push(e);
      else ix.set(k, [e]);
    }
    EDGE_TGT_INDEX.set(edges, ix);
  }
  return ix.get(`${kind}|${target}`) ?? NO_EDGES;
}

/* ==================== 5. 年轮 / 变更事件 ==================== */

/** 岗位在指定月份对各能力的有效权重（两路径聚合，算法 §4.1） */
export function jobSkillWeights(
  jobId: string,
  month: string,
  edges: GraphEdge[],
  signals: Map<string, EntitySignal>,
  lambda = 1,
): Map<
  string,
  {
    total: number;
    direct: number;
    viaTask: number;
    viaTasks: Map<string, number>;
    /** total 里由前瞻修正贡献的那一截 —— 用它算“前瞻占比”比 forward 布尔值精确得多 */
    overlay: number;
    forward: boolean;
    /* 证据构成沿着两条路径一起汇总：匹配报告里每一项要求旁边都要能立刻答出
       “这个数背后有几条证据、跨了几个来源”，而不是等用户切回全景图谱去查。
       置信度按贡献量加权 —— 一条只贡献 0.02 的边，不该把整项的置信度拉下来。 */
    mix: { jd: number; paper: number; news: number };
    confidence: number;
    /** 上面那个加权平均的分母，算完即弃 */
    confWeight: number;
  }
> {
  /* 月份转下标按轴上的位置查，不按自然月相减：观测窗口未必连续，
     相减会在断档之后错位，末窗读到 undefined，整条权重链随之成 NaN */
  const at = MONTHS.indexOf(month);
  const mi = at >= 0 ? at : Math.max(0, Math.min(MONTHS.length - 1, monthDiff(START_MONTH, month)));
  /* 相对强度按节点缓存：两跳路径上同一项技能会被十余条任务各要求一次，
     而这里每次都要在四十六个窗口的序列上求一遍峰值 —— 一次全站取数下来
     是八百余万次。同一次调用内 mi 固定，故同一节点的值必然相同。 */
  const relCache = new Map<string, number>();
  const rel = (nid: string) => {
    const hit = relCache.get(nid);
    if (hit !== undefined) return hit;
    const s = signals.get(nid);
    let v = 1;
    if (s) {
      let peak = 0.001;
      for (let i = 0; i < s.jd.length; i++) if (s.jd[i] > peak) peak = s.jd[i];
      v = clamp(s.jd[mi] / peak + s.gap[mi] * 0.8, 0, 1.2);
    }
    relCache.set(nid, v);
    return v;
  };

  const out = new Map<
    string,
    {
      total: number;
      direct: number;
      viaTask: number;
      viaTasks: Map<string, number>;
      overlay: number;
      forward: boolean;
      mix: { jd: number; paper: number; news: number };
      confidence: number;
      confWeight: number;
    }
  >();
  const ensure = (sid: string) => {
    if (!out.has(sid))
      out.set(sid, {
        total: 0,
        direct: 0,
        viaTask: 0,
        viaTasks: new Map(),
        overlay: 0,
        forward: false,
        mix: { jd: 0, paper: 0, news: 0 },
        confidence: 0,
        confWeight: 0,
      });
    return out.get(sid)!;
  };

  /** 把一条边的证据构成计入某一项能力，按它对该项要求的贡献量加权 */
  const credit = (o: ReturnType<typeof ensure>, e: GraphEdge, contribution: number) => {
    o.mix.jd += e.sourceMix.jd;
    o.mix.paper += e.sourceMix.paper;
    o.mix.news += e.sourceMix.news;
    const w = Math.max(contribution, 1e-6);
    o.confidence += e.confidence * w;
    o.confWeight += w;
  };

  const eff = (e: GraphEdge) => e.baseWeight + lambda * e.deltaWeight;

  // 路径一：J → S
  for (const e of edgesFrom(edges, 'J-S', jobId)) {
    const o = ensure(e.target);
    const r = rel(e.target);
    const v = eff(e) * r;
    o.direct += v;
    o.total += v;
    o.overlay += lambda * e.deltaWeight * r;
    credit(o, e, v);
    if (e.deltaWeight > 0.02) o.forward = true;
  }
  // 路径二：J → T → S
  const jt = edgesFrom(edges, 'J-T', jobId);
  for (const e1 of jt) {
    const r1 = rel(e1.target);
    const wJT = eff(e1) * r1;
    const wJTBase = e1.baseWeight * r1;
    for (const e2 of edgesFrom(edges, 'T-S', e1.target)) {
      const r2 = rel(e2.target);
      const v = wJT * eff(e2) * r2;
      const o = ensure(e2.target);
      o.viaTask += v;
      o.total += v;
      o.overlay += Math.max(0, v - wJTBase * e2.baseWeight * r2);
      o.viaTasks.set(e1.target, (o.viaTasks.get(e1.target) ?? 0) + v);
      /* 两跳路径上，“这项能力被要求”这句话是 T→S 那条边在讲的，
         J→T 只决定这条任务在本岗位里有多重。所以证据记 e2，权重按最终贡献。 */
      credit(o, e2, v);
      if (e1.deltaWeight > 0.02 || e2.deltaWeight > 0.02) o.forward = true;
    }
  }
  /** 把加权和收成加权平均 —— 调用方拿到的 confidence 直接可用 */
  for (const o of out.values()) if (o.confWeight > 0) o.confidence /= o.confWeight;
  return out;
}

function buildAnnuli(
  nodes: GraphNode[],
  edges: GraphEdge[],
  signals: Map<string, EntitySignal>,
): { annuli: JobAnnuli[]; changes: ChangeEvent[] } {
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const annuli: JobAnnuli[] = [];
  const changes: ChangeEvent[] = [];
  const SHARE_FLOOR = 0.02;
  const MODIFY_THRESHOLD = 0.18;

  for (const j of JOBS) {
    const jid = id('J', j.name);
    const rings: AnnulusRing[] = [];
    const defs = VERSION_DEFS_ALL;

    for (let vi = 0; vi < defs.length; vi++) {
      const def = defs[vi];
      const predicted = !!PREDICTED_VERSION && vi === defs.length - 1;
      // 叠层贡献随版本推进逐步增强：早期版本几乎纯基图，越晚前瞻修正占比越高
      const ringLambda = predicted ? 1 : 0.2 + (0.55 * vi) / Math.max(defs.length - 2, 1);
      const w = jobSkillWeights(jid, def.date, edges, signals, ringLambda);
      const entries = [...w.entries()].map(([sid, v]) => ({ sid, v }));
      const sum = entries.reduce((a, b) => a + b.v.total, 0) || 1;
      const slices = entries
        .map(({ sid, v }) => {
          const n = nodeById.get(sid)!;
          return {
            skillId: sid,
            name: n.name,
            share: v.total / sum,
            status: n.status,
            origin: (v.forward && v.direct + v.viaTask < 0.15 ? 'overlay' : 'base') as 'base' | 'overlay',
          };
        })
        .filter((s) => s.share >= SHARE_FLOOR)
        .sort((a, b) => b.share - a.share);
      const renorm = slices.reduce((a, b) => a + b.share, 0) || 1;
      slices.forEach((s) => (s.share = s.share / renorm));
      rings.push({ version: def.version, date: def.date, slices, predicted });
    }

    // 由相邻环差分导出变更事件 —— 保证年轮与变更清单严格一致
    const jobChanges: ChangeEvent[] = [];
    for (let vi = 1; vi < rings.length; vi++) {
      const prev = new Map(rings[vi - 1].slices.map((s) => [s.skillId, s]));
      const cur = new Map(rings[vi].slices.map((s) => [s.skillId, s]));
      const ver = rings[vi].version;
      const date = rings[vi].date;

      for (const [sid, s] of cur) {
        const p = prev.get(sid);
        const node = nodeById.get(sid)!;
        const ev = pickEvidenceFor(edges, jid, sid);
        if (!p) {
          jobChanges.push({
            id: `${jid}|${ver}|add|${sid}`,
            version: ver,
            date,
            op: 'add',
            jobId: jid,
            target: { kind: node.kind, id: sid, name: node.name },
            field: 'effective_weight',
            before: 0,
            after: Number(s.share.toFixed(3)),
            reason: `${date} 批次中，${node.name}在该岗位招聘信息中的共现频率跨过入图阈值${
              s.origin === 'overlay' ? '；当前主要由论文与新闻侧的前瞻信号支撑，招聘侧尚待确认' : '，并获多源独立确认'
            }。`,
            sources: ev,
            reviewState: s.origin === 'overlay' ? 'auto' : 'approved',
          });
        } else {
          const rel = (s.share - p.share) / Math.max(p.share, 1e-6);
          if (Math.abs(rel) > MODIFY_THRESHOLD) {
            jobChanges.push({
              id: `${jid}|${ver}|mod|${sid}`,
              version: ver,
              date,
              op: 'modify',
              jobId: jid,
              target: { kind: node.kind, id: sid, name: node.name },
              field: 'effective_weight',
              before: Number(p.share.toFixed(3)),
              after: Number(s.share.toFixed(3)),
              reason:
                rel > 0
                  ? `该能力在本岗位招聘信息中的提及占比较上一版本上升 ${(rel * 100).toFixed(0)}%，且高薪岗位样本的加权贡献同步提高。`
                  : `该能力占比较上一版本下降 ${(Math.abs(rel) * 100).toFixed(0)}%，相关任务的技能要求正被更细分的技能点取代。`,
              sources: ev,
              reviewState: 'auto',
            });
          }
        }
      }
      for (const [sid, p] of prev) {
        if (cur.has(sid)) continue;
        const node = nodeById.get(sid)!;
        jobChanges.push({
          id: `${jid}|${ver}|rm|${sid}`,
          version: ver,
          date,
          op: 'remove',
          jobId: jid,
          target: { kind: node.kind, id: sid, name: node.name },
          field: 'effective_weight',
          before: Number(p.share.toFixed(3)),
          after: 0,
          reason: `连续两个批次未在该岗位招聘信息中检出有效证据，置信度衰减至删除阈值以下，按软删除移出当前版本（历史版本保留）。`,
          sources: pickEvidenceFor(edges, jid, sid),
          reviewState: 'auto',
        });
      }
    }

    // 注入 Quality Guardian 的冗余合并事件
    if (rand01(`${jid}|merge`) > 0.62 && rings.length > 3) {
      const ri = randInt(`${jid}|mri`, 2, rings.length - 2);
      const s = rings[ri].slices[randInt(`${jid}|msi`, 0, Math.max(0, rings[ri].slices.length - 1))];
      if (s) {
        jobChanges.push({
          id: `${jid}|${rings[ri].version}|merge|${s.skillId}`,
          version: rings[ri].version,
          date: rings[ri].date,
          op: 'merge',
          jobId: jid,
          target: { kind: 'skill', id: s.skillId, name: s.name },
          reason: `图谱健康巡检检出冗余节点对，三项信号同时越过阈值，经大模型消歧确认为同一概念的不同表述，已合并。`,
          sources: pickEvidenceFor(edges, jid, s.skillId),
          reviewState: 'approved',
          mergeScores: {
            nameCosine: Number(randRange(`${jid}|mc`, 0.86, 0.95).toFixed(2)),
            outJaccard: Number(randRange(`${jid}|mo`, 0.71, 0.88).toFixed(2)),
            inJaccard: Number(randRange(`${jid}|mi`, 0.7, 0.9).toFixed(2)),
          },
          mergedFrom: `${s.name}（旧表述）`,
        });
      }
    }

    jobChanges.sort((a, b) => (a.date < b.date ? 1 : -1));
    annuli.push({ jobId: jid, jobName: j.name, rings, changes: jobChanges });
    changes.push(...jobChanges);
  }

  return { annuli, changes };
}

/**
 * 变更事件的数据源：直达 J-S 边 > 该岗位任务的 T-S 边。
 * 只认落在这个岗位邻域内的证据 —— 借用别的岗位的原文会让“更新依据”失真，
 * 宁可返回空，让界面照实标注“尚无可展示原文”。
 */
function pickEvidenceFor(edges: GraphEdge[], jobId: string, skillId: string): EvidenceRef[] {
  const jobTasks = new Set(edges.filter((e) => e.kind === 'J-T' && e.source === jobId).map((e) => e.target));
  const pool = [
    ...(edges.find((e) => e.source === jobId && e.target === skillId)?.evidence ?? []),
    ...edges
      .filter((e) => e.kind === 'T-S' && jobTasks.has(e.source) && e.target === skillId)
      .flatMap((e) => e.evidence),
  ];

  // 同一段模板文案在多条 JD 里复现，清单里连列三遍只是噪声：按原文去重后再取样
  const seen = new Set<string>();
  const out: EvidenceRef[] = [];
  for (const ev of [...pool.filter((e) => !e.duplicateOf), ...pool.filter((e) => e.duplicateOf)]) {
    if (seen.has(ev.snippet)) continue;
    seen.add(ev.snippet);
    out.push(ev);
    if (out.length === 3) break;
  }
  return out;
}

/* ==================== 6. Loop / 质量 / 治理 ==================== */

function buildLoops(): LoopRun[] {
  return VERSION_DEFS.slice()
    .reverse()
    .slice(0, 5)
    .map((v, i) => {
      const k = `loop|${v.version}`;
      const jd = randInt(k + '|jd', 620, 1850);
      const paper = randInt(k + '|pp', 240, 720);
      const news = randInt(k + '|nw', 90, 320);
      return {
        id: `LOOP-${v.version}`,
        version: v.version,
        startedAt: `${v.date}-01`,
        batch: { jd, paper, news },
        agents: [
          {
            name: 'Collector',
            role: '采集预处理',
            status: i === 0 ? 'done' : 'done',
            durationMs: randInt(k + '|d1', 40000, 120000),
            input: `招聘平台 ${jd} 条 JD · arXiv ${paper} 篇 · 行业新闻 ${news} 条`,
            output: `去重后 ${Math.round(jd * 0.83)} 条 JD 进入抽取队列`,
            metric: `文本相似度>95% 折叠 ${Math.round(jd * 0.17)} 条`,
          },
          {
            name: 'Extractor',
            role: 'LLM 实体与边证据抽取',
            status: 'done',
            durationMs: randInt(k + '|d2', 180000, 520000),
            input: `Top-K(K=30) 图谱术语上下文 + 原文分块`,
            output: `节点候选 ${randInt(k + '|n', 320, 980)} · 直接边证据 ${randInt(k + '|e', 540, 1600)}`,
            metric: `new_candidate 占比 ${(randRange(k + '|nc', 0.06, 0.14) * 100).toFixed(1)}%`,
          },
          {
            name: 'Graph Builder',
            role: '基图构建（仅 JD）',
            status: 'done',
            durationMs: randInt(k + '|d3', 20000, 60000),
            input: `JD 侧抽取结果`,
            output: `更新 J-T / J-S / T-S / S-SP 四类边`,
            metric: `历史衰减 α=0.85`,
          },
          {
            name: 'Evolution Analyzer',
            role: '叠层计算（论文/新闻）',
            status: 'done',
            durationMs: randInt(k + '|d4', 15000, 48000),
            input: `论文 ${paper} 篇 + 新闻 ${news} 条的实体强度`,
            output: `Δw 修正 ${randInt(k + '|dw', 40, 160)} 条`,
            metric: `γ_paper=0.05 · γ_news=0.12`,
          },
          {
            name: 'Quality Guardian',
            role: '质量巡检',
            status: 'done',
            durationMs: randInt(k + '|d5', 30000, 90000),
            input: `合成后的 G_eff 全图`,
            output: `冗余候选 ${randInt(k + '|mg', 3, 14)} 对 · 幻觉拦截 ${randInt(k + '|hb', 2, 11)} 条`,
            metric: `噪声话术过滤 ${randInt(k + '|nf', 120, 460)} 处`,
          },
          {
            name: 'Matching',
            role: '人岗匹配服务',
            status: i === 0 ? 'running' : 'idle',
            durationMs: randInt(k + '|d6', 200, 900),
            input: `用户简历实时提交`,
            output: `Skill 层向量 + cosine 匹配`,
            metric: `不参与 Loop，读 G_eff`,
          },
        ],
        deltas: {
          nodesAdded: randInt(k + '|na', 6, 34),
          edgesAdded: randInt(k + '|ea', 18, 92),
          edgesStrengthened: randInt(k + '|es', 40, 180),
          edgesWeakened: randInt(k + '|ew', 12, 70),
          edgesRemoved: randInt(k + '|er', 2, 22),
          overlayApplied: randInt(k + '|oa', 30, 140),
        },
      } satisfies LoopRun;
    });
}

/**
 * 抄袭簇：直接由证据里已经判出的副本关系聚起来，不再另造一批 docId。
 *
 * 上一版的成员是随机生成的编号，与图谱里任何一条真实证据都对不上 ——
 * 界面上摆着“9 个模板簇、36 份副本”，却没有一份点得开。
 * 一个查不到源文档的抄袭判定，本身就是需要被防控的那种“幻觉”。
 */
function buildPlagiarism(edges: GraphEdge[]): PlagiarismCluster[] {
  const byDoc = new Map<string, EvidenceRef>();
  /** 被抄的那条 docId → 抄它的那些条 */
  const copiesOf = new Map<string, EvidenceRef[]>();
  for (const e of edges) {
    for (const ev of e.evidence) {
      if (ev.sourceType !== 'jd') continue;
      byDoc.set(ev.docId, ev);
      if (!ev.duplicateOf) continue;
      const arr = copiesOf.get(ev.duplicateOf);
      if (arr) arr.push(ev);
      else copiesOf.set(ev.duplicateOf, [ev]);
    }
  }

  return [...copiesOf.entries()]
    .filter(([src, copies]) => byDoc.has(src) && copies.length >= 2)
    .sort((a, b) => b[1].length - a[1].length)
    .slice(0, 9)
    .map(([srcId, copies], i) => {
      const anchor = byDoc.get(srcId)!;
      const members = copies
        .slice()
        .sort((a, b) => (a.publishedAt < b.publishedAt ? -1 : 1))
        .map((c) => ({
          docId: c.docId,
          title: c.title,
          company: c.company ?? '未署名企业',
          publishedAt: c.publishedAt,
          sim: c.duplicateSim ?? Number((1 - c.originality).toFixed(3)),
        }));
      return {
        id: `PC-${i + 1}`,
        canonicalDocId: anchor.docId,
        canonicalTitle: anchor.title,
        company: anchor.company ?? '—',
        publishedAt: anchor.publishedAt,
        similarity: Number((members.reduce((s, m) => s + m.sim, 0) / members.length).toFixed(3)),
        members,
        fingerprint: `sha1:${hashStr(anchor.snippet).toString(16).padStart(8, '0')}${hashStr(anchor.docId)
          .toString(16)
          .slice(0, 4)}`,
      };
    });
}

function buildNoise(): NoisePhrase[] {
  return NOISE_PHRASES.map((p, i) => ({
    phrase: p,
    docFreq: Number(randRange(`np|${i}`, 0.62, 0.96).toFixed(3)),
    tfidf: Number(randRange(`np|${i}|t`, 0.008, 0.06).toFixed(4)),
    action: rand01(`np|${i}|a`) > 0.4 ? 'ignored' : 'downweighted',
  }));
}

/** 二元字组余弦 —— 与源文件合并规则同一口径，可当场复算 */
function nameCosine2(a: string, b: string): number {
  const grams = (s: string) => {
    const m = new Map<string, number>();
    for (let i = 0; i < s.length - 1; i++) m.set(s.slice(i, i + 2), (m.get(s.slice(i, i + 2)) ?? 0) + 1);
    return m;
  };
  const A = grams(a);
  const B = grams(b);
  let dot = 0;
  for (const [g, v] of A) dot += v * (B.get(g) ?? 0);
  const na = Math.sqrt([...A.values()].reduce((x, y) => x + y * y, 0));
  const nb = Math.sqrt([...B.values()].reduce((x, y) => x + y * y, 0));
  return na && nb ? Number((dot / (na * nb)).toFixed(3)) : 0;
}

function buildMerges(): MergeCandidate[] {
  /* 真实数据下这一栏有据可依：岗位体系 v2.0 记录了每个岗位由哪几个 v1 节点
     归并而来（source_codes / source_names）。255 个 v1 节点里 200 个被保留，
     其中 33 个岗位收了不止一个来源 —— 下面逐条列的就是这些被并掉的节点。

     名称相似度按二元字组余弦当场算出；出/入边重合度算不了 ——
     岗位关联边算法侧尚未产出，如实写在说明里，不拿一个随机数填上去。 */
  if (IS_REAL_TAXONOMY) {
    let n = 0;
    return REAL_MERGES.flatMap((m) =>
      m.from
        // 与保留岗位同名的那个来源不是"被并掉的"，它就是这个岗位本身
        .filter((f) => f !== m.name)
        .map((from) => {
          n += 1;
          return {
            id: `MC-${n}`,
            kind: 'job' as const,
            a: { id: id('J', from), name: from },
            b: { id: id('J', m.name), name: m.name },
            nameCosine: nameCosine2(from, m.name),
            outJaccard: 0,
            inJaccard: 0,
            verdict: 'merged' as const,
            llmNote: `体系 v2.0 已记录该合并：v1 的"${from}"并入"${m.name}"（该岗位共收 ${m.from.length} 个来源节点，合计 ${m.posts.toLocaleString()} 条招聘信息）。出边/入边重合度未计算：岗位关联边本批数据未产出。`,
          };
        }),
    );
  }

  const pairs: [string, string, 'task' | 'skill'][] = [
    ['大模型应用开发', '大模型应用构建与集成', 'task'],
    ['向量检索优化', '向量库检索调优', 'task'],
    ['安全对齐与红队测试', '模型安全对抗评测', 'task'],
    ['提示词设计与评测', '提示词工程与效果评估', 'task'],
    ['算法优化能力', '性能优化能力', 'skill'],
    ['数据处理能力', '数据加工能力', 'skill'],
    ['端侧模型部署', '边缘侧模型落地', 'task'],
  ];
  return pairs.map(([a, b, kind], i) => {
    const k = `mc|${i}`;
    const nameCosine = Number(randRange(k + '|n', 0.83, 0.96).toFixed(3));
    const outJ = Number(randRange(k + '|o', 0.62, 0.91).toFixed(3));
    const inJ = Number(randRange(k + '|i', 0.6, 0.93).toFixed(3));
    const pass = nameCosine >= 0.85 && outJ >= 0.7 && inJ >= 0.7;
    return {
      id: `MC-${i + 1}`,
      kind,
      a: { id: id(kind === 'task' ? 'T' : 'S', a), name: a },
      b: { id: id(kind === 'task' ? 'T' : 'S', b), name: b },
      nameCosine,
      outJaccard: outJ,
      inJaccard: inJ,
      verdict: pass ? (rand01(k + '|v') > 0.35 ? 'merged' : 'pending') : 'kept',
      llmNote: pass
        ? '三项信号同时越阈；对比上下文后判定为同一概念的不同表述，建议合并并保留别名。'
        : '名称相近但图邻域重叠不足，判定为不同概念，保持独立节点。',
    };
  });
}

function buildHallucinations(): HallucinationBlock[] {
  const items: [string, string, HallucinationBlock['stage']][] = [
    ['“大模型算法工程师需精通量子计算”', '抽取结果在全部 1,842 条招聘信息中无证据支撑，判定为模型自由生成，不予采纳', 'extract'],
    ['“Agent编排工程师要求 5 年以上经验”', '与该岗位首现时间（2024-06）矛盾，时间一致性校验未通过', 'cross-validate'],
    ['“LangChain 属于数据库能力”', '归类与 S-SP 共现统计严重不符（共现率 0.4%），拒绝入图', 'graph-commit'],
    ['“具身智能算法工程师平均薪资 200k”', '超出该岗位薪资分布 P99 三倍，数值幻觉拦截', 'cross-validate'],
    ['“RAG 系统工程师必须掌握 COBOL”', '单源单次出现且无跨源确认，停留于候选状态未激活', 'graph-commit'],
    ['“MCP协议由 W3C 标准化发布”', '事实性断言无权威来源锚定，不写入节点定义', 'extract'],
    ['“数据分析师核心职责为模型训练加速”', '与该岗位 J-T 边分布冲突，职责越界判定', 'cross-validate'],
  ];
  return items.map(([claim, reason, stage], i) => ({
    id: `HB-${i + 1}`,
    claim,
    reason,
    stage,
    detectedAt: addMonths(NOW, -randInt(`hb|${i}`, 0, 9)),
  }));
}

/* ==================== 简历（演示用脱敏样本） ====================
   报告页要做到“一边看简历、一边看分析”，简历侧就不能只是一段纯文本：
   每一行都要有 id，右边的分析才点得回来、左边才高亮得起来。
   三份样本各自留了一些真实简历里常见的问题（技能只写在清单里、
   数字没有口径、经历时长与自述年限对不上、自我评价照抄招聘信息措辞），
   真实性核验那一块检出的就是这些 —— 不是写死的结论。 */

interface RawResume {
  name: string;
  years: number;
  degree: string;
  city: string;
  sections: { title: string; lines: string[] }[];
  experiences: {
    title: string;
    org: string;
    period: string;
    months: number;
    kind: ResumeExperience['kind'];
    bullets: string[];
    claims: string[];
    /** 对应原文行，写成 `段序号:行序号` */
    lines: string[];
  }[];
  /** [技能点名, 熟练度, 来源, 原文行] */
  skills: [string, number, 'list' | 'experience', string[]][];
}

const RAW_RESUMES: RawResume[] = [
  {
    name: '示例简历 · 应用开发方向',
    years: 3,
    degree: '硕士',
    city: '杭州',
    sections: [
      {
        title: '基本信息',
        lines: [
          '求职意向：大模型应用开发 / RAG 系统工程　·　期望城市：杭州、上海',
          '工作年限：3 年（2023.07 至今）　·　可到岗时间：一个月内',
        ],
      },
      {
        title: '教育背景',
        lines: [
          '2020.09 – 2023.06　某 985 高校　计算机技术　硕士',
          '2016.09 – 2020.06　某 211 高校　软件工程　本科',
        ],
      },
      {
        title: '工作与项目经历',
        lines: [
          '2023.07 – 至今　某电商科技公司　算法应用工程师',
          '企业知识库问答系统：基于 LangChain 与 Milvus 搭建 RAG检索增强 链路，负责文档切分、向量化与重排策略',
          '把线上问答准确率从 71% 提升到 86%',
          '用 Docker 打包推理服务并接入公司现有的 微服务架构，日均调用 12 万次',
          '2022.03 – 2023.02　某研究院　实习 · 算法工程',
          '参与多轮对话意图识别模型迭代，基于 Transformers 做微调，F1 提升 4 个点',
          '负责 提示词工程 与效果评测脚本，沉淀了一套 200 条的人工评测集',
        ],
      },
      {
        title: '技能清单',
        lines: [
          '编程语言：Python（熟练）、Java（了解）',
          '大模型工程：LangChain、LangGraph、提示词工程、RAG检索增强',
          '数据与存储：Milvus、Elasticsearch、MySQL',
          '工程化：Docker、微服务架构',
          '深度学习：PyTorch、Transformers',
        ],
      },
      {
        title: '其他',
        lines: [
          '英语六级；保持每周阅读 arXiv 论文的习惯，关注 Agent 编排与上下文工程方向',
          '自我评价：具备完整项目落地经验，能独立完成方案设计与实现，沟通能力强、抗压能力好、学习能力强',
        ],
      },
    ],
    experiences: [
      {
        title: '企业知识库问答系统',
        org: '某电商科技公司',
        period: '2023.07 – 至今',
        months: 36,
        kind: 'work',
        bullets: [
          '基于 LangChain 与 Milvus 搭建 RAG 检索链路，负责文档切分、向量化与重排策略',
          '把线上问答准确率从 71% 提升到 86%',
          '用 Docker 打包推理服务并接入公司现有的微服务架构，日均调用 12 万次',
        ],
        claims: ['LangChain', 'Milvus', 'RAG检索增强', 'Docker', '微服务架构', 'Python', 'Elasticsearch'],
        lines: ['2:0', '2:1', '2:2', '2:3'],
      },
      {
        title: '多轮对话意图识别',
        org: '某研究院（实习）',
        period: '2022.03 – 2023.02',
        months: 12,
        kind: 'project',
        bullets: [
          '参与多轮对话意图识别模型迭代，基于 Transformers 做微调，F1 提升 4 个点',
          '负责提示词工程与效果评测脚本，沉淀了一套 200 条的人工评测集',
        ],
        claims: ['Transformers', '提示词工程', 'Python'],
        lines: ['2:4', '2:5', '2:6'],
      },
    ],
    skills: [
      ['Python', 0.9, 'experience', ['3:0', '2:1']],
      ['LangChain', 0.72, 'experience', ['2:1', '3:1']],
      ['LangGraph', 0.35, 'list', ['3:1']],
      ['RAG检索增强', 0.68, 'experience', ['2:1', '3:1']],
      ['Milvus', 0.55, 'experience', ['2:1', '3:2']],
      ['Docker', 0.6, 'experience', ['2:3', '3:3']],
      ['微服务架构', 0.5, 'experience', ['2:3', '3:3']],
      ['提示词工程', 0.75, 'experience', ['2:6', '3:1']],
      ['Transformers', 0.5, 'experience', ['2:5', '3:4']],
      ['PyTorch', 0.45, 'list', ['3:4']],
      ['Elasticsearch', 0.5, 'list', ['3:2']],
      ['MySQL', 0.45, 'list', ['3:2']],
    ],
  },

  {
    name: '示例简历 · 数据工程方向',
    years: 5,
    degree: '本科',
    city: '深圳',
    sections: [
      {
        title: '基本信息',
        lines: [
          '求职意向：实时数仓 / 数据平台开发　·　期望城市：深圳、广州',
          '工作年限：5 年（2021.07 至今）　·　可到岗时间：两周内',
        ],
      },
      { title: '教育背景', lines: ['2017.09 – 2021.06　某双一流高校　软件工程　本科'] },
      {
        title: '工作经历',
        lines: [
          '2023.05 – 至今　某集团数据平台部　高级数据开发',
          '集团级实时数仓建设：Flink 与 Kafka 组成实时链路，日均处理 40 亿条事件，端到端延迟 P99 控制在 3 秒内',
          '基于 ClickHouse 重构指标查询层，核心看板在同一批 120 个查询上的 P95 耗时下降 62%',
          '2021.07 – 2023.04　某互联网公司　数据开发工程师',
          'Spark 离线任务治理：梳理 300+ 任务血缘，按资源画像重排队列，月度计算成本下降 32%',
          '用 Airflow 统一调度全部离线任务，任务失败率从 4.1% 降到 0.9%',
        ],
      },
      {
        title: '竞赛与开源',
        lines: ['2022 年　某数据挖掘竞赛　全国二等奖（第 3 名 / 1200 支队伍）'],
      },
      {
        title: '技能清单',
        lines: [
          '编程语言：Python、Java、Scala（了解）',
          '计算引擎：Spark、Flink',
          '消息与存储：Kafka、ClickHouse、MySQL',
          '调度与运维：Airflow',
        ],
      },
    ],
    experiences: [
      {
        title: '集团级实时数仓建设',
        org: '某集团数据平台部',
        period: '2023.05 – 至今',
        months: 38,
        kind: 'work',
        bullets: [
          'Flink 与 Kafka 组成实时链路，日均处理 40 亿条事件，端到端延迟 P99 控制在 3 秒内',
          '基于 ClickHouse 重构指标查询层，核心看板在同一批 120 个查询上的 P95 耗时下降 62%',
        ],
        claims: ['Flink', 'Kafka', 'ClickHouse', 'Python'],
        lines: ['2:0', '2:1', '2:2'],
      },
      {
        title: 'Spark 离线任务治理',
        org: '某互联网公司',
        period: '2021.07 – 2023.04',
        months: 22,
        kind: 'work',
        bullets: [
          '梳理 300+ 任务血缘，按资源画像重排队列，月度计算成本下降 32%',
          '用 Airflow 统一调度全部离线任务，任务失败率从 4.1% 降到 0.9%',
        ],
        claims: ['Spark', 'Airflow', 'MySQL', 'Python'],
        lines: ['2:3', '2:4', '2:5'],
      },
      {
        title: '某数据挖掘竞赛 全国二等奖',
        org: '公开竞赛',
        period: '2022 年',
        months: 3,
        kind: 'competition',
        bullets: ['第 3 名 / 1200 支队伍，负责特征工程与模型融合'],
        claims: ['Python', 'Spark'],
        lines: ['3:0'],
      },
    ],
    skills: [
      ['Python', 0.85, 'experience', ['4:0', '2:1']],
      ['Spark', 0.88, 'experience', ['2:4', '4:1']],
      ['Flink', 0.8, 'experience', ['2:1', '4:1']],
      ['Kafka', 0.78, 'experience', ['2:1', '4:2']],
      ['ClickHouse', 0.7, 'experience', ['2:2', '4:2']],
      ['Airflow', 0.65, 'experience', ['2:5', '4:3']],
      ['MySQL', 0.8, 'list', ['4:2']],
      ['Java', 0.6, 'list', ['4:0']],
    ],
  },

  {
    name: '示例简历 · 算法研究方向',
    years: 2,
    degree: '博士',
    city: '北京',
    sections: [
      {
        title: '基本信息',
        lines: [
          '求职意向：大模型算法 / 预训练与对齐　·　期望城市：北京',
          '研究经历：2 年（2024.03 起进入大模型方向）',
        ],
      },
      {
        title: '教育背景',
        lines: ['2022.09 – 至今　某 C9 高校　人工智能　博士在读', '2018.09 – 2022.06　某 985 高校　自动化　本科'],
      },
      {
        title: '科研经历',
        lines: [
          '2024.03 – 至今　领域大模型继续预训练与对齐',
          '在 PyTorch 上完成 7B 模型的继续预训练，使用 DeepSpeed 的 ZeRO-3 做显存优化',
          '实现 SFT 与 RLHF 两阶段对齐，人工评测胜率提升 19%',
          '2023.05 – 2024.02　高效微调方法对比研究',
          '系统对比 LoRA微调 与 QLoRA 在四个中文任务上的表现，发表 CCF-A 类论文 1 篇',
        ],
      },
      { title: '论文与专利', lines: ['一作 CCF-A 类论文 2 篇（其中 1 篇在审）；申请发明专利 1 项'] },
      {
        title: '技能清单',
        lines: [
          '框架：PyTorch、DeepSpeed、Transformers',
          '方法：Transformer架构、LoRA微调、RLHF、模型量化',
          '基础：Python、线性代数、概率统计、CUDA',
        ],
      },
      { title: '其他', lines: ['熟悉 vLLM 推理部署（课程项目）；英语可无障碍阅读文献'] },
    ],
    experiences: [
      {
        title: '领域大模型继续预训练与对齐',
        org: '某 C9 高校实验室',
        period: '2024.03 – 至今',
        months: 28,
        kind: 'research',
        bullets: [
          '在 PyTorch 上完成 7B 模型的继续预训练，使用 DeepSpeed 的 ZeRO-3 做显存优化',
          '实现 SFT 与 RLHF 两阶段对齐，人工评测胜率提升 19%',
        ],
        claims: ['PyTorch', 'DeepSpeed', 'RLHF', 'Transformer架构', 'Python'],
        lines: ['2:0', '2:1', '2:2'],
      },
      {
        title: '高效微调方法对比研究',
        org: '某 C9 高校实验室',
        period: '2023.05 – 2024.02',
        months: 10,
        kind: 'research',
        bullets: ['系统对比 LoRA 与 QLoRA 在四个中文任务上的表现，发表 CCF-A 类论文 1 篇'],
        claims: ['LoRA微调', 'PyTorch', 'Python'],
        lines: ['2:3', '2:4'],
      },
    ],
    skills: [
      ['PyTorch', 0.92, 'experience', ['2:1', '4:0']],
      ['Transformer架构', 0.85, 'experience', ['2:1', '4:1']],
      ['LoRA微调', 0.7, 'experience', ['2:4', '4:1']],
      ['RLHF', 0.6, 'experience', ['2:2', '4:1']],
      ['CUDA', 0.5, 'list', ['4:2']],
      ['Python', 0.88, 'experience', ['4:2', '2:1']],
      ['线性代数', 0.9, 'list', ['4:2']],
      ['概率统计', 0.85, 'list', ['4:2']],
      ['DeepSpeed', 0.45, 'experience', ['2:1', '4:0']],
    ],
  },
];

function buildResumes(): ResumeProfile[] {
  return RAW_RESUMES.map((r, ri) => {
    const lineId = (ref: string) => {
      const [s, l] = ref.split(':');
      return `r${ri}-s${s}-l${l}`;
    };
    const sections: ResumeSection[] = r.sections.map((sec, si) => ({
      id: `r${ri}-s${si}`,
      title: sec.title,
      lines: sec.lines.map((text, li) => ({ id: `r${ri}-s${si}-l${li}`, text })),
    }));
    const textOf = (ref: string) => {
      const [s, l] = ref.split(':').map(Number);
      return r.sections[s]?.lines[l] ?? '';
    };
    return {
      name: r.name,
      years: r.years,
      degree: r.degree,
      city: r.city,
      sections,
      experiences: r.experiences.map((e, ei) => ({
        id: `r${ri}-e${ei}`,
        title: e.title,
        org: e.org,
        period: e.period,
        months: e.months,
        kind: e.kind,
        bullets: e.bullets,
        claims: e.claims,
        lines: e.lines.map(lineId),
      })),
      skillPoints: r.skills.map(([n, p, from, refs]) => {
        /* 简历原文写的是具体技术名，落点分三种情形：

           其一，该写法本身就是图谱里的技能点（Python、Docker、MySQL 这一类），
           直接落在技能点节点上，不再归并；
           其二，图谱里没有这个技能点，但抽取词典能把它归并到某一项技能
           （LangChain 归到"AI智能体构建与编排"），落点为该技能节点；
           其三，两者皆不中，落点仍记作技能点，报告里如实标为未对齐。

           情形二在本批数据上不算少：这批招聘信息止于 2022-10，
           LangChain、Milvus 一类工具当时尚未进入招聘要求，
           它们在图谱里查不到技能点，只能归并到所属技能这一层。 */
        const inGraph = IS_REAL_GRAPH && SKILLPOINT_NAMES.has(n);
        const merged = !inGraph && IS_REAL_TAXONOMY ? DEMO_SKILL_EXTRACTION[n] : undefined;
        const skillCode = merged && IS_REAL_GRAPH ? SKILL_CODE_BY_NAME.get(merged) : undefined;
        const mapped = inGraph ? undefined : merged;
        return {
          id: skillCode ? `S:${skillCode}` : id('SP', mapped ?? n),
          name: n,
          mappedName: mapped,
          proficiency: p,
          // 抽取置信度：有经历描述兜底的自然更高，只写在清单里的一律偏低
          confidence: Number(
            randRange(
              `res|${r.name}|${n}`,
              from === 'experience' ? 0.84 : 0.62,
              from === 'experience' ? 0.98 : 0.8,
            ).toFixed(2),
          ),
          evidence: refs.map(textOf).find(Boolean) ?? '简历中提及',
          anchors: refs.map(lineId),
          from,
        };
      }),
      skillVector: {},
    } satisfies ResumeProfile;
  });
}

/* ==================== 组装 ==================== */

/* nodeById 与 signalMap 同为随数据集一次建成的索引：节点表本批两万余条，
   页面上「按 id 取一个节点」这件事出现在每一处跳转、每一条榜单与每一次悬停，
   逐次 find 一遍是首页切换报告期卡三秒的直接原因。 */
type FullDataset = Dataset & {
  annuli: JobAnnuli[];
  signalMap: Map<string, EntitySignal>;
  nodeById: Map<string, GraphNode>;
};

let _cache: FullDataset | null = null;

/**
 * 算法侧图谱产物的装配。
 *
 * 节点、边、月度序列、三源信号、版本、变更、能力年轮、Loop 批次八项取实测值；
 * 质量指标中的抄袭变体占比、跨源验证率、前瞻命中率三项由实测量算出，
 * 其余四项与噪声话术、冗余合并、幻觉拦截、简历四类数据本批产物未含，
 * 沿用演示补齐层，界面上按 provenance 的登记标注口径。
 */
function buildRealDataset(): FullDataset {
  const nodes = buildRealNodes();
  fillAbsentAttributes(nodes);
  const signals = buildRealSignals(nodes);
  const signalMap = new Map(signals.map((s) => [s.entityId, s]));
  const edges = buildRealEdges();
  fillCoreDuties(nodes, [...edges, ...INFERRED_EDGES]);
  const annuli = buildRealAnnuli();
  /* 变更清单两路合并：层级份额的跨窗差分，与各岗位技能构成的跨窗差分。
     前者回答"这一项在整个市场里的分量变了多少"，
     后者回答"这一项在某个岗位的要求里进退了多少"。 */
  const changes = [...buildRealChanges(nodes), ...annuli.flatMap((a) => a.changes)].sort((a, b) =>
    a.date < b.date ? 1 : a.date > b.date ? -1 : 0,
  );

  const foresightEdges = edges.filter((e) => e.deltaWeight > 1e-6);
  const crossValidated = foresightEdges.filter(
    (e) => [e.sourceMix.jd, e.sourceMix.paper, e.sourceMix.news].filter((x) => x > 0).length >= 2,
  ).length;
  /* 前瞻命中率：叠层记录的信号中，已在招聘信息里测到的比例。
     观测区间短于设计时滞时这一比例必然偏低；本批已覆盖四十八个自然月，
     长于算法侧设计的一年以上时滞，该比例因而开始有可读性。 */
  const foresight = signals.filter((s) => s.firstPaperAt || s.firstNewsAt);
  const foresightHit = foresight.filter((s) => s.firstJdAt).length;

  return {
    nodes,
    edges,
    /* 叠层新岗位的推导关联，单列不并入 edges —— 见 types/graph.ts 的字段说明 */
    inferredEdges: INFERRED_EDGES,
    signals,
    prismTimeline: buildRealPrism(),
    versions: buildRealVersions(),
    changes,
    loops: buildRealLoops(),
    quality: {
      ...DEMO_QUALITY,
      crossValidatedRatio: Number((crossValidated / Math.max(foresightEdges.length, 1)).toFixed(3)),
      foresightHitRate: Number((foresightHit / Math.max(foresight.length, 1)).toFixed(3)),
      lastEvaluatedAt: REAL_GRAPH_STATS.to,
    },
    plagiarism: buildPlagiarism(edges),
    noise: buildNoise(),
    merges: buildMerges(),
    hallucinations: buildHallucinations(),
    resumes: buildResumes(),
    annuli,
    signalMap,
    nodeById: new Map(nodes.map((n) => [n.id, n])),
  };
}

/**
 * 补齐招聘数据未含的分布。
 *
 * 现在只剩企业类别一项：原文表无对应列，仅有企业名，不足以判定类别
 * （“上海艾杰飞人力资源有限公司”是代招方，不是用人单位），
 * 故仍由演示补齐层按岗位名哈希铺开，界面上另标口径，
 * 登记在 provenance 的 node.attrs 通道。
 *
 * 学历一项原文表虽有其名、整批为空，值改由正文的门槛语抽出
 * （realGraph.degreeShare），与省份、经验、薪资三项一样有实测来源，
 * 此处只在该岗位一条也判不出时才回落。
 */

/** 岗位定义五要素里核心职责一项列出的任务数。列全了卡片要翻两屏 */
const CORE_DUTY_TOP = 6;

/**
 * 岗位定义五要素中的核心职责，取该岗位承担的任务。
 *
 * 四层体系里任务这一层给的正是“这个岗位做什么”，与核心职责问的是同一件事，
 * 无须另设字段：J-T 边按权重降序取前若干项即得。权重来自招聘信息的统计，
 * 因而这一项与图上该岗位的中段是同一份数据，两处不会各说各的。
 *
 * 叠层新岗位没有实测的 J-T 边，其任务由构建阶段推得（jobvec.mjs），
 * 边上带 inferred 标记，界面上按 derived 交代。推不出的岗位这一项留空。
 *
 * 五要素的其余三项（必备技能、加分技能、典型应用场景）由构建阶段按招聘统计推出，
 * 挂在节点的 jobDef 上（见 data-pipeline 的 6.5 节与 realGraph 的岗位节点一段）。
 */
function fillCoreDuties(nodes: GraphNode[], edges: GraphEdge[]) {
  const byJob = new Map<string, GraphEdge[]>();
  for (const e of edges) {
    if (e.kind !== 'J-T') continue;
    const arr = byJob.get(e.source);
    if (arr) arr.push(e);
    else byJob.set(e.source, [e]);
  }
  const nameOf = new Map(nodes.map((n) => [n.id, n.name]));
  for (const n of nodes) {
    if (n.kind !== 'job') continue;
    const arr = byJob.get(n.id);
    if (!arr?.length) continue;
    /* 同一个任务在 J-T 上可以挂着多条边，按边排序取前若干条时，同一个任务名
       因而重复出现（AI Agent 工程师的六项里"大模型应用开发"占了两项）。
       先按任务名合并、取其最大权重，再排序取前若干：这一栏列的是"这个岗位
       主要做哪几件事"，同一件事列两遍既占掉一个名额，也让人以为数据出了错。 */
    const best = new Map<string, number>();
    for (const e of arr) {
      const nm = nameOf.get(e.target) ?? '';
      if (!nm) continue;
      const cur = best.get(nm);
      if (cur === undefined || e.effectiveWeight > cur) best.set(nm, e.effectiveWeight);
    }
    n.coreDuties = [...best.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, CORE_DUTY_TOP)
      .map(([nm]) => nm);
  }
}

function fillAbsentAttributes(nodes: GraphNode[]) {
  for (const n of nodes) {
    if (n.kind !== 'job') continue;
    const mid = n.attrs?.medianSalary ?? 20;
    const demo = jobAttributes(n.name, [mid * 0.8, mid * 1.2], n.cluster ?? '', n.emerging, n.posts);
    if (!n.attrs) {
      /* 叠层岗位尚未进入招聘市场，六项分布一律留空。

         这六项在既有岗位上全部有实测来源：省份取招聘原文的 place 列，学历由正文
         的门槛语抽出，经验与薪资取汇总表，技术方向取技术栈一列，条数取 jobid 的
         连接数。给一个还没人在招的岗位编一份分布，等于把"这个岗位还没人在招"
         这条结论直接抹掉；界面上凡读到这六项的地方，对新岗位一律不画。 */
      n.attrs =
        n.origin === 'overlay'
          ? {
              cities: {},
              degrees: {},
              experience: {},
              salaryBands: {},
              techStacks: {},
              postCount: 0,
              medianSalary: 0,
            }
          : demo;
      continue;
    }
    /* 城市与学历来自招聘原文，逐岗位有值；原文表连不上该岗位、
       或该岗位的正文一条也判不出学历时才回落 */
    if (!Object.keys(n.attrs.cities).length) n.attrs.cities = demo.cities;
    if (!Object.keys(n.attrs.degrees).length) n.attrs.degrees = demo.degrees;
    /* 级别与薪资两项来自汇总表，档名与演示词表不同口径；汇总表缺该岗位时才回落 */
    if (!Object.keys(n.attrs.experience).length) n.attrs.experience = demo.experience;
    if (!Object.keys(n.attrs.salaryBands).length) n.attrs.salaryBands = demo.salaryBands;
  }
}

/** 本批产物未含的质量指标，沿用演示补齐层 */
const DEMO_QUALITY = {
  jdParseAccuracy: 0.937,
  resumeExtractAccuracy: 0.921,
  matchAccuracy: 0.908,
  testSetSize: 128,
  dedupRate: 0.171,
  noiseFilterRate: 0.083,
  hallucinationBlocked: 47,
  crossValidatedRatio: 0,
  foresightHitRate: 0,
  lastEvaluatedAt: '',
};

export function getDataset() {
  if (_cache) return _cache;
  if (IS_REAL_GRAPH) return (_cache = buildRealDataset());

  const nodes = buildNodes();
  const signals = buildSignals(nodes);
  const signalMap = new Map(signals.map((s) => [s.entityId, s]));
  applySignalsToNodes(nodes, signalMap);
  const edges = buildEdges(nodes, signalMap);
  const { annuli, changes } = buildAnnuli(nodes, edges, signalMap);

  const versions: GraphVersion[] = VERSION_DEFS.map((v, i) => {
    const f = (i + 1) / VERSION_DEFS.length;
    return {
      ...v,
      stats: {
        jobs: Math.round(JOBS.length * (0.65 + 0.35 * f)),
        tasks: Math.round(TASKS.length * (0.6 + 0.4 * f)),
        skills: SKILLS.length,
        skillPoints: Math.round(SKILL_POINTS.length * (0.55 + 0.45 * f)),
        edges: Math.round(edges.length * (0.58 + 0.42 * f)),
        overlayEdges: Math.round(edges.filter((e) => e.deltaWeight > 0.01).length * (0.4 + 0.6 * f)),
      },
    };
  });

  // 跨源验证率只对"带前瞻修正的边"计算才有意义：
  // 纯 JD 支撑的成熟边本就不该期待论文/新闻佐证
  const foresightEdges = edges.filter((e) => e.deltaWeight > 0.01);
  const crossValidated = foresightEdges.filter(
    (e) => [e.sourceMix.jd, e.sourceMix.paper, e.sourceMix.news].filter((x) => x > 0).length >= 2,
  ).length;

  const quality = {
    jdParseAccuracy: 0.937,
    resumeExtractAccuracy: 0.921,
    matchAccuracy: 0.908,
    testSetSize: 128,
    dedupRate: 0.171,
    noiseFilterRate: 0.083,
    hallucinationBlocked: 47,
    crossValidatedRatio: Number((crossValidated / Math.max(foresightEdges.length, 1)).toFixed(3)),
    foresightHitRate: (() => {
      const detected = signals.filter((s) => s.kind !== 'job' && s.firstPaperAt);
      const hit = detected.filter((s) => s.firstJdAt).length;
      return Number((hit / Math.max(detected.length, 1)).toFixed(3));
    })(),
    lastEvaluatedAt: '2026-07-12',
  };

  _cache = {
    nodes,
    edges,
    /* 演示词表与仅体系两档下没有叠层新岗位的推导关联可言 */
    inferredEdges: [],
    signals,
    prismTimeline: buildPrismTimeline(nodes, signalMap),
    versions,
    changes,
    loops: buildLoops(),
    quality,
    plagiarism: buildPlagiarism(edges),
    noise: buildNoise(),
    merges: buildMerges(),
    hallucinations: buildHallucinations(),
    resumes: buildResumes(),
    annuli,
    signalMap,
    nodeById: new Map(nodes.map((n) => [n.id, n])),
  };
  return _cache;
}

/** 技术栈分组统计（棱镜扇区用） */
export function stacksOf(nodes: GraphNode[]): TechStack[] {
  const set = new Set<TechStack>();
  nodes.forEach((n) => set.add(n.category));
  return [...set];
}
