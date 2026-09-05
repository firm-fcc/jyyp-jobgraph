/* =========================================================
   首页

   分段式版式：页头 → 核心洞察 → 榜单 → 四层体系 → 系统特点 →
   演示 → 快速入口 → 运行状态条。
   定位与入口另立一页（/landing），本页只承载成段内容。

   一条贯穿全页的约束：**页面上不出现凭空写死的事实**。
   指标条、榜单、结论、卡片里的数量词，全部从图谱现算 ——
   首页写"前瞻观察 10 项"，点进全景图谱就该数得出这 10 项。
   写死的数字迟早会和数据对不上，而页面自相矛盾最难被发现。
   ========================================================= */

import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Icon } from '@/components/Icon';
import { Footer } from '@/components/Footer';
import { useGuide } from '@/components/common/guideContext';
import { useDataset } from '@/api/client';
import { ORPHAN_CLUSTER, REAL_SCALE } from '@/data/realTaxonomy';
import { NEWS_BRIEFS, REAL_GRAPH_STATS } from '@/data/realGraph';
import { KIND_LABEL } from '@/utils/viz';
import type { GraphEdge, NodeKind } from '@/types/graph';
import '@/styles/home.css';

/* ---------------- 报告期 ----------------

   观测按月，一窗一月，故最细的报告期是月，没有周这一档 —— 原先的"周报"
   在数据上无从落地，其下三个数也不是按周算的。三档现按窗口数取：
   本月一窗、本季末三窗、本年末十二窗，各档报的都是同三个量，只是区间不同。 */
type Period = 'monthly' | 'quarterly' | 'yearly';

const PERIODS: { v: Period; label: string; windows: number; title: string }[] = [
  { v: 'monthly', label: '月报', windows: 1, title: '本月' },
  { v: 'quarterly', label: '季度报', windows: 3, title: '本季度' },
  { v: 'yearly', label: '年报', windows: 12, title: '本年度' },
];

/* ---------------- 本期重点前瞻项 ----------------

   取在本期各观测窗口内被论文或新闻提及最多的一条尚未写入招聘要求的条目，
   并要求它在全部窗口中至少三个月被提及。后一道门槛滤的是抽取出来的一次性
   专名：这类条目只在某一篇论文里出现过一次，前瞻强度却因"招聘侧全无"而
   接近满值，按强度排序时会把它们顶到最前。

   选取范围限在技能一层，与全景图谱页的三源前瞻分析同口径 —— 卡片的"查看详情"
   正是跳到那一块。技能点一层是逐条抽取的原始产物，其中夹着大量论文自造的
   专名，且那一块并不收它们，取到技能点便会跳过去对不上号。 */
function pickHottest(
  d: ReturnType<typeof useDataset>,
  months: string[],
): { hottest?: (typeof d.signals)[number]; hottestSpread: number } {
  const spreadOf = (s: (typeof d.signals)[number]) => {
    let k = 0;
    for (let i = 0; i < s.months.length; i++) {
      if ((s.paper[i] ?? 0) > 0 || (s.news[i] ?? 0) > 0) k++;
    }
    return k;
  };

  let hottest: (typeof d.signals)[number] | undefined;
  let hottestSpread = 0;
  let best = 0;
  for (const s of d.signals) {
    if (s.kind !== 'skill') continue;
    /* 已写入招聘要求的不再是前瞻项，它已经落地 */
    if (s.firstJdAt) continue;
    const spread = spreadOf(s);
    if (spread < 3) continue;
    let score = 0;
    for (const m of months) {
      const i = s.months.indexOf(m);
      if (i >= 0) score += (s.paper[i] ?? 0) + (s.news[i] ?? 0);
    }
    if (score > best) {
      best = score;
      hottest = s;
      hottestSpread = spread;
    }
  }
  return { hottest, hottestSpread };
}

const LAYERS = [
  { sym: 'P', label: '岗位', desc: 'Job Position', color: 'var(--lay-job)' },
  { sym: 'T', label: '任务', desc: 'Task', color: 'var(--lay-task)' },
  { sym: 'S', label: '技能', desc: 'Skill', color: 'var(--lay-skill)' },
  { sym: 'SP', label: '技能点', desc: 'Skill Point', color: 'var(--lay-sp)' },
];

/* 三张指标卡只读数，不跳转：它们报的是本期规模，
   而各页入口已经在下面的功能卡与顶栏里给过，卡片再挂一次链接
   只会让人以为点进去能看到这三个数各自的明细页 —— 并没有那样一页。 */
interface Stat {
  number: string;
  label: string;
}
interface Feature {
  tag: 'jd' | 'research' | 'news' | 'insight';
  tagText: string;
  title: string;
  desc: string;
  to: string;
}

/* 快报按落点的层级排序。一条报道锚在岗位上，读者一眼就知道它关系到哪一类工作；
   锚在某一项技能或技能点上则要先知道那项技能属于谁，才读得出这条消息的分量。 */
const ANCHOR_RANK: Record<NodeKind, number> = { job: 0, task: 1, skill: 2, skillpoint: 3 };

/** 岗位层的落点写作"某某等 N 个岗位"，其余层直接列名。列全会把一行撑到两行 */
const ANCHOR_TEXT: Record<NodeKind, string> = {
  job: '岗位',
  task: '任务',
  skill: '技能',
  skillpoint: '技能点',
};

export function Home() {
  const d = useDataset();
  const openGuide = useGuide();
  const [period, setPeriod] = useState<Period>('monthly');

  const jobs = useMemo(() => d.nodes.filter((n) => n.kind === 'job'), [d.nodes]);

  /* ---------------- 报告期的区间与本期计量 ----------------

     三个量都按窗口逐个累加，故换一档只换区间、不换口径，三档之间可直接相比：

       招聘信息     各窗扫描的条数之和
       新增能力要求 各窗层间关系较上一窗的净增之和（只计增量，减量另计）
       新增前瞻信号 入场窗口落在区间内的叠层条目数

     刻意不取"增强条目数"一项：叠层记录逐窗累积，该数是截至该窗的存量而非增量，
     跨窗相加会把同一条增强按其在图内的窗数重复计一遍。 */
  const span = useMemo(() => {
    const n = PERIODS.find((p) => p.v === period)!.windows;
    const runs = d.loops.slice().sort((a, b) => (a.startedAt < b.startedAt ? -1 : 1));
    const inSpan = runs.slice(-n);
    const idx = new Map(runs.map((r, i) => [r.id, i]));
    const posts = inSpan.reduce((a, r) => a + r.batch.jd, 0);
    /* 首窗没有上一窗可比，其"净增"实为建图本身，不计入 */
    const edgesAdded = inSpan.reduce((a, r) => a + ((idx.get(r.id) ?? 0) > 0 ? r.deltas.edgesAdded : 0), 0);
    const months = inSpan.map((r) => r.startedAt.slice(0, 7));
    const born = new Set(months);
    const signals = d.nodes.filter(
      (n2) => n2.origin === 'overlay' && born.has(n2.firstSeen.slice(0, 7)),
    ).length;
    return {
      from: months[0] ?? '',
      to: months[months.length - 1] ?? '',
      windows: inSpan.length,
      posts,
      edgesAdded,
      signals,
      /* 区间两端的四层规模，取自各观测窗口的构建记录。
         起点取区间首窗的**前一窗** —— 报的是"这一期长了多少"，
         而首窗自身的规模是这一期开始时就已经有的。 */
      head: d.versions[Math.max(0, d.versions.length - n - 1)],
      tail: d.versions[d.versions.length - 1],
      /* 本期最受关注的前瞻条目，及它在全部观测窗口中被提及的月数。

         此处原取"本期入场且前瞻强度最高"的一项。前瞻强度衡量的是论文与新闻
         的热度同招聘要求之间的落差，而一个当期才入场、只在一篇论文里出现过
         一次的名词，落差天然接近满值 —— 这一格因此恒由当期新造的专名占着
         （如"框架指数""Sci-TQA2原则"），既不是一项能力，也无从查证。

         现改按本期证据量取，并设一道准入：该条目须在观测区间内至少三个月
         被论文或新闻提及。只出现过一次的抽取结果因而不入选，留下的是确有
         持续热度、而招聘要求尚未跟上的那一类 —— 这正是前瞻信号要报的东西。 */
      ...pickHottest(d, months),
    };
  }, [d.loops, d.nodes, d.nodeById, d.signals, d.versions, period]);

  /* ---------------- 热门招聘岗位 ---------------- */
  const hotJobs = useMemo(
    () =>
      [...jobs]
        .filter((j) => !j.emerging)
        .sort((a, b) => (b.attrs?.postCount ?? 0) - (a.attrs?.postCount ?? 0))
        .slice(0, 3),
    [jobs],
  );

  /* ---------------- 高关注度论文 ---------------- */
  const papers = useMemo(() => {
    const ranked = [...d.signals]
      .filter((s) => s.kind !== 'job')
      .sort((a, b) => b.gap[b.gap.length - 1] - a.gap[a.gap.length - 1]);
    const seen = new Set<string>();
    const out: {
      title: string;
      docId: string;
      outlet: string;
      at: string;
      entity: string;
      kind: string;
      focusId: string;
      gap: number;
    }[] = [];
    /* 指向该条目的边一次归拢：ranked 有数千条，逐条 filter 四万余条边
       会把这一段拖成数百毫秒，而它要找的只是每条信号名下的第一篇论文。 */
    const inTo = new Map<string, GraphEdge[]>();
    for (const e of d.edges) {
      const arr = inTo.get(e.target);
      if (arr) arr.push(e);
      else inTo.set(e.target, [e]);
    }
    for (const s of ranked) {
      const ev = (inTo.get(s.entityId) ?? [])
        .flatMap((e) => e.evidence)
        .find((v) => v.sourceType === 'paper' && !seen.has(v.docId));
      if (!ev) continue;
      seen.add(ev.docId);
      out.push({
        title: ev.title,
        docId: ev.docId,
        outlet: ev.outlet ?? 'arXiv',
        at: ev.publishedAt,
        entity: s.entityName,
        kind: KIND_LABEL[s.kind],
        focusId: s.entityId,
        gap: s.gap[s.gap.length - 1],
      });
      if (out.length === 3) break;
    }
    return out;
  }, [d.signals, d.edges]);

  /* ---------------- 本期结论与关键数据 ---------------- */
  const facts = useMemo(() => {
    const detected = d.signals.filter((s) => s.kind !== 'job' && s.firstPaperAt);
    const confirmed = detected.filter((s) => s.firstJdAt);
    const leads = confirmed
      .map((s) => s.leadMonths.paper)
      .filter((v): v is number => typeof v === 'number' && v > 0);
    const lead = leads.length ? leads.reduce((a, b) => a + b, 0) / leads.length : 0;
    /* 中位数与均值并列：提前量的分布右偏，只报均值时会被少数长时滞的条目拉高 */
    const sorted = [...leads].sort((a, b) => a - b);
    const leadMedian = sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0;
    const hottest = [...d.signals]
      .filter((s) => s.kind !== 'job')
      .sort((a, b) => b.gap[b.gap.length - 1] - a.gap[a.gap.length - 1])[0];
    const loop = d.loops[0];
    const first = d.versions[0];
    const last = d.versions[d.versions.length - 1];
    return {
      /* 与封面同一口径：只数岗位层的前瞻条目，标签才对得上 */
      newJobs: d.nodes.filter((n) => n.kind === 'job' && n.origin === 'overlay').length,
      emerging: jobs.filter((j) => j.emerging).length,
      changed: d.changes.filter((c) => c.version === last?.version).length,
      hitRate: detected.length ? confirmed.length / detected.length : 0,
      lead,
      leadMedian,
      leadN: leads.length,
      hottest,
      loop,
      first,
      last,
      nodes: d.nodes.length,
      edges: d.edges.length,
      skillPoints: d.nodes.filter((n) => n.kind === 'skillpoint').length,
      // 体系本身的规模口径 —— 首屏第一段现在只报这一类可回源核对的数
      jobs: d.nodes.filter((n) => n.kind === 'job').length,
      // 归并链路只算体系内的规范节点，新发现岗位恰恰是还没进体系的那一批
      canonJobs: d.nodes.filter((n) => n.kind === 'job' && !n.emerging).length,
      jobCategories: new Set(
        d.nodes.filter((n) => n.kind === 'job').map((n) => n.topCategory || ORPHAN_CLUSTER),
      ).size,
      tasks: d.nodes.filter((n) => n.kind === 'task').length,
      skillGroups: d.nodes.filter((n) => n.kind === 'skill').length,
      skills: d.nodes.filter((n) => n.kind === 'skillpoint').length,
      /* 基准与叠层分开报：前者已被招聘市场确认，后者只有论文与新闻支持，
         合成一个数会把"尚未被市场确证"这件事抹掉 */
      baseTasks: REAL_GRAPH_STATS.base.tasks,
      baseSkills: REAL_GRAPH_STATS.base.skills,
      baseSkillPoints: REAL_GRAPH_STATS.base.skillpoints,
      overlayJobs: REAL_GRAPH_STATS.overlay.jobs,
      overlayTasks: REAL_GRAPH_STATS.overlay.tasks,
      overlaySkills: REAL_GRAPH_STATS.overlay.skills,
      overlaySkillPoints: REAL_GRAPH_STATS.overlay.skillpoints,
      /* ---- 体系口径，全部读岗位体系 v2.0 的真实字段 ---- */
      // 255 个 v1 节点 → 200 个归并保留 + 46 个低 IT 相关剔除 + 9 个大类撤销
      v1Nodes: REAL_SCALE.v1Nodes,
      kept: REAL_SCALE.kept,
      excluded: REAL_SCALE.excluded,
      merges: REAL_SCALE.merges,
      definitions: REAL_SCALE.definitions,
      boundaries: REAL_SCALE.boundaries,
      // 招聘信息条数 —— 岗位这一层唯一的实测计量
      posts: REAL_SCALE.posts,
    };
  }, [d, jobs]);

  /* ---------------- 首页要点名的那个萌芽岗位 ----------------
     不写死 id：取信号最强的一个萌芽岗位，五要素的条数也从它身上现数。
     上一版把“核心职责 4 项 · 必备能力 3 类”写死在文案里 ——
     数据一改就静默过期，而且没人会发现。 */
  const showcase = useMemo(() => {
    const j = [...jobs].filter((x) => x.emerging).sort((a, b) => b.confidence - a.confidence)[0];
    if (!j) return null;
    /* 支撑该候选的论文与新闻条数。本批数据的叠层只给信号与证据、不给定义字段，
       五要素为空时改用它来交代"凭什么把它列为候选"，
       而不是把三个 0 摆出来当作介绍。 */
    const ev = d.signalMap.get(j.id);
    const evCount = d.edges
      .filter((e) => e.target === j.id || e.source === j.id)
      .reduce((a, e) => a + e.evidence.length, 0);
    return {
      job: j,
      firstSeen: `${j.firstSeen.slice(0, 4)}年${Number(j.firstSeen.slice(5))}月`,
      duties: j.coreDuties?.length ?? 0,
      must: j.mustSkills?.length ?? 0,
      scenarios: j.scenarios?.length ?? 0,
      firstPaperAt: ev?.firstPaperAt,
      evCount,
    };
  }, [jobs, d.signalMap, d.edges]);

  /* 三条设计约束。每条都对应界面上可当场验证的一件事，不写无法核对的形容词。
     第二条的领先量必须与页面别处同源 —— 写“1–3 年”而实测是 14 个月，
     等于自己给自己造一处对不上。 */
  const why = useMemo(
    () => [
      {
        icon: 'layers' as const,
        title: '更全面的数据',
        desc: '融合招聘广告、学术论文、行业新闻三类异构数据源，以交叉比对与持续采集扩展覆盖范围。',
      },
      {
        icon: 'trend' as const,
        title: '更前瞻的判断',
        desc: '以时序演化分析与关系网络推演，识别新兴岗位的萌芽信号与能力要求的迁移趋势。',
      },
      {
        icon: 'shield' as const,
        title: '更可信的结论',
        desc: '图谱中每一条表述均有原文支撑，无原文依据的内容不予写入，支持从图谱维度到数据源的回溯。',
      },
    ],
    [],
  );

  /* 三档只换区间，不换口径：同三个量、同三条链接，故不再按档各写一套。 */
  const cur: { stats: Stat[]; lead: Feature } = useMemo(() => {
    const hot = span.hottest;
    return {
      stats: [
        { number: span.posts.toLocaleString(), label: '本期招聘信息' },
        { number: span.edgesAdded.toLocaleString(), label: '本期新增能力要求' },
        { number: String(span.signals), label: '本期新增前瞻信号' },
      ],
      lead: {
        tag: 'research',
        tagText: '前瞻信号',
        title: `本期重点前瞻项：${hot?.entityName ?? '—'}`,
        /* 来源按该条目实际有的那几路写。此前一律写作"由学术论文与行业新闻提出"，
           而多数条目只见于其中一路，读者据此去对照另一路会扑空。 */
        desc: hot
          ? `${hot.firstPaperAt && hot.firstNewsAt
            ? `行业新闻首现 ${hot.firstNewsAt}、学术论文首现 ${hot.firstPaperAt}`
            : hot.firstPaperAt
              ? `学术论文首现 ${hot.firstPaperAt}`
              : `行业新闻首现 ${hot.firstNewsAt}`
          }，至今未写入招聘要求；${d.versions.length} 个观测窗口中有 ${span.hottestSpread} 个月见于上述信源，` +
          `当前前瞻强度 ${((hot.gap[hot.gap.length - 1] ?? 0) * 100).toFixed(0)}%，在图谱中按前瞻信号计入、权重逐月衰减。` +
          (facts.lead > 0
            ? `同类信号自论文首现到写入招聘要求，实测中位提前 ${facts.leadMedian} 个月、均值 ${facts.lead.toFixed(1)} 个月。`
            : '')
          : '本期没有可报的前瞻项。',
        to: `/panorama?focus=${encodeURIComponent(hot?.entityId ?? '')}`,
      },
    };
  }, [d.versions, facts, span]);

  /* ---------------- 本期快报 ----------------

     右侧这一格此前放的是三段随报告期切换的说明文字：能力年轮怎么成环、
     同名岗位的职责差异怎么分档、图谱规模在各窗口间怎么变。三段讲的都是
     本站某一块的做法，与"本期发生了什么"无关，放在核心洞察这一节里
     既不随区间变，也没有可读的时效。

     现改为报本期采信的行业报道。这批报道本就在库里 —— 图谱中每一条前瞻
     信号的出处即由它们与学术论文两路构成，只是此前只在条目详情里逐条出现，
     没有一处按时间把它们汇到一起。快报因而不是新添的一类内容，
     而是把已有的证据换一个方向读：由"这个条目凭什么成立"改为
     "这一期市场上说了什么，它落在图谱的哪一处"。 */
  const briefs = useMemo(() => {
    const from = span.from ? `${span.from}-01` : '';
    const to = span.to ? `${span.to}-31` : '';
    /* 锚点须在图内：报道锚定的条目若不在当前节点表内，点过去是一片空白。

       名字取图谱节点的，不取叠层记录里的：同一个条目两处叫法可以不同
       （叠层记作 SRE，体系里的规范名是运维工程师），卡片上写一个、
       点进去看到另一个，读者无从判断是不是点错了地方。 */
    const usable = NEWS_BRIEFS.map((b) => ({
      ...b,
      anchors: b.anchors
        .filter((a) => d.nodeById.has(a.id))
        .map((a) => ({ ...a, name: d.nodeById.get(a.id)?.name ?? a.name }))
        .filter((a, i, arr) => arr.findIndex((x) => x.name === a.name) === i),
    })).filter((b) => b.anchors.length > 0 && !b.truncated && b.anchors[0].kind !== 'skillpoint');

    const inSpan = usable.filter((b) => b.date >= from && b.date <= to);
    /* 本期一条也没有时退到最近的几条，并在脚注里写明这是回退 ——
       报告期短而报道稀疏时，空着一格比换个区间更难读 */
    const pool = inSpan.length ? inSpan : usable;
    const rank = (a: (typeof pool)[number], b: (typeof pool)[number]) =>
      /* 先按落点的层级：报道锚在岗位上，比锚在某一项技能上更贴近"本期市场说了什么" */
      ANCHOR_RANK[a.anchors[0].kind] - ANCHOR_RANK[b.anchors[0].kind] ||
      b.anchors.length - a.anchors.length ||
      (a.date < b.date ? 1 : -1);
    /* 头条取本期落点最广的一条，其余两条按时间倒序 —— 头条回答"这一期最要紧的
       是哪件事"，其下两行回答"还有什么"，后者按时间读才顺 */
    const sorted = [...pool].sort(rank);
    const rest = sorted
      .slice(1)
      .sort((a, b) => (a.date < b.date ? 1 : -1))
      .slice(0, 2);
    return {
      list: sorted.length ? [sorted[0], ...rest] : [],
      /* 脚注报的是本期采信的全部报道，不是上面过滤之后的那几条：
         过滤只决定哪几条适合摆在首页（落点须直达、标题须完整），
         报道本身条条都进了图谱，用过滤后的数当作本期规模会把这一期报小。 */
      total: NEWS_BRIEFS.filter((b) => b.date >= from && b.date <= to).length,
      fallback: inSpan.length === 0,
      /* 区间跨年时次条须带年份：只写月日，年报里的 10-09 与 04-22 看上去是
         同一年内的先后两条，实际相隔半年且次序相反 */
      showYear: inSpan.length === 0 || span.from.slice(0, 4) !== span.to.slice(0, 4),
    };
  }, [span.from, span.to, d.nodeById]);

  /** 一条报道的落点。锚在岗位上就去那个岗位的数据来源一节，那里逐篇列出原文；
      其余落到全景图谱的该条目，与前瞻信号卡片同一去处 */
  const briefTo = (b: (typeof briefs)['list'][number]) => {
    const job = b.anchors.find((a) => a.kind === 'job');
    if (job) {
      const j = d.nodeById.get(job.id);
      return `/jobs?tab=${j?.emerging ? 'new' : 'existing'}&id=${encodeURIComponent(job.id)}&src=1`;
    }
    return `/panorama?focus=${encodeURIComponent(b.anchors[0].id)}`;
  };



  return (
    <div className="home-apple">
      {/* ============================================================
          核心洞察 —— 本页的第一段。

          原来这里上面还有一个页头：主标题 + 系统说明 + 四项规模指标条，
          与封面页（/landing）的标题、说明与指标条完全重复，
          从封面点进来等于把同一段话再读一遍，已整段移除。
          ============================================================ */}
      <section className="insight-section">
        <div className="wrap">
          <div className="insight-header">
            <div>
              <h2 className="section-title-apple">
                {PERIODS.find((p) => p.v === period)!.title}关键发现
              </h2>
              {/* 区间与窗口数写在标题下：三档的数只有连着区间读才可比 */}
              <p className="insight-span">
                统计区间 {span.from === span.to ? span.from : `${span.from} — ${span.to}`}
              </p>
            </div>
            <div className="period-switch-apple">
              {PERIODS.map((p) => (
                <button
                  key={p.v}
                  className={period === p.v ? 'period-btn-apple active' : 'period-btn-apple'}
                  onClick={() => setPeriod(p.v)}
                  aria-pressed={period === p.v}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          <div className="insight-lead">
            <div className="insight-lead-icon">
              <Icon name="spark" size={20} />
            </div>
            {/* 这一段随报告期变。此前它写的是图谱当前的四层规模 —— 一个与区间
                无关的量，三档之间逐字相同，切换报告期时上面的标题与区间都变了，
                这一段却纹丝不动。现改为报本期末的规模与本期的净增：规模回答
                "现在有多大"，净增回答"这一期长了多少"，后者才随区间变。 */}
            <p className="insight-lead-text">
              截至 <strong>{span.to}</strong>，图谱为四层结构：岗位{' '}
              <strong>{facts.canonJobs}</strong> 个分属 <strong>{facts.jobCategories}</strong> 个类别，
              任务 <strong>{facts.baseTasks}</strong> 项，技能 <strong>{facts.baseSkills}</strong> 项，
              技能点 <strong>{facts.baseSkillPoints.toLocaleString()}</strong> 个，层间关系{' '}
              <strong>{facts.edges.toLocaleString()}</strong> 条。
              {span.head && span.tail && span.head.date !== span.tail.date && (
                <>
                  {' '}较 <strong>{span.head.date}</strong>，本
                  {PERIODS.find((p) => p.v === period)!.title.slice(1)}新增技能点{' '}
                  <strong>
                    {Math.max(0, span.tail.stats.skillPoints - span.head.stats.skillPoints).toLocaleString()}
                  </strong>{' '}
                  个、层间关系{' '}
                  <strong>{Math.max(0, span.tail.stats.edges - span.head.stats.edges).toLocaleString()}</strong> 条
                  {span.tail.stats.tasks > span.head.stats.tasks && (
                    <>
                      ，任务由 <strong>{span.head.stats.tasks}</strong> 增至{' '}
                      <strong>{span.tail.stats.tasks}</strong> 项
                    </>
                  )}
                  。
                </>
              )}{' '}
              本期另有论文与新闻提出、尚未被招聘市场确证的前瞻条目{' '}
              <strong>{span.signals}</strong> 项入场；累计在图的前瞻条目为新岗位{' '}
              <strong>{facts.overlayJobs}</strong> 个、新任务 <strong>{facts.overlayTasks}</strong> 项、
              新技能 <strong>{facts.overlaySkills}</strong> 项与前瞻技能点{' '}
              <strong>{facts.overlaySkillPoints.toLocaleString()}</strong> 个。
            </p>
          </div>

          <div className="insight-stats-grid">
            {cur.stats.map((s) => (
              <div key={s.label} className="insight-stat-card">
                <span className="insight-stat-num">{s.number}</span>
                <span className="insight-stat-label">{s.label}</span>
              </div>
            ))}
          </div>

          <div className="insight-features">
            <Link className="insight-feature-card" to={cur.lead.to}>
              <span className={`insight-feature-tag tag-${cur.lead.tag}`}>{cur.lead.tagText}</span>
              <h3 className="insight-feature-title">{cur.lead.title}</h3>
              <p className="insight-feature-desc">{cur.lead.desc}</p>
              <span className="insight-feature-link">
                查看详情
                <Icon name="arrowR" size={13} />
              </span>
            </Link>

            {/* 快报这一格整块不做成一个链接：卡内三条报道各有各的落点，
                外层再挂一条会与内层的三条相互吞掉点击。 */}
            {briefs.list.length > 0 && (
              <div className="insight-feature-card brief-card">
                <span className="insight-feature-tag tag-news">本期快报</span>
                {briefs.list.map((b, i) =>
                  i === 0 ? (
                    <div key={b.docId} className="brief-lead">
                      <Link className="brief-lead-link" to={briefTo(b)}>
                        <h3 className="insight-feature-title">{b.title}</h3>
                      </Link>
                      <p className="brief-meta">
                        <span className="brief-outlet">{b.outlet}</span>
                        <em>{b.date}</em>
                        <span>
                          锚定{ANCHOR_TEXT[b.anchors[0].kind]}{' '}
                          {b.anchors
                            .slice(0, 2)
                            .map((a) => a.name)
                            .join('、')}
                          {b.anchors.length > 2 ? ` 等 ${b.anchors.length} 项` : ''}
                        </span>
                      </p>
                      {b.lines[0] && <p className="brief-quote">{b.lines[0]}</p>}
                    </div>
                  ) : null,
                )}

                {briefs.list.length > 1 && (
                  <ul className="brief-more">
                    {briefs.list.slice(1).map((b) => (
                      <li key={b.docId}>
                        <Link to={briefTo(b)}>
                          <em>{briefs.showYear ? b.date : b.date.slice(5)}</em>
                          <span className="brief-more-title">{b.title}</span>
                          <i>{b.anchors[0].name}</i>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}

                {/* 取数口径就地交代：这一格列的是本期采信的报道里落点可直达图谱的几条，
                    而"本期采信 N 条"报的是全部，两个数不同源，故一句话里都写出来 */}
              </div>
            )}
          </div>
        </div>
      </section>

      {/* ============================================================
          榜单区
          ============================================================ */}
      <section className="ranking-section">
        <div className="wrap">
          <div className="ranking-grid">
            <div className="ranking-card">
              <div className="ranking-card-hd">
                <h3>热门招聘岗位</h3>
                <span className="ranking-badge">招聘信息</span>
              </div>
              <div className="ranking-list">
                {hotJobs.map((j, i) => (
                  <Link
                    key={j.id}
                    className="ranking-item"
                    to={`/jobs?tab=existing&id=${encodeURIComponent(j.id)}`}
                  >
                    <span className={`ranking-num rank-${i + 1}`}>{i + 1}</span>
                    <div className="ranking-body">
                      <div className="ranking-name">
                        {j.name} <span className="ranking-cluster">{j.cluster}</span>
                      </div>
                      <div className="ranking-meta">
                        中位 {j.attrs?.medianSalary}k · 在招 {j.attrs?.postCount?.toLocaleString()}
                      </div>
                    </div>
                    <Icon name="chevronR" size={14} className="ranking-arrow" />
                  </Link>
                ))}
              </div>
            </div>

            <div className="ranking-card">
              <div className="ranking-card-hd">
                <h3>高关注度论文</h3>
                <span className="ranking-badge paper">论文</span>
              </div>
              {/* 论文这一栏不跳转：它列的是原始数据本身，点它会让人以为要看论文，
                  而系统里没有论文原文页；此前跳去全景图谱定位那个技能点，与预期对不上。 */}
              <div className="ranking-list">
                {papers.map((p, i) => (
                  <div key={p.docId} className="ranking-item static">
                    <span className={`ranking-num rank-${i + 1}`}>{i + 1}</span>
                    <div className="ranking-body">
                      {/* 标题为原题，多为英文长句，故不与来源同行，另起一行给编号与日期 */}
                      <div className="ranking-name paper-title">{p.title}</div>
                      <div className="ranking-meta">
                        {p.outlet}:{p.docId} · {p.at} · 支撑{p.kind}“{p.entity}”
                      </div>
                    </div>
                  </div>
                ))}
                {papers.length === 0 && <p className="ranking-empty">暂无可展示的论文来源。</p>}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ============================================================
          四层能力体系
          ============================================================ */}
      <section className="layers-section">
        <div className="wrap">
          <h2 className="section-title-apple">岗位—任务—技能—技能点</h2>
          {/* 拼成一个字符串而不是直接分行写：JSX 会把源码里的换行折成一个空格，
              中文之间因此多出一个词间距 —— 这一段居中且字号大，那处空隙看得很清楚。 */}
          <p className="section-sub">
            {'岗位由任务构成，任务要求相应技能，每项技能再由具体技能点支撑。' +
              '技能跨越数年保持稳定，技能点随技术迭代快速更替 —— ' +
              '分作两层，图谱才能在结构不变的前提下反映技术演进。'}
          </p>
          <div className="layers-grid">
            {LAYERS.map((l) => (
              <div className="layer-card" key={l.sym}>
                <div className="layer-symbol" style={{ color: l.color, borderColor: l.color }}>
                  {l.sym}
                </div>
                <h4 className="layer-label">{l.label}</h4>
                <p className="layer-desc">{l.desc}</p>
              </div>
            ))}
          </div>
          {/* 四张并排的瓦片说明不了这四层是怎么连起来的，而那正是全景图谱在画的东西。
              带 #sec-flow 直接落到那张图上：全景图谱页首屏是分析引擎与前瞻分析，
              主图在页面末段，不带锚点时点进去看到的是另一块内容。 */}
          <Link className="layers-link" to="/panorama#sec-flow">
            在全景图谱中查看这四层的连接关系
            <Icon name="arrowR" size={14} />
          </Link>
        </div>
      </section>

      {/* ============================================================
          差异化优势
          ============================================================ */}
      <section className="why-section">
        <div className="wrap">
          <h2 className="section-title-apple">三个维度的差异化设计</h2>
          <div className="why-grid-apple">
            {why.map((w) => (
              <div className="why-card-apple" key={w.title}>
                <div className="why-card-icon">
                  <Icon name={w.icon} size={28} />
                </div>
                <h3 className="why-card-title">{w.title}</h3>
                <p className="why-card-desc">{w.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ============================================================
          系统导览

          这里曾经写着“时长 3:42”与“2026-06 录制”—— 仓库里并没有视频文件，
          两个数都是凭空写的，而那个日期其实是图谱版本日期。已改成现算的覆盖范围。

          那枚播放按钮同样是空的：80px 的圆形播放键没有挂任何行为，
          点上去不发生任何事。现在它打开四大功能那一屏，即第一次进入系统时看到的那一屏，
          按钮文字与图标随之改掉 —— 一个播放三角形指向的不是一段说明。
          ============================================================ */}
      <section className="demo-section">
        <div className="wrap">
          <div className="demo-card">
            <div className="demo-visual">
              <div className="demo-frame">
                <button className="demo-play-btn" onClick={openGuide} aria-label="打开四大功能说明">
                  <Icon name="route" size={28} />
                </button>
              </div>
              <div className="demo-glow" />
            </div>
            <div className="demo-info">
              <h2 className="demo-title">破解青年人才技能错配的动态技能图谱与可视化就业导航系统</h2>
              <p className="demo-desc">
                三类数据源交叉验证，用四层图谱刻画岗位的能力要求，并回答两个问题：
                新岗位的发现与定义，既有岗位能力要求的动态演变。
                四个功能页构成一条链路，顺序与顶栏一致。
              </p>
              <div className="demo-meta">
                <span className="demo-meta-item">
                  <Icon name="layers" size={14} />
                  覆盖 {jobs.length} 个岗位 · {facts.skillPoints} 个技能点
                </span>
                <span className="demo-meta-item">
                  <Icon name="clock" size={14} />
                  图谱版本 {facts.last?.version} · {facts.last?.date}
                </span>
              </div>
              <button className="demo-cta" onClick={openGuide}>
                查看四大功能
                <Icon name="arrowR" size={14} />
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* ============================================================
          快速入口
          ============================================================ */}
      <section className="quick-section">
        <div className="wrap">
          <div className="quick-grid">
            <Link
              className="quick-card"
              to={showcase ? `/jobs?tab=new&id=${encodeURIComponent(showcase.job.id)}` : '/jobs?tab=new'}
            >
              <div className="quick-card-icon">
                <Icon name="cap" size={26} />
              </div>
              <h3 className="quick-card-title">岗位演变与更新</h3>
              {showcase ? (
                <>
                  <p className="quick-card-quote">
                    <b>{showcase.job.name}</b>
                    {showcase.job.definition}
                  </p>
                  <p className="quick-card-desc">
                    {showcase.duties + showcase.must + showcase.scenarios > 0
                      ? `首现于${showcase.firstSeen}，定义含 ${showcase.duties} 项核心职责、${showcase.must} 项必备技能与 ${showcase.scenarios} 个典型应用场景，逐项标注推法与依据。`
                      : `首现于${showcase.firstSeen}，由学术论文与行业新闻提出，招聘市场尚未出现；证据太少，任务构成推不出来，页内逐条列出支撑它的原文证据。`}
                  </p>
                </>
              ) : (
                <p className="quick-card-desc">
                  识别尚未进入标准岗位体系的萌芽岗位，生成可回溯到原始文档的岗位定义。
                </p>
              )}
              <span className="quick-card-link">
                进入岗位洞察
                <Icon name="arrowR" size={13} />
              </span>
            </Link>

            <Link className="quick-card" to="/panorama">
              <div className="quick-card-icon">
                <Icon name="db" size={26} />
              </div>
              <h3 className="quick-card-title">岗位能力要求全景</h3>
              <p className="quick-card-desc">
                融合招聘广告、学术论文、行业新闻三类独立信源，以交叉验证实现前瞻信号与现实需求的互补。
                针对招聘数据固有的时滞、噪声与模板复制，入图前逐道治理：相似度超过 95% 的模板化广告计为一条证据，样板化表述不计入能力统计，
                无原文支撑的表述一律不予收录。
              </p>
              <span className="quick-card-link">
                进入全景图谱
                <Icon name="arrowR" size={13} />
              </span>
            </Link>

            <Link className="quick-card" to="/explore">
              <div className="quick-card-icon">
                <Icon name="target" size={26} />
              </div>
              <h3 className="quick-card-title">职业探索</h3>
              <p className="quick-card-desc">
                目标岗位尚未确定时，从能力反查岗位：{facts.skillPoints} 个技能点与 {jobs.length} 个岗位三段并排，
                选中一项能力即列出要求它的岗位及其城市与薪资档分布。岗位另按能力结构聚类，
                先比较结构，再落到具体岗位。
              </p>
              <span className="quick-card-link">
                进入职业探索
                <Icon name="arrowR" size={13} />
              </span>
            </Link>

            <Link className="quick-card" to="/match">
              <div className="quick-card-icon">
                <Icon name="route" size={26} />
              </div>
              <h3 className="quick-card-title">人岗匹配与差距分析</h3>
              <p className="quick-card-desc">
                左右双栏对照：左栏为简历原文，右栏为分析结果。分析分三步进行：先核验简历的真实性与一致性，
                再逐段计算与目标岗位的关联度，最后给出分维度诊断结论与分阶段学习路径。
                综合匹配度按五个维度加权，各维度权重与扣分明细逐项列出，可回溯至简历原文核对。
              </p>
              <span className="quick-card-link">
                进入人岗匹配
                <Icon name="arrowR" size={13} />
              </span>
            </Link>
          </div>

          {/* 这一条原来写的是“论文支撑与专利”，而数据集里既没有文献表也没有专利字段。
              改成系统真正做到、并且在界面上能当场验证的那一件事。 */}
          <div className="patent-banner">
            <div className="patent-banner-icon">
              <Icon name="doc" size={22} />
            </div>
            <div className="patent-banner-text">
              <h4>界面上的每一项数值均可回溯</h4>
              <p>
                每项能力评分均附判定依据，可逐层展开至原始数据来源；支撑关系的证据可按发布时间与来源类型分别切分，
                用以检验同一结论在各子样本下是否同样成立。
              </p>
            </div>
          </div>
        </div>
      </section>

      <div className="live-bar">
        <div className="wrap">
          <div className="live-bar-inner">
            <span className="live-dot-apple" />
            当前图谱基线 {facts.last?.version} · {facts.last?.date}
          </div>
        </div>
      </div>

      <Footer />
    </div>
  );
}
