import type { EdgeStatus, GraphEdge, NodeKind } from '@/types/graph';

/* ---------------- 极坐标 ---------------- */

export const TAU = Math.PI * 2;

export function polar(cx: number, cy: number, r: number, a: number): [number, number] {
  return [cx + r * Math.cos(a - Math.PI / 2), cy + r * Math.sin(a - Math.PI / 2)];
}

/** 环形扇区路径 */
export function annulusPath(cx: number, cy: number, r0: number, r1: number, a0: number, a1: number): string {
  const [x0, y0] = polar(cx, cy, r1, a0);
  const [x1, y1] = polar(cx, cy, r1, a1);
  const [x2, y2] = polar(cx, cy, r0, a1);
  const [x3, y3] = polar(cx, cy, r0, a0);
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return `M${x0},${y0} A${r1},${r1} 0 ${large} 1 ${x1},${y1} L${x2},${y2} A${r0},${r0} 0 ${large} 0 ${x3},${y3} Z`;
}

/** 单条圆弧，不闭合 —— 在环带内画一条参考高度线（如棱镜的对比基准月） */
export function arcPath(cx: number, cy: number, r: number, a0: number, a1: number): string {
  const [x0, y0] = polar(cx, cy, r, a0);
  const [x1, y1] = polar(cx, cy, r, a1);
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return `M${x0},${y0} A${r},${r} 0 ${large} 1 ${x1},${y1}`;
}

/**
 * 层次边捆绑样条（Holten 2006 的简化实现）
 * 控制点沿半径向圆心收拢：同一走向的连线会并成一束，而不是各画各的直线 ——
 * 这是把上百条跨环连线压到可读密度的关键，beta 越小捆得越紧。
 */
export function bundledPath(
  cx: number,
  cy: number,
  r0: number,
  a0: number,
  r1: number,
  a1: number,
  beta = 0.72,
): string {
  let da = a1 - a0;
  while (da > Math.PI) da -= TAU;
  while (da < -Math.PI) da += TAU;

  const inner = Math.min(r0, r1) * (1 - beta) + 6;
  const steps = 6;
  const pts: [number, number][] = [];
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const bell = Math.sin(Math.PI * t);
    const r = r0 + (r1 - r0) * t - (Math.min(r0, r1) - inner) * bell * (1 - beta);
    const a = a0 + da * t;
    pts.push(polar(cx, cy, Math.max(r, 4), a));
  }
  let d = `M${pts[0][0].toFixed(2)},${pts[0][1].toFixed(2)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[Math.min(pts.length - 1, i + 2)];
    d +=
      ` C${(p1[0] + (p2[0] - p0[0]) / 6).toFixed(2)},${(p1[1] + (p2[1] - p0[1]) / 6).toFixed(2)}` +
      ` ${(p2[0] - (p3[0] - p1[0]) / 6).toFixed(2)},${(p2[1] - (p3[1] - p1[1]) / 6).toFixed(2)}` +
      ` ${p2[0].toFixed(2)},${p2[1].toFixed(2)}`;
  }
  return d;
}

/* 折线 → 平滑路径。

   曲线取 Catmull-Rom 转三次贝塞尔，切线只看相邻两点，因而不保单调：一段
   由零陡升的序列，其起点处的控制点会被后一点的斜率甩到零以下，画出来是一条
   先向下探、再折返上行的曲线。三源对照图上那几处"跌破零"、岗位涌现相图上
   首现点左侧的下探，都由此而来 —— 落点本身一个负值也没有，是画法把它带下去的。

   故将每段的两个控制点钳回该段两端所夹的范围内。钳制后整条曲线落在逐段的
   包围盒内，既不会越出数据的取值范围，各段之间也仍然接得平滑；代价是曲率
   极大处比原先略平，而那本就是过冲发生的地方。 */
export function smoothPath(pts: [number, number][]): string {
  if (pts.length === 0) return '';
  if (pts.length < 3) return `M${pts.map((p) => p.join(',')).join(' L')}`;
  const clamp = (v: number, a: number, b: number) =>
    Math.min(Math.max(v, Math.min(a, b)), Math.max(a, b));
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[Math.min(pts.length - 1, i + 2)];
    const c1x = clamp(p1[0] + (p2[0] - p0[0]) / 6, p1[0], p2[0]);
    const c1y = clamp(p1[1] + (p2[1] - p0[1]) / 6, p1[1], p2[1]);
    const c2x = clamp(p2[0] - (p3[0] - p1[0]) / 6, p1[0], p2[0]);
    const c2y = clamp(p2[1] - (p3[1] - p1[1]) / 6, p1[1], p2[1]);
    d +=
      ` C${c1x.toFixed(2)},${c1y.toFixed(2)}` +
      ` ${c2x.toFixed(2)},${c2y.toFixed(2)}` +
      ` ${p2[0].toFixed(2)},${p2[1].toFixed(2)}`;
  }
  return d;
}

/* ---------------- 颜色与措辞 ----------------
   界面上一律不出现 λ / β / G_base ⊕ ΔG 这类记号：
   同一件事对内是算法量，对外要说人话。 */

export const KIND_COLOR: Record<NodeKind, string> = {
  job: 'var(--lay-job)',
  task: 'var(--lay-task)',
  skill: 'var(--lay-skill)',
  skillpoint: 'var(--lay-sp)',
};

export const KIND_SOFT: Record<NodeKind, string> = {
  job: 'var(--lay-job-soft)',
  task: 'var(--lay-task-soft)',
  skill: 'var(--lay-skill-soft)',
  skillpoint: 'var(--lay-sp-soft)',
};

/* 第三、四层在界面上定名为“技能 / 技能点”，全站以此为准。

   源文件 skills0821.json 是 2 个维度 → 9 个组 → 49 个技能的三级结构。
   第四层取"技能点"：赛题写的是"颗粒度到技能点级别"，评审拿赛题对照界面时
   找的就是这三个字。第三层此前叫"能力组"，与第四层区分得开，但读者要多记
   一个词；现改为"技能"，与源文件的三级命名一致，量词上用"项"与"个"区分
   （九项技能 / 49 个技能点）。

   代码内部仍沿用 skill / skillGroup 一类标识，只有界面文案改名。 */
export const KIND_LABEL: Record<NodeKind, string> = {
  job: '岗位',
  task: '任务',
  skill: '技能',
  skillpoint: '技能点',
};

export const STATUS_LABEL: Record<EdgeStatus, string> = {
  candidate: '待确认',
  active: '稳定',
  strengthening: '需求上升',
  weakening: '需求回落',
};

export const STATUS_COLOR: Record<EdgeStatus, string> = {
  candidate: 'var(--amber)',
  active: 'var(--ink-2)',
  strengthening: 'var(--green)',
  weakening: 'var(--red)',
};

export const SOURCE_COLOR = {
  jd: 'var(--src-jd)',
  paper: 'var(--src-paper)',
  news: 'var(--src-news)',
  resume: 'var(--green)',
} as const;

export const SOURCE_LABEL = {
  jd: '招聘信息',
  paper: '学术论文',
  news: '行业新闻',
  resume: '简历',
} as const;

/**
 * 前瞻程度作用下的综合权重。
 * foresight = 0 时只反映招聘市场已经写进 JD 的部分；
 * 调到 1 则把论文与新闻领先出来的那一截也计入。
 */
export const weightAt = (e: GraphEdge, foresight: number) => e.baseWeight + foresight * e.deltaWeight;

/** 只看高可信时，按可信度压暗 */
export const trustOpacity = (confidence: number, on: boolean) =>
  on ? 0.1 + 0.9 * Math.pow(confidence, 2.4) : 1;

export const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

/* ==================== SVG 里的文字排布 ====================
   字宽实测而不是按字数估：中西文混排（"RAG检索增强生成与向量数据库"）估不准，
   估宽了留白、估窄了压到条上，两种都会让密排的条形图读不出来。 */

let ctx2d: CanvasRenderingContext2D | null | undefined;
let fontFamily = '';

/**
 * weight 须与实际绘制时的字重一致。中日韩字形多为等宽，字重不改宽度，
 * 但西文加粗后要宽出百分之几 —— "RAG系统工程师"这类混排按常规字重量出来偏窄，
 * 避让就会照着偏窄的框排，标签之间因此蹭上。
 */
export function measureText(text: string, px: number, weight: number | string = 400): number {
  if (ctx2d === undefined) {
    ctx2d = typeof document === 'undefined' ? null : document.createElement('canvas').getContext('2d');
    fontFamily =
      (typeof document !== 'undefined' &&
        getComputedStyle(document.documentElement).getPropertyValue('--font').trim()) ||
      'sans-serif';
  }
  if (!ctx2d) {
    // 量不到时按"中文一个字宽、西文半个"估，宁可估宽也不要压到条上
    let u = 0;
    for (const ch of text) u += ch.charCodeAt(0) > 0x2e80 ? 1 : 0.56;
    return u * px;
  }
  ctx2d.font = `${weight} ${px}px ${fontFamily}`;
  return ctx2d.measureText(text).width;
}

/**
 * 名字放不下先压字号，压到下限才截断。
 * 真实体系里最长的技能点名 18 个字、岗位名 27 个字，一律按 12px 排必然压到条上。
 */
export function fitText(text: string, maxW: number, base = 12, min = 9.5): { size: number; text: string } {
  const w0 = measureText(text, base);
  if (w0 <= maxW) return { size: base, text };
  const est = Math.max(min, (base * maxW) / w0);
  if (est > min && measureText(text, est) <= maxW) return { size: Number(est.toFixed(2)), text };
  let t = text;
  while (t.length > 1 && measureText(`${t}…`, min) > maxW) t = t.slice(0, -1);
  return { size: min, text: `${t}…` };
}
