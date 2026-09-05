/* =========================================================
   职业探索 —— JobViz 系统的复刻

   人岗匹配回答的是"我这份简历配不配得上这个岗位"，前提是目标岗位已经定了。
   这一页回答它前面那一步：目标还没定的时候，从能力出发能走到哪些岗位去。

   整页照 JobViz（Wang et al., Visual Informatics 2024）Figure 2 的三视图排：

     左  能力—岗位总览（Skill-job Overview, Fig.2A）
         能力体系树 + 需求条 ‖ 岗位条 ‖ 属性分布条，三段用曲线连起来
     右上 岗位探索（Post Exploration View, Fig.2B）
         先看聚类字形，点开一簇再看簇内岗位落在"公司类别 × 薪资档"的哪一格
     右下 岗位详情（Post Detail View, Fig.2C）
         逐条列出所选落点，最多两个左右并排

   左列高、右列拆成上下两块，是论文原本的比例：总览是一张要从头读到尾的长图，
   而聚类与详情是"点一下看一眼"的短图，叠在右侧正好共用总览的那一屏高度。

   移到本系统之后换掉的只有对象本身：论文的"技能框架 → 岗位"变成
   "能力维度 → 能力组 → 技能点 → 岗位"，属性栏的"行业"换成同样来自岗位属性的
   "公司类别"。编码方式与四类交互一条没改，取数口径见 data/explore.ts 顶部的对应表。
   ========================================================= */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useDataset } from '@/api/client';
import { Panel } from '@/components/common/Panel';
import { PageGuide } from '@/components/common/PageGuide';
import { NextSteps, type StepItem } from '@/components/common/NextSteps';
import { JumpDock } from '@/components/common/JumpDock';
import { stationOf } from '@/data/journey';
import { Tooltip, type TipState } from '@/components/common/Tooltip';
import { Icon } from '@/components/Icon';
import { Footer } from '@/components/Footer';
import { SkillJobOverview } from '@/components/viz/SkillJobOverview';
import { PostExplorationView } from '@/components/viz/PostExplorationView';
import { PostDetailView } from '@/components/viz/PostDetailView';
import {
  ATTR_KINDS,
  MIX_COLORS,
  PROF_COLORS,
  PROF_LEVELS,
  PROF_UNKNOWN,
  SALARY_COLORS,
  SKILL_TYPES,
  buildAttrGroups,
  buildClusters,
  buildOverview,
  buildPostCells,
  cityCountsIn,
  countPicks,
  emptyPicks,
  exploreBase,
  filterByPicks,
  sortBySimilarity,
  type AttrKind,
  type AttrPicks,
  type PostCell,
} from '@/data/explore';
import { ATTR_ROW_LIMIT, postDetail, skillTree } from '@/data/jobviz';
import { CITY_COUNTS, DEGREE_AXIS } from '@/data/realGraph';
import { provinceOf } from '@/data/provinces';
import { useSize } from '@/hooks/useSize';
import '@/styles/explore.css';

/** 数据里实际出现过的城市，按条数降序。城市勾选菜单只列这些 —— 
    行政区划表里有而本批一条招聘信息也没有的城市，列出来只是让菜单变长 */
const CITIES_IN_DATA = Object.entries(CITY_COUNTS)
  .sort((a, b) => b[1] - a[1])
  .map(([c]) => c);

/** 省 → 该省在数据里出现过的城市，按条数降序 */
const CITIES_BY_PROVINCE_IN_DATA = (() => {
  const m = new Map<string, string[]>();
  for (const c of CITIES_IN_DATA) {
    const pv = provinceOf(c);
    const arr = m.get(pv);
    if (arr) arr.push(c);
    else m.set(pv, [c]);
  }
  return m;
})();

/* 岗位列一次列出多少个。论文过滤掉招聘信息不足百条的岗位，剩 79 个。
   这里画布高度固定 980px，列出得越多每条越细：80 条时行距压到 12px，
   条与名字糊成一片，故默认取 40（行距 24px）。体系内共 142 个岗位，
   "全部列出"即这 142 个，中间不必再设更高的档。 */
const JOB_STEPS = [40, 80, 0];

const keyOf = (c: PostCell | null) => (c ? `${c.jobId}|${c.cc}|${c.band}` : null);

export function Explore() {
  const d = useDataset();
  const [params] = useSearchParams();

  const base = useMemo(() => exploreBase(), []);
  const tree = useMemo(() => skillTree(), []);
  const allGroupIds = useMemo(() => tree.flatMap((t) => t.groups.map((g) => g.id)), [tree]);

  /* 取数层已把没有实测边的岗位排除在外（见 explore.exploreBase）。
     这里跟着读同一份，页头的计数才与图上真正能列出的岗位数对得上。 */
  const allJobIds = useMemo(() => [...base.jobs.keys()], [base.jobs]);

  /* ---- 左栏控件 ---- */
  const [sortMode, setSortMode] = useState<'quantity' | 'similarity'>('quantity');
  const [anchor, setAnchor] = useState<string | null>(null);
  /* 岗位列一次列出多少个。此处固定取默认档：列得越多每条越细，
     而这张图要读的是条长。 */
  const jobLimit = JOB_STEPS[0];

  /* 省份这一维画在属性栏上，城市只在省份行自己的下拉里出现：勾掉一座城，
     它那部分条数从所属省份的条上退出去。cityPick 为 null 即“未设城市条件”，
     此时不必逐岗位重算省份分布，走 attrs 里现成的那一份。 */
  const [cityPick, setCityPick] = useState<Set<string> | null>(null);
  /** 图上省份行的城市下拉开在哪一省 */
  const [cityMenuOf, setCityMenuOf] = useState<string | null>(null);

  /* ---- 选中态 ---- */
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(allGroupIds));
  const [litDims, setLitDims] = useState<Set<string>>(new Set());
  const [selSkills, setSelSkills] = useState<Set<string>>(new Set());
  const [selJobs, setSelJobs] = useState<Set<string>>(new Set());
  /* 四维各存一份已选分档：换维不再清空，四维的条件可以同时挂着 */
  const [picks, setPicks] = useState<AttrPicks>(emptyPicks);

  /* ---- 右栏 ---- */
  const [submitted, setSubmitted] = useState<string[] | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  /* 详情栏的两格与"下一次左键该顶掉哪一格"合成一份状态：填哪一格要同时看
     另一格占没占，拆成三份 state 的话连点两下会各自读到同一份旧值，
     第二下把第一下顶掉，两列永远凑不齐。 */
  const [pair, setPair] = useState<{
    left: PostCell | null;
    right: PostCell | null;
    next: 'left' | 'right';
  }>({ left: null, right: null, next: 'left' });
  const { left, right } = pair;

  const [tip, setTip] = useState<TipState | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  /* 最后被选定的那个岗位，跨页出口指向它。
     本页有三处可以选定一个岗位：图上单击岗位条、簇内分布里单击落点送入详情栏、
     岗位条右击设为排序基准。三处若按固定优先级取，先动的那一处会一直压着后动的；
     这里按发生的先后取，页尾的出口与图上的浮窗因此始终指同一个岗位。 */
  const [pickedJob, setPickedJob] = useState<string | null>(null);

  const flowBox = useSize<HTMLDivElement>();
  const mapBox = useSize<HTMLDivElement>();

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 2400);
    return () => window.clearTimeout(t);
  }, [toast]);

  /* 城市下拉：点到别处即收起。浮窗内部的点击在渲染处已挡下 */
  useEffect(() => {
    if (!cityMenuOf) return;
    const off = () => setCityMenuOf(null);
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setCityMenuOf(null);
    };
    document.addEventListener('mousedown', off);
    document.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('mousedown', off);
      document.removeEventListener('keydown', key);
    };
  }, [cityMenuOf]);

  /* 从别页带岗位过来（`/explore?job=`）：选中它并按它重排整列 */
  const incoming = params.get('job');
  const [landedId, setLandedId] = useState<string | null>(() => params.get('job'));
  useEffect(() => {
    if (!incoming || !base.jobs.has(incoming)) return;
    setSelJobs(new Set([incoming]));
    setAnchor(incoming);
    setPickedJob(incoming);
    setSortMode('similarity');
  }, [incoming, base.jobs]);

  /** 岗位节点，用于取名与判断是否萌芽岗位（跨页链接要落到对的子页面上） */
  const jobNodeById = useMemo(
    () => new Map(d.nodes.filter((n) => n.kind === 'job').map((n) => [n.id, n])),
    [d.nodes],
  );

  /* ==================== 岗位列 ==================== */

  const byPosts = useMemo(
    () => [...allJobIds].sort((a, b) => (base.jobs.get(b)?.posts ?? 0) - (base.jobs.get(a)?.posts ?? 0)),
    [allJobIds, base.jobs],
  );

  const ordered = useMemo(
    () => (sortMode === 'similarity' && anchor ? sortBySimilarity(byPosts, anchor) : byPosts),
    [sortMode, anchor, byPosts],
  );

  const visibleJobIds = useMemo(
    () => (jobLimit > 0 ? ordered.slice(0, jobLimit) : ordered),
    [ordered, jobLimit],
  );

  const overview = useMemo(() => buildOverview(visibleJobIds, true), [visibleJobIds]);
  /* 图上第三列画的是技能这一层。技能点行仍留着：它是技能行的构成明细，
     悬停提示与右栏的下钻读它，只是不在这一列逐行铺开。 */
  const itemById = useMemo(
    () => new Map(overview.groupRows.map((r) => [r.id, r])),
    [overview.groupRows],
  );

  /* ==================== 属性栏 ====================
     论文的属性栏只统计"当前选中的那批岗位"。一个岗位都没选时整栏是空的，
     这里改成回落到图上列出的全部岗位 —— 空白的一栏说不出任何事，
     而它本来要回答的问题（这批岗位在什么条件下被招）在没选之前同样成立。 */
  const attrScope = useMemo(
    () => (selJobs.size ? visibleJobIds.filter((id) => selJobs.has(id)) : visibleJobIds),
    [selJobs, visibleJobIds],
  );

  const attrGroups = useMemo(() => {
    const gs = buildAttrGroups(attrScope, cityPick);
    /* 省份一组三十余行，不截：赛题问的是"在哪儿招人"，截掉一半的省等于
       只答了沿海几个。这一列因此可能高过画布，由容器给一条竖向滚动条。
       其余三组仍按论文的 salary_limit 截断。 */
    return gs.map((g) => ({
      ...g,
      rows: g.kind === 'cities' ? g.rows : g.rows.slice(0, ATTR_ROW_LIMIT),
    }));
  }, [attrScope, cityPick]);

  const pickCount = countPicks(picks);

  /* 城市下拉里的条数按属性栏同一个岗位范围算，与它上一行的省份条同源。
     此前这里读的是全样本的逐城条数，而省份条只统计当前列出的那批岗位，
     于是省下面挂着比该省还大的城市数。 */
  const cityCounts = useMemo(() => cityCountsIn(attrScope), [attrScope]);
  /** 当前范围内该省有条数的城市，按条数降序 */
  const citiesOfProvince = useCallback(
    (pv: string) =>
      (CITIES_BY_PROVINCE_IN_DATA.get(pv) ?? [])
        .filter((c) => (cityCounts[c] ?? 0) >= 0.5)
        .sort((a, b) => (cityCounts[b] ?? 0) - (cityCounts[a] ?? 0)),
    [cityCounts],
  );

  /* ==================== 右栏取数 ==================== */

  /* 论文里属性栏一经点选，筛选后的岗位即刻送入岗位探索视图（"all job posts he is
     interested will be fed into the next Post Exploration View"），聚类作用于这批岗位
     的全体：既没有中间的提交动作，也不看岗位列一次列出多少个 —— "列出前 N 个"
     是这张长图的显示降级，不是口径。 */
  const attrFiltered = useMemo(() => filterByPicks(byPosts, picks), [byPosts, picks]);

  const clusterScope = submitted ?? attrFiltered;
  const clusterModel = useMemo(() => buildClusters(clusterScope), [clusterScope]);

  const activeCluster = useMemo(
    () => (openId === null ? null : (clusterModel.clusters.find((c) => c.id === openId) ?? null)),
    [openId, clusterModel],
  );

  const postMap = useMemo(
    () =>
      activeCluster
        ? buildPostCells(activeCluster.jobIds)
        : { cells: [], jobsShown: 0, hiddenJobs: 0, coverage: 0 },
    [activeCluster],
  );

  /* 簇内分布图的横轴：本簇岗位在招聘正文里提出的学历门槛。
     取全簇而不是取第一个岗位 —— 各岗位的门槛不尽相同，只按其中一个取列名时，
     其余岗位落在列名之外的格会整格消失。

     列次序按学历轴给，不按岗位数排：学历自有高低之序，按数量排会把"本科"
     排到"大专"左边，一行读下来是乱的。末档"学历不限"是一条独立读数，
     不是最低的一级，故仍留在轴末。 */
  const degreeCols = useMemo(() => {
    const has = new Set<string>();
    for (const id of clusterScope) {
      for (const k of Object.keys(base.jobs.get(id)?.attrs.degrees ?? {})) has.add(k);
    }
    return DEGREE_AXIS.filter((k) => has.has(k));
  }, [base.jobs, clusterScope]);
  const salaryBands = overview.salaryBands;

  /* 换簇时清掉上一簇的落点：详情栏里挂着别的簇的岗位，与图上说的不是一回事 */
  useEffect(() => {
    setPair({ left: null, right: null, next: 'left' });
  }, [openId]);

  /* 属性筛选一变，"只聚这几个"的子集就作废了 —— 它是在上一套口径下挑出来的 */
  useEffect(() => {
    setSubmitted(null);
    setOpenId(null);
  }, [picks]);

  /* ==================== 交互 ==================== */

  /* 论文之外多出来的一步：把聚类收到左栏已选中的那几个岗位上。
     论文里点岗位条只做高亮连线，聚类始终作用于筛选后的全体，因此这不是默认路径 ——
     按钮只在确实选中了岗位时出现，按下之后标题栏改挂"回到全部岗位"。 */
  const clusterSelected = () => {
    const ids = attrFiltered.filter((id) => selJobs.has(id));
    if (!ids.length) {
      setToast('所选岗位不在当前属性筛选的口径内');
      return;
    }
    setSubmitted(ids);
    setOpenId(null);
    setToast(`聚类已收到选中的 ${ids.length} 个岗位`);
  };

  /* 详情栏收一个落点。论文 6.3：两个岗位可同时并排比较，Case Study 里
     写明是左右键各送一列。左键在此基础上补一条轮转 —— 只知道左键的人
     连点两下也能凑齐两列，第三下顶掉先来的那一个。 */
  const pickCell = (cell: PostCell, side: 'left' | 'right') => {
    setPickedJob(cell.jobId);
    setPair((p) => {
      if (side === 'right') return { ...p, right: cell, next: 'left' };
      const k = keyOf(cell);
      /* 已经在栏里的岗位再点一次不该换位置：那只是想看清楚它，不是想换一个 */
      if (keyOf(p.left) === k || keyOf(p.right) === k) return p;
      if (!p.left) return { ...p, left: cell };
      if (!p.right) return { ...p, right: cell, next: 'left' };
      return p.next === 'left'
        ? { ...p, left: cell, next: 'right' }
        : { ...p, right: cell, next: 'left' };
    });
  };

  const clearSide = (side: 'left' | 'right') => setPair((p) => ({ ...p, [side]: null }));

  const toggleIn = <T,>(set: Set<T>, v: T) => {
    const next = new Set(set);
    if (next.has(v)) next.delete(v);
    else next.add(v);
    return next;
  };

  const togglePick = (kind: AttrKind, bucket: string) =>
    setPicks((p) => ({ ...p, [kind]: toggleIn(p[kind], bucket) }));

  const reset = () => {
    setSelSkills(new Set());
    setSelJobs(new Set());
    setPicks(emptyPicks());
    setLitDims(new Set());
    setAnchor(null);
    setPickedJob(null);
    setSortMode('quantity');
    setExpanded(new Set(allGroupIds));
  };

  const detailLeft = useMemo(
    () => (left ? postDetail(left, activeCluster?.color ?? 'var(--primary)') : null),
    [left, activeCluster],
  );
  const detailRight = useMemo(
    () => (right ? postDetail(right, activeCluster?.color ?? 'var(--primary)') : null),
    [right, activeCluster],
  );

  /** 未设条件时视同全选 */
  const cityOn = (c: string) => !cityPick || cityPick.has(c);
  const toggleCity = (c: string) =>
    setCityPick((cur) => {
      const next = new Set(cur ?? CITIES_IN_DATA);
      if (next.has(c)) next.delete(c);
      else next.add(c);
      return next.size === CITIES_IN_DATA.length ? null : next;
    });
  /** 图上的选中态：能力、岗位、能力维度三处任一有选中即为真 */
  const picked = selSkills.size > 0 || selJobs.size > 0 || litDims.size > 0;
  /** 只撤图上的选中，不动属性筛选与排序基准 —— 那两项各有自己的撤出口 */
  const clearPicked = () => {
    setSelSkills(new Set());
    setSelJobs(new Set());
    setLitDims(new Set());
    setPickedJob(null);
  };
  /** 撤掉排序基准，行序回到招聘信息条数那一档 */
  const clearAnchor = () => {
    setAnchor(null);
    setSortMode('quantity');
  };

  /* ---------------- 跨页出口 ----------------
     这一页此前没有任何跨页链接：在图上找到一个合适的岗位之后无处可去。
     出口按最后被选定的那个岗位给（见 pickedJob），一个都没选过时给通用入口。

     同一份数组供两处使用：页尾的"下一步"写全每条回答什么，总览图上贴着该岗位
     那一行的浮窗（JumpDock）只取 short 与图标。选定岗位的三条各带一个 short，
     通用入口不带，浮窗因此只在选定了岗位时出现，也不必另设开关。 */
  const exitJob = useMemo(() => {
    const id = pickedJob ?? left?.jobId ?? right?.jobId ?? anchor ?? null;
    return id ? (jobNodeById.get(id) ?? null) : null;
  }, [pickedJob, left, right, anchor, jobNodeById]);

  const exits = useMemo<StepItem[]>(() => {
    if (!exitJob) {
      return [
        {
          to: '/match',
          label: '运行一次匹配诊断',
          desc: '目标岗位可在报告内随时切换，三份示例简历亦可直接选用。',
          icon: 'target',
          primary: true,
        },
        {
          to: '/jobs?tab=existing',
          label: '查看既有岗位本期变动',
          desc: '逐岗位的能力年轮与变更清单，每条变动标注增、删、改与数据来源。',
          icon: 'cap',
        },
        {
          to: '/panorama',
          label: '回到领域全貌',
          desc: '当前的能力要求分布，及其中仅由前瞻信号支持的部分。',
          icon: 'graph',
        },
      ];
    }
    const q = encodeURIComponent(exitJob.id);
    return [
      {
        to: `/match?target=${q}`,
        label: `以“${exitJob.name}”运行匹配诊断`,
        desc: '五维评价体系、能力差距明细与分阶段学习路径，逐项可回到简历原文核对。',
        icon: 'target',
        primary: true,
        short: '人岗匹配',
      },
      {
        to: `/jobs?tab=${exitJob.emerging ? 'new' : 'existing'}&id=${q}`,
        label: '查看该岗位的能力变动与来源',
        desc: exitJob.emerging
          ? '该岗位的定义五要素、涌现相图位置，及支撑定义的三类原文。'
          : '该岗位本期的能力年轮与逐条变更清单，及支撑变动的三类原文。',
        icon: 'cap',
        short: '岗位洞察',
      },
      {
        to: `/panorama?focus=${q}`,
        label: '在全景图谱中定位该岗位',
        desc: '该岗位由哪些核心任务构成，这些任务又要求哪些能力。',
        icon: 'graph',
        short: '全景图谱',
      },
    ];
  }, [exitJob]);

  return (
    <div className="explore">
      <div className="ex-wrap">
        <PageGuide
          station={stationOf('/explore')!}
          landed={landedId ? (jobNodeById.get(landedId)?.name ?? null) : null}
          onClearLanded={() => {
            setLandedId(null);
            reset();
          }}
        />

        {/* ============ 口径摘要 ============ */}
        <div className="ex-meta">
          <span>
            <Icon name="db" size={15} />
            {visibleJobIds.length} 个岗位已列出
            <em>体系内共 {allJobIds.length} 个</em>
          </span>
          <span>
            <Icon name="layers" size={15} />
            {base.axes.length} 项技能 · 已列出岗位涉及{' '}
            {overview.itemRows.length.toLocaleString()} 个技能点
          </span>
          <span>
            <Icon name="graph" size={15} />
            聚成 {clusterModel.clusters.length} 簇
            <em>
              作用于 {clusterScope.length} 个岗位 · 迭代 {clusterModel.iterations} 轮收敛 ·{' '}
              {clusterModel.ms < 1 ? '<1' : Math.round(clusterModel.ms)} ms
            </em>
          </span>
          <span>
            <Icon name="doc" size={15} />
            {Math.round(overview.totalPosts).toLocaleString()} 条招聘信息
          </span>
          {/* 此处原挂一枚"清空选择"。这一行是自动换行的弹性行，宽度已被四段
              口径读数占满，按钮一出现整行折成两行，下面的图跟着往下跳一档。

              三件可撤销的事现各有各的就近入口：图上的选中与排序基准在图的
              标题栏右侧，属性筛选在筛选行末尾的"全部撤出"。一个统管三者的
              总闸因此不再需要。 */}
        </div>

        <div className="ex-grid">
          {/* ==================== 左：能力—岗位总览 ==================== */}
          <Panel
            title="能力—岗位总览"
            className="ex-panel ex-a"
            actions={
              <div className="pn-act">
                {/* 排序依据与两个撤出口一并收在标题行：它们管的都是这张图，
                    与标题同行时不必在图与控件之间来回换行读。

                    次序：会出没的三样（两个撤出口、基准名）一律排在左边，右端留给
                    恒在的排序分段器与口径标。这一行是右对齐的，按钮若排在右边，
                    一出现就把分段器整体往左推一截 —— 而那个分段器是要去点的目标。 */}
                {picked && (
                  <button className="btn sm" onClick={clearPicked}>
                    <Icon name="close" size={13} />
                    清除选中
                  </button>
                )}
                {anchor && (
                  <button className="btn sm" onClick={clearAnchor}>
                    <Icon name="close" size={13} />
                    取消基准
                  </button>
                )}
                {sortMode === 'similarity' && anchor && (
                  <span className="ex-anchor">基准：{base.jobs.get(anchor)?.name}</span>
                )}
                <span className="ex-ctl-lb">岗位排序依据</span>
                <div className="seg">
                  <button
                    className={sortMode === 'quantity' ? 'on' : ''}
                    onClick={() => {
                      setSortMode('quantity');
                      setAnchor(null);
                    }}
                  >
                    招聘信息条数
                  </button>
                  <button
                    className={sortMode === 'similarity' ? 'on' : ''}
                    disabled={!anchor}
                    title={anchor ? undefined : '先在岗位条上右击，指定一个基准岗位'}
                    onClick={() => anchor && setSortMode('similarity')}
                  >
                    能力构成相似度
                  </button>
                </div>
                {/* 本图各段现已全部有实测来源，故不再挂演示数据标；
                    两处需推导一层的（学历、要求程度）在口径里逐条写明 */}
              </div>
            }
            bodyStyle={{ padding: '0 0 12px' }}
          >
            {/* ---- 已选分档 ----
                 空着时也占这一行：有没有挂着筛选条件是读这张图的前提，
                 而一行时有时无，读者须先判断它是空的还是没画。 */}
            <div className="ex-picks">
              <span className="ex-ctl-lb">属性筛选</span>
              {pickCount === 0 ? (
                <span className="ex-picks-empty">
                  暂无
                </span>
              ) : (
                <>
                  {ATTR_KINDS.filter((a) => picks[a.v].size > 0).map((a) => (
                    <span key={a.v} className="ex-pick-grp">
                      <em>{a.label}</em>
                      {[...picks[a.v]].map((b) => (
                        <button
                          key={b}
                          className="ex-pick"
                          title={`撤出「${a.label} · ${b}」`}
                          onClick={() => togglePick(a.v, b)}
                        >
                          {b}
                          <i>×</i>
                        </button>
                      ))}
                    </span>
                  ))}
                  <span className="ex-picks-n">筛出 {attrFiltered.length} 个岗位</span>
                  <button className="ex-more" onClick={() => setPicks(emptyPicks())}>
                    全部撤出
                  </button>
                </>
              )}
            </div>

            <div className="ex-scroll" ref={flowBox.ref}>
              <SkillJobOverview
                width={flowBox.w || 1000}
                tree={tree}
                expanded={expanded}
                litDims={litDims}
                itemById={itemById}
                jobs={overview.jobRows}
                attrGroups={attrGroups}
                salaryBands={salaryBands}
                selSkills={selSkills}
                selJobs={selJobs}
                picks={picks}
                onToggleDim={(name) => setLitDims((s) => toggleIn(s, name))}
                onToggleGroup={(id) => setExpanded((s) => toggleIn(s, id))}
                onToggleSkill={(id) => setSelSkills((s) => toggleIn(s, id))}
                onToggleJob={(id) => {
                  const next = toggleIn(selJobs, id);
                  setSelJobs(next);
                  /* 撤掉的正是出口指着的那一个时，退回仍选着的里面最后加进来的那个 */
                  const rest = [...next];
                  setPickedJob(next.has(id) ? id : (rest[rest.length - 1] ?? null));
                }}
                onSortBy={(id) => {
                  setAnchor(id);
                  setPickedJob(id);
                  setSortMode('similarity');
                  setToast(`已按与“${base.jobs.get(id)?.name}”的能力构成相似度重排`);
                }}
                onTogglePick={togglePick}
                onTip={(e, content) =>
                  setTip(e && content ? { x: e.clientX, y: e.clientY, content } : null)
                }
                jumpId={exitJob?.id ?? null}
                jumpDock={<JumpDock items={exits} label="把选定的岗位带往其他页面" />}
                cityMenuOf={cityMenuOf}
                onOpenCityMenu={setCityMenuOf}
                cityMenu={
                  cityMenuOf && (
                    <div className="ex-citypop" onMouseDown={(e) => e.stopPropagation()}>
                      <p className="ex-citypop-hd">
                        {cityMenuOf}
                        <em>{citiesOfProvince(cityMenuOf).length} 座城市有招聘信息</em>
                      </p>
                      <div className="ex-citypop-list">
                        {citiesOfProvince(cityMenuOf).map((ci) => (
                          <label key={ci} className={cityOn(ci) ? 'on' : ''}>
                            <input type="checkbox" checked={cityOn(ci)} onChange={() => toggleCity(ci)} />
                            {ci}
                            <em>{Math.round(cityCounts[ci] ?? 0).toLocaleString()}</em>
                          </label>
                        ))}
                        {citiesOfProvince(cityMenuOf).length === 0 && (
                          <p className="ex-dd-none">当前岗位范围内该省暂无招聘信息。</p>
                        )}
                      </div>
                      <div className="ex-citypop-act">
                        <button
                          className="btn sm"
                          onClick={() => setCityPick((cur) => {
                            const next = new Set(cur ?? CITIES_IN_DATA);
                            for (const ci of citiesOfProvince(cityMenuOf)) next.add(ci);
                            return next.size === CITIES_IN_DATA.length ? null : next;
                          })}
                        >
                          全选本省
                        </button>
                        <button
                          className="btn sm"
                          onClick={() => setCityPick((cur) => {
                            const next = new Set(cur ?? CITIES_IN_DATA);
                            for (const ci of citiesOfProvince(cityMenuOf)) next.delete(ci);
                            return next;
                          })}
                        >
                          清空本省
                        </button>
                        <button className="btn sm" onClick={() => setCityMenuOf(null)}>
                          收起
                        </button>
                      </div>
                    </div>
                  )
                }
              />
            </div>

            <div className="ex-legend">
              <span className="ex-lg-grp">
                要求程度
                {PROF_LEVELS.map((p, i) => (
                  <em key={p}>
                    {/* 第四档在图上是点阵，图注照同一种画法 */}
                    <i
                      className={i === PROF_UNKNOWN ? 'sw-unknown' : undefined}
                      style={i === PROF_UNKNOWN ? undefined : { background: PROF_COLORS[i] }}
                    />
                    {p}
                  </em>
                ))}
              </span>
              <span className="ex-lg-grp">
                软硬构成
                {SKILL_TYPES.map((t) => (
                  <em key={t.v}>
                    <i style={{ background: MIX_COLORS[t.v] }} />
                    {t.label}
                  </em>
                ))}
              </span>
              <span className="ex-lg-grp">
                薪资档
                {salaryBands.map((b, i) => (
                  <em key={b}>
                    <i style={{ background: SALARY_COLORS[i] }} />
                    {b}
                  </em>
                ))}
              </span>
              {/* 截断如实写出：中段只画得下四十行，读者看到的不是全部岗位。
                  与能力演变时间线的图注同一种写法 —— 画了一部分而不说明总数，
                  画出来的就会被当成全部。 */}
              <span className="ex-lg-cap">
                已进入招聘市场的岗位共 {ordered.length} 个，
                {sortMode === 'similarity' && anchor
                  ? `按与“${base.jobs.get(anchor)?.name ?? ''}”的能力构成相似度`
                  : '按招聘信息条数'}
                由高到低列出前 {overview.jobRows.length} 个；聚类与属性栏不受此限，仍按全部岗位统计
              </span>
            </div>

          </Panel>

          {/* ==================== 右 ==================== */}
          <div className="ex-side">
            {/* ---- 岗位探索 ---- */}
            <Panel
              title={activeCluster ? `簇内岗位分布 · 以${activeCluster.label}为代表` : '岗位聚类'}
              className="ex-panel ex-b"
              actions={
                <div className="pn-act">
                  {activeCluster ? (
                    <button className="btn sm" onClick={() => setOpenId(null)}>
                      ← 回到全部聚类
                    </button>
                  ) : submitted ? (
                    <button className="btn sm" onClick={() => setSubmitted(null)}>
                      ← 回到全部岗位
                    </button>
                  ) : (
                    selJobs.size > 0 && (
                      <button className="btn sm primary" onClick={clusterSelected}>
                        <Icon name="refresh" size={13} /> 聚类选中的 {selJobs.size} 个
                      </button>
                    )
                  )}
                </div>
              }
              bodyStyle={{ padding: '10px 12px 12px' }}
            >
              <div className="ex-scroll" ref={mapBox.ref}>
                <PostExplorationView
                  width={mapBox.w || 660}
                  height={640}
                  model={clusterModel}
                  axes={base.groupAxes}
                  open={activeCluster}
                  cells={postMap.cells}
                  columns={degreeCols}
                  bands={salaryBands}
                  leftKey={keyOf(left)}
                  rightKey={keyOf(right)}
                  onOpenCluster={setOpenId}
                  onPick={pickCell}
                  onTip={(e, content) => setTip(e && content ? { x: e.clientX, y: e.clientY, content } : null)}
                />
              </div>

              {/* 图下原有一段图注，把落点数、每岗绘制格数、覆盖率与独立性假设一并写出。
                  这些是读图的前提，属于问号里的内容，写在图下只是把面板顶高，
                  且与图本身争夺视线。已并入本屏的问号。 */}
            </Panel>

            {/* ---- 岗位详情 ---- */}
            <Panel
              title="岗位详情"
              className="ex-panel ex-c"
              actions={
                <div className="pn-act">
                  <span className="ex-cmp-n">{(left ? 1 : 0) + (right ? 1 : 0)} / 2</span>
                  {(left || right) && (
                    <button
                      className="btn sm"
                      onClick={() => setPair({ left: null, right: null, next: 'left' })}
                    >
                      清空
                    </button>
                  )}
                </div>
              }
              bodyStyle={{ padding: '12px 14px 14px' }}
            >
              <PostDetailView
                left={detailLeft}
                right={detailRight}
                domain={base.groupMaxShare}
                stage={activeCluster ? 'map' : 'cluster'}
                onClear={clearSide}
              />

            </Panel>
          </div>
        </div>

        <NextSteps from="/explore" items={exits} />
      </div>

      {toast && <div className="ex-toast">{toast}</div>}
      <Tooltip tip={tip} />
      <Footer />
    </div>
  );
}
