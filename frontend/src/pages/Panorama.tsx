/* =========================================================
   全景图谱 —— 按“先结论、后细节”重排的三层结构

   ① 分析引擎：一排快速定位按钮 + 一个查询对话框，
      回答带指向本页具体位置的链接。
   ② 前瞻分析：能力演变时间线、前瞻热度排行、三源对照，
      三张图共用同一批逐月三源强度序列。
   ③ 岗位能力全景图：三段并排的流图，四个切换下拉与时间游标都收在图内顶部，
      整块置于页面末尾 —— 它是最细的一层，不占第一屏。

   ---------------------------------------------------------
   主图为什么从同心环换成三段并排的条形图

   赛题对全景图谱的规定是"展示领域内岗位的能力要求，颗粒度到技能点级别，
   可以按技术栈和级别切换视图"。原先的能力棱镜是一张同心环图，圆心是
   所选岗位 —— 它把"领域内岗位"压成了一个下拉框里的单选项，一次只讲一个，
   读者要看第二个岗位只能换一次口径重看一遍，"领域内"这三个字落不了地。

   换成条形图之后，岗位是图上的一整段，与核心任务、技能两段并排。
   三段从左到右就是 岗位 → 任务 → 技能，也是算法侧的数据流向；连线把
   "这些岗位 → 要求这些任务 → 要求这些技能"一次讲完，每一行都标得出名字，
   不再是环上一段标不出名字的弧。

   技能点这一层落在技能行的下钻里：点开一项技能，其技能点就地展开在该行下方。
   不并排画作第四段，原因是数量。技能为封闭体系五十四项，一屏读得完；
   技能点是随市场文本生长的开放集合，本批逾两万项，铺成一列时行高压到
   一像素以下，条长之间读不出差别，"颗粒度到技能点"反而落不了地。
   下钻则是逐项打开，每次只看一项技能下的十来个技能点，条长才有分辨率。

   两条切换轴照旧：技术栈取自招聘信息汇总表逐条标注的技术栈一列，按岗位归一为
   占比后落在岗位这一段上；级别落在岗位职级上。

   四个切换项一律做成下拉，与时间游标同框贴在图的顶部：一屏之内换一次
   就重画一次图，因果才连得上；平铺成上百枚 chip 时，选择器自己比图还高。
   ========================================================= */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useSearchParams } from 'react-router-dom';
import { useDataset } from '@/api/client';
import { useSize } from '@/hooks/useSize';
import { Tooltip, type TipState } from '@/components/common/Tooltip';
import { JobCapabilityFlow, type FlowLayoutInfo } from '@/components/viz/JobCapabilityFlow';
import {
  buildFlow,
  rowAt,
  stackOptions,
  totalsOf,
  type ChangeKind,
  type FlowRow,
} from '@/data/panoramaFlow';
import {
  jobCategories as profileCategories,
  JOB_LEVELS,
  type JobLevel,
  type ProfileScope,
} from '@/data/jobProfile';
import { MIX_COLORS, PROF_COLORS, PROF_LEVELS, PROF_UNKNOWN, SKILL_TYPES as MIX_TYPES } from '@/data/explore';
import { PrismTimeAxis, monthText } from '@/components/viz/PrismTimeAxis';
import { SignalTimeline } from '@/components/viz/SignalTimeline';
import { SourceCompare } from '@/components/viz/SourceCompare';
import { DualSparkline } from '@/components/viz/Primitives';
import { PageGuide } from '@/components/common/PageGuide';
import { NextSteps, type StepItem } from '@/components/common/NextSteps';
import { JumpDock } from '@/components/common/JumpDock';
import { stationOf } from '@/data/journey';
import { EngineChat, type ChatAnswer } from '@/components/panels/EngineChat';
import { Icon } from '@/components/Icon';
import { Footer } from '@/components/Footer';
import type { NodeKind, SkillType } from '@/types/graph';
import { ORPHAN_CLUSTER, REAL_MERGES } from '@/data/realTaxonomy';
import { KIND_LABEL, SOURCE_LABEL } from '@/utils/viz';
import { DROPPED_EDGES, REAL_MONTHS } from '@/data/realGraph';
import { monthDiff } from '@/utils/format';
import '@/styles/panorama.css';

/** 岗位段与任务段不设绘制上限：所选大类关联到多少岗位、多少任务就画多少。

    此前两段各设 60 与 40 条。岗位一段从未被截到过（单个大类最多 36 个），
    任务一段则会 —— 全库 98 项任务里，软件开发一类关联到的就超过四十项，
    图上只画前四十项而把其余的收进一行小字，等于把"这个大类要做哪些事"
    答了一半。两段合计不过百余行，与右段的技能行数同量级，画得下。 */
const JOB_LIMIT = Number.POSITIVE_INFINITY;
const TASK_LIMIT = Number.POSITIVE_INFINITY;


/*
 * 撤掉的控件，理由记在这里，免得下次又被当成"漏了"补回来：
 *
 * · 技能点成熟度（基础 / 进阶 / 前沿）。它是按首现年份分出来的三档，
 *   源文件没有这一维；"这一项是哪年起来的"由时间游标回答得更准，
 *   一个分档标签还会与"岗位级别"重名。
 * · 前瞻程度滑杆。它改不动主图，只改排序与连线粗细；前瞻这一维本身
 *   仍在页面上 —— 结论区的三源前瞻分析整块讲的就是它。
 * · 能力粒度（技能点 / 能力组）。第三段一律画技能，技能点由行内下钻打开，
 *   能力组与能力维度仍在图上，作为技能行右侧那两级括号出现。
 *   粒度因而由点击本身表达，不必再占一个下拉。
 * · 逐月走势列与连线模式。走势由时间游标承担，连线固定只画选中项。
 */

/* 三源前瞻分析只看技能这一层，不设条目类型切换。
   叠层的既有条目增强按体系编码记录，落在岗位、任务、技能三层上，其中技能层的
   信号最全（151 条）；任务层稀疏，岗位层不参与（先出现在论文里的是它要求的
   那批能力，不是岗位本身）；技能点一层的叠层产出是新实体而非对既有条目的增强，
   没有"论文先于招聘出现"这一对时刻可比。四档里只有技能一档处处有数，
   另外三档留在界面上只是三个多半空着的开关。 */
const FORE_KIND: NodeKind = 'skill';

export function Panorama() {
  const d = useDataset();
  /** 顶栏搜索或岗位洞察页跳过来时带着 ?focus=<节点 id>，直接选中那一项 */
  const [params] = useSearchParams();
  const focus = params.get('focus');

  /** 岗位类别：图上左段的取值范围，一次只看一类。
      "领域整体"这一档撤掉了 —— 十几个大类平均出来的一张图，
      峰值被摊平到只剩单类的两成，读者看到的是一圈贴底的矮条。 */
  const [category, setCategory] = useState<string>(() => profileCategories()[0]?.name ?? ORPHAN_CLUSTER);
  /** 赛题“按级别切换视图”：同一批岗位在三档职级下的能力要求 */
  const [level, setLevel] = useState<JobLevel>('mid');
  /** 赛题“按技术栈切换视图”：null 为不限技术栈 */
  const [stack, setStack] = useState<string | null>(null);
  const [flowInfo, setFlowInfo] = useState<FlowLayoutInfo | null>(null);
  const [tip, setTip] = useState<TipState | null>(null);

  /* ---------------- 时间游标 ----------------
     默认停在最后一个实测月，不是轴末：轴末那六个月是外推。
     默认落在实测末月还有一层意思 —— 此时整张图与加时间维之前完全一致，
     时间维是"想看才往回拖"的一层，不是每次进页面都要先关掉的干扰。 */
  const timeline = d.prismTimeline;
  const forecastFrom = timeline.forecastFrom
    ? timeline.months.indexOf(timeline.forecastFrom)
    : timeline.months.length;
  /** 最后一个实测月 */
  const observedLast = Math.max(0, (forecastFrom > 0 ? forecastFrom : timeline.months.length) - 1);
  const [cursor, setCursor] = useState(observedLast);
  const [baseline, setBaseline] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(focus);

  /* 三源前瞻分析的口径筛选。条目类型固定为技能，只留"待招聘市场确认"一档 */
  const [pendingOnly, setPendingOnly] = useState(false);
  const [pick, setPick] = useState<string | null>(null);

  /* ---------------- 跨块跳转与高亮 ----------------
     对话框里的动作按钮要能把某一块滚到眼前并让人看清是哪一块。
     scroll-margin-top 由 CSS 给（顶栏是 sticky 的），这里只管滚和打标记。 */
  const [spot, setSpot] = useState<string | null>(null);
  const spotTimer = useRef<number | null>(null);
  const jump = useCallback((id: string) => {
    const el = document.getElementById(id);
    el?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setSpot(id);
    if (spotTimer.current) window.clearTimeout(spotTimer.current);
    spotTimer.current = window.setTimeout(() => setSpot((s) => (s === id ? null : s)), 2200);
  }, []);
  useEffect(
    () => () => {
      if (spotTimer.current) window.clearTimeout(spotTimer.current);
    },
    [],
  );
  const spotCls = (id: string) => (spot === id ? 'spot' : undefined);

  /* 带 #sec-flow 进来时直接落到主图上。

     首页的"四层结构"一段指的就是这张图，而它在页面末段：不滚过去时，
     点进来看到的是分析引擎与前瞻分析两块，与那句话说的不是一回事。
     延一帧再滚 —— 图的高度由行数定，行数要等 buildFlow 出来才知道，
     挂载当帧滚过去会停在错的位置。 */
  const wantAnchor = useLocation().hash.replace('#', '');
  useEffect(() => {
    if (wantAnchor !== 'sec-flow') return;
    const t = window.setTimeout(() => jump('sec-flow'), 120);
    return () => window.clearTimeout(t);
  }, [wantAnchor, jump]);

  const flowBox = useSize<HTMLDivElement>();

  /* 顶栏搜索或岗位洞察页带 ?focus= 进来。岗位现在是图上的一整段，
     所以无论哪一层，带进来的 id 一律选中同一个东西：图上那一行。
     岗位再额外把大类切到它所属的那一类，否则那一行不在图上。 */
  /* 带参数进来时，页首职责条要能说出"你是为了哪一项过来的" ——
     此前页面确实会选中那一项，但没有任何一处把这件事写出来，
     从首页榜单点进来的人只看到一张图上不知为何有一行是高亮的。 */
  const [landed, setLanded] = useState<string | null>(null);

  useEffect(() => {
    if (!focus) return;
    const n = d.nodes.find((x) => x.id === focus);
    if (!n) return;
    setLanded(n.name);
    if (n.kind === 'job') setCategory(n.topCategory || ORPHAN_CLUSTER);
    /* 带进来的若是三源前瞻分析所收的那一层条目（首页"本期重点前瞻项"即由此跳来），
       落点是三源对照那一块：卡片上写的首现时间与三条曲线都在那里。
       此前只把它选中而不滚动，页面停在顶端，点进来的人看不出这一趟去了哪。
       其余条目仍落到主图。等一帧再滚，让选中先渲染出来。

       判据此前写作"它在 signals 里"，而 signals 逐节点都产一条，这个条件恒真：
       从岗位洞察页带一个岗位 id 跳来时也会滚到三源对照，且把 pick 设成一个
       前瞻榜上没有的 id，对照区因而退回榜首那一条 —— 落点与来路对不上号。

       前瞻这一层的条目多为叠层新技能，并不在主图的三段里，故这一支不设主图的
       选中项：选中一个图上没有的 id，整幅图会被压暗而没有一行亮着。 */
    const asSignal = d.signals.some((x) => x.entityId === focus && x.kind === FORE_KIND);
    if (asSignal) {
      setPick(focus);
      setSelectedId(null);
    } else {
      setSelectedId(focus);
    }
    const t = window.setTimeout(() => jump(asSignal ? 'sec-compare' : 'sec-flow'), 160);
    return () => window.clearTimeout(t);
  }, [focus, d.nodes, d.signals, jump]);

  /* 播放：每 460ms 推一个月，与条长 320ms 的过渡首尾相接，看起来是连续走的。
     走到实测末月就停 —— 一是不该自动把读者带进外推区，二是循环播放会让人
     分不清"又转回去了"和"数据真的降回去了"。外推那六个月靠自己拖过去。 */
  useEffect(() => {
    if (!playing) return;
    if (cursor >= observedLast) {
      setPlaying(false);
      return;
    }
    const t = window.setTimeout(() => setCursor((i) => Math.min(observedLast, i + 1)), 460);
    return () => window.clearTimeout(t);
  }, [playing, cursor, observedLast]);

  /** 播放时从头开始：停在末月点播放却什么都不动，那个按钮就是坏的 */
  const togglePlay = useCallback(
    (v: boolean) => {
      if (v && cursor >= observedLast) setCursor(0);
      setPlaying(v);
    },
    [cursor, observedLast],
  );

  /** 时间轴的里程碑：图谱版本节点，用来解释某一段为什么在那时候起量 */
  const milestones = useMemo(() => d.versions.map((v) => ({ month: v.date, label: v.label })), [d.versions]);

  const stats = useMemo(() => {
    const c = (k: string) => d.nodes.filter((n) => n.kind === k).length;
    const emerging = d.nodes.filter((n) => n.kind === 'job' && n.emerging).length;
    return {
      /* 岗位这一格分开写既有与新岗位两个数。整份图谱含新岗位共 105 个，
         而下方类别下拉里各类之和是 100 —— 新岗位不归入任何一个一级类别，
         也不进这张按市场需求取数的图。两个数同屏出现而对不上，
         不写明就成了"数据没更新"。 */
      job: c('job') - emerging,
      jobEmerging: emerging,
      task: c('task'),
      skill: c('skill'),
      sp: c('skillpoint'),
      pending: d.nodes.filter((n) => n.origin === 'overlay').length,
      edges: d.edges.length,
    };
  }, [d.nodes, d.edges]);

  /* ---------------- 筛选口径：四个下拉同时管三段 ---------------- */

  /** 岗位类别的可选项。按该类的岗位数排序，"无一级归属"固定在末位 */
  const jobCategories = useMemo(() => profileCategories(), []);

  /** 图上左段的取值范围 —— 下拉里选的那一个大类 */
  const scope = useMemo<ProfileScope>(
    () => ({ kind: 'category', id: category, label: category, jobCount: 0 }),
    [category],
  );

  /* 软硬两类一律全要。这一维此前是图上的第四个下拉，而它筛的是技能点、
     筛完之后中段与右段一并收窄 —— 与另三根轴筛的是同一批行，读者却分不清
     图上少掉的那一截是哪一根轴筛掉的。软硬构成仍在图上：岗位条按它分两段，
     图注里也列着两色，作为读数保留，不再作为筛选轴。 */
  const activeSkillTypes = useMemo<SkillType[]>(() => ['hard', 'soft'], []);

  /** 赛题"按技术栈切换视图"的可选项，取自招聘信息汇总表的技术栈一列 */
  const stacks = useMemo(() => stackOptions(scope), [scope]);

  /** 主图的数据源：这批岗位在所选职级、所选技术栈下的能力要求 */
  const flow = useMemo(
    () => buildFlow({ scope, level, stack, skillTypes: activeSkillTypes }),
    [scope, level, stack, activeSkillTypes],
  );

  /** 时间轴底衬的那条曲线：能力这一段逐月的要求总量，看一眼就知道该把游标拖到哪 */
  const axisTotal = useMemo(() => totalsOf(flow.itemRows, flow.months.length), [flow]);


  /* ---------------- 三源前瞻分析的信号切片 ----------------
     岗位本身不参与：岗位不会"先出现在论文里"，先出现的是它要求的那批能力。 */
  const foreSignals = useMemo(
    () =>
      d.signals.filter((s) => {
        if (s.kind !== FORE_KIND) return false;
        if (pendingOnly && s.firstJdAt) return false;
        return true;
      }),
    [d.signals, pendingOnly],
  );

  /** 提前量分布看的是全量信号，不跟着上面的类型筛选走 —— 它回答的是整体 */
  const allSignals = useMemo(() => d.signals.filter((s) => s.kind !== 'job'), [d.signals]);

  /** 排行取前 10 —— 这张表只占半幅页宽，再长会把左右两列的底边差得太远 */
  const gapRank = useMemo(
    () => [...foreSignals].sort((a, b) => b.gap[b.gap.length - 1] - a.gap[a.gap.length - 1]).slice(0, 10),
    [foreSignals],
  );

  /** 三源对照默认落在排行里第一条招聘市场已确认的条目 —— 只有它有招聘曲线可对齐 */
  const currentSignal = useMemo(() => {
    const byPick = pick ? foreSignals.find((s) => s.entityId === pick) : undefined;
    return byPick ?? gapRank.find((s) => s.firstJdAt) ?? gapRank[0] ?? foreSignals[0];
  }, [pick, gapRank, foreSignals]);

  /* ==================== 分析引擎 ====================
     回答全部从图谱现算，不走在线模型：同一个问题恒定给同一个结果。
     接上真实模型后只需替换 resolve()，定位链接那一套不用动。 */

  /** 把某个条目选中到主图上并滚过去 */
  const focusEntity = useCallback(
    (nodeId: string) => {
      const n = d.nodes.find((x) => x.id === nodeId);
      setSelectedId(nodeId);
      /* 岗位那一段一次只画一个大类，所以要先把大类切到它所属的那一类 */
      if (n?.kind === 'job') setCategory(n.topCategory || ORPHAN_CLUSTER);
      jump('sec-flow');
    },
    [d.nodes, jump],
  );

  const engineStats = useMemo(() => {
    const detected = allSignals.filter((s) => s.firstPaperAt);
    const confirmed = detected.filter((s) => s.firstJdAt);
    const leads = confirmed
      .map((s) => s.leadMonths.paper)
      .filter((v): v is number => typeof v === 'number' && v > 0);

    /* 关系的证据来源构成：两类以上来源共同证实的、
       以及尚无招聘信息支持的各多少条 —— 供分析引擎回答“命中率/可信度”一类提问。 */
    let multiSource = 0;
    let pendingSource = 0;
    for (const e of d.edges) {
      const used =
        (e.sourceMix.jd > 0 ? 1 : 0) + (e.sourceMix.paper > 0 ? 1 : 0) + (e.sourceMix.news > 0 ? 1 : 0);
      if (used >= 2) multiSource += 1;
      if (used > 0 && e.sourceMix.jd === 0) pendingSource += 1;
    }

    return {
      categories: jobCategories.length,
      detected: detected.length,
      confirmed: confirmed.length,
      hitRate: detected.length ? confirmed.length / detected.length : 0,
      lead: leads.length ? leads.reduce((a, b) => a + b, 0) / leads.length : 0,
      leadN: leads.length,
      multiSource,
      pendingSource,
    };
  }, [d.edges, jobCategories.length, allSignals]);

  const topGapAnswer = useCallback((): ChatAnswer => {
    const top = [...allSignals]
      .sort((a, b) => b.gap[b.gap.length - 1] - a.gap[a.gap.length - 1])
      .slice(0, 4);
    const first = top[0];
    return {
      text: first
        ? `前瞻热度最高的是“${first.entityName}”（${KIND_LABEL[first.kind]} · ${first.category}），热度 ${(
          first.gap[first.gap.length - 1] * 100
        ).toFixed(0)}；前四位中 ${top.filter((s) => s.firstJdAt).length} 项已被招聘市场确认。`
        : '当前没有可用的前瞻信号。',
      links: [
        ...(first ? [{ label: `定位“${first.entityName}”`, run: () => focusEntity(first.entityId) }] : []),
        { label: '前瞻热度排行', run: () => jump('sec-rank') },
      ],
    };
  }, [allSignals, focusEntity, jump]);

  const resolve = useCallback(
    (question: string): ChatAnswer => {
      const q = question.trim();
      const lower = q.toLowerCase();

      /* ① 命中某个岗位类别。
         类别名同时也可能是一个岗位节点的名字，此时按类答 ——
         问的人多半想看这一片的全貌；末尾再给一个跳到同名节点的按钮。
         只有当问句里出现了比类名更长的条目名时才让位给条目。 */
      const stack = jobCategories.map((c) => c.name).find((s) => q.includes(s));
      const longerHit =
        stack && d.nodes.find((n) => n.name.length > stack.length && q.includes(n.name));
      if (stack && !longerHit) {
        const inStack = d.nodes.filter((n) => n.kind === 'job' && (n.topCategory || ORPHAN_CLUSTER) === stack);
        const sameName = d.nodes.find((n) => n.name === stack && n.kind === 'job');
        /* 报的三个数各自都能回源：岗位数按节点现数，招聘信息条数取自体系的
           hits 字段，被并来源数取自 source_codes —— 不报"子树叶子"，
           两级体系下岗位自己就是叶子，那个数恒为 1，写出来只会让人以为还有下一层。 */
        const posts = inStack.reduce((a, n) => a + (n.posts ?? 0), 0);
        const merged = REAL_MERGES.filter((m) => m.category === stack);
        const fromNodes = merged.reduce((a, m) => a + m.from.length, 0);
        return {
          text: `岗位类别“${stack}”下共 ${inStack.length} 个岗位，对应 ${posts.toLocaleString()} 条招聘信息；其中 ${merged.length} 个岗位由平台上的 ${fromNodes} 个原始节点归并而来，每个岗位各带一条定义与一条边界判据。`,
          links: [
            {
              // 把主图的岗位段切到这一类：三段仍然都在，只是岗位这一头换了范围
              label: `看“${stack}”的能力要求`,
              run: () => {
                setCategory(stack);
                jump('sec-flow');
              },
            },
            ...(sameName
              ? [{ label: `定位同名${KIND_LABEL[sameName.kind]}`, run: () => focusEntity(sameName.id) }]
              : []),
          ],
        };
      }

      /* ② 命中某个条目名（岗位 / 任务 / 能力 / 技能点） */
      const hit = [...d.nodes]
        .filter((n) => q.includes(n.name) || (n.name.length >= 3 && lower.includes(n.name.toLowerCase())))
        .sort((a, b) => b.name.length - a.name.length)[0];
      if (hit) {
        const sig = d.signalMap.get(hit.id);
        const when = sig?.firstJdAt
          ? `学术论文首现 ${sig.firstPaperAt ?? '—'}，招聘要求首现 ${sig.firstJdAt}，实测提前 ${sig.leadMonths.paper ?? '—'} 个月。`
          : sig
            ? `按论文热度推算，预计 ${sig.predictedJdRange?.[0] ?? '—'} 至 ${sig.predictedJdRange?.[1] ?? '—'} 进入招聘要求。`
            : '';
        return {
          text:
            (hit.origin === 'overlay'
              ? `“${hit.name}”为${hit.category}下的${KIND_LABEL[hit.kind]}，目前仅有学术论文与行业新闻支持，招聘要求尚未出现，按前瞻信号计入并逐月衰减。`
              : `“${hit.name}”为${hit.category}下已被招聘市场确认的${KIND_LABEL[hit.kind]}，市场占比 ${(
                hit.marketShare * 100
              ).toFixed(2)}%，前瞻热度 ${(hit.gap * 100).toFixed(0)}，置信度 ${(
                hit.confidence * 100
              ).toFixed(0)}%。`) + (when ? ` ${when}` : ''),
          links: [{ label: '在全景图中定位', run: () => focusEntity(hit.id) }],
        };
      }

      /* ③ 命中率 / 可信度 */
      if (/命中|兑现|靠谱|准确|验证|可信|幻觉/.test(q)) {
        return {
          text:
            `图谱现有 ${stats.edges.toLocaleString()} 条关系，每条均记录其证据的来源构成：` +
            `${engineStats.multiSource} 条由两类以上来源共同证实，` +
            `${engineStats.pendingSource} 条尚无招聘信息支持、按前瞻信号计入并逐月衰减。` +
            /* 模板去重率、噪声过滤率与幻觉拦截条数三项，本批产物未含实测值。
               此前写出来另注"此三项为演示数据"—— 一条自带免责声明的读数，
               在一段全是实测量的回答里只会让读者怀疑其余几个数。整句撤掉，
               换成同一位置上确有实测的那一条：端点校验剔除的越界关系数。 */
            `入图前经端点校验，剔除 ${DROPPED_EDGES} 条端点落在体系之外的关系。` +
            `前瞻侧共检出 ${engineStats.detected} 项论文先行信号，已被招聘市场确认 ${engineStats.confirmed} 项，` +
            `准确率 ${(engineStats.hitRate * 100).toFixed(0)}%。` +
            /* 提前量由信号与招聘两条曲线在自然月轴上的互相关求最优滞后得出，
               两条序列先取相邻观测之差再求相关，相关系数低于下限时该条不给值，
               故判出条数少于已确认条数。 */
            (engineStats.lead > 0
              ? `其中 ${engineStats.leadN} 条测得提前量，平均提前 ${engineStats.lead.toFixed(1)} 个月。`
              : '两条曲线的互相关未达显著水平，本批不给出提前量。'),
          links: [{ label: '能力演变时间线', run: () => jump('sec-timeline') }],
        };
      }

      /* ④ 结构 / 层级 / 规模 */
      if (/结构|层级|四层|规模|多少个|全景/.test(q)) {
        return {
          text: `图谱当前包含 ${stats.job} 个岗位、${stats.task} 个任务、${stats.skill} 项技能、${stats.sp} 个技能点，岗位分属 ${engineStats.categories} 个岗位类别。岗位能力全景图把这四层排成三段：左段是岗位，中段是任务，右段是能力体系（技能点 → 技能 → 能力维度），连线为“这些岗位要求这些任务、这些任务要求这些能力”。四类关系合计 ${stats.edges.toLocaleString()} 条，每条各带一个由招聘信息统计出的基图权重与一个叠加前瞻修正后的合成权重，均取自算法侧的实测产出。`,
          links: [{ label: '岗位能力全景图', run: () => jump('sec-flow') }],
        };
      }

      /* ⑤ 前瞻 / 最热 / 领先 */
      if (/前瞻|最热|领先|趋势|新兴|萌芽|提前/.test(q)) return topGapAnswer();

      /* ⑥ 兜底：给方向，不装作听懂了 */
      return {
        text: `未在图谱中匹配到对应条目。图谱现有 ${stats.job + stats.task + stats.skill + stats.sp
          } 个条目，可直接输入岗位、技能或技能点名称。`,
        links: [{ label: '岗位能力全景图', run: () => jump('sec-flow') }],
      };
    },
    [d.nodes, d.edges, d.signalMap, d.quality, engineStats, focusEntity, jump, stats, topGapAnswer],
  );

  /** 快速定位：指向本页各张图 */
  const anchors = useMemo(
    () => [
      { label: '能力演变时间线', run: () => jump('sec-timeline') },
      { label: '前瞻热度排行', run: () => jump('sec-rank') },
      { label: '岗位能力全景图', run: () => jump('sec-flow') },
    ],
    [jump],
  );

  /* ---------------- 跨页出口 ----------------
     这一页此前一条跨页链接都没有：往下滚十屏，末尾只剩页脚。
     出口按"图上此刻选中了什么"给：选中一个岗位时，三个出口全部带着这个岗位过去；
     什么都没选时给两条通用入口，说明各自回答什么。

     同一份数组供两处使用：页尾的"下一步"写全每条回答什么，主图上贴着选中行的
     浮窗（JumpDock）只取 short 与图标。选中岗位的三条各带一个 short，
     未选中时的通用入口不带，浮窗因此只在选中岗位时出现，也不必另设开关。 */
  const exits = useMemo<StepItem[]>(() => {
    const n = selectedId ? d.nodes.find((x) => x.id === selectedId) : null;
    if (n?.kind === 'job') {
      const q = encodeURIComponent(n.id);
      return [
        {
          to: `/jobs?tab=${n.emerging ? 'new' : 'existing'}&id=${q}`,
          label: `在岗位洞察中打开“${n.name}”`,
          desc: n.emerging
            ? '该岗位的定义五要素、涌现相图位置，及支撑定义的三类原文。'
            : '该岗位本期的能力年轮与逐条变更清单，及支撑变动的三类原文。',
          icon: 'cap',
          primary: true,
          short: '岗位洞察',
        },
        {
          to: `/explore?job=${q}`,
          label: '查看能力结构相近的岗位',
          desc: '按能力构成重排全部岗位，给出与之最接近的若干个及其城市、薪资档分布。',
          icon: 'route',
          short: '职业探索',
        },
        {
          to: `/match?target=${q}`,
          label: '以该岗位运行匹配诊断',
          desc: '五维评价体系、能力差距明细与分阶段学习路径。',
          icon: 'target',
          short: '人岗匹配',
        },
      ];
    }
    return [
      {
        to: '/jobs?tab=new',
        label: '查看正在萌芽的新岗位',
        desc: '尚未进入标准岗位体系的候选，及其定义五要素的生成依据。',
        icon: 'cap',
        primary: true,
      },
      {
        to: '/jobs?tab=existing',
        label: '查看既有岗位本期变动',
        desc: '逐岗位的能力年轮与变更清单，每条变动标注增、删、改与数据来源。',
        icon: 'trend',
      },
      {
        to: '/explore',
        label: '从能力反查岗位',
        desc: '选中一项能力，列出要求它的岗位。',
        icon: 'route',
      },
    ];
  }, [selectedId, d.nodes]);


  return (
    <div className="pano">
      <div className="pano-wrap">
        <PageGuide
          station={stationOf('/panorama')!}
          landed={landed}
          onClearLanded={() => {
            setLanded(null);
            setSelectedId(null);
          }}
        />

        {/* ==================== ① 分析引擎 ==================== */}
        <EngineChat
          title="分析引擎"
          subtitle="定位本页的岗位、能力与前瞻趋势"
          anchors={anchors}
          resolve={resolve}
          suggestions={['前瞻热度最高的条目', '前瞻信号准确率', '安全与合规', 'Agent 编排']}
        />

        {/* ==================== 前瞻分析 ====================
            时间线 / 前瞻热度排行 / 三源对照三张图共用同一批输入：
            每个条目在招聘、论文、新闻三条来源上的逐月强度序列。招聘一路取自六窗的
            月度份额，论文与新闻两路取自叠层逐窗记录的既有条目增强，逐条可核到
            论文编号或新闻标题，三张图共用同一组口径筛选。

            叠层的既有条目增强按体系编码记录，落在岗位、任务、技能三层；技能点一层
            叠层产出的是新实体而非对既有条目的增强，没有"论文先于招聘出现"这一对
            时刻可比，故该层不列入本区块的档位。 */}
        <div className="pano-sec-hd">
          <h2>前瞻分析</h2>
        </div>

        <div className="fore-grid">
          <section className={`panel fore-full ${spotCls('sec-timeline') ?? ''}`} id="sec-timeline">
            <header className="panel-hd">
              <div className="panel-hd-text">
                <h2>能力演变时间线</h2>
              </div>
              <div className="panel-hd-act timeline-head-act" aria-label="前瞻分析口径">
                <button
                  className={pendingOnly ? 'chip on' : 'chip'}
                  onClick={() => setPendingOnly((v) => !v)}
                  aria-pressed={pendingOnly}
                >
                  待招聘市场确认
                </button>
              </div>
            </header>
            <div className="panel-bd" style={{ padding: '14px 16px 16px' }}>
              <SignalTimeline
                signals={foreSignals}
                limit={18}
                onPick={(s) => setPick(s.entityId)}
                selectedId={currentSignal?.entityId ?? null}
              />
            </div>
          </section>

          <section className={`panel ${spotCls('sec-rank') ?? ''}`} id="sec-rank">
            <header className="panel-hd">
              <div className="panel-hd-text">
                <h2>前瞻热度排行</h2>
              </div>
            </header>
            <div className="panel-bd">
              <table className="rank">
                <thead>
                  <tr>
                    <th className="c-idx">#</th>
                    <th>条目</th>
                    <th className="c-spark">论文 / 新闻热度</th>
                    <th className="c-num">前瞻热度</th>
                    <th className="c-st">招聘市场</th>
                  </tr>
                </thead>
                <tbody>
                  {gapRank.map((s, i) => {
                    const on = currentSignal?.entityId === s.entityId;
                    return (
                      <tr
                        key={s.entityId}
                        className={on ? 'on' : ''}
                        onClick={() => setPick(s.entityId)}
                        tabIndex={0}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            setPick(s.entityId);
                          }
                        }}
                      >
                        <td className="c-idx">{i + 1}</td>
                        <td>
                          <span className="rk-n">{s.entityName}</span>
                          {/* 本区块只列技能一层，条目类型对每一行都相同，
                              写在行内等于每行重复同一个词，故只留所属的技能组 */}
                          <small>{s.category}</small>
                        </td>
                        <td className="c-spark">
                          <DualSparkline
                            series={[
                              { values: s.paper, color: 'var(--src-paper)' },
                              { values: s.news, color: 'var(--src-news)' },
                            ]}
                            w={68}
                            h={20}
                          />
                        </td>
                        <td className="c-num">
                          <b>{(s.gap[s.gap.length - 1] * 100).toFixed(0)}</b>
                        </td>
                        <td className="c-st">
                          {s.firstJdAt ? (
                            <span className="tag ok">已确认</span>
                          ) : (
                            <span className="tag wait">待确认</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                  {gapRank.length === 0 && (
                    <tr className="rk-empty">
                      <td colSpan={5}>当前口径下无可展示的条目，可取消“待招聘市场确认”。</td>
                    </tr>
                  )}
                </tbody>
              </table>

              <div className="viz-legend">
                <span>
                  <i style={{ background: 'var(--src-paper)' }} />
                  学术论文
                </span>
                <span>
                  <i style={{ background: 'var(--src-news)' }} />
                  行业新闻
                </span>
                {/* 截断必须写出来：只列前十却不说明总数，读的人会当成"符合条件的就这十项" */}
                <span className="muted">
                  共 {foreSignals.length} 项，按前瞻热度排序列出前 {gapRank.length} 项
                </span>
              </div>
            </div>
          </section>

          {currentSignal && (
            <section className={`panel fore-compare ${spotCls('sec-compare') ?? ''}`} id="sec-compare">
              <header className="panel-hd">
                <div className="panel-hd-text">
                  <h2>数据源对照 · {currentSignal.entityName}</h2>
                </div>
              </header>
              <div className="panel-bd">
                <SourceCompare key={currentSignal.entityId} signal={currentSignal} all={allSignals} />

                {/* 三个首现月份各带自己那一路的来源色：上一版三块共用一道同样的顶线，
                    线本身既不分辨三者也不表达任何量，与图上三色的编码还对不上。
                    改为左侧一道来源色的色条加同色极淡的底，与图例、曲线同色系。 */}
                {/* 三格一律不以破折号示人：破折号在这一行既可能读作"该源未提及"，
                    也可能读作"这个数没算出来"，而两者对读者意味着完全不同的事。
                    未提及即写"未见于该源"，预计时间算不出即写明算不出的缘由。 */}
                <dl className="dv-firsts">
                  <div className="dvf paper">
                    <dt>学术论文首次出现</dt>
                    <dd className={currentSignal.firstPaperAt ? 'c-paper' : 'dvf-none'}>
                      {currentSignal.firstPaperAt ?? '未见于该源'}
                    </dd>
                  </div>
                  <div className="dvf news">
                    <dt>行业新闻首次出现</dt>
                    <dd className={currentSignal.firstNewsAt ? 'c-news' : 'dvf-none'}>
                      {currentSignal.firstNewsAt ?? '未见于该源'}
                    </dd>
                  </div>
                  <div className={currentSignal.firstJdAt ? 'dvf jd' : 'dvf paper'}>
                    <dt>招聘信息首次出现</dt>
                    <dd
                      className={
                        currentSignal.firstJdAt
                          ? 'c-jd'
                          : currentSignal.predictedJdAt
                            ? 'c-paper'
                            : 'dvf-none'
                      }
                    >
                      {currentSignal.firstJdAt ??
                        (currentSignal.predictedJdAt
                          ? `预计 ${currentSignal.predictedJdAt}`
                          : '暂无可推算的依据')}
                    </dd>
                  </div>
                </dl>

                {/* 已确认的一档原先在此写出提前量的月数。三个首现月份就列在上面一行，
                    月数由它们相减即得，本批数据下多数条目又取不到值，写出来是一行破折号。
                    未确认的一档保留：预计区间与时效衰减两个量在别处没有落点。

                    预计时间与区间由实测滞后的分位数现算（见 buildRealSignals 的说明），
                    两档口径不同，故分别成句：一档自首现月起算，一档自末窗起算。
                    区间与中位落点写在同一句里，且逐项交代样本条数与已等待月数 ——
                    只摆出两个月份而不说它们从哪来，读者无从判断这个预计有多可信。 */}
                {!currentSignal.firstJdAt &&
                  (currentSignal.predictedJdAt && currentSignal.predictedBasis ? (
                    <p className="note warn">
                      该项尚未出现在招聘市场。
                      {currentSignal.predictedBasis.beyondSample ? (
                        <>
                          自{SOURCE_LABEL[currentSignal.predictedBasis.route]}首现已历{' '}
                          <b>{currentSignal.predictedBasis.waited}</b> 个月，超出实测滞后的上界，
                          预计时间因而自末窗 <b>{REAL_MONTHS[REAL_MONTHS.length - 1]}</b> 起算：
                          按 {currentSignal.predictedBasis.n} 条实测滞后的四分位，此后仍需{' '}
                          <b>
                            {monthDiff(
                              REAL_MONTHS[REAL_MONTHS.length - 1],
                              currentSignal.predictedJdRange?.[0] ?? currentSignal.predictedJdAt,
                            )}{' '}
                            至{' '}
                            {monthDiff(
                              REAL_MONTHS[REAL_MONTHS.length - 1],
                              currentSignal.predictedJdRange?.[1] ?? currentSignal.predictedJdAt,
                            )}
                          </b>{' '}
                          个月
                        </>
                      ) : (
                        <>
                          同类信号自{SOURCE_LABEL[currentSignal.predictedBasis.route]}
                          首现到写入招聘要求的实测滞后中，长于本项已等待的{' '}
                          <b>{currentSignal.predictedBasis.waited}</b> 个月的共{' '}
                          {currentSignal.predictedBasis.n} 条，据其四分位，预计在{' '}
                          <b>
                            {currentSignal.predictedJdRange?.[0]} 至{' '}
                            {currentSignal.predictedJdRange?.[1]}
                          </b>{' '}
                          之间进入招聘要求
                        </>
                      )}
                      ，中位落点 <b>{currentSignal.predictedJdAt}</b>；在此之前仅作为前瞻信号计入，
                      权重逐月衰减（当前时效 <b>{Math.round(currentSignal.decayFactor * 100)}%</b>）。
                    </p>
                  ) : (
                    <p className="note warn">
                      该项尚未出现在招聘市场，在观测区间内也未见于学术论文与行业新闻，
                      无从推算进入招聘要求的时间。
                    </p>
                  ))}

                <div className="dv-act">
                  <button className="btn sm" onClick={() => focusEntity(currentSignal.entityId)}>
                    <Icon name="target" size={13} />
                    在全景图中定位该条目
                  </button>
                </div>
              </div>
            </section>
          )}
        </div>

        {/* ==================== ③ 岗位能力全景图 ==================== */}
        <div className="pano-sec-hd" id="sec-flow">
          <h2>岗位能力全景图</h2>

          {/* 图谱规模与节名同处一行。它说的是整份图谱有多大，不是这张图上画了
              多少 —— 下面那张图只画当前口径内的那一批，两个数不是一回事。
              此前它单占一行，读者会把它读成主图的图注；并到标题行之后，
              它的位置与右侧的口径标一致，都是这一节的元信息。 */}
          <div className="pano-meta">
            <span className="pm-lead">
              <Icon name="layers" size={15} />
              图谱规模
            </span>
            <span>
              岗位<b>{stats.job}</b>
              {stats.jobEmerging > 0 && <em className="pm-sub">另有新岗位 {stats.jobEmerging} 个</em>}
            </span>
            <span>
              任务<b>{stats.task}</b>
            </span>
            <span>
              技能<b>{stats.skill}</b>
            </span>
            <span>
              技能点<b>{stats.sp}</b>
            </span>
            <span>
              关系<b>{stats.edges}</b>
            </span>
            <span className="pm-hi">
              前瞻条目<b>{stats.pending}</b>
            </span>
          </div>

        </div>

        {/* 主图。四个切换下拉与时间游标一并收在图内顶部：
            每换一次就重画一次图，控件与图同框，因果才连得上。 */}
        <div className="pano-flow">
          {/* 跳转闪光挂在面板上而不是外层容器上：外层没有边框与底色，闪不出来 */}
          <section className={`panel pano-flow-panel ${spotCls('sec-flow') ?? ''}`}>
            <div className="panel-bd">
              {/* 控件与图一并包在 .flow-stick 里：控件是 sticky 的，粘附范围由
                  最近的定位上下文决定。不包这一层的话，它的范围一直延伸到面板底，
                  滚到图末尾时会盖住下面的图例与口径行。包上之后，图滚完控件即随之
                  离场，图例与口径行始终露在外面。 */}
              <div className="flow-stick">
                {/* 吸附在图顶部。这张图有近千像素高，控件跟着滚出视野的话，
                    改了什么当场就看不见了 —— 而"看见变化"正是它们存在的理由。 */}
                <div className="flow-head">
                  <div className="flow-ctl">
                    <div className="fc-pick">
                      <span className="fc-lb">
                        岗位类别
                      </span>
                      <select
                        value={category}
                        onChange={(e) => setCategory(e.target.value)}
                        aria-label="岗位类别"
                      >
                        {jobCategories.map((c) => (
                          <option key={c.name} value={c.name}>
                            {c.name}（{c.count} 个岗位）
                          </option>
                        ))}
                      </select>
                    </div>

                    <div className="fc-pick">
                      <span className="fc-lb">
                        技术栈
                      </span>
                      <select
                        value={stack ?? ''}
                        onChange={(e) => setStack(e.target.value || null)}
                        aria-label="技术栈"
                      >
                        {/* 选项名逐字取自招聘信息汇总表的技术栈一列，可回源核对；
                            括号里报该栈覆盖的岗位数，选之前就知道图会剩多少行 */}
                        {/* 括号里报的是**所选岗位类别内**的岗位数，不是全库数：
                            这个下拉与岗位类别叠加生效，报全库数会让人以为选了它
                            图上就有那么多行 */}
                        <option value="">不限技术栈（{flow.jobRows.length} 个岗位）</option>
                        {stacks.map((s) => (
                          <option key={s.v} value={s.v}>
                            {s.label}（{s.jobs} 个岗位）
                          </option>
                        ))}
                      </select>
                    </div>

                    <div className="fc-pick">
                      <span className="fc-lb">
                        岗位级别
                      </span>
                      <select
                        value={level}
                        onChange={(e) => setLevel(e.target.value as JobLevel)}
                        aria-label="岗位级别"
                      >
                        {JOB_LEVELS.map((o) => (
                          <option key={o.v} value={o.v}>
                            {o.label}（{o.years}）
                          </option>
                        ))}
                      </select>
                    </div>

                    {/* 取消选中与四个下拉同处控件行。它原先落在图脚的图注一行，
                        而这张图有近千像素高：选中一行之后要取消，得先滚到图底。
                        控件行是吸附的，按钮跟着留在视野里。 */}
                    {selectedId && (
                      <button className="btn sm fc-clear" onClick={() => setSelectedId(null)}>
                        取消选中
                      </button>
                    )}
                  </div>

                  <div className="flow-time">
                    <PrismTimeAxis
                      months={flow.months}
                      forecastFrom={flow.forecastFrom}
                      latest={flow.observedLast}
                      value={cursor}
                      onChange={setCursor}
                      baseline={baseline}
                      onBaseline={setBaseline}
                      playing={playing}
                      onPlaying={togglePlay}
                      total={axisTotal}
                      totalLabel="当月能力要求合计"
                      milestones={milestones}
                    />
                  </div>
                </div>

                <div className="flow-scroll">
                  {/* 量宽量的是这一层而不是外面那层滚动容器：滚动容器带左右内边距，
                      量到的宽里含着它，图会正好宽出这一截而凭空多一条横向滚动条。
                      这一层无内边距，量到的就是图真正能用的宽度，内边距改多少都不影响。 */}
                  <div className="flow-canvas" ref={flowBox.ref}>
                    <JobCapabilityFlow
                      model={flow}
                      cursor={cursor}
                      baseline={baseline}
                      selected={selectedId}
                      onSelect={setSelectedId}
                      jobLimit={JOB_LIMIT}
                      taskLimit={TASK_LIMIT}
                      width={flowBox.w || 1204}
                      onLayoutInfo={setFlowInfo}
                      jumpDock={<JumpDock items={exits} label="把选中的岗位带往其他页面" />}
                      onTip={(e, r) =>
                        setTip(
                          r
                            ? {
                              x: e.clientX,
                              y: e.clientY,
                              content: <FlowTip row={r} cursor={cursor} baseline={baseline} months={flow.months} />,
                            }
                            : null,
                        )
                      }
                    />
                  </div>
                </div>
              </div>

              {/* 筛到极窄时（如"人工智能与智能技术"里没有软技能）三段会同时为空。
                  空图不作解释就成了坏掉的图，所以写明是哪两个口径的交集为空。 */}
              {flow.itemRows.length === 0 && (
                <p className="flow-empty">
                  当前口径下无可绘制的行：所选技术栈与岗位类别的交集里没有该类技能，可更换其中一项。
                </p>
              )}

              {/* 图脚一行两件事：左侧图注、右侧口径。两者此前各占一行，而两行加起来
                  仍不足整幅图宽的六成，右侧因此空着一大片，图与下一块之间也多出一行的
                  高度。现并作一行，左右分置；窄屏排不下时自行折回两行。 */}
              <div className="flow-foot">
                {/* 图注：图上出现的每一种编码各占一条，不写读法说明，也不写组名 ——
                    色块加它自己的名字已经说清楚是哪一种编码（"硬技能""精通"
                    "当月未进入图谱"），再压一行"岗位条 · 软硬构成"这样的抬头，
                    等于把同一件事说两遍。分组关系由组间那条竖线交代。 */}
                <div className="legend-bar">
                  <div className="legend-grp">
                    {MIX_TYPES.map((t) => (
                      <span key={t.v}>
                        <i style={{ background: MIX_COLORS[t.v] }} />
                        {t.label}
                      </span>
                    ))}
                  </div>
                  <div className="legend-grp">
                    {PROF_LEVELS.map((p, i) => (
                      <span key={p}>
                        {/* 第四档在图上画的是点阵而不是色块，图注里也照画 ——
                          图注与图不是同一种画法的话，对不上的是读的人 */}
                        <i
                          className={i === PROF_UNKNOWN ? 'sw-unknown' : undefined}
                          style={i === PROF_UNKNOWN ? undefined : { background: PROF_COLORS[i] }}
                        />
                        {p}
                      </span>
                    ))}
                  </div>
                  <div className="legend-grp">
                    <span>
                      <i className="sw-track" />
                      当月未进入图谱
                    </span>
                    <span>
                      <i className="sw-wait" />
                      未被招聘市场确认
                    </span>
                  </div>
                  {baseline !== null && (
                    <div className="legend-grp">
                      {/* 这一组留着前缀：增 / 减 / 新增 / 退出 都是"相对哪个月"的说法，
                        不写出基准月，四个色块就没有比较对象 */}
                      <span className="legend-lb">相对 {monthText(flow.months[baseline])}</span>
                      <span>
                        <i className="sw-up" />增
                      </span>
                      <span>
                        <i className="sw-down" />减
                      </span>
                      <span>
                        <i className="sw-new" />新增
                      </span>
                      <span>
                        <i className="sw-gone" />退出
                      </span>
                    </div>
                  )}
                </div>

                {/* 口径行：图上画了多少行、当月有多少项已被招聘市场确认。
                  截断只在真的发生时才写出来 —— 画了一部分却不说明总数，
                  读的人会把画出来的当成全部。 */}
                <p className="flow-cap">
                  <span className="cap-i">
                    岗位 <b>{flow.jobRows.length}</b>
                  </span>
                  <span className="cap-i">
                    任务 <b>{flow.taskRows.length}</b>
                  </span>
                  {/* 第三段画到技能这一层，技能点在展开时才出现 */}
                  <span className="cap-i">
                    技能 <b>{flow.groupRows.length}</b>
                  </span>
                  {flowInfo && flowInfo.absent > 0 && (
                    <span className="cap-i">
                      当月未进入图谱 <b>{flowInfo.absent}</b>
                    </span>
                  )}
                  {baseline !== null && baseline !== cursor && flowInfo && (
                    <span className="cap-i">
                      相对 {monthText(flow.months[baseline])}：新增 <b className="ch-new">{flowInfo.changed.new}</b>、上升{' '}
                      <b className="ch-up">{flowInfo.changed.up}</b>、下降 <b className="ch-down">{flowInfo.changed.down}</b>
                      、退出 <b className="ch-gone">{flowInfo.changed.gone}</b>
                    </span>
                  )}
                </p>
              </div>
            </div>
          </section>
        </div>

        <NextSteps from="/panorama" items={exits} />
      </div>
      <Tooltip tip={tip} />
      <Footer />
    </div>
  );
}

/* ==================== 主图的悬停提示 ====================
   一行上同时有三件事要交代：当月是多少、跟基准月比怎么样、这一层是什么。
   写进提示而不是画到图上 —— 那三样都是"想知道再看"的量，画上去只会挤掉条。 */

/**
 * 相对基准月的四种变化。
 *
 * "退出"说的是当月这一条不在图谱里、而基准月在。本批数据是只增不减的一次快照，
 * 所以它只在把游标拖到基准月之前时出现；算法侧按月产出之后，一项能力被移出
 * 岗位要求同样走这一档，图与口径行都不用改。赛题②要的"新增 / 删除 / 修改"
 * 分别落在 new / gone / up-down 上。
 */
const CHANGE_TEXT: Record<ChangeKind | 'flat', string> = {
  new: '新增',
  up: '要求上升',
  down: '要求下降',
  flat: '基本持平',
  gone: '退出图谱',
};

const KIND_TEXT: Record<FlowRow['kind'], string> = {
  skillpoint: '技能点',
  skill: '技能',
  task: '任务',
  job: '岗位',
};

function FlowTip({
  row,
  cursor,
  baseline,
  months,
}: {
  row: FlowRow;
  cursor: number;
  baseline: number | null;
  months: string[];
}) {
  const a = rowAt(row, cursor, baseline);
  /* 岗位一段的条长是该岗位涉及的技能点权重之和，跨岗位可比而没有单位；
     浮层上把它写作一个三位小数的数，读者会当成一个可以逐位核对的计量。
     岗位行因而只报招聘信息条数（该层唯一的实测计量）与相对高度，
     其余两段报的是同层内的相对高度，量纲一致，写作百分比。 */
  const peak = Math.max(...row.series.map((v) => v ?? 0), 1e-9);
  const rel = a.value === null ? null : a.value / peak;
  return (
    <>
      <div className="tt-title">{row.name}</div>
      <div>
        {KIND_TEXT[row.kind]}
        {row.group && row.group !== row.name ? ` · ${row.group}` : ''}
      </div>
      {row.kind === 'job' ? (
        <div>
          {monthText(months[cursor])}：招聘信息{' '}
          <b>{(row.posts ?? 0).toLocaleString()}</b> 条
        </div>
      ) : (
        <div>
          {monthText(months[cursor])}：要求强度{' '}
          {rel === null ? '当月不在图谱内' : `${(rel * 100).toFixed(0)}%（相对本项峰值）`}
        </div>
      )}
      {/* 技能行：要求程度的四档构成。
          "无法确定"单独占一档，不并进前三档 —— 原文没写程度词是一件
          需要被看见的事，摊进"了解"里就成了一个测出来的结论。

          技能点一层不列这一段：熟练度的实测粒度止于“某岗位对某技能”，
          技能点没有自己的档位读数。 */}
      {row.kind === 'skill' && (
        <div className="tt-prof">
          {PROF_LEVELS.map((lv, k) => {
            const sum = row.prof.reduce((x, y) => x + y, 0) || 1;
            const pct = Math.round((row.prof[k] / sum) * 100);
            if (!row.prof[k]) return null;
            return (
              <span key={lv}>
                <i
                  className={k === PROF_UNKNOWN ? 'sw-unknown' : undefined}
                  style={k === PROF_UNKNOWN ? undefined : { background: PROF_COLORS[k] }}
                />
                {lv} {pct}%
              </span>
            );
          })}
        </div>
      )}
      {a.change && (
        <div className={`ch-${a.change}`}>
          相对 {monthText(months[baseline!])}：{CHANGE_TEXT[a.change]}
          {a.base !== null && a.value !== null && a.change !== 'flat' && (
            <> （{a.base > 1e-9 ? `${((a.value - a.base) / a.base * 100).toFixed(0)}%` : '—'}）</>
          )}
        </div>
      )}
      {/* 操作说明不进浮层：浮层报的是这一行的读数，把"单击可以做什么"
          混在同一段里，读者会把它当成这一行的一条数据。 */}
      <div className="tt-muted">
        {a.confirmed
          ? `${monthText(months[row.confirmedAt])} 写入招聘要求`
          : '尚未写入招聘要求，仅有学术论文与行业新闻支持'}
      </div>
    </>
  );
}
