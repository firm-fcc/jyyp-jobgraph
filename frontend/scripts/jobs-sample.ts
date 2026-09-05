/* =========================================================
   岗位洞察页样本导出

     node --experimental-strip-types 跑不了带路径别名的源码，
     因此走 esbuild 打包再执行，见 package.json 的 sample 脚本。

   裁剪口径：岗位取两个（一个萌芽、一个既有），任务只留这两个岗位
   连到的，能力组与技能点全留 —— 年轮切片与变更清单会点到全部 9 个
   能力组，留一半会让样本自己通不过文档第九节的引用完整性自检。
   ========================================================= */

import { writeFileSync } from 'node:fs';
import { getDataset } from '../src/data/generator';
import type { EvidenceRef, GraphEdge, GraphNode } from '../src/types/graph';

const EMERGING_JOB = 'J:AI智能体编排工程师';
const EXISTING_JOB = 'J:图像算法工程师';
const MAX_EVIDENCE = 2;

const d = getDataset();
const byId = new Map(d.nodes.map((n) => [n.id, n]));

const jobIds = [EMERGING_JOB, EXISTING_JOB];
for (const id of jobIds) if (!byId.has(id)) throw new Error(`样本岗位不存在：${id}`);

/* ---- 节点 ---- */
const jobEdges = d.edges.filter((e) => jobIds.includes(e.source));
const taskIds = new Set(jobEdges.filter((e) => e.kind === 'J-T').map((e) => e.target));
const keepIds = new Set<string>([
  ...jobIds,
  ...taskIds,
  ...d.nodes.filter((n) => n.kind === 'skill' || n.kind === 'skillpoint').map((n) => n.id),
]);
const nodes = d.nodes.filter((n) => keepIds.has(n.id));

/* ---- 边 ---- */
const edges = d.edges.filter(
  (e) =>
    ((e.kind === 'J-T' || e.kind === 'J-S') && jobIds.includes(e.source)) ||
    (e.kind === 'T-S' && taskIds.has(e.source)) ||
    e.kind === 'S-SP',
);

/** 裁 evidence：留前 N 条，被 duplicateOf 指到的那条一并留下，指不到的把标记摘掉 */
function trimEvidence(list: EvidenceRef[]): EvidenceRef[] {
  const kept = list.slice(0, MAX_EVIDENCE);
  const docIds = new Set(kept.map((v) => v.docId));
  for (const v of [...kept]) {
    if (!v.duplicateOf || docIds.has(v.duplicateOf)) continue;
    const origin = list.find((z) => z.docId === v.duplicateOf);
    if (origin) {
      kept.push(origin);
      docIds.add(origin.docId);
    }
  }
  return kept.map((v) => {
    const out: EvidenceRef = { ...v };
    if (out.duplicateOf && !docIds.has(out.duplicateOf)) {
      delete out.duplicateOf;
      delete out.duplicateSim;
    }
    if (out.extractedNodeId && !keepIds.has(out.extractedNodeId)) delete out.extractedNodeId;
    return out;
  });
}

const trimmedEdges: GraphEdge[] = edges.map((e) => ({ ...e, evidence: trimEvidence(e.evidence) }));

/* ---- 信号与年轮 ---- */
const signals = d.signals.filter((s) => jobIds.includes(s.entityId));
const annuli = d.annuli
  .filter((a) => jobIds.includes(a.jobId))
  .map((a) => ({ ...a, changes: a.changes.map((c) => ({ ...c, sources: trimEvidence(c.sources) })) }));

const sample = {
  _readme: [
    '岗位洞察页（/jobs）后端数据样本。四个键即四个端点的响应体，形状与前端真实消费的一致。',
    `【数据版本】分类体系 v2.0（岗位 131 + 萌芽 11 / 任务 27 / 能力组 9 / 技能点 49），自 mock 生成器实跑导出。`,
    '【裁剪口径】岗位只留两个：一个萌芽岗位、一个既有岗位；任务只留这两个岗位连到的；',
    '能力组 9 个与技能点 49 个全留（年轮切片与变更清单会点到全部能力组）。',
    '每条边的 evidence 留前 2 份（线上不设此上限）；被 duplicateOf 指到的那一份会一并保留，因此少数边是 3 份。',
    '样本自身通过文档第九节的 8 条联调自检，可直接当 fixture 使用。',
    '字段口径见 docs/岗位洞察-后端数据接口.md。',
  ],
  graph: { nodes, edges: trimmedEdges },
  signals,
  versions: { versions: d.versions, annuli },
  quality: d.quality,
};

/* ---- 自检：文档第九节那 8 条 ---- */
const problems: string[] = [];
const ids = new Set(nodes.map((n: GraphNode) => n.id));
for (const e of trimmedEdges) {
  if (!ids.has(e.source) || !ids.has(e.target)) problems.push(`悬空边 ${e.id}`);
  const docIds = new Set(e.evidence.map((v) => v.docId));
  for (const v of e.evidence) {
    if (v.duplicateOf && !docIds.has(v.duplicateOf)) problems.push(`悬空 duplicateOf ${v.docId}`);
    if (v.extractedNodeId && !ids.has(v.extractedNodeId)) problems.push(`悬空 extractedNodeId ${v.docId}`);
  }
}
for (const a of annuli) {
  if (!ids.has(a.jobId)) problems.push(`悬空 annuli.jobId ${a.jobId}`);
  for (const c of a.changes) {
    if (!ids.has(c.target.id)) problems.push(`悬空 change.target ${c.id}`);
    const docIds = new Set(c.sources.map((v) => v.docId));
    for (const v of c.sources) {
      if (v.duplicateOf && !docIds.has(v.duplicateOf)) problems.push(`悬空 change.sources.duplicateOf ${v.docId}`);
      if (v.extractedNodeId && !ids.has(v.extractedNodeId)) problems.push(`悬空 change.sources.extractedNodeId ${v.docId}`);
    }
  }
  for (const r of a.rings) {
    for (const s of r.slices) if (!ids.has(s.skillId)) problems.push(`悬空 ring.slice ${s.skillId}`);
    const sum = r.slices.reduce((x, s) => x + s.share, 0);
    if (Math.abs(sum - 1) > 0.005) problems.push(`环份额未归一 ${a.jobId} ${r.version} = ${sum.toFixed(4)}`);
  }
}
for (const s of signals) {
  const n = s.months.length;
  if (s.jd.length !== n || s.paper.length !== n || s.news.length !== n || s.gap.length !== n)
    problems.push(`序列不等长 ${s.entityId}`);
}
const versionSet = new Set(d.versions.map((v) => v.version));
versionSet.add('v2.2⁺');
for (const a of annuli) for (const c of a.changes) if (!versionSet.has(c.version)) problems.push(`未知版本号 ${c.version}`);
for (const n of nodes) {
  if (n.marketShare < 0 || n.marketShare > 1) problems.push(`marketShare 越界 ${n.id}`);
  if (n.confidence < 0 || n.confidence > 1) problems.push(`confidence 越界 ${n.id}`);
  if (n.kind !== 'job' || !n.attrs) continue;
  for (const [k, dist] of Object.entries(n.attrs)) {
    if (typeof dist !== 'object') continue;
    const sum = Object.values(dist as Record<string, number>).reduce((a, b) => a + b, 0);
    if (Math.abs(sum - 1) > 0.005) problems.push(`分布未归一 ${n.id}.${k} = ${sum.toFixed(4)}`);
  }
}
for (const s of signals) if (!jobIds.includes(s.entityId)) problems.push(`多余信号 ${s.entityId}`);
for (const id of jobIds) if (!signals.some((s) => s.entityId === id)) problems.push(`缺信号 ${id}`);
for (const v of trimmedEdges.flatMap((e) => e.evidence)) if (!v.title.includes('·')) problems.push(`title 未带出处 ${v.docId}`);

const out = 'docs/samples/jobs-insight.sample.json';
writeFileSync(out, JSON.stringify(sample, null, 2) + '\n', 'utf8');
console.log(
  `已写出 ${out}\n` +
    `  节点 ${nodes.length}（岗位 ${jobIds.length} / 任务 ${taskIds.size} / 能力组 ${nodes.filter((n) => n.kind === 'skill').length} / 技能点 ${nodes.filter((n) => n.kind === 'skillpoint').length}）\n` +
    `  边 ${trimmedEdges.length} · 信号 ${signals.length} · 版本 ${d.versions.length} · 年轮 ${annuli.length}`,
);
if (problems.length) {
  console.error(`\n自检未通过（${problems.length} 项）：`);
  for (const p of [...new Set(problems)].slice(0, 20)) console.error('  ' + p);
  process.exit(1);
}
console.log('  自检 8 项全部通过');
