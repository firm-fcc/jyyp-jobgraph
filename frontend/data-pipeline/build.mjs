#!/usr/bin/env node
/* ============================================================
   图谱数据构建脚本

   把算法侧的时间窗口产物（graph/{窗口}/{base,delta,effective}/ 与
   jd-summaries/）转换为前端直读的紧凑格式，输出至 public/data/。

   输入目录由 --src 指定，默认取仓库同级的 Reference-tmp 数据包。
   输入不入版本库，产物入库：产物是确定性转换的结果，
   可由同一份输入随时重建，转换过程不引入任何随机量或补齐值。

   用法：
     node data-pipeline/build.mjs [--src <目录>] [--out <目录>]
   ============================================================ */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { inferOverlayJobVectors } from './jobvec.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.resolve(HERE, '..');      /* frontend/ */
const REPO = path.resolve(APP, '..');      /* 仓库根 */

/* ---------------- 参数 ---------------- */

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const DEFAULT_SRC = path.resolve(REPO, '..', 'Reference-tmp', 'graph-2022-05_10(1)', 'graph-2022-05_10');
const SRC = path.resolve(arg('src', process.env.GRAPH_SRC || DEFAULT_SRC));
const OUT = path.resolve(arg('out', path.join(APP, 'public', 'data')));

/* ---------------- 工具 ---------------- */

const rd = (p) => JSON.parse(fs.readFileSync(p, 'utf8'));
const exists = (p) => fs.existsSync(p);
/** 统一保留四位小数，与算法侧产出的精度一致，避免浮点尾差进入产物 */
const r4 = (x) => Math.round(x * 1e4) / 1e4;
const r6 = (x) => Math.round(x * 1e6) / 1e6;

function writeJson(name, obj) {
  const p = path.join(OUT, name);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(obj), 'utf8');
  const kb = (fs.statSync(p).size / 1024).toFixed(1);
  console.log(`  ${name.padEnd(22)} ${kb.padStart(9)} KB`);
  return Number(kb);
}

/* ---------------- 输入定位 ---------------- */

if (!exists(SRC)) {
  console.error(`找不到输入目录：${SRC}`);
  console.error('用 --src 指定算法侧产出目录，或设置环境变量 GRAPH_SRC。');
  process.exit(1);
}

const GRAPH_DIR = path.join(SRC, 'graph');
const WINDOWS = fs
  .readdirSync(GRAPH_DIR)
  .filter((d) => /^\d{4}-\d{2}$/.test(d) && fs.statSync(path.join(GRAPH_DIR, d)).isDirectory())
  .sort();
const LATEST = WINDOWS[WINDOWS.length - 1];
const REL = ['job_task', 'job_skill', 'task_skill', 'skill_skillpoint'];
const REL_KEY = { job_task: 'jobTask', job_skill: 'jobSkill', task_skill: 'taskSkill', skill_skillpoint: 'skillSkillpoint' };

const winDir = (w, layer) => path.join(GRAPH_DIR, w, layer);
const hasLayer = (w, layer) => exists(winDir(w, layer));

console.log(`输入 ${SRC}`);
console.log(`窗口 ${WINDOWS.join(' ')}（末窗 ${LATEST}）`);
console.log(`输出 ${OUT}\n`);

fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });

/* ============================================================
   1. 体系：岗位 / 任务 / 技能三层的规范名与归属

   三份体系文件前端已有（src/data/real/），此处只取图谱侧需要的
   编码到名称映射，避免同一份数据在两处各存一份。
   ============================================================ */

const latestBase = winDir(LATEST, 'base');
const jobsDoc = rd(path.join(latestBase, 'jobs.json'));
const tasksDoc = rd(path.join(latestBase, 'tasks.json'));
const skillsDoc = rd(path.join(latestBase, 'skills.json'));

/** 岗位编码 → { name, category } */
const JOB_META = new Map();
for (const [code, j] of Object.entries(jobsDoc.detail)) {
  JOB_META.set(code, { name: j.name_zh, category: j.category, hits: j.hits ?? 0 });
}
const CAT_NAME = new Map(jobsDoc.categories.map((c) => [c.code, c.name_zh]));

/* 转正岗位的一级归属补录。算法侧按批次积累若干轮后才统一归类，本批 GJ-006 至
   GJ-014 九个新转正岗位的 category 为空字符串；空归属会在按类别切分的各图上
   多出一档"无一级归属"。补录表逐条按岗位定义判入既有九类之一，见
   job-category-overrides.json；体系文件里有值时以体系文件为准。 */
const CAT_OVERRIDE = rd(path.join(APP, 'data-pipeline', 'job-category-overrides.json')).overrides;
const catCodeOf = (id, given) => given || CAT_OVERRIDE[id] || '';

/** 任务编码 → name */
const TASK_NAME = new Map(tasksDoc.tasks.map((t) => [t.code, t.name_zh]));

/** 技能编码 → { name, group, dim, type } */
const SKILL_META = new Map();
/* 能力组的界面用名。原文表里 T-DG 一组名为"前瞻转正（ΔG 涌现）"，
   括号内是算法侧的中间量名，出现在轴名、年轮、图例等十余处，读者无从解读；
   界面一律改写为"前瞻新技能"。此处只换显示名，编码与归属一条未动。 */
const GROUP_RENAME = new Map([['前瞻转正（ΔG 涌现）', '前瞻新技能']]);
const groupName = (n) => GROUP_RENAME.get(n) ?? n;

/** 技能名 → 编码。汇总表的技能列写的是中文名，回查编码时用 */
const SKILL_CODE_BY_NAME = new Map();
{
  const detail = skillsDoc.detail;
  const tree = skillsDoc['简明体系'];
  const groupOfName = new Map();
  for (const [dimCode, dim] of Object.entries(tree)) {
    for (const [gCode, g] of Object.entries(dim.groups)) {
      for (const sName of g.skills) {
        groupOfName.set(sName, { group: groupName(g.name), groupCode: gCode, dim: dim.name, dimCode });
      }
    }
  }
  for (const [code, s] of Object.entries(detail)) {
    const g = groupOfName.get(s.name_zh) ?? {};
    SKILL_CODE_BY_NAME.set(s.name_zh, code);
    /* 软硬两态：文件里另有 6 项标 hybrid，按所属一级维度并入，
       与前端 realTaxonomy.normalizeSkillType 同口径 */
    const type = s.skill_type === 'hard' || (s.skill_type !== 'soft' && !code.startsWith('F')) ? 'hard' : 'soft';
    SKILL_META.set(code, { name: s.name_zh, ...g, type, definition: s.definition });
  }
}

/* ============================================================
   1.5 叠层编号的归一与时间校正

   算法侧的叠层条目按来源流各自计数：论文一侧与新闻一侧都从 PJ-001 起编，
   合入同一份 new_jobs.json 后同一编号指向两个实体（2024-01 窗的 PJ-007
   既是"事实核查员"又是"产品工程师"）。按编号并表会丢掉后出现的那个 ——
   末窗四层合计 605 条叠层条目里有 97 条因此消失。故先做一遍全窗扫描，
   按"编号 + 名称"建立唯一键：编号首次被某个名称占用时原样保留，
   再被另一个名称占用时顺次加后缀。窗口按时间升序扫描，
   同一次构建的结果确定，不同次构建之间亦不变。

   另一处校正是时间。论文一侧的条目 born_window 与 first_seen 一致，
   强度逐窗变化；新闻一侧则把同一读数复制进每一个窗口，
   "平台工程师"的 born_window 为 2023-07，却自 2022-07 起每窗都以 0.488
   出现。这类条目在其入场窗口之前不构成观测，逐窗序列上表现为一段凭空的
   平台期，故按窗口早于 born_window 剔除，计入 manifest.overlayGuards。
   ============================================================ */

const OVERLAY_UID = new Map();
const OVERLAY_TAKEN = new Set();
let overlayRenumbered = 0;

/* 重列回新岗位队列的那几个岗位：名称 → 体系编码。名单见 emerging-rejoin.json，
   逐窗取数与元数据的填充在本节之后（REJOIN_CODES 一段），此处先取这张对照表，
   因为编号的归一发生在扫描之前。 */
const REJOIN_BY_NAME = new Map(
  (rd(path.join(APP, 'data-pipeline', 'emerging-rejoin.json')).jobs ?? []).map((j) => [j.name, j.code]),
);

/** (层, 编号, 名称) → 全局唯一编号 */
function overlayUid(kind, id, name) {
  /* 重列的岗位一律改挂体系编码：逐窗的叠层记录、证据表与节点三处因而落在同一个
     键上。不归一时，论文与新闻的强度序列挂在算法侧的 PJ 编号下，而节点用的是
     体系的 GJ 编号，卡片上便只剩招聘一路有线可画。 */
  if (kind === 'job') {
    const code = REJOIN_BY_NAME.get(name);
    if (code) return code;
  }
  const key = `${kind}|${id}|${name}`;
  const hit = OVERLAY_UID.get(key);
  if (hit) return hit;
  let uid = id;
  for (let i = 2; OVERLAY_TAKEN.has(`${kind}|${uid}`); i++) uid = `${id}~${i}`;
  if (uid !== id) overlayRenumbered++;
  OVERLAY_UID.set(key, uid);
  OVERLAY_TAKEN.add(`${kind}|${uid}`);
  return uid;
}

/** 该条目在本窗是否构成观测：入场窗口晚于本窗的一律不算 */
const bornInTime = (it, w) => !it.born_window || it.born_window <= w;

const DELTA_FILE_KIND = {
  'new_jobs.json': 'job',
  'new_tasks.json': 'task',
  'new_skills.json': 'skill',
  'skillpoints.json': 'skillpoint',
};

let overlayPreBorn = 0;
{
  const wins = WINDOWS.filter((w) => hasLayer(w, 'delta'));
  for (const w of wins) {
    for (const [file, kind] of Object.entries(DELTA_FILE_KIND)) {
      const p = path.join(winDir(w, 'delta'), file);
      if (!exists(p)) continue;
      for (const it of rd(p).items ?? []) {
        if (!bornInTime(it, w)) {
          overlayPreBorn++;
          continue;
        }
        overlayUid(kind, it.id, it.name_zh);
      }
    }
  }
}

/* ============================================================
   2. 末窗图谱：四类边 + 四层节点

   边同时带基图权重与合成权重。前端默认读基图（市场当期需求），
   需要前瞻修正时切到合成权重，两者同源同窗，可直接对比。
   ============================================================ */

function readEdges(w, layer, rel) {
  const p = path.join(winDir(w, layer), `${rel}.json`);
  if (!exists(p)) return null;
  return rd(p).edges;
}

/* ---------------- 端点校验 ----------------
   四类边各自的端点必须落在对应体系内。基图六窗零越界零自环，
   合成层的 task_skill 有 97 条边的起点写成了技能编码而非任务编码
   （另含 4 条自环），系算法侧 synthesis 挑选高 gap 实体时未按体系过滤，
   技能编码与任务编码同以 T- 起首所致。这类边在四层结构中无落点，
   转换阶段剔除并计入 dropped，不进入产物。

   叠层新实体（PJ-/PT-/PS-/PK- 起首）不在基准体系内但为合法端点，走白名单。 */

const OVERLAY_PREFIX = /^(PJ|PT|PS|PK)-/;
const TASK_CODE = new Set(tasksDoc.tasks.map((t) => t.code));
const SKILL_CODE = new Set(Object.keys(skillsDoc.detail));
const JOB_CODE = new Set(Object.keys(jobsDoc.detail));

const inSet = (set, id) => set.has(id) || OVERLAY_PREFIX.test(id);
const ENDPOINT_SPEC = {
  job_task: [JOB_CODE, TASK_CODE],
  job_skill: [JOB_CODE, SKILL_CODE],
  task_skill: [TASK_CODE, SKILL_CODE],
  /* 技能点是开放集合，名称即 id，终点不设校验 */
  skill_skillpoint: [SKILL_CODE, null],
};

const dropped = {};

function validEdge(rel, e) {
  const [srcSet, dstSet] = ENDPOINT_SPEC[rel];
  if (e.src === e.dst) return false;
  if (!inSet(srcSet, e.src)) return false;
  if (dstSet && !inSet(dstSet, e.dst)) return false;
  return true;
}

function buildGraph(w) {
  const base = {};
  const eff = {};
  for (const rel of REL) {
    const b = readEdges(w, 'base', rel) ?? [];
    const f = hasLayer(w, 'effective') ? readEdges(w, 'effective', rel) ?? [] : [];
    const bOk = b.filter((e) => validEdge(rel, e));
    const fOk = f.filter((e) => validEdge(rel, e));
    const n = b.length - bOk.length + (f.length - fOk.length);
    if (n) dropped[rel] = (dropped[rel] ?? 0) + n;
    base[rel] = bOk;
    eff[rel] = fOk;
  }

  /* 合成层按 src>dst 建索引，与基图边逐条对齐；
     合成层独有的边（origin != base）单独记，它们在基图中没有对应项 */
  const edges = {};
  for (const rel of REL) {
    const effMap = new Map(eff[rel].map((e) => [`${e.src}>${e.dst}`, e]));
    const out = [];
    const seen = new Set();
    for (const e of base[rel]) {
      const key = `${e.src}>${e.dst}`;
      seen.add(key);
      const ee = effMap.get(key);
      out.push({
        s: e.src,
        t: e.dst,
        w: r4(e.weight),
        e: ee ? r4(ee.effective_weight ?? e.weight) : r4(e.weight),
        g: ee && ee.gap > 0 ? r4(ee.gap) : 0,
      });
    }
    /* 合成层新增的边：基图权重为 0，全部来自前瞻修正 */
    for (const ee of eff[rel]) {
      const key = `${ee.src}>${ee.dst}`;
      if (seen.has(key)) continue;
      out.push({
        s: ee.src,
        t: ee.dst,
        w: 0,
        e: r4(ee.effective_weight ?? 0),
        g: r4(ee.gap ?? 0),
        o: ee.origin || 'synthesized',
      });
    }
    edges[REL_KEY[rel]] = out;
  }
  return edges;
}

const edges = buildGraph(LATEST);

/* 节点：从边与体系文件求并，只保留图谱里实际出现的条目。
   岗位体系有 131 项，六窗 JD 里命中 97 项，不出现的不写进图谱节点表。 */
const spDoc = rd(path.join(latestBase, 'skillpoints.json'));
const entityFreq = rd(path.join(latestBase, 'entity_freq.json'));

const jobIds = new Set([...edges.jobTask.map((e) => e.s), ...edges.jobSkill.map((e) => e.s)]);
const taskIds = new Set([...edges.jobTask.map((e) => e.t), ...edges.taskSkill.map((e) => e.s)]);
const skillIds = new Set([
  ...edges.jobSkill.map((e) => e.t),
  ...edges.taskSkill.map((e) => e.t),
  ...edges.skillSkillpoint.map((e) => e.s),
]);

/* 叠层新实体一并进节点表。这批条目尚未被招聘市场确认，多数还没有任何边，
   在图上是只有自己没有连接的孤立节点。它们仍须进表：新岗位与新能力的发现
   本身就是叠层要回答的问题，只收有边的那几个等于把发现结果按连接数筛掉。 */
{
  const dDir = winDir(LATEST, 'delta');
  const add = (file, set) => {
    const p = path.join(dDir, file);
    if (!exists(p)) return;
    for (const it of rd(p).items ?? []) {
      if (!bornInTime(it, LATEST)) continue;
      set.add(overlayUid(DELTA_FILE_KIND[file], it.id, it.name_zh));
    }
  };
  add('new_jobs.json', jobIds);
  add('new_tasks.json', taskIds);
  add('new_skills.json', skillIds);
}

/** 叠层新实体的规范名、入场窗口与强度，供 overlay 节点补字段 */
const OVERLAY_META = new Map();
{
  const dDir = winDir(LATEST, 'delta');
  for (const f of ['new_jobs.json', 'new_tasks.json', 'new_skills.json', 'skillpoints.json']) {
    const p = path.join(dDir, f);
    if (!exists(p)) continue;
    for (const it of rd(p).items ?? []) {
      if (!bornInTime(it, LATEST)) continue;
      OVERLAY_META.set(overlayUid(DELTA_FILE_KIND[f], it.id, it.name_zh), {
        name: it.name_zh,
        nameEn: it.name_en ?? '',
        /* 入场窗口即该信号首次被论文或新闻提出的窗口，与基图条目的首现口径不同 */
        born: it.born_window ?? it.first_seen?.slice(0, 7) ?? LATEST,
        strength: r4(it.strength ?? 0),
        def: it.definition ?? it.description ?? '',
        /* 证据句留给岗位向量的推导（jobvec.mjs）：JD 一类的证据句里带着
           算法侧已抽出、但没有写成边的技能与任务名 */
        sentences: Object.values(it.evidence ?? {}).flatMap((v) => v.sentences ?? []),
      });
    }
  }
}
/* ---- 已转正新岗位的重列 ----

   算法侧只在一个岗位尚未写入体系时把它列进 new_jobs.json，写入之后便不再列出，
   末窗因而只剩一条仍在观测中的记录，页面上的新岗位队列只有一个。四十六窗里
   先后有十五个岗位被判为新岗位，其中七个已写进体系，在图上与既有岗位无异 ——
   而这七个的招聘信息条数至今为零：体系已收，市场未至，这正是"新岗位"要呈现的。

   emerging-rejoin.json 按体系编码指名其中几个，取其在数据包内的末次新岗位记录
   （入场窗口、前瞻强度、定义、证据句）重新归入叠层。重列只改归属与随之而来的
   三个字段，其任务与技能边一条未动 —— 那是体系给的关联，比按证据句推导的一份
   可靠。名单入库、取数仍在数据包内，重跑可得同一份产物。 */
const REJOIN_CODES = new Set();
{
  /* 逐窗升序扫过去，同名条目后来的记录覆盖先前的，末次记录即其转正前的定稿 */
  for (const w of WINDOWS.filter((x) => hasLayer(x, 'delta'))) {
    const p = path.join(winDir(w, 'delta'), 'new_jobs.json');
    if (!exists(p)) continue;
    for (const it of rd(p).items ?? []) {
      const code = REJOIN_BY_NAME.get(it.name_zh);
      if (!code || !bornInTime(it, w)) continue;
      REJOIN_CODES.add(code);
      OVERLAY_META.set(code, {
        name: it.name_zh,
        nameEn: it.name_en ?? '',
        born: it.born_window ?? it.first_seen?.slice(0, 7) ?? w,
        strength: r4(it.strength ?? 0),
        def: it.definition ?? it.description ?? '',
        sentences: Object.values(it.evidence ?? {}).flatMap((v) => v.sentences ?? []),
      });
    }
  }
  const missing = [...REJOIN_BY_NAME.values()].filter((c) => !REJOIN_CODES.has(c));
  if (missing.length) console.warn(`  重列名单中 ${missing.join('、')} 在数据包内无新岗位记录，已跳过`);
}

const isOverlay = (id) => OVERLAY_PREFIX.test(id) || REJOIN_CODES.has(id);
const overlayMeta = (id) => OVERLAY_META.get(id) ?? {};

const nodes = {
  jobs: [...jobIds].sort().map((id) => {
    const m = JOB_META.get(id) ?? {};
    return {
      id,
      name: m.name ?? overlayMeta(id).name ?? id,
      /* 重列的岗位不带一级归属：它们此刻列在新岗位队列里，同时又标着既有体系的
         归属，两件事互相拆台。与尚未转正的那一个一样，一律作"尚未归入体系"。 */
      cat: (() => {
        if (REJOIN_CODES.has(id)) return '';
        const c = catCodeOf(id, m.category);
        return c ? CAT_NAME.get(c) ?? c : '';
      })(),
      catCode: REJOIN_CODES.has(id) ? '' : catCodeOf(id, m.category),
      /* 招聘信息条数，岗位这一层唯一的实测计量；叠层新岗位尚无市场计量 */
      hits: m.hits ?? 0,
      /* 岗位在本窗的加权出现量，freq.json 的 jobs[].w */
      w: 0,
      origin: isOverlay(id) ? 'overlay' : 'base',
      ...(isOverlay(id)
        ? { born: overlayMeta(id).born, strength: overlayMeta(id).strength, def: overlayMeta(id).def }
        : {}),
    };
  }),
  tasks: [...taskIds].sort().map((id) => ({
    id,
    name: TASK_NAME.get(id) ?? overlayMeta(id).name ?? id,
    /* entity_freq 里的份额：该任务在全部岗位需求中的加权占比 */
    share: r6(entityFreq.tasks?.[id] ?? 0),
    origin: isOverlay(id) ? 'overlay' : 'base',
    ...(isOverlay(id)
      ? { born: overlayMeta(id).born, strength: overlayMeta(id).strength, def: overlayMeta(id).def }
      : {}),
  })),
  skills: [...skillIds].sort().map((id) => {
    const m = SKILL_META.get(id) ?? {};
    return {
      id,
      name: m.name ?? overlayMeta(id).name ?? id,
      group: m.group ?? '',
      groupCode: m.groupCode ?? '',
      dim: m.dim ?? '',
      dimCode: m.dimCode ?? '',
      type: m.type ?? 'hard',
      def: m.definition ?? '',
      share: r6(entityFreq.skills?.[id] ?? 0),
      origin: isOverlay(id) ? 'overlay' : 'base',
      ...(isOverlay(id)
        ? { born: overlayMeta(id).born, strength: overlayMeta(id).strength, def: overlayMeta(id).def }
        : {}),
    };
  }),
  /* 技能点是开放集合，名称本身即 id。基图技能点来自招聘信息统计，
     叠层技能点来自论文与新闻，尚未在招聘市场出现，以 origin 区分两者。 */
  skillpoints: (() => {
    const base = Object.entries(spDoc.skillpoints).map(([name, v]) => ({
      id: name,
      w: r4(v.weight),
      /* 首次出现的窗口，windows 数组按时间升序 */
      from: v.windows?.[0] ?? LATEST,
      n: v.windows?.length ?? 1,
      origin: 'base',
    }));
    const seen = new Set(base.map((s) => s.id));
    const dp = path.join(winDir(LATEST, 'delta'), 'skillpoints.json');
    const overlay = exists(dp)
      ? (rd(dp).items ?? [])
          .filter((it) => !seen.has(it.name_zh))
          .map((it) => ({
            id: it.name_zh,
            /* 叠层强度落在 [0,1]，与基图权重不同量纲，仅作排序用 */
            w: r4(it.strength ?? 0),
            from: it.born_window ?? LATEST,
            n: 1,
            origin: 'overlay',
            def: it.definition ?? it.description ?? '',
          }))
      : [];
    return [...base.sort((a, b) => b.w - a.w), ...overlay.sort((a, b) => b.w - a.w)];
  })(),
};

/* 岗位的加权出现量取自 freq.json */
{
  const freq = rd(path.join(latestBase, 'freq.json'));
  const byId = new Map(nodes.jobs.map((j) => [j.id, j]));
  for (const [id, v] of Object.entries(freq.freq.jobs ?? {})) {
    const j = byId.get(id);
    if (j) j.w = r4(v.w ?? 0);
  }
}

const buildInfo = rd(path.join(latestBase, 'build_info.json'));

/* ============================================================
   2b. 叠层新岗位的任务与技能向量（推导）

   叠层新岗位在四类边里一条也没有：算法侧的叠层关联边（delta/job_links.json，
   四十四个叠层窗合计 299 条、末窗 1 条）落在 delta 层，不进 effective 的四类边。
   岗位空间关系图要按任务构成算距离，零向量与任一岗位的余弦距离恒等于 1，
   这些点因而一直读不出落位。

   由已有字段推一份，算法与口径见 jobvec.mjs。产出单列在 inferred 一节，
   不并入 edges —— 四类边是实测，这一份是推导，混在一起会让口径失真。
   ============================================================ */

const overlayJobNodes = nodes.jobs.filter((j) => j.origin === 'overlay');
const inferred = overlayJobNodes.length
  ? inferOverlayJobVectors({
      overlayJobs: overlayJobNodes.map((j) => ({
        id: j.id,
        name: j.name,
        nameEn: overlayMeta(j.id).nameEn ?? '',
        def: j.def ?? '',
        sentences: overlayMeta(j.id).sentences ?? [],
      })),
      tasks: tasksDoc.tasks.map((t) => ({
        id: t.code,
        name: t.name_zh,
        nameEn: t.name_en,
        desc: t.description,
      })),
      skills: Object.entries(skillsDoc.detail).map(([code, s]) => ({
        id: code,
        name: s.name_zh,
        nameEn: s.name_en,
        def: s.definition,
      })),
      baseJobs: Object.entries(jobsDoc.detail).map(([code, j]) => ({
        id: code,
        name: j.name_zh,
        def: j.definition,
        keywords: j.keywords,
        boundary: j.boundary,
      })),
      /* 锚点扩写只读基图权重：合成权重里含前瞻修正，拿它扩写锚点再去定位
         前瞻岗位，等于让这批岗位的落位取决于它们自己带来的那部分修正 */
      jobTask: edges.jobTask.filter((e) => !OVERLAY_PREFIX.test(e.s) && e.w > 0),
      taskSkill: edges.taskSkill.filter((e) => !OVERLAY_PREFIX.test(e.s) && e.w > 0),
      skillPoints: (() => {
        const m = new Map();
        for (const e of [...edges.skillSkillpoint].sort((a, b) => b.w - a.w)) {
          const list = m.get(e.s);
          if (list) list.push(e.t);
          else m.set(e.s, [e.t]);
        }
        return m;
      })(),
    })
  : null;

if (inferred) {
  const m = Object.values(inferred.jobs);
  console.log(
    `\n叠层新岗位向量：${m.length} 个，定位 ${m.filter((x) => x.anchored).length} 个` +
      `（结构化信号 ${m.filter((x) => x.via === 'signal' && x.anchored).length}、` +
      `文本锚点 ${m.filter((x) => x.via === 'text' && x.anchored).length}），` +
      `推得 J-T ${inferred.jobTask.length} 条、J-S ${inferred.jobSkill.length} 条`,
  );
  for (const j of overlayJobNodes) {
    const x = inferred.jobs[j.id];
    console.log(
      `  ${j.id.padEnd(9)} ${j.name.padEnd(10)} ${x.anchored ? '定位' : '不定位'} ` +
        `via=${x.via} 信号${x.sigLines}条 相似度${x.topSim} 任务${x.nTasks}项 技能${x.nSkills}项`,
    );
  }
}

/* 末窗图谱分两份写出。

   技能点一层占全份的八成（节点两万余条、边三万余条），而首屏要显示的封面
   与首页读不到这一层：分开之后，两份可并行取回，浏览器的缓存与重取也按层
   各算一份 —— 上层三层的体量小、随体系变动，技能点一层大、随市场文本增长。
   前端在加载层重新并作一份，下游读到的形状与拆分前一致。 */
writeJson('graph.json', {
  window: LATEST,
  fingerprint: buildInfo.params_fingerprint,
  alpha: buildInfo.alpha,
  totalWeight: r4(buildInfo.total_weight),
  nodes: { jobs: nodes.jobs, tasks: nodes.tasks, skills: nodes.skills },
  edges: { jobTask: edges.jobTask, jobSkill: edges.jobSkill, taskSkill: edges.taskSkill },
  inferred,
});
writeJson('graph-skillpoints.json', {
  window: LATEST,
  nodes: nodes.skillpoints,
  edges: edges.skillSkillpoint,
});

/* ============================================================
   3. 月度序列：六窗逐月的量

   结构不随月份变，只有量在变。岗位、任务、技能三层给份额，
   技能点给权重与进入窗口，配合前端 PrismTimeline 的 series / demand。
   ============================================================ */

const series = {
  months: WINDOWS,
  /* 岗位：本窗加权出现量占全窗总量的比例 */
  jobs: {},
  /* 任务、技能：entity_freq 的份额，取值为该条目在全部需求中的加权占比 */
  tasks: {},
  skills: {},
  /* 技能点：逐窗权重。条目数近六千，只保留末窗权重前 1200 项的序列，
     其余条目在末窗权重已不足万分之一，逐月序列没有可读的分辨率 */
  skillpoints: {},
  /* 每窗的规模计数，用于时间轴上的体系规模读数 */
  counts: {},
};

const SP_SERIES_TOP = 1200;
const spTop = new Set(nodes.skillpoints.slice(0, SP_SERIES_TOP).map((s) => s.id));

for (const w of WINDOWS) {
  const b = winDir(w, 'base');
  const ef = rd(path.join(b, 'entity_freq.json'));
  const fq = rd(path.join(b, 'freq.json'));
  const sp = rd(path.join(b, 'skillpoints.json'));
  const bi = rd(path.join(b, 'build_info.json'));
  const wi = WINDOWS.indexOf(w);

  const total = fq.freq.total || 1;
  for (const [id, v] of Object.entries(fq.freq.jobs ?? {})) {
    (series.jobs[id] ??= new Array(WINDOWS.length).fill(null))[wi] = r6((v.w ?? 0) / total);
  }
  for (const [id, v] of Object.entries(ef.tasks ?? {})) {
    (series.tasks[id] ??= new Array(WINDOWS.length).fill(null))[wi] = r6(v);
  }
  for (const [id, v] of Object.entries(ef.skills ?? {})) {
    (series.skills[id] ??= new Array(WINDOWS.length).fill(null))[wi] = r6(v);
  }
  for (const [id, v] of Object.entries(sp.skillpoints ?? {})) {
    if (!spTop.has(id)) continue;
    (series.skillpoints[id] ??= new Array(WINDOWS.length).fill(null))[wi] = r4(v.weight);
  }

  series.counts[w] = {
    jdScanned: bi.scan?.n_scanned ?? 0,
    jdSampled: bi.scan?.n_sampled ?? 0,
    jobs: bi.scan?.n_jobs ?? 0,
    tasks: bi.n_tasks_seen ?? 0,
    skills: bi.n_skills_seen ?? 0,
    skillpoints: bi.n_skillpoints ?? 0,
    edges: bi.n_edges ?? {},
    totalWeight: r4(bi.total_weight ?? 0),
    droppedNearDup: bi.scan?.n_dropped_near_dup ?? 0,
    droppedNonIt: bi.scan?.n_dropped_non_it ?? 0,
  };
}

writeJson('series.json', series);

/* ============================================================
   3.5 逐窗的岗位构成

   每个岗位在各窗口的任务与技能构成，即岗位到任务、岗位到技能两类边
   按窗口切开后归一化的份额。能力年轮读它：同一个岗位六个窗口的构成并置，
   即可读出该岗位的能力要求在观测期内如何变化。

   全量六窗四类边约 6 MB，超出前端一次加载的合理范围。此处按岗位取
   权重前若干项，保留构成的主体：一个岗位在末窗平均关联 36 项技能、
   22 项任务，前 15 与前 12 项已覆盖其权重的九成以上。
   ============================================================ */

const RING_SKILL_TOP = 15;
const RING_TASK_TOP = 12;

const rings = { months: WINDOWS, jobs: {} };
for (const w of WINDOWS) {
  const js = (readEdges(w, 'base', 'job_skill') ?? []).filter((e) => validEdge('job_skill', e));
  const jt = (readEdges(w, 'base', 'job_task') ?? []).filter((e) => validEdge('job_task', e));
  const group = (edgeList, top) => {
    const by = new Map();
    for (const e of edgeList) {
      const l = by.get(e.src) ?? [];
      l.push([e.dst, e.weight]);
      by.set(e.src, l);
    }
    const out = new Map();
    for (const [src, list] of by) {
      const kept = list.sort((a, b) => b[1] - a[1]).slice(0, top);
      const sum = kept.reduce((a, x) => a + x[1], 0) || 1;
      out.set(src, kept.map(([dst, wt]) => [dst, r4(wt / sum)]));
    }
    return out;
  };
  const gs = group(js, RING_SKILL_TOP);
  const gt = group(jt, RING_TASK_TOP);
  for (const src of new Set([...gs.keys(), ...gt.keys()])) {
    const rec = (rings.jobs[src] ??= {});
    rec[w] = { skills: gs.get(src) ?? [], tasks: gt.get(src) ?? [] };
  }
}
writeJson('rings.json', rings);

/* ============================================================
   4. 叠层：前瞻信号

   新实体带定义、强度、入场窗口与证据；增强是论文与新闻对既有条目的提及。
   证据句逐条保留原文与来源，前端的证据链读它。
   ============================================================ */

const DELTA_WINDOWS = WINDOWS.filter((w) => hasLayer(w, 'delta'));

/* ---------------- 论文标题 ----------------

   证据表逐条只给来源文档的 arXiv 编号，不设标题字段。但抽取阶段留下的证据句里，
   有一类以"标题："起头，其内容即该文的标题；逐窗扫一遍即可按编号建起标题表。
   本批引用到的论文中约九成四由此得到标题，其余录于 paper-titles.json，
   取自 arXiv 官方接口，故构建过程不联网，仍为确定性转换。

   标题表另存一份，不写进各条证据：同一篇论文常同时支撑数十个条目，
   逐条重复一遍标题会让产物凭空长出数十倍于表本身的体积。 */
const TITLE_PREFIX = '标题：';
const paperTitleAll = new Map();

function harvestTitles(ev) {
  if (!ev) return;
  for (const [docId, v] of Object.entries(ev)) {
    if (paperTitleAll.has(docId)) continue;
    for (const s of v.sentences ?? []) {
      if (typeof s === 'string' && s.startsWith(TITLE_PREFIX)) {
        const t = s.slice(TITLE_PREFIX.length).trim();
        if (t) paperTitleAll.set(docId, t);
        break;
      }
    }
  }
}

const titleSupplement = (() => {
  const p = path.join(HERE, 'paper-titles.json');
  return exists(p) ? (rd(p).titles ?? {}) : {};
})();

function pickEvidence(ev, cap = 3) {
  if (!ev) return [];
  return Object.entries(ev)
    .slice(0, cap)
    .map(([docId, v]) => ({
      doc: docId,
      date: v.date ?? '',
      src: v.src ?? '',
      tier: v.tier ?? '',
      conf: v.confidence ?? '',
      /* 证据句截断到 220 字：完整原文在算法侧仓库，此处只保留可核对的锚点 */
      lines: (v.sentences ?? []).slice(0, 2).map((s) => (s.length > 220 ? s.slice(0, 220) + '…' : s)),
    }));
}

function readDeltaEntities(w, file, kind) {
  const p = path.join(winDir(w, 'delta'), file);
  if (!exists(p)) return [];
  const doc = rd(p);
  for (const it of doc.items ?? []) harvestTitles(it.evidence);
  return (doc.items ?? []).filter((it) => bornInTime(it, w)).map((it) => ({
    id: overlayUid(kind, it.id, it.name_zh),
    kind,
    name: it.name_zh,
    nameEn: it.name_en ?? '',
    def: it.definition ?? it.description ?? '',
    strength: r4(it.strength ?? 0),
    born: it.born_window ?? '',
    firstSeen: it.first_seen ?? '',
    lastSeen: it.last_seen ?? '',
    /* 算法侧给出的关联落点，落在基准体系的编码上。多数条目为空数组：
       一个刚被论文提出的信号，与既有体系的关联本身尚未确立 */
    relTasks: it.related_tasks ?? [],
    relSkills: it.related_skills ?? [],
    sources: it.sources ?? [],
    evidence: pickEvidence(it.evidence),
    nEvidence: Object.keys(it.evidence ?? {}).length,
  }));
}

/* 叠层记录逐窗累积：一条 2022-07 入场的信号在其后每一窗都再出现一次，
   定义、来源与证据句逐字重复。十七窗下来同一句证据在产物里存了十余遍，
   前端建证据表时又逐窗读一遍，同一句进表十余次。故按实体存一份静态记录，
   逐窗只留随窗变化的强度与来源。证据取末次出现的那份 —— 证据随窗累积，
   最后一次的集合最全。 */
const deltaEntities = {};
const deltaStrengthen = {};
const deltaByWindow = {};
for (const w of DELTA_WINDOWS) {
  const items = [
    ...readDeltaEntities(w, 'new_jobs.json', 'job'),
    ...readDeltaEntities(w, 'new_tasks.json', 'task'),
    ...readDeltaEntities(w, 'new_skills.json', 'skill'),
    ...readDeltaEntities(w, 'skillpoints.json', 'skillpoint'),
  ];
  const stPath = path.join(winDir(w, 'delta'), 'strengthenings.json');
  if (exists(stPath)) for (const it of rd(stPath).items ?? []) harvestTitles(it.evidence);
  const strengthenings = exists(stPath)
    ? (rd(stPath).items ?? []).map((it) => ({
        taxonomy: it.taxonomy,
        code: it.code,
        name: it.name_zh,
        strength: r4(it.strength ?? 0),
        sources: it.sources ?? [],
        firstSeen: it.first_seen ?? '',
        lastSeen: it.last_seen ?? '',
        evidence: pickEvidence(it.evidence, 2),
        nEvidence: Object.keys(it.evidence ?? {}).length,
      }))
    : [];
  const jlPath = path.join(winDir(w, 'delta'), 'job_links.json');
  const links = exists(jlPath)
    ? (rd(jlPath).edges ?? []).map((e) => ({
        s: e.src,
        sName: e.src_name,
        t: e.dst,
        tName: e.dst_name,
        rel: e.relation,
        w: r4(e.weight ?? 0),
      }))
    : [];
  /* 静态字段进实体表，逐窗只留 [键, 强度, 来源]。
     来源随窗变化（一条信号被招聘市场接住后由 papers 变为 jd+papers），
     三源序列按当窗来源分流，故不可提出去。 */
  const itemRefs = items.map((it) => {
    const key = `${it.kind}|${it.id}`;
    const { strength, sources, ...rest } = it;
    deltaEntities[key] = rest;
    return [key, strength, sources.join('+')];
  });
  const stRefs = strengthenings.map((st) => {
    const key = `${st.taxonomy}|${st.code}`;
    const { strength, sources, ...rest } = st;
    deltaStrengthen[key] = rest;
    return [key, strength, sources.join('+')];
  });
  deltaByWindow[w] = { items: itemRefs, strengthenings: stRefs, links };
}

/* 末窗的合成实体清单：叠层实体最终参与合成时的强度与状态 */
const newEntPath = path.join(winDir(LATEST, 'effective'), 'new_entities.json');
const NE_KIND = { new_jobs: 'job', new_tasks: 'task', new_skills: 'skill', skillpoints: 'skillpoint' };
const newEntities = exists(newEntPath)
  ? (rd(newEntPath).items ?? []).map((e) => ({
      id: overlayUid(NE_KIND[e.kind] ?? e.kind, e.id, e.name_zh),
      kind: NE_KIND[e.kind] ?? e.kind,
      name: e.name_zh,
      strength: r4(e.strength ?? 0),
      status: e.status,
      participates: !!e.participates,
      links: e.n_links ?? 0,
    }))
  : [];

/* 标题表只收进产物里实际引用到的那些论文：语料中的其余篇目没有落点，
   进表只是让产物变大。缺标题的编号一律不写空串 —— 界面据"表里有没有这一条"
   决定显示标题还是编号，写空串会让两者分不开。 */
const paperTitles = (() => {
  const used = new Set();
  for (const it of [...Object.values(deltaEntities), ...Object.values(deltaStrengthen)]) {
    for (const ev of it.evidence ?? []) if (ev.src === 'papers') used.add(ev.doc);
  }
  const out = {};
  let fromRaw = 0;
  let fromSupp = 0;
  for (const doc of [...used].sort()) {
    const raw = paperTitleAll.get(doc);
    const supp = titleSupplement[doc];
    if (raw) {
      out[doc] = raw;
      fromRaw++;
    } else if (supp) {
      out[doc] = supp;
      fromSupp++;
    }
  }
  console.log(
    `\n论文标题：引用 ${used.size} 篇，证据句内取得 ${fromRaw} 篇、补充表取得 ${fromSupp} 篇` +
      `${used.size - fromRaw - fromSupp > 0 ? `，仍缺 ${used.size - fromRaw - fromSupp} 篇` : ''}`,
  );
  return out;
})();

/* 叠层同样分两份写出：条目的定义、强度与逐窗读数是各图都要读的骨架，
   逐条证据原文只在证据链展开时才读到，而后者占全份的四成有余。
   两份的键一一对应，前端在加载层挂回原处。 */
const deltaEv = {};
for (const [k, e] of Object.entries(deltaEntities)) {
  if (e.evidence?.length) deltaEv[k] = e.evidence;
  delete e.evidence;
}
const strengthenEv = {};
for (const [k, e] of Object.entries(deltaStrengthen)) {
  if (e.evidence?.length) strengthenEv[k] = e.evidence;
  delete e.evidence;
}

writeJson('delta.json', {
  windows: DELTA_WINDOWS,
  baselineWindows: WINDOWS.filter((w) => !hasLayer(w, 'delta')),
  entities: deltaEntities,
  strengthenDefs: deltaStrengthen,
  byWindow: deltaByWindow,
  newEntities,
  /* 转正数为零是时序设计下的正常状态：最早一批信号 2022-07 入场，
     按设计一个信号从论文提出到市场付钱通常有一年以上时滞 */
  graduated: newEntities.filter((e) => e.status === 'graduated').length,
});
writeJson('delta-evidence.json', {
  entities: deltaEv,
  strengthenDefs: strengthenEv,
  paperTitles,
});

/* ============================================================
   5. 熟练度：各技能的 P1-P4/U 五档分布

   算法侧产出的 name_zh 写的是编码，此处补回中文名。
   六窗中每窗一份，可读出要求档位随时间的变化。
   ============================================================ */

const prof = { months: WINDOWS, rubric: '', byWindow: {} };
for (const w of WINDOWS) {
  const p = path.join(winDir(w, 'base'), 'skill_prof.json');
  if (!exists(p)) continue;
  const doc = rd(p);
  prof.rubric = doc.rubric_version ?? prof.rubric;
  prof.byWindow[w] = {
    nJds: doc.n_jds ?? 0,
    skills: Object.fromEntries(
      Object.entries(doc.skills ?? {}).map(([code, v]) => [
        code,
        {
          name: SKILL_META.get(code)?.name ?? code,
          n: r4(v.n ?? 0),
          levels: Object.fromEntries(Object.entries(v.levels ?? {}).map(([k, x]) => [k, r4(x)])),
        },
      ]),
    ),
  };
}
writeJson('prof.json', prof);

/* ============================================================
   6. 招聘信息汇总：逐条 JD 的多维统计聚合

   逐条明细共约 6 万行、47 MB，不入产物。此处按岗位与窗口聚合出
   薪资、级别、技术栈三个维度的分布，以及每条 JD 抽出的条目数。
   城市与学历两项汇总表未包含，前端相应分布仍为演示数据。
   ============================================================ */

const SUM_DIR = path.join(SRC, 'jd-summaries');
const SALARY_BANDS = [
  { name: '10k以下', lo: 0, hi: 10000 },
  { name: '10-20k', lo: 10000, hi: 20000 },
  { name: '20-30k', lo: 20000, hi: 30000 },
  { name: '30-50k', lo: 30000, hi: 50000 },
  { name: '50-70k', lo: 50000, hi: 70000 },
  { name: '70k以上', lo: 70000, hi: Infinity },
];

/** 逗号分隔且带引号包裹的一行，按 CSV 规则切分 */
function splitCsv(line) {
  const out = [];
  let cur = '';
  let q = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (q) {
      if (c === '"') {
        if (line[i + 1] === '"') { cur += '"'; i++; } else q = false;
      } else cur += c;
    } else if (c === '"') q = true;
    else if (c === ',') { out.push(cur); cur = ''; }
    else cur += c;
  }
  out.push(cur);
  return out;
}

const jdStats = { months: [], byJob: {}, overall: {}, skillpointProf: {}, profBands: [], levels: { n: {}, skills: {} } };

/* 熟练度五档。P1 至 P4 为要求的深度，U 为原文点到该项但未写明深度 */
const PROF_BANDS = ['P1', 'P2', 'P3', 'P4', 'U'];
/** 每个岗位每一职级保留的技能项数 */
const LEVEL_SKILL_TOP = 20;
/** 技能点进入档位分布的最小样本量 */
const SP_PROF_MIN = 20;
/** 技能点的档位分布，键为技能点名，值为五档计数 */
const spProf = new Map();

/* 全样本的职级 × 技能，不按岗位切也不截尾。按岗位切的那一份每档只留权重
   前若干项，用来算职级倾斜会把尾部一并算没；全站的职级系数读这一份。 */
const levelSkills = {};
const levelN = {};

/** "技能名:P3;技能名:U" → Map(技能名 → 档位) */
function parseProf(s) {
  const m = new Map();
  if (!s) return m;
  for (const seg of s.split(';')) {
    const i = seg.lastIndexOf(':');
    if (i < 0) continue;
    const band = seg.slice(i + 1).trim();
    if (!PROF_BANDS.includes(band)) continue;
    m.set(seg.slice(0, i).trim(), band);
  }
  return m;
}

/** "技能名:点A,点B;技能名:点C" → [技能名, [技能点…]][] */
function parseSpMap(s) {
  const out = [];
  if (!s) return out;
  for (const seg of s.split(';')) {
    const i = seg.indexOf(':');
    if (i < 0) continue;
    const pts = seg
      .slice(i + 1)
      .split(',')
      .map((x) => x.trim())
      .filter(Boolean);
    if (pts.length) out.push([seg.slice(0, i).trim(), pts]);
  }
  return out;
}

if (exists(SUM_DIR)) {
  const files = fs.readdirSync(SUM_DIR).filter((f) => f.endsWith('.csv')).sort();
  const overall = {
    n: 0,
    salaryBands: {},
    levels: {},
    stacks: {},
    salarySum: 0,
    salaryN: 0,
    salaries: [],
  };
  for (const f of files) {
    const w = f.match(/(\d{4}-\d{2})/)?.[1] ?? f;
    jdStats.months.push(w);
    const text = fs.readFileSync(path.join(SUM_DIR, f), 'utf8');
    const lines = text.split(/\r?\n/);
    const header = splitCsv(lines[0].replace(/^﻿/, ''));
    const ix = Object.fromEntries(header.map((h, i) => [h, i]));
    for (let i = 1; i < lines.length; i++) {
      if (!lines[i]) continue;
      const c = splitCsv(lines[i]);
      const job = c[ix.std_job];
      if (!job) continue;
      const rec = (jdStats.byJob[job] ??= {
        n: 0,
        byWindow: {},
        salaryBands: {},
        levels: {},
        stacks: {},
        salarySum: 0,
        salaryN: 0,
        salaries: [],
        nSkills: 0,
        nTasks: 0,
        nSkillpoints: 0,
        /* 职级 → 该职级下各技能被要求的条数，以及该职级的条目总数。
           界面上的“按职级分档的能力要求”读它 */
        levelSkills: {},
        levelN: {},
        /* 技能编码 → 五档计数。算法侧的熟练度分布产出到技能一层且不分岗位，
           此处按岗位切开，供“某岗位对某技能要求到什么程度”一问 */
        skillProf: {},
      });
      rec.n++;
      overall.n++;
      rec.byWindow[w] = (rec.byWindow[w] ?? 0) + 1;

      const sal = Number(c[ix.salary_monthly]);
      if (Number.isFinite(sal) && sal > 0) {
        const band = SALARY_BANDS.find((b) => sal >= b.lo && sal < b.hi);
        if (band) {
          rec.salaryBands[band.name] = (rec.salaryBands[band.name] ?? 0) + 1;
          overall.salaryBands[band.name] = (overall.salaryBands[band.name] ?? 0) + 1;
        }
        rec.salarySum += sal;
        rec.salaryN++;
        rec.salaries.push(sal);
        overall.salarySum += sal;
        overall.salaryN++;
        overall.salaries.push(sal);
      }
      const lv = c[ix.level];
      if (lv) {
        rec.levels[lv] = (rec.levels[lv] ?? 0) + 1;
        overall.levels[lv] = (overall.levels[lv] ?? 0) + 1;
      }
      const st = c[ix.techstack];
      if (st) {
        rec.stacks[st] = (rec.stacks[st] ?? 0) + 1;
        overall.stacks[st] = (overall.stacks[st] ?? 0) + 1;
      }
      rec.nSkills += Number(c[ix.n_skills]) || 0;
      rec.nTasks += Number(c[ix.n_tasks]) || 0;
      rec.nSkillpoints += Number(c[ix.n_skillpoints]) || 0;

      /* ---- 职级 × 能力 ----
         逐条 JD 带一个职级与一组能力要求，按职级切开即得各档的能力构成。
         职级列约五成半有值，无值的条目不进入本维，见 manifest.levelCoverage */
      const names = (c[ix.skill_vec_01] ?? '').split('|').map((x) => x.trim()).filter(Boolean);
      if (lv) {
        rec.levelN[lv] = (rec.levelN[lv] ?? 0) + 1;
        const bag = (rec.levelSkills[lv] ??= {});
        levelN[lv] = (levelN[lv] ?? 0) + 1;
        const gbag = (levelSkills[lv] ??= {});
        for (const nm of names) {
          const code = SKILL_CODE_BY_NAME.get(nm);
          if (!code) continue;
          bag[code] = (bag[code] ?? 0) + 1;
          gbag[code] = (gbag[code] ?? 0) + 1;
        }
      }

      /* ---- 熟练度 ----
         技能一层的档位逐条给出；技能点一层的档位按同一条 JD 内其父技能的档位归属，
         即“这条招聘信息把该技能要求到 P3，并在该技能下点名了这几个技能点”。
         此为推导量而非独立观测，前端按 derived 登记 */
      const prof = parseProf(c[ix.skill_vec_prof]);
      for (const [nm, band] of prof) {
        const code = SKILL_CODE_BY_NAME.get(nm);
        if (!code) continue;
        const row = (rec.skillProf[code] ??= {});
        row[band] = (row[band] ?? 0) + 1;
      }
      for (const [nm, pts] of parseSpMap(c[ix.skillpoint_map])) {
        const band = prof.get(nm);
        if (!band) continue;
        for (const pt of pts) {
          const row = spProf.get(pt) ?? (spProf.set(pt, {}), spProf.get(pt));
          row[band] = (row[band] ?? 0) + 1;
        }
      }
    }
  }

  const median = (a) => {
    if (!a.length) return 0;
    const s = a.slice().sort((x, y) => x - y);
    const m = s.length >> 1;
    return s.length % 2 ? s[m] : Math.round((s[m - 1] + s[m]) / 2);
  };

  for (const rec of Object.values(jdStats.byJob)) {
    rec.medianSalary = median(rec.salaries);
    rec.avgSkills = rec.n ? r4(rec.nSkills / rec.n) : 0;
    rec.avgTasks = rec.n ? r4(rec.nTasks / rec.n) : 0;
    rec.avgSkillpoints = rec.n ? r4(rec.nSkillpoints / rec.n) : 0;
    delete rec.salaries;
    delete rec.salarySum;
    delete rec.nSkills;
    delete rec.nTasks;
    delete rec.nSkillpoints;
    /* 每档只留权重前若干项：一个岗位在一档下平均命中三十余项技能，
       尾部多为个位数，进入产物后按岗位数与档数两重放大 */
    for (const [lvName, bag] of Object.entries(rec.levelSkills)) {
      const kept = Object.entries(bag)
        .sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))
        .slice(0, LEVEL_SKILL_TOP);
      rec.levelSkills[lvName] = Object.fromEntries(kept);
    }
  }
  /* 技能点的档位分布只保留样本量足够的那一部分：本批共上万项技能点，
     其中多数只在个位数条招聘信息里出现过，五档分布读不出分布 */
  jdStats.profBands = PROF_BANDS;
  jdStats.skillpointProf = Object.fromEntries(
    [...spProf.entries()]
      .map(([k, v]) => [k, v, PROF_BANDS.reduce((a, b) => a + (v[b] ?? 0), 0)])
      .filter(([, , n]) => n >= SP_PROF_MIN)
      .sort((a, b) => b[2] - a[2])
      .map(([k, v]) => [k, PROF_BANDS.map((b) => v[b] ?? 0)]),
  );
  /* 全样本的职级 × 技能。skills 为该职级的招聘信息中提及该技能的条数，
     n 为该职级的条目总数，两者相除即提及率，可跨档比较 */
  jdStats.levels = { n: levelN, skills: levelSkills };
  jdStats.overall = {
    n: overall.n,
    salaryBands: overall.salaryBands,
    levels: overall.levels,
    stacks: overall.stacks,
    medianSalary: median(overall.salaries),
  };
}

writeJson('jdstats.json', jdStats);

/* ============================================================
   6.5 岗位定义的三项要素：必备技能、加分技能、典型应用场景

   算法侧的岗位体系只给名称与定义，五要素里的核心职责由该岗位承担的任务给出，
   其余三项此前留空。三项在本节由汇总表已有的两列推出，不引入外部判断：

   · 覆盖率  该岗位的招聘信息中提到某项技能的条数占比（skill_vec_01）。
   · 熟练度  同一批条目中写明程度词的那一部分的平均档位，P1 至 P4 记 1 至 4
             （skill_vec_prof）。未写明程度（U）的不计入均值 —— 把它摊进档位里，
             等于把"没写"说成"要求不高"。

   必备技能取覆盖率高的那一批：三成以上的招聘信息都提到，即为这个岗位的通例。
   加分技能取另一头：提的企业不多，可一旦提到，要求的熟练度反而高于必备技能的
   平均水平 —— 这正是"少数岗位额外要求、写进简历能加分"的那一类。加分一侧另设
   样本量下限，否则几十条里出现两三次的偶发项会因均值不稳而排到前面。

   典型应用场景取汇总表的技术栈一列：该列逐条标注这条招聘信息属于哪些技术方向，
   按岗位聚合后即"这个岗位实际投放在哪些方向上"。一条招聘信息可同时标注多个
   方向，故各项占比之和大于一，界面上写明这一点。
   ============================================================ */

const PROF_WEIGHT = { P1: 1, P2: 2, P3: 3, P4: 4 };
/** 必备技能的覆盖率门槛与条数上限 */
const MUST_COV = 0.3;
const MUST_TOP = 8;
/** 覆盖率无一项过门槛时的退让门槛，与条数上限 */
const MUST_COV_LOW = 0.15;
const MUST_TOP_LOW = 5;
/** 加分技能的样本量下限（取绝对条数与岗位条数的百分之一中的大者）与条数上限 */
const PLUS_MIN_N = 20;
const PLUS_MIN_RATE = 0.005;
const PLUS_TOP = 6;
/** 典型应用场景取前若干个技术方向 */
const SCENE_TOP = 4;

const jobDef = {};
for (const j of nodes.jobs) {
  const rec = jdStats.byJob[j.name];
  if (!rec || !rec.n) continue;

  const rows = Object.entries(rec.skillProf ?? {}).map(([code, c]) => {
    const all = Object.values(c).reduce((a, b) => a + b, 0);
    let graded = 0;
    let sum = 0;
    for (const [k, w] of Object.entries(PROF_WEIGHT)) {
      graded += c[k] ?? 0;
      sum += w * (c[k] ?? 0);
    }
    return {
      code,
      name: SKILL_META.get(code)?.name ?? code,
      cov: r4(all / rec.n),
      n: all,
      graded,
      lvl: graded ? r4(sum / graded) : 0,
    };
  });

  const byCov = rows.slice().sort((a, b) => b.cov - a.cov);
  let must = byCov.filter((r) => r.cov >= MUST_COV).slice(0, MUST_TOP);
  if (!must.length) must = byCov.filter((r) => r.cov >= MUST_COV_LOW).slice(0, MUST_TOP_LOW);
  const mustCodes = new Set(must.map((r) => r.code));
  /* 加分的判据是"比必备还要求得熟练"，故门槛取必备一批的平均档位 */
  const mustLvl = must.length ? must.reduce((a, r) => a + r.lvl, 0) / must.length : 0;
  const minN = Math.max(PLUS_MIN_N, Math.round(rec.n * PLUS_MIN_RATE));
  const plus = rows
    .filter((r) => !mustCodes.has(r.code) && r.graded >= minN && r.lvl > mustLvl)
    .sort((a, b) => b.lvl - a.lvl || b.cov - a.cov)
    .slice(0, PLUS_TOP);

  /* 技术栈一列写作以竖线分隔的组合，逐项拆开后按条数计 */
  const stackN = new Map();
  for (const [combo, n] of Object.entries(rec.stacks ?? {})) {
    for (const one of combo.split('|')) {
      const k = one.trim();
      if (k) stackN.set(k, (stackN.get(k) ?? 0) + n);
    }
  }
  const scenarios = [...stackN.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, SCENE_TOP)
    .map(([name, n]) => ({ name, n, share: r4(n / rec.n) }));

  jobDef[j.id] = {
    n: rec.n,
    must: must.map((r) => ({ code: r.code, name: r.name, cov: r.cov, lvl: r.lvl, n: r.n })),
    plus: plus.map((r) => ({ code: r.code, name: r.name, cov: r.cov, lvl: r.lvl, n: r.graded })),
    scenarios,
  };
}
/* ---- 叠层新岗位 ----

   新岗位尚无招聘投放，上面那套覆盖率与熟练度无从谈起。三项改由已有的两处推得，
   逐项在产物里记 via='inferred'，界面上另行标注：

   · 必备与加分  取 jobvec 推导出的技能构成（graph.json 的 inferred.jobSkill，
                  权重已按该岗位最高项归一）。权重居前的一批读作这个岗位立得住
                  必须具备的能力，其后一段读作有则更好的能力；权重再低的不列。
   · 典型应用场景 取能力构成最相近的若干既有岗位所投放的技术方向，按相似度加权。
                  一个尚未被市场确证的岗位，其应用方向只能由市场上与它最像的
                  那几个岗位来回答。 */
const NEW_MUST_W = 0.5;
const NEW_PLUS_W = 0.25;
const NEW_PEERS = 5;
/* 直接信号与任务一路的配比。直接信号只有十项且几乎全是通用素养 ——
   论文与新闻谈一个新岗位时谈的多是"要能持续学习、要能沟通"；
   任务一路是招聘市场对这些任务的实测要求，故取较大的一份 */
const DIRECT_W = 0.3;

if (inferred && inferred.jobSkill.length) {
  /* 既有岗位的技能覆盖率向量，与新岗位的推导构成在同一批技能编码上比 */
  const baseVec = new Map();
  for (const j of nodes.jobs) {
    if (j.origin === 'overlay') continue;
    const rec = jdStats.byJob[j.name];
    if (!rec || !rec.n) continue;
    const v = new Map();
    for (const [code, c] of Object.entries(rec.skillProf ?? {})) {
      v.set(code, Object.values(c).reduce((a, b) => a + b, 0) / rec.n);
    }
    if (v.size) baseVec.set(j.id, v);
  }
  const cos = (a, b) => {
    let dot = 0;
    let na = 0;
    let nb = 0;
    for (const [k, x] of a) {
      na += x * x;
      const y = b.get(k);
      if (y) dot += x * y;
    }
    for (const [, y] of b) nb += y * y;
    return na > 0 && nb > 0 ? dot / Math.sqrt(na * nb) : 0;
  };

  /* 任务一头要求的能力：叠层证据里直接点到的技能以通用素养居多（论文与新闻
     谈的多是"这个新岗位要能持续学习、要能沟通"），只据此列必备技能，
     整张卡片会全是软技能。故与"该岗位承担的任务反过来要求的能力"合起来算：
     后者沿 T-S 边取，是招聘市场对这些任务的实测要求。两路各占一半。 */
  const tsOf = new Map();
  for (const e of edges.taskSkill) {
    const arr = tsOf.get(e.s) ?? [];
    arr.push(e);
    tsOf.set(e.s, arr);
  }
  const taskOfNew = new Map();
  for (const e of inferred.jobTask) {
    const m = taskOfNew.get(e.s) ?? new Map();
    m.set(e.t, Math.max(m.get(e.t) ?? 0, e.w));
    taskOfNew.set(e.s, m);
  }

  const skillOfNew = new Map();
  for (const e of inferred.jobSkill) {
    const m = skillOfNew.get(e.s) ?? new Map();
    m.set(e.t, Math.max(m.get(e.t) ?? 0, e.w));
    skillOfNew.set(e.s, m);
  }
  const normMax = (m) => {
    let mx = 0;
    for (const v of m.values()) if (v > mx) mx = v;
    if (mx > 0) for (const [k, v] of m) m.set(k, v / mx);
    return m;
  };
  for (const [jobId, tasks] of taskOfNew) {
    const viaTask = new Map();
    for (const [tid, w1] of tasks) {
      for (const e2 of tsOf.get(tid) ?? []) {
        viaTask.set(e2.t, (viaTask.get(e2.t) ?? 0) + w1 * e2.e);
      }
    }
    normMax(viaTask);
    const direct = normMax(skillOfNew.get(jobId) ?? new Map());
    const merged = new Map();
    for (const k of new Set([...direct.keys(), ...viaTask.keys()])) {
      merged.set(k, DIRECT_W * (direct.get(k) ?? 0) + (1 - DIRECT_W) * (viaTask.get(k) ?? 0));
    }
    skillOfNew.set(jobId, normMax(merged));
  }

  for (const [jobId, vec] of skillOfNew) {
    const rows = [...vec.entries()]
      .map(([code, w]) => ({ code, name: SKILL_META.get(code)?.name ?? code, w: r4(w) }))
      .sort((a, b) => b.w - a.w);

    const peers = [...baseVec.entries()]
      .map(([id, v]) => ({ id, sim: cos(vec, v) }))
      .sort((a, b) => b.sim - a.sim)
      .slice(0, NEW_PEERS)
      .filter((x) => x.sim > 0);

    const stackW = new Map();
    let simSum = 0;
    for (const p of peers) {
      const rec = jdStats.byJob[nodes.jobs.find((j) => j.id === p.id)?.name ?? ''];
      if (!rec || !rec.n) continue;
      simSum += p.sim;
      for (const [combo, n] of Object.entries(rec.stacks ?? {})) {
        for (const one of combo.split('|')) {
          const k = one.trim();
          if (k) stackW.set(k, (stackW.get(k) ?? 0) + (p.sim * n) / rec.n);
        }
      }
    }
    const scenarios = [...stackW.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, SCENE_TOP)
      .map(([name, w]) => ({ name, share: r4(simSum ? w / simSum : 0) }));

    jobDef[jobId] = {
      via: 'inferred',
      n: 0,
      must: rows
        .filter((r) => r.w >= NEW_MUST_W)
        .slice(0, MUST_TOP)
        .map((r) => ({ code: r.code, name: r.name, w: r.w })),
      plus: rows
        .filter((r) => r.w >= NEW_PLUS_W && r.w < NEW_MUST_W)
        .slice(0, PLUS_TOP)
        .map((r) => ({ code: r.code, name: r.name, w: r.w })),
      scenarios,
      peers: peers.map((p) => ({ id: p.id, sim: r4(p.sim) })),
    };
  }
}

writeJson('jobdef.json', {
  window: LATEST,
  method: 'coverage+proficiency@jd_summary',
  params: {
    mustCoverage: MUST_COV,
    mustTop: MUST_TOP,
    plusMinSamples: PLUS_MIN_N,
    plusMinRate: PLUS_MIN_RATE,
    plusTop: PLUS_TOP,
    sceneTop: SCENE_TOP,
    newMustWeight: NEW_MUST_W,
    newPlusWeight: NEW_PLUS_W,
    newPeers: NEW_PEERS,
  },
  jobs: jobDef,
});
console.log(
  `\n岗位定义三要素：${Object.keys(jobDef).length} 个岗位，` +
    `必备平均 ${(Object.values(jobDef).reduce((a, v) => a + v.must.length, 0) / Math.max(Object.keys(jobDef).length, 1)).toFixed(1)} 项、` +
    `加分平均 ${(Object.values(jobDef).reduce((a, v) => a + v.plus.length, 0) / Math.max(Object.keys(jobDef).length, 1)).toFixed(1)} 项、` +
    `场景平均 ${(Object.values(jobDef).reduce((a, v) => a + v.scenarios.length, 0) / Math.max(Object.keys(jobDef).length, 1)).toFixed(1)} 项`,
);


/* ============================================================
   6.5 招聘原文：城市、企业与逐条摘录

   汇总表未含城市与企业两列，逐条原文另存于算法侧的时间线目录。
   该目录逐窗数十万行、合计四吉字节，不进本脚本，由 jdraw.mjs 先行
   聚合成中间件；此处只作透传与体量核对。中间件缺席时本节不产出，
   相应维度仍记入 absent。
   ============================================================ */

const JDRAW_SRC = path.join(SRC, 'jdraw.json');
const jdRaw = exists(JDRAW_SRC) ? rd(JDRAW_SRC) : null;
if (jdRaw) writeJson('jdraw.json', jdRaw);

/* ============================================================
   7. 清单：来源、口径与规模

   前端据此判定各字段通道的来源等级，并在界面上交代口径。
   ============================================================ */

const manifest = {
  schema: '1.0',
  generatedAt: new Date().toISOString().slice(0, 19) + 'Z',
  source: {
    repo: 'Pyecv/Challenge26',
    package: path.basename(SRC),
    fingerprint: buildInfo.params_fingerprint,
    createdBy: buildInfo.created ?? '',
  },
  taxonomy: {
    jobs: jobsDoc.version,
    tasks: tasksDoc.version,
    skills: skillsDoc.version,
  },
  windows: WINDOWS,
  latest: LATEST,
  deltaWindows: DELTA_WINDOWS,
  counts: {
    jobs: nodes.jobs.length,
    jobsInTaxonomy: Object.keys(jobsDoc.detail).length,
    tasks: nodes.tasks.length,
    skills: nodes.skills.length,
    skillpoints: nodes.skillpoints.length,
    /* 叠层条目按层分开计数。封面页只读本清单出数，不再为四个数把整份图谱拉下来 */
    overlay: {
      jobs: nodes.jobs.filter((x) => x.origin === 'overlay').length,
      tasks: nodes.tasks.filter((x) => x.origin === 'overlay').length,
      skills: nodes.skills.filter((x) => x.origin === 'overlay').length,
      skillpoints: nodes.skillpoints.filter((x) => x.origin === 'overlay').length,
    },
    edges: Object.fromEntries(Object.entries(edges).map(([k, v]) => [k, v.length])),
    deltaEntities: newEntities.length,
    jdSampled: Object.values(series.counts).reduce((a, c) => a + c.jdSampled, 0),
    jdSummaryRows: jdStats.overall.n ?? 0,
    jdRawRows: jdRaw?.overall.rows ?? 0,
    jdRawMatched: jdRaw?.overall.matched ?? 0,
    cities: jdRaw?.overall.nCities ?? 0,
    companies: jdRaw?.overall.nCompanies ?? 0,
    skillpointProf: Object.keys(jdStats.skillpointProf).length,
  },
  /* 本批数据未包含的维度。界面上凡读到这些字段一律标演示数据。
     城市、企业与学历三项由原文表补上后退出本清单：前两项直接取自原文表的
     place 与 company 两列，学历一列原文表虽有其名整批为空，改由正文的门槛语
     抽出，记 derived 而非 absent，故与前两项同随中间件进退 */
  absent: [
    '企业类别分布',
    ...(jdRaw ? [] : ['城市分布', '招聘原文摘录', '学历分布']),
  ],
  /* 端点校验剔除的边数，按关系分。基图为零，剔除全部发生在合成层 */
  dropped,
  /* 叠层输入的两处校正，见 1.5 节 */
  overlayGuards: {
    /* 因编号被另一实体占用而改号的条目数 */
    renumbered: overlayRenumbered,
    /* 因窗口早于入场窗口而剔除的条目出现次数 */
    preBornDropped: overlayPreBorn,
  },
  /* 序列截断说明：技能点逐月序列只覆盖末窗权重前 N 项 */
  seriesSkillpointTop: SP_SERIES_TOP,
  /* 职级列的非空比例。按职级分档的能力要求只统计有职级的那一部分 */
  levelCoverage: (() => {
    const withLv = Object.values(jdStats.byJob).reduce(
      (a, r) => a + Object.values(r.levelN ?? {}).reduce((x, y) => x + y, 0),
      0,
    );
    return jdStats.overall.n ? r4(withLv / jdStats.overall.n) : 0;
  })(),
  /* 算法侧另给出 2022-12、2023-01 两窗，两窗的 base/ 逐字节相同，freq.json 的
     window 字段均写作 2022-11，且无对应的汇总表，系原文表缺这两个月时的结转。
     本批两窗的 build_info 停在上一轮（2026-08-30），未随其余各窗重算，
     故与本轮的 2022-11 亦不再逐字节相同 —— 结转的判据不在此，
     在于二者同为一份 2022-11 的复制、无独立观测可依。结转窗进入月度序列
     会成为两个凭空的持平月，故不接入；两窗均在数据包内，需要时可单独取用 */
  windowsExcluded: [
    { window: '2022-12', reason: '基图为 2022-11 的结转，非独立观测' },
    { window: '2023-01', reason: '基图为 2022-11 的结转，非独立观测' },
  ],
  /* 窗口序列的断档。前端的时间轴按窗口序号等距排布，此处记明实际的月份跳跃 */
  windowGaps: WINDOWS.flatMap((w, i) => {
    if (i === 0) return [];
    const [y0, m0] = WINDOWS[i - 1].split('-').map(Number);
    const [y1, m1] = w.split('-').map(Number);
    const d = (y1 - y0) * 12 + (m1 - m0);
    return d > 1 ? [{ after: WINDOWS[i - 1], before: w, months: d - 1 }] : [];
  }),
};


/* ============================================================
   6.6 下一季度的能力构成预测

   算法侧另有一路时序预测（Chronos-2 零样本，见 0903-Challenge-thm/code/）：
   以各（岗位, 技能）的季度份额序列为目标，做两遍 2026-Q3 的预测 ——

     p50_uni  只用份额序列自身的单变量预测
     p50_cov  另把该技能当季的论文与新闻情报占比作为仅过去协变量加入后的预测
     signal   两者之差。为正即"把论文与新闻算进来之后，这一项的下季占比被上调"，
              这正是本系统所称的前瞻信号在预测一侧的落点

   逐条按岗位归拢后写成一份产物：既有岗位能力动态页据此在能力年轮外圈画一环
   预测环，并列出下一季度进退最大的若干项。

   输入是一份独立的 CSV（不在图谱数据包内），随算法侧另行交付，
   置于 data-pipeline/forecast/ 下；缺席时本节不产出，界面上相应一块不画。
   ============================================================ */

const FORECAST_CSV = path.join(APP, 'data-pipeline', 'forecast', 'forecast_2026_Q3_skill_share_cov.csv');
if (exists(FORECAST_CSV)) {
  const raw = fs.readFileSync(FORECAST_CSV, 'utf8').replace(/^\uFEFF/, '');
  const lines = raw.split(/\r?\n/).filter((l) => l.trim());
  const head = lines[0].split(',');
  const ix = Object.fromEntries(head.map((h, i) => [h.trim(), i]));
  /* 名称 → 编码。CSV 按中文名给岗位与技能，产物一律按编码索引 */
  const jobCodeOf = new Map(nodes.jobs.map((j) => [j.name, j.id]));
  const skillCodeOf = new Map([...SKILL_META.entries()].map(([code, m]) => [m.name, code]));

  const byJob = {};
  let quarter = '';
  let unmatched = 0;
  for (let i = 1; i < lines.length; i++) {
    const c = lines[i].split(',');
    quarter = quarter || (c[ix.forecast_quarter] ?? '').trim();
    const jobId = jobCodeOf.get((c[ix.job] ?? '').trim());
    const skillCode = skillCodeOf.get((c[ix.skill] ?? '').trim());
    if (!jobId || !skillCode) {
      unmatched++;
      continue;
    }
    const uni = Number(c[ix.p50_uni]);
    const cov = Number(c[ix.p50_cov]);
    if (!Number.isFinite(uni) || !Number.isFinite(cov)) continue;
    (byJob[jobId] ??= []).push([
      skillCode,
      r6(uni),
      r6(cov),
      r6(cov - uni),
      r6(Number(c[ix.last_share]) || 0),
      Number(c[ix.n_hist_quarters]) || 0,
    ]);
  }
  for (const arr of Object.values(byJob)) arr.sort((a, b) => b[2] - a[2]);

  writeJson('forecast.json', {
    quarter,
    method: 'chronos-2 zero-shot · 单变量 vs 论文新闻协变量',
    /* 逐条为 [技能编码, 单变量预测, 协变量预测, 前瞻信号, 上季实测份额, 历史季度数] */
    fields: ['skill', 'uni', 'cov', 'signal', 'lastShare', 'nHist'],
    jobs: byJob,
  });
  console.log(
    `\n下一季度预测：${quarter}，${Object.keys(byJob).length} 个岗位、` +
      `${Object.values(byJob).reduce((a, v) => a + v.length, 0)} 条（岗位，技能）` +
      `${unmatched ? `，${unmatched} 条名称对不上体系已跳过` : ''}`,
  );
}

writeJson('manifest.json', manifest);

const total = fs
  .readdirSync(OUT)
  .reduce((a, f) => a + fs.statSync(path.join(OUT, f)).size, 0);
console.log(`\n合计 ${(total / 1024 / 1024).toFixed(2)} MB`);
console.log(`岗位 ${manifest.counts.jobs}/${manifest.counts.jobsInTaxonomy} · 任务 ${manifest.counts.tasks} · 技能 ${manifest.counts.skills} · 技能点 ${manifest.counts.skillpoints}`);
console.log(`边 ${Object.entries(manifest.counts.edges).map(([k, v]) => `${k} ${v}`).join(' · ')}`);
