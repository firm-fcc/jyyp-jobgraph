/* ============================================================
   简历真实性与关联性核验

   报告页最先要回答的两件事：
     ① 这份简历里有没有说不通、对不上、拿不出依据的地方
     ② 他写的这些经历，和目标岗位到底有多大关系

   两件事都不下“造假 / 没造假”这种断言 —— 系统能做的是把可核验的
   矛盾点摆出来，标明是从简历哪一行读出来的，让人自己判断。
   所有结论都能回溯到原文行 id，界面上点一下就在左栏高亮。
   ============================================================ */

import type { GraphEdge, ResumeExperience, ResumeProfile } from '@/types/graph';
import { NOISE_PHRASES } from './taxonomy';
import { SEED_SKILL_POINTS as SKILL_POINTS } from './seeds';
import { resolveClaim } from './demoFill';
import { getDataset } from './generator';
import { jobVector } from './matching';

export type CheckLevel = 'pass' | 'watch' | 'risk';

export interface AuthCheck {
  id: string;
  title: string;
  level: CheckLevel;
  /** 一个可以当场核对的数 */
  metric: string;
  detail: string;
  /** 判定依据落在简历哪几行 */
  lines: string[];
  /** 涉及的具体条目，界面上列成小标签 */
  items?: string[];
}

export interface AuthReport {
  checks: AuthCheck[];
  /** 可核验度 0–100：把存疑与风险项按权重扣出来的分，不是“真假概率” */
  score: number;
  risk: number;
  watch: number;
}

/* ---------------- 文本工具 ---------------- */

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

const allLines = (r: ResumeProfile) => r.sections.flatMap((s) => s.lines);

/* ============================================================
   一、真实性核验
   ============================================================ */

export function auditResume(resume: ResumeProfile, targetJobId: string): AuthReport {
  const d = getDataset();
  const lines = allLines(resume);
  const checks: AuthCheck[] = [];

  /* ① 技能自述有没有经历兜底 —— 简历里最常见的“注水”形态 */
  const claimed = new Set(resume.experiences.flatMap((e) => e.claims));
  const listOnly = resume.skillPoints.filter((sp) => sp.from === 'list' && !claimed.has(sp.name));
  checks.push({
    id: 'claim-vs-experience',
    title: '技能自述是否有对应经历支撑',
    level: listOnly.length === 0 ? 'pass' : listOnly.length <= 2 ? 'watch' : 'risk',
    metric: `${listOnly.length} / ${resume.skillPoints.length} 项仅列于技能清单`,
    detail:
      listOnly.length === 0
        ? '每项技能均可在项目或工作经历中找到对应描述，自述与经历一致。'
        : `${listOnly.map((s) => s.name).join('、')} 仅出现在技能清单中，全部经历描述内均无对应内容；` +
          '此类项无法判定掌握程度，匹配计算中已按较低置信度处理，建议在面试环节重点追问。',
    lines: [...new Set(listOnly.flatMap((s) => s.anchors))],
    items: listOnly.map((s) => s.name),
  });

  /* ② 熟练度自评与证据强度是否背离 */
  const over = resume.skillPoints.filter((sp) => sp.proficiency >= 0.7 && sp.anchors.length <= 1);
  checks.push({
    id: 'proficiency-evidence',
    title: '熟练度自评与原文证据是否匹配',
    level: over.length === 0 ? 'pass' : over.length <= 2 ? 'watch' : 'risk',
    metric: `${over.length} 项自评偏高而原文仅一处提及`,
    detail:
      over.length === 0
        ? '自评熟练度较高的技能在简历中均有两处以上落点，自评与证据强度相称。'
        : `${over.map((s) => `${s.name}（自评 ${s.proficiency.toFixed(2)}）`).join('、')} ` +
          '全篇仅被提及一次，缺少第二处佐证。自评偏高而证据不足，属于易失真的表述类型。',
    lines: [...new Set(over.flatMap((s) => s.anchors))],
    items: over.map((s) => s.name),
  });

  /* ③ 经历时长与自述年限对不对得上 */
  const workMonths = resume.experiences
    .filter((e) => e.kind !== 'competition')
    .reduce((a, e) => a + e.months, 0);
  const declared = resume.years * 12;
  const ratio = declared > 0 ? workMonths / declared : 1;
  checks.push({
    id: 'timeline',
    title: '经历时长与自述年限是否自洽',
    level: ratio > 1.25 || ratio < 0.7 ? 'watch' : 'pass',
    metric: `经历合计 ${workMonths} 个月 · 自述 ${declared} 个月`,
    detail:
      ratio > 1.25
        ? `各段经历时长合计 ${workMonths} 个月，较自述的 ${declared} 个月多出 ${workMonths - declared} 个月，` +
          '表明存在并行或时间重叠的经历（如实习与在校、项目与本职工作并行）。此项不构成矛盾，但对外声明年限时需注明统计口径。'
        : ratio < 0.7
          ? `各段经历合计覆盖 ${workMonths} 个月，与自述的 ${declared} 个月相差 ${declared - workMonths} 个月未作说明，建议补充该时间段的经历。`
          : `各段经历合计 ${workMonths} 个月，与自述的 ${declared} 个月基本吻合，时间线自洽。`,
    lines: resume.experiences.flatMap((e) => e.lines.slice(0, 1)),
  });

  /* ④ 量化结果有没有交代口径 —— “提升 62%”如果不说在什么上比，就无法核验 */
  const numRe = /\d+(\.\d+)?\s*%|\d+(\.\d+)?\s*倍/;
  /* 只认真正交代了比较范围的词。“线上”“任务”这类看着像口径、其实什么都没说清 ——
     放进来会让“准确率从 71% 提升到 86%”这种最典型的无口径表述被判成通过。 */
  const basisRe = /测试集|评测集|验证集|基准|基线|同一批|对比组|口径|A\/B|人工评测|个查询|条样本/;
  const quantified = lines.filter((l) => numRe.test(l.text));
  const unbacked = quantified.filter((l) => !basisRe.test(l.text));
  checks.push({
    id: 'metric-basis',
    title: '量化结果是否说明统计口径',
    level: unbacked.length === 0 ? 'pass' : unbacked.length <= 2 ? 'watch' : 'risk',
    metric: `${unbacked.length} / ${quantified.length} 条数字未说明比较基准`,
    detail:
      unbacked.length === 0
        ? '简历中出现的量化结果均写明了统计范围与比较对象，属于可核验的表述。'
        : `${unbacked.map((l) => `“${l.text.slice(0, 26)}…”`).join('　')} 仅给出变化幅度，` +
          '未说明所在数据集、任务范围与比较基线。此类数字无法核验，通常为面试追问的重点。',
    lines: unbacked.map((l) => l.id),
  });

  /* ⑤ 措辞是否照抄招聘信息 + 样板话术
     判断依据不是“像不像模板”这种感觉，而是与目标岗位真实招聘信息原文的字面重合度。 */
  const jdSnippets = d.edges
    .filter((e) => e.source === targetJobId)
    .flatMap((e) => e.evidence)
    .filter((ev) => ev.sourceType === 'jd')
    .map((ev) => bigrams(ev.snippet));
  let echoLine = lines[0];
  let echoSim = 0;
  for (const l of lines) {
    const b = bigrams(l.text);
    for (const j of jdSnippets) {
      const s = jaccard(b, j);
      if (s > echoSim) {
        echoSim = s;
        echoLine = l;
      }
    }
  }
  const noiseHits = lines.flatMap((l) =>
    NOISE_PHRASES.filter((p) => l.text.includes(p)).map((p) => ({ line: l, phrase: p })),
  );
  const echoBad = echoSim >= 0.3 || noiseHits.length >= 2;
  checks.push({
    id: 'template-echo',
    title: '措辞是否照搬招聘信息与样板话术',
    level: echoBad ? 'watch' : 'pass',
    metric: `与招聘原文最高重合 ${(echoSim * 100).toFixed(0)}% · 样板话术 ${noiseHits.length} 处`,
    detail: echoBad
      ? `“${echoLine?.text.slice(0, 30) ?? ''}…”与该岗位招聘信息原文的字面重合度达到 ${(echoSim * 100).toFixed(0)}%；` +
        (noiseHits.length
          ? `另检出 ${noiseHits.length} 处样板话术（${[...new Set(noiseHits.map((n) => n.phrase))].join('、')}）。`
          : '') +
        '此类表述普遍存在于各类简历中，区分度低，抽取环节一律不计入能力统计，建议替换为具体承担的工作内容。'
      : `全篇与招聘信息原文的最高字面重合度为 ${(echoSim * 100).toFixed(0)}%，且未检出成串的样板话术，表述具备原创性。`,
    lines: [...new Set([echoBad && echoLine ? echoLine.id : '', ...noiseHits.map((n) => n.line.id)].filter(Boolean))],
    items: [...new Set(noiseHits.map((n) => n.phrase))],
  });

  /* ⑥ 技术栈聚焦度：样样都写“精通”，反而说明没有主线 */
  const catOf = (name: string) => SKILL_POINTS.find((s) => s.name === name)?.category;
  const catCount = new Map<string, number>();
  for (const sp of resume.skillPoints) {
    const c = catOf(sp.name);
    if (c) catCount.set(c, (catCount.get(c) ?? 0) + 1);
  }
  const mapped = [...catCount.values()].reduce((a, b) => a + b, 0) || 1;
  const topCat = [...catCount.entries()].sort((a, b) => b[1] - a[1])[0];
  const focus = topCat ? topCat[1] / mapped : 0;
  checks.push({
    id: 'stack-focus',
    title: '技术栈是否有主线',
    level: catCount.size >= 5 && focus < 0.4 ? 'watch' : 'pass',
    metric: `覆盖 ${catCount.size} 个技术栈 · 主栈占比 ${(focus * 100).toFixed(0)}%`,
    detail:
      catCount.size >= 5 && focus < 0.4
        ? `技能点分散于 ${catCount.size} 个技术栈，单一技术栈占比均未达四成；技术主线不明确的简历在筛选环节易被判定为广度有余、深度不足，建议突出主栈。`
        : `技能点集中于“${topCat?.[0] ?? '—'}”，占比 ${(focus * 100).toFixed(0)}%，技术主线清晰。`,
    lines: [],
  });

  /* ⑦ 同一类计数在不同段落里对不上 */
  const countRe = /([一-龥A-Za-z0-9-]{2,10})\s*(\d+)\s*(篇|项)/g;
  const groups = new Map<string, { n: number; line: string; text: string }[]>();
  for (const l of lines) {
    for (const m of l.text.matchAll(countRe)) {
      const key = `${m[1]}|${m[3]}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push({ n: Number(m[2]), line: l.id, text: l.text });
    }
  }
  const conflicts = [...groups.entries()].filter(
    ([, v]) => new Set(v.map((x) => x.n)).size > 1,
  );
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
    lines: conflicts.flatMap(([, v]) => v.map((x) => x.line)),
  });

  const risk = checks.filter((c) => c.level === 'risk').length;
  const watch = checks.filter((c) => c.level === 'watch').length;
  const score = Math.max(0, Math.round(100 - risk * 18 - watch * 8));
  return { checks, score, risk, watch };
}

/* ============================================================
   二、经历 ↔ 岗位能力 的关联度
   ============================================================ */

export interface ExperienceLink {
  exp: ResumeExperience;
  /** 这段经历覆盖了目标岗位能力要求权重的百分之多少 0–1 */
  coverage: number;
  hits: { skillId: string; name: string; required: number; owned: number }[];
  tasks: string[];
  /** 简历里写了、但图谱里对不上的技能点 */
  unmapped: string[];
}

/** 一组技能点名 → 能力层向量（与匹配算法同一条路径：沿 S-SP 边反向映射） */
function claimsToSkillVector(
  claims: string[],
  profOf: (name: string) => number,
  edges: GraphEdge[],
): Record<string, number> {
  const acc: Record<string, { num: number; den: number }> = {};
  for (const raw of claims) {
    /* 与简历技能点走同一个落点判定，否则同一个"Python"在两处一处对得上、
       一处对不上，报告里"已对齐"与"未对齐"两栏会互相矛盾 */
    const hit = resolveClaim(raw);
    const name = hit.mappedName ?? raw;
    /* 已归并到技能这一层的写法直接计入，技能点层没有它的落点 */
    if (hit.id.startsWith('S:')) {
      if (!acc[hit.id]) acc[hit.id] = { num: 0, den: 0 };
      acc[hit.id].num += profOf(name);
      acc[hit.id].den += 1;
      continue;
    }
    for (const l of edges.filter((e) => e.kind === 'S-SP' && e.target === hit.id)) {
      const w = Math.max(l.effectiveWeight, 0.05);
      if (!acc[l.source]) acc[l.source] = { num: 0, den: 0 };
      acc[l.source].num += profOf(name) * w;
      acc[l.source].den += w;
    }
  }
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(acc)) out[k] = v.den > 0 ? v.num / v.den : 0;
  return out;
}

export function linkExperiences(resume: ResumeProfile, jobId: string): ExperienceLink[] {
  const d = getDataset();
  const nodeById = d.nodeById;
  const jvec = jobVector(jobId);
  const jobTotal = Object.values(jvec).reduce((a, b) => a + b, 0) || 1;
  const profOf = (name: string) => resume.skillPoints.find((s) => s.name === name)?.proficiency ?? 0.6;

  const jobTasks = d.edges.filter((e) => e.kind === 'J-T' && e.source === jobId);

  return resume.experiences.map((exp) => {
    const evec = claimsToSkillVector(exp.claims, profOf, d.edges);
    const hits = Object.keys(evec)
      .filter((sid) => (jvec[sid] ?? 0) >= 0.08)
      .map((sid) => ({
        skillId: sid,
        name: nodeById.get(sid)?.name ?? sid,
        required: Number((jvec[sid] ?? 0).toFixed(3)),
        owned: Number(evec[sid].toFixed(3)),
      }))
      .sort((a, b) => b.required - a.required);

    /* 覆盖率用“达成率加权”而不是简单命中数：
       岗位要求 0.9 的能力只掌握到 0.3，不该按整项算进覆盖。 */
    const covered = hits.reduce((a, h) => a + h.required * Math.min(1, h.owned / Math.max(h.required, 0.05)), 0);

    const hitIds = new Set(hits.map((h) => h.skillId));
    const tasks = jobTasks
      .filter((jt) =>
        d.edges.some((e) => e.kind === 'T-S' && e.source === jt.target && hitIds.has(e.target) && e.effectiveWeight > 0.25),
      )
      .sort((a, b) => b.effectiveWeight - a.effectiveWeight)
      .slice(0, 3)
      .map((jt) => nodeById.get(jt.target)?.name ?? jt.target);

    return {
      exp,
      coverage: Math.min(1, covered / jobTotal),
      hits: hits.slice(0, 6),
      tasks,
      /* 未对齐 = 该写法在图谱里既没有技能点落点、也归并不到任何一项技能 */
      unmapped: exp.claims.filter((c) => !nodeById.has(resolveClaim(c).id)),
    };
  });
}

/* ============================================================
   三、技能点的外部参考资料
   只收录官方文档的稳定入口，拿不准的一律不给链接 ——
   报告里出现一个打不开的链接，比没有链接更糟。
   ============================================================ */

const DOC_LINKS: Record<string, string> = {
  Python: 'https://docs.python.org/3/',
  Go: 'https://go.dev/doc/',
  Rust: 'https://doc.rust-lang.org/book/',
  Java: 'https://docs.oracle.com/en/java/',
  PyTorch: 'https://pytorch.org/docs/stable/index.html',
  TensorFlow: 'https://www.tensorflow.org/api_docs',
  Transformers: 'https://huggingface.co/docs/transformers',
  LangChain: 'https://python.langchain.com/',
  Docker: 'https://docs.docker.com/',
  Kubernetes: 'https://kubernetes.io/docs/home/',
  Spark: 'https://spark.apache.org/docs/latest/',
  Flink: 'https://flink.apache.org/',
  Kafka: 'https://kafka.apache.org/documentation/',
  Airflow: 'https://airflow.apache.org/docs/',
  ClickHouse: 'https://clickhouse.com/docs',
  Milvus: 'https://milvus.io/docs',
  Elasticsearch: 'https://www.elastic.co/docs',
  MySQL: 'https://dev.mysql.com/doc/',
  Redis: 'https://redis.io/docs/latest/',
  CUDA: 'https://docs.nvidia.com/cuda/',
  DeepSpeed: 'https://www.deepspeed.ai/',
  vLLM: 'https://docs.vllm.ai/',
  ONNX: 'https://onnx.ai/onnx/',
  TensorRT: 'https://docs.nvidia.com/deeplearning/tensorrt/',
  Ray: 'https://docs.ray.io/en/latest/',
  MLflow: 'https://mlflow.org/docs/latest/index.html',
  Prometheus: 'https://prometheus.io/docs/introduction/overview/',
  MQTT: 'https://mqtt.org/',
  ROS2: 'https://docs.ros.org/en/rolling/index.html',
  OpenCV: 'https://docs.opencv.org/',
};

export const docLinkOf = (skillPointName: string): string | undefined => DOC_LINKS[skillPointName];
