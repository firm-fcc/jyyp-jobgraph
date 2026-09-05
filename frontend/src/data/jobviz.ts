/* ============================================================
   JobViz 复刻层 —— 论文界面本身需要、而 explore.ts 里没有的那几件量

   explore.ts 已经把图谱数据整理成论文的对象形状（技能框架 × 岗位 × 属性，
   对应表见该文件顶部）。这里补的四件事都与取数无关，只与论文那套画法有关：

     · 能力体系三级树        Figure 2(A1) 的左半：维度 → 能力组 → 技能点
     · 省—市两级词表         属性栏 Location 下的 Provinces / Cities 两级下拉
     · 簇字形落位            论文 collision_detection：二维投影后按半径互斥推开
     · 岗位分布图的格与落点  论文 get_radar_r / get_radar_axis：格内不重叠随机散点

   落位这两件事论文里用 Math.random，刷新一次换一个样子。这里改成按键派生的
   确定性伪随机（utils/rng）：同一份筛选每次进来落点一致。这张图的用法之一
   就是"记住某个字形在哪儿、回头再看一眼"，每次重渲染都跳一下就没法用了。
   ============================================================ */

import { PROVINCES_ALL, PROVINCE_OTHER, citiesOf } from './provinces';
import { dimRank, exploreBase, type ClusterInfo, type PostCell } from './explore';
import { hashStr, mulberry32 } from '@/utils/rng';

/* ==================== 能力体系三级树 ==================== */

export interface TreeGroup {
  id: string;
  name: string;
  /** 组内技能 id */
  items: string[];
}

export interface TreeDim {
  name: string;
  groups: TreeGroup[];
}

let _tree: TreeDim[] | null = null;

/**
 * 维度 → 能力组 → 技能。
 *
 * 论文的三级是"2 类 → 8 组 → 23 项"，这里是"2 维度 → 9 组 → 49 项"，
 * 层数与画法一致，只是每层的条目多一些。
 *
 * 末一级为技能而非技能点：技能点是随市场文本生长的开放集合，本批逾两万项，
 * 在这一列铺开时行高压到一像素以下，需求条之间读不出长短；
 * 技能则是封闭体系，五十余项恰好排满这一列。
 */
export function skillTree(): TreeDim[] {
  if (_tree) return _tree;
  const base = exploreBase();
  const byDim = new Map<string, Map<string, string[]>>();
  for (const a of base.axes) {
    const dim = byDim.get(a.dim) ?? new Map<string, string[]>();
    const arr = dim.get(a.group) ?? [];
    arr.push(a.id);
    dim.set(a.group, arr);
    byDim.set(a.dim, dim);
  }
  /* 维度次序按软硬两态取（技术在前），与右侧需求条的排序同源，见 explore.dimRank */
  const typeOfDim = new Map(base.axes.map((a) => [a.dim, a.type]));
  _tree = [...byDim.entries()]
    .sort(([a], [b]) => dimRank(typeOfDim.get(a)) - dimRank(typeOfDim.get(b)))
    .map(([name, groups]) => ({
      name,
      groups: [...groups.entries()].map(([gname, items]) => ({ id: `G:${gname}`, name: gname, items })),
    }));
  return _tree;
}

/* ==================== 省 — 市 ====================
   论文属性栏的 Location 是两级下拉：先选省，再在该省的市里勾选。
   省份一级即属性栏上画的那一维，市一级只在下拉里出现 —— 勾掉一座市，
   它那部分条数从所属省份的条上退出去。

   两级都取自行政区划表（data/provinces.ts），不从数据里推断：
   一座城属于哪个省是既定事实，与本批数据无关。 */

export interface Province {
  name: string;
  cities: string[];
}

export const PROVINCES: Province[] = [...PROVINCES_ALL, PROVINCE_OTHER].map((name) => ({
  name,
  cities: citiesOf(name),
}));

/** 论文的 salary_limit：属性栏最多同时列这么多行，再多就读不出来了 */
export const ATTR_ROW_LIMIT = 23;

/* ==================== 地平线图的层色 ====================
   论文用一条与簇色无关的紫—靛阶：簇色管"这是哪一簇"，层色管"这一档里有多少条"。
   两件事各用一个通道，叠在同一个字形里才不会互相盖过去。
   取值沿用论文 radarColor.level1–10，它本身就落在本系统的 --violet 色族里。 */
export const HORIZON_COLORS = [
  '#e3d8ff',
  '#c7beff',
  '#aba3ff',
  '#8e89fd',
  '#7473e6',
  '#5b5ece',
  '#4049b5',
  '#1f369e',
  '#052184',
  '#000b66',
];

/* ==================== 簇字形落位 ==================== */

/**
 * 簇字形半径。论文的 get_cluster_radius：让 k 个字形按外接正方形铺满画布，
 * 再退一档留出间隙。字形太小看不出形状，太大互相压住，所以两头都封顶。
 */
export function clusterRadius(k: number, w: number, h: number): number {
  if (k <= 0) return 24;
  const n = Math.sqrt((w * h) / (4 * k)) - 6;
  return Math.max(18, Math.min(40, n));
}

interface Placed {
  x: number;
  y: number;
  weight: number;
}

/**
 * 论文的 collision_detection：先把二维投影铺满画布，再把互相压住的两个字形
 * 沿连线推开，推的幅度按对方的岗位数反比分配（大簇稳、小簇让）。
 * 论文写成 do…while(tag) 无上限循环，这里给一个迭代上限 —— 极端摆位下
 * 互斥条件可能永远满足不了，那时候宁可留一点重叠也不能把主线程卡死。
 */
export function layoutClusters(
  clusters: ClusterInfo[],
  w: number,
  h: number,
  r: number,
  seedKey: string,
): [number, number][] {
  const rnd = mulberry32(hashStr(`cluster|${seedKey}|${clusters.length}`));
  const jitter = () => rnd() - 0.5;
  const space = 5;
  const lo = r + space;
  const hiX = w - r - space;
  const hiY = h - r - space;

  const pts: Placed[] = clusters.map((c) => ({
    x: c.xy[0] * w,
    y: c.xy[1] * h,
    weight: Math.max(c.jobIds.length, 1),
  }));

  const clampIn = (p: Placed) => {
    if (p.x < lo) p.x = lo + jitter();
    if (p.x > hiX) p.x = hiX + jitter();
    if (p.y < lo) p.y = lo + jitter();
    if (p.y > hiY) p.y = hiY + jitter();
  };
  for (const p of pts) clampIn(p);

  const need = 2 * r + space;
  for (let it = 0; it < 400; it++) {
    let moved = false;
    for (let i = 0; i < pts.length; i++) {
      for (let j = i + 1; j < pts.length; j++) {
        const a = pts[i];
        const b = pts[j];
        const d = Math.hypot(a.x - b.x, a.y - b.y) || 0.001;
        if (d >= need) continue;
        moved = true;
        const dx = (need * (a.x - b.x)) / d;
        const dy = (need * (a.y - b.y)) / d;
        const tw = a.weight + b.weight;
        a.x += (dx * b.weight) / tw + jitter();
        a.y += (dy * b.weight) / tw + jitter();
        b.x -= (dx * a.weight) / tw + jitter();
        b.y -= (dy * a.weight) / tw + jitter();
        clampIn(a);
        clampIn(b);
      }
    }
    if (!moved) break;
  }
  return pts.map((p) => [p.x, p.y]);
}

/* ==================== 岗位分布图 ==================== */

export interface MapAxis {
  key: string;
  /** 起点（列为 x，行为 y） */
  at: number;
  /** 长度（列为宽，行为高） */
  size: number;
  count: number;
}

export interface MapGlyph {
  cell: PostCell;
  x: number;
  y: number;
  r: number;
}

export interface PostMapLayout {
  cols: MapAxis[];
  rows: MapAxis[];
  glyphs: MapGlyph[];
  /** 坐标轴刻度带的宽度 / 高度 */
  gutter: number;
}

/**
 * 单个字形的半径。论文 get_radar_r 用步进 0.1 的循环逼近，条件是
 * "n 个外接正方形（各留一格间隙）三倍面积仍装得下"，这里直接解出来：
 *   (n+1)² · 4 · N · 3 ≤ 格面积   ⇒   n ≤ √(面积 / 12N) − 1
 * 另外两条封顶（不超过格宽 / 格高的一半、上限 30）与论文一致。
 */
function radarR(w: number, h: number, n: number): number {
  if (n <= 0) return 0;
  let r = Math.sqrt((w * h) / (12 * n)) - 1;
  r = Math.min(r, w / 2, h / 2, 30);
  if (r > 10 && n > 40) r -= 5;
  return Math.max(r, 2.5);
}

/**
 * 格内落点。论文用带拒绝的随机采样铺不重叠的点，重试到成功为止；
 * 这里改成确定性伪随机 + 有限次重试，试满仍冲突就取"离已放点最远"的那一次 ——
 * 让某一格挤一点，好过整页卡在一个采样循环里。
 */
function scatter(
  n: number,
  r: number,
  x0: number,
  y0: number,
  w: number,
  h: number,
  seedKey: string,
): [number, number][] {
  const rnd = mulberry32(hashStr(seedKey));
  const out: [number, number][] = [];
  const pad = r + 1;
  const spanW = Math.max(w - 2 * pad, 1);
  const spanH = Math.max(h - 2 * pad, 1);
  const need = 2 * (r + 1);

  for (let i = 0; i < n; i++) {
    let best: [number, number] = [x0 + pad, y0 + pad];
    let bestGap = -Infinity;
    for (let t = 0; t < 60; t++) {
      const x = x0 + pad + rnd() * spanW;
      const y = y0 + pad + rnd() * spanH;
      let gap = Infinity;
      for (const p of out) gap = Math.min(gap, Math.hypot(x - p[0], y - p[1]));
      if (gap >= need) {
        best = [x, y];
        bestGap = Infinity;
        break;
      }
      if (gap > bestGap) {
        bestGap = gap;
        best = [x, y];
      }
    }
    out.push(best);
  }
  return out;
}

/**
 * 论文 Figure 2(B2)：横轴学历门槛、纵轴薪资档，格的宽高按落在该列 / 该行的
 * 岗位数分配（各列先各得一个基准宽度，余下的按占比摊）。
 * 同格内的岗位随机散开，位置本身不编码任何量 —— 这一点与论文一致。
 */
export function postMapLayout(
  cells: PostCell[],
  columns: string[],
  bands: string[],
  w: number,
  h: number,
  seedKey: string,
): PostMapLayout {
  const gutter = 20;
  /* 下缘那条列名带比左缘宽：列名是学历门槛的全称（最长的"高中及中专"五字），
     窄列上仍要折两行写，这条带子要够两行的高度。 */
  const gutterBottom = 30;
  const base = 56;

  const colCount = new Map<string, number>();
  const rowCount = new Map<string, number>();
  for (const c of cells) {
    colCount.set(c.cc, (colCount.get(c.cc) ?? 0) + 1);
    rowCount.set(c.band, (rowCount.get(c.band) ?? 0) + 1);
  }
  const usedCols = columns.filter((c) => colCount.has(c));
  const usedRows = bands.filter((b) => rowCount.has(b));
  const total = Math.max(cells.length, 1);

  /* 刻度带各占一条 gutter：行名竖排在左缘，列名横排在下缘。
     两条带子都要从格区里让出来，否则最后一行的字形会压在列名上。 */
  const build = (
    keys: string[],
    count: Map<string, number>,
    span: number,
    start: number,
    pad: number,
  ): MapAxis[] => {
    const free = Math.max(span - pad - base * keys.length, 0);
    let at = start;
    return keys.map((k) => {
      const n = count.get(k) ?? 0;
      const size = base + (free * n) / total;
      const a: MapAxis = { key: k, at, size, count: n };
      at += size;
      return a;
    });
  };

  const cols = build(usedCols, colCount, w, gutter, gutter);
  const rows = build(usedRows, rowCount, h, 0, gutterBottom);

  const glyphs: MapGlyph[] = [];
  for (const col of cols) {
    for (const row of rows) {
      const mine = cells.filter((c) => c.cc === col.key && c.band === row.key);
      if (!mine.length) continue;
      const r = radarR(col.size, row.size, mine.length);
      const pts = scatter(
        mine.length,
        r,
        col.at,
        row.at,
        col.size,
        row.size,
        `${seedKey}|${col.key}|${row.key}|${mine.length}`,
      );
      mine.forEach((cell, i) => glyphs.push({ cell, x: pts[i][0], y: pts[i][1], r }));
    }
  }
  return { cols, rows, glyphs, gutter };
}

/* ==================== 详情表 ====================
   论文 Figure 2(C) 逐条列出所选招聘信息：公司、技术方向、地点、薪资、学历、
   经验，外加四段可展开的长文（公司简介 / 岗位职责 / 岗位信息 / 任职要求）。

   本系统的一条落点不是一则广告，而是"某岗位落在某学历门槛 × 某薪资档的一批
   招聘信息"，所以这一栏列的是这批信息的口径：岗位本身的定义与职责照列，
   公司名这类逐条字段没有对应物，不编 —— 换成同一位置上确实有的那几项。 */

export interface DetailRow {
  label: string;
  value: string;
  /** 长文行：默认折起，点一下才展开（论文的 "(Click and expand details)"） */
  long?: boolean;
}

export interface DetailRecord {
  id: string;
  title: string;
  color: string;
  vector: number[];
  rows: DetailRow[];
}

/* “其他”一档收的是行政区划对照表未登记的城市，不是一个省；
   这一处要答的是“主要在哪个省”，故不取它 */
const topKey = (dist: Record<string, number> | undefined) => {
  if (!dist) return '—';
  let best = '—';
  let v = -1;
  for (const [k, p] of Object.entries(dist)) {
    if (k === PROVINCE_OTHER) continue;
    if (p > v) {
      v = p;
      best = k;
    }
  }
  return best;
};

const pctOf = (dist: Record<string, number> | undefined, key: string) =>
  dist && dist[key] !== undefined ? `${Math.round(dist[key] * 100)}%` : '—';

const withPct = (dist: Record<string, number> | undefined) => {
  const k = topKey(dist);
  return k === '—' ? '—' : `${k}（${pctOf(dist, k)}）`;
};

export function postDetail(cell: PostCell, color: string): DetailRecord {
  const base = exploreBase();
  const job = base.jobs.get(cell.jobId);
  const node = base.nodeById.get(cell.jobId);
  const a = job?.attrs;
  const join = (arr?: string[]) => (arr && arr.length ? arr.join('；') : '—');

  return {
    id: `${cell.jobId}|${cell.cc}|${cell.band}`,
    title: cell.jobName,
    color,
    vector: cell.vector,
    rows: [
      /* 前三行是所选落点这一格的坐标与量，其后各行是该岗位全体招聘信息的口径。
         两段口径答的不是同一个问题：一个岗位在图上通常有若干落点（凡占该岗位
         四个百分点以上的学历档各得一列），点中硕士那一列时本行即为硕士，而
         "主要学历"仍是全体条目里占比最高的那一档，二者不同并非相互矛盾。
         "本格"前缀原只加在条数一行，学历与薪资两行因而读来像是岗位属性，
         与下方的"主要学历"正面冲突。三行统一前缀之后，口径即自明。 */
      { label: '本格学历门槛', value: cell.cc },
      { label: '本格薪资档', value: cell.band },
      { label: '本格招聘信息条数', value: `${Math.round(cell.posts).toLocaleString()} 条` },
      { label: '岗位类别', value: job?.cluster ?? '—' },
      { label: '岗位招聘信息总数', value: `${Math.round(job?.posts ?? 0).toLocaleString()} 条` },
      { label: '薪资中位数', value: a ? `${a.medianSalary}k / 月` : '—' },
      { label: '主要省份', value: withPct(a?.cities) },
      { label: '主要学历', value: withPct(a?.degrees) },
      { label: '主要经验', value: withPct(a?.experience) },
      {
        label: '软硬构成',
        value: job
          ? `硬 ${Math.round(job.mix.hard * 100)}% · 软 ${Math.round(job.mix.soft * 100)}%`
          : '—',
      },
      { label: '平台职能名', value: node?.funtypes?.length ? node.funtypes.join('、') : '—' },
      { label: '判定关键词', value: node?.keywords?.length ? node.keywords.join('、') : '—', long: true },
      { label: '岗位定义', value: node?.definition ?? '—', long: true },
      /* 边界判据：与最容易混淆的那个同侪岗位之间凭什么划开。
         定义说这个岗位是什么，边界说它不是什么 —— 后者才是归类真正用得上的那一句。 */
      { label: '与相近岗位的边界', value: node?.boundary ?? '—', long: true },
      { label: '核心职责', value: join(node?.coreDuties), long: true },
      { label: '必备技能', value: join(node?.mustSkills), long: true },
      { label: '加分技能', value: join(node?.plusSkills), long: true },
      { label: '典型应用场景', value: join(node?.scenarios), long: true },
    ],
  };
}
