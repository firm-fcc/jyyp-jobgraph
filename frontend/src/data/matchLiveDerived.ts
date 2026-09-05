/* ============================================================
   实测链路的派生量 —— 后端逐项判定 × 图谱结构

   后端一次只对一条招聘信息计算，且只回答一件事：这一项能力要求达成了没有。
   而报告里另有四件事要答：

     · 岗位的核心任务，这份简历能接下几成
     · 这份简历在全部岗位构成的能力空间里落在哪
     · 相近岗位各覆盖多少
     · 每一段经历分别支撑了哪些能力，以及简历本身有没有对不上的地方

   四者都不在后端的口径之内，但所需的两样东西都已在手：一是后端逐项的能力
   判定与熟练度分档，二是图谱的岗位—任务—能力权重（三类边在本批数据下均为
   实测，见 provenance.ts）。本文件只做这两者的合成。

   此前这四块取的是本站内置的示例简历，与上传件无关，故各挂一枚演示数据标；
   现改由上述两样实测输入算出，标随之撤去。合成中唯一的约定是熟练度四档到
   0–1 强度轴的对应（见 LEVEL_OWN），该对应固定且在界面上写明，
   不引入任何词表、示例简历或按名称哈希铺开的值。

   口径与页首那枚读数不同源，也不可换算：

     页首  岗位要求达成率 = 已验证满足的要求 / 可计分的要求，由服务端按单条
           招聘信息逐项判定，非对称，超出岗位要求的能力不计分
     此处  能力覆盖       = 按图谱的岗位要求权重加权的达成率，对全部岗位统一
           计算，用于排序与定位，不作为对某个岗位的正式结论

   两者在界面上分列两处、各自标名，不并列显示。
   ============================================================ */

import type { CandidateSkillProfile, JobSummaryResponse, ProficiencyLevel } from '@/api/matchApi';
import type { GraphNode } from '@/types/graph';
import { edgesFrom, getDataset } from './generator';
import { jobRawSource } from './realGraph';
import { jobDirectVector, jobVector } from './matching';
import type { LiveSkillItem } from './matchLive';

/* ==================== 一、能力持有度向量 ==================== */

/**
 * 熟练度四档在 0–1 强度轴上的落点。
 *
 * 图谱一侧的岗位要求是一个连续强度（归一到该岗位最重的一项为 1），
 * 简历一侧的判定是四个档位，两者要比对就必须先落到同一根轴上。
 * 四档按等距略偏上取值：P1 是“了解”，不足以承担；P4 是“精通”，记满。
 * U 为“能力存在但证据不足以定级”，记在 P1 与 P2 之间，
 * 与 partially_supported 的处理一致 —— 两者都是证据不足，不是能力缺失。
 */
const LEVEL_OWN: Record<ProficiencyLevel, number> = {
  P1: 0.4,
  P2: 0.65,
  P3: 0.85,
  P4: 1,
  U: 0.5,
};

/** 未定级时按判定状态取值。已具备而未定级的按 P2 与 P3 之间记 */
const STATUS_OWN: Record<string, number> = {
  supported: 0.75,
  partially_supported: 0.4,
  unsupported: 0,
};

/**
 * 简历一侧的能力持有度，键为图谱的技能节点 id。
 *
 * 取自后端的 candidate_skill_profile 与本次比对得到的熟练度分档：
 * 前者覆盖 49 项团队技能的判定状态与证据，后者只覆盖岗位要求且简历已支持的
 * 那几项。两者都在时以档位为准，档位缺失时退到判定状态。
 */
export function liveSkillVector(
  profile: CandidateSkillProfile,
  levels: Record<string, ProficiencyLevel> | undefined,
  skillNodes: Map<string, GraphNode>,
): Record<string, number> {
  const out: Record<string, number> = {};
  for (const a of profile.assessments ?? []) {
    const node = skillNodes.get(a.team_skill_id);
    const key = node?.id ?? `S:${a.team_skill_id}`;
    const level = levels?.[a.team_skill_id];
    const v = level ? LEVEL_OWN[level] : (STATUS_OWN[a.status] ?? 0);
    /* 同一项技能只会出现一次；真出现两次时取高的那次，不叠加 */
    out[key] = Math.max(out[key] ?? 0, v);
  }
  return out;
}

/** 达成率：持有度相对该岗位对这一项的要求强度，封顶 1。与 matching.fitAgainst 同式 */
const attainOf = (req: number, own: number) => (req <= 0.02 ? 1 : Math.min(1, own / req));

/** 与 fitAgainst 的 SOLID 同源：达成 85% 即视为站稳，不再计入短板 */
const SOLID = 0.85;

/* ==================== 二、对某个岗位的能力覆盖 ==================== */

/**
 * 一个岗位要哪些能力、各要到什么份上。键为图谱的技能节点 id，值归一到最大项为 1。
 *
 * 两个来源，优先取前者：
 *
 *   服务端  /api/job-summary 按窗口内该岗位全部招聘信息汇总出的提及率
 *           （算法工程师一岗 234 条），区间完整，岗位之间分得开
 *   图谱    直达的岗位—能力边。本窗口内没有该岗位样本时（后端回 404，
 *           本批 131 个岗位中有 34 个）退回这一支
 *
 * 两者都是实测，差别在粒度：前者逐条招聘信息数出来，后者是图谱构建时的归一化
 * 权重。同一份报告内不混用 —— 每个岗位各自取到哪一支就用哪一支，且都归一化到
 * 同一根轴上，故排序仍可比。
 */
export type ReqVecOf = (jobId: string) => Record<string, number>;

/** 后端汇总转成要求向量。键由 team_skill_id 加图谱的 S: 前缀而来 */
export function summaryReqVec(sum: JobSummaryResponse): Record<string, number> {
  const o: Record<string, number> = {};
  let max = 1e-6;
  for (const sk of sum.skills) {
    const v = sk.jd_presence_rate;
    if (v <= 0) continue;
    o[`S:${sk.team_skill_id}`] = v;
    if (v > max) max = v;
  }
  for (const k of Object.keys(o)) o[k] /= max;
  return o;
}

/**
 * 按后端汇总取要求向量，取不到的岗位退回图谱直达边。
 *
 * summaries 的键是后端的岗位编码（AID-01），图谱一侧带 J: 前缀（J:AID-01），
 * 此处剥去前缀后查表。
 */
export function makeReqVecOf(summaries: Map<string, JobSummaryResponse | null>): ReqVecOf {
  const cache = new Map<string, Record<string, number>>();
  return (jobId: string) => {
    const hit = cache.get(jobId);
    if (hit) return hit;
    const code = jobId.replace(/^J:/, '');
    const sum = summaries.get(code);
    let v = sum ? summaryReqVec(sum) : {};
    if (Object.keys(v).length === 0) {
      /* 萌芽岗位可能连直达边也没有（本批 105 个岗位中有一个），再退一层到两跳，
         否则它们会一律显示为零覆盖，看上去像是算错了而不是数据未及。 */
      const direct = jobDirectVector(jobId);
      v = Object.keys(direct).length > 0 ? direct : jobVector(jobId);
    }
    cache.set(jobId, v);
    return v;
  };
}

/** 只按图谱取要求向量。后端不可达、或调用方尚未取到汇总时的形态 */
export const graphReqVecOf: ReqVecOf = (jobId) => {
  const direct = jobDirectVector(jobId);
  return Object.keys(direct).length > 0 ? direct : jobVector(jobId);
};

/** 两个岗位的能力要求有多像。夹角相似度，与相近岗位一列同源 */
export function reqSimilarity(a: Record<string, number>, b: Record<string, number>): number {
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

/** 与目标岗位能力要求最接近的几个岗位 */
export function liveSimilarJobs(jobId: string, jobIds: string[], reqOf: ReqVecOf, top = 4) {
  const base = reqOf(jobId);
  return jobIds
    .filter((j) => j !== jobId)
    .map((j) => ({ jobId: j, sim: reqSimilarity(base, reqOf(j)) }))
    .sort((a, b) => b.sim - a.sim)
    .slice(0, top);
}


/**
 * 本简历对某岗位能力要求的覆盖度 0–1：按岗位侧的要求权重加权的达成率。
 *
 * 用于岗位选择器、能力地形与相近岗位三处的排序与定位。它与页首那枚由服务端
 * 给出的达成率不同源：后者按单条招聘信息逐项判定，此处按窗口内该岗位全部
 * 招聘信息汇总出的要求权重统一计算，对全部岗位可比、可排序。
 *
 * 要求权重不取图谱那份并入了两跳的向量：两跳在本批数据上占权重总量的九成五，
 * 而任务层与能力层近乎全连，各岗位因此被共有的通用能力拉平 —— 同一份简历对
 * 全部岗位算下来只落在三到五个百分点之间，且顺序失真，一份算法简历对
 * “UI设计师”的覆盖会高过“网络工程师”。
 */
export function liveJobCoverage(
  jobId: string,
  own: Record<string, number>,
  reqOf: ReqVecOf = graphReqVecOf,
): number {
  const jv = reqOf(jobId);
  let num = 0;
  let den = 0;
  for (const [sid, req] of Object.entries(jv)) {
    if (req <= 0.04) continue;
    den += req;
    num += req * attainOf(req, own[sid] ?? 0);
  }
  return den > 0 ? num / den : 0;
}

/* ==================== 三、岗位核心任务的覆盖 ==================== */

export interface LiveTaskCoverage {
  taskId: string;
  taskName: string;
  /** 该任务在本岗位中的权重 */
  weight: number;
  coverage: number;
  /** 拖低这项任务的能力，至多三项 */
  weakest: string[];
}

/**
 * 岗位的每项核心任务，本简历能覆盖多少。
 *
 * 任务这一层不在服务端的比对口径内 —— 它逐项比对的是能力要求，不经由任务。
 * 但图谱有岗位—任务与任务—能力两类实测关系，简历一侧的能力持有度又已由
 * 服务端给出，两者相乘即得：一项任务的覆盖度 = 它所需各能力的加权平均达成率，
 * 权重取任务—能力边的综合权重。与 matching.fitAgainst 的任务块同式，
 * 差别只在简历一侧的向量换成了服务端的判定。
 */
export function liveTaskCoverage(
  jobId: string,
  own: Record<string, number>,
  reqOf: ReqVecOf = graphReqVecOf,
  lambda = 1,
): LiveTaskCoverage[] {
  const d = getDataset();
  /* 分母是任务—能力边（一项任务要用到哪些能力、各占多重），分子里那句
     「这一项达成了没有」按岗位一侧的要求强度判定，与覆盖度一列同源。 */
  const jv = reqOf(jobId);
  const nameOf = (nid: string) => d.nodeById.get(nid)?.name ?? nid;

  return edgesFrom(d.edges, 'J-T', jobId)
    .map((jt) => {
      let num = 0;
      let den = 0;
      const holes: { name: string; miss: number }[] = [];
      for (const e of edgesFrom(d.edges, 'T-S', jt.target)) {
        const req = e.baseWeight + lambda * e.deltaWeight;
        const a = attainOf(jv[e.target] ?? 0, own[e.target] ?? 0);
        num += req * a;
        den += req;
        if (a < SOLID) holes.push({ name: nameOf(e.target), miss: req * (1 - a) });
      }
      return {
        taskId: jt.target,
        taskName: nameOf(jt.target),
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
}

/* ==================== 四、简历分段 ==================== */

/**
 * 一段经历。切分依据是简历原文本身的版面：小节标题与“起止时间”单独成行。
 *
 * 服务端对上传件不返回经历元信息 —— 它的经历切分只对内部脱敏记录生效，
 * 文件上传一路走的是整篇抽取，每条证据的 source_experience_id 一律记作
 * resume_full（见 candidate_core/run_v3.py 的 extraction_mode）。
 * 但每条证据都带着落在全文上的字符偏移，据此把证据归入所在段即可，
 * 切分规则与服务端 resume_segmentation_v4 的日期行规则一致。
 */
export interface ResumeSegment {
  id: string;
  kind: 'work' | 'project' | 'education' | 'other';
  /** 段名：工作段取单位名，项目段取项目名 */
  title: string;
  /** 工作段的职位；项目段的角色。取不到时为空 */
  role: string;
  /** 起止时间原文，未写明时为空 */
  period: string;
  start: number;
  end: number;
  text: string;
}

/** 与服务端 resume_segmentation_v4._DATE_RANGE_RE 同式：整行只有一个起止区间 */
const DATE_RANGE = /^(?:19|20)\d{2}(?:[./-]\d{1,2})?\s*[-–—~～至到]\s*(?:(?:19|20)\d{2}(?:[./-]\d{1,2})?|至今|现在|Present)$/i;

/** 简历小节标题。命中即换一段，段的类别随之而定 */
const SECTION_KIND: { re: RegExp; kind: ResumeSegment['kind'] }[] = [
  { re: /^(工作|职业)经[历验]$/, kind: 'work' },
  { re: /^(项目|科研|研究)经[历验]$/, kind: 'project' },
  { re: /^教育(经历|背景)$/, kind: 'education' },
  { re: /^(实习经[历验])$/, kind: 'work' },
];

/** 页眉页脚一类的重复行，切分时跳过 */
const NOISE_LINE = /^(第\s*\d+\s*页|来源：|页码)/;

interface Line {
  text: string;
  start: number;
  end: number;
}

function lineSpans(text: string): Line[] {
  const out: Line[] = [];
  let cursor = 0;
  for (const raw of text.split('\n')) {
    out.push({ text: raw.trim(), start: cursor, end: cursor + raw.length });
    cursor += raw.length + 1;
  }
  return out;
}

/**
 * 把简历全文切成若干段经历。
 *
 * 规则：先按小节标题定下当前段的类别，段内每遇到一行“起止时间”即开一段新条目，
 * 条目的起点上溯到该时间行之前的名称行 —— 工作段再上溯一行取单位名，
 * 与服务端切内部记录时的取法相同（公司 → 职位 → 时间 / 名称 → 时间 → 角色）。
 */
export function segmentResume(resumeText: string): ResumeSegment[] {
  if (!resumeText.trim()) return [];
  const lines = lineSpans(resumeText);

  /* 每行属于哪一小节 */
  const kindAt: ResumeSegment['kind'][] = [];
  let cur: ResumeSegment['kind'] = 'other';
  for (const l of lines) {
    const hit = SECTION_KIND.find((s) => s.re.test(l.text));
    if (hit) cur = hit.kind;
    else if (l.text && /^[一-龥]{2,8}$/.test(l.text) && /^(专利|论文|期刊|会议|授权|荣誉|奖项|资格|证书|专业技能|求职意向|技能)/.test(l.text))
      cur = 'other';
    kindAt.push(cur);
  }

  /* 起点：每个日期行往上找名称（工作段再往上找单位） */
  const anchors: { at: number; dateAt: number; kind: ResumeSegment['kind'] }[] = [];
  for (let i = 0; i < lines.length; i++) {
    if (!DATE_RANGE.test(lines[i].text)) continue;
    const kind = kindAt[i];
    if (kind === 'other') continue;
    let t = i - 1;
    while (t >= 0 && (!lines[t].text || NOISE_LINE.test(lines[t].text))) t -= 1;
    if (t < 0) continue;
    if (kind === 'work') {
      let c = t - 1;
      while (c >= 0 && (!lines[c].text || NOISE_LINE.test(lines[c].text))) c -= 1;
      /* 上一行已是小节标题或另一段的正文时不再上溯 */
      if (c >= 0 && !SECTION_KIND.some((s) => s.re.test(lines[c].text)) && lines[c].text.length <= 30) t = c;
    }
    anchors.push({ at: t, dateAt: i, kind });
  }
  if (anchors.length === 0) return [];

  const out: ResumeSegment[] = [];
  const counter: Record<string, number> = {};
  for (let k = 0; k < anchors.length; k++) {
    const a = anchors[k];
    const startLine = a.at;
    /* 段末止于下一条经历，或本小节结束处 —— 以先到者为准。
       只按下一条经历切，末尾那一段会把小节之后的论文、证书、技能清单
       一并吞进来，落在那里的证据便会被算成该段经历的支撑。 */
    let sectionEnd = lines.length - 1;
    for (let i = a.dateAt + 1; i < lines.length; i++) {
      if (kindAt[i] !== a.kind) {
        sectionEnd = i - 1;
        break;
      }
    }
    const endLine = Math.min(
      k + 1 < anchors.length ? anchors[k + 1].at - 1 : lines.length - 1,
      sectionEnd,
    );
    const start = lines[startLine].start;
    const end = lines[endLine].end;
    counter[a.kind] = (counter[a.kind] ?? 0) + 1;
    const head = lines.slice(startLine, a.dateAt).map((l) => l.text).filter(Boolean);
    const after = lines[a.dateAt + 1]?.text ?? '';
    out.push({
      id: `${a.kind}_${String(counter[a.kind]).padStart(3, '0')}`,
      kind: a.kind,
      title: head[0] ?? '（未写明）',
      /* 工作段的职位在单位名之后；项目段的角色在时间行之后，且不是整句正文 */
      role: head[1] ?? (after && after.length <= 12 && !DATE_RANGE.test(after) ? after : ''),
      period: lines[a.dateAt].text,
      start,
      end,
      text: resumeText.slice(start, end),
    });
  }
  return out;
}

/* ==================== 五、经历 ↔ 岗位能力 ==================== */

export interface LiveExperienceLink {
  seg: ResumeSegment;
  /** 这段经历覆盖了目标岗位可计分要求权重的百分之多少 0–1 */
  coverage: number;
  /** 该段支撑到的岗位要求 */
  hits: { name: string; weight: number; gap: LiveSkillItem['gap'] }[];
  /** 该段证据落在这几项任务上 */
  tasks: string[];
  /** 该段贡献的证据条数 */
  evidence: number;
}

/**
 * 每段经历各支撑了目标岗位的哪些能力要求，占其要求权重的几成。
 *
 * 证据带着落在简历全文上的偏移，按偏移落入哪一段即归入哪一段；
 * 一项能力要求由某段的证据支撑时，按该项在岗位一侧的权重计入该段的覆盖。
 * 权重取 target_job_profile 的市场信号（effective_weight），为实测。
 */
export function liveExperienceLinks(
  segments: ResumeSegment[],
  items: LiveSkillItem[],
  jobId: string,
): LiveExperienceLink[] {
  const total = items.reduce((s, i) => s + i.weight, 0) || 1;
  const d = getDataset();
  const nameOf = (nid: string) => d.nodeById.get(nid)?.name ?? nid;
  /* 该岗位的每项任务各要求哪些能力、各占该任务多重。一段经历落在某项任务上的
     分量，即它支撑到的那几项能力在该任务里的权重之和。 */
  const taskLinks = edgesFrom(d.edges, 'J-T', jobId).map((jt) => {
    const links = edgesFrom(d.edges, 'T-S', jt.target);
    const den = links.reduce((a, e) => a + e.effectiveWeight, 0) || 1;
    return {
      taskId: jt.target,
      taskName: nameOf(jt.target),
      jobWeight: jt.effectiveWeight,
      den,
      w: new Map(links.map((e) => [e.target, e.effectiveWeight])),
    };
  });

  /** 该项要求在这一段里的兑现程度：已满足记满，等级不足与证据不足记半 */
  const credit = (gap: LiveSkillItem['gap']) => (gap === 'SATISFIED' ? 1 : 0.5);

  return segments
    .filter((s) => s.kind === 'work' || s.kind === 'project')
    .map((seg) => {
      const hits: LiveExperienceLink['hits'] = [];
      /* 技能 id 与判定状态成对留存：下面的 hits 要按权重重排，两者分列两个数组
         再按下标配对就会错位 */
      const hitIds: { id: string; gap: LiveSkillItem['gap'] }[] = [];
      let evidence = 0;
      for (const it of items) {
        const inSeg = it.evidence.filter(
          (e) => e.start !== null && e.start >= seg.start && e.start < seg.end,
        );
        if (inSeg.length === 0) continue;
        evidence += inSeg.length;
        hits.push({ name: it.name, weight: it.weight, gap: it.gap });
        hitIds.push({ id: `S:${it.teamSkillId}`, gap: it.gap });
      }
      hits.sort((a, b) => b.weight - a.weight);
      const covered = hits.reduce((s, h) => s + h.weight * credit(h.gap), 0);
      const tasks = taskLinks
        .map((t) => {
          let num = 0;
          for (const h of hitIds) num += (t.w.get(h.id) ?? 0) * credit(h.gap);
          return { name: t.taskName, share: num / t.den, jobWeight: t.jobWeight };
        })
        .filter((t) => t.share > 0.08)
        .sort((a, b) => b.share - a.share || b.jobWeight - a.jobWeight)
        .slice(0, 3)
        .map((t) => t.name);
      return { seg, coverage: Math.min(1, covered / total), hits: hits.slice(0, 6), tasks, evidence };
    })
    .sort((a, b) => b.coverage - a.coverage);
}

/* ==================== 六、真实性与一致性核验 ==================== */

export type LiveCheckLevel = 'pass' | 'watch' | 'risk';

export interface LiveCheck {
  id: string;
  title: string;
  level: LiveCheckLevel;
  /** 一个可以当场核对的数 */
  metric: string;
  detail: string;
  /** 判据落在原文的哪几处，[起, 止) 字符偏移 */
  spans: [number, number][];
  items?: string[];
}

export interface LiveAudit {
  checks: LiveCheck[];
  /** 可核验度 0–100：把存疑与风险项按权重扣出来的分，不是“真假概率” */
  score: number;
  risk: number;
  watch: number;
}

function bigrams(s: string): Set<string> {
  const t = s.replace(/[\s　·、，。：；（）()／/]/g, '');
  const out = new Set<string>();
  for (let i = 0; i < t.length - 1; i++) out.add(t.slice(i, i + 2));
  return out;
}

function jaccard(a: Set<string>, b: Set<string>): number {
  if (a.size === 0 || b.size === 0) return 0;
  let inter = 0;
  for (const x of a) if (b.has(x)) inter += 1;
  return inter / (a.size + b.size - inter);
}

/** 起止时间原文 → 月数。写“至今”按取数当月算 */
function monthsOf(period: string, now = new Date()): number {
  const m = period.match(
    /((?:19|20)\d{2})(?:[./-](\d{1,2}))?\s*[-–—~～至到]\s*(?:((?:19|20)\d{2})(?:[./-](\d{1,2}))?|(至今|现在|Present))/i,
  );
  if (!m) return 0;
  const y0 = Number(m[1]);
  const m0 = Number(m[2] ?? 1);
  const y1 = m[5] ? now.getFullYear() : Number(m[3]);
  const m1 = m[5] ? now.getMonth() + 1 : Number(m[4] ?? 12);
  return Math.max(0, (y1 - y0) * 12 + (m1 - m0));
}

/** 简历里自述的工作年限。写法不一，取第一处能读出的数 */
function declaredYears(text: string): number | null {
  const m = text.match(/(?:工作|工作经验|经验)\s*(\d{1,2})\s*年|(\d{1,2})\s*年(?:工作)?经验/);
  if (!m) return null;
  return Number(m[1] ?? m[2]);
}

/**
 * 简历真实性与一致性核验。
 *
 * 七项判据全部落在实测输入上：简历原文（服务端返回的解析全文）、
 * 服务端逐项的能力判定与证据偏移、简历技能清单的列名（explicit_skill_mentions），
 * 以及目标岗位招聘原文的锚点句（图谱的岗位—能力边所带证据）。
 * 每一项都给出判据落在原文的哪几处，界面上点一下即在左栏定位。
 *
 * 本节不下“造假 / 没造假”的断言 —— 系统能做的是把可核验的矛盾点摆出来。
 */
export function liveAuditResume(
  resumeText: string,
  segments: ResumeSegment[],
  items: LiveSkillItem[],
  mentions: { text: string; start: number; end: number; mapping_status: string }[],
  /** 目标岗位的规范名，用于取该岗位的招聘原文 */
  targetJobName: string,
  /** 简历一侧已判定的各项能力所属的维度，取自图谱的技能节点 */
  profileDims: string[],
): LiveAudit {
  const checks: LiveCheck[] = [];

  /* ① 技能清单里列到的技术名，有没有经历兜底 ——
        服务端已把“只在清单里列了个名”与“经历里拿得出行为证据”分成两件事记，
        前者的 mapping_status 一律带 frozen_display_only 前缀，不构成对能力的支撑。 */
  const listOnly = mentions.filter(
    (m) => m.mapping_status.startsWith('frozen_display_only') && /^[A-Za-z0-9.+#\- ]{2,}$/.test(m.text),
  );
  const evidenceText = items.flatMap((i) => i.evidence.map((e) => e.text)).join('\n');
  const unbacked = listOnly.filter((m) => !evidenceText.includes(m.text));
  checks.push({
    id: 'claim-vs-experience',
    title: '技能清单的列名是否有经历支撑',
    level: unbacked.length === 0 ? 'pass' : unbacked.length <= 2 ? 'watch' : 'risk',
    metric: `${unbacked.length} / ${listOnly.length} 项仅列于技能清单`,
    detail:
      listOnly.length === 0
        ? '简历未单列技能清单，各项能力均由经历描述直接支撑。'
        : unbacked.length === 0
          ? '技能清单所列各项均可在经历描述中找到对应内容，自述与经历一致。'
          : `${unbacked.map((m) => m.text).join('、')} 仅出现在技能清单中，各段经历的行为证据里均无对应内容；` +
            '此类列名不进入能力判定，也无从判断掌握程度，宜在面试环节追问。',
    spans: unbacked.map((m) => [m.start, m.end] as [number, number]),
    items: unbacked.map((m) => m.text),
  });

  /* ② 判定置信度与证据条数是否相称 ——
        服务端给每项判定一个置信度；置信度高而全篇只找到一处落点的，
        属于易失真的表述类型，须回原文核对。 */
  const thin = items.filter((i) => i.gap !== 'MISSING' && i.evidence.length === 1);
  checks.push({
    id: 'evidence-thickness',
    title: '能力判定的证据是否成串',
    level: thin.length === 0 ? 'pass' : thin.length <= 2 ? 'watch' : 'risk',
    metric: `${thin.length} / ${items.filter((i) => i.gap !== 'MISSING').length} 项仅一处证据`,
    detail:
      thin.length === 0
        ? '已判定的各项能力在简历中均有两处以上落点，判定与证据强度相称。'
        : `${thin.map((i) => i.name).join('、')} 全篇仅被一段话支撑，缺少第二处佐证。` +
          '单点证据无法排除偶发或转述，是面试追问的重点。',
    spans: thin.flatMap((i) =>
      i.evidence
        .filter((e) => e.start !== null && e.end !== null)
        .map((e) => [e.start as number, e.end as number] as [number, number]),
    ),
    items: thin.map((i) => i.name),
  });

  /* ③ 各段经历时长与自述年限是否自洽 */
  const declared = declaredYears(resumeText);
  const workSegs = segments.filter((s) => s.kind === 'work');
  const months = workSegs.reduce((a, s) => a + monthsOf(s.period), 0);
  const ratio = declared && declared > 0 ? months / (declared * 12) : 1;
  checks.push({
    id: 'timeline',
    title: '经历时长与自述年限是否自洽',
    level: declared === null ? 'pass' : ratio > 1.25 || ratio < 0.7 ? 'watch' : 'pass',
    metric:
      declared === null
        ? `工作段合计 ${months} 个月 · 简历未自述年限`
        : `工作段合计 ${months} 个月 · 自述 ${declared * 12} 个月`,
    detail:
      declared === null
        ? `简历未写明工作年限，各工作段的起止时间合计 ${months} 个月，可据此核对。`
        : ratio > 1.25
          ? `各工作段时长合计 ${months} 个月，较自述的 ${declared * 12} 个月多出 ${months - declared * 12} 个月，` +
            '表明存在并行或时间重叠的经历（如借调、兼任、在读期间任职）。此项不构成矛盾，但对外声明年限时需注明统计口径。'
          : ratio < 0.7
            ? `各工作段合计覆盖 ${months} 个月，与自述的 ${declared * 12} 个月相差 ${declared * 12 - months} 个月未作说明，建议补充该时间段的经历。`
            : `各工作段合计 ${months} 个月，与自述的 ${declared * 12} 个月基本吻合，时间线自洽。`,
    spans: workSegs.map((s) => [s.start, Math.min(s.end, s.start + s.title.length + 40)] as [number, number]),
  });

  /* ④ 量化结果有没有交代口径 —— “提升 62%”不说在什么上比，就无法核验 */
  const numRe = /\d+(?:\.\d+)?\s*%|\d+(?:\.\d+)?\s*倍/;
  const basisRe = /测试集|评测集|验证集|基准|基线|同一批|对比组|口径|A\/B|人工评测|个查询|条样本/;
  const lines = lineSpans(resumeText).filter((l) => l.text && !NOISE_LINE.test(l.text));
  const quantified = lines.filter((l) => numRe.test(l.text));
  const noBasis = quantified.filter((l) => !basisRe.test(l.text));
  checks.push({
    id: 'metric-basis',
    title: '量化结果是否说明统计口径',
    level: noBasis.length === 0 ? 'pass' : noBasis.length <= 2 ? 'watch' : 'risk',
    metric: `${noBasis.length} / ${quantified.length} 条数字未说明比较基准`,
    detail:
      quantified.length === 0
        ? '简历中未出现百分比或倍数一类的量化结果，无此项可核。'
        : noBasis.length === 0
          ? '简历中出现的量化结果均写明了统计范围与比较对象，属于可核验的表述。'
          : `${noBasis.map((l) => `“${l.text.slice(0, 26)}…”`).join('　')} 仅给出变化幅度，` +
            '未说明所在数据集、任务范围与比较基线。此类数字无法核验，通常为面试追问的重点。',
    spans: noBasis.map((l) => [l.start, l.end] as [number, number]),
  });

  /* ⑤ 措辞是否照搬招聘原文 ——
        判据不是“像不像模板”，而是与该岗位真实招聘信息锚点句的字面重合度。 */
  const raw = jobRawSource(targetJobName);
  /* 两处原文：逐条摘录的招聘正文（按句切开），与逐项能力要求的句级归因。
     两者同出于招聘原文表，见 data-pipeline/jdraw.mjs。 */
  const jdLines = [
    ...(raw?.samples ?? []).flatMap((x) => x.text.split(/[。；;\n]|\d+[、.)）]/)),
    ...Object.values(raw?.attrib ?? {}).flatMap((a) => (a.quotes ?? []).map((q) => q.text)),
  ]
    .map((t) => t.trim())
    .filter((t) => t.length >= 8);
  const jdSnippets = jdLines.map((t) => bigrams(t));
  let echoLine: Line | null = null;
  let echoSim = 0;
  for (const l of lines) {
    if (l.text.length < 8) continue;
    const b = bigrams(l.text);
    for (const j of jdSnippets) {
      const s = jaccard(b, j);
      if (s > echoSim) {
        echoSim = s;
        echoLine = l;
      }
    }
  }
  const echoBad = echoSim >= 0.3;
  checks.push({
    id: 'template-echo',
    title: '措辞是否照搬招聘原文',
    level: jdSnippets.length === 0 ? 'pass' : echoBad ? 'watch' : 'pass',
    metric:
      jdSnippets.length === 0
        ? '该岗位无可比对的招聘锚点句'
        : `与招聘原文最高重合 ${(echoSim * 100).toFixed(0)}% · 比对 ${jdSnippets.length} 句`,
    detail:
      jdSnippets.length === 0
        ? '本窗口该岗位的招聘信息未留下可比对的锚点句，此项不作判定。'
        : echoBad
          ? `“${echoLine?.text.slice(0, 30) ?? ''}…”与该岗位招聘原文的字面重合度达到 ${(echoSim * 100).toFixed(0)}%；` +
            '此类表述普遍存在于各类简历中，区分度低，建议替换为具体承担的工作内容。'
          : `全篇与该岗位 ${jdSnippets.length} 句招聘原文的最高字面重合度为 ${(echoSim * 100).toFixed(0)}%，表述具备原创性。`,
    spans: echoBad && echoLine ? [[echoLine.start, echoLine.end]] : [],
  });

  /* ⑥ 能力是否有主线：简历一侧已判定的各项能力落在几个维度上。
        取候选人自身的能力画像，而不是目标岗位的要求 —— 主线是这份简历的属性，
        换一个目标岗位不该改变它。 */
  const dimCount = new Map<string, number>();
  for (const dim of profileDims) dimCount.set(dim, (dimCount.get(dim) ?? 0) + 1);
  const mapped = [...dimCount.values()].reduce((a, b) => a + b, 0) || 1;
  const topDim = [...dimCount.entries()].sort((a, b) => b[1] - a[1])[0];
  const focus = topDim ? topDim[1] / mapped : 0;
  checks.push({
    id: 'stack-focus',
    title: '能力是否有主线',
    level: dimCount.size >= 4 && focus < 0.4 ? 'watch' : 'pass',
    metric: `覆盖 ${dimCount.size} 个能力维度 · 主线占比 ${(focus * 100).toFixed(0)}%`,
    detail:
      dimCount.size >= 4 && focus < 0.4
        ? `已判定的能力分散于 ${dimCount.size} 个维度，单一维度占比未达四成；主线不明确的简历在筛选环节易被判定为广度有余、深度不足。`
        : `已判定的能力集中于“${topDim?.[0] ?? '—'}”，占比 ${(focus * 100).toFixed(0)}%，能力主线清晰。`,
    spans: [],
  });

  /* ⑦ 同一类计数在不同段落里对不上 */
  const countRe = /([一-龥A-Za-z0-9-]{2,10})\s*(\d+)\s*(篇|项|次|个)/g;
  const groups = new Map<string, { n: number; line: Line }[]>();
  for (const l of lines) {
    for (const m of l.text.matchAll(countRe)) {
      const key = `${m[1]}|${m[3]}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push({ n: Number(m[2]), line: l });
    }
  }
  const conflicts = [...groups.entries()].filter(([, v]) => new Set(v.map((x) => x.n)).size > 1);
  checks.push({
    id: 'count-conflict',
    title: '同一事项在不同段落中的计数是否一致',
    level: conflicts.length ? 'watch' : 'pass',
    metric: conflicts.length ? `${conflicts.length} 处计数不一致` : '未检出冲突',
    detail: conflicts.length
      ? conflicts
          .map(
            ([k, v]) =>
              `“${k.split('|')[0]}”在不同段落中分别记为 ${[...new Set(v.map((x) => x.n))].join(' 与 ')} ${k.split('|')[1]}`,
          )
          .join('；') + '。统计口径不同（如在审、共同一作）时可能同时成立，但需在面试环节说明。'
      : '简历中出现的计数（论文、专利、奖项等）在各段落之间彼此一致。',
    spans: conflicts.flatMap(([, v]) => v.map((x) => [x.line.start, x.line.end] as [number, number])),
  });

  const risk = checks.filter((c) => c.level === 'risk').length;
  const watch = checks.filter((c) => c.level === 'watch').length;
  return { checks, score: Math.max(0, Math.round(100 - risk * 18 - watch * 8)), risk, watch };
}
