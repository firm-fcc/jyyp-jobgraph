/* ============================================================
   叠层新岗位的任务与技能向量

   ------------------------------------------------------------
   要补的是什么

   岗位空间关系图问的是“新发现的岗位落在已有岗位体系的什么位置”，
   答案由一句话给出：把全部岗位放进同一个任务向量空间，两两算余弦距离。
   既有岗位的向量取自实测的 J-T 边；叠层新岗位在这四类边里一条也没有
   （算法侧的 delta/job_links.json 四十四个叠层窗合计 299 条、末窗 1 条，
   落在 delta 层不进 effective），向量因而是零向量。

   零向量与任一单位向量的余弦距离恒等于 1。照常排序取前三，得到的是三个
   并列 1.000 的“相近岗位”——把“测不出”报成了“测出来是最远”。图上这几个
   点因此一直不给相近岗位清单。

   算法侧尚未产出这一层关联。本模块由已有字段推得一份，供该图取用。

   ------------------------------------------------------------
   两条路径，按证据形态分流

   ① 结构化信号。叠层条目的 JD 类证据句写作
        「JD标题：卫星测试工程师｜正文信号：技能：数据分析与可视化、……；
          任务：系统运维、数据分析、生产环境测试；技术栈：TS-01」
      冒号后的名称即体系内的规范名，逐条解析即得一份计数。这一路径不作
      任何语义推断，只是把算法侧已抽出、但没有写成边的那份信号读出来。

   ② 文本锚点。只有论文或新闻证据的条目没有上述信号，改由文本相似度定位：
      给每项任务、每项技能建一份锚点文本，与该岗位的定义及证据句算余弦。

      锚点不止取体系文件里的那一句描述 —— 一句话二十来字，与新岗位的
      定义匹配时命中的多是“开发”“系统”这类到处都有的词。故沿图结构扩写：
      任务的锚点并入承担它最多的那几个岗位的技术关键词与它要求最重的
      那几项技能名，技能的锚点并入它下面权重最高的那些技能点名。
      扩写用的全是图内已有的边，不引入外部词表。

   两路径都命中时以结构化信号为主：它是从招聘原文里抽出来的读数，
   文本锚点是相似度推断，两者不同级。

   ------------------------------------------------------------
   这份向量不是实测

   产出写在 graph.json 的 inferred 一节，不混进 edges。四类边是实测，
   这一份是推导，混在一起会让“四类边均为实测”这句口径失真。前端按
   derived 登记，图上另标口径。
   ============================================================ */

/* ==================== 分词 ====================
   中英混排。中文不分词，取连续两字的滑窗 —— 三十五项任务、五十项技能的
   描述都在百字以内，引一个分词表进来，词表本身的规模就超过被切的文本。
   英文取小写词，技术名里的 + # . 保留（c++、c#、node.js 三者都靠它区分）。 */

const CJK = /[一-龥]/;

function tokenize(text) {
  const out = [];
  const s = (text ?? '').toLowerCase();
  let cjk = '';
  const flushCjk = () => {
    for (let i = 0; i + 1 < cjk.length; i++) out.push(cjk.slice(i, i + 2));
    /* 单字不入袋：汉字单字的区分度太低，“数”“系”“开”几乎每份文本都有 */
    cjk = '';
  };
  let i = 0;
  while (i < s.length) {
    const c = s[i];
    if (CJK.test(c)) {
      cjk += c;
      i++;
      continue;
    }
    flushCjk();
    if (/[a-z0-9]/.test(c)) {
      let j = i;
      while (j < s.length && /[a-z0-9+#._-]/.test(s[j])) j++;
      const w = s.slice(i, j).replace(/[._-]+$/, '');
      if (w.length >= 2) out.push(w);
      i = j;
      continue;
    }
    i++;
  }
  flushCjk();
  return out;
}

/** 词袋 → 词频表 */
function bagOf(texts) {
  const m = new Map();
  for (const t of texts) for (const w of tokenize(t)) m.set(w, (m.get(w) ?? 0) + 1);
  return m;
}

/* ==================== TF-IDF ==================== */

/**
 * 在给定语料上建一份 IDF，并给出把词袋转成单位向量的函数。
 *
 * 语料取“全部锚点 + 全部既有岗位的文本”：IDF 要压的是这个领域里到处都有的词
 * （“开发”“系统”“负责”），拿一份通用语料算出来的 IDF 压不到它们。
 */
function tfidfSpace(docs) {
  const df = new Map();
  for (const bag of docs) for (const w of bag.keys()) df.set(w, (df.get(w) ?? 0) + 1);
  const N = docs.length;
  const idf = new Map();
  for (const [w, n] of df) idf.set(w, Math.log((N + 1) / (n + 0.5)));

  /** 词袋 → 单位向量（Map 形式的稀疏向量）。词频取对数抑制长文本里的重复 */
  const vec = (bag) => {
    const v = new Map();
    let sq = 0;
    for (const [w, tf] of bag) {
      const k = idf.get(w);
      /* 语料里没见过的词不给权：它的 IDF 无从算起，按最大值给会让
         一个错别字压过全部实词 */
      if (k === undefined) continue;
      const x = (1 + Math.log(tf)) * k;
      v.set(w, x);
      sq += x * x;
    }
    const n = Math.sqrt(sq);
    if (n > 1e-12) for (const [w, x] of v) v.set(w, x / n);
    return v;
  };

  const cos = (a, b) => {
    /* 短的那个作外循环：稀疏向量的内积开销由较短的一侧决定 */
    const [p, q] = a.size <= b.size ? [a, b] : [b, a];
    let s = 0;
    for (const [w, x] of p) {
      const y = q.get(w);
      if (y !== undefined) s += x * y;
    }
    return s;
  };

  return { vec, cos };
}

/* ==================== 结构化信号 ====================
   「技能：A、B、C；任务：X、Y；技术栈：TS-01」*/

const SIGNAL_FIELD = /(技能|任务|技术栈)\s*[:：]\s*([^；;]+)/g;

/** 一句证据 → { skills: [名], tasks: [名] }。名称未落在体系内的丢弃 */
function parseSignal(sentence, skillNames, taskNames) {
  const out = { skills: [], tasks: [] };
  if (!sentence) return out;
  SIGNAL_FIELD.lastIndex = 0;
  let m;
  while ((m = SIGNAL_FIELD.exec(sentence))) {
    const kind = m[1];
    /* 技术栈一栏给的是 TS- 编码，不是体系内的名称，本模块不用 */
    if (kind === '技术栈') continue;
    const names = m[2]
      .split(/[、,，]/)
      .map((x) => x.trim())
      .filter(Boolean);
    for (const n of names) {
      if (kind === '技能' && skillNames.has(n)) out.skills.push(n);
      else if (kind === '任务' && taskNames.has(n)) out.tasks.push(n);
    }
  }
  return out;
}

/* ==================== 主过程 ==================== */

/** 保留的项数上限。既有岗位的任务连接度中位数 26 / 35、技能 40 / 50，
    推导向量的稀疏度取同一量级，两侧的余弦距离才落在可比的区间里 */
const KEEP_TASK = 24;
const KEEP_SKILL = 36;
/** 相对阈值：低于最高项这个比例的一律截掉，免得长尾把方向拉平 */
const KEEP_RATIO = 0.12;
/** 锚点扩写时每项任务并入的岗位数与技能数、每项技能并入的技能点数 */
const ANCHOR_JOBS = 6;
const ANCHOR_SKILLS = 8;
const ANCHOR_POINTS = 24;
/** 结构化信号与文本锚点并存时前者的权重。信号是读出来的，锚点是推断的 */
const SIGNAL_WEIGHT = 0.75;
/**
 * 认定“定得住”的判据。
 *
 * 有结构化信号即定得住 —— 那是从招聘原文里抽出来的读数。只有文本锚点时，
 * 相似度大于零即取，不再另设下限。
 *
 * 此前这里设过 0.08 的门槛，理由是：一份定义与全部任务都只有零点零几的
 * 相似度时，排出来的前几项更接近噪声的次序。判断本身没有变，改的是它落在
 * 哪一侧 —— 落在此处则该岗位整条不出向量，图上是一个不给距离、不给相近
 * 岗位的空心点，界面上要为这一个点专设一套空态与四段口径说明；落在界面
 * 一侧则四个叠层岗位同口径出图，弱的那一个弱在 topSim 上，该字段逐岗位
 * 写在 graph.json 的 inferred.jobs 里，可核可查。
 *
 * 本批四个叠层岗位的 topSim 为 0.0899 / 0.159 / 0.0356 / 0.1695，
 * 第三个（事实核查员）明显低于其余三个：其证据只有论文与新闻的英文摘录，
 * 与中文任务锚点的词面重合本就稀薄。它的相近岗位据此只作参考。
 */
const TEXT_FLOOR = 0;

/**
 * 给叠层新岗位推一份任务向量与技能向量。
 *
 * @param o.overlayJobs  [{ id, name, nameEn, def, sentences: [] }]
 * @param o.tasks        [{ id, name, nameEn, desc }]
 * @param o.skills       [{ id, name, nameEn, def }]
 * @param o.baseJobs     [{ id, name, def, keywords: [], boundary }]
 * @param o.jobTask      [{ s, t, w }] 既有岗位的实测 J-T 边
 * @param o.taskSkill    [{ s, t, w }] 实测 T-S 边
 * @param o.skillPoints  Map<技能编码, [技能点名]> 按边权降序
 */
export function inferOverlayJobVectors(o) {
  const { overlayJobs, tasks, skills, baseJobs, jobTask, taskSkill, skillPoints } = o;
  const taskById = new Map(tasks.map((t) => [t.id, t]));
  const skillById = new Map(skills.map((s) => [s.id, s]));
  const taskByName = new Map(tasks.map((t) => [t.name, t.id]));
  const skillByName = new Map(skills.map((s) => [s.name, s.id]));
  const taskNames = new Set(taskByName.keys());
  const skillNames = new Set(skillByName.keys());
  const jobById = new Map(baseJobs.map((j) => [j.id, j]));

  /* ---- 锚点扩写 ----
     任务并入承担它最多的几个岗位的关键词与它要求最重的几项技能名；
     技能并入它下面权重最高的那些技能点名。都是图内已有的边。 */
  const topBy = (rows, key, val, k) => {
    const m = new Map();
    for (const r of rows) {
      const a = m.get(r[key]);
      if (a) a.push(r);
      else m.set(r[key], [r]);
    }
    for (const [id, list] of m) m.set(id, list.sort((x, y) => y[val] - x[val]).slice(0, k));
    return m;
  };
  const jobsOfTask = topBy(
    jobTask.map((e) => ({ t: e.t, s: e.s, w: e.w })),
    't',
    'w',
    ANCHOR_JOBS,
  );
  const skillsOfTask = topBy(taskSkill, 's', 'w', ANCHOR_SKILLS);

  const taskAnchor = tasks.map((t) => {
    const parts = [t.name, t.nameEn ?? '', t.desc ?? ''];
    for (const e of skillsOfTask.get(t.id) ?? []) {
      const s = skillById.get(e.t);
      if (s) parts.push(s.name, s.nameEn ?? '');
    }
    for (const e of jobsOfTask.get(t.id) ?? []) {
      const j = jobById.get(e.s);
      if (j) parts.push(j.name, (j.keywords ?? []).join(' '));
    }
    return { id: t.id, bag: bagOf(parts) };
  });

  const skillAnchor = skills.map((s) => {
    const parts = [s.name, s.nameEn ?? '', s.def ?? ''];
    const pts = skillPoints.get(s.id) ?? [];
    if (pts.length) parts.push(pts.slice(0, ANCHOR_POINTS).join(' '));
    return { id: s.id, bag: bagOf(parts) };
  });

  /* 既有岗位的文本一并进语料：IDF 要压的是这个领域里到处都有的词，
     只拿八十五份锚点算，语料太小，“开发”一类的词压不下去 */
  const jobBags = baseJobs.map((j) => bagOf([j.name, j.def ?? '', (j.keywords ?? []).join(' '), j.boundary ?? '']));
  const { vec, cos } = tfidfSpace([...taskAnchor.map((a) => a.bag), ...skillAnchor.map((a) => a.bag), ...jobBags]);

  const taskVecs = taskAnchor.map((a) => ({ id: a.id, v: vec(a.bag) }));
  const skillVecs = skillAnchor.map((a) => ({ id: a.id, v: vec(a.bag) }));

  /* ---- 逐个新岗位 ---- */
  const outTask = [];
  const outSkill = [];
  const meta = {};

  for (const job of overlayJobs) {
    /* 结构化信号：逐条 JD 证据解析出的体系名，按提及次数计 */
    const sigTask = new Map();
    const sigSkill = new Map();
    let sigLines = 0;
    for (const sen of job.sentences ?? []) {
      const p = parseSignal(sen, skillNames, taskNames);
      if (!p.skills.length && !p.tasks.length) continue;
      sigLines++;
      for (const n of p.tasks) {
        const id = taskByName.get(n);
        if (id) sigTask.set(id, (sigTask.get(id) ?? 0) + 1);
      }
      for (const n of p.skills) {
        const id = skillByName.get(n);
        if (id) sigSkill.set(id, (sigSkill.get(id) ?? 0) + 1);
      }
    }

    /* 文本锚点：定义与全部证据句合成一份查询向量 */
    const q = vec(bagOf([job.name, job.nameEn ?? '', job.def ?? '', ...(job.sentences ?? [])]));
    const simTask = taskVecs.map((t) => ({ id: t.id, x: cos(q, t.v) }));
    const simSkill = skillVecs.map((s) => ({ id: s.id, x: cos(q, s.v) }));
    const topTaskSim = Math.max(0, ...simTask.map((t) => t.x));
    const topSkillSim = Math.max(0, ...simSkill.map((s) => s.x));

    /** 两路径合成一份分数。信号一侧先归一到 [0,1]，与相似度同量纲后再加权 */
    const blend = (sig, sim, topSim) => {
      const maxSig = Math.max(0, ...sig.values());
      const maxSim = topSim > 1e-9 ? topSim : 1;
      const out = new Map();
      for (const { id, x } of sim) {
        const a = maxSig > 0 ? (sig.get(id) ?? 0) / maxSig : 0;
        const b = x / maxSim;
        const v = maxSig > 0 ? SIGNAL_WEIGHT * a + (1 - SIGNAL_WEIGHT) * b : b;
        if (v > 0) out.set(id, v);
      }
      return out;
    };

    const scoreTask = blend(sigTask, simTask, topTaskSim);
    const scoreSkill = blend(sigSkill, simSkill, topSkillSim);

    /** 截尾并把最高项归一到 1，与实测边的取值区间一致 */
    const pack = (score, keep) => {
      const rows = [...score.entries()].sort((a, b) => b[1] - a[1]);
      const top = rows[0]?.[1] ?? 0;
      if (top <= 0) return [];
      return rows
        .filter(([, v]) => v >= top * KEEP_RATIO)
        .slice(0, keep)
        .map(([id, v]) => ({ id, w: Math.round((v / top) * 1e4) / 1e4 }));
    };

    const tRows = pack(scoreTask, KEEP_TASK);
    const sRows = pack(scoreSkill, KEEP_SKILL);

    const bySignal = sigLines > 0;
    const anchored = bySignal || topTaskSim > TEXT_FLOOR;

    if (anchored) {
      for (const r of tRows) outTask.push({ s: job.id, t: r.id, w: r.w });
      for (const r of sRows) outSkill.push({ s: job.id, t: r.id, w: r.w });
    }

    meta[job.id] = {
      /* 定得住才进图；定不住的仍照旧不给相近岗位清单 */
      anchored,
      /* 由哪条路径定的：signal 是从招聘原文抽出的读数，text 是相似度推断 */
      via: bySignal ? 'signal' : 'text',
      /** 解析到结构化信号的证据条数 */
      sigLines,
      /** 结构化信号覆盖的任务数与技能数 */
      sigTasks: sigTask.size,
      sigSkills: sigSkill.size,
      /** 文本锚点的最高相似度，供界面交代这一份推导有多稳 */
      topSim: Math.round(topTaskSim * 1e4) / 1e4,
      nTasks: anchored ? tRows.length : 0,
      nSkills: anchored ? sRows.length : 0,
    };
  }

  return {
    method: 'signal+anchor-tfidf',
    params: {
      keepTask: KEEP_TASK,
      keepSkill: KEEP_SKILL,
      keepRatio: KEEP_RATIO,
      signalWeight: SIGNAL_WEIGHT,
      textFloor: TEXT_FLOOR,
      anchorJobs: ANCHOR_JOBS,
      anchorSkills: ANCHOR_SKILLS,
      anchorPoints: ANCHOR_POINTS,
    },
    jobTask: outTask,
    jobSkill: outSkill,
    jobs: meta,
  };
}
