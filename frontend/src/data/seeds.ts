/* ============================================================
   取数开关

   三档，由环境变量 VITE_DATA 切换：

     默认（graph）      算法侧的图谱产物：四层节点、四类边、各观测窗口的
                        月度序列、叠层前瞻信号、技能熟练度分布。见 realGraph.ts
     VITE_DATA=taxonomy 只用三份分类文件的体系，关系与时序由演示补齐层生成。
                        图谱产物接入之前的形态，留作对照
     VITE_DATA=mock     退回 taxonomy.ts 里的演示词表，用于规模对照

   把切换点收在一个文件里，三套数据可以直接对照，而不必改动下游任何一张图。
   ============================================================ */

import { JOBS, SKILLS, SKILL_POINTS, TASKS } from './taxonomy';
import {
  REAL_JOBS,
  REAL_SKILLS,
  REAL_SKILL_POINTS,
  REAL_TASKS,
} from './realTaxonomy';
import { DEMO_NEW_JOBS } from './demoFill';
import { IS_REAL_GRAPH, IS_REAL_TAXONOMY } from './dataSource';
import { GRAPH_SKILLPOINT_SEEDS, GRAPH_SKILL_SEEDS, GRAPH_TASK_SEEDS } from './realGraph';

/* 开关本身定义在 dataSource.ts，此处转出供既有引用点继续读 */
export { DATA_SOURCE, IS_REAL_GRAPH, IS_REAL_TAXONOMY, type DataSource } from './dataSource';

/* 真实体系的 131 个规范岗位 + 尚未进入体系的新发现岗位（演示数据）。
   重名的一律不并入 —— 节点 id 由名字生成，撞名会让整棵 React 树出现重复 key，
   而且"新发现"这个前提本身也不成立。 */
const REAL_JOB_NAMES = new Set(REAL_JOBS.map((j) => j.name));
export const SEED_JOBS = IS_REAL_TAXONOMY
  ? [...REAL_JOBS, ...DEMO_NEW_JOBS.filter((j) => !REAL_JOB_NAMES.has(j.name))]
  : JOBS;
export const SEED_TASKS = IS_REAL_GRAPH
  ? GRAPH_TASK_SEEDS
  : IS_REAL_TAXONOMY
    ? REAL_TASKS
    : TASKS;

/* 技能与技能点两层在接入图谱产物后归位：
   技能层由能力组一层换为体系内的各项技能，技能点层由技能一层换为算法侧的开放集合。
   种子须与图谱同源，否则同一条目在图谱里查得到、在种子里查不到，
   人岗匹配的对齐环节会把它判成体系外条目。 */
export const SEED_SKILLS = IS_REAL_GRAPH
  ? GRAPH_SKILL_SEEDS
  : IS_REAL_TAXONOMY
    ? REAL_SKILLS
    : SKILLS;
export const SEED_SKILL_POINTS = IS_REAL_GRAPH
  ? GRAPH_SKILLPOINT_SEEDS
  : IS_REAL_TAXONOMY
    ? REAL_SKILL_POINTS
    : SKILL_POINTS;

