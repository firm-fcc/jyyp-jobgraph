/* ============================================================
   演示链路的报告数据

   报告页只有一套版式。解析服务在线时，各项判定由服务端给出；不在线、或载入的
   是内置示例简历时，同样的判定在此处按同一口径算出，两条链路因而给出同一份
   报告，差别只在数据的来源 —— 此前演示链路另有一套版式与另一组读数，
   同一页在两种情形下长得不一样，读者无从判断哪一处是口径之别、哪一处是实现之别。

   本模块产出的是报告页各块直接消费的视图模型（见 matchLive 的 ReportModel），
   不伪造服务端的接口报文：凡服务端才有的原始计数（某一项能力在该岗位多少条
   招聘信息里被写明熟练度）此处不予补造，相应的展开层照其缺省形态渲染。

   四项判定沿用与服务端一致的语义：

     未覆盖      简历中没有这一项的行为证据
     证据不足    只在技能清单里列了名，没有可核对的经历描述
     等级不足    有行为证据，但熟练度低于该岗位标明的档位
     已满足      有行为证据，且熟练度达到该岗位标明的档位

   岗位一侧的要求取自图谱的岗位定义（jobDef）：覆盖率与平均档位均由招聘信息
   汇总表逐条统计而来，是实测量。学习路径取自 public/data/devgraph.json，
   与服务端所用的是同一批能力发展图谱。
   ============================================================ */

import type {
  ExplicitSkillMention,
  GapType,
  PathMode,
  ProficiencyLevel,
} from '@/api/matchApi';
import type { GraphEdge, GraphNode, ResumeProfile } from '@/types/graph';
import {
  buildEvidenceIndex,
  type GapCounts,
  type LiveCandidateSummary,
  type LivePath,
  type LivePathItem,
  type LiveSkillItem,
  type ReportModel,
} from '@/data/matchLive';
import { buildLiveAdvice } from '@/data/matchLive';

/* ==================== 能力发展图谱 ==================== */

export interface DevGraphNode {
  id: string;
  name: string;
  task: string;
  crit: string[];
  pre: string[];
}

export interface DevGraph {
  name: string;
  nodes: DevGraphNode[];
  verify: { name: string; desc: string; crit: string[] } | null;
  cap: { obj: string; desc: string; crit: string[] } | null;
}

export type DevGraphs = Record<string, DevGraph>;

/* 与服务端 AUXILIARY_TEAM_SKILL_IDS 同一份名单：这六项不参与评级计分，
   服务端不为它们规划路径，此处也不计入达成率的分母。 */
const AUXILIARY = new Set(['F-1-01', 'F-1-03', 'F-1-04', 'F-3-04', 'F-4-01', 'F-4-02']);

/* ==================== 简历一侧 ==================== */

/** 一行原文在全文里的位置。listing 表示这一行出自技能清单一类的罗列小节 */
interface LineSpan {
  start: number;
  end: number;
  text: string;
  listing: boolean;
}

/** 技能清单一类的小节。其中列到的技术名只是写了个名字，不构成对能力的支撑 */
const LISTING_SECTION = /技能|技术栈|工具|证书|荣誉/;

/** 把结构化的示例简历摊成一份带偏移的全文，与服务端返回的 resume_text 同一角色 */
export function demoResumeText(resume: ResumeProfile): {
  text: string;
  spans: Map<string, LineSpan>;
} {
  const spans = new Map<string, LineSpan>();
  const parts: string[] = [];
  let at = 0;
  const push = (s: string, id?: string, listing = false) => {
    if (id) spans.set(id, { start: at, end: at + s.length, text: s, listing });
    parts.push(s);
    at += s.length + 1;
  };
  for (const sec of resume.sections) {
    const listing = LISTING_SECTION.test(sec.title);
    push(sec.title);
    for (const l of sec.lines) push(l.text, l.id, listing);
  }
  return { text: parts.join('\n'), spans };
}

/** 熟练度 0–1 折成四档。与 matchLiveDerived 的 LEVEL_OWN 互为反函数，取各档中值为界 */
const levelOf = (p: number): ProficiencyLevel =>
  p >= 0.92 ? 'P4' : p >= 0.75 ? 'P3' : p >= 0.52 ? 'P2' : 'P1';

/** 简历在某一项能力上的证据与熟练度 */
interface Held {
  /** 加权平均熟练度 0–1 */
  proficiency: number;
  /** 有经历描述支撑的原文行 */
  anchors: string[];
  /** 抽取置信度 */
  confidence: number;
  /** 只在技能清单里列了名，没有经历描述 */
  listOnly: boolean;
  /** 简历原文里的写法 */
  terms: string[];
}

/**
 * 把简历的技能点归并到团队技能这一层。
 *
 * 简历项已落在技能层的直接计入；落在技能点层的沿 S-SP 边反查所属能力，
 * 与 matching.resumeToSkillVector 同式。列名与经历描述分开累计：
 * 只出现在技能清单里的不构成对能力的支撑，仅使该项进入“证据不足”一档。
 */
export function demoHeldSkills(
  resume: ResumeProfile,
  edges: GraphEdge[],
  spans: Map<string, LineSpan>,
): Map<string, Held> {
  const acc = new Map<
    string,
    { num: number; den: number; anchors: string[]; conf: number[]; exp: boolean; terms: string[] }
  >();
  const put = (skillId: string, w: number, sp: ResumeProfile['skillPoints'][number]) => {
    const key = skillId.replace(/^S:/, '');
    let a = acc.get(key);
    if (!a) {
      a = { num: 0, den: 0, anchors: [], conf: [], exp: false, terms: [] };
      acc.set(key, a);
    }
    a.num += sp.proficiency * w;
    a.den += w;
    a.conf.push(sp.confidence);
    if (!a.terms.includes(sp.name)) a.terms.push(sp.name);
    /* 落在技能清单里的那几行不算行为证据：那里只写了技术名，没有承担的任务、
       做法与结果。简历项即便标为经历来源，其锚点也常同时指向清单里的同一个词。 */
    const cited = sp.anchors.filter((id) => {
      const ln = spans.get(id);
      return !!ln && !ln.listing;
    });
    if (sp.from === 'experience' && cited.length > 0) {
      a.exp = true;
      for (const id of cited) if (!a.anchors.includes(id)) a.anchors.push(id);
    }
  };

  for (const sp of resume.skillPoints) {
    if (sp.id.startsWith('S:')) {
      put(sp.id, 1, sp);
      continue;
    }
    for (const e of edges) {
      if (e.kind !== 'S-SP' || e.target !== sp.id) continue;
      put(e.source, Math.max(e.effectiveWeight, 0.05), sp);
    }
  }

  const out = new Map<string, Held>();
  for (const [k, a] of acc) {
    out.set(k, {
      proficiency: a.den > 0 ? a.num / a.den : 0,
      anchors: a.anchors,
      confidence: a.conf.reduce((s, x) => s + x, 0) / Math.max(a.conf.length, 1),
      listOnly: !a.exp,
      terms: a.terms,
    });
  }
  return out;
}

/** 技能清单里列到的写法。只作展示，不构成对能力的支撑 */
function demoMentions(
  resume: ResumeProfile,
  text: string,
  spans: Map<string, LineSpan>,
): ExplicitSkillMention[] {
  const out: ExplicitSkillMention[] = [];
  const used = new Set<number>();
  /* 清单小节的字符区间。同一个名字在正文里也可能出现，只有落在清单内的那一处算列名 */
  const listing = [...spans.values()].filter((v) => v.listing);
  const inListing = (at: number, end: number) =>
    listing.some((v) => at >= v.start && end <= v.end);
  for (const sp of resume.skillPoints) {
    let from = 0;
    let at = text.indexOf(sp.name, from);
    while (at >= 0 && (used.has(at) || !inListing(at, at + sp.name.length))) {
      from = at + 1;
      at = text.indexOf(sp.name, from);
    }
    if (at < 0) continue;
    used.add(at);
    out.push({
      text: sp.name,
      start: at,
      end: at + sp.name.length,
      source: 'skill_list',
      mention_type: 'explicit_skill',
      mapping_status: sp.mappedName ? 'mapped' : 'unmapped',
    });
  }
  return out.sort((a, b) => a.start - b.start);
}

/* ==================== 岗位一侧 ==================== */

/** 岗位定义里的一项能力要求。cov 与 lvl 均由招聘信息汇总表统计而来 */
interface Requirement {
  code: string;
  cov: number;
  level: ProficiencyLevel | null;
}

const LEVEL_BY_RANK: ProficiencyLevel[] = ['P1', 'P2', 'P3', 'P4'];

function demoRequirements(job: GraphNode | undefined): Requirement[] {
  const def = job?.jobDef;
  if (!def) return [];
  const seen = new Map<string, Requirement>();
  for (const el of [...def.must, ...def.plus]) {
    if (!el.code || seen.has(el.code)) continue;
    const rank = typeof el.lvl === 'number' ? Math.min(4, Math.max(1, Math.round(el.lvl))) : 0;
    seen.set(el.code, {
      code: el.code,
      cov: typeof el.cov === 'number' ? el.cov : (el.w ?? 0),
      level: rank > 0 ? LEVEL_BY_RANK[rank - 1] : null,
    });
  }
  return [...seen.values()];
}

/* ==================== 逐项判定 ==================== */

const RANK: Record<ProficiencyLevel, number> = { P1: 1, P2: 2, P3: 3, P4: 4, U: 0 };

const ATTAIN: Record<GapType, number> = {
  SATISFIED: 1,
  LEVEL_GAP: 0.5,
  EVIDENCE_INSUFFICIENT: 0.5,
  MISSING: 0,
};

const MODE_OF: Record<GapType, PathMode> = {
  SATISFIED: 'NONE',
  LEVEL_GAP: 'DEEPEN',
  EVIDENCE_INSUFFICIENT: 'VERIFY_FIRST',
  MISSING: 'LEARN',
};

const GAP_ORDER: Record<GapType, number> = {
  MISSING: 0,
  LEVEL_GAP: 1,
  EVIDENCE_INSUFFICIENT: 2,
  SATISFIED: 3,
};

function judge(req: Requirement, held: Held | undefined): {
  gap: GapType;
  candidate: ProficiencyLevel | null;
} {
  if (!held) return { gap: 'MISSING', candidate: null };
  if (held.listOnly) return { gap: 'EVIDENCE_INSUFFICIENT', candidate: 'U' };
  const candidate = levelOf(held.proficiency);
  if (!req.level) return { gap: 'SATISFIED', candidate };
  return RANK[candidate] >= RANK[req.level]
    ? { gap: 'SATISFIED', candidate }
    : { gap: 'LEVEL_GAP', candidate };
}

/* ==================== 学习路径 ==================== */

/** 按先修关系排序。图谱内的次序本已合法，此处只在其上做一次稳定的拓扑排序 */
function ordered(nodes: DevGraphNode[]): DevGraphNode[] {
  const at = new Map(nodes.map((n, i) => [n.id, i]));
  const left = new Map(nodes.map((n) => [n.id, n.pre.filter((p) => at.has(p)).length]));
  const out: DevGraphNode[] = [];
  const ready = nodes.filter((n) => (left.get(n.id) ?? 0) === 0);
  while (ready.length > 0) {
    ready.sort((a, b) => (at.get(a.id) ?? 0) - (at.get(b.id) ?? 0));
    const n = ready.shift() as DevGraphNode;
    out.push(n);
    for (const m of nodes) {
      if (!m.pre.includes(n.id)) continue;
      const k = (left.get(m.id) ?? 0) - 1;
      left.set(m.id, k);
      if (k === 0) ready.push(m);
    }
  }
  return out.length === nodes.length ? out : nodes;
}

function demoPath(items: LiveSkillItem[], graphs: DevGraphs | null): LivePath | null {
  if (!graphs) return null;
  const ready: LivePathItem[] = [];
  for (const it of items) {
    if (it.gap === 'SATISFIED') continue;
    const g = graphs[it.teamSkillId];
    if (!g) continue;
    const verifyFirst = it.gap === 'EVIDENCE_INSUFFICIENT';
    ready.push({
      teamSkillId: it.teamSkillId,
      name: it.name,
      gap: it.gap,
      pathMode: it.pathMode,
      requiredLevel: it.requiredLevel,
      observedLevel: it.candidateLevel,
      currentState: '',
      developmentGoal: '',
      steps:
        verifyFirst || !g.nodes.length
          ? []
          : ordered(g.nodes).map((n) => ({
              nodeId: n.id,
              nodeName: n.name,
              evidenceTask: n.task,
              criteria: n.crit,
            })),
      capstone:
        verifyFirst || !g.cap
          ? null
          : { objective: g.cap.obj, description: g.cap.desc, criteria: g.cap.crit },
      verification:
        verifyFirst && g.verify
          ? { name: g.verify.name, description: g.verify.desc, criteria: g.verify.crit }
          : null,
      status: 'READY',
      reassessmentGuidance: '',
    });
  }
  return {
    ready,
    unavailable: [],
    noAction: [],
    curatedGraphCount: Object.keys(graphs).length,
    pathStatus: 'READY',
  };
}

/* ==================== 装配 ==================== */

/**
 * 由内置示例简历与图谱的岗位定义算出一份报告。
 *
 * skillNodes 的键同时收了 `S:T-SW-01` 与 `T-SW-01` 两种写法，见 Match 的同名索引。
 */
export function buildDemoReport(
  resume: ResumeProfile,
  jobNode: GraphNode | undefined,
  edges: GraphEdge[],
  skillNodes: Map<string, GraphNode>,
  graphs: DevGraphs | null,
): ReportModel {
  const { text, spans } = demoResumeText(resume);
  const held = demoHeldSkills(resume, edges, spans);
  const reqs = demoRequirements(jobNode).filter((r) => !AUXILIARY.has(r.code));

  const items: LiveSkillItem[] = reqs.map((r) => {
    const h = held.get(r.code);
    const { gap, candidate } = judge(r, h);
    const node = skillNodes.get(r.code);
    return {
      teamSkillId: r.code,
      name: node?.name ?? r.code,
      backendName: node?.name ?? r.code,
      definition: node?.definition,
      dimension: node?.category,
      hard: node?.skillType !== 'soft',
      requiredLevel: r.level,
      candidateLevel: candidate,
      gap,
      pathMode: MODE_OF[gap],
      evidence: (h?.anchors ?? [])
        .map((id) => spans.get(id))
        .filter((s): s is LineSpan => !!s)
        .map((s) => ({ text: s.text, sourceId: 'resume', start: s.start, end: s.end })),
      requirementRefs: [],
      explanation: '',
      weight: r.cov,
      forwardLooking: false,
      demand: r.cov,
      /* 岗位一侧“写明了熟练度的那一截”只有服务端的逐条计数才数得出来，
         此处不予推测，故内条按整条计，深浅两截不再分列。 */
      demandGraded: 0,
      attain: gap === 'LEVEL_GAP' && r.level && candidate
        ? Math.min(1, RANK[candidate] / RANK[r.level])
        : ATTAIN[gap],
    };
  });

  items.sort((a, b) => GAP_ORDER[a.gap] - GAP_ORDER[b.gap] || b.weight - a.weight);

  const counts: GapCounts = {
    required_skills: items.length,
    satisfied: items.filter((i) => i.gap === 'SATISFIED').length,
    level_gap: items.filter((i) => i.gap === 'LEVEL_GAP').length,
    evidence_insufficient: items.filter((i) => i.gap === 'EVIDENCE_INSUFFICIENT').length,
    missing: items.filter((i) => i.gap === 'MISSING').length,
  };

  /* 简历一侧的能力持有度，键与图谱的技能节点 id 一致。
     地形、相近岗位与岗位选择器三处共用它，故与实测链路取同一根轴。 */
  const own: Record<string, number> = {};
  for (const [code, h] of held) {
    const key = skillNodes.get(code)?.id ?? `S:${code}`;
    own[key] = h.listOnly ? 0.4 : Math.max(0.4, h.proficiency);
  }

  const supported = [...held.values()].filter((h) => !h.listOnly).length;
  const partial = [...held.values()].filter((h) => h.listOnly).length;
  const confs = [...held.values()].map((h) => h.confidence);

  const summary: LiveCandidateSummary = {
    candidateId: resume.name,
    registryVersion: '0.4',
    supported,
    partial,
    unsupported: Math.max(0, items.length - supported - partial),
    evidenceCount: items.reduce((s, i) => s + i.evidence.length, 0),
    avgConfidence: confs.length ? confs.reduce((s, x) => s + x, 0) / confs.length : null,
  };

  return {
    items,
    counts,
    score: counts.required_skills > 0 ? (counts.satisfied / counts.required_skills) * 100 : 0,
    advice: buildLiveAdvice(counts, items),
    path: demoPath(items, graphs),
    own,
    resumeText: text,
    mentions: demoMentions(resume, text, spans),
    evidence: buildEvidenceIndex(items),
    summary,
  };
}
