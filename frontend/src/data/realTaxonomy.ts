/* ============================================================
   真实体系接入层 —— 把算法侧产出的三份分类文件转成前端词表种子

   数据来源（src/data/real/）：
     jobs_v2.json     岗位体系 v2.5，9 个类别 / 136 个岗位，两级
     tasks_v3.json    任务体系 v0.8，35 项任务，扁平无层级
     skills0821.json  技能体系 v0.6，50 个技能，3 层（2 维度 → 10 组 → 50 技能）

   三份文件与图谱产物同源：取自数据包末窗（2024-01）的 base/ 目录，
   与 public/data/ 下的产物出自同一次算法侧运行。两处若不同版，
   同一条目会在图谱里查得到、在种子里查不到。

   ------------------------------------------------------------
   随 2026-08-31 批次换版

   岗位 v2.0 → v2.5（131 → 136）
     叠层的五个前瞻岗位转正进入体系：人机协同专家、平台工程师、科研软件工程师、
     量子技术从业者、产品工程师，编码以 GJ- 起首。五者的 category 字段为空，
     算法侧未给一级归属，界面上归入 ORPHAN_CLUSTER。

   任务 v0.3 → v0.8（27 → 35）
     新增八项，均为叠层转正：多模态数据融合建模、仿真数据增强、后台自动化操作、
     生产环境测试、集群生命周期管理、智能体评测、机器人技能示教、AI辅助编程。

   技能 v0.5 → v0.6（49 → 50）
     新增一个能力组 T-DG「前瞻新技能」，收两项转正技能。
     T 维因此由 4 组增至 5 组，能力组总数由 9 增至 10。

   ------------------------------------------------------------
   上一次换版（2026-08-21 算法侧产出，2026-08-22 接入）

   岗位 v1.1 → v2.0（255 节点 → 131 岗位）
     归类口径换了 —— 由逐 JD 关键词词库 + LLM 判定，不再依赖 51job funtype。
     255 个 v1 节点里，200 个归并进 131 个岗位（33 个岗位由多个来源节点合并
     而来），46 个低 IT 相关岗位剔除，9 个大类整类撤销。一级归属从 15 个大类
     + 41 个孤立叶子收到 9 个类别，131 个岗位全部已归类。

     新增的四样东西上一版一件都没有：
       · 131 条岗位定义，LLM 阅读数据集中的真实 JD 样例生成（每岗 4 篇）
       · 131 条边界说明，各点名一个最易混淆的同侪岗位并写出判据
       · 2557 条判定关键词，即归类用的快路词库
       · hits —— 每个岗位对应的招聘信息条数，合计 510 万条。
         上一版的 count 字段 255 条全为 0，"市场占比"只能靠补齐；
         这一版它是实测量，凡是读市场规模的地方一律改读它。

     funtypes 与来源节点码也保留了下来（255 条职能名 / 200 个来源码），
     归并链路因此可以逐条回源核对。

   任务 v0.2 → v0.3（35 项 → 27 项）
     收录判据改为"是否信息技术岗位的工作职责"，法务、财务、人力、化学分析
     与制造工艺类任务整批移出；另人工审定补入 T-26 技术团队管理、
     T-27 技术培训与知识赋能。27 项全部重新命名，与上一版无一项同名。

   技能 v0.4 → v0.5（49 项不变）
     纯命名规范化：20 项更名，名称收敛到 7–10 字，编码 / 定义 / 软硬分类
     / 英文名一概未动，三级结构（2 维度 / 9 组 / 49 技能）也未动。

   ------------------------------------------------------------
   哪些是真实的，哪些是本文件补出来的

   真实：全部条目的规范名称、中英文名、岗位的一级归属（转正的五个岗位除外）、
        136 条岗位定义与 131 条边界说明、岗位判定关键词、招聘信息条数、
        51job 职能名与来源节点码、技能的三级归属与定义、技能软硬分类、
        任务的中文描述。

   补的：技术栈标签、成熟度分档。三份分类文件里没有这两维。

   岗位与任务的关联、任务与能力的关联、要求程度分档、薪资、城市、发现时间
   六项，此前确由本层补齐；图谱产物接入后各有实测来源，不再走补齐层，
   本文件的种子在 IS_REAL_GRAPH 下亦由 realGraph 的图谱种子取代
   （见 seeds.ts）。

   补出来的部分一律走 SYNTHETIC 常量登记，界面上按这份清单标注口径。
   把它们混进"真实数据"里当结论展示，等于拿生成数据冒充测量结果。
   ------------------------------------------------------------

   层级映射：三份分类文件里没有"技能点"这一层。图谱产物接入之前，四环按
   岗位 → 任务 → 能力组 → 技能 对齐，把技能体系的"组"当能力层、"技能"当
   技能点层。图谱产物给出了真正的技能点层（开放集合，本批逾万项），
   四层因而按算法侧口径归位：能力组降为技能的一级归属。本文件的这套映射
   只在 VITE_DATA=taxonomy 的对照档下仍走。
   ============================================================ */

import type { SkillType } from '@/types/graph';
import type { JobSeed, SkillPointSeed, SkillSeed, TaskSeed } from './taxonomy';
import { demoJobProfile, demoTaskSkills } from './demoFill';
import { randInt } from '@/utils/rng';

import jobsDoc from './real/jobs_v2.json';
import catOverrides from '../../data-pipeline/job-category-overrides.json';
import tasksDoc from './real/tasks_v3.json';
import skillsDoc from './real/skills0821.json';

/* ---------------- 补齐项登记 ----------------
   哪些字段是算法侧真的产出、哪些由演示补齐层生成，以这两张表为准。
   界面上不再有集中陈列它们的"数据口径"一栏 —— 那一栏读的是全页脚注，
   而每张图的"演示数据"标已各自写明本图的口径，更贴近要核对的那个位置。
   这两张表因此改为供代码与文档引用：新增补齐项时先登记在这里。 */
export const SYNTHETIC_FIELDS = [
  '技术栈标签（三份分类文件均无此维度，界面改用能力体系的两维十组）',
  '企业类别分布（招聘原文表只给企业名，无类别一列）',
  '技能点的成熟度分档',
  '岗位定义五要素中的必备技能、加分技能与典型应用场景三项',
  '简历样例及其解析结果',
] as const;

/**
 * 图谱产物接入后不再由演示补齐层给出的维度。
 *
 * 这几项此前均记在补齐清单内，接入后各有实测来源，界面上相应位置的
 * 演示数据标随之撤除。清单保留是为了让口径的变更本身有一处记录：
 * 同一张图在两版之间由补齐改为实测，读者据此可以判断新旧截图之间的差异
 * 来自数据换版，而非画法调整。
 *
 * 后四项为 2026-08-31 批次新增；末三项由已有字段推得，不是算法侧补的新字段。
 */
export const PROMOTED_FIELDS = [
  '岗位—任务、任务—技能、岗位—技能、技能—技能点四类关联关系及其权重',
  '要求程度分档（招聘信息的熟练度判定，P1 至 P4 与无法确定五档，末窗覆盖入图的 52 项技能中的 46 项）',
  '薪资档与级别分布（逐条招聘信息汇总，四十六窗合计 378,068 条）',
  '四层的月度序列与三源强度（四十六个观测窗口，2022-05 至 2026-04）',
  '前瞻信号的证据链（论文编号与新闻标题，逐条可核）',
  '城市分布（招聘原文表 place 列，391 座城市；界面按省级行政区汇总）',
  '在招企业（招聘原文表 company 列，81,362 家）',
  '招聘原文摘录（每岗三条，各截断至 420 字）',
  '按职级分档的能力要求（汇总表逐条的职级与能力要求，职级列 75.0% 有值）',
  '要求程度落到“某岗位对某技能”这一粒度（汇总表 skill_vec_prof 列）',
  '学历分布（原文表 degree 一列多数为空，缺值由正文的门槛语抽出，覆盖招聘原文条数的七成四）',
  '招聘原文的句级归因（由 skillpoint_map 的技能点名在正文中的落点反查所在句，覆盖八成九的“条—能力项”对）',
  '叠层新岗位的任务与技能构成（算法侧未产出关联边，由叠层证据句的结构化信号与锚点文本相似度推得）',
] as const;

export const REAL_FIELDS = [
  '岗位 136 个规范名称与中英文名（jobs v2.5）',
  '岗位一级归属：9 个类别，136 个岗位全部已归类',
  '岗位招聘信息条数 510 万条（130 个岗位有计量，6 个为 0）',
  '岗位定义 136 条（LLM 阅读数据集中的真实招聘 JD 样例生成）',
  '岗位边界说明 131 条，点名体系内的另一个岗位',
  '岗位判定关键词 2557 条',
  '51job 原始职能名 260 条与来源节点码 200 个',
  '任务 35 项名称与描述（tasks v0.8）',
  '能力 50 项名称、定义与软硬技能分类（硬 31 / 软 19，原文件里 6 项“软硬兼具”按所属维度并入；skills v0.6）',
  '能力体系三级归属（2 维度 / 10 组 / 50 技能）',
] as const;

/** 无一级归属的岗位的归属名。
    v2.0 里 131 个岗位全部归了类，这一档不再出现 —— 常量留着只作兜底：
    topCategory 是可选字段，画法一旦读到空值，宁可落到一个写明"没有归属"的
    位置上，也不许悄悄塞进某个它并不属于的类里。 */
export const ORPHAN_CLUSTER = '无一级归属';

/* ---------------- 岗位体系 ---------------- */

interface RawJob {
  code: string;
  /** 一级类别码 */
  category: string;
  name_zh: string;
  name_en: string;
  definition: string;
  /** 归类判定用的关键词，也是本岗位最具体的技术指纹 */
  keywords: string[];
  boundary: string;
  /** 51job 原始职能名 */
  funtypes: string[];
  /** v1 体系里归并进本岗位的节点码 */
  source_codes: string[];
  source_names: string[];
  /** 招聘信息条数 —— 岗位这一层唯一的实测计量 */
  hits: number;
  /** 生成定义时读的 JD 样例篇数 */
  n_samples: number;
}

interface RawCategory {
  code: string;
  name_zh: string;
  name_en: string;
  description: string;
}

const JOB_DETAIL = jobsDoc.detail as unknown as Record<string, RawJob>;
const RAW_JOBS = Object.values(JOB_DETAIL);
const RAW_CATS = jobsDoc.categories as unknown as RawCategory[];

const CAT_NAME = new Map(RAW_CATS.map((c) => [c.code, c.name_zh]));

/* 转正岗位的一级归属补录。算法侧按批次积累若干轮后才统一归类，本批九个新转正的
   岗位在体系文件里 category 为空字符串。补录表与构建脚本共用同一份，故体系视图
   与图谱产物给出的归属必然一致；体系文件里有值时以体系文件为准。 */
const CAT_OVERRIDE = catOverrides.overrides as Record<string, string>;
const catCodeOf = (j: RawJob) => j.category || CAT_OVERRIDE[j.code] || '';

/** 9 个类别。children / leaves 两个字段名沿用上一版，下游按它排序与计数 ——
    两级体系下这两个数相同：类下就是岗位，岗位不再往下分。
    posts 是本类岗位的招聘信息条数合计，这一版才有。 */
export const JOB_CATEGORIES: {
  code: string;
  name: string;
  scope: string;
  children: number;
  leaves: number;
  posts: number;
}[] = RAW_CATS.map((c) => {
  const kids = RAW_JOBS.filter((j) => catCodeOf(j) === c.code);
  return {
    code: c.code,
    name: c.name_zh,
    scope: c.description,
    children: kids.length,
    leaves: kids.length,
    posts: kids.reduce((a, j) => a + (j.hits ?? 0), 0),
  };
}).sort((a, b) => b.posts - a.posts);

/** 无一级归属的岗位数。v2.0 实测为 0，留着这个读数是为了让"全部已归类"
    这句话有出处 —— 写死一个 0 与逐条数出来的 0 不是一回事。 */
export const ORPHAN_JOB_COUNT = RAW_JOBS.filter((j) => !CAT_NAME.has(catCodeOf(j))).length;

/** 每个岗位的一级归属名 */
const TOP_OF = new Map<string, string>();
for (const j of RAW_JOBS) TOP_OF.set(j.code, CAT_NAME.get(catCodeOf(j)) ?? ORPHAN_CLUSTER);

/* ---------------- 体系收敛：255 个 v1 节点怎么变成 131 个岗位 ----------------
   上一版这个位置读的是 jobs0806.json 的 duplicate_merge（12 组去重记录）。
   v2.0 的收敛比那一版彻底得多，而且每一步都能回源：
     · 200 个 v1 节点归并进 131 个岗位，其中 33 个岗位由多个节点合并而来
     · 46 个低 IT 相关岗位整条剔除（楼宇自动化、电池/电源开发、计量工程师……）
     · 9 个大类整类撤销
   三个数加起来正好是 v1 的 255 个节点，剔除清单逐条带原节点码与名字。 */

const FROM_V1 = jobsDoc.meta.from_v1 as unknown as {
  kept_merged: number;
  excluded_non_it: number;
  dropped_categories: number;
  excluded_detail: Record<string, string>;
  dropped_category_detail?: Record<string, string>;
};

/** 由多个 v1 节点合并而来的岗位 —— 归并链路真正发生了合并的那一批 */
export const REAL_MERGES: {
  code: string;
  name: string;
  category: string;
  /** 被合进来的来源节点名 */
  from: string[];
  fromCodes: string[];
  posts: number;
}[] = RAW_JOBS.filter((j) => (j.source_codes?.length ?? 0) > 1)
  .map((j) => ({
    code: j.code,
    name: j.name_zh,
    category: TOP_OF.get(j.code) ?? ORPHAN_CLUSTER,
    from: j.source_names ?? [],
    fromCodes: j.source_codes ?? [],
    posts: j.hits ?? 0,
  }))
  .sort((a, b) => b.from.length - a.from.length || b.posts - a.posts);

/** 被剔除的 46 个低 IT 相关岗位，原节点码 → 原名 */
export const EXCLUDED_JOBS: { code: string; name: string }[] = Object.entries(
  FROM_V1.excluded_detail ?? {},
).map(([code, name]) => ({ code, name }));

/** 体系收敛的三笔账，界面上按它如实交代 */
export const CONVERGENCE = {
  /* v1 的节点总数 = 归并保留 + 剔除 + 整类撤销的大类节点。
     实测 200 + 46 + 9 = 255，与上一版 jobs0806.json 的节点数对得上 ——
     只写前两项会少掉 9 个，账就平不了。 */
  v1Nodes: FROM_V1.kept_merged + FROM_V1.excluded_non_it + FROM_V1.dropped_categories,
  kept: FROM_V1.kept_merged,
  excluded: FROM_V1.excluded_non_it,
  droppedCategories: FROM_V1.dropped_categories,
  jobs: RAW_JOBS.length,
  /** 真正发生合并（来源节点 ≥ 2）的岗位数 */
  merged: REAL_MERGES.length,
};

/** 255 条原始职能名的分布：多少岗位挂着平台原始名，一条都没有的有几个 */
export const FUNTYPE_STATS = (() => {
  const withFt = RAW_JOBS.filter((j) => j.funtypes?.length);
  return {
    total: RAW_JOBS.length,
    covered: withFt.length,
    strings: withFt.reduce((a, j) => a + j.funtypes.length, 0),
  };
})();

/* ---------------- 边界判定 ----------------
   每个岗位一条边界说明，点名一个最容易与它混淆的同侪岗位并写出判据。
   它与上面的归并链路回答的是同一个问题的两半 ——
   "凭什么说这是两个岗位而不是一个"：归并说的是合了谁，边界说的是与谁划界。 */

export interface JobBoundary {
  code: string;
  /** 被界定的岗位 */
  name: string;
  category: string;
  /** 边界里点名的那个岗位 */
  refName: string;
  refCode: string;
  /** 点名的岗位是否在体系内 */
  inSystem: boolean;
  /** 同类还是跨类 —— 跨类的那几条说明类的边界本身容易被踩 */
  crossCategory: boolean;
  /** 判据原文 */
  text: string;
  posts: number;
}

/** 边界原文里点名的那个岗位。文件没有单独的 ref 字段，按体系内岗位名在
    原文里做最长匹配取出 —— 取最长是因为"产品经理"是"数据产品经理"的子串，
    短名先命中就会把判据指向一个更泛的岗位。本岗位自身排除在外。 */
const NAMES_BY_LEN = RAW_JOBS.map((j) => j.name_zh).sort((a, b) => b.length - a.length);

function refOf(job: RawJob): { name: string; code: string } {
  const text = job.boundary ?? '';
  for (const n of NAMES_BY_LEN) {
    if (n === job.name_zh) continue;
    if (text.includes(n)) {
      const hit = RAW_JOBS.find((x) => x.name_zh === n)!;
      return { name: n, code: hit.code };
    }
  }
  return { name: '', code: '' };
}

export const REAL_BOUNDARIES: JobBoundary[] = RAW_JOBS.map((j) => {
  const ref = refOf(j);
  const refJob = ref.code ? JOB_DETAIL[ref.code] : undefined;
  return {
    code: j.code,
    name: j.name_zh,
    category: TOP_OF.get(j.code) ?? ORPHAN_CLUSTER,
    refName: ref.name,
    refCode: ref.code,
    inSystem: !!refJob,
    crossCategory: !!refJob && catCodeOf(refJob) !== catCodeOf(j),
    text: j.boundary ?? '',
    posts: j.hits ?? 0,
  };
});

/** 边界判定的覆盖情况：多少条点到了体系内的岗位、其中多少条跨类 */
export const BOUNDARY_STATS = {
  total: REAL_BOUNDARIES.length,
  inSystem: REAL_BOUNDARIES.filter((b) => b.inSystem).length,
  crossCategory: REAL_BOUNDARIES.filter((b) => b.crossCategory).length,
  definitions: RAW_JOBS.filter((j) => j.definition).length,
  keywords: RAW_JOBS.reduce((a, j) => a + (j.keywords?.length ?? 0), 0),
  samples: RAW_JOBS.reduce((a, j) => a + (j.n_samples ?? 0), 0),
};

/** 互相点名的岗位对 —— A 的边界点 B、B 的边界点 A。
    这类对子是体系里最贴近的一批：两边都认为对方是自己最容易被混淆的岗位。 */
export const MUTUAL_BOUNDARIES: { a: JobBoundary; b: JobBoundary }[] = (() => {
  const byCode = new Map(REAL_BOUNDARIES.map((b) => [b.code, b]));
  const out: { a: JobBoundary; b: JobBoundary }[] = [];
  const seen = new Set<string>();
  for (const b of REAL_BOUNDARIES) {
    const other = byCode.get(b.refCode);
    if (!other || other.refCode !== b.code) continue;
    const key = [b.code, other.code].sort().join('|');
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ a: b, b: other });
  }
  return out.sort((x, y) => y.a.posts + y.b.posts - (x.a.posts + x.b.posts));
})();

/** 招聘信息条数的口径 */
export const POST_STATS = {
  total: RAW_JOBS.reduce((a, j) => a + (j.hits ?? 0), 0),
  covered: RAW_JOBS.filter((j) => (j.hits ?? 0) > 0).length,
  jobs: RAW_JOBS.length,
};

/* ---------------- 能力体系 ---------------- */

interface RawSkill {
  code: string;
  name_zh: string;
  name_en: string;
  definition: string;
  /** 原始文件里还留着 hybrid 这一档，故此处不收窄到 SkillType */
  skill_type: string;
}

/* 算法侧只给硬 / 软两类，而 skills0821.json 里还有 6 项标着 hybrid（软硬兼具）。
   归并按它所在的一级维度走：T（技术技能）下的算硬，F（通用素养）下的算软。
   这条规则与文件里已有的 43 项完全相容 —— hard 那 27 项全在 T 下，
   soft 那 16 项全在 F 下，归并之后是硬 30 / 软 19。 */
const normalizeSkillType = (raw: string, code: string): SkillType =>
  raw === 'hard' || (raw !== 'soft' && !code.startsWith('F')) ? 'hard' : 'soft';

interface RawSkillGroup {
  name: string;
  name_en: string;
  skills: string[];
}

interface RawSkillDim {
  name: string;
  name_en: string;
  groups: Record<string, RawSkillGroup>;
}

const SKILL_DETAIL = skillsDoc.detail as unknown as Record<string, RawSkill>;
const SKILL_TREE = (skillsDoc as unknown as { 简明体系: Record<string, RawSkillDim> })['简明体系'];

/** 9 个能力组 —— 对应四环里的"能力"层 */
export const SKILL_GROUPS: {
  code: string;
  name: string;
  dim: string;
  dimCode: string;
  skills: string[];
}[] = (() => {
  const out: { code: string; name: string; dim: string; dimCode: string; skills: string[] }[] = [];
  for (const [dimCode, dim] of Object.entries(SKILL_TREE)) {
    for (const [gc, g] of Object.entries(dim.groups)) {
      out.push({ code: gc, name: g.name, dim: dim.name, dimCode, skills: g.skills });
    }
  }
  return out;
})();

const GROUP_OF_SKILL = new Map<string, (typeof SKILL_GROUPS)[number]>();
for (const g of SKILL_GROUPS) for (const s of g.skills) GROUP_OF_SKILL.set(s, g);

/** 49 项能力的软硬构成，按组统计 —— 覆盖面分布图的真实分母 */
export const SKILL_TYPE_BY_GROUP = SKILL_GROUPS.map((g) => {
  const types = g.skills.map((n) => {
    const d = Object.values(SKILL_DETAIL).find((s) => s.name_zh === n);
    return d ? normalizeSkillType(d.skill_type, d.code) : 'hard';
  });
  return {
    code: g.code,
    name: g.name,
    dim: g.dim,
    total: g.skills.length,
    hard: types.filter((t) => t === 'hard').length,
    soft: types.filter((t) => t === 'soft').length,
  };
});

/* ---------------- 一级归属：各层用自己的，不再共用技术栈 ----------------
   真实数据里不存在一条跨 岗位/任务/能力 三层的公共维度，
   四个候选轴逐一试过都覆盖不全。与其造一条假的公共轴，
   不如让每层报自己的一级归属：
     岗位 → 9 个类别之一
     任务 → 扁平，无归属
     能力组 → 2 个能力维度之一
     技能点 → 所属能力组 */
const TASK_CATEGORY = '（体系为扁平结构，无一级归属）';

/* ---------------- 种子导出 ---------------- */

/** 能力层 = 9 个能力组，一级归属取其所在能力维度 */
export const REAL_SKILLS: SkillSeed[] = SKILL_GROUPS.map((g) => ({
  name: g.name,
  category: g.dim,
  realCount: g.skills.length,
}));

/** 技能点层 = 49 个具体技能，归属其所在能力组 */
export const REAL_SKILL_POINTS: SkillPointSeed[] = Object.values(SKILL_DETAIL).map((s) => {
  const g = GROUP_OF_SKILL.get(s.name_zh);
  return {
    name: s.name_zh,
    skills: [g?.name ?? SKILL_GROUPS[0].name],
    category: g?.name ?? SKILL_GROUPS[0].name,
    // level 是演示词表时代的成熟度分档，真实数据没有这一维；留 1 只为满足契约，
    // 界面上已不再读它（筛选轴换成了真实存在的 skill_type 两态）
    level: 1,
    firstSeen: '',
    skillType: normalizeSkillType(s.skill_type, s.code),
    definition: s.definition,
  };
});

export const REAL_TASKS: TaskSeed[] = (
  tasksDoc.tasks as { code: string; name_zh: string; name_en: string; description: string }[]
).map((t) => ({
  name: t.name_zh,
  category: TASK_CATEGORY,
  // 由任务自己的能力映射给（见 demoFill.demoTaskSkills）
  skills: demoTaskSkills(t.name_zh),
  // 真实数据没有发现时间。留空串，由 generator 决定怎么如实呈现"没有"
  firstSeen: '',
  definition: t.description,
}));

export const REAL_JOBS: JobSeed[] = RAW_JOBS.map((j) => {
  const top = TOP_OF.get(j.code) ?? ORPHAN_CLUSTER;
  /* 能力与任务画像按类别骨架给，再按本岗位的判定关键词做一次命中
     （见 demoFill.demoJobProfile）。按名字哈希随便挑出来的画像会让
     "相近岗位""能力地形""差距明细"同时失去意义。 */
  const { must, plus, tasks } = demoJobProfile(j.code, top, j.keywords ?? []);
  const lo = randInt(`${j.code}|sl`, 12, 40);
  return {
    name: j.name_zh,
    category: top,
    cluster: top,
    // v2.0 的定义是真实字段，逐条来自 LLM 阅读的真实 JD 样例，直接用
    definition: j.definition,
    coreDuties: [],
    mustSkills: must,
    plusSkills: plus,
    scenarios: [],
    tasks,
    directSkills: must.slice(0, 3),
    emerging: false,
    firstSeen: '',
    salary: [lo, lo + randInt(`${j.code}|sh`, 10, 40)] as [number, number],
    /* ---- 以下均为真实字段 ---- */
    aliases: j.funtypes ?? [],
    funtypes: j.funtypes ?? [],
    keywords: j.keywords ?? [],
    boundary: j.boundary,
    nameEn: j.name_en,
    /** 招聘信息条数 —— 岗位这一层唯一的实测计量，市场规模一律读它 */
    posts: j.hits ?? 0,
    realCount: j.hits ?? 0,
    topCategory: top,
  };
});

/** 规模口径，供界面如实交代 */
export const REAL_SCALE = {
  jobs: REAL_JOBS.length,
  jobCategories: JOB_CATEGORIES.length,
  jobOrphans: ORPHAN_JOB_COUNT,
  jobLeaves: RAW_JOBS.length,
  tasks: REAL_TASKS.length,
  skillGroups: REAL_SKILLS.length,
  skills: REAL_SKILL_POINTS.length,
  skillDims: Object.keys(SKILL_TREE).length,
  funtypes: FUNTYPE_STATS.strings,
  funtypeCovered: FUNTYPE_STATS.covered,
  /** 体系收敛 */
  v1Nodes: CONVERGENCE.v1Nodes,
  kept: CONVERGENCE.kept,
  excluded: CONVERGENCE.excluded,
  droppedCategories: CONVERGENCE.droppedCategories,
  merges: CONVERGENCE.merged,
  /** 定义与边界 */
  definitions: BOUNDARY_STATS.definitions,
  boundaries: BOUNDARY_STATS.total,
  boundariesInSystem: BOUNDARY_STATS.inSystem,
  boundariesCross: BOUNDARY_STATS.crossCategory,
  mutualPairs: MUTUAL_BOUNDARIES.length,
  keywords: BOUNDARY_STATS.keywords,
  samples: BOUNDARY_STATS.samples,
  /** 招聘计量 */
  posts: POST_STATS.total,
  postsCovered: POST_STATS.covered,
};
