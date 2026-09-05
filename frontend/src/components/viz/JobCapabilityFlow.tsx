/* ============================================================
   岗位能力全景流图 —— 全景图谱的主图

   ------------------------------------------------------------
   编排

   三段并排、两组连线，形式沿用 JobViz（Wang et al., Visual Informatics
   2024）Figure 2(A)；对象与方向都换成图谱自己的那条链。

     A1 岗位          条长 = 该岗位的能力要求总量，行序 = 招聘信息条数
                      两个通道编码的不是同一件事：条长随口径变，行序不变 ——
                      要求总量是补出来的权重和，招聘条数是实测量，谁排在最上面
                      这件事交给后者。分段仍是软硬构成，见图注。
     A2 核心任务      条长 = 要求强度
     A3 能力体系      技能 ├ 能力组 ├ 能力维度，条长 = 要求强度
                      技能点在行内下钻：点开一项技能，其技能点就地展开在该行下方

   从左到右就是 岗位 → 任务 → 技能，也是算法侧的数据流向（P-T / T-S）。
   读者从左边任取一个岗位，顺着连线往右走两跳，就走到它要的每一项技能，
   再点开那一行，就走到具体的技能点（S-SP）。

   第四层不并排画作第四段，原因是数量：技能为封闭体系五十四项，一屏读得完；
   技能点是随市场文本生长的开放集合，本批逾两万项，铺成一列时行高压到
   一像素以下，条长之间读不出差别。下钻逐项打开，每次只看一项技能下的
   十来个技能点，条长才有分辨率。

   方向与职业探索页恰好相反，这是有意的：那一页问"从能力出发能走到哪些岗位"，
   所以能力在左；这一页问"岗位的要求分解成什么"，所以岗位在左。
   两页并排看时，方向本身就说明了各自在回答哪个问题。

   ------------------------------------------------------------
   时间维长在哪

   时间由图上方的游标控制，在图上有两处落点：

     ① 条长（跟着游标走）：某一个月的截面长什么样？
        拖时间轴，三段的条长同时改写，排序不动 —— 行的位置一旦跟着月份变，
        同一项能力每个月换一行，就没人追得住了。
     ② 基准月对比：跟那时候比，变了什么？
        钉一个基准月之后，每行画出增量段（实心接长）或收缩段（斜纹回缩），
        新出现的标“新增”、当月已不在图谱里的标“退出”。
        赛题②要的"明确标注岗位新增、删除、修改的技能点"落在这里。

   ------------------------------------------------------------
   两条约定与全站一致

     · 连线只在被问到时才画，逐段给。没有选中项时一条不画：三段并排本身已经
       交代了顺序，铺一层常显底反而让第一眼落在连线而不是条长上，而条长才是
       这张图的量。选中一个岗位画它到各项任务的那一束；要看某一项任务落到哪些
       技能，点亮那一项任务，此时只画那一项的 —— 一个岗位常带十余项任务、
       四十余项技能，两束一起铺开是一片网。选中任务时两侧同时画，选中技能时
       只画任务到它的那一束。
     · 岗位选中态下点它关联的任务或技能不改选中项，只往下钻一层，链因而不散。
     · 名字放不下先压字号（12 → 9.5px），压到下限才截断。
   ============================================================ */

import { useEffect, useMemo, useState } from 'react';
import type { FlowModel, FlowRow } from '@/data/panoramaFlow';
import { rowAt, type ChangeKind } from '@/data/panoramaFlow';
import { MIX_COLORS, PROF_COLORS, PROF_LEVELS, PROF_UNKNOWN, SKILL_TYPES } from '@/data/explore';
import { fitText, measureText } from '@/utils/viz';

/* ---------------- 布局常量 ----------------
   行距与条高定了整幅图的墨水量。上一版 ROW=18 / BAR_H=10，实测 63 行画成
   1495×636 的一张图，纵向约九成是空白与小字 —— 作为一页的主图，第一眼
   给不出分量。这一版行距放到 24、条高放到 15（占行距 0.62），条与条之间
   仍留 9px 的白，不至于糊成一片色块，但每一根条本身厚了一半。 */

/** 最密的那一段的行距。整幅图有多高由它乘以最长一段的行数定出来 */
const ROW = 24;
/** 条高。三段共用这一个值，不随各段行数变 —— 见下面 laneOf 那一段 */
const BAR_H = Math.round(ROW * 0.62);
/* 下钻展开的技能点条高。取父条的三分之二：细一档才看得出层级，
   再细则两侧的要求程度分段挤成色线，读不出分段本身。 */
const BAR_KID_H = Math.round(BAR_H * 0.66);
/** 列头那一区：序号与段名一行、编码说明一行、基线一行，之后再留 12px 到首根条。
    主图要有自己的抬头，不能一上来就是条。 */
const PAD_TOP = 63;
const PAD_BOTTOM = 22;

/** 选中行浮窗的整体高度，只用来判断该行上方还排不排得下（见 jumpAt） */
const JUMP_H = 34;
/** 浮窗与条之间留的空档，尾巴的高度落在这一段里 */
const JUMP_GAP = 14;

const W_GROUP = 100;
const W_DIM = 36; // 竖排的能力维度名
const W_BAR = 104; // 三根条的常规长度，整幅宽度的下限按它算
/** 名字排不下时条能让到的最短长度 —— 让到这里为止，再短就读不出长短了 */
const W_BAR_TIGHT = 84;
const GAP_LINK = 48;
/** 任务条：已被招聘市场确认的那一截 / 由前瞻信号追加的那一截。

    ------------------------------------------------------------
    中间段为什么从橙棕换成紫罗兰

    两端是定死的：岗位段跟全站主色走蓝（OKLCH 色相 266°），技能点段是熟练度
    青阶（184°）。在这两端之间给中间段找色相，此前的扫描把明度锁死在 0.42
    逐度扫过 360 个色相，结论是只剩红橙 24°–42° 与橙棕 45°–60° —— 于是
    连出三版都是暖色，而这三版全被否掉，理由一致：偏红，不好看。

    那个结论漏了一段。把明度放开到 0.38–0.46 这一整段（仍与两端齐平，
    不是上浮）重扫，可行区并不止暖色那一片：312°–338° 的紫罗兰同样过线。
    此前判紫“在红绿色觉缺陷下与岗位蓝 ΔE 只有 0.4–5.0”，量的是低色度的
    蓝紫（280°–310°，C≈0.075）；色度提到 0.15 并把色相推到 320° 之后，
    与岗位蓝在 protan/deutan 下拉开到 8.0，已在门槛之上。

    换过来这一版的三项实测：

      · 明度  0.419，两端是 0.424 与 0.408 —— 三段仍然齐平，中间段不浮起来。
              这是前两版被否的根因，任何后续调整都要先守住这一行。
      · 色度  0.150，落在岗位蓝 0.181 与技能点青 0.070 之间，视觉重心不偏。
      · 色觉  protan/deutan 下与两端的最小 ΔE 8.0（上一版橙棕 9.1，同属合格档）；
              tritan 下 15.5，而上一版橙棕在这一档只有 4.9 —— 蓝黄轴缺陷者
              读橙棕与深青几乎同色，换成紫之后这一档才真正分开。

    紫在本项目里不是新色：tokens.css 的 `--lay-task` 早已是 #7c5cfc，
    中间段本就该是紫，只是全景图这幅一直沿用暖色。#7c5cfc 直接拿来会
    浮起来（L 0.597、C 0.226），所以按上面三项压成同族的深档。

    余下的约束没变：色觉缺陷 ΔE 落在 6–8 那一档时须由非颜色的编码兜底，
    图内的三列分置、列头色号与层色基线、每行直接标名都在。

    金黄 #d4ad12 也试过并被否（“更丑了”），不必再回头试黄。
    ------------------------------------------------------------ */
const TASK_BASE = '#6f287e';
const TASK_FORE = '#a66db3';

/* ---------------- 三列文字的宽度 ----------------
   名字列过去按一个定值排，多出来的宽度全给三根条。真实体系里的名字长短相差
   一倍有余（岗位从"计量工程师"到"嵌入式软件开发(Linux/单片机/PLC/DSP等)"），
   于是宽屏上条已经拉到近两百像素，长一点的岗位名还在压字号乃至截断 ——
   读者认不出是哪个岗位，条再长也没有用。

   改成：min 是短名字排得下的宽度，max 是这一层最长的那个名字按 12px 排得下的
   宽度（岗位 27 字、技能点 18 字，实测 228 / 203 像素，加上列内留白即下面的数）。
   每次渲染按本屏真正要排的那批名字在两者之间取值，多出来的宽度先补足名字列，
   补齐了剩下的才平分给三根条 —— 名字列因此一律不低于 min。 */
interface NameCol {
  /** 列宽里不归文字的那部分：变化标记、与条之间的空档 */
  pad: number;
  min: number;
  max: number;
}
const COL_JOB: NameCol = { pad: 18, min: 138, max: 248 };
const COL_TASK: NameCol = { pad: 21, min: 148, max: 168 };
const COL_ITEM: NameCol = { pad: 19, min: 168, max: 226 };

/** 本屏这一列真正需要多宽 —— 按最长的那个名字量，钳在 [min, max] 内 */
function colWidth(rows: FlowRow[], col: NameCol): number {
  let m = 0;
  for (const r of rows) {
    const w = measureText(r.name, 12);
    if (w > m) m = w;
  }
  // 向上取整：差 0.1 像素也会让 fitText 判定排不下，白压一档字号
  return Math.ceil(Math.min(Math.max(m + col.pad, col.min), col.max));
}

/** 三列文字之外的固定开销：左右留白、两处连线空档、能力组与能力维度两列 */
const W_FIXED = 6 + GAP_LINK + GAP_LINK + 4 + W_GROUP + W_DIM + 6;
/** 名字列全取下限、三根条全取下限时的整幅宽度 */
const MIN_W = W_FIXED + COL_JOB.min + COL_TASK.min + COL_ITEM.min + 3 * W_BAR;

interface Cols {
  jobName: number;
  /** 岗位条的右端，也是第一组连线的起点 */
  jobEnd: number;
  barMax: number;
  taskX: number;
  taskName: number;
  /** 任务段的右端，第二组连线的起点 */
  taskEnd: number;
  capX: number;
  capName: number;
  capGroup: number;
  capDim: number;
  /** 能力段的右端，列头那条基线画到这里为止 */
  capEnd: number;
  /** 三列文字实际拿到的宽度，排版预算（减去 pad）按它算 */
  jobW: number;
  taskW: number;
  itemW: number;
  w: number;
}

/** want* 为 colWidth 量出来的期望宽度，宽度不够时按各自缺口的比例回压 */
function layout(w: number, wantJob: number, wantTask: number, wantItem: number): Cols {
  const W = Math.max(w, MIN_W);
  /* 三列文字与三根条分这一段可用宽度：名字要多少给多少，剩下的三根条平分 ——
     条比名字长上一截读不出更多东西，名字缺一个字就认不出是哪一行。
     名字要得比给得出的还多时（窄屏），条一路让到 W_BAR_TIGHT 为止；
     仍不够就按各列的缺口比例分，余下的交给 fitText 压字号。 */
  const avail = W - W_FIXED;
  const gap = wantJob - COL_JOB.min + (wantTask - COL_TASK.min) + (wantItem - COL_ITEM.min);
  const minSum = COL_JOB.min + COL_TASK.min + COL_ITEM.min;
  const nameSum = Math.min(minSum + gap, Math.max(minSum, avail - 3 * W_BAR_TIGHT));
  const k = gap > 0 ? (nameSum - minSum) / gap : 0;
  const jobW = COL_JOB.min + (wantJob - COL_JOB.min) * k;
  const taskW = COL_TASK.min + (wantTask - COL_TASK.min) * k;
  const itemW = COL_ITEM.min + (wantItem - COL_ITEM.min) * k;
  const barMax = (avail - jobW - taskW - itemW) / 3;

  const jobName = 6;
  const jobEnd = jobName + jobW + barMax;

  const taskX = jobEnd + GAP_LINK;
  const taskName = taskX + barMax;
  const taskEnd = taskName + taskW;

  const capX = taskEnd + GAP_LINK;
  const capName = capX + barMax;
  const capGroup = capName + itemW + 4;
  const capDim = capGroup + W_GROUP;
  return {
    jobName,
    jobEnd,
    barMax,
    taskX,
    taskName,
    taskEnd,
    capX,
    capName,
    capGroup,
    capDim,
    capEnd: capDim + W_DIM,
    jobW,
    taskW,
    itemW,
    w: W,
  };
}

/* ---------------- 三段的纵向落位 ----------------
   三段的行数天然不等：一个大类可能有 32 个岗位、8 项核心任务、23 个技能点。
   共用一个行距的话，短的那一段画到三分之一就没了，右边一长列独自往下走，
   版面右重左轻，两段之间的连线也全挤在图的上缘。

   改成每一段在同一段可用高度里摊开：首行的条顶贴着上边界、末行的条底贴着
   下边界，中间等距分。三段的第一根条与最后一根条因此严格对齐，最密的那一段
   行距恰好是 ROW。按"行格子等分"也能让行底对齐，但行内容居中会让稀疏那一段
   的末条比密集段的高出半个行距 —— 眼睛对的是条，不是看不见的格子。

   行距不设上限：一封顶，行数少的那一段就又缩回去，底边重新对不齐 ——
   那正是要治的病。

   条高则三段共用一个值（BAR_H），不跟着本段行距走。曾按行距给条加粗，本意是
   让稀疏那一段不至于散成几根细线；代价是同一幅图里三段的条粗细不一，读者会把
   粗细当成一重编码去读 —— 而条上唯一的编码是长度。稀疏段靠每行整列宽的轨道底
   维持"这几根条是一列"，那一条已经够用。 */

interface Lane {
  /** 相邻两行的间距。只有一行时为 0，那一行落在整列正中 */
  step: number;
  /** 首行的行心 */
  first: number;
  /** 行的命中区高度 —— 不跟着 step 无限长，否则鼠标停在半列之外也会点亮某一行 */
  hit: number;
}

function laneOf(n: number, usable: number): Lane {
  const step = n > 1 ? (usable - BAR_H) / (n - 1) : 0;
  return {
    step,
    first: n > 1 ? PAD_TOP + BAR_H / 2 : PAD_TOP + usable / 2,
    hit: Math.min(Math.max(step, BAR_H + 6), BAR_H + 18),
  };
}

const yIn = (l: Lane, i: number) => l.first + l.step * i;

/** 一段的标度量程：该段所有行在所有月份上取到过的最大值 */
function spanMax(rows: FlowRow[]): number {
  let m = 1e-9;
  for (const r of rows) {
    if (r.demand > m) m = r.demand;
    for (const v of r.series) if (v !== null && v !== undefined && v > m) m = v;
  }
  return m;
}

const curve = (x0: number, y0: number, x1: number, y1: number) => {
  const mx = (x0 + x1) / 2;
  return `M${x0.toFixed(1)},${y0.toFixed(1)} C${mx.toFixed(1)},${y0.toFixed(1)} ${mx.toFixed(1)},${y1.toFixed(1)} ${x1.toFixed(1)},${y1.toFixed(1)}`;
};

/** 第三段的一行：depth 0 为技能，depth 1 为展开出来的技能点 */
interface CapRow {
  row: FlowRow;
  depth: 0 | 1;
  parent?: string;
}

export interface FlowLayoutInfo {
  capRows: number;
  taskRows: number;
  jobRows: number;
  /** 当月已进入招聘要求的技能数（第三段的行） */
  lit: number;
  /** 当月尚未进入图谱的行数（画成空轨道） */
  absent: number;
  /** 相对基准月的增减计数 */
  changed: { new: number; up: number; down: number; gone: number };
}

interface Props {
  model: FlowModel;
  cursor: number;
  baseline: number | null;
  selected: string | null;
  onSelect: (id: string | null) => void;
  /** 岗位段上限，图外如实写出总数 */
  jobLimit: number;
  /** 任务段上限 */
  taskLimit: number;
  width: number;
  onLayoutInfo?: (info: FlowLayoutInfo) => void;
  onTip: (e: React.MouseEvent, row: FlowRow | null) => void;
  /**
   * 选中一个岗位时贴着那一行浮出的一小块内容，用于跨页出口（见 JumpDock）。
   * 图只负责落位，内容与去向由页面给：这一层不认识路由。
   */
  jumpDock?: React.ReactNode;
}

export function JobCapabilityFlow({
  model,
  cursor,
  baseline,
  selected,
  onSelect,
  jobLimit,
  taskLimit,
  width,
  onLayoutInfo,
  onTip,
  jumpDock,
}: Props) {
  /* ---------------- 第三段的两层 ----------------

     常态画到技能这一层（49 项，封闭体系，一屏读得完）；选中一项技能时，
     该行下方就地展开它的技能点，条长为父条按组内占比切开的一截，
     子条之和等于父条本身。下钻因而不改变读数：展开前读到的那根条有多长，
     展开后各段加起来仍是那么长。

     不并排画两层的理由是数量：技能点是随市场文本生长的开放集合，本批逾两万项，
     铺成一列时行高压到一像素以下，条长之间读不出差别。
     不另开抽屉的理由是标度：抽屉里的条与图上的条不共用一把尺子，
     "这一项在整段里占多重"这个问题就答不出来。 */
  /* ---------------- 选中之下的一层：下钻 ----------------
     岗位是一条链的起点，选中它之后还要能问下去："这些任务里的哪一项要求哪些技能"、
     "这一项技能落到哪些技能点"。上一版每问一次就把选中项换成被点的那一行，
     岗位随之失选，整条链跟着散掉 —— 而问的人从头到尾看的是同一个岗位。

     因此在选中项之下另设一层：岗位处于选中态时，点它关联的任务或技能只改这一层，
     岗位的选中状态不动；点未关联的行才换选中项。任务与技能各存一格，
     互不影响：一格看连线，一格看技能点展开。 */
  const [drillTask, setDrillTask] = useState<string | null>(null);
  const [drillSkill, setDrillSkill] = useState<string | null>(null);
  /* 换了选中项，下钻即作废：它是相对某一个岗位而言的 */
  useEffect(() => {
    setDrillTask(null);
    setDrillSkill(null);
  }, [selected]);

  /** 选中项落在哪一层。三处判断共用，免得各写各的 */
  const jobPicked = useMemo(
    () => (selected ? model.jobRows.some((r) => r.id === selected) : false),
    [selected, model.jobRows],
  );

  /* 技能点展开：直接选中一项技能时由选中项给，岗位选中态下由下钻那一格给。
     切换选中另一项技能时前一项自然收起 —— 两处都只认一个值。 */
  const expandedSkill = useMemo(() => {
    if (jobPicked) return drillSkill;
    if (!selected) return null;
    return model.groupRows.some((r) => r.id === selected) ? selected : null;
  }, [selected, jobPicked, drillSkill, model.groupRows]);

  const capTree: CapRow[] = useMemo(() => {
    const base = model.groupRows.map((row) => ({ row, depth: 0 as const, parent: undefined }));
    if (!expandedSkill) return base;
    const i = base.findIndex((r) => r.row.id === expandedSkill);
    if (i < 0) return base;
    const kids = model.itemsBySkill.get(expandedSkill) ?? [];
    if (!kids.length) return base;
    const parent = base[i].row;
    /* 展开段用自己的一把尺子，不按父条切分。

       两种画法试过：按父条切分能守住"子条之和等于父条"这个不变量，
       读数在下钻前后不变；但技能点的分布是长尾，一项技能下前十二项加起来
       常不足一成，切下来每根子条都短到只剩钳位的那两像素，十二根一样长，
       读不出谁重谁轻 —— 而下钻要回答的正是这个问题。

       改为按本技能内的相对量标度：满格是本技能下最强的那个技能点。
       代价是子条与父条不同尺，故在展开段的段首写明这一条。 */
    const scaled = kids.map((k) => ({
      row: k,
      depth: 1 as const,
      parent: parent.id,
    }));
    return [...base.slice(0, i + 1), ...scaled, ...base.slice(i + 1)];
  }, [model.groupRows, model.itemsBySkill, expandedSkill]);

  /* 下游一律按 FlowRow 处理，层级另存一张表：改动只落在渲染那一处，
     标度、连线、选中传播、分组带的算法全都不必知道有没有展开。 */
  const capRows = useMemo(() => capTree.map((c) => c.row), [capTree]);
  /** 展开段的首末行下标：父技能行到它最后一个技能点，供归属线定两端 */
  const kidSpan = useMemo(() => {
    const a = capTree.findIndex((c) => c.depth === 1);
    if (a < 0) return null;
    let b = a;
    while (b + 1 < capTree.length && capTree[b + 1].depth === 1) b++;
    return { a: a - 1, b };
  }, [capTree]);
  const taskRows = useMemo(() => model.taskRows.slice(0, taskLimit), [model.taskRows, taskLimit]);
  const jobRows = useMemo(() => model.jobRows.slice(0, jobLimit), [model.jobRows, jobLimit]);

  /* 列宽按本屏这一批名字量，所以要等行选出来之后再排版。

     能力段量的是 groupRows 而不是展开后的 capRows：展开段里那条合计行的名字
     （“另有 N 项未列出”）比任何一项技能名都长，量进去会把
     名字列顶到上限，三根条随之各短一截，整幅图在点开的一瞬间向左错位。
     列宽只认技能这一层，展开与收起因此不改版面。 */
  const c = useMemo(
    () =>
      layout(
        width,
        colWidth(jobRows, COL_JOB),
        colWidth(taskRows, COL_TASK),
        colWidth(model.groupRows, COL_ITEM),
      ),
    [width, jobRows, taskRows, model.groupRows],
  );

  /* 三段共用同一段可用高度，各自等分 —— 见上面 laneOf 那一段 */
  const usable = Math.max(capRows.length, taskRows.length, jobRows.length, 1) * ROW;
  const height = PAD_TOP + usable + PAD_BOTTOM;
  const laneJob = useMemo(() => laneOf(jobRows.length, usable), [jobRows.length, usable]);
  const laneTask = useMemo(() => laneOf(taskRows.length, usable), [taskRows.length, usable]);
  const laneCap = useMemo(() => laneOf(capRows.length, usable), [capRows.length, usable]);
  const yJob = (i: number) => yIn(laneJob, i);
  const yTask = (i: number) => yIn(laneTask, i);
  const yCap = (i: number) => yIn(laneCap, i);

  /* ---------------- 选中行浮窗落在哪 ----------------
     只有岗位段给浮窗：跨页出口一律带着一个岗位过去，任务与技能点没有对应的落点。

     横向锚在 c.jobEnd，即岗位条的条尾，也是列头基线的右端。岗位条右对齐，
     条尾对每一行都在同一竖直线上，浮窗因此不随行长左右跳动。

     纵向默认落在该行上方。这个方向压到的是岗位段轨道区里相邻的一行，
     而选中状态下非链路行已整体退到 0.28 透明度；换成放在条尾右侧，
     压到的则是当前这条链的连线起点与任务条的头一截，代价高得多。
     上方排不下时（前两行会顶到列头）翻到下方，取舍相同。 */
  const jumpAt = (() => {
    if (!jumpDock || !selected) return null;
    const i = jobRows.findIndex((r) => r.id === selected);
    if (i < 0) return null;
    const y = yJob(i);
    return { y, below: y - BAR_H / 2 - JUMP_GAP - JUMP_H < PAD_TOP + 4 };
  })();

  /** 能力段第 i 行占的上下边界。分组带与两级括号共用，两端都不越出整列 */
  const capHalf = Math.max(laneCap.step, BAR_H + 6) / 2;
  const capTop = (i: number) => Math.max(PAD_TOP, yCap(i) - capHalf);
  const capBot = (i: number) => Math.min(PAD_TOP + usable, yCap(i) + capHalf);

  /* ---------------- 标度 ----------------
     一段一把尺子，量程取该段全时段的峰值 —— 不是当月最大值，也不是末月最大值。

     · 不按当月最大值：那样整张图会随游标一起涨缩，"这一项涨了"与"别的都跌了"
       在图上长得一模一样。
     · 不按末月最大值：要求强度走的是同层份额，早期月份同层条目还没起来，
       留在图上的那几项各自占的份额比现在大得多（软压之后仍到 1.3 倍上下）。
       尺子只量到末月，往回拖时这些条就越过自己那一列，压到左边的岗位名、
       右边的技能点名和能力组括号上 —— 图上一大片色块，读不出是哪一行的。

     量程按全时段峰值定死之后，同一根条在不同月份之间、不同条在同一个月之间
     都能直接比长短，而任何一个月都不会有条越出列宽。 */
  /* 技能这一层的量程按全时段峰值定，与另外两段同口径 */
  const maxCap = useMemo(() => spanMax(model.groupRows), [model.groupRows]);
  /* 展开段另有一把尺子，满格为本技能下最强的那个技能点。
     合计行不参与定尺：它是若干项之和，常大于任何单项，
     计入之后十二根单项条会一起被压扁，正是要避开的那种画法。 */
  const maxKid = useMemo(() => {
    const kids = capTree.filter((c) => c.depth === 1 && !c.row.id.startsWith('agg:'));
    return kids.length ? spanMax(kids.map((c) => c.row)) : 1;
  }, [capTree]);
  const maxTask = useMemo(() => spanMax(taskRows), [taskRows]);
  const maxJob = useMemo(() => spanMax(jobRows), [jobRows]);

  const capIdx = useMemo(() => new Map(capRows.map((r, i) => [r.id, i])), [capRows]);
  const taskIdx = useMemo(() => new Map(taskRows.map((r, i) => [r.id, i])), [taskRows]);
  const jobIdx = useMemo(() => new Map(jobRows.map((r, i) => [r.id, i])), [jobRows]);

  /* ---------------- 图外的选中项 ----------------

     选中项是外部给的，而它未必落在这张图的三段里：顶栏搜索、首页的前瞻信号卡片
     与本期快报都带 ?focus= 跳来，其中的叠层前瞻条目大多不在当前大类的三段内，
     技能点一层更是只在某一行技能展开后才存在。

     这类 id 一旦照旧进选中态，整幅图会被压到 0.28 的透明度，而没有任何一行
     亮着 —— 看上去像是数据没加载出来，且点哪一行都回不到正常状态。
     故两处都拦：压暗态只认落在图上的选中项；同时把这个 id 清回给外部，
     "取消选中"一类跟着选中态走的控件才不会挂在那里。 */
  const selectedOnChart =
    selected != null && (jobIdx.has(selected) || taskIdx.has(selected) || capIdx.has(selected));

  useEffect(() => {
    /* 三段全空只可能出现在筛选把这一类筛没了的时候，此时不动选中项：
       那是"图上暂时没有行"，不是"选中项无处可落" */
    if (!selected || selectedOnChart) return;
    if (jobRows.length + taskRows.length + capRows.length === 0) return;
    onSelect(null);
  }, [selected, selectedOnChart, jobRows.length, taskRows.length, capRows.length, onSelect]);

  /* ---------------- 选中传播：一次点亮整条链 ----------------
     点一个岗位 → 它的任务 → 那些任务要求的能力；
     点一项能力 → 要它的任务 → 那些任务所属的岗位。两个方向共用这一段。 */
  const lit = useMemo(() => {
    if (!selected) return null;
    const caps = new Set<string>();
    const tasks = new Set<string>();
    const jobs = new Set<string>();

    if (jobIdx.has(selected)) {
      jobs.add(selected);
      for (const t of taskRows) if ((t.links.get(selected) ?? 0) >= 0.2) tasks.add(t.id);
      for (const r of capRows) for (const tid of tasks) if ((r.links.get(tid) ?? 0) >= 0.2) caps.add(r.id);
    } else if (taskIdx.has(selected)) {
      tasks.add(selected);
      const t = taskRows.find((x) => x.id === selected)!;
      for (const [jid, w] of t.links) if (w >= 0.2) jobs.add(jid);
      for (const r of capRows) if ((r.links.get(selected) ?? 0) >= 0.2) caps.add(r.id);
    } else if (capIdx.has(selected)) {
      caps.add(selected);
      const r = capRows.find((x) => x.id === selected)!;
      for (const [tid, w] of r.links) if (w >= 0.2) tasks.add(tid);
      for (const t of taskRows) if (tasks.has(t.id)) for (const [jid, w] of t.links) if (w >= 0.2) jobs.add(jid);
    } else {
      return null;
    }
    return { caps, tasks, jobs };
  }, [selected, capRows, taskRows, capIdx, taskIdx, jobIdx]);

  /* ---------------- 连线 ----------------
     数据里存的关系方向是"下层 → 上层"（任务记着要它的岗位，能力记着要它的任务），
     画的时候按版面从左到右接：岗位条尾 → 任务条头，任务名列末 → 能力条头。 */
  interface Link {
    key: string;
    d: string;
    w: number;
    on: boolean;
  }

  /* 没有选中项时一条不画。上一版铺一层每行两条的常显底，它交代得了"三段是连着的"，
     代价是六十余行各挂两条曲线，第一眼落在连线而不是条长上，而条长才是这张图的量。
     三段并排本身已经说明了顺序，连线因此只在被问到时才画。

     选中之后画哪几段，按选中的是哪一层分：

       · 岗位：先画它到各项任务的那一束。任务到技能不画 —— 一个岗位常带十余项
         任务、四十余项技能，两束一起铺开是一片网，看不出哪一项任务要哪几项技能。
         要看这一段，点亮其中一项任务，那时只画那一项的。
       · 任务：两侧同时画。任务处在链的中间，它的上下游各只有一束。
       · 技能：只画任务到它这一束。岗位与任务之间那一束与"这项技能被谁要求"
         无关，画上去只是把图铺满。 */
  const links = useMemo(() => {
    const job: Link[] = [];
    const cap: Link[] = [];
    if (!lit || !selected) return { job, cap };

    const drawJob = !capIdx.has(selected) || jobIdx.has(selected) || taskIdx.has(selected);
    /* 岗位选中态下，任务到技能那一束只画下钻到的那一项任务 */
    const capFrom = jobPicked ? (drillTask ? new Set([drillTask]) : new Set<string>()) : lit.tasks;

    if (drawJob) {
      for (const t of taskRows) {
        if (!lit.tasks.has(t.id)) continue;
        const ti = taskIdx.get(t.id)!;
        for (const [jid, w] of t.links) {
          const ji = jobIdx.get(jid);
          if (ji === undefined || !lit.jobs.has(jid)) continue;
          job.push({
            key: `${jid}>${t.id}`,
            d: curve(c.jobEnd, yJob(ji), c.taskX, yTask(ti)),
            w: 0.5 + w * 2,
            on: true,
          });
        }
      }
    }
    for (const r of capRows) {
      if (!lit.caps.has(r.id)) continue;
      const si = capIdx.get(r.id)!;
      for (const [tid, w] of r.links) {
        const ti = taskIdx.get(tid);
        if (ti === undefined || !capFrom.has(tid)) continue;
        cap.push({
          key: `${tid}>${r.id}`,
          d: curve(c.taskEnd, yTask(ti), c.capX, yCap(si)),
          w: 0.5 + w * 2,
          on: true,
        });
      }
    }
    return { job, cap };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lit, selected, jobPicked, drillTask, capRows, taskRows, capIdx, taskIdx, jobIdx, c, laneJob, laneTask, laneCap]);

  /* 能力维度与能力组的括号：同一维度 / 同一组的连续行合成一段，名字落在段的中线上。
     它们在名字的右侧 —— 这一段整体是镜像的，越往右越粗粒度。 */
  /* 分组带与两级括号按技能这一层的归属划分。展开出来的技能点并入其父技能
     所属的组，不自成一组：它们是那一行的展开，不是同级的邻居。 */
  const spans = useMemo(() => {
    const dim: { name: string; a: number; b: number }[] = [];
    const grp: { name: string; a: number; b: number }[] = [];
    let curDim = '';
    let curGrp = '';
    capTree.forEach((c, i) => {
      if (c.depth === 0) {
        curDim = c.row.dim;
        curGrp = c.row.group;
      }
      const d = dim[dim.length - 1];
      if (d && d.name === curDim) d.b = i;
      else dim.push({ name: curDim, a: i, b: i });
      const g = grp[grp.length - 1];
      if (g && g.name === curGrp) g.b = i;
      else grp.push({ name: curGrp, a: i, b: i });
    });
    return { dim, grp };
  }, [capTree]);

  /* ---------------- 口径回报 ---------------- */
  const info = useMemo<FlowLayoutInfo>(() => {
    const changed = { new: 0, up: 0, down: 0, gone: 0 };
    let absent = 0;
    let litN = 0;
    for (const r of [...capRows, ...taskRows, ...jobRows]) {
      const a = rowAt(r, cursor, baseline);
      if (a.value === null) absent += 1;
      /* 第三段现在画到技能这一层，已确认的计数随之改数技能行；
         展开出来的技能点是这一行的下钻，不重复计入。合计行不是条目，同样不计。 */
      if (r.kind === 'skill' && !r.id.startsWith('agg:') && a.confirmed && a.value !== null) litN += 1;
      if (a.change && a.change !== 'flat') changed[a.change] += 1;
    }
    return {
      capRows: capRows.length,
      taskRows: taskRows.length,
      jobRows: jobRows.length,
      lit: litN,
      absent,
      changed,
    };
  }, [capRows, taskRows, jobRows, cursor, baseline]);

  /* 回报口径给页面。依赖只挂序列化后的值：info 每次渲染都是新对象，
     挂对象本身会在父组件 setState 之后立刻再触发一次，形成来回。 */
  const infoKey = JSON.stringify(info);
  useEffect(() => {
    onLayoutInfo?.(JSON.parse(infoKey) as FlowLayoutInfo);
  }, [infoKey, onLayoutInfo]);

  /* ---------------- 一根条 ----------------
     当月值画实心，基准月的差额画在条尾：增量接长一段深色，收缩画一段斜纹。
     当月不在图谱里则只留一条点线空轨道 —— 与"当月测得为零"必须分得开。 */
  const Bar = ({
    row,
    yc,
    x0,
    max,
    span,
    dir,
    segs,
    height: barH,
  }: {
    row: FlowRow;
    yc: number;
    x0: number;
    max: number;
    span: number;
    /** 1 = 向右长（任务、能力），-1 = 向左长（岗位段右对齐，条尾贴着连线） */
    dir: 1 | -1;
    segs: { v: number; color: string }[];
    /** 下钻展开的技能点条比技能条细一档，除此之外三段一律用 BAR_H */
    height?: number;
  }) => {
    /** 条高三段一致，不由本段行距定 —— 见上面 laneOf 那一段 */
    const h = barH ?? BAR_H;
    const a = rowAt(row, cursor, baseline);
    /* 钳在列宽以内是兜底：量程已按全时段峰值取，正常数据不会触发；
       真触发了说明取数层给出了超出量程的值，宁可条顶到列尾，也不许压到隔壁列的字上 */
    const fit = (v: number) => Math.min(Math.max((v / max) * span, 2), span);
    const len = a.value === null ? 0 : fit(a.value);
    const baseLen = a.base === null ? null : fit(a.base);
    const start = dir === 1 ? x0 : x0 - len;
    const sum = segs.reduce((s, x) => s + x.v, 0) || 1;
    const top = yc - h / 2;

    /* 轨道底：整段列宽的一条浅槽。条只占其中一截，槽把"这一行到哪儿为止"交代清楚 ——
       没有它，短条就是一列长短不一的碴儿，读者也无从判断条离满格还有多远。 */
    const track = (
      <rect
        className="jcf-track"
        x={dir === 1 ? x0 : x0 - span}
        y={top}
        width={span}
        height={h}
        rx={2}
      />
    );

    if (a.value === null) {
      // 空轨道：当月这一项还不在图谱里
      const w = baseLen ?? 12;
      return (
        <>
          {track}
          <rect
            className="jcf-absent"
            x={dir === 1 ? x0 : x0 - w}
            y={top}
            width={w}
            height={h}
            rx={2}
          />
        </>
      );
    }

    let acc = start;
    const body = segs.map((s, k) => {
      const w = (len * s.v) / sum;
      const x = acc;
      acc += w;
      return w > 0.4 ? (
        <rect key={k} className="jcf-seg" x={x} y={top} width={w} height={h} fill={s.color} />
      ) : null;
    });

    /* 增减：接在条尾，不改条本身的长度 —— 条长永远读的是"当月是多少" */
    let delta: React.ReactNode = null;
    if (baseLen !== null && a.change && a.change !== 'flat' && a.change !== 'new') {
      const d = len - baseLen;
      if (Math.abs(d) > 1) {
        const w = Math.abs(d);
        const x = d > 0 ? (dir === 1 ? start + len - w : start) : dir === 1 ? start + len : start - w;
        delta =
          d > 0 ? (
            <rect className="jcf-up" x={x} y={top} width={w} height={h} />
          ) : (
            <rect className="jcf-down" x={x} y={top} width={w} height={h} />
          );
      }
    }

    return (
      <>
        {track}
        {body}
        {delta}
        <rect
          className={a.confirmed ? 'jcf-barout' : 'jcf-barout wait'}
          x={start}
          y={top}
          width={Math.max(len, 1)}
          height={h}
        />
        {a.change === 'new' && (
          <rect
            className="jcf-new"
            x={start - 1.5}
            y={top - 1.5}
            width={Math.max(len, 1) + 3}
            height={h + 3}
            rx={2.5}
          />
        )}
      </>
    );
  };

  /** 变化标记：行首一个小三角或方块，灰度打印下也分得开 */
  const Mark = ({ change, x, yc }: { change: ChangeKind | null; x: number; yc: number }) => {
    if (!change || change === 'flat') return null;
    if (change === 'up') return <path className="jcf-mk up" d={`M${x},${yc + 3} L${x + 5},${yc + 3} L${x + 2.5},${yc - 2.5} Z`} />;
    if (change === 'down') return <path className="jcf-mk down" d={`M${x},${yc - 3} L${x + 5},${yc - 3} L${x + 2.5},${yc + 2.5} Z`} />;
    if (change === 'new') return <rect className="jcf-mk new" x={x} y={yc - 2.6} width={5.2} height={5.2} rx={1} />;
    return <path className="jcf-mk gone" d={`M${x},${yc - 2.6} L${x + 5.2},${yc + 2.6} M${x + 5.2},${yc - 2.6} L${x},${yc + 2.6}`} />;
  };

  /** 下钻到的那一行另加一档：它比同为"关联行"的邻居更进一步，
      但仍不是选中项 —— 选中项始终是那个岗位 */
  const drilled = (id: string) => id === drillTask || id === drillSkill;

  const rowCls = (r: FlowRow, set?: Set<string>) =>
    `jcf-row${selected === r.id ? ' on' : ''}${set?.has(r.id) && selected !== r.id ? ' lit' : ''}${drilled(r.id) ? ' sub' : ''
    }`;

  /* 点一行发生什么，取决于当前选中的是哪一层，以及被点的这一行与它是否关联。

     岗位选中态下，点它关联的任务或技能只改下钻那一层，岗位不失选；
     点未关联的行才换选中项 —— 那时读者问的已经是另一条链了。
     其余情形照旧：点同一行取消，点别的行选中它。 */
  const pick = (r: FlowRow) => {
    /* 技能点是某一行技能展开出来的子行，本身不构成一个可选中的对象。

       这一条要放在最前面。此前它只写在"岗位处于选中态"那一支里：技能选中态下
       点开展开段再点其中一个技能点，会走到末尾的 onSelect(技能点 id) ——
       而选中项一旦不是技能行，展开段随即收起，这个 id 在三段里都找不到，
       点亮集因而为空，整幅图被置暗，且点哪儿都回不来。 */
    if (r.kind === 'skillpoint') return;
    if (selected === r.id) {
      onSelect(null);
      return;
    }
    if (jobPicked && lit) {
      if (r.kind === 'task' && lit.tasks.has(r.id)) {
        setDrillTask((v) => (v === r.id ? null : r.id));
        return;
      }
      if (r.kind === 'skill' && lit.caps.has(r.id)) {
        setDrillSkill((v) => (v === r.id ? null : r.id));
        return;
      }
    }
    onSelect(r.id);
  };

  const handlers = (r: FlowRow) => ({
    onClick: () => pick(r),
    onMouseMove: (e: React.MouseEvent) => onTip(e, r),
    onMouseLeave: () => onTip({} as React.MouseEvent, null),
  });

  return (
    <div className="jcf">
      <svg
        width={c.w}
        height={height}
        className={`jcf-svg${selectedOnChart ? ' picked' : ''}`}
        role="img"
        aria-label="领域内岗位、任务与能力要求的联动全景"
      >
        <defs>
          <pattern id="jcf-hatch" width="4.5" height="4.5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="4.5" height="4.5" fill="var(--panel)" />
            <line x1="0" y1="0" x2="0" y2="4.5" stroke="var(--ink-3)" strokeWidth="1.6" opacity="0.5" />
          </pattern>
          {/* 要求程度的第四档"无法确定"。它不是色阶上的第四级 —— 那三级量的是
              "有多吃重"，这一档说的是"原文没写程度词"，两者不在同一条量纲上。
              于是不给它色阶里的任何一档，改成点阵：与三段实心档的区分靠有无
              填充，不靠色差（实测中性灰与中间那档青在红色盲下 ΔE 只有 3.4，
              给它一个灰色块等于让两档在色觉缺陷下合并）。点阵同时把这一档
              压到视觉后景 —— 没测到的东西不该比测到的更显眼。 */}
          <pattern id="jcf-unknown" width="4" height="4" patternUnits="userSpaceOnUse">
            <rect width="4" height="4" fill="var(--panel)" />
            <circle cx="1" cy="1" r="0.85" fill="#7d8798" opacity="0.75" />
          </pattern>
          {/* 下钻展开时的合计条：一批未单列的技能点之和。它不是一项条目，
              画成实心会与单项混同；斜纹交代"这一段是若干项加起来的"。 */}
          <pattern id="jcf-agg" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="5" height="5" fill="var(--panel)" />
            <line x1="0" y1="0" x2="0" y2="5" stroke={PROF_COLORS[1]} strokeWidth="2.2" opacity="0.55" />
          </pattern>
        </defs>

        {/* ---- 列头。三段各占一格，段名压一条与本段列宽等长的基线。

                上一版列头 13px，与图内的能力组名同级，整幅图没有一处比别处
                更重 —— 一张主图第一眼总要先落在某个地方。这一版段名放到
                18px 加粗、编码说明另起一行退到 11px 灰字，段名与说明之间
                拉开层级；序号章标压在段名前面，让"岗位 → 核心任务 → 技能点"
                这条链在列头这一行就读得出来，不必等看完三列条。 ---- */}
        {(
          [
            { k: 'job', n: '1', x0: c.jobName - 6, x1: c.jobEnd, label: '岗位', sub: '条长 = 要求总量 · 行序 = 招聘条数' },
            /* 段名写"任务"而不是"核心任务"：这一段现在画的是所选大类关联到的
               全部任务，不再只取要求最重的前若干项，"核心"二字与所画的不符 */
            { k: 'task', n: '2', x0: c.taskX, x1: c.taskEnd, label: '任务', sub: '条长 = 要求强度 · 分段 = 前瞻追加' },
            {
              k: 'cap',
              n: '3',
              x0: c.capX,
              x1: c.capEnd,
              label: '技能',
              /* 第三段画到技能这一层，技能点为下钻的落点。列头把这件事写在
                 段名底下，读者不必先点一下才知道这一段还能往里走。 */
              sub: expandedSkill
                ? '条长 = 要求强度 · 展开项为该技能下的技能点'
                : '条长 = 要求强度 · 分段 = 要求程度 · 点击展开技能点',
            },
          ] as const
        ).map((h) => (
          <g key={h.k} className={`jcf-head ${h.k}`}>
            {/* 序号的圆底是一个真正的 circle。上一版用粗描边模拟圆，
                描边沿字形外扩，"1"扩成窄胶囊、"3"的镂空被填平后读作"8"。 */}
            <circle className="jcf-colbadge" cx={h.x0 + 11} cy={17} r={9} />
            <text className="jcf-colnum" x={h.x0 + 11} y={17}>
              {h.n}
            </text>
            <text className="jcf-colhead" x={h.x0 + 26} y={17}>
              {h.label}
            </text>
            <text className="jcf-colsub" x={h.x0 + 2} y={40}>
              {h.sub}
            </text>
            <line className="jcf-colrule" x1={h.x0} y1={51} x2={h.x1} y2={51} />
          </g>
        ))}

        {/* ---- 连线。只在选中之后画，画哪几束见上方 links 一段 ---- */}
        <g className="jcf-links">
          {links.job.map((l) => (
            <path key={l.key} className={l.on ? 'jd on' : 'jd'} d={l.d} strokeWidth={l.w} />
          ))}
          {links.cap.map((l) => (
            <path key={l.key} className={l.on ? 'on' : undefined} d={l.d} strokeWidth={l.w} />
          ))}
        </g>

        {/* ---- A1 岗位 ---- */}
        <g className="jcf-jobs">
          {jobRows.map((r, i) => {
            const yc = yJob(i);
            const a = rowAt(r, cursor, baseline);
            const f = fitText(r.name, c.jobW - COL_JOB.pad, 12);
            const mix = r.mix ?? { hard: 1, soft: 0 };
            return (
              <g key={r.id} className={rowCls(r, lit?.jobs)} {...handlers(r)} aria-label={r.name}>
                <rect
                  className="jcf-hit"
                  x={c.jobName - 6}
                  y={yc - laneJob.hit / 2}
                  width={c.jobEnd - c.jobName + 8}
                  height={laneJob.hit}
                />
                <Mark change={a.change} x={c.jobName - 4} yc={yc} />
                <text className="jcf-name" x={c.jobName + 6} y={yc + 1} style={{ fontSize: `${f.size}px` }}>
                  {f.text}
                </text>
                <Bar
                  row={r}
                  yc={yc}
                  x0={c.jobEnd}
                  max={maxJob}
                  span={c.barMax}
                  dir={-1}
                  segs={SKILL_TYPES.map((t) => ({ v: mix[t.v], color: MIX_COLORS[t.v] }))}
                />
              </g>
            );
          })}
        </g>

        {/* ---- A2 核心任务 ---- */}
        <g className="jcf-tasks">
          {taskRows.map((r, i) => {
            const yc = yTask(i);
            const a = rowAt(r, cursor, baseline);
            const f = fitText(r.name, c.taskW - COL_TASK.pad, 12);
            return (
              <g key={r.id} className={rowCls(r, lit?.tasks)} {...handlers(r)} aria-label={r.name}>
                <rect
                  className="jcf-hit"
                  x={c.taskX - 4}
                  y={yc - laneTask.hit / 2}
                  width={c.taskEnd - c.taskX + 6}
                  height={laneTask.hit}
                />
                <Bar
                  row={r}
                  yc={yc}
                  x0={c.taskX}
                  max={maxTask}
                  span={c.barMax}
                  dir={1}
                  segs={[
                    { v: 1 - r.forwardShare, color: TASK_BASE },
                    { v: r.forwardShare, color: TASK_FORE },
                  ]}
                />
                <Mark change={a.change} x={c.taskName + 2} yc={yc} />
                <text className="jcf-name" x={c.taskName + 13} y={yc + 1} style={{ fontSize: `${f.size}px` }}>
                  {f.text}
                </text>
              </g>
            );
          })}
        </g>

        {/* ---- A3 能力体系。这一段整体镜像：越往右越粗粒度（技能点 → 组 → 维度） ---- */}
        <g className="jcf-caps">
          {/* 能力组的分组带：隔一组铺一条淡底。右侧那道括号只画在名字外，
              隔着一列条读起来要来回找；一条底带把"这几行属于同一组"直接压在行上。 */}
          {spans.grp.map((s, k) =>
            k % 2 === 1 ? (
              <rect
                key={`band-${s.name}`}
                className="jcf-band"
                x={c.capX - 4}
                y={capTop(s.a)}
                width={c.capDim - c.capX + 4}
                height={capBot(s.b) - capTop(s.a)}
              />
            ) : null,
          )}
          {/* 下钻的归属线：从展开的技能行落到它最后一个技能点上，
              左侧一条竖线加逐行的横向短线，交代"这几行是上面那一行拆开来的"。
              没有它，展开出来的几行与相邻的技能行在版面上看不出层级差别。 */}
          {kidSpan && (
            <g className="jcf-kidline">
              <path
                d={`M${c.capName + 6},${yCap(kidSpan.a) + 2} L${c.capName + 6},${yCap(kidSpan.b)}`}
              />
              {capTree.map((cr, i) =>
                cr.depth === 1 ? (
                  <path key={`kl-${cr.row.id}`} d={`M${c.capName + 6},${yCap(i)} h6`} />
                ) : null,
              )}
            </g>
          )}
          {capTree.map((cr, i) => {
            const r = cr.row;
            const kid = cr.depth === 1;
            const yc = yCap(i);
            const a = rowAt(r, cursor, baseline);
            const isAgg = r.id.startsWith('agg:');
            /* 展开的行往右缩进一档，名字预算相应收窄 */
            const indent = kid ? 14 : 0;
            const f = fitText(r.name, c.itemW - COL_ITEM.pad - indent, kid ? 11 : 12);
            const psum = r.prof.reduce((a2, b) => a2 + b, 0) || 1;
            const canDrill = !kid && (model.itemsBySkill.get(r.id)?.length ?? 0) > 0;
            const open = !kid && r.id === expandedSkill;
            return (
              <g
                key={r.id}
                className={`${rowCls(r, lit?.caps)}${kid ? ' kid' : ''}${open ? ' open' : ''}`}
                {...handlers(r)}
                aria-label={r.name}
              >
                <rect
                  className="jcf-hit"
                  x={c.capX - 4}
                  y={yc - laneCap.hit / 2}
                  width={c.capGroup - c.capX}
                  height={laneCap.hit}
                />
                {/* 合计行不画条：它是一批技能点的和，与单项不同尺，
                    画出来只会顶到列尾，把十二根单项条一起压扁。其条数与占比写在名字里。 */}
                {!isAgg && (
                  <Bar
                    row={r}
                    yc={yc}
                    x0={c.capX}
                    max={kid ? maxKid : maxCap}
                    span={c.barMax}
                    dir={1}
                    height={kid ? BAR_KID_H : BAR_H}
                    segs={
                      kid
                        ? /* 技能点一层不分档。熟练度产出到“某岗位对某技能”这一粒度为止，
                             算法侧不产出技能点自己的档位读数；照技能行分成四段，画的是
                             父技能的构成，读的人却会当成这个技能点自己的程度要求。
                             展开段因此只留条长一个编码：这项技能下有哪些技能点、各占多少。 */
                        [{ v: 1, color: 'var(--lay-skill-ink)' }]
                        : PROF_LEVELS.map((_, k) => ({
                          v: r.prof[k] / psum,
                          // 第四档画点阵，不给色阶里的颜色 —— 见 defs 里 jcf-unknown 那一段
                          color: k === PROF_UNKNOWN ? 'url(#jcf-unknown)' : PROF_COLORS[k],
                        }))
                    }
                  />
                )}
                <Mark change={a.change} x={c.capName + 2} yc={yc} />
                {/* 可下钻的技能行在名字前给一枚指示：闭合时朝右，展开时朝下 */}
                {canDrill && (
                  <path
                    className="jcf-caret"
                    d={
                      open
                        ? `M${c.capName + 9},${yc - 1.6} l3.4,4 l3.4,-4 z`
                        : `M${c.capName + 10.4},${yc - 3.4} l4,3.4 l-4,3.4 z`
                    }
                  />
                )}
                <text
                  className={kid ? 'jcf-name kid' : 'jcf-name'}
                  x={c.capName + 13 + indent + (canDrill ? 9 : 0)}
                  y={yc + 1}
                  style={{ fontSize: `${f.size}px` }}
                >
                  {f.text}
                </text>
              </g>
            );
          })}

          {/* 括号的两端顶到该段首末行的行边界上，不是行心 ——
              行距一变宽，括号跟着长，才始终"框住"它管的那几行 */}
          {spans.grp.map((s) => {
            const a0 = capTop(s.a) + 2;
            const b1 = capBot(s.b) - 2;
            const mid = (a0 + b1) / 2;
            const f = fitText(s.name, W_GROUP - 12, 12);
            return (
              <g key={s.name}>
                <path
                  className="jcf-brace"
                  d={`M${c.capGroup + 4},${a0} L${c.capGroup},${a0} L${c.capGroup},${b1} L${c.capGroup + 4},${b1}`}
                />
                <text className="jcf-grp" x={c.capGroup + 8} y={mid} style={{ fontSize: `${f.size}px` }}>
                  {f.text}
                </text>
              </g>
            );
          })}

          {spans.dim.map((s) => {
            const a0 = capTop(s.a) + 1;
            const b1 = capBot(s.b) - 1;
            const mid = (a0 + b1) / 2;
            return (
              <g key={s.name}>
                <path
                  className="jcf-brace"
                  d={`M${c.capDim + 10},${a0} L${c.capDim + 5},${a0} L${c.capDim + 5},${b1} L${c.capDim + 10},${b1}`}
                />
                <text className="jcf-dim" x={c.capDim + 22} y={mid} transform={`rotate(-90 ${c.capDim + 22} ${mid})`}>
                  {s.name}
                </text>
              </g>
            );
          })}
        </g>
      </svg>

      {/* 浮窗本体是一层 HTML，不进 svg：它有阴影、圆角与悬停提示，
          还要能被 Tab 走到，这几样在 svg 里都要另造一遍。
          外层是一个零尺寸的锚点，内层贴着它右对齐，浮窗因此不受 .jcf 实际
          宽度影响：窄屏下 svg 比容器宽，按容器右缘算会偏出一截。
          key 挂选中项，换一个岗位即重播一次入场，浮窗不在两行之间滑行。
          两个类名与另外两页共用，见 styles/jumpdock.css。 */}
      {jumpAt && (
        <div key={selected ?? ''} className="jdk-at" style={{ left: c.jobEnd, top: jumpAt.y }}>
          <div className={jumpAt.below ? 'jdk-wrap below' : 'jdk-wrap'}>{jumpDock}</div>
        </div>
      )}
    </div>
  );
}
