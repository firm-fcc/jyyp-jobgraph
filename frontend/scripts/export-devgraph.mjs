/* 导出能力发展图谱的精简副本，供报告页在未接入解析服务时规划学习路径。

   源文件是 backend/candidate_core/config/skill_development_graph_*.json ——
   与解析服务在线时所用的是同一批图谱，故两条链路给出的学习路径同源，
   差别只在能力差距是由上传件判定的还是由内置示例简历判定的。

   只取渲染要用到的字段：节点的名称、证据任务、判据与先修关系，以及验证任务
   与综合任务。来源登记、定义与学习目标不上界面，不予导出。

     npm run devgraph
*/
import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve, join } from 'node:path';

const SRC = resolve('../backend/candidate_core/config');
const OUT = resolve('public/data/devgraph.json');

const files = readdirSync(SRC)
  .filter((f) => f.startsWith('skill_development_graph_') && f.endsWith('.json'))
  .sort();

const out = {};
for (const f of files) {
  const g = JSON.parse(readFileSync(join(SRC, f), 'utf-8'));
  out[g.team_skill_id] = {
    name: g.team_skill_name,
    nodes: (g.nodes ?? []).map((n) => ({
      id: n.subskill_id,
      name: n.name_zh,
      task: n.evidence_task ?? '',
      crit: n.validation_criteria ?? [],
      pre: n.prerequisites ?? [],
    })),
    verify: g.verification_task
      ? {
          name: g.verification_task.objective,
          desc: g.verification_task.task_description,
          crit: g.verification_task.validation_criteria ?? [],
        }
      : null,
    cap: g.capstone_evidence_task
      ? {
          obj: g.capstone_evidence_task.objective,
          desc: g.capstone_evidence_task.task_description,
          crit: g.capstone_evidence_task.validation_criteria ?? [],
        }
      : null,
  };
}

writeFileSync(OUT, JSON.stringify(out), 'utf-8');
console.log(`devgraph.json  ${files.length} 项能力  ${(readFileSync(OUT).length / 1024).toFixed(0)} KB`);
