/* ============================================================
   Skill-job Overview —— JobViz 论文 Figure 2(A) 的复刻

   三段并排，用曲线连起来：

     A1  能力体系（维度 → 能力组 → 技能）+ 需求条
         树的两级用横向贝塞尔连线；点一个能力组展开 / 收起它的技能，
         展开的技能右侧才出现需求条。

         末一级画到技能而非技能点：技能点是随市场文本生长的开放集合，
         本批逾两万项，在这一列铺开时行高压到一像素以下，条长之间读不出长短；
         技能是封闭体系，五十余项恰好排满这一列。技能点的明细在悬停提示里给。
         条长 = 市场需求量，条内三段 = 要求强度（了解 / 熟练 / 精通）。

     A2  岗位条
         条长 = 该岗位的招聘信息条数，条内两段 = 硬技能 / 软技能占比。

     A3  属性分布
         省份 / 学历 / 经验三组一次画完，
         条长 = 落在该分档的招聘信息条数，条内六段 = 薪资档。

   四类交互与论文一致：
     · 点一级维度名  → 它到能力组的连线点亮
     · 点能力组名    → 展开 / 收起组内技能
     · 点需求条      → 该项技能累加进选中集，连到要求它的岗位（权重 ≥ 该项最高值的一半）
     · 点岗位条      → 该岗位累加进选中集，连到它要求的、当前可见的能力
     · 右键岗位条    → 整列按与它的能力构成相似度重排

   与论文实现的两处出入，都写在这里而不是藏在代码里：

     ① 论文的岗位条点击只连到"已经选中的"能力（tools_for_d3 里那一句
        link1.skill[...] === 0 的守卫）—— 那是为了避开未展开的组没有条可连、
        querySelector 取到 null。这里按论文正文写的对称语义实现：连到它要求的、
        当前画得出来的能力，收起的组不连。

     ② 论文里岗位与属性两段之间没有连线（源码里那段是注释掉的）。这里同样不画：
        A3 本来就只统计选中的那批岗位，再画一次连线是重复说同一件事。

     ③ 论文的属性栏一次只显示四维中的一维，由一组单选按钮换维。这里四维同屏：
        四维合计 25 个分档，按论文 27px 的行距占 675px，本就落在这一列的可用
        高度内；一次只画一维，既使这一列空出大半，也使"北京 + 本科"这类
        跨维的条件无从表达。四维的分档数不同，故标尺按组定而非四组共用。
   ============================================================ */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { AttrGroup, AttrPicks, AttrKind, JobRow, SkillRow } from '@/data/explore';
import { MIX_COLORS, PROF_COLORS, PROF_LEVELS, PROF_UNKNOWN, SALARY_COLORS, SKILL_TYPES } from '@/data/explore';
import type { TreeDim } from '@/data/jobviz';
import { fitText, measureText } from '@/utils/viz';

/* ---------------- 版面 ----------------
   列宽按论文 1200px 画布的比例折算到中文名的实际字宽：
   论文的技能名是缩写过的拉丁短词（"Sys. ARCH&Infrastructure"），
   本系统最长的技能点名 18 个汉字、岗位名 27 个汉字，两边都要更宽的文字列。 */

const PAD_T = 34;
const PAD_B = 16;
/** 画布高度。论文是 950，这里留一点给列头 */
const CANVAS_H = 980;

/* 各列此前合计 984px。左栏在 1440 宽的屏上只分到 730 余像素，
   于是这张图恒定横向溢出，栏内始终挂着一条横滚条 —— 而三段并排要的正是
   一眼看全，横滚使它退化成一张要左右拖动的长图。
   现按“文字列取实际最长名所需、条列取最小可辨长度”重定，合计 810px：
   文字列的字号由 fitText 在 12px 与 9.5px 之间按列宽自适应，收窄一档不改变
   可读性；三根条列在宽度有余时按 (W − 810)/3 逐列摊回，宽屏上仍与此前等长。 */
const X_L1 = 6;
/* 维度名最长六字（“基础通用技能”），12px 下占 72px，另需留出 W_FAN 给连线。
   此前定 72，六字被压成“基础通用…”—— 这一列只有两三个名字，缩写省下的
   十余像素换来的是一个读不全的标签。 */
const W_L1 = 88;
const X_L2 = X_L1 + W_L1;
const W_L2 = 100;
const X_L3 = X_L2 + W_L2;
const W_L3 = 118;
/* 名字排到列宽的最后一格，连线就没有横向行程可走，一把扇形会退化成一条竖线。
   两级树的文字各自留出这么多给连线。 */
const W_FAN = 22;
const W_BAR = 96;
const GAP_LINK = 28;
const W_JOBBAR = 92;
const W_JOBNAME = 104;
const GAP_ATTR = 22;
const W_ATTRBAR = 86;
const W_ATTRNAME = 74;
/** 组内滚动条的宽度与其距条名的间距 */
const SB_W = 6;
const SB_GAP = 6;

/* 末尾留出组内滚动条的位置：省份一组的滚动条画在条名右侧，
   不留这一段它会落在画布之外，滚得动却看不见。 */
const MIN_W =
  X_L3 +
  W_L3 +
  W_BAR +
  GAP_LINK +
  W_JOBBAR +
  6 +
  W_JOBNAME +
  GAP_ATTR +
  W_ATTRBAR +
  6 +
  W_ATTRNAME +
  SB_GAP +
  SB_W;

/** 属性行的固定行距，论文取 27 */
/** 选中行浮窗的整体高度，只用来判断该行上方还排不排得下（见 jumpAt） */
const JUMP_H = 34;
/** 浮窗与该行之间留的空档，尾巴的高度落在这一段里。
    岗位段行距只有二十余像素，空档按行距收窄，压到的相邻行少一点 */
const JUMP_GAP = 9;

const ATTR_PITCH = 27;
/** 属性分组标题占的高 */
const ATTR_HEAD = 26;
/** 组与组之间的间隔。四组排完仍有余高时由它吸收，上限之外的余高留在列尾 */
const ATTR_GAP_MIN = 10;
const ATTR_GAP_MAX = 40;
/** 省份一组在视窗内一次显示多少行的上限。其余各行由组内滚动带出来 */
const PROV_VIEW_ROWS = 14;
/** 省份一组的视窗行数下限。再少下去，组内的高低差就只剩两三行可比 */
const PROV_MIN_ROWS = 8;

interface Cols {
  w: number;
  barMax: number;
  barEnd: number;
  jobX: number;
  jobW: number;
  jobName: number;
  attrX: number;
  attrW: number;
  attrName: number;
}

function layout(w: number): Cols {
  const W = Math.max(w, MIN_W);
  /* 多出来的宽度全给三根条：文字列的宽度由最长的名字定死，拉宽只会多留白 */
  const g = (W - MIN_W) / 3;
  const barMax = W_BAR + g;
  const barEnd = X_L3 + W_L3 + barMax;
  const jobX = barEnd + GAP_LINK;
  const jobW = W_JOBBAR + g;
  const jobName = jobX + jobW + 6;
  const attrX = jobName + W_JOBNAME + GAP_ATTR;
  const attrW = W_ATTRBAR + g;
  const attrName = attrX + attrW + 6;
  return { w: W, barMax, barEnd, jobX, jobW, jobName, attrX, attrW, attrName };
}

/** 组头上的满格值。这一列的条数在十万量级，写全了占掉半个组头 */
const fullScale = (v: number) =>
  v >= 10000 ? `${(v / 10000).toFixed(1)} 万条` : `${Math.round(v).toLocaleString()} 条`;

/** d3.linkHorizontal 的等价写法：控制点落在两端 x 的中点上 */
const curve = (x0: number, y0: number, x1: number, y1: number) => {
  const mx = (x0 + x1) / 2;
  return `M${x0.toFixed(1)},${y0.toFixed(1)} C${mx.toFixed(1)},${y0.toFixed(1)} ${mx.toFixed(1)},${y1.toFixed(1)} ${x1.toFixed(1)},${y1.toFixed(1)}`;
};

export interface OverviewProps {
  width: number;
  tree: TreeDim[];
  /** 已展开的能力组 id */
  expanded: Set<string>;
  /** 连线被点亮的能力维度名 */
  litDims: Set<string>;
  /** 技能点行，按 id 取 */
  itemById: Map<string, SkillRow>;
  jobs: (JobRow & { share: number })[];
  /** 省份 / 学历 / 经验三组，顺序即画的顺序 */
  attrGroups: AttrGroup[];
  salaryBands: string[];
  selSkills: Set<string>;
  selJobs: Set<string>;
  picks: AttrPicks;
  onToggleDim: (name: string) => void;
  onToggleGroup: (id: string) => void;
  onToggleSkill: (id: string) => void;
  onToggleJob: (id: string) => void;
  onSortBy: (jobId: string) => void;
  onTogglePick: (kind: AttrKind, bucket: string) => void;
  /** 收起提示时传 (null, null)：那一步没有指针位置，也不需要 */
  onTip: (e: React.MouseEvent | null, content: React.ReactNode | null) => void;
  /**
   * 选中一个岗位时贴着那一行浮出的一小块内容，用于跨页出口（见 JumpDock）。
   * 图只负责落位，内容与去向由页面给：这一层不认识路由。
   */
  jumpDock?: React.ReactNode;
  /** 浮窗贴哪一行。取本页当前选定的岗位，不在图上时不出现 */
  jumpId?: string | null;
  /**
   * 省份行右侧的下拉：单击即在该省下逐座勾选城市。
   *
   * 图只负责报"点了哪个省"并算出锚点落位，菜单本身由页面渲染并经
   * cityMenu 传回来贴上去 —— 勾选状态与筛选逻辑都在页面那一层，
   * 这一层不认识城市。
   */
  cityMenuOf?: string | null;
  onOpenCityMenu?: (bucket: string | null) => void;
  cityMenu?: React.ReactNode;
}

export function SkillJobOverview({
  width,
  tree,
  expanded,
  litDims,
  itemById,
  jobs,
  attrGroups,
  salaryBands,
  selSkills,
  selJobs,
  picks,
  onToggleDim,
  onToggleGroup,
  onToggleSkill,
  onToggleJob,
  onSortBy,
  onTogglePick,
  onTip,
  jumpDock,
  jumpId,
  cityMenuOf,
  onOpenCityMenu,
  cityMenu,
}: OverviewProps) {
  const c = useMemo(() => layout(width), [width]);

  const usable = CANVAS_H - PAD_T - PAD_B;

  /* ---- 组内滚动 ----
     省份一组的行数超出视窗，滚动偏移按组记。滚轮在组内滚动，
     拖拽滚动条同样落到这份状态上，两条路径改同一个数。 */
  const [attrScroll, setAttrScroll] = useState<Partial<Record<AttrKind, number>>>({});
  const scrollOf = (k: AttrKind) => attrScroll[k] ?? 0;

  /* 滚轮要吃掉，不能让它同时把页面也滚一截。React 的 onWheel 挂在根节点上、
     且是被动监听，其中的 preventDefault 不生效，故改为在 svg 上挂一个
     非被动的原生监听，按指针落点判定是否落在某个可滚动组的视窗内。
     视窗矩形随渲染写进 ref —— 监听只装一次，读的却要是当前这一帧的版面。 */
  const svgRef = useRef<SVGSVGElement | null>(null);
  /** 城市下拉的锚点。由图算出，页面只管填内容 */
  const [cityMenuAt, setCityMenuAt] = useState<{ x: number; y: number } | null>(null);
  /** 指针停在属性栏的哪一行。下拉钮只在这一行与已展开的那一行出现 */
  const [hoverAttr, setHoverAttr] = useState<string | null>(null);
  const zones = useRef<{ kind: AttrKind; x0: number; x1: number; y0: number; y1: number; max: number }[]>([]);

  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      const r = el.getBoundingClientRect();
      const x = e.clientX - r.left;
      const y = e.clientY - r.top;
      const z = zones.current.find((v) => x >= v.x0 && x <= v.x1 && y >= v.y0 && y <= v.y1);
      if (!z) return;
      const cur = scrollRef.current[z.kind] ?? 0;
      const next = Math.max(0, Math.min(z.max, cur + e.deltaY));
      /* 已经滚到头时不再吃掉事件，页面照常滚 —— 否则指针停在这一小块上
         整页就再也滚不动了 */
      if (next === cur) return;
      e.preventDefault();
      setAttrScroll((s) => ({ ...s, [z.kind]: next }));
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, []);

  /* 城市浮层内的滚轮只滚浮层自己的那张城市表。浮层是 HTML 层，落在上面这条
     挂在 svg 上的监听之外；而一省的城市短于一屏时它并不是滚动容器，滚轮因而
     冒泡到页面 —— 浮层按图上的坐标定位，页面一滚它随图移出视野，读者看到的
     是"在下拉里滚，整页在动"。故在浮层这一层一律吃掉滚轮：够长时滚它自己，
     不够长或已到端时也不放行，浮层开着期间页面不动。 */
  const popRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = popRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const list = el.querySelector<HTMLElement>('.ex-citypop-list');
      if (!list) return;
      const room = list.scrollHeight - list.clientHeight;
      if (room <= 0) return;
      list.scrollTop = Math.max(0, Math.min(room, list.scrollTop + e.deltaY));
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [cityMenuOf, cityMenuAt]);

  /** 监听是一次性装的，闭包里读不到最新的 state，故另存一份 */
  const scrollRef = useRef(attrScroll);
  scrollRef.current = attrScroll;

  /** 拖动滚动条时按住的那一组。指针移动与抬起挂在 window 上，
      指针滑出图外仍跟得住 */
  const drag = useRef<{ kind: AttrKind; max: number; viewH: number; y0: number } | null>(null);
  const startDrag = useCallback(
    (e: React.PointerEvent, kind: AttrKind, max: number, viewH: number, rows: number) => {
      e.preventDefault();
      const total = rows * ATTR_PITCH;
      drag.current = { kind, max, viewH, y0: e.clientY };
      const start = scrollOf(kind);
      const move = (ev: PointerEvent) => {
        const d = drag.current;
        if (!d) return;
        /* 滑块走一像素，内容走 总高/视窗高 像素 */
        const next = start + ((ev.clientY - d.y0) * total) / viewH;
        setAttrScroll((cur) => ({ ...cur, [kind]: Math.max(0, Math.min(max, next)) }));
      };
      const up = () => {
        drag.current = null;
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [attrScroll],
  );

  /* ---- A1：树的三层落位 ----
     能力组按论文的做法在整幅高度上等距排开（不随展开与否移动），技能点另起一套
     等距行；连线因此张成一把扇形 —— 这把扇子正是论文那张图最先认出来的特征。 */
  const groups = useMemo(() => tree.flatMap((d) => d.groups.map((g) => ({ ...g, dim: d.name }))), [tree]);
  const gPitch = usable / Math.max(groups.length, 1);
  const yOfGroup = (i: number) => PAD_T + gPitch * (i + 0.5);

  const visibleItems = useMemo(() => {
    const out: { row: SkillRow; group: string; dim: string }[] = [];
    for (const d of tree) {
      for (const g of d.groups) {
        if (!expanded.has(g.id)) continue;
        for (const iid of g.items) {
          const row = itemById.get(iid);
          if (row) out.push({ row, group: g.id, dim: d.name });
        }
      }
    }
    return out;
  }, [tree, expanded, itemById]);

  const iPitch = Math.min(30, usable / Math.max(visibleItems.length, 1));
  const yOfItem = (i: number) => PAD_T + iPitch * (i + 0.5);
  const itemIndex = useMemo(
    () => new Map(visibleItems.map((v, i) => [v.row.id, i])),
    [visibleItems],
  );

  const dimSpans = useMemo(() => {
    const out: { name: string; a: number; b: number }[] = [];
    groups.forEach((g, i) => {
      const last = out[out.length - 1];
      if (last && last.name === g.dim) last.b = i;
      else out.push({ name: g.dim, a: i, b: i });
    });
    return out;
  }, [groups]);

  /* ---- A2 落位 ---- */
  const jPitch = Math.max(usable / Math.max(jobs.length, 1), 9);
  const yOfJob = (i: number) => PAD_T + jPitch * (i + 0.5);
  const jobIndex = useMemo(() => new Map(jobs.map((j, i) => [j.id, i])), [jobs]);

  /* ---- A3 落位 ----
     四组自上而下顺排：每组一个标题，组内按固定行距列出分档。
     四组排完剩下的高度摊给组间距，摊到上限为止；余高更多时留在列尾 ——
     组间距超过这个上限，同一组内的行反而不再像一组。 */
  const attrLay = useMemo(() => {
    /* 省份一组列出全部 34 个省级行政区，四组合计高过画布定高。整张图跟着长高
       会把左两列一并拉稀，故只给这一组开一个定高的视窗，组内自行滚动，
       其余三组与画布高度照旧。

       视窗行数由余高反推，不取定值：取定值时四组排完常比另两列高出四五十像素，
       三列的底边因而对不齐 —— 而这三列读的是同一批岗位，底边错开会让人以为
       右侧这一列另有内容。先按最小组间距算出留给这一组的高度预算，行数据此
       定下，钳在上下限之内；预算宽裕时它也随之多列几行，把余高用掉。 */
    const scrollable = (g: AttrGroup) => g.kind === 'cities';
    const gapsN = Math.max(attrGroups.length - 1, 1);
    const fixedCore = attrGroups
      .filter((g) => !scrollable(g))
      .reduce((s, g) => s + ATTR_HEAD + g.rows.length * ATTR_PITCH, 0);
    const scrollHeads = attrGroups.filter(scrollable).length * ATTR_HEAD;
    const budget = usable - gapsN * ATTR_GAP_MIN - fixedCore - scrollHeads;
    const scrollRows = Math.max(
      PROV_MIN_ROWS,
      Math.min(PROV_VIEW_ROWS, Math.floor(budget / ATTR_PITCH)),
    );
    const viewRows = (g: AttrGroup) =>
      scrollable(g) ? Math.min(g.rows.length, scrollRows) : g.rows.length;
    const core = attrGroups.reduce((s, g) => s + ATTR_HEAD + viewRows(g) * ATTR_PITCH, 0);
    const slack = usable - core;
    const gaps = Math.max(attrGroups.length - 1, 1);
    const gap = Math.min(ATTR_GAP_MAX, Math.max(ATTR_GAP_MIN, slack / gaps));
    let y = PAD_T;
    const out = attrGroups.map((g) => {
      const headY = y + ATTR_HEAD - 9;
      const y0 = y + ATTR_HEAD;
      /** 视窗高度。滚动组按视窗算，其余组即全部行的高度 */
      const viewH = viewRows(g) * ATTR_PITCH;
      y = y0 + viewH + gap;
      /* 标尺按组定，不是四组共用一把。
         四组切的虽是同一批招聘信息，分档数却不同：城市 10 档、学历 4 档，
         "本科"那一档本就该比任何单个城市长几倍。共用标尺时城市那十行的条长
         全部落在满格的三成以内，组内的高低差随之读不出 —— 而组内比较正是
         这一列要回答的问题，"北京与本科哪个多"则没有对应的问题。
         代价是等长的两根条在不同组里代表不同的量，故满格值写在组头上，
         逐行的绝对条数另写在行名下方。 */
      const max = Math.max(...g.rows.map((r) => r.posts), 1e-9);
      /** 组内可滚动的行数超出视窗多少像素。为零即不滚 */
      const overflow = Math.max(0, g.rows.length * ATTR_PITCH - viewH);
      return { ...g, headY, y0, max, viewH, overflow };
    });
    return { groups: out, total: y - gap - PAD_T };
  }, [attrGroups, usable]);

  /* 可滚动组的视窗矩形。滚轮监听按指针落点在这里找组 */
  useEffect(() => {
    zones.current = attrLay.groups
      .filter((g) => g.overflow > 0)
      .map((g) => ({
        kind: g.kind,
        x0: c.attrX - 6,
        x1: c.attrName + W_ATTRNAME + SB_GAP + SB_W,
        y0: g.y0,
        y1: g.y0 + g.viewH,
        max: g.overflow,
      }));
  }, [attrLay, c]);

  const height = Math.max(
    CANVAS_H,
    PAD_T +
    PAD_B +
    Math.max(visibleItems.length * iPitch, jobs.length * jPitch, attrLay.total),
  );

  const barH = Math.max(6, Math.min(12, iPitch - 7));
  const jobH = Math.max(5, Math.min(10, jPitch - 3));

  /* ---------------- 选中行浮窗落在哪 ----------------
     只有岗位段给浮窗：跨页出口一律带着一个岗位过去，技能点与属性分档没有对应的落点。

     横向锚在岗位条那一列的右缘，不到岗位名列上去。岗位段行距二十余像素，
     浮窗高三十像素，落在哪一侧都会盖住相邻的一行；收在条列之内，盖住的便只有
     那一行的条与左边的连线束，岗位名整列不受影响 —— 一行的条读不全还认得出是
     哪一行，名字盖掉就认不出了。这条竖线对每一行都一样，浮窗因此不随条长左右
     跳动；整幅图九百余像素而面板常宽不到这个数，故再按容器右缘收一档，
     先保证浮窗整块可见。

     纵向默认落在该行上方：往下压的是紧接着要读的一行，往上压的是已经读过的一行。
     取上方，与全景图谱同一取向。上方排不下时（头两行会顶到列头）翻到下方。 */
  const jumpAt = (() => {
    if (!jumpDock || !jumpId) return null;
    const i = jobIndex.get(jumpId);
    if (i === undefined) return null;
    const y = yOfJob(i);
    const x = Math.min(c.jobX + c.jobW, Math.max(width - 6, c.jobX + 40));
    return { x, y, below: y - jobH / 2 - JUMP_GAP - JUMP_H < PAD_T + 4 };
  })();

  /* ---- 条长的标尺 ----
     需求量在五十余项技能上跨了一个半数量级：最高的一项是最低的四十多倍，
     按线性排，除了头部那几条，其余全是一像素的碴儿 —— 论文对这一列同样不用线性，
     用的是三段折线式的压缩标尺（get_skill_bar_length）。这里取平方根：
     一个函数说得清、图例上写得下，压缩力度也够（1/40 的量仍占满长的六分之一）。
     岗位与属性两列的量只跨十几倍，线性就够，不再另加一层换算。 */
  const skillLen = (share: number) => Math.sqrt(Math.max(share, 0));
  const maxSkill = Math.max(...visibleItems.map((v) => skillLen(v.row.share)), 1e-9);
  const maxJob = Math.max(...jobs.map((j) => j.posts), 1e-9);

  /* ---- 能力 ↔ 岗位 连线 ----

     一条边成立与否是一件事实，不该因为从哪一头看而变。此前两个方向各用一条
     判据：由能力出发取 ≥ 该能力行内峰值的一半，由岗位出发取 ≥ 该岗位列内峰值的
     十分之一。阈值差着五倍，于是同一条边在一个方向上过线、在另一个方向上不过 ——
     实测「机器学习与深度学习 ↔ 数据开发工程师」这条边权重 0.51，行内比 0.40、
     列内比 0.45，选中岗位时点亮，选中该能力时不亮，看上去像是两侧数据对不上。

     现改为两侧共用一条判据，对称因而是结构上的：一条边要同时在它所在的行与列上
     都够得上份量才算成立 —— 这个岗位确是要这项能力的主力岗位之一，且这项能力
     确是这个岗位的主要要求之一。取「与」而非「或」还顺带收住了岗位一侧：
     旧的十分之一太松，选中一个岗位会点亮它五十二项能力里的四十六项，
     等于没有区分；现在中位数是十六项。

     两个峰值仍各按自己那一侧取：行峰是该项能力在各岗位间的最高覆盖率，
     列峰是该岗位在当前列出的各项能力上的最高覆盖率。覆盖率的绝对水平在能力之间
     相差一个数量级，不先归一，某一侧会恒真。 */
  const LINK_ROW_MIN = 0.35;
  const LINK_COL_MIN = 0.35;

  const jobPeak = useMemo(() => {
    const m = new Map<string, number>();
    for (const v of visibleItems) {
      for (const [jid, w] of v.row.jobs) m.set(jid, Math.max(m.get(jid) ?? 0, w));
    }
    return m;
  }, [visibleItems]);
  /** 每项能力在各岗位间的最高覆盖率。与 jobPeak 成对，供同一条判据取用 */
  const itemPeak = useMemo(() => {
    const m = new Map<string, number>();
    for (const [id, row] of itemById) m.set(id, Math.max(...row.jobs.values(), 0));
    return m;
  }, [itemById]);
  /** 一条能力—岗位边是否成立。两个方向共用，故不会出现一边亮、一边不亮 */
  const linked = useCallback(
    (iid: string, jid: string, w: number) => {
      const rp = itemPeak.get(iid) ?? 0;
      const cp = jobPeak.get(jid) ?? 0;
      if (rp <= 0 || cp <= 0) return false;
      return w / rp >= LINK_ROW_MIN && w / cp >= LINK_COL_MIN;
    },
    [itemPeak, jobPeak],
  );
  const links = useMemo(() => {
    const seen = new Set<string>();
    const out: { key: string; d: string; w: number }[] = [];
    const push = (iid: string, jid: string, weight: number) => {
      const si = itemIndex.get(iid);
      const ji = jobIndex.get(jid);
      if (si === undefined || ji === undefined) return;
      const key = `${iid}>${jid}`;
      if (seen.has(key)) return;
      seen.add(key);
      out.push({
        key,
        d: curve(c.barEnd, yOfItem(si), c.jobX, yOfJob(ji)),
        w: 0.4 + weight * 1.8,
      });
    };

    /* 线宽取行内比：粗细表达的是"这个岗位在要这项能力的岗位里排多前" */
    for (const iid of selSkills) {
      const row = itemById.get(iid);
      if (!row) continue;
      const peak = itemPeak.get(iid) ?? 1e-9;
      for (const [jid, w] of row.jobs) if (linked(iid, jid, w)) push(iid, jid, w / peak);
    }
    for (const jid of selJobs) {
      for (const v of visibleItems) {
        const w = v.row.jobs.get(jid);
        if (w === undefined || !linked(v.row.id, jid, w)) continue;
        const peak = itemPeak.get(v.row.id) ?? 1e-9;
        push(v.row.id, jid, w / peak);
      }
    }
    return out;
  }, [selSkills, selJobs, visibleItems, itemIndex, jobIndex, itemById, itemPeak, linked, c, iPitch, jPitch]);

  /** 因为连线而被点亮的两侧 —— 论文用同一个描边表示"选中"与"被连上" */
  const litJobs = useMemo(() => {
    const s = new Set<string>();
    for (const iid of selSkills) {
      const row = itemById.get(iid);
      if (!row) continue;
      for (const [jid, w] of row.jobs) if (linked(iid, jid, w)) s.add(jid);
    }
    return s;
  }, [selSkills, itemById, linked]);

  const litItems = useMemo(() => {
    const s = new Set<string>();
    for (const jid of selJobs) {
      for (const v of visibleItems) {
        const w = v.row.jobs.get(jid);
        if (w !== undefined && linked(v.row.id, jid, w)) s.add(v.row.id);
      }
    }
    return s;
  }, [selJobs, visibleItems, linked]);

  return (
    <div className="sjo">
      <svg
        ref={svgRef}
        width={c.w}
        height={height}
        className="sjo-svg"
        role="img"
        aria-label="能力体系、岗位与属性分布的联动总览"
        onContextMenu={(e) => e.preventDefault()}
      >
        <defs>
          {/* 要求程度的第四档"无法确定"：与全景图谱同一种画法。
              招聘原文没写程度词是一件与"要求有多高"不同性质的事，
              不给它色阶上的任何一档，改用点阵退到后景。 */}
          <pattern id="sjo-unknown" width="4" height="4" patternUnits="userSpaceOnUse">
            <rect width="4" height="4" fill="var(--panel)" />
            <circle cx="1" cy="1" r="0.85" fill="#7d8798" opacity="0.75" />
          </pattern>
        </defs>
        {/* ---- 列头 ---- */}
        <text className="sjo-colhead" x={X_L1} y={15}>
          能力体系（条长 = 需求量的平方根，色 = 要求程度）
        </text>
        <text className="sjo-colhead" x={c.jobX} y={15}>
          岗位（条长 = 招聘信息条数，色 = 软硬构成）
        </text>
        <text className="sjo-colhead" x={c.attrX} y={15}>
          岗位属性（条长 = 条数，色 = 薪资档）
        </text>

        {/* ---- 能力 ↔ 岗位 连线，垫在最底下 ---- */}
        <g className="sjo-links">
          {links.map((l) => (
            <path key={l.key} d={l.d} strokeWidth={l.w} />
          ))}
        </g>

        {/* ---- A1 树 ---- */}
        <g className="sjo-tree">
          {dimSpans.map((s) => {
            const y = (yOfGroup(s.a) + yOfGroup(s.b)) / 2;
            const on = litDims.has(s.name);
            const f = fitText(s.name, W_L1 - W_FAN, 12);
            const x1 = X_L1 + measureText(f.text, f.size) + 8;
            return (
              <g key={s.name} className={`sjo-dim${on ? ' on' : ''}`}>
                {groups.slice(s.a, s.b + 1).map((g, k) => (
                  <path key={g.id} className="sjo-tlink" d={curve(x1, y, X_L2 - 4, yOfGroup(s.a + k))} />
                ))}
                <text
                  className="sjo-dimname"
                  x={X_L1}
                  y={y + 4}
                  style={{ fontSize: `${f.size}px` }}
                  onClick={() => onToggleDim(s.name)}
                >
                  {f.text}
                  <title>{s.name}　单击点亮它到技能的连线</title>
                </text>
              </g>
            );
          })}

          {groups.map((g, i) => {
            const y = yOfGroup(i);
            const open = expanded.has(g.id);
            const f = fitText(g.name, W_L2 - W_FAN - 6, 12);
            const x1 = X_L2 + measureText(f.text, f.size) + 8;
            const kids = open ? g.items.map((iid) => itemIndex.get(iid)).filter((v) => v !== undefined) : [];
            return (
              <g key={g.id} className={`sjo-grp${open ? ' open' : ''}`}>
                {kids.map((ki) => (
                  <path key={ki} className="sjo-tlink" d={curve(x1, y, X_L3 - 4, yOfItem(ki as number))} />
                ))}
                <text
                  className="sjo-grpname"
                  x={X_L2}
                  y={y + 4}
                  style={{ fontSize: `${f.size}px` }}
                  onClick={() => onToggleGroup(g.id)}
                >
                  {f.text}
                  <title>{g.name}　单击{open ? '收起' : '展开'}组内技能</title>
                </text>
              </g>
            );
          })}
        </g>

        {/* ---- A1 技能点与需求条 ---- */}
        <g className="sjo-skills">
          {visibleItems.map((v, i) => {
            const r = v.row;
            const y = yOfItem(i);
            const on = selSkills.has(r.id);
            const lit = litItems.has(r.id);
            const len = Math.max((skillLen(r.share) / maxSkill) * c.barMax, 2);
            const x0 = c.barEnd - len;
            const psum = r.prof.reduce((a, b) => a + b, 0) || 1;
            const f = fitText(r.name, W_L3 - 8, Math.min(12, iPitch - 6));
            let acc = x0;
            return (
              <g
                key={r.id}
                className={`sjo-row${on ? ' on' : ''}${lit ? ' lit' : ''}`}
                onClick={() => onToggleSkill(r.id)}
                aria-label={r.name}
              >
                {/* 整行的命中区。浮层挂在它身上而不是外层的 <g>：条上那几段各有
                    自己的浮层（报的是该段的档位），挂在 <g> 上会在事件冒泡时把
                    段级读数覆盖成行级读数。命中区在段之下，指针落在段上时它不在
                    事件路径里，两级浮层因而各管各的一段。

                    此前这一行只有条上有浮层：名字与条之间的空档、以及条本身留白
                    的那一截，指针停上去既无浮层也无读数，而一行说的是同一项能力。 */}
                <rect
                  className="sjo-hit"
                  x={X_L3 - 4}
                  y={y - iPitch / 2}
                  width={c.barEnd - X_L3 + 8}
                  height={iPitch}
                  onMouseMove={(e) =>
                    onTip(
                      e,
                      <>
                        <div className="tt-title">{r.name}</div>
                        <div>需求量 {Math.round(r.demand).toLocaleString()}</div>
                        <div className="tt-muted">
                          {PROF_LEVELS.map((lv, k) =>
                            r.prof[k] > 0
                              ? `${lv} ${Math.round((r.prof[k] / psum) * 100)}%`
                              : null,
                          )
                            .filter(Boolean)
                            .join(' · ')}
                        </div>
                      </>,
                    )
                  }
                  onMouseLeave={(e) => onTip(e, null)}
                />
                <text className="sjo-itemname" x={X_L3} y={y + 3.5} style={{ fontSize: `${f.size}px` }}>
                  {f.text}
                  <title>{r.name}</title>
                </text>
                {PROF_LEVELS.map((lv, k) => {
                  const w = (len * r.prof[k]) / psum;
                  const x = acc;
                  acc += w;
                  return w > 0.4 ? (
                    <rect
                      key={lv}
                      className="sjo-seg"
                      x={x}
                      y={y - barH / 2}
                      width={w}
                      height={barH}
                      // 第四档画点阵：原文没写程度词不是色阶上的一级，见 explore.PROF_COLORS
                      fill={k === PROF_UNKNOWN ? 'url(#sjo-unknown)' : PROF_COLORS[k]}
                      onMouseMove={(e) =>
                        onTip(
                          e,
                          <>
                            <div className="tt-title">{r.name}</div>
                            <div>
                              {k === PROF_UNKNOWN ? lv : `要求${lv}`}：
                              {Math.round((r.prof[k] / psum) * 100)}%
                            </div>
                            <div className="tt-muted">需求量 {Math.round(r.demand).toLocaleString()}</div>
                          </>,
                        )
                      }
                      onMouseLeave={(e) => onTip(e, null)}
                    />
                  ) : null;
                })}
                <rect className="sjo-barout" x={x0} y={y - barH / 2} width={len} height={barH} rx={2} />
              </g>
            );
          })}
        </g>

        {/* ---- A2 岗位 ---- */}
        <g className="sjo-jobs">
          {jobs.map((j, i) => {
            const y = yOfJob(i);
            const on = selJobs.has(j.id);
            const lit = litJobs.has(j.id);
            const len = Math.max((j.posts / maxJob) * c.jobW, 2);
            const f = fitText(j.name, W_JOBNAME - 4, Math.min(11, jPitch - 1));
            let acc = c.jobX;
            return (
              <g
                key={j.id}
                className={`sjo-row${on ? ' on' : ''}${lit ? ' lit' : ''}`}
                onClick={() => onToggleJob(j.id)}
                onContextMenu={(e) => {
                  e.preventDefault();
                  onSortBy(j.id);
                }}
                onMouseMove={(e) =>
                  onTip(
                    e,
                    <>
                      <div className="tt-title">{j.name}</div>
                      <div>
                        {j.cluster} · {Math.round(j.posts).toLocaleString()} 条招聘信息
                      </div>
                      {/* 操作说明不进浮层：浮层报的是这一行的读数，把"右键能做什么"
                          混在同一段里，读者会把它当成这一行的一条数据。重排的入口
                          在图上方的排序控件里另有一处。 */}
                      <div className="tt-muted">
                        硬 {Math.round(j.mix.hard * 100)}% · 软 {Math.round(j.mix.soft * 100)}%
                      </div>
                    </>,
                  )
                }
                onMouseLeave={(e) => onTip(e, null)}
                aria-label={j.name}
              >
                <rect
                  className="sjo-hit"
                  x={c.jobX - 4}
                  y={y - jPitch / 2}
                  width={c.jobW + W_JOBNAME + 14}
                  height={jPitch}
                />
                {SKILL_TYPES.map((t) => {
                  const w = len * j.mix[t.v];
                  const x = acc;
                  acc += w;
                  return w > 0.4 ? (
                    <rect
                      key={t.v}
                      className="sjo-seg"
                      x={x}
                      y={y - jobH / 2}
                      width={w}
                      height={jobH}
                      fill={MIX_COLORS[t.v]}
                    />
                  ) : null;
                })}
                <rect className="sjo-barout" x={c.jobX} y={y - jobH / 2} width={len} height={jobH} rx={2} />
                <text
                  className="sjo-jobname"
                  x={c.jobName + W_JOBNAME}
                  y={y + 3.5}
                  style={{ fontSize: `${f.size}px` }}
                >
                  {f.text}
                  <title>{j.name}</title>
                </text>
              </g>
            );
          })}
        </g>

        {/* ---- A3 属性分布：四组一次画完 ---- */}
        <g className="sjo-attrs">
          {attrLay.groups.map((grp) => {
            const sel = picks[grp.kind];
            const off = grp.overflow > 0 ? Math.min(scrollOf(grp.kind), grp.overflow) : 0;
            const clipId = `sjo-clip-${grp.kind}`;
            return (
              <g key={grp.kind} className={`sjo-attrgrp${sel.size ? ' on' : ''}`}>
                <text className="sjo-attrhead" x={c.attrX} y={grp.headY}>
                  {grp.label}
                  {/* 该维的分布由补齐层给出时在组头标出：四组并排、条长共用一种读法，
                      不标的话读者无从区分哪一组的高低是测出来的 */}
                  <tspan className="sjo-attrhead-sc">　满格 {fullScale(grp.max)}</tspan>
                  {sel.size > 0 && <tspan className="sjo-attrhead-n">　已选 {sel.size}</tspan>}
                </text>
                <line
                  className="sjo-attrrule"
                  x1={c.attrX}
                  x2={c.attrName + W_ATTRNAME}
                  y1={grp.headY + 5}
                  y2={grp.headY + 5}
                />
                {grp.overflow > 0 && (
                  <clipPath id={clipId}>
                    <rect
                      x={c.attrX - 6}
                      y={grp.y0}
                      width={c.attrW + W_ATTRNAME + 20}
                      height={grp.viewH}
                    />
                  </clipPath>
                )}
                <g clipPath={grp.overflow > 0 ? `url(#${clipId})` : undefined}>
                  <g transform={off ? `translate(0 ${-off})` : undefined}>
                {grp.rows.map((a, i) => {
                  const y = grp.y0 + ATTR_PITCH * (i + 0.5);
                  const on = sel.has(a.bucket);
                  const len = Math.max((a.posts / grp.max) * c.attrW, 2);
                  const ssum = a.salary.reduce((x, v) => x + v, 0) || 1;
                  const f = fitText(a.bucket, W_ATTRNAME - 6, 11);
                  /* 名字右对齐，左缘随字数浮动；下拉钮贴着这个左缘走 */
                  const nameLeft = c.attrName + W_ATTRNAME - measureText(f.text, f.size);
                  let acc = c.attrX;
                  return (
                    <g
                      key={a.bucket}
                      className={`sjo-row attr${on ? ' on' : ''}`}
                      onClick={() => onTogglePick(grp.kind, a.bucket)}
                      onMouseEnter={grp.kind === 'cities' ? () => setHoverAttr(a.bucket) : undefined}
                      onMouseLeave={grp.kind === 'cities' ? () => setHoverAttr(null) : undefined}
                      aria-label={`${grp.label} ${a.bucket}：${Math.round(a.posts)} 条`}
                    >
                      {/* 与能力列同理：浮层挂在命中区上，条上那几段各报各的薪资档，
                          两级互不覆盖；名字与条之间的空档也因此有读数。 */}
                      <rect
                        className="sjo-hit"
                        x={c.attrX - 4}
                        y={y - ATTR_PITCH / 2}
                        width={c.attrW + W_ATTRNAME + 14}
                        height={ATTR_PITCH}
                        onMouseMove={(e) =>
                          onTip(
                            e,
                            <>
                              <div className="tt-title">
                                {grp.label} · {a.bucket}
                              </div>
                              <div>{Math.round(a.posts).toLocaleString()} 条招聘信息</div>
                              <div className="tt-muted">单击{on ? '撤出' : '加入'}筛选</div>
                            </>,
                          )
                        }
                        onMouseLeave={(e) => onTip(e, null)}
                      />
                      {a.salary.map((v, k) => {
                        const w = (len * v) / ssum;
                        const x = acc;
                        acc += w;
                        return w > 0.4 ? (
                          <rect
                            key={salaryBands[k] ?? k}
                            className="sjo-seg"
                            x={x}
                            y={y - 5.5}
                            width={w}
                            height={11}
                            fill={SALARY_COLORS[k]}
                            onMouseMove={(e) =>
                              onTip(
                                e,
                                <>
                                  <div className="tt-title">
                                    {grp.label} {a.bucket} · {salaryBands[k]}
                                  </div>
                                  <div>占该分档 {Math.round((v / ssum) * 100)}%</div>
                                  <div className="tt-muted">
                                    约 {Math.round(v).toLocaleString()} 条　单击
                                    {on ? '撤出' : '加入'}筛选
                                  </div>
                                </>,
                              )
                            }
                            onMouseLeave={(e) => onTip(e, null)}
                          />
                        ) : null;
                      })}
                      <rect className="sjo-barout" x={c.attrX} y={y - 5.5} width={len} height={11} rx={2} />
                      <text
                        className="sjo-attrname"
                        x={c.attrName + W_ATTRNAME}
                        y={y + 3.5}
                        style={{ fontSize: `${f.size}px` }}
                      >
                        {f.text}
                        <tspan className="sjo-attrnum" dy={11} x={c.attrName + W_ATTRNAME}>
                          {Math.round(a.posts).toLocaleString()}
                        </tspan>
                      </text>
                      {/* 省份行的下拉：单击即在该省下逐座勾选城市。
                          只在悬停的那一行与已展开的那一行出现 —— 三十余行各挂一个箭头
                          会在条与名之间立起一整列同形的图标，把这一栏切成两半，
                          而这一列真正要读的是条长与省名。
                          位置贴在省名左侧，随名字长短浮动，不另占一列。 */}
                      {grp.kind === 'cities' &&
                        onOpenCityMenu &&
                        (hoverAttr === a.bucket || cityMenuOf === a.bucket) && (
                          <g
                            className={`sjo-dd${cityMenuOf === a.bucket ? ' on' : ''}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              const open = cityMenuOf === a.bucket;
                              setCityMenuAt(open ? null : { x: c.attrX - 10, y: y - off + 14 });
                              onOpenCityMenu(open ? null : a.bucket);
                            }}
                          >
                            <title>{a.bucket}　单击勾选该省下的城市</title>
                            <rect
                              className="sjo-dd-hit"
                              x={nameLeft - 19}
                              y={y - 8}
                              width={17}
                              height={17}
                              rx={4}
                            />
                            <path
                              className="sjo-dd-caret"
                              d={`M${nameLeft - 15},${y - 1.5} l3.5,3.5 l3.5,-3.5`}
                            />
                          </g>
                        )}
                    </g>
                  );
                })}
                  </g>
                </g>
                {/* 组内滚动条。省份一组三十余行，视窗一次只放得下十余行；
                    没有这条指示，读者读到的是"只有这十几个省"。 */}
                {grp.overflow > 0 && (
                  <g
                    className="sjo-sb"
                    onPointerDown={(e) => startDrag(e, grp.kind, grp.overflow, grp.viewH, grp.rows.length)}
                  >
                    <rect
                      className="sjo-sb-track"
                      x={c.attrName + W_ATTRNAME + SB_GAP}
                      y={grp.y0}
                      width={SB_W}
                      height={grp.viewH}
                      rx={SB_W / 2}
                    />
                    <rect
                      className="sjo-sb-thumb"
                      x={c.attrName + W_ATTRNAME + SB_GAP}
                      y={grp.y0 + (off / (grp.overflow + grp.viewH)) * grp.viewH}
                      width={SB_W}
                      height={Math.max(18, (grp.viewH / (grp.overflow + grp.viewH)) * grp.viewH)}
                      rx={SB_W / 2}
                    />
                  </g>
                )}
              </g>
            );
          })}
        </g>
      </svg>

      {/* 浮窗本体是一层 HTML，不进 svg：它有阴影、圆角与悬停提示，还要能被 Tab
          走到，这几样在 svg 里都要另造一遍。外层是零尺寸锚点，内层贴着它右对齐，
          浮窗因此不受 .sjo 实际宽度影响 —— 这张图比容器宽，按容器右缘算会偏出一截。
          key 挂当前岗位，换一个岗位即重播一次入场，浮窗不在两行之间滑行。 */}
      {jumpAt && (
        <div
          key={jumpId ?? ''}
          className="jdk-at"
          style={{ left: jumpAt.x, top: jumpAt.y }}
        >
          <div className={jumpAt.below ? 'jdk-wrap below' : 'jdk-wrap'}>{jumpDock}</div>
        </div>
      )}

      {cityMenuOf && cityMenuAt && cityMenu && (
        <div ref={popRef} className="sjo-citypop" style={{ left: cityMenuAt.x, top: cityMenuAt.y }}>
          {cityMenu}
        </div>
      )}
    </div>
  );
}
