/* ============================================================
   数据来源 —— 该岗位判断依据的出处

   回答三个问题：
     ① 三类来源（招聘信息 / 行业新闻 / 学术论文）各自在内容上发生了什么变化
     ② 不同企业、不同媒体对该岗位职责与工作内容的表述差异在哪里
     ③ 这些差异中哪些可直接采纳、哪些需谨慎、哪些需综合判断

   原文摘录就地展开在卡片内，核对时无需再次点击；三列各自限高滚动，
   免得招聘那一列一长就把另外两列的表头顶出屏幕。

   差异研判只平铺“哪一项、判成哪一档、凭什么档”这三件事，
   完整的判定依据收在每行后面 —— 一次看的是分档结果，
   逐条核对判定依据是另一件事，不该在同一屏里抢位置。

   研判之后接一段“跨条件复现”：分档说的是能不能采纳，复现说的是这个结论
   稳不稳。三家企业全在北京、全在同一批次，和三家跨四城跨三批次，
   在分档标签上读数完全一样，可信度却差得远。
   ============================================================ */

import { useEffect, useMemo, useState } from 'react';
import { Icon } from '@/components/Icon';
import type {
  EntitySignal,
  EvidenceRef,
  GraphEdge,
  GraphNode,
  SourceType,
} from '@/types/graph';
import { Sparkline } from '@/components/viz/Primitives';
import { MONTHS } from '@/data/generator';
import { EVIDENCE_BY_ENTITY, jobAttribution, jobRawSource } from '@/data/realGraph';
import { SOURCE_LABEL } from '@/utils/viz';

interface Props {
  job: GraphNode;
  edges: GraphEdge[];
  nodeById: Map<string, GraphNode>;
  /** 三类来源各自的热度走势，贴在各自那一列的表头上 */
  signal?: EntitySignal;
}

/** 一条落到具体对象上的原文 */
interface Anchored {
  ev: EvidenceRef;
  target: string;
  weight: number;
}


/** 一条研判结论。tag 是判定理由的短名，收起时替代整段说明 */
interface Verdict {
  name: string;
  tag: string;
  why: string;
  mix: string;
}

export function JobSourceView({ job, edges, nodeById, signal }: Props) {
  /** 研判里被展开的行。换岗位时清空 —— 上一个岗位展开了哪几条，与这个岗位无关 */
  const [openRows, setOpenRows] = useState<Set<string>>(new Set());
  useEffect(() => {
    setOpenRows(new Set());
  }, [job.id]);

  const toggleRow = (k: string) =>
    setOpenRows((cur) => {
      const next = new Set(cur);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });

  const anchored = useMemo<Anchored[]>(() => {
    /* 只认落在这个岗位邻域内的原文：岗位直达的边，以及它自己那些任务下的能力边。
       借别的岗位的原文来撑场面，“来源”这一块就失去意义了。 */
    const myTasks = new Set(edges.filter((e) => e.kind === 'J-T' && e.source === job.id).map((e) => e.target));
    const pool = edges.filter(
      (e) => e.source === job.id || (e.kind === 'T-S' && myTasks.has(e.source)),
    );
    const out: Anchored[] = [];
    const seen = new Set<string>();
    for (const e of pool) {
      const target = nodeById.get(e.target)?.name ?? e.target;
      for (const ev of e.evidence) {
        const k = `${ev.docId}|${target}`;
        if (seen.has(k)) continue;
        seen.add(k);
        out.push({ ev, target, weight: e.effectiveWeight });
      }
    }
    /* 叠层新岗位一条实测边也没有，上面这一路因而取不到任何原文，而这批岗位
       恰恰全靠论文与新闻的证据句立起来。它们的证据挂在条目本身而不在边上，
       故按条目再取一次，落点写作岗位名 —— 这些句子讲的就是这个岗位。 */
    if (!out.length) {
      for (const ev of EVIDENCE_BY_ENTITY.get(job.id) ?? []) {
        const k = `${ev.docId}|${job.name}`;
        if (seen.has(k)) continue;
        seen.add(k);
        out.push({ ev, target: job.name, weight: 1 });
      }
    }
    return out.sort((a, b) => (a.ev.publishedAt < b.ev.publishedAt ? 1 : -1));
  }, [edges, job.id, job.name, nodeById]);

  const bySource = (t: SourceType) => anchored.filter((a) => a.ev.sourceType === t);

  /* ---------------- ① 招聘信息：不同公司各自强调什么 ---------------- */
  const jdView = useMemo(() => {
    const rows = bySource('jd');
    const byCompany = new Map<string, { targets: Map<string, number>; docs: Anchored[] }>();
    for (const a of rows) {
      const co = a.ev.company ?? '未署名企业';
      let slot = byCompany.get(co);
      if (!slot) {
        slot = { targets: new Map(), docs: [] };
        byCompany.set(co, slot);
      }
      slot.targets.set(a.target, Math.max(slot.targets.get(a.target) ?? 0, a.weight));
      slot.docs.push(a);
    }

    // 每个对象被几家公司提到 —— 共识与分歧全从这一个数出来
    const mentionedBy = new Map<string, Set<string>>();
    for (const [co, slot] of byCompany) {
      for (const t of slot.targets.keys()) {
        if (!mentionedBy.has(t)) mentionedBy.set(t, new Set());
        mentionedBy.get(t)!.add(co);
      }
    }
    const total = byCompany.size || 1;
    const consensus = [...mentionedBy.entries()]
      .filter(([, s]) => s.size >= 2 && s.size / total >= 0.5)
      .map(([t, s]) => ({ name: t, n: s.size }))
      .sort((a, b) => b.n - a.n);
    const only = [...mentionedBy.entries()]
      .filter(([, s]) => s.size === 1)
      .map(([t, s]) => ({ name: t, company: [...s][0] }));

    const companies = [...byCompany.entries()]
      .map(([company, slot]) => ({
        company,
        city: slot.docs[0]?.ev.city,
        latest: slot.docs[0],
        targets: [...slot.targets.entries()].sort((a, b) => b[1] - a[1]).map(([t]) => t),
        docs: slot.docs.length,
        dupes: slot.docs.filter((x) => x.ev.duplicateOf).length,
      }))
      .sort((a, b) => b.targets.length - a.targets.length)
      .slice(0, 5);

    return { rows, companies, consensus, only, companyCount: byCompany.size };
  }, [anchored]);

  /* ---------------- ②③ 新闻与论文：各自在讲什么、重点在哪 ---------------- */
  const outletView = (t: 'news' | 'paper') => {
    const rows = bySource(t);
    const byOutlet = new Map<string, number>();
    for (const a of rows) {
      /* 出处优先取证据自带的那一项（论文为 arXiv，新闻为媒体名）。演示词表下
         没有这一项，其标题写作“机器之心 · XXX”，前半段即来源。 */
      const outlet = a.ev.outlet || a.ev.title.split('·')[0]?.trim() || SOURCE_LABEL[t];
      byOutlet.set(outlet, (byOutlet.get(outlet) ?? 0) + 1);
    }
    const focus = new Map<string, number>();
    for (const a of rows) focus.set(a.target, (focus.get(a.target) ?? 0) + 1);
    return {
      rows: rows.slice(0, 4),
      count: rows.length,
      outlets: [...byOutlet.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4),
      focus: [...focus.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4),
    };
  };
  const newsView = useMemo(() => outletView('news'), [anchored]);
  const paperView = useMemo(() => outletView('paper'), [anchored]);

  /* ---------------- 招聘原文：企业分布与逐条摘录 ----------------
     与上一块的区别在粒度：上一块回答"哪家企业强调了哪一项能力"，落到句；
     这一块只回答"在招这个岗位的是哪些企业、原文长什么样"，落到条。
     两块并存而不合并：核对一项能力要求与通读一条招聘原文是两件事。 */
  const rawSrc = useMemo(() => jobRawSource(job.name), [job.name]);

  /* ---------------- 招聘侧的能力归因 ----------------
     哪一句招聘原文支撑哪一项能力要求。

     算法侧的逐条产出不直接给这一层，但给足了推出它的材料：汇总表逐条列出
     该条要求的技能与各技能下命中的技能点，原文表给出正文全文。技能点是具体的
     技术名与工具名，在正文里以原字面出现，故可反查落点，再由落点定位所在句。
     构建阶段据此逐条定位（data-pipeline/jdraw.mjs 的 attributeOf），
     全批 (条, 能力项) 对中定位到八成九。

     归因按岗位聚合后有三样东西：该项由多少条支撑、由哪些企业和城市提出、
     以及按企业去重的几条支撑句。企业表述侧重读第二样与第三样，
     跨条件复现读第二样。 */
  const jdAttrib = useMemo(() => {
    const attrib = jobAttribution(job.name);
    const items = Object.entries(attrib);
    if (!items.length) return null;

    /* 企业 → 它提出过的能力项，与其中一条支撑句。
       支撑句每项能力只留三条且已按企业去重，故一家企业未必每一项都有句可引；
       取它第一条有句的那一项，卡片上至少有一处可核对的原文。 */
    interface CoSlot {
      skills: { name: string; n: number; alone: boolean }[];
      quote?: { skill: string; text: string; points: string[]; city: string; date: string };
      posts: number;
      city: string;
    }
    const byCompany = new Map<string, CoSlot>();
    const slotOf = (co: string) => {
      let slot = byCompany.get(co);
      if (!slot) byCompany.set(co, (slot = { skills: [], posts: 0, city: '' }));
      return slot;
    };
    for (const [name, a] of items) {
      /* 只有一家企业提出过的项另作标记。判据取 nCompanies —— 那是不截断的计数，
         按 byCompany 清单判会把"清单只列了一家"错读成"只有一家提过" */
      const alone = a.nCompanies === 1;
      for (const [co, n] of a.byCompany) {
        const slot = slotOf(co);
        slot.skills.push({ name, n, alone });
        slot.posts += n;
      }
      for (const q of a.quotes) {
        if (!q.company) continue;
        const slot = slotOf(q.company);
        if (!slot.city) slot.city = q.city;
        if (!slot.quote) {
          slot.quote = { skill: name, text: q.text, points: q.points, city: q.city, date: q.date };
        }
      }
    }

    const companies = [...byCompany.entries()]
      .map(([company, slot]) => ({
        company,
        city: slot.city,
        posts: slot.posts,
        quote: slot.quote,
        skills: slot.skills.sort((a, b) => b.n - a.n),
      }))
      /* 有支撑句的排在前，其次按提出的能力项数、再次按条数。

         支撑句每项能力只留三条且按企业去重，故只有一部分企业被引到；
         若单按能力项数排，入表的五家可能一条原文也没有 —— 这一块要答的是
         "各家怎么说"，标签答得了侧重，答不了表述本身。
         不按条数排：这一块问的是各家侧重什么，发得多的那一家未必写得全。 */
      .sort(
        (a, b) =>
          Number(!!b.quote) - Number(!!a.quote) ||
          b.skills.length - a.skills.length ||
          b.posts - a.posts,
      )
      .slice(0, 5);

    const total = items.reduce((m, [, a]) => Math.max(m, a.n), 0) || 1;
    return {
      companies,
      /* 该岗位各项能力要求，按支撑条数降序。提及率以支撑条数最高的一项为满格 */
      items: items
        .map(([name, a]) => ({ name, ...a, rate: a.n / total }))
        .sort((a, b) => b.n - a.n),
      alone: items.filter(([, a]) => a.nCompanies === 1).length,
      nItems: items.length,
    };
  }, [job.name]);

  /* ---------------- 差异研判：可直接采纳 / 需谨慎采信 / 需综合分析 ----------------

     判据分两路取数，缺一路则该条只按另一路判：

     · 招聘一侧读句级归因（jdraw 的 attrib）。它逐项给出"由多少条招聘信息支撑、
       由多少家企业与多少座城市提出"，这三个数是不截断的实测计数。此前这一档
       改读边上的证据条目去数企业名，而边上的证据只有论文与新闻两类 ——
       招聘侧一条也没有，独立企业数因而恒为零或一，全部条目一律落进"孤证"，
       "可直接采纳"这一栏于是恒空。

     · 前瞻一侧读边的三源构成与基图权重，回答"招聘市场跟上没有"。

     顺序仍是"先挑毛病、再看分歧、剩下的才算共识"。 */
  const verdicts = useMemo(() => {
    const myTasks = new Set(edges.filter((e) => e.kind === 'J-T' && e.source === job.id).map((e) => e.target));
    const pool = edges
      .filter((e) => e.source === job.id || (e.kind === 'T-S' && myTasks.has(e.source)))
      .sort((a, b) => b.effectiveWeight - a.effectiveWeight)
      .slice(0, 22);

    const attrib = jobAttribution(job.name);

    const ok: Verdict[] = [];
    const care: Verdict[] = [];
    const think: Verdict[] = [];

    /** 三源构成写成占比，不写原始小数：这三个数是同一条边上的构成比例，
        写作 0.8524170421958214 时既读不出它是个比例，也没有那么多有效位 */
    const mixText = (m: { jd: number; paper: number; news: number }) => {
      const sum = m.jd + m.paper + m.news;
      if (sum <= 0) return '';
      const pct = (v: number) => `${((v / sum) * 100).toFixed(0)}%`;
      return [
        m.jd > 0 ? `招聘 ${pct(m.jd)}` : '',
        m.paper > 0 ? `论文 ${pct(m.paper)}` : '',
        m.news > 0 ? `新闻 ${pct(m.news)}` : '',
      ]
        .filter(Boolean)
        .join(' · ');
    };

    const seen = new Set<string>();
    for (const e of pool) {
      const name = nodeById.get(e.target)?.name ?? e.target;
      if (seen.has(name)) continue;
      seen.add(name);

      const at = attrib[name];
      /* 提出过该项的独立企业数。归因表覆盖不到时退回边上的证据 */
      const jd = e.evidence.filter((x) => x.sourceType === 'jd');
      const cos = at ? at.nCompanies : new Set(jd.map((x) => x.company).filter(Boolean)).size;
      const cities = at?.nCities ?? 0;
      const posts = at?.n ?? jd.length;
      const dupes = jd.filter((x) => x.duplicateOf).length;
      const kinds = [e.sourceMix.jd, e.sourceMix.paper, e.sourceMix.news].filter((x) => x > 0).length;
      const mix = mixText(e.sourceMix);
      const foreShare = e.effectiveWeight > 0 ? e.deltaWeight / e.effectiveWeight : 0;

      if (e.baseWeight === 0) {
        think.push({
          name,
          tag: '仅前瞻信号',
          why: `招聘信息中尚未出现，仅有论文与新闻讨论；已连续 ${e.unconfirmedMonths} 个月未被招聘市场确认，权重按月衰减。暂作趋势观察，不写入正式岗位定义。`,
          mix,
        });
      } else if (jd.length > 0 && dupes / jd.length >= 0.34) {
        care.push({
          name,
          tag: '模板复制',
          why: `支撑该项的招聘信息中有 ${dupes}/${jd.length} 条判定为模板副本（相似度超过 95%），去重后独立来源不足，该部分仅按一条证据计入。`,
          mix,
        });
      } else if (cos <= 1) {
        care.push({
          name,
          tag: '孤证',
          why: `全库仅 ${cos === 1 ? '一家企业' : '单一来源'}如此表述，其余企业的岗位描述中未出现，可能属于该企业内部的组织分工，不宜作为行业共识。`,
          mix,
        });
      } else if (cos < 3) {
        care.push({
          name,
          tag: `${cos} 家提及`,
          why: `提出该项的企业仅 ${cos} 家、支撑 ${posts.toLocaleString()} 条，独立复现的家数尚不足以判为行业共识，宜待样本增加后复核。`,
          mix,
        });
      } else if (!e.explicitLink && e.confidence < 0.75) {
        care.push({
          name,
          tag: '统计共现',
          why: `原文未直接写明该任务需要该能力，此关系由同一份招聘信息内的共现统计推出，置信度 ${(e.confidence * 100).toFixed(0)}%。共现不等于因果，需人工确认。`,
          mix,
        });
      } else if (foreShare >= 0.22) {
        think.push({
          name,
          tag: '要求强度滞后',
          why: `招聘信息已有该项要求，但强度落后于论文与新闻的讨论热度：综合权重中 ${(foreShare * 100).toFixed(0)}% 来自前瞻修正。学术侧领先、招聘侧仍在跟进，写入岗位定义时应标注为上升项而非现状。`,
          mix,
        });
      } else if (e.status === 'weakening') {
        think.push({
          name,
          tag: '招聘侧回落',
          why: `招聘信息中的出现频率正在回落，而${e.sourceMix.paper || e.sourceMix.news ? '论文与新闻仍在讨论' : '尚无其他来源可佐证这一回落'}：两侧走向不一致，暂不建议移除，待下一批数据后再行判定。`,
          mix,
        });
      } else if (kinds >= 2) {
        ok.push({
          name,
          tag: '跨源交叉验证',
          why: `${cos.toLocaleString()} 家企业的招聘信息独立提及（支撑 ${posts.toLocaleString()} 条${cities ? `、覆盖 ${cities} 座城市` : ''}），且有${e.sourceMix.paper ? '学术论文' : '行业新闻'}佐证，跨源交叉验证通过。`,
          mix,
        });
      } else {
        /* 成熟能力在论文里本来就不会被专门讨论（没人写论文论证"要会写 SQL"），
           拿"没有学术佐证"去否掉它是误伤。多家独立写明本身就是够强的证据。 */
        ok.push({
          name,
          tag: `${cos.toLocaleString()} 家独立复现`,
          why: `${cos.toLocaleString()} 家企业的招聘信息各自写明了这项要求，支撑 ${posts.toLocaleString()} 条${cities ? `、覆盖 ${cities} 座城市` : ''}，措辞互不相同，不构成模板复制。此类成熟能力在学术侧本就少有专门讨论，招聘侧的独立复现已足以支撑。`,
          mix,
        });
      }
    }
    return { ok: ok.slice(0, 5), care: care.slice(0, 4), think: think.slice(0, 4) };
  }, [edges, job.id, job.name, nodeById]);

  const dupTotal = anchored.filter((a) => a.ev.duplicateOf).length;

  /* 三源栏的首现与走势线只在该源确有条目时画。

     此前两者各读各的：条数与摘录读锚点到本岗位的证据表，走势线与首现读逐窗的
     三源强度序列。两处对不上时，界面上就会出现"新闻 0 条、暂无报道，右边却有
     一条新闻曲线和一个首现月"，以及反过来"论文尚未出现，下面却列着上百篇摘录"。
     两者不同源是数据本身的事实：逐窗强度按叠层记录的 src 分流，而证据表按文档
     归属分列，同一条信号在两处可以落在不同的来源上。界面不去调和这一点，
     只按"这一源在本岗位下有没有可核对的条目"决定画不画 —— 有条目才画曲线，
     首现取该源最早那一条证据的发布月，与下方摘录逐条对得上。

     这个月份说的是"这一栏所列原文里最早的一篇发表于何时"，不是"这个岗位首次
     出现于何时"，两者不可混作一谈：论文与新闻的证据挂在能力与任务上，同一篇
     论文支撑某项能力，凡要求该能力的岗位都会把它收进本栏，故各岗位的最早一篇
     多数落在同一个月（本批为 2022-07，即论文数据的起点）。原先此处写作"首现"，
     一屏之内换几个岗位都是同一个月，读者只能理解成算错了。故改写为"最早一篇"，
     并在下方口径里写明这批原文的范围。

     招聘信息一栏另有一层：该栏的原文不带来源类型为 jd 的证据条目（招聘一侧
     逐条统计在汇总表里，不进证据表），jdFirst 恒为空，取的一直是逐窗信号的
     firstJdAt —— 那是该岗位在招聘数据里首次出现的观测窗口，与"最早一篇原文"
     不是一回事，措辞因而分开写。 */
  /* 一源的时间跨度与逐月篇数，两者同出这一栏所列的那批原文。

     走势线此前读的是逐窗的三源强度序列，与本栏的条数不同源：叠层强度按条目的
     来源分流，而证据表按文档归属分列，同一条信号在两处可以落在不同的来源上，
     于是出现"写着一百三十篇、右边却没有线"。现改为按这批原文自己的发布月计数，
     线下的总量即那个篇数，两者对得上。

     报的是区间而非"最早一篇"：最早那一篇多数落在数据起点上（论文自 2022-07、
     新闻自 2022-10），换几个岗位都是同一个月，读不出差别；而区间的右端与
     其间有原文的月数逐岗位不同 —— 同为百余篇，有的铺在三十四个月里，
     有的只铺在十八个月里，那是两回事。 */
  const spanOfSource = (t: SourceType) => {
    const perMonth = new Map<string, number>();
    for (const a of anchored) {
      if (a.ev.sourceType !== t) continue;
      const m = a.ev.publishedAt?.slice(0, 7);
      if (!m) continue;
      perMonth.set(m, (perMonth.get(m) ?? 0) + 1);
    }
    if (perMonth.size === 0) return null;
    const keys = [...perMonth.keys()].sort();
    return {
      first: keys[0],
      last: keys[keys.length - 1],
      nMonths: keys.length,
      /* 铺到观测窗口轴上：各源共用同一条横轴，几条线才比得出先后 */
      series: MONTHS.map((m) => perMonth.get(m) ?? 0),
    };
  };
  const jdSpan = spanOfSource('jd');
  const newsSpan = spanOfSource('news');
  const paperSpan = spanOfSource('paper');


  /** 一档研判：一行一条，判定依据点开才展开；条目名单独可点，用来切换下方的复现检验 */
  const verdictList = (bucket: string, items: Verdict[], empty: string) => (
    <ul className="jv-list">
      {items.map((v) => {
        const k = `${bucket}:${v.name}`;
        const on = openRows.has(k);
        return (
          <li key={v.name} className={on ? 'jv-item open' : 'jv-item'}>
            <div className="jv-row">
              <span className="jv-name">
                <strong>{v.name}</strong>
              </span>
              <span className="jv-tag">{v.tag}</span>
              <button className="jv-more" onClick={() => toggleRow(k)} aria-expanded={on}>
                判定依据
                <Icon name="chevronD" size={12} />
              </button>
            </div>
            {on && (
              <div className="jv-why">
                <p>{v.why}</p>
                <span className="jv-mix">{v.mix}</span>
              </div>
            )}
          </li>
        );
      })}
      {items.length === 0 && <li className="jsrc-none">{empty}</li>}
    </ul>
  );

  return (
    /* 带 id：首页快报点进来时要落到这一节，那条报道的原文就列在下面的新闻一列里 */
    <section className="panel jsrc" id="job-source">
      <header className="panel-hd">
        <div className="panel-hd-text">
          <h2>数据来源</h2>
        </div>
        <div className="pn-act">
        </div>
      </header>

      <div className="panel-bd">
        {/* 叠层新岗位不出招聘信息一列：这批岗位尚未进入招聘市场，算法侧的
            叠层产物里它们的证据只有论文与新闻两类，招聘侧一条也没有
            （hits 为零、无实测边、逐条证据的 src 全为 papers / news）。
            留着这一列，三栏里恒有一栏是空的。 */}
        <div className={job.emerging ? 'jsrc-cols two' : 'jsrc-cols'}>
          {!job.emerging && (
          <article className="jsrc-col jd">
            <div className="jsrc-hd">
              <span className="src src-jd">招聘信息</span>
              {/* 条数取招聘原文表按 jobid 连接后的实测量，不读锚点表：
                  锚点表只收正文里点出了具体工具的那一部分，按它计数会把
                  "有一成多的条目没写具体工具"读成"这些企业没在招这个岗位" */}
              <b>{(rawSrc?.n ?? jdView.rows.length).toLocaleString()} 条</b>
              <small>
                来自 {(rawSrc?.nCompanies ?? jdView.companyCount).toLocaleString()} 家企业
                {rawSrc ? ` · ${rawSrc.nCities} 座城市` : ''}
                {dupTotal > 0 && ` · 模板副本 ${dupTotal} 条已折叠`}
              </small>
              {/* 走势线要有逐窗序列才画得出，而条数与月份不依赖它。此前整块以
                  signal 为条件，缺序列的岗位连月份也一并不显示，读者看到的是
                  "写着上百条，右边却空着一格"。现改为条数在则出这一格，
                  线只在有序列时画。 */}
              {/* 招聘一栏的条数取自汇总表按岗位连接后的实测量，逐条不带日期，
                  故这一路仍读逐窗的招聘序列——两者同为招聘侧的统计，口径一致。 */}
              {(rawSrc?.n || jdSpan) && (
                <span className="jsrc-spark">
                  {signal && <Sparkline values={signal.jd} color="var(--src-jd)" w={104} h={26} />}
                  <em>{signal?.firstJdAt ? `自 ${signal.firstJdAt} 起在招` : '尚未在招'}</em>
                </span>
              )}
            </div>

            <div className="jsrc-scroll">
              <div className="jsrc-blk">
                <h4>各企业的表述侧重</h4>
                <table className="jsrc-tbl">
                  <tbody>
                    {/* 归因产出后，这张表由 jdAttrib 铺；算法侧若日后直接给出
                        句级归因，jdView 那一路即自动接管，两者结构一致。 */}
                    {(jdAttrib?.companies.length ? jdAttrib.companies : []).map((c) => (
                      <tr key={c.company}>
                        <th>
                          {c.company}
                          {c.city && <small>{c.city}</small>}
                        </th>
                        <td>
                          <span className="jsrc-tags">
                            {c.skills.slice(0, 5).map((t) => (
                              <span
                                key={t.name}
                                className={t.alone ? 'jsrc-tag alone' : 'jsrc-tag'}
                                title={`该企业 ${t.n.toLocaleString()} 条招聘信息提出此项`}
                              >
                                {t.name}
                              </span>
                            ))}
                            {c.skills.length > 5 && <span className="jsrc-more">另 {c.skills.length - 5} 项</span>}
                          </span>
                          {c.quote && (
                            <>
                              <p className="jsrc-quote">“{c.quote.text}”</p>
                              {/* 引文下写明它支撑的是哪一项、由哪几个技能点定位到 ——
                                  没有这一行，读者只看见一句招聘原文，看不出它凭什么
                                  被归到上面那几个标签中的某一个 */}
                              <p className="jsrc-legend">
                                支撑<b>{c.quote.skill}</b>一项，定位词{' '}
                                {c.quote.points.slice(0, 4).join('、')}
                                {c.quote.date && ` · ${c.quote.date}`}
                              </p>
                            </>
                          )}
                        </td>
                      </tr>
                    ))}
                    {!jdAttrib?.companies.length &&
                      jdView.companies.map((c) => (
                        <tr key={c.company}>
                          <th>
                            {c.company}
                            {c.city && <small>{c.city}</small>}
                          </th>
                          <td>
                            <span className="jsrc-tags">
                              {c.targets.slice(0, 4).map((t) => (
                                <span
                                  key={t}
                                  className={
                                    jdView.only.some((o) => o.name === t && o.company === c.company)
                                      ? 'jsrc-tag alone'
                                      : 'jsrc-tag'
                                  }
                                >
                                  {t}
                                </span>
                              ))}
                            </span>
                            {c.latest && <p className="jsrc-quote">“{c.latest.ev.snippet}”</p>}
                            {c.dupes > 0 && (
                              <p className="jsrc-flag">
                                该企业 {c.docs} 条中有 {c.dupes} 条与更早的广告高度重复，合并后按一条计入
                              </p>
                            )}
                          </td>
                        </tr>
                      ))}
                    {!jdAttrib?.companies.length && jdView.companies.length === 0 && (
                      <tr>
                        <td colSpan={2} className="jsrc-none">
                          {rawSrc
                            ? '该岗位的招聘原文里未定位到可归因的能力要求：正文中没有出现体系内技能点的字面表述。原文与企业分布见下两块。'
                            : '该岗位暂无可展示的招聘信息原文。'}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
                <p className="jsrc-legend">
                  <i className="jsrc-tag alone">底色标出</i>
                  的能力仅该企业提及，其余企业的岗位描述中未出现。
                  {jdAttrib && (
                    <>
                      {' '}本表由该岗位的 {jdAttrib.nItems} 项能力要求归因而来，其中{' '}
                      {jdAttrib.alone} 项只有一家企业提出。
                    </>
                  )}
                </p>
              </div>

              {rawSrc && (
                <div className="jsrc-blk">
                  <h4>
                    在招企业
                  </h4>
                  <ul className="jsrc-cos">
                    {rawSrc.companies.slice(0, 6).map(([name, n]) => (
                      <li key={name}>
                        <b>{name}</b>
                        <em>{n.toLocaleString()} 条</em>
                      </li>
                    ))}
                  </ul>
                  <p className="jsrc-legend">
                    共 {rawSrc.nCompanies.toLocaleString()} 家企业 · {rawSrc.nCities} 座城市 ·{' '}
                    {rawSrc.n.toLocaleString()} 条招聘信息
                  </p>
                </div>
              )}

              {rawSrc && rawSrc.samples.length > 0 && (
                <div className="jsrc-blk">
                  <h4>
                    招聘原文摘录
                  </h4>
                  <ul className="jsrc-raw">
                    {rawSrc.samples.map((s) => (
                      <li key={`${s.w}|${s.id}`}>
                        <p className="jsrc-raw-hd">
                          <b>{s.company || '未署名企业'}</b>
                          <em>
                            {[s.city, s.salary, s.date].filter(Boolean).join(' · ')}
                          </em>
                        </p>
                        <p className="jsrc-quote">“{s.text}”</p>
                        <p className="jsrc-legend">
                          {s.title || job.name} · 原文 {s.full.toLocaleString()} 字
                        </p>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </article>
          )}

          {/* ---------------- 行业新闻 ---------------- */}
          <article className="jsrc-col news">
            <div className="jsrc-hd">
              <span className="src src-news">行业新闻</span>
              <b>{newsView.count} 条</b>
              <small>{newsView.outlets.map(([o, n]) => `${o} ${n}`).join(' · ') || '暂无'}</small>
              {newsView.count > 0 && (
                <span className="jsrc-spark">
                  {newsSpan && (
                    <Sparkline values={newsSpan.series} color="var(--src-news)" w={104} h={26} />
                  )}
                  <em>
                    {newsSpan
                      ? `${newsSpan.first} — ${newsSpan.last} · ${newsSpan.nMonths} 个月有`
                      : '原文未标发布月'}
                  </em>
                </span>
              )}
            </div>

            <div className="jsrc-scroll">
              {newsView.focus.length > 0 && (
                <div className="jsrc-blk">
                  <h4>报道最集中的方向</h4>
                  <p className="jsrc-focus">
                    {newsView.focus.map(([t, n]) => (
                      <b key={t}>
                        {t}
                        <em>{n}</em>
                      </b>
                    ))}
                  </p>
                </div>
              )}

              <div className="jsrc-blk">
                <h4>原文摘录</h4>
                <ul className="jsrc-quotes">
                  {/* 同一篇文档可以抽出多条摘录，docId 单用作 key 会撞号 */}
                  {newsView.rows.map((a, i) => (
                    <li key={`${a.ev.docId}#${i}`}>
                      <div className="jsrc-q-meta">
                        <span>{a.ev.title}</span>
                        <em>{a.ev.publishedAt}</em>
                      </div>
                      <p>{a.ev.snippet}</p>
                    </li>
                  ))}
                  {newsView.rows.length === 0 && <li className="jsrc-none">暂无相关报道。</li>}
                </ul>
              </div>
            </div>
          </article>

          {/* ---------------- 学术论文 ---------------- */}
          <article className="jsrc-col paper">
            <div className="jsrc-hd">
              <span className="src src-paper">学术论文</span>
              <b>{paperView.count} 篇</b>
              <small>{paperView.outlets.map(([o, n]) => `${o} ${n}`).join(' · ') || '暂无'}</small>
              {paperView.count > 0 && (
                <span className="jsrc-spark">
                  {paperSpan && (
                    <Sparkline values={paperSpan.series} color="var(--src-paper)" w={104} h={26} />
                  )}
                  <em>
                    {paperSpan
                      ? `${paperSpan.first} — ${paperSpan.last} · ${paperSpan.nMonths} 个月有`
                      : '原文未标发表月'}
                  </em>
                </span>
              )}
            </div>

            <div className="jsrc-scroll">
              {paperView.focus.length > 0 && (
                <div className="jsrc-blk">
                  <h4>研究最集中的方向</h4>
                  <p className="jsrc-focus">
                    {paperView.focus.map(([t, n]) => (
                      <b key={t}>
                        {t}
                        <em>{n}</em>
                      </b>
                    ))}
                  </p>
                </div>
              )}

              <div className="jsrc-blk">
                <h4>原文摘录</h4>
                <ul className="jsrc-quotes">
                  {paperView.rows.map((a, i) => (
                    <li key={`${a.ev.docId}#${i}`}>
                      <div className="jsrc-q-meta">
                        <span>{a.ev.title}</span>
                        <em>{a.ev.publishedAt}</em>
                      </div>
                      <p>{a.ev.snippet}</p>
                    </li>
                  ))}
                  {paperView.rows.length === 0 && <li className="jsrc-none">暂无相关论文。</li>}
                </ul>
              </div>
            </div>
          </article>
        </div>

        {/* ---------------- 差异研判 ----------------
            三档全空时整节不画：叠层新岗位没有实测边，三档必然各为零项，
            画出来是三个写着“暂无”的空格子。 */}
        {verdicts.ok.length + verdicts.care.length + verdicts.think.length > 0 && (
        <div className="jsrc-verdict">
          <div className="jsrc-vhd">
            <h3>差异研判</h3>
          </div>

          <div className="jsrc-vcols">
            <section className="jv ok">
              <header>
                <b>可直接采纳</b>
                <em>{verdicts.ok.length} 项</em>
                <small>多家企业独立提及，且有第二类来源佐证</small>
              </header>
              {verdictList('ok', verdicts.ok, '暂无跨源交叉验证通过的条目。')}
            </section>

            <section className="jv care">
              <header>
                <b>需谨慎采信</b>
                <em>{verdicts.care.length} 项</em>
                <small>孤证，或原文本身是模板复制</small>
              </header>
              {verdictList('care', verdicts.care, '本期没有检出孤证或模板复制项。')}
            </section>

            <section className="jv think">
              <header>
                <b>需综合分析</b>
                <em>{verdicts.think.length} 项</em>
                <small>三类来源表述不一致，需人工判断</small>
              </header>
              {verdictList('think', verdicts.think, '三类来源目前基本一致。')}
            </section>
          </div>
        </div>
        )}
      </div>
    </section>
  );
}
