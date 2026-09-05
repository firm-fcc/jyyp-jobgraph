/* ============================================================
   人岗匹配后端结果的适配层

   后端与本站的四层图谱共用同一套技能身份（team_skill_id），因此后端逐项返回的
   能力要求可以直接挂回图谱的技能节点，取到本站的规范名、定义与所属维度。
   后端 49 项技能全部落在图谱的技能层之内，显示名有二十处措辞差异，
   一律以图谱名为准 —— 界面术语在全站已经固定，不因取数来源而变。

   两侧的分值口径不同，不可混用，也不可换算：

     后端  岗位要求达成率 = 已验证满足的要求 / 可计分的要求
           非对称，简历多出来的能力不计分也不扣分；不使用相似度与加权和
     本站  综合匹配度     = 五个维度的加权和，其中能力结构一维用的是夹角相似度

   因此接入后端之后，报告页首屏的分值改用后端口径，五维加权一路不再作为
   正式结论呈现。解析服务不在线、或载入的是内置示例简历时，同一组视图模型
   改由 demoLive 按上面这条达成率口径算出，版式与各块含义均不变。
   ============================================================ */

import type {
  ExplicitSkillMention,
  GapType,
  LearningPathApiResponse,
  MatchResultV1,
  MatchSkillItem,
  PathMode,
  ProficiencyLevel,
  SkillLearningPath,
  TargetJobProfile,
} from '@/api/matchApi';
import type { GraphNode } from '@/types/graph';

/* ==================== 口径文案 ==================== */

/** 四种差距状态的中文名。措辞与后端契约一一对应，不作意译 */
export const GAP_TEXT: Record<GapType, string> = {
  SATISFIED: '已满足',
  LEVEL_GAP: '等级不足',
  EVIDENCE_INSUFFICIENT: '证据不足',
  MISSING: '未覆盖',
};

/** 四种状态的说明。差距状态是后端的判定结果，界面要把判据一并交代 */
export const GAP_HINT: Record<GapType, string> = {
  SATISFIED: '简历中有可核验的行为证据，且熟练度达到该项要求的等级。',
  LEVEL_GAP: '简历中有可核验的行为证据，但熟练度低于该项要求的等级。',
  EVIDENCE_INSUFFICIENT: '简历中提及该项能力，但证据不足以判定其熟练度等级。',
  MISSING: '简历中未发现支持该项能力的行为证据。仅在技能清单中列名不计入。',
};

export const PATH_MODE_TEXT: Record<PathMode, string> = {
  LEARN: '从头习得',
  DEEPEN: '深化提升',
  VERIFY_FIRST: '先行验证',
  NONE: '无需行动',
};

/* 熟练度四档。U 是"能力存在但证据不足以定级"，与 P1 不是一回事。

   只留中文一档：P1–P4 是服务端的编码，读者据以行动的是"了解／应用／熟练／精通"
   这四个词，编码放在旁边只会让人以为还有一层要读。 */
export const LEVEL_TEXT: Record<ProficiencyLevel, string> = {
  P1: '了解',
  P2: '应用',
  P3: '熟练',
  P4: '精通',
  U: '无法定级',
};

export const levelLabel = (v: ProficiencyLevel | null) => (v ? LEVEL_TEXT[v] : '未标明等级');

/** 岗位一侧未标等级时记 LEVEL_UNSPECIFIED，它不等于 P1 —— 这一条后端反复申明 */
export const requiredLabel = (v: ProficiencyLevel | null) => (v ? LEVEL_TEXT[v] : '未标明等级');

/* ==================== 报告的取数模型 ====================

   报告页只有一套版式，两条链路各自填同一组视图模型：解析服务在线时由
   服务端的逐项判定转出（本文件的 buildLiveItems 等），不在线或载入的是内置
   示例简历时由 demoLive 按同一口径算出。两者此后走的是同一条渲染路径。 */

/** 四态判定的计数。达成率即 satisfied / required_skills */
export interface GapCounts {
  required_skills: number;
  satisfied: number;
  level_gap: number;
  evidence_insufficient: number;
  missing: number;
}

/** 报告页各块直接消费的一份数据 */
export interface ReportModel {
  items: LiveSkillItem[];
  counts: GapCounts;
  /** 岗位要求达成率 0–100 */
  score: number;
  advice: LiveAdvice[];
  path: LivePath | null;
  /** 简历一侧的能力持有度，键为图谱的技能节点 id */
  own: Record<string, number>;
  resumeText: string;
  mentions: ExplicitSkillMention[];
  evidence: LiveEvidence[];
  summary: LiveCandidateSummary | null;
}

/* ==================== 差距明细 ==================== */

/** 一项能力要求，已挂回图谱节点 */
export interface LiveSkillItem {
  teamSkillId: string;
  /** 图谱里的规范名；图谱中没有该项时退回后端给的名字 */
  name: string;
  /** 后端给的名字，与图谱名不一致时在界面上并列注明 */
  backendName: string;
  definition?: string;
  /** 图谱里的技能所属维度，如“人工智能与数据”。图谱中没有该项时为空 */
  dimension?: string;
  hard: boolean;
  requiredLevel: ProficiencyLevel | null;
  candidateLevel: ProficiencyLevel | null;
  gap: GapType;
  pathMode: PathMode;
  /** 简历一侧支撑该项的原文片段 */
  evidence: { text: string; sourceId: string; start: number | null; end: number | null }[];
  /** 岗位一侧该项要求的出处 */
  requirementRefs: string[];
  explanation: string;
  /** 岗位在本窗口对该项能力的合成权重，取自 target job profile 的市场信号 */
  weight: number;
  forwardLooking: boolean;
  /** 这一项要求的原始计数。岗位级基准下有值，单条招聘信息为基准时为空 */
  stats?: RequirementStats;
  /**
   * 这一项要求有多重：该能力在本岗位多大比例的招聘信息中被要求，0–1。
   * 取自后端的逐条计数，不是折算量。取不到计数时退回市场信号的相对强度。
   */
  demand: number;
  /**
   * demand 里"写明了熟练度"的那一截。要求写明了档位的，比只提一句名称的更硬。
   * 两截同出于同一批条目，故可直接相加得 demand。
   */
  demandGraded: number;
  /**
   * 简历在这一项上够到了多少，0–1。按服务端的四态判定取值，不引入新的算术：
   * 已满足记满；等级不足按两侧档位在四档轴上的序数比；证据不足记半
   * （能力存在而证据不足以定级，与 U 的语义一致）；未覆盖记零。
   */
  attain: number;
}

/** 岗位一侧某项要求的原始计数，见 aggregated_target_job_service */
export interface RequirementStats {
  jdCount: number;
  presenceCount: number;
  presenceRate: number;
  gradedCount: number;
  gradedRatio: number;
  levelDistribution: Record<string, number>;
}

/** 四档在序数轴上的位置。等级不足一态按两侧序数比折算达成程度 */
const LEVEL_RANK: Record<ProficiencyLevel, number> = { P1: 1, P2: 2, P3: 3, P4: 4, U: 0 };

/** 简历在某一项上够到了多少。四态各有定值，不额外引入算术 */
function attainOf(
  gap: GapType,
  required: ProficiencyLevel | null,
  candidate: ProficiencyLevel | null,
): number {
  if (gap === 'SATISFIED') return 1;
  if (gap === 'MISSING') return 0;
  if (gap === 'EVIDENCE_INSUFFICIENT') return 0.5;
  /* 等级不足：两侧都在四档轴上，取序数比。要求档缺失时退回记半 */
  const r = required ? LEVEL_RANK[required] : 0;
  const c = candidate ? LEVEL_RANK[candidate] : 0;
  return r > 0 ? Math.min(1, c / r) : 0.5;
}

/** 排序：先按差距严重程度，同档内按岗位侧权重 */
const GAP_ORDER: Record<GapType, number> = {
  MISSING: 0,
  LEVEL_GAP: 1,
  EVIDENCE_INSUFFICIENT: 2,
  SATISFIED: 3,
};

export function buildLiveItems(
  match: MatchResultV1,
  target: TargetJobProfile,
  skillNodes: Map<string, GraphNode>,
): LiveSkillItem[] {
  const bySkill = new Map(target.skills.map((s) => [s.team_skill_id, s]));
  const items = match.skills.map((s: MatchSkillItem): LiveSkillItem => {
    const node = skillNodes.get(s.team_skill_id);
    const t = bySkill.get(s.team_skill_id);
    const rs = t?.requirement_statistics;
    /* 单条招聘信息为基准时没有逐条计数，退回市场信号的相对强度，
       它与提及率不同源但同为 0–1，只用作条形的相对长度 */
    const demand = rs ? rs.jd_presence_rate : Math.min(1, t?.market_signal?.effective_weight ?? 0);
    return {
      teamSkillId: s.team_skill_id,
      name: node?.name ?? s.team_skill_name,
      backendName: s.team_skill_name,
      definition: node?.definition,
      dimension: node?.category,
      hard: t?.skill_type !== 'soft',
      requiredLevel: s.required_level,
      candidateLevel: s.candidate_level,
      gap: s.gap_type,
      pathMode: s.path_mode,
      evidence: (s.candidate_evidence ?? []).map((e) => ({
        text: String(e.text ?? ''),
        sourceId: String(e.source_id ?? ''),
        start: e.start ?? null,
        end: e.end ?? null,
      })),
      requirementRefs: s.requirement_evidence ?? [],
      explanation: s.explanation,
      weight: t?.market_signal?.effective_weight ?? 0,
      forwardLooking: (t?.market_signal?.delta_weight ?? 0) > 0,
      stats: rs
        ? {
            jdCount: rs.jd_count,
            presenceCount: rs.jd_presence_count,
            presenceRate: rs.jd_presence_rate,
            gradedCount: rs.graded_posting_count,
            gradedRatio: rs.graded_ratio,
            levelDistribution: rs.level_distribution,
          }
        : undefined,
      demand,
      demandGraded: rs ? demand * rs.graded_ratio : demand,
      attain: attainOf(s.gap_type, s.required_level, s.candidate_level),
    };
  });
  return items.sort((a, b) => GAP_ORDER[a.gap] - GAP_ORDER[b.gap] || b.weight - a.weight);
}

/** 岗位一侧被排除在计分之外的要求。排除的理由要能说清，否则界面上就是凭空少了几项 */
export interface ExcludedRequirement {
  teamSkillId: string;
  name: string;
  reason: string;
}

const EXCLUDE_REASON: Record<string, string> = {
  AUXILIARY_NOT_GRADED: '辅助能力，本版不参与评级计分',
  PROFICIENCY_NOT_AVAILABLE: '该岗位未给出可比对的熟练度要求',
};

export function excludedRequirements(
  target: TargetJobProfile,
  skillNodes: Map<string, GraphNode>,
): ExcludedRequirement[] {
  return target.skills
    .filter((s) => s.requirement_status in EXCLUDE_REASON)
    .map((s) => ({
      teamSkillId: s.team_skill_id,
      name: skillNodes.get(s.team_skill_id)?.name ?? s.team_skill_name,
      reason: EXCLUDE_REASON[s.requirement_status],
    }));
}

/* ==================== 诊断结论 ==================== */

export interface LiveAdvice {
  kind: 'gap' | 'forward' | 'dimension' | 'strength';
  title: string;
  body: string;
}

/**
 * 由后端逐项判定归纳出的结论。此处只做归并与措辞，不引入任何新的算术：
 * 每一句话都能在上方的差距明细里逐条对上。
 */
export function buildLiveAdvice(counts: GapCounts, items: LiveSkillItem[]): LiveAdvice[] {
  const out: LiveAdvice[] = [];
  const { satisfied, missing, level_gap, evidence_insufficient, required_skills } = counts;
  /* 标题只列前几项，正文给的是该类的总数。两数不等时标题末尾补一个“等”，
     否则读者会在标题里数出三项、在正文里读到四项，以为漏掉了一项。 */
  const nameOf = (list: LiveSkillItem[], total = list.length) =>
    list.map((i) => i.name).join('、') + (total > list.length ? ' 等' : '');

  const missingItems = items.filter((i) => i.gap === 'MISSING');
  const gapItems = items.filter((i) => i.gap === 'LEVEL_GAP');
  const uncertainItems = items.filter((i) => i.gap === 'EVIDENCE_INSUFFICIENT');
  const okItems = items.filter((i) => i.gap === 'SATISFIED');

  out.push({
    kind: satisfied * 2 >= required_skills ? 'strength' : 'gap',
    title: `该岗位可计分的 ${required_skills} 项能力要求中，已满足 ${satisfied} 项`,
    body:
      `其余 ${required_skills - satisfied} 项分别为：未覆盖 ${missing} 项、等级不足 ${level_gap} 项、` +
      `证据不足 ${evidence_insufficient} 项。达成率按满足项数占可计分项数计算，` +
      `简历中超出该岗位要求的能力既不计分也不扣分。`,
  });

  if (missingItems.length > 0) {
    const top = missingItems.slice(0, 3);
    out.push({
      kind: 'gap',
      title: `优先补齐：${nameOf(top, missingItems.length)}`,
      body:
        `这 ${missingItems.length} 项在简历中未发现可核验的行为证据。` +
        `技能清单中列名、或课程与证书中提及，均不足以构成支撑；` +
        `需要在项目经历中写明承担的具体任务、采取的做法与可核对的结果。`,
    });
  }

  if (gapItems.length > 0) {
    out.push({
      kind: 'dimension',
      title: `等级不足：${nameOf(gapItems.slice(0, 3), gapItems.length)}`,
      body:
        `这 ${gapItems.length} 项已有行为证据，但熟练度低于岗位标明的等级。` +
        `相较从头习得，补足等级所需的投入更小，宜优先安排。`,
    });
  }

  if (uncertainItems.length > 0) {
    out.push({
      kind: 'forward',
      title: `证据不足以定级：${nameOf(uncertainItems.slice(0, 3), uncertainItems.length)}`,
      body:
        `这 ${uncertainItems.length} 项在简历中有所提及，但描述不足以判定熟练度等级，` +
        `因而未计入已满足。补充这几项经历中的任务范围、技术选择与结果指标，` +
        `即可使其进入可比对的状态。`,
    });
  }

  if (okItems.length > 0) {
    out.push({
      kind: 'strength',
      title: `已满足：${nameOf(okItems.slice(0, 4), okItems.length)}`,
      body: `这 ${okItems.length} 项的行为证据与熟练度均达到岗位要求，可在投递材料中置于前列。`,
    });
  }

  return out;
}

/* ==================== 学习路径 ==================== */

export interface LivePathItem {
  teamSkillId: string;
  name: string;
  gap: GapType;
  pathMode: PathMode;
  requiredLevel: ProficiencyLevel | null;
  observedLevel: ProficiencyLevel | null;
  currentState: string;
  developmentGoal: string;
  steps: { nodeId: string; nodeName: string; evidenceTask: string; criteria: string[] }[];
  capstone: { objective: string; description: string; criteria: string[] } | null;
  /** VERIFY_FIRST 模式下没有学习步骤，取而代之的是一项验证任务 */
  verification: { name: string; description: string; criteria: string[] } | null;
  /** READY 表示可渲染；GRAPH_UNAVAILABLE 表示该技能尚无 curated 图谱 */
  status: string;
  reassessmentGuidance: string;
}

export interface LivePath {
  /** 有 curated 图谱、给出了具体步骤的那些 */
  ready: LivePathItem[];
  /** 有缺口但尚无 curated 图谱的那些 —— 不能凭空造路径，只如实列出 */
  unavailable: LivePathItem[];
  /** 已满足、无需行动的那些 */
  noAction: LivePathItem[];
  curatedGraphCount: number;
  pathStatus: string;
}

const toPathItem = (p: SkillLearningPath, skillNodes: Map<string, GraphNode>): LivePathItem => ({
  teamSkillId: p.team_skill_id,
  /* 无 curated 图谱时后端把 team_skill_name 退化成了 id，此处一律以图谱名为准 */
  name: skillNodes.get(p.team_skill_id)?.name ?? p.team_skill_name,
  gap: p.gap_type,
  pathMode: p.path_mode,
  requiredLevel: p.required_level,
  observedLevel: p.observed_level,
  currentState: p.current_state,
  developmentGoal: p.development_goal,
  steps: (p.learning_steps ?? []).map((s) => ({
    nodeId: s.node_id,
    nodeName: s.node_name,
    evidenceTask: s.evidence_task,
    criteria: s.validation_criteria ?? [],
  })),
  capstone: p.capstone_guidance
    ? {
        objective: p.capstone_guidance.objective,
        description: p.capstone_guidance.task_description,
        criteria: p.capstone_guidance.validation_criteria ?? [],
      }
    : null,
  verification: p.verification_guidance
    ? {
        name: p.verification_guidance.task_name,
        description: p.verification_guidance.task_description,
        criteria: p.verification_guidance.validation_criteria ?? [],
      }
    : null,
  status: p.path_status,
  reassessmentGuidance: p.reassessment_guidance,
});

/**
 * 分档按 path_status，不按有没有学习步骤。
 * VERIFY_FIRST 模式（能力已具备、证据不足以定级）同样是 READY，
 * 但它给的是一段验证指引而非分步骤的路径；若按步骤数分档，
 * 这一档会被误归到“尚无发展图谱”，而该技能其实是有图谱的。
 */
export function buildLivePath(
  res: LearningPathApiResponse,
  skillNodes: Map<string, GraphNode>,
): LivePath {
  const all = (res.rendered?.skill_paths ?? []).map((p) => toPathItem(p, skillNodes));
  return {
    ready: all.filter((p) => p.status === 'READY'),
    unavailable: all.filter((p) => p.status === 'GRAPH_UNAVAILABLE'),
    noAction: all.filter((p) => p.status !== 'READY' && p.status !== 'GRAPH_UNAVAILABLE'),
    curatedGraphCount: Number(res.diagnostics?.curated_graph_count ?? 0),
    pathStatus: res.path_status,
  };
}

/* ==================== 简历一侧 ==================== */

/** 一段被引用的原文片段。后端不返回简历全文，能落到原文上的只有这些片段 */
export interface LiveEvidence {
  id: string;
  text: string;
  sourceId: string;
  start: number | null;
  end: number | null;
  /** 引用了这一片段的能力名 */
  skills: string[];
}

/**
 * 把逐项能力下的证据倒排成“按原文片段归并”的一份清单，供报告页左栏渲染。
 * 同一段话常被多项能力同时引用，按 sourceId 与偏移去重后合并引用方。
 */
export function buildEvidenceIndex(items: LiveSkillItem[]): LiveEvidence[] {
  const bag = new Map<string, LiveEvidence>();
  for (const item of items) {
    for (const e of item.evidence) {
      if (!e.text.trim()) continue;
      const key = `${e.sourceId}:${e.start ?? '-'}:${e.end ?? '-'}:${e.text}`;
      const hit = bag.get(key);
      if (hit) {
        if (!hit.skills.includes(item.name)) hit.skills.push(item.name);
        continue;
      }
      bag.set(key, {
        id: key,
        text: e.text,
        sourceId: e.sourceId,
        start: e.start,
        end: e.end,
        skills: [item.name],
      });
    }
  }
  /* 同一段经历内按原文出现顺序排，经历之间按首次出现的顺序排 */
  const order = new Map<string, number>();
  for (const ev of bag.values()) if (!order.has(ev.sourceId)) order.set(ev.sourceId, order.size);
  return [...bag.values()].sort(
    (a, b) =>
      (order.get(a.sourceId) ?? 0) - (order.get(b.sourceId) ?? 0) ||
      (a.start ?? 0) - (b.start ?? 0),
  );
}

/** 简历一侧的抽取概况，取自 candidate_skill_profile 的判定分布 */
export interface LiveCandidateSummary {
  candidateId: string;
  registryVersion: string;
  supported: number;
  partial: number;
  unsupported: number;
  evidenceCount: number;
  /** 有置信度的那部分的均值。后端允许 confidence 为空 */
  avgConfidence: number | null;
}

export function summarizeCandidate(profile: {
  candidate_id: string;
  skill_registry_version: string;
  assessments: { status: string; evidence: unknown[]; confidence: number | null }[];
}): LiveCandidateSummary {
  const a = profile.assessments ?? [];
  const conf = a.map((x) => x.confidence).filter((x): x is number => typeof x === 'number');
  return {
    candidateId: profile.candidate_id,
    registryVersion: profile.skill_registry_version,
    supported: a.filter((x) => x.status === 'supported').length,
    partial: a.filter((x) => x.status === 'partially_supported').length,
    unsupported: a.filter((x) => x.status === 'unsupported').length,
    evidenceCount: a.reduce((s, x) => s + (x.evidence?.length ?? 0), 0),
    avgConfidence: conf.length ? conf.reduce((s, x) => s + x, 0) / conf.length : null,
  };
}
