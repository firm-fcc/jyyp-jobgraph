/* ============================================================
   人岗匹配 —— 算法侧 Matching Agent 的前端镜像
   · 岗位向量：J→S 直达 + J→T→S 两跳，两路径合并（保留分解，用来解释“为什么要这项能力”）
   · 简历向量：抽取到的技能点沿 S-SP 边反向映射回能力层
   · 匹配得分：两个向量的夹角相似度
   · 差距分析：已具备 / 需提升 / 缺失
   · 任务覆盖：岗位的每项核心任务，简历能覆盖到多少

   界面上不出现本文件里的算法记号（cosine / J_vec / λ 等），
   对外一律说人话，措辞对照见 README。
   ============================================================ */

import type {
  EntitySignal,
  GraphEdge,
  GraphNode,
  LearningStage,
  MatchAdvice,
  MatchItem,
  MatchResult,
  MatchTask,
  ResumeProfile,
} from '@/types/graph';
import { edgesFrom, getDataset, jobSkillWeights, NOW } from './generator';
import { SEED_SKILL_POINTS as SKILL_POINTS } from './seeds';

/** 简历技能点 → 能力层向量（沿 S-SP 边反向映射并加权归并） */
export function resumeToSkillVector(resume: ResumeProfile, edges: GraphEdge[]): Record<string, number> {
  const acc: Record<string, { num: number; den: number }> = {};
  for (const sp of resume.skillPoints) {
    /* 简历项已归并到技能这一层时直接计入，不必再沿边反查：
       归并的前提正是该写法在技能点层没有落点（见 generator 的抽取三情形）。 */
    if (sp.id.startsWith('S:')) {
      if (!acc[sp.id]) acc[sp.id] = { num: 0, den: 0 };
      acc[sp.id].num += sp.proficiency;
      acc[sp.id].den += 1;
      continue;
    }
    const links = edges.filter((e) => e.kind === 'S-SP' && e.target === sp.id);
    for (const l of links) {
      const w = Math.max(l.effectiveWeight, 0.05);
      if (!acc[l.source]) acc[l.source] = { num: 0, den: 0 };
      acc[l.source].num += sp.proficiency * w;
      acc[l.source].den += w;
    }
  }
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(acc)) out[k] = v.den > 0 ? v.num / v.den : 0;
  return out;
}

function cosine(a: Record<string, number>, b: Record<string, number>) {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  let dot = 0;
  let na = 0;
  let nb = 0;
  for (const k of keys) {
    const x = a[k] ?? 0;
    const y = b[k] ?? 0;
    dot += x * y;
    na += x * x;
    nb += y * y;
  }
  return na > 0 && nb > 0 ? dot / Math.sqrt(na * nb) : 0;
}

/* 技能点词表按名索引一次。词表本批一万七千余项，按名逐条线性查时，
   学习路径那一段（每项待补能力沿 S-SP 边取候选技能点）会退化成
   「边数 × 词表长度」的两重扫描 —— 实测一次 computeMatch 走掉一秒余。 */
const SP_BY_NAME = new Map(SKILL_POINTS.map((s) => [s.name, s]));
const spLevel = (name: string) => SP_BY_NAME.get(name)?.level ?? 2;

/**
 * 岗位的能力要求向量（归一化到最大项为 1）。
 * 排行榜、相近岗位、地形投影都要反复取同一个岗位的向量，
 * 而每算一次都要把全部边扫两遍，所以在模块级缓一份。
 * 数据集本身是确定性生成的、进程内不变，缓存不会脏。
 */
const vecCache = new Map<string, Record<string, number>>();

export function jobVector(jobId: string, lambda = 1): Record<string, number> {
  const key = `${jobId}|${lambda}`;
  const hitCache = vecCache.get(key);
  if (hitCache) return hitCache;
  const d = getDataset();
  const w = jobSkillWeights(jobId, NOW, d.edges, d.signalMap, lambda);
  const max = Math.max(...[...w.values()].map((v) => v.total), 1e-6);
  const o: Record<string, number> = {};
  for (const [sid, v] of w) o[sid] = v.total / max;
  vecCache.set(key, o);
  return o;
}

/**
 * 岗位自身直接要求的能力向量（只走 J→S 直达边，归一化到最大项为 1）。
 *
 * 与 jobVector 的差别在于不并入 J→T→S 那一跳。两跳在本批数据上占该岗位
 * 权重总量的九成五：任务层与能力层近乎全连（九十八项任务，每项平均连着
 * 五十项能力，能力共六十五项），逐条累加之后，凡是各项任务都要用到的
 * 通用能力都会被推到前列 —— 算法工程师一岗，“团队协作与协调”“书面技术
 * 文档撰写”即因此排在“机器学习与深度学习”之前。用于加权求和时这一层
 * 无妨，因为它对各岗位同向抬升；但拿来比岗位与岗位的异同就不成立：
 * 全站两两夹角相似度中位数因此落在 0.99，任何两个岗位看上去都一样。
 *
 * 岗位之间像不像，本就该看各自招聘信息里直接写明的能力要求。仅取直达边
 * 时相似度分布回到 0.46–0.94，可用于排序。
 */
const directVecCache = new Map<string, Record<string, number>>();

export function jobDirectVector(jobId: string, lambda = 1): Record<string, number> {
  const key = `${jobId}|${lambda}`;
  const hit = directVecCache.get(key);
  if (hit) return hit;
  const d = getDataset();
  const o: Record<string, number> = {};
  let max = 1e-6;
  for (const e of edgesFrom(d.edges, 'J-S', jobId)) {
    const v = e.baseWeight + lambda * e.deltaWeight;
    o[e.target] = v;
    if (v > max) max = v;
  }
  for (const k of Object.keys(o)) o[k] /= max;
  directVecCache.set(key, o);
  return o;
}

/* ==================== 综合匹配度 ====================
   界面上的“综合匹配度”必须能被下面五个维度当场验算出来 ——
   早先它直接取能力结构的夹角相似度，于是出现“综合 41.8，
   而五个分项分别是 42 / 47 / 50 / 89 / 100”这种低于所有分项的数，
   看的人只会认为算错了。现在它就是这五项的加权和，一分不多一分不少。

   权重取法：能力与任务是主证据，占六成；关键能力是“重点是否踩中”的
   补充；经验与学历是硬性条件，占比压低 —— 它们短期改不了，
   不该盖过能力结构本身。 */

export const DIM_WEIGHTS: { key: keyof MatchResult['dims']; weight: number }[] = [
  { key: 'skill', weight: 0.34 },
  { key: 'task', weight: 0.26 },
  { key: 'domain', weight: 0.16 },
  { key: 'experience', weight: 0.14 },
  { key: 'degree', weight: 0.1 },
];

/**
 * 五维全部计分。能力结构 / 任务覆盖 / 关键能力这三维依赖两样东西：
 * 岗位与能力之间的映射，以及把简历里的具体技术名归并到能力体系的抽取词典。
 * 算法侧都还没产出，演示补齐层（data/demoFill.ts）先各给了一份，
 * 因此这三维在界面上标为演示数据。
 */
/** 学历的序数轴。岗位一侧六档、简历一侧四档，同义写法一并登记 */
const DEGREE_RANK: Record<string, number> = {
  学历不限: 0,
  高中及中专: 1,
  高中: 1,
  中专: 1,
  大专: 2,
  专科: 2,
  本科: 3,
  学士: 3,
  硕士: 4,
  研究生: 4,
  博士: 5,
};

export const SCORED_DIMS: (keyof MatchResult['dims'])[] = DIM_WEIGHTS.map((d) => d.key);

export const overall = (dims: MatchResult['dims']) =>
  DIM_WEIGHTS.reduce((s, d) => s + d.weight * dims[d.key], 0);

/* 边索引：一次匹配要对全部岗位各算一遍五维分，
   若每次都 filter 整张边表，光索引这一层就是几十万次无谓迭代。 */
let edgeIdx: { edges: GraphEdge[]; jt: Map<string, GraphEdge[]>; ts: Map<string, GraphEdge[]> } | null = null;

function indexOf(edges: GraphEdge[]) {
  if (edgeIdx && edgeIdx.edges === edges) return edgeIdx;
  const jt = new Map<string, GraphEdge[]>();
  const ts = new Map<string, GraphEdge[]>();
  for (const e of edges) {
    const m = e.kind === 'J-T' ? jt : e.kind === 'T-S' ? ts : null;
    if (!m) continue;
    const arr = m.get(e.source);
    if (arr) arr.push(e);
    else m.set(e.source, [e]);
  }
  edgeIdx = { edges, jt, ts };
  return edgeIdx;
}

export interface JobFit {
  score: number;
  dims: MatchResult['dims'];
  items: MatchItem[];
  tasks: MatchTask[];
}

/* 以简历对象本身为键：接入真实简历后每次解析都是一份新对象，
   缓存自然作废，不会出现“换了简历还拿到上一份的分”这种脏读。 */
const fitCache = new WeakMap<ResumeProfile, Map<string, JobFit>>();

/**
 * 一个岗位的完整五维打分。报告页与排行榜走的是同一个函数 ——
 * 若排行榜另算一套轻量分，就会出现“下拉框里 42、报告里 57”这类对不上的数。
 * 简历向量由调用方算一次传进来，不在这里重算。
 */
export function fitAgainst(
  resume: ResumeProfile,
  rvec: Record<string, number>,
  jobId: string,
  lambda = 1,
): JobFit {
  const ck = `${jobId}|${lambda}`;
  let byJob = fitCache.get(resume);
  if (!byJob) {
    byJob = new Map<string, JobFit>();
    fitCache.set(resume, byJob);
  }
  const cached = byJob.get(ck);
  if (cached) return cached;

  const d = getDataset();
  const { edges } = d;
  const idx = indexOf(edges);
  /* 节点索引随数据集一次建成：一次排行要对全部岗位各算一遍分，
     在这里重建就是把两万余条节点表按岗位数重铺一遍 */
  const nodeById = d.nodeById;
  const job = nodeById.get(jobId)!;

  const jw = jobSkillWeights(jobId, NOW, edges, d.signalMap, lambda);
  const maxW = Math.max(...[...jw.values()].map((v) => v.total), 1e-6);

  const jvec: Record<string, number> = {};
  for (const [sid, v] of jw) jvec[sid] = v.total / maxW;

  /* 达成率 = 掌握程度相对本岗位要求的比例，封顶 1。
     整页统一用它判断“这项算不算短板”—— 能力清单里写着“已具备”、
     任务那一栏却把同一项列成短板，是上一版最刺眼的自相矛盾。 */
  const attain = (sid: string) => {
    const req = jvec[sid] ?? 0;
    if (req <= 0.02) return 1;
    return Math.min(1, (rvec[sid] ?? 0) / req);
  };
  /** 与 band 的 have 阈值同源：达成 85% 即视为站稳，不再计入短板 */
  const SOLID = 0.85;

  const skill = cosine(jvec, rvec);

  const items: MatchItem[] = [...jw.entries()]
    .map(([sid, v]) => {
      const required = v.total / maxW;
      const owned = rvec[sid] ?? 0;
      const gap = required - owned;
      const band: MatchItem['band'] = owned <= 0.02 ? 'missing' : attain(sid) >= SOLID ? 'have' : 'improve';
      return {
        skillId: sid,
        name: nodeById.get(sid)?.name ?? sid,
        required: Number(required.toFixed(3)),
        directPart: Number((v.direct / maxW).toFixed(3)),
        viaTaskPart: Number((v.viaTask / maxW).toFixed(3)),
        viaTasks: [...v.viaTasks.entries()]
          .map(([tid, part]) => ({
            taskId: tid,
            taskName: nodeById.get(tid)?.name ?? tid,
            part: Number((part / maxW).toFixed(3)),
          }))
          .sort((a, b) => b.part - a.part)
          .slice(0, 4),
        owned: Number(owned.toFixed(3)),
        gap: Number(gap.toFixed(3)),
        band,
        // 只要沾一点前瞻修正就打标，等于全场都是“前瞻”，标了跟没标一样。
        // 这里按前瞻贡献占该项要求的比重卡线，留下真正被前瞻信号推起来的那几项。
        forwardLooking: v.total > 0 && v.overlay / v.total >= 0.18,
        mix: v.mix,
        confidence: Number(v.confidence.toFixed(3)),
      };
    })
    .filter((i) => i.required > 0.04)
    .sort((a, b) => b.required - a.required);

  /* ---- 任务覆盖：岗位的每项核心任务，简历能覆盖到多少 ----
     一项任务的覆盖度 = 它所需各项能力的“加权平均达成率”，
     权重取任务→能力边的综合权重。这一层是 P→T→S 结构在匹配上的兑现：
     光看能力清单看不出“哪件事你还干不了”。

     用达成率而不是掌握程度本身：一项岗位只要求 0.55 的能力，
     简历有 0.77 就该按满覆盖算，按 0.77 算等于拿绝对值去比相对要求。 */
  const tasks: MatchTask[] = (idx.jt.get(jobId) ?? [])
    .map((jt) => {
      const links = idx.ts.get(jt.target) ?? [];
      let num = 0;
      let den = 0;
      const holes: { name: string; miss: number }[] = [];
      for (const e of links) {
        const req = e.baseWeight + lambda * e.deltaWeight;
        const a = attain(e.target);
        num += req * a;
        den += req;
        // 只有本身没站稳的能力才算这项任务的短板
        if (a < SOLID) holes.push({ name: nodeById.get(e.target)?.name ?? e.target, miss: req * (1 - a) });
      }
      return {
        taskId: jt.target,
        taskName: nodeById.get(jt.target)?.name ?? jt.target,
        weight: Number((jt.baseWeight + lambda * jt.deltaWeight).toFixed(3)),
        coverage: den > 0 ? Number((num / den).toFixed(3)) : 0,
        weakest: holes
          .sort((a, b) => b.miss - a.miss)
          .slice(0, 3)
          .filter((h) => h.miss > 0.05)
          .map((h) => h.name),
      };
    })
    .sort((a, b) => b.weight - a.weight);

  const taskDen = tasks.reduce((s, t) => s + t.weight, 0);
  const taskDim = taskDen > 0 ? tasks.reduce((s, t) => s + t.weight * t.coverage, 0) / taskDen : 0;

  /* ---- 其余维度 ---- */
  const attrs = job.attrs;
  const expBuckets = Object.entries(attrs?.experience ?? {});
  const expWant = expBuckets.sort((a, b) => b[1] - a[1])[0]?.[0] ?? '3-5年';
  const expTarget = expWant.includes('应届')
    ? 0
    : expWant.includes('1-3')
      ? 2
      : expWant.includes('3-5')
        ? 4
        : expWant.includes('5-10')
          ? 7
          : 10;
  /* 经验只罚“不够”，不罚“超出”：3 年经验去投一个写着“应届”的岗位，
     不该被算成 25 分的短板。超出很多时给一点轻微回落，表示岗位可能偏低配。 */
  const experience =
    resume.years >= expTarget
      ? Math.max(0.82, 1 - (resume.years - expTarget) / 26)
      : Math.max(0, 1 - (expTarget - resume.years) / Math.max(expTarget, 3));

  const degWant = Object.entries(attrs?.degrees ?? {}).sort((a, b) => b[1] - a[1])[0]?.[0] ?? '本科';
  /* 岗位一侧的档名来自招聘正文的门槛语（六档），简历一侧来自示例简历的词表（四档），
     两侧不同名而同序，故统一折到一根序数轴上再比。“学历不限”折作 0：
     它不是最低的一级，是"这个岗位不设学历门槛"，此时任何学历都满足。 */
  const want = DEGREE_RANK[degWant] ?? 3;
  const have = DEGREE_RANK[resume.degree] ?? 3;
  const degree = want <= 0 ? 1 : Math.min(1, have / want);

  /* 关键能力：要求最重的那批，按要求权重加权的达成率。
     早先只数“有几项不是空白”，一项达成 3% 和达成 100% 记同样的分。 */
  const keyItems = items.filter((i) => i.required > 0.35);
  const keyDen = keyItems.reduce((s, i) => s + i.required, 0);
  const domain = keyDen > 0 ? keyItems.reduce((s, i) => s + i.required * attain(i.skillId), 0) / keyDen : 0;

  const dims = {
    skill: Number(skill.toFixed(3)),
    task: Number(taskDim.toFixed(3)),
    experience: Number(experience.toFixed(3)),
    degree: Number(degree.toFixed(3)),
    domain: Number(domain.toFixed(3)),
  };

  const fit: JobFit = { score: Number(overall(dims).toFixed(3)), dims, items, tasks };
  byJob.set(ck, fit);
  return fit;
}

/**
 * 只要分数：给排行榜、相近岗位、地形投影用。
 * 与报告页同口径 —— 两处若各算各的，界面上就会出现对不上的两个数。
 */
export function scoreAgainst(
  resume: ResumeProfile,
  resumeVec: Record<string, number>,
  jobId: string,
  lambda = 1,
): number {
  return fitAgainst(resume, resumeVec, jobId, lambda).score;
}

export function computeMatch(resume: ResumeProfile, jobId: string, lambda = 1): MatchResult {
  const d = getDataset();
  const { edges } = d;
  const job = d.nodeById.get(jobId)!;

  const rvec = resumeToSkillVector(resume, edges);
  const { score, dims, items, tasks } = fitAgainst(resume, rvec, jobId, lambda);

  const expWant = Object.entries(job.attrs?.experience ?? {}).sort((a, b) => b[1] - a[1])[0]?.[0] ?? '3-5年';
  const degWant = Object.entries(job.attrs?.degrees ?? {}).sort((a, b) => b[1] - a[1])[0]?.[0] ?? '本科';

  return {
    jobId,
    jobName: job.name,
    score,
    dims,
    items,
    tasks,
    path: buildLearningPath(items, resume, edges, job.category),
    advice: buildAdvice(job, items, tasks, dims, resume, d.signalMap, expWant, degWant),
  };
}

/* ==================== 学习路径 ==================== */

function buildLearningPath(
  items: MatchItem[],
  resume: ResumeProfile,
  edges: GraphEdge[],
  jobStack: string,
): LearningStage[] {
  const owned = new Set(resume.skillPoints.map((s) => s.name));
  const needed = items.filter((i) => i.band !== 'have').sort((a, b) => b.gap - a.gap);

  /**
   * 把待补能力展开到具体技能点。
   * 一个能力类别下的技能点可能横跨多个技术栈（如“系统架构设计”既含 MQTT 也含 AI Gateway），
   * 因此按目标岗位所属技术栈加权，避免给 Agent 编排岗推荐物联网协议这类明显跑偏的路标。
   */
  const spFor = (skillId: string) =>
    edgesFrom(edges, 'S-SP', skillId)
      .map((e) => {
        const spName = e.target.replace(/^SP:/, '');
        const meta = SP_BY_NAME.get(spName);
        const relevance = meta?.category === jobStack ? 1 : 0.15;
        // 前沿技能点（前瞻信号支撑）基础权重天然偏低，此处补偿，
        // 以兑现“第三阶段取自前瞻信号”的设计
        const foresight = e.baseWeight < 0.02 ? 2.2 : 1;
        return { name: spName, score: e.effectiveWeight * relevance * foresight };
      })
      .sort((a, b) => b.score - a.score)
      .map((x) => x.name)
      .filter((n) => !owned.has(n));

  const buckets: { l: 1 | 2 | 3; items: { name: string; forwardLooking: boolean; gap: number }[] }[] = [
    { l: 1, items: [] },
    { l: 2, items: [] },
    { l: 3, items: [] },
  ];

  /* 候选池取宽一点：待补能力本来就可能只有两三项，
     每项只取前 3 个技能点的话，中间那一档常常一个都落不下，
     路径就会出现“第二阶段空着”这种看起来像出了 bug 的空段。 */
  for (const it of needed) {
    const sps = spFor(it.skillId).slice(0, 6);
    for (const sp of sps) {
      const lv = spLevel(sp);
      const b = buckets.find((x) => x.l === lv)!;
      if (b.items.length < 6 && !b.items.some((x) => x.name === sp)) {
        b.items.push({ name: sp, forwardLooking: it.forwardLooking && lv === 3, gap: it.gap });
      }
    }
  }

  const meta = [
    { title: '筑基', weeks: 6, res: ['官方文档精读', '基础项目复现', '公开课与教材'] },
    { title: '进阶', weeks: 10, res: ['生产级项目实战', '源码阅读', '性能调优专题'] },
    { title: '前沿', weeks: 12, res: ['论文与技术报告跟进', '开源社区贡献', '前沿方案验证'] },
  ];

  return buckets.map((b, i) => ({
    stage: i + 1,
    title: meta[i].title,
    weeks: meta[i].weeks,
    skills: b.items,
    resources: meta[i].res.map((t) => ({ title: t, type: i === 2 ? '前沿' : i === 1 ? '实战' : '入门' })),
  }));
}

/* ==================== 针对性改进建议 ====================
   赛题要求“提供针对性改进建议”。建议不另起一套逻辑，
   全部从上面已经算出的差距 / 任务覆盖 / 维度里读出来，
   保证界面上说的每一句话都能在同一页里找到对应的数字。 */

function buildAdvice(
  job: GraphNode,
  items: MatchItem[],
  tasks: MatchTask[],
  dims: MatchResult['dims'],
  resume: ResumeProfile,
  signalMap: Map<string, EntitySignal>,
  expWant: string,
  degWant: string,
): MatchAdvice[] {
  const out: MatchAdvice[] = [];

  // ① 权重最高的缺口
  const top = items.filter((i) => i.band !== 'have').sort((a, b) => b.gap - a.gap)[0];
  if (top) {
    const via = top.viaTasks.slice(0, 2).map((t) => `“${t.taskName}”`);
    out.push({
      kind: 'gap',
      title: `优先补齐 ${top.name}`,
      body:
        `缺口最大的一项：岗位要求 ${top.required.toFixed(2)}，当前水平 ${top.owned.toFixed(2)}。` +
        (via.length
          ? `该项要求主要经由 ${via.join('、')} 传导，补齐后相关任务的覆盖度将同步提升。`
          : '该项能力由招聘信息直接点名，属于硬性门槛。'),
    });
  }

  // ② 前瞻能力：论文/新闻已经走强、招聘市场尚未普及（第①条已经点过的不重复说）
  const fore = items.filter((i) => i.forwardLooking && i.band !== 'have' && i.skillId !== top?.skillId).slice(0, 2);
  if (fore.length) {
    const lead = fore
      .map((f) => signalMap.get(f.skillId)?.leadMonths.paper)
      .filter((v): v is number => typeof v === 'number' && v > 0);
    const leadTxt = lead.length ? `学术侧平均领先招聘市场约 ${Math.round(lead.reduce((a, b) => a + b, 0) / lead.length)} 个月，` : '';
    out.push({
      kind: 'forward',
      title: `提前储备 ${fore.map((f) => f.name).join(' / ')}`,
      body: `${leadTxt}该批要求目前仅出现在部分招聘信息中，但论文与行业新闻的强度已连续走高；学习路径第三阶段即依据该批信号排布。`,
    });
  }

  // ③ 覆盖度最低的核心任务
  const weakTask = tasks.filter((t) => t.weight > 0.2).sort((a, b) => a.coverage - b.coverage)[0];
  if (weakTask && weakTask.coverage < 0.6) {
    out.push({
      kind: 'dimension',
      title: `补充“${weakTask.taskName}”的实战经历`,
      body:
        (weakTask.coverage < 0.005
          ? '该任务在简历中暂无对应内容，为全部核心任务中覆盖度最低的一项。'
          : `该任务的覆盖度为 ${Math.round(weakTask.coverage * 100)}%，在全部核心任务中最低。`) +
        (weakTask.weakest.length ? `薄弱项集中于 ${weakTask.weakest.join('、')}。` : '') +
        '建议通过一个可运行、可复述的完整项目补齐，并在简历中写明对应描述，以便抽取环节获取证据。',
    });
  }

  // ④ 硬性条件
  if (dims.experience < 0.7 || dims.degree < 1) {
    const parts: string[] = [];
    if (dims.experience < 0.7) parts.push(`该岗位在招信息中最常见的经验档位为“${expWant}”，简历为 ${resume.years} 年`);
    if (dims.degree < 1) parts.push(`学历要求集中于“${degWant}”`);
    out.push({
      kind: 'dimension',
      title: '硬性条件存在差距',
      body: `${parts.join('；')}。此类条件短期内难以改变，但在能力结构相似度较高时通常存在放宽空间，优先补齐上述技能点收益更高。`,
    });
  }

  // ⑤ 优势
  const strong = items.filter((i) => i.band === 'have' && i.required > 0.3).slice(0, 3);
  if (strong.length) {
    out.push({
      kind: 'strength',
      title: `已达标的技能点：${strong.map((s) => s.name).join('、')}`,
      body: `上述能力已达到或超过 ${job.name} 的要求，建议在简历中重点展开，以具体任务与量化结果支撑，而非仅罗列技术栈。`,
    });
  }

  return out;
}

/* ==================== 相近岗位 ==================== */

/** 与目标岗位能力结构最接近的若干岗位（按能力要求向量的夹角相似度） */
export function similarJobs(jobId: string, jobIds: string[], top = 4) {
  /* 取直达边而非 jobVector：后者并入了经由任务的两跳，而任务层与能力层
     近乎全连，两个岗位的向量会被共有的通用能力拉到几乎重合（全站相似度
     中位数 0.99），排出来的“相近岗位”与显示的百分数都失去区分力。 */
  const base = jobDirectVector(jobId);
  return jobIds
    .filter((j) => j !== jobId)
    .map((j) => ({ jobId: j, sim: cosine(base, jobDirectVector(j)) }))
    .sort((a, b) => b.sim - a.sim)
    .slice(0, top);
}

/* ==================== 二维投影（能力地形图用） ==================== */

/** 岗位在能力空间的二维投影（经典 MDS） */
export function projectJobs(jobIds: string[]): Map<string, [number, number]> {
  const vecs = jobIds.map((jid) => jobVector(jid));

  const n = jobIds.length;
  const keys = [...new Set(vecs.flatMap((v) => Object.keys(v)))];
  const D: number[][] = Array.from({ length: n }, () => new Array(n).fill(0));
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      let s = 0;
      for (const k of keys) {
        const dd = (vecs[i][k] ?? 0) - (vecs[j][k] ?? 0);
        s += dd * dd;
      }
      D[i][j] = D[j][i] = Math.sqrt(s);
    }
  }

  // 经典 MDS：双中心化 + 幂迭代取前两个特征向量
  const D2 = D.map((r) => r.map((v) => v * v));
  const rowMean = D2.map((r) => r.reduce((a, b) => a + b, 0) / n);
  const grand = rowMean.reduce((a, b) => a + b, 0) / n;
  const B = D2.map((r, i) => r.map((v, j) => -0.5 * (v - rowMean[i] - rowMean[j] + grand)));

  const power = (M: number[][], exclude: number[][] | null) => {
    let v = Array.from({ length: n }, (_, i) => Math.sin(i * 12.9898) % 1 || 0.5);
    let lam = 0;
    for (let it = 0; it < 128; it++) {
      let nv = M.map((row) => row.reduce((a, b, j) => a + b * v[j], 0));
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
    return { v, lambda: lam };
  };

  const e1 = power(B, null);
  const e2 = power(B, [e1.v]);
  const s1 = Math.sqrt(Math.max(e1.lambda, 1e-6));
  const s2 = Math.sqrt(Math.max(e2.lambda, 1e-6));

  const out = new Map<string, [number, number]>();
  jobIds.forEach((jid, i) => out.set(jid, [e1.v[i] * s1, e2.v[i] * s2]));
  return out;
}

/**
 * 简历在同一投影空间中的落点：按与各岗位的相似度做加权重心。
 * 分数由外部传入（页面上已经为排行榜算过一遍，不重复算）。
 */
export function projectResume(
  jobIds: string[],
  coords: Map<string, [number, number]>,
  scoreOf: (jobId: string) => number,
): [number, number] {
  let sx = 0;
  let sy = 0;
  let sw = 0;
  for (const jid of jobIds) {
    const c = coords.get(jid);
    if (!c) continue;
    const w = Math.pow(Math.max(scoreOf(jid), 0), 6);
    sx += c[0] * w;
    sy += c[1] * w;
    sw += w;
  }
  return sw > 0 ? [sx / sw, sy / sw] : [0, 0];
}
