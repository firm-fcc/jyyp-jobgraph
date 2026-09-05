/* =========================================================
   岗位洞察 —— 两个子页面共用一套骨架

     · 新岗位发现：相图里挑出正在成型的候选，逐字段给出它的定义
     · 既有岗位能力动态：年轮看长期走势，变更清单逐条列出增删改

   骨架：顶部子页面切换 → 筛选条 → 左列表 / 右详情。
   两个子页面的右列首块都是本页的主图，选中项在图与列表之间双向联动。
   ========================================================= */

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useDataset } from '@/api/client';
import { Panel } from '@/components/common/Panel';
import { REAL_GRAPH_STATS } from '@/data/realGraph';
import { PageGuide } from '@/components/common/PageGuide';
import { NextSteps, type StepItem } from '@/components/common/NextSteps';
import { JumpDock } from '@/components/common/JumpDock';
import { stationOf } from '@/data/journey';
import { Icon } from '@/components/Icon';
import { Footer } from '@/components/Footer';
import { EmergencePhasePlot } from '@/components/viz/EmergencePhasePlot';
import { JobSpaceMap } from '@/components/viz/JobSpaceMap';
import { JobSpacePeers } from '@/components/viz/JobSpacePeers';
import { CapabilityAnnuli, opLabel, useAnnuliColors } from '@/components/viz/CapabilityAnnuli';
import { DualSparkline, Sparkline, StructureBars } from '@/components/viz/Primitives';
import { JobSourceView } from '@/components/panels/JobSourceView';
import { jobSkillWeights, NOW, VERSION_DEFS } from '@/data/generator';
import { buildJobSpace } from '@/data/jobSpace';
import { PROVINCE_OTHER } from '@/data/provinces';
import type {
  ChangeOp,
  Distribution,
  EntitySignal,
  GraphEdge,
  GraphNode,
  JobAnnuli,
} from '@/types/graph';
import { monthDiff } from '@/utils/format';
import '@/styles/jobs.css';

/* ==================== 常量 ==================== */

type Tab = 'new' | 'existing';

const LATEST = VERSION_DEFS[VERSION_DEFS.length - 1];

const TABS: { v: Tab; title: string; icon: string }[] = [
  { v: 'new', title: '新岗位发现与定义', icon: 'spark' },
  { v: 'existing', title: '既有岗位能力动态', icon: 'trend' },
];

const SORTS: { v: string; label: string; tab?: Tab }[] = [
  { v: 'recent', label: '最近首现', tab: 'new' },
  { v: 'gap', label: '前瞻热度最高', tab: 'new' },
  { v: 'changes', label: '本期变动最多', tab: 'existing' },
  { v: 'share', label: '市场占比最高', tab: 'existing' },
  { v: 'confidence', label: '定义置信度' },
];

const OP_COLOR: Record<ChangeOp, string> = {
  add: 'var(--green)',
  remove: 'var(--red)',
  modify: 'var(--primary)',
  merge: 'var(--amber)',
};

const LEVEL_LABEL: Record<number, string> = { 1: '基础', 2: '进阶', 3: '前沿' };

/* ==================== 小工具 ==================== */

/* "其他"一档收的是行政区划对照表未登记的城市，不是一个省。
   这一处要答的是"主要在哪个省"，答"其他"等于没答，故取次高的具名档。 */
/* 候选卡片右侧那条走势线。

   既有岗位画招聘信息一路：它们的市场需求就是这条线。新岗位画学术论文与行业新闻
   两路：这批岗位是由这两路的讨论立起来的，招聘一路要么整条为零（尚未进入市场），
   要么零星几窗，画出来是一条贴底的直线，说的只是"招聘侧没有读数"。

   两路都无读数时整条不画 —— 一条平线与"图没画出来"分不开。

   横轴不取固定的末若干窗，而是裁到两路合起来确有读数的那一段：一则新岗位被
   提出的时点可早可晚（本批最早的两个在 2022-08、2022-09，取末二十四窗时那一段
   正好落在窗外，线是空的）；二则已写入体系的候选自转正的那一窗起不再作为
   新岗位记录，其后各窗一律为零，整条画出来后半截是一条贴着底边的长直线 ——
   读作"热度归零"，而实情是"不再观测"。 */
function foreSpark(sig?: EntitySignal) {
  const src = [
    { values: sig?.paper ?? [], color: 'var(--src-paper)', label: '学术论文' },
    { values: sig?.news ?? [], color: 'var(--src-news)', label: '行业新闻' },
  ].filter((r) => r.values.some((v) => v > 0));
  if (!src.length) return null;
  let lo = Infinity;
  let hi = -1;
  for (const r of src) {
    for (let i = 0; i < r.values.length; i++) {
      if (r.values[i] <= 0) continue;
      if (i < lo) lo = i;
      if (i > hi) hi = i;
    }
  }
  if (hi < lo) return null;
  return src.map((r) => ({ ...r, values: r.values.slice(lo, hi + 1) }));
}

const topBucket = (dist?: Distribution) => {
  if (!dist) return '—';
  const e = Object.entries(dist)
    .filter(([k]) => k !== PROVINCE_OTHER)
    .sort((a, b) => b[1] - a[1])[0];
  return e ? e[0] : '—';
};

/** 置信度条 */
function ConfBar({ value, label }: { value: number; label?: string }) {
  const pct = Math.round(value * 100);
  const tone = pct >= 85 ? 'hi' : pct >= 65 ? 'mid' : 'low';
  return (
    <span className={`conf conf-${tone}`}>
      <span className="conf-track">
        <i style={{ width: `${pct}%` }} />
      </span>
      <span className="conf-v">
        {label ? `${label} ` : ''}
        {pct}%
      </span>
    </span>
  );
}

/* ==================== 页面 ==================== */

export function Jobs() {
  const d = useDataset();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();

  const [query, setQuery] = useState('');
  const [cluster, setCluster] = useState('');
  const [sort, setSort] = useState('');
  const [skillFocus, setSkillFocus] = useState<string | null>(null);
  const [peerId, setPeerId] = useState<string | null>(null);
  const [opFilter, setOpFilter] = useState<ChangeOp | 'all'>('all');
  const [verFilter, setVerFilter] = useState('all');
  const [toast, setToast] = useState<string | null>(null);

  const tab: Tab = params.get('tab') === 'existing' ? 'existing' : 'new';
  const urlId = params.get('id');

  const say = useCallback((msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast((t) => (t === msg ? null : t)), 2600);
  }, []);

  /* ---------------- 数据切片 ---------------- */

  const allJobs = useMemo(() => d.nodes.filter((n) => n.kind === 'job'), [d.nodes]);
  const annuliOf = useMemo(() => new Map(d.annuli.map((a) => [a.jobId, a])), [d.annuli]);
  const nodeById = useMemo(() => new Map(d.nodes.map((n) => [n.id, n])), [d.nodes]);

  /* “本季”取该岗位年轮最外一个**实测**环所在的那一档，不取全站版本序列的末位：
     年轮按季成环，而版本序列按窗计数，两者的标识不同名，
     按后者过滤会一条都对不上，列表上的变动数因而恒为零。

     另须跳过最外那一环预测环：它画的是下一季度的预测构成，不产出变更条目，
     取到它时每个岗位都会读成“本季零变动”。 */
  const lastRingOf = useCallback(
    (jobId: string) => [...(annuliOf.get(jobId)?.rings ?? [])].reverse().find((r) => !r.predicted),
    [annuliOf],
  );
  /** 本季（年轮最外一个实测环）的变更条数 */
  const freshCount = useCallback(
    (jobId: string) => {
      const a = annuliOf.get(jobId);
      const v = [...(a?.rings ?? [])].reverse().find((r) => !r.predicted)?.version;
      return v ? a!.changes.filter((c) => c.version === v).length : 0;
    },
    [annuliOf],
  );

  const pool = useMemo(
    () => allJobs.filter((j) => (tab === 'new' ? j.emerging : !j.emerging)),
    [allJobs, tab],
  );
  const clusters = useMemo(
    () => [...new Set(pool.map((j) => j.cluster).filter(Boolean))] as string[],
    [pool],
  );

  /** 按 id 取名。岗位空间要列任务名，而它只拿得到边上的 id */
  const nameOf = useCallback((id: string) => nodeById.get(id)?.name ?? id, [nodeById]);

  /* 岗位空间的投影。新岗位那一屏才用得上，但它必须拿全部岗位来算 ——
     少了 131 个已有岗位，“离哪个已有岗位近”就没有参照系。
     算一次要遍历全部边并做一次主成分分解，因此只在数据本身变动时重算。 */
  const space = useMemo(
    () =>
      tab === 'new'
        ? buildJobSpace(allJobs, d.edges, d.signalMap, nameOf, d.inferredEdges)
        : null,
    [tab, allJobs, d.edges, d.inferredEdges, d.signalMap, nameOf],
  );

  const skillNamesOf = useCallback(
    (jobId: string) =>
      d.edges
        .filter((e) => (e.kind === 'J-S' || e.kind === 'J-T') && e.source === jobId)
        .map((e) => nodeById.get(e.target)?.name ?? '')
        .filter(Boolean),
    [d.edges, nodeById],
  );

  /** 子页面专属排序项切换后可能失效，这里兜回该子页面的默认排序 */
  const sortKey = useMemo(() => {
    const ok = SORTS.some((s) => s.v === sort && (!s.tab || s.tab === tab));
    return ok ? sort : tab === 'new' ? 'recent' : 'changes';
  }, [sort, tab]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = pool.filter((j) => {
      if (cluster && j.cluster !== cluster) return false;
      if (!q) return true;
      const hay = [
        j.name,
        j.definition ?? '',
        j.cluster ?? '',
        j.category,
        ...(j.coreDuties ?? []),
        ...(j.mustSkills ?? []),
        ...(j.plusSkills ?? []),
        ...(j.scenarios ?? []),
        ...skillNamesOf(j.id),
      ]
        .join(' ')
        .toLowerCase();
      return hay.includes(q);
    });

    const cmp: Record<string, (a: GraphNode, b: GraphNode) => number> = {
      recent: (a, b) => (a.firstSeen < b.firstSeen ? 1 : a.firstSeen > b.firstSeen ? -1 : 0),
      gap: (a, b) => b.gap - a.gap,
      changes: (a, b) => freshCount(b.id) - freshCount(a.id),
      share: (a, b) => b.marketShare - a.marketShare,
      confidence: (a, b) => b.confidence - a.confidence,
      frequency: (a, b) => b.frequency - a.frequency,
    };
    return [...list].sort(cmp[sortKey] ?? cmp.confidence);
  }, [pool, query, cluster, sortKey, skillNamesOf, freshCount]);

  const current = useMemo(
    () => shown.find((j) => j.id === urlId) ?? shown[0] ?? null,
    [shown, urlId],
  );

  /* 选中新岗位在岗位空间里的落点。右栏的对照全部取自它的 near，
     与图上那三条连线、三个序号是同一份数据，不另算一遍。 */
  const spacePoint = useMemo(
    () => (space && current ? (space.points.find((p) => p.job.id === current.id) ?? null) : null),
    [space, current],
  );

  /* 进页时带的 id 只取首帧那一次：此后左列每换一个岗位都会把 id 写回地址栏，
     用当前 urlId 判断会让"已按来路定位到"这一行永远挂着。
     页内自己换岗位或换子页面时清掉它。 */
  const [landedId, setLandedId] = useState<string | null>(() => params.get('id'));
  const landedName = useMemo(
    () => (landedId ? (allJobs.find((j) => j.id === landedId)?.name ?? null) : null),
    [landedId, allJobs],
  );

  /** URL 与选中项对齐；列表点选走 replace，不往历史里堆记录。
      从相图里点到另一类岗位时，子页面跟着切过去，筛选里的子页面专属条件同时归位。 */
  const select = useCallback(
    (id: string) => {
      const t: Tab = allJobs.find((j) => j.id === id)?.emerging ? 'new' : 'existing';
      setParams({ tab: t, id }, { replace: true });
      setSkillFocus(null);
      setPeerId(null);
      setLandedId(null);
      if (t !== tab) {
        setSort('');
        setCluster('');
      }
    },
    [allJobs, setParams, tab],
  );

  const switchTab = (t: Tab) => {
    const first = allJobs.find((j) => (t === 'new' ? j.emerging : !j.emerging));
    setParams({ tab: t, ...(first ? { id: first.id } : {}) }, { replace: true });
    setSkillFocus(null);
    setLandedId(null);
    setSort('');
    setCluster('');
  };

  useEffect(() => {
    if (current && current.id !== urlId) {
      setParams({ tab, id: current.id }, { replace: true });
    }
  }, [current, urlId, tab, setParams]);

  /* 选中的卡片滚进视野。

     左列有百余个岗位，而选中项可以由地址栏带进来（首页榜单、全景图谱与职业探索页
     都往这里跳），也可以由相图或空间图上点选。此前两种情形都不动滚动条：右栏画的是
     甲岗位，左列停在列首、亮着的那张卡片在一万多像素之外，读者看到的是"跳过来了
     但没选中"。列表自身的滚动容器单独滚，不牵动整页。 */
  const selRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const el = selRef.current;
    const box = el?.closest('.jl-scroll') as HTMLElement | null;
    if (!el || !box) return;
    const top = el.offsetTop - box.offsetTop;
    const bottom = top + el.offsetHeight;
    if (top < box.scrollTop || bottom > box.scrollTop + box.clientHeight) {
      /* 直接定位，不做平滑滚动：列表有百余项、上下可差一万余像素，平滑滚动
         要滚上几秒；带 id 进来时读者是"落到这一项"，不是"看着列表滚过去"。
         标签页在后台时平滑滚动还会整段不执行。 */
      box.scrollTo({ top: Math.max(0, top - box.clientHeight / 3), behavior: 'auto' });
    }
  }, [current?.id, tab]);

  /* 首页快报带 ?src=1 进来。那条报道的原文列在"数据来源"一节的新闻一列里，
     而这一节在岗位卡片下方两屏处：只把岗位选中、页面停在顶端，点进来的人
     看到的是一张与刚才那条消息对不上号的岗位卡。

     只滚一次：此后换岗位是读者自己的动作，再把页面拽到同一节等于夺走滚动条。
     延一帧再滚，等这一节随 current 渲染出来。

     src 只在首帧读一次，不进依赖表：选中项一落定，上面那个 effect 就把地址栏
     改写成不带 src 的形式，src 若作为依赖，这一次滚动会在定时器到点之前
     被依赖变化的清理函数取消。 */
  const srcWanted = useRef(params.get('src') === '1');
  useEffect(() => {
    if (!srcWanted.current || !current) return;
    /* 标记在定时器回调里清，不在 effect 体内清：选中项在这几百毫秒内还会
       随筛选与排序重算一两次，每重算一次清理函数就取消一次这里的定时器 ——
       标记若先置位，之后的重算只会直接返回，页面永远停在顶端。

       滚两次：这一节上方的年轮图与技能构成两块的高度要等各自量完宽度才定下来，
       首次滚到位之后它们还会把这一节往下推一屏有余。第二次按新位置校正，
       两次都用平滑滚动，读起来是一次连贯的下移。 */
    const at = () =>
      document.getElementById('job-source')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    const t = window.setTimeout(at, 260);
    const t2 = window.setTimeout(() => {
      srcWanted.current = false;
      at();
    }, 1100);
    return () => {
      window.clearTimeout(t);
      window.clearTimeout(t2);
    };
  }, [current]);

  /* ---------------- 检索框 ----------------
     全页任意位置按 “/” 聚焦检索框，Esc 清空当前检索词。 */

  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return;
      const el = document.activeElement as HTMLElement | null;
      if (el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable)) return;
      e.preventDefault();
      searchRef.current?.focus();
      searchRef.current?.select();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  /* ---------------- 概览统计 ---------------- */

  const meta = useMemo(() => {
    return {
      newCount: allJobs.filter((j) => j.emerging).length,
      oldCount: allJobs.filter((j) => !j.emerging).length,
      changed: allJobs.filter((j) => !j.emerging && freshCount(j.id) > 0).length,
      /* 本窗由叠层转正进入正式体系的条数。队列短不等于没发现，
         多数候选是被市场确认后转正走的，这个数把那一层交代出来 */
      graduated: REAL_GRAPH_STATS.graduated ?? 0,
      /* 候选侧没有“本期新进”这个字段，改报队列里最新的一个首现于何时 */
      newestFirst: allJobs
        .filter((j) => j.emerging)
        .reduce((m, j) => (j.firstSeen > m ? j.firstSeen : m), ''),
    };
  }, [allJobs, freshCount]);

  /* ---------------- 跨页出口 ----------------
     此前本页只有摘要栏上的“匹配该岗位”一条跨页入口。三个出口一律带上当前岗位，
     与摘要栏的那一条同源。

     同一份数组供两处使用：页尾的“下一步”写全每条回答什么，左列表中贴着选中卡片的
     浮窗（JumpDock）只取 short 与图标。摘要栏在两张主图之下，页尾更在四屏开外，
     而岗位是在列表里选定的，出口须在选定的地方就有一份。 */
  const exits = useMemo<StepItem[]>(() => {
    if (!current) return [];
    const q = encodeURIComponent(current.id);
    return [
      {
        to: `/match?target=${q}`,
        label: `以“${current.name}”运行匹配诊断`,
        desc: '上传或选取一份简历，按五维评价体系给出评分、能力差距明细与分阶段学习路径。',
        icon: 'target',
        primary: true,
        short: '人岗匹配',
      },
      {
        to: `/explore?job=${q}`,
        label: '查看能力结构相近的岗位',
        desc: '按能力构成重排全部岗位，给出与之最接近的若干个及其城市、薪资档分布。',
        icon: 'route',
        short: '职业探索',
      },
      {
        to: `/panorama?focus=${q}`,
        label: '在全景图谱中定位该岗位',
        desc: '该岗位在四层结构中的位置：由哪些核心任务构成，这些任务又要求哪些能力。',
        icon: 'graph',
        short: '全景图谱',
      },
    ];
  }, [current]);

  return (
    <div className="jobs">
      <div className="jobs-wrap">
        <PageGuide
          station={stationOf('/jobs')!}
          landed={landedName}
          onClearLanded={() => setLandedId(null)}
        />

        {/* ============ 子页面切换 ============ */}
        <div className="jobs-tabs" role="tablist" aria-label="岗位洞察子页面">
          {TABS.map((t) => (
            <button
              key={t.v}
              role="tab"
              aria-selected={tab === t.v}
              className={tab === t.v ? 'jt on' : 'jt'}
              onClick={() => tab !== t.v && switchTab(t.v)}
            >
              <span className="jt-ic">
                <Icon name={t.icon} size={19} />
              </span>
              <span className="jt-text">
                <b>
                  {t.title}
                  <em>{t.v === 'new' ? meta.newCount : meta.oldCount}</em>
                </b>
              </span>
              {/* 候选队列一侧另报本窗转正条数。

                  队列只剩一个候选不是"没发现几个"，而是上一批的候选多数已被
                  招聘市场确认、写进正式体系 —— 转正正是这条链路要的结果。
                  只报队列长度时，这一层意思整个丢掉，一个"1"看着像是没做出东西。 */}
              <span className="jt-flag">
                {t.v === 'new'
                  ? `${meta.graduated > 0 ? `本窗转正 ${meta.graduated} 项 · ` : ''}${meta.newestFirst ? `最近首现 ${meta.newestFirst}` : '暂无候选'
                  }`
                  : `${meta.changed} 个岗位本季有变动`}
              </span>
            </button>
          ))}
        </div>

        {/* ============ 筛选 ============
            检索是本页的主入口，此前它与两个下拉同宽同高同描边，四个控件同权，
            视线扫过时落不到检索上。这里把它提为主控件：加高、主色描边、
            独立投影，并给出快捷键与清空入口；下拉与重置降为次级控件。 */}
        <div className="jobs-filter">
          <label className={query ? 'jf-search on' : 'jf-search'}>
            <Icon name="search" size={18} />
            <input
              ref={searchRef}
              type="search"
              placeholder="搜索岗位名称、定义、职责或能力…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape' && query) {
                  e.preventDefault();
                  setQuery('');
                }
              }}
              aria-label="搜索岗位"
            />
            {query ? (
              <button
                type="button"
                className="jf-clear"
                onClick={() => {
                  setQuery('');
                  searchRef.current?.focus();
                }}
                aria-label="清空检索词"
              >
                <Icon name="close" size={13} />
              </button>
            ) : (
              <span className="jf-hint" aria-hidden="true">
                <kbd>/</kbd> 检索
              </span>
            )}
          </label>
          {/* 真实体系下岗位只有"顶层大类"这一条归属轴，不再另设技术栈下拉 ——
              两个下拉列的是同一批值，只会让人以为可以交叉筛出更细的口径。

              少于两个类别时整个下拉不出现：叠层新岗位尚未归入岗位体系的任一类别，
              这一子页面下拉里只剩"全部岗位类别"一项，点开与不点开筛出来的是同一批，
              留着它等于给一个按了没有反应的控件。 */}
          {clusters.length > 1 && (
            <select
              className={cluster ? 'on' : undefined}
              value={cluster}
              onChange={(e) => setCluster(e.target.value)}
              aria-label="按岗位类别筛选"
            >
              <option value="">全部岗位类别</option>
              {clusters.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          )}
          <select value={sortKey} onChange={(e) => setSort(e.target.value)} aria-label="排序方式">
            {SORTS.filter((s) => !s.tab || s.tab === tab).map((s) => (
              <option key={s.v} value={s.v}>
                {s.label}
              </option>
            ))}
          </select>
          <button
            className="btn"
            onClick={() => {
              setQuery('');
              setCluster('');
              setSort('');
              say('筛选条件已重置');
            }}
          >
            <Icon name="refresh" size={14} />
            重置
          </button>
          <span className={query || cluster ? 'jf-count on' : 'jf-count'}>
            筛选出 <b>{shown.length}</b> 个岗位
          </span>
        </div>

        {/* ============ 工作区 ============ */}
        <div className="jobs-work">
          {/* -------- 左：列表 -------- */}
          <aside className="jobs-list">
            <div className="jl-hd">
              <strong>{tab === 'new' ? '新岗位候选' : '既有岗位监测'}</strong>
              <span>{shown.length} 个</span>
              {/* 卡片第二行各项的来源在表头交代一次，逐张标太碎；
                  逐项的口径见选中后右栏的属性格。 */}
            </div>
            <div className="jl-scroll">
              {shown.map((j) => {
                const sig = d.signalMap.get(j.id);
                const n = freshCount(j.id);
                const on = current?.id === j.id;
                return (
                  <Fragment key={j.id}>
                    <button
                      ref={on ? selRef : undefined}
                      className={on ? 'jl-card on' : 'jl-card'}
                      onClick={() => select(j.id)}
                      aria-pressed={on}
                    >
                      <span className="jl-t">
                        <strong>{j.name}</strong>
                        {(() => {
                          if (j.emerging) {
                            const rows = foreSpark(sig);
                            if (!rows) return null;
                            return (
                              <span
                                className="jl-spark"
                                title={`${rows.map((r) => r.label).join(' 与 ')}逐观测窗口的强度`}
                              >
                                <DualSparkline series={rows} w={64} h={24} />
                              </span>
                            );
                          }
                          const jd = (sig?.jd ?? []).slice(-24);
                          if (!jd.some((v) => v > 0)) return null;
                          return (
                            <span className="jl-spark" title="招聘信息的近 24 个观测窗口">
                              <Sparkline values={jd} color="var(--src-jd)" w={64} h={24} />
                            </span>
                          );
                        })()}
                      </span>
                      {/* 真实体系下大类与聚类是同一个字段，同一行里重复两遍只是在占位。

                          城市、经验与薪资三项只对既有岗位画：这三项取自招聘信息，
                          而新岗位尚未进入招聘市场，三格一律是破折号 —— 一行里三个
                          破折号，读起来像是数据没加载出来。新岗位这一行改写它自己
                          有读数的两项：一级归属与前瞻信号强度。 */}
                      {j.emerging ? (
                        <span className="jl-m">
                          {j.cluster || '尚未归入体系'}
                          <i>·</i>
                          前瞻强度 {(j.gap * 100).toFixed(0)}%
                        </span>
                      ) : (
                        <>
                          <span className="jl-m">
                            {j.cluster}
                            {j.category !== j.cluster && (
                              <>
                                <i>·</i>
                                {j.category}
                              </>
                            )}
                            <i>·</i>
                            {topBucket(j.attrs?.cities)}
                            <i>·</i>
                            {topBucket(j.attrs?.experience)}
                          </span>
                          <span className="jl-b">
                            <ConfBar value={j.confidence} />
                            <span className="jl-sal">{topBucket(j.attrs?.salaryBands)}</span>
                          </span>
                        </>
                      )}
                      <span className="jl-u">
                        {j.emerging
                          ? `首现 ${j.firstSeen} · 至今 ${monthDiff(j.firstSeen, NOW)} 个月`
                          : `${lastRingOf(j.id)?.date ?? LATEST.date} · ${n > 0 ? `本季 ${n} 项能力变动` : '本季未检出重大变动'}`}
                      </span>
                    </button>
                    {/* 选中卡片上的跨页出口。锚点是一张零高度的空块，排在卡片之后，
                      浮窗自锚点向上浮起，落进选中卡片末行之下开出来的那段空白里。

                      浮在卡片之外的两个方向都试过：向上压的是上一张卡片的末行，
                      向下压的是下一张卡片的岗位名 —— 压的都是另一个岗位，且随选中项
                      逐张挪动，每点一次就有一行别的信息被换着盖掉；卡片之间又只隔
                      四像素，尖角撑不起“这是给哪一张卡片的”这层交代。改为由选中的
                      卡片自己让出一段位置（见 jobs.css 的 .jl-card.on），归属由包含
                      关系直接交代，卡片上原有的四行一行不压。

                      浮窗不进 button：它内含链接，而链接不能嵌在按钮里。 */}
                    {on && (
                      <div className="jl-dock">
                        <div className="jdk-wrap">
                          <JumpDock items={exits} label="把选中的岗位带往其他页面" />
                        </div>
                      </div>
                    )}
                  </Fragment>
                );
              })}
              {shown.length === 0 && (
                <div className="jl-empty">
                  <Icon name="search" size={30} />
                  <p>没有符合当前筛选条件的岗位</p>
                </div>
              )}
            </div>
          </aside>

          {/* -------- 右：主图 + 详情 -------- */}
          <main className="jobs-detail">
            {tab === 'new' ? (
              <>
                <Panel
                  title="岗位涌现相图"
                  bodyStyle={{ padding: 12 }}
                >
                  <EmergencePhasePlot
                    jobs={pool}
                    edges={d.edges}
                    signalMap={d.signalMap}
                    selectedId={current?.id ?? null}
                    focusIds={new Set(shown.map((j) => j.id))}
                    onSelect={select}
                  />
                </Panel>

                {/* 相图之后紧接着这一张：相图说“它在长出来”，这一张说“它长在哪儿”。
                  两张图共用同一个选中项，点哪一张另一张跟着走。 */}
                {space && (
                  <Panel
                    title="岗位空间关系图"
                    bodyStyle={{ padding: 12 }}
                  >
                    <div className="jsp-split">
                      <div className="jsp-main">
                        <JobSpaceMap
                          space={space}
                          selectedId={current?.id ?? null}
                          focusIds={new Set(shown.map((j) => j.id))}
                          peerId={peerId}
                          onSelect={select}
                          onPeerHover={setPeerId}
                        />
                        <div className="jsp-legend">
                          <span>
                            <i className="sw sw-star" />
                            既有岗位 {space.points.filter((p) => !p.job.emerging).length} 个
                          </span>
                          <span>
                            <i className="sw-ring" />
                            {/* 圈的半径口径此前只写在代码里：它取该类成员到质心距离的
                                四分之三分位，故每一类都有约四分之一的岗位落在自己那一圈
                                之外。不写明这一条，圈外的岗位会被读成归类出了错。 */}
                            岗位类别覆盖范围
                          </span>
                          <span>
                            <i className="sw-mix" />
                            新岗位信号来源：<b style={{ color: 'var(--src-paper-ink)' }}>论文</b>（前瞻）←→{' '}
                            <b style={{ color: 'var(--src-news-ink)' }}>新闻</b>（产业采纳）
                            {/* 两侧的点按两套口径定大小，图例里要分开说：右下角那一组
                                尺寸标的是既有岗位的市场占比，而新岗位尚无占比可言。 */}
                          </span>
                        </div>
                      </div>
                      <JobSpacePeers
                        point={spacePoint}
                        name={current?.name}
                        peerId={peerId}
                        onPeerHover={setPeerId}
                      />
                    </div>
                  </Panel>
                )}
              </>
            ) : current && annuliOf.get(current.id) ? (
              <EvolutionBlock
                annuli={annuliOf.get(current.id)!}
                skillFocus={skillFocus}
                onSkillFocus={setSkillFocus}
                opFilter={opFilter}
                onOpFilter={setOpFilter}
                verFilter={verFilter}
                onVerFilter={setVerFilter}
              />
            ) : null}

            {current ? (
              <>
                <JobSummary
                  job={current}
                  tab={tab}
                  changeCount={freshCount(current.id)}
                  onMatch={() => nav(`/match?target=${encodeURIComponent(current.id)}`)}
                />

                {tab === 'new' ? (
                  <DefinitionCard job={current} />
                ) : (
                  <SkillComposition
                    job={current}
                    edges={d.edges}
                    nodeById={nodeById}
                    signalMap={d.signalMap}
                    skillFocus={skillFocus}
                    onSkillFocus={setSkillFocus}
                  />
                )}

                <JobSourceView
                  job={current}
                  edges={d.edges}
                  nodeById={nodeById}
                  signal={d.signalMap.get(current.id)}
                />
              </>
            ) : (
              <Panel title="暂无可展示的岗位" sub="调整左侧筛选条件，或清空搜索词">
                <div className="jl-empty">
                  <Icon name="doc" size={32} />
                  <p>当前筛选条件下没有匹配的岗位，可放宽大类或清空搜索词</p>
                </div>
              </Panel>
            )}
          </main>
        </div>

        <NextSteps from="/jobs" items={exits} />
      </div>

      {toast && <div className="jobs-toast">{toast}</div>}
      <Footer />
    </div>
  );
}

/* ==================== 岗位摘要 ==================== */

function JobSummary({
  job,
  tab,
  changeCount,
  onMatch,
}: {
  job: GraphNode;
  tab: Tab;
  changeCount: number;
  onMatch: () => void;
}) {
  const a = job.attrs;
  const flag =
    tab === 'new'
      ? { cls: 'tag-new', text: '新岗位候选' }
      : changeCount > 0
        ? { cls: 'tag-diff', text: `本季 ${changeCount} 项能力变动` }
        : { cls: 'tag-calm', text: '本季稳定' };

  /* 市场占比只在测得出的那一档露出。叠层新岗位尚未进入招聘市场，加权出现量
     整批为零，这一项对它们恒等于 0.00% —— 一个恒定的零读起来像一次读数。 */
  const showShare = !job.emerging && job.marketShare > 0;

  return (
    <section className="panel js">
      <div className="js-main">
        <div className="js-left">
          {/* 置信度与占比与岗位名同行：它们是这个岗位名的限定语 ——
              这条定义有多稳、这个岗位在市场上多大，读岗位名时就该一并读到。
              另起一行时中间隔着类别与标签，两者与名字的关系反而看不出来。 */}
          {/* 一级归属并入标题行。它与置信度、市场占比一样是岗位名的限定语，
              单起一行时下面只有两三个字、上面是一行大字，中间那道空白比这两个字
              还高，读起来像是漏了一段内容。 */}
          <div className="js-title">
            <h2>{job.name}</h2>
            {/* 新岗位不带一级归属，此处照写会剩一个空标签；与左栏卡片同一句 */}
            <span className="js-cat">{job.cluster || '尚未归入体系'}</span>
            {!!job.cluster && job.category !== job.cluster && (
              <span className="js-cat">{job.category}</span>
            )}
            <span className={`tag ${flag.cls}`}>{flag.text}</span>
            <span className="js-title-m">
              <ConfBar value={job.confidence} label="定义置信度" />
              {showShare && <span>市场占比 {(job.marketShare * 100).toFixed(2)}%</span>}
            </span>
          </div>
          {/* 新岗位一侧不在此处再列一遍定义：紧随其后的"岗位定义 · 五要素"
              第一格就是岗位名与同一段定义，两处逐字相同。 */}
          {job.definition && tab !== 'new' && <p className="js-def">{job.definition}</p>}
          {/* 此处原另起一行列出边界判据（与最容易混淆的那个同侪岗位凭什么划开）。
              这一行讲的是归类的判据，属定义的细目，与页头这一段"这个岗位是什么"
              不在同一层；页头连出两段长文，读者尚未看到任何读数就先读了三行字。
              边界仍在下方"岗位定义 · 五要素"与岗位详情表内逐条列出，此处不再重复。 */}
        </div>
        <div className="js-act">
          <button className="btn primary" onClick={onMatch}>
            <Icon name="target" size={14} />
            匹配该岗位
          </button>
        </div>
      </div>
      {/* 属性格。这六格全部取自招聘信息，故对尚未进入招聘市场的叠层新岗位
          一格也填不出来：省份、学历、经验、薪资、出现频次五项在这批岗位上
          均无实测值，一律不出现，而不是列出来再各写一个“—”——
          六个破折号排成一行，读起来像是数据没加载出来。

          新岗位这一侧只留"首次出现"与"前瞻强度"两格：前者是它的入场窗口，
          后者是它现有的唯一读数，两项都实测。其能力构成与证据另在下方的
          "岗位定义 · 五要素"与"数据来源"两节。

          地域一维取到省级：招聘原文的 place 列写到市，一格里列不下三百余座
          城市，按行政区划汇总后与"在哪儿招人"这一问的既有认知一致，
          逐城的分布在职业探索页的属性栏里仍可展开。 */}
      <dl className={job.emerging ? 'js-grid js-grid-3' : 'js-grid'}>
        {!job.emerging && (
          <>
            <div>
              <dt>主要省份</dt>
              <dd>{topBucket(a?.cities)}</dd>
            </div>
            <div>
              <dt>学历要求</dt>
              <dd>{topBucket(a?.degrees)}</dd>
            </div>
            <div>
              <dt>经验要求</dt>
              <dd>{topBucket(a?.experience)}</dd>
            </div>
            <div>
              <dt>薪资区间</dt>
              <dd>{topBucket(a?.salaryBands)}</dd>
            </div>
          </>
        )}
        <div>
          <dt>首次出现</dt>
          <dd>{job.firstSeen}</dd>
        </div>
        {job.emerging && (
          <div>
            <dt>前瞻强度</dt>
            <dd>{(job.gap * 100).toFixed(0)}%</dd>
          </div>
        )}
        {!job.emerging && (
          <div>
            {/* 括号里交代口径：这个数与下方“数据来源”里的招聘信息条数不同源，
                前者数的是全库判定为该岗位的原文条数，后者数的是抽样汇总表内的
                条数，两个数同屏出现而相差一个量级，不写明就成了自相矛盾。 */}
            <dt>出现频次（招聘条数）</dt>
            <dd>{job.frequency.toLocaleString()}</dd>
          </div>
        )}
      </dl>
    </section>
  );
}

/* ==================== 岗位定义五要素 ====================

   五项分两类：名称与定义取自算法侧的叠层产出，核心职责、必备技能、加分技能
   与典型应用场景四项由构建阶段推得，逐项在卡片上写明依据。

   版式由两列平铺改为"一栏定义 + 三段读数"。平铺时四项各占一格、格内一串标签，
   一屏之内既读不出每项能力有多重，也读不出四项之间的先后；而这几项本身都带
   量：必备与加分带覆盖率或权重，场景带占比。量画成条，四项因而在同一把尺子下
   可比，右侧留出的那一列数也不再是"暂无"两个字占着一格。
   ======================================================== */

/** 一条带量的要素行：左名右量，量画成一根贴底的细条 */
function DefRow({
  name,
  value,
  text,
  tone,
}: {
  name: string;
  /** 0–1，条长按它取 */
  value: number;
  /** 条右侧的读数 */
  text: string;
  tone: string;
}) {
  return (
    <li className="dfr">
      <span className="dfr-n">{name}</span>
      <span className="dfr-bar">
        <i className={`dfr-fill tone-${tone}`} style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }} />
      </span>
      <span className="dfr-v">{text}</span>
    </li>
  );
}

/** 熟练度均值写成档名加小数，只写 P2 读不出"偏向哪一头" */
const PROF_NAME = ['', '了解', '熟练', '精通', '专家'];
const profText = (lvl?: number) => {
  if (!lvl) return '—';
  const k = Math.max(1, Math.min(4, Math.round(lvl)));
  return `${PROF_NAME[k]}（均值 ${lvl.toFixed(2)}）`;
};

function DefinitionCard({ job }: { job: GraphNode }) {
  const def = job.jobDef;
  const inferred = def?.via === 'inferred';
  const duties = job.coreDuties ?? [];

  return (
    <Panel
      title="岗位定义 · 五要素"
      actions={
        <div className="pn-act">
        </div>
      }
    >
      <div className="five">
        {/* 01 名称与定义单占一栏：它是这张卡片的主语，其余三段都在说它 */}
        <div className="five-head">
          <div className="five-lb">01 · 岗位名称</div>
          <div className="five-name">{job.name}</div>
          <p className="five-def">{job.definition}</p>
        </div>

        <div className="five-grid">
          <section className="five-cell wide">
            <div className="five-lb tone-task">
              02 · 核心职责
              <em className="five-n">{duties.length} 项</em>
            </div>
            {/* 下一行的 key 取名称加序号：名称本身曾作 key，而同名条目一出现，
                React 便按同一个 key 复用节点，换个岗位时上一个岗位的行留在原处，
                一栏里因而排出十来行、序号还对不上。 */}
            {duties.length ? (
              <ol className="five-duty">
                {duties.map((it, i2) => (
                  <li key={`${it}#${i2}`}>
                    <span>{i2 + 1}</span>
                    <div>{it}</div>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="five-none">证据不足以推出任务构成，本项留空。</p>
            )}
          </section>

          <section className="five-cell">
            <div className="five-lb tone-skill">
              03 · 必备技能
              <em className="five-n">{def?.must.length ?? 0} 项</em>
            </div>
            {def?.must.length ? (
              <ul className="dfr-list">
                {def.must.map((x) => (
                  <DefRow
                    key={x.code}
                    name={x.name}
                    value={inferred ? (x.w ?? 0) : (x.cov ?? 0)}
                    text={inferred ? `权重 ${(x.w ?? 0).toFixed(2)}` : `${((x.cov ?? 0) * 100).toFixed(0)}% 的招聘信息要求`}
                    tone="skill"
                  />
                ))}
              </ul>
            ) : (
              <p className="five-none">无覆盖率过门槛的技能。</p>
            )}
          </section>

          <section className="five-cell">
            <div className="five-lb tone-sp">
              04 · 加分技能
              <em className="five-n">{def?.plus.length ?? 0} 项</em>
            </div>
            {def?.plus.length ? (
              <ul className="dfr-list">
                {def.plus.map((x) => (
                  <DefRow
                    key={x.code}
                    name={x.name}
                    value={inferred ? (x.w ?? 0) : Math.min(1, (x.lvl ?? 0) / 4)}
                    text={inferred ? `权重 ${(x.w ?? 0).toFixed(2)}` : profText(x.lvl)}
                    tone="sp"
                  />
                ))}
              </ul>
            ) : (
              <p className="five-none">
                超出必备均值的技能均未达样本量下限，本项不列。
              </p>
            )}
          </section>

          <section className="five-cell wide">
            <div className="five-lb tone-news">
              05 · 典型应用场景
              <em className="five-n">{def?.scenarios.length ?? 0} 个技术方向</em>
            </div>
            {def?.scenarios.length ? (
              <ul className="dfr-list two">
                {def.scenarios.map((x) => (
                  <DefRow
                    key={x.name}
                    name={x.name}
                    value={x.share}
                    text={`${(x.share * 100).toFixed(0)}%`}
                    tone="news"
                  />
                ))}
              </ul>
            ) : (
              <p className="five-none">尚无可据以判定技术方向的招聘投放。</p>
            )}
          </section>
        </div>
      </div>
    </Panel>
  );
}

/* ==================== 既有岗位：年轮 + 变更清单 ==================== */

function EvolutionBlock({
  annuli,
  skillFocus,
  onSkillFocus,
  opFilter,
  onOpFilter,
  verFilter,
  onVerFilter,
}: {
  annuli: JobAnnuli;
  skillFocus: string | null;
  onSkillFocus: (v: string | null) => void;
  opFilter: ChangeOp | 'all';
  onOpFilter: (v: ChangeOp | 'all') => void;
  verFilter: string;
  onVerFilter: (v: string) => void;
}) {
  const colors = useAnnuliColors(annuli);

  const stat = useMemo(() => {
    const s: Record<ChangeOp, number> = { add: 0, remove: 0, modify: 0, merge: 0 };
    annuli.changes.forEach((c) => (s[c.op] += 1));
    return s;
  }, [annuli]);

  /* 此处原挂一块"下一季度前瞻"：把论文与新闻的讨论热度作为协变量算进季度预测，
     报出各能力被抬高或压低多少个百分点。两遍推算之差本身是个二阶量，落到界面上
     是一列三位小数的差值，读者要先接受"两遍推算"这个前提才读得懂它在说什么，
     而这一栏又窄，能力名一压再压。整块撤除，右栏由变更清单独占。

     年轮最外一圈的虚线预测环仍在，它读的是同一份预测的绝对占比，
     一环一句"下一季大致是这个样子"，不必先讲清两遍推算的差。 */

  /* 本季 = 最外一个**实测**环所在的那一季。预测环不产出变更条目，
     故取环列表里最后一个 predicted 为假的环，与左列卡片上的那个数同源。 */
  const latestVer = useMemo(() => {
    const r = [...annuli.rings].reverse().find((x) => !x.predicted);
    return r?.version ?? '';
  }, [annuli.rings]);
  const freshN = useMemo(
    () => annuli.changes.filter((c) => c.version === latestVer).length,
    [annuli.changes, latestVer],
  );

  /** 各能力各自有多少条变更 —— 标在图例上，避免点开一项才发现它从没变过 */
  const perSkill = useMemo(() => {
    const m = new Map<string, number>();
    annuli.changes.forEach((c) => m.set(c.target.id, (m.get(c.target.id) ?? 0) + 1));
    return m;
  }, [annuli]);

  /** 图例分段。colors.legend 已按“组 → 组内份额”排好，这里只做一次相邻归并 */
  const legendGroups = useMemo(() => {
    const out: { group: string; items: typeof colors.legend }[] = [];
    for (const it of colors.legend) {
      const last = out[out.length - 1];
      if (last && last.group === it.group) last.items.push(it);
      else out.push({ group: it.group, items: [it] });
    }
    return out;
  }, [colors.legend]);

  const focusName = skillFocus
    ? (colors.nameOf.get(skillFocus) ?? annuli.changes.find((c) => c.target.id === skillFocus)?.target.name)
    : undefined;

  const list = useMemo(
    () =>
      annuli.changes.filter(
        (c) =>
          (opFilter === 'all' || c.op === opFilter) &&
          (verFilter === 'all' || c.version === verFilter) &&
          (!skillFocus || c.target.id === skillFocus),
      ),
    [annuli, opFilter, verFilter, skillFocus],
  );

  return (
    /* 左栏是年轮，右栏自上而下是变更清单与下一季度前瞻。
       清单原先排在年轮下方的左栏里：它逐条写的正是年轮上那些刻痕的来龙去脉，
       与图分处上下两段时，看图的人要把视线移出图外再往回找。移到右栏之后
       两者并排，点图上一环即在右侧读到对应的那几条。前瞻是下一季的推算，
       排在已发生的变更之后。 */
    <section className="evo">
      <Panel title="能力年轮" className="evo-ann" bodyStyle={{ padding: 12 }}>
        <CapabilityAnnuli data={annuli} selectedSkillId={skillFocus} onSelectSkill={onSkillFocus} />
        {/* 图例按能力组分段，段内的几项共用一个色相。
            上一版只列前八项，其余合成一格“其他 N 项能力”——
            那 N 项在图上占了近半圆周，却在图例里既没有名字也点不开。 */}
        <div className="an-legend">
          {legendGroups.map((g) => (
            <div className="anl-g" key={g.group}>
              <span className="anl-gt">{g.group}</span>
              {g.items.map((c) => {
                const n = perSkill.get(c.id) ?? 0;
                return (
                  <button
                    key={c.id}
                    className={skillFocus === c.id ? 'anl on' : 'anl'}
                    onClick={() => onSkillFocus(skillFocus === c.id ? null : c.id)}
                    aria-label={n > 0 ? `${c.name}，${n} 条变更` : `${c.name}，各版本保持稳定`}
                  >
                    <i style={{ background: c.color }} />
                    {c.name}
                    {n > 0 && <b className="anl-n">{n}</b>}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
        <div className="an-marks">
          <span>
            <i className="mk add" />
            新增刻痕
          </span>
          <span>
            <i className="mk mod" />
            修改刻痕
          </span>
          <span>
            <i className="mk merge" />
            合并
          </span>
          <span>
            <i className="mk rm" />✕ 删除
          </span>
          <span>
            <i className="mk hatch" />
            斜纹 = 前瞻信号支撑
          </span>
          <span>
            <i className="mk pred" />
            虚线圈 = 尚未发生的预测
          </span>
        </div>
      </Panel>

      <Panel
        title="能力变更清单"
        className="evo-chg"
        actions={
          <div className="ch-act">
            {/* 选项取自这张年轮自己的环，不取全站版本序列：年轮按季成环，
                两者的标识不同名，用后者做选项会筛不出任何一条。 */}
            <select value={verFilter} onChange={(e) => onVerFilter(e.target.value)} aria-label="按季度筛选">
              <option value="all">全部季度</option>
              {annuli.rings.map((r) => (
                <option key={r.version} value={r.version}>
                  {r.version} · {r.date}
                </option>
              ))}
            </select>
            {skillFocus && (
              <button className="btn sm" onClick={() => onSkillFocus(null)}>
                清除能力筛选
              </button>
            )}
          </div>
        }
      >
        {/* "全部"数的是这张年轮全部季度累计的变更，而左列卡片上的那个数只数
            最外一环那一季 —— 两处口径不同，此前都只写一个数字，摆在同一屏里
            对不上。现在各自写明数的是哪一段。 */}
        <div className="ch-bar">
          <button className={opFilter === 'all' ? 'chip on' : 'chip'} onClick={() => onOpFilter('all')}>
            全部季度 {annuli.changes.length}
          </button>
          <button
            className={verFilter === latestVer ? 'chip on' : 'chip'}
            onClick={() => onVerFilter(verFilter === latestVer ? 'all' : latestVer)}
          >
            本季 {freshN}
          </button>
          {(['add', 'remove', 'modify', 'merge'] as ChangeOp[]).map((o) => (
            <button
              key={o}
              className={opFilter === o ? 'chip on' : 'chip'}
              onClick={() => onOpFilter(o)}
              style={opFilter === o ? undefined : { color: OP_COLOR[o] }}
            >
              {opLabel(o)} {stat[o]}
            </button>
          ))}
          <span className="ch-prog">
            当前列出 <b>{list.length}</b> 条
          </span>
        </div>

        <div className="ch-list">
          {/* 变更由逐窗的份额差分算出，一条变更对应的是两窗之间的一个差值，
              不对应某一篇文档，故此处不再挂原文与复核状态两栏：前者恒为零条，
              后者恒为"自动入图"，两栏加起来占掉每张卡片近一半的高度。
              逐条原文在下方"数据来源"里按来源列出。 */}
          {list.map((c) => {
            return (
              <article key={c.id} className="ch">
                <div className="ch-top">
                  <span className="ch-op" style={{ background: OP_COLOR[c.op] }}>
                    {opLabel(c.op)}
                  </span>
                  <button className="ch-name" onClick={() => onSkillFocus(c.target.id)}>
                    {c.target.name}
                  </button>
                  {c.before !== undefined && c.after !== undefined && (
                    <span className="ch-delta">
                      {(c.before * 100).toFixed(1)}%
                      <Icon name="arrowR" size={12} />
                      {(c.after * 100).toFixed(1)}%
                    </span>
                  )}
                  <span className="ch-ver">
                    {c.version} · {c.date}
                  </span>
                </div>
                <p className="ch-reason">
                  <b>更新说明：</b>
                  {c.reason}
                </p>
                {c.mergeScores && (
                  <p className="ch-merge">
                    冗余判定：名称相似 {c.mergeScores.nameCosine} · 出边重合 {c.mergeScores.outJaccard} · 入边重合{' '}
                    {c.mergeScores.inJaccard}
                    {c.mergedFrom && ` · 合并自“${c.mergedFrom}”`}
                  </p>
                )}
              </article>
            );
          })}
          {list.length === 0 && (
            <div className="jl-empty">
              <Icon name="check" size={28} />
              {/* 两种空要分开写：一种是筛出来的空，一种是这个岗位本身没有变动。
                  后者是一条结论 —— 能力构成在本批各季度间的进退均未超过记入阈值，
                  与"没查到"不是一回事。 */}
              <p>
                {focusName
                  ? `“${focusName}”在所选季度内没有变更记录`
                  : annuli.changes.length === 0
                    ? '该岗位的能力构成在本批各季度之间未出现超过记入阈值的进退，故无变更条目'
                    : '当前筛选下没有变更记录，可放宽季度或变更类型'}
              </p>
            </div>
          )}
        </div>
      </Panel>
    </section>
  );
}

/* ==================== 能力构成（到技能点） ==================== */

function SkillComposition({
  job,
  edges,
  nodeById,
  signalMap,
  skillFocus,
  onSkillFocus,
}: {
  job: GraphNode;
  edges: GraphEdge[];
  nodeById: Map<string, GraphNode>;
  signalMap: Map<string, EntitySignal>;
  skillFocus: string | null;
  onSkillFocus: (v: string | null) => void;
}) {
  const rows = useMemo(() => {
    const w0 = jobSkillWeights(job.id, NOW, edges, signalMap, 0);
    const w1 = jobSkillWeights(job.id, NOW, edges, signalMap, 1);
    const out = [...w1.entries()].map(([sid, v]) => {
      const base = w0.get(sid)?.total ?? 0;
      const points = edges
        .filter((e) => e.kind === 'S-SP' && e.source === sid)
        .sort((a, b) => b.effectiveWeight - a.effectiveWeight)
        .slice(0, 5)
        .map((e) => {
          const n = nodeById.get(e.target);
          return {
            id: e.target,
            name: n?.name ?? e.target,
            level: n?.level ?? 1,
            ghost: e.baseWeight === 0,
          };
        });
      const direct = edges.find((e) => e.kind === 'J-S' && e.source === job.id && e.target === sid);
      return {
        id: sid,
        name: nodeById.get(sid)?.name ?? sid,
        base,
        delta: Math.max(0, v.total - base),
        total: v.total,
        forward: v.forward,
        points,
        edge: direct,
      };
    });
    return out.sort((a, b) => b.total - a.total).slice(0, 8);
  }, [job.id, edges, nodeById, signalMap]);

  return (
    <Panel
      title="能力构成"
      actions={
        <div className="pn-act">
          {skillFocus && (
            <button className="btn sm" onClick={() => onSkillFocus(null)}>
              取消高亮
            </button>
          )}
        </div>
      }
    >
      <StructureBars items={rows.map((r) => ({ name: r.name, base: r.base, delta: r.delta }))} max={8} />
      <div className="mini-legend">
        <span>
          <i style={{ background: 'var(--src-jd)' }} />
          招聘市场已确认
        </span>
        <span>
          <i style={{ background: 'var(--src-paper)' }} />
          前瞻信号追加
        </span>
      </div>

      <div className="sp-list">
        {rows.map((r) => (
          <div key={r.id} className={skillFocus === r.id ? 'sp on' : 'sp'}>
            <div className="sp-hd">
              <button className="sp-n" onClick={() => onSkillFocus(skillFocus === r.id ? null : r.id)}>
                {r.name}
              </button>
              {r.forward && <span className="tag tone-news">含前瞻</span>}
              <span className="sp-w">{r.total.toFixed(2)}</span>
              {/* 只当作可信度读数，不再点开抽屉 —— 原文一律在下方“数据来源”里就地展开 */}
              {r.edge && (
                <span
                  className="sp-ev"
                  aria-label={`${r.name}：招聘 ${r.edge.sourceMix.jd} · 论文 ${r.edge.sourceMix.paper} · 新闻 ${r.edge.sourceMix.news} 条来源，可信度 ${(r.edge.confidence * 100).toFixed(0)}%`}
                >
                  {r.edge.provenance === 'measured' ? '原始数据已记录' : '推断值'}
                </span>
              )}
            </div>
            <div className="sp-pts">
              {r.points.map((p) => (
                <span key={p.id} className={p.ghost ? 'spp ghost' : 'spp'}>
                  {p.name}
                  <em>{LEVEL_LABEL[p.level]}</em>
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}
