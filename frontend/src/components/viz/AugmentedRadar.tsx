/* ============================================================
   增强雷达字形 —— JobViz 论文 Figure 4 的图元，按其几何原样移植

   一个字形同时说两件事：
     · 雷达折线：这一簇岗位的平均能力构成（每条轴一个能力组）
     · 地平线图：簇内岗位在该能力构成占比上的分布 —— 扇区沿半径切成等距分箱，
       每个分箱的条数沿切线方向铺开；超过一层的部分翻折回来叠在上面，
       于是"颜色越深 = 落在这个占比区间的招聘信息越多"。

   两者叠在同一个字形里的理由：光看折线只知道平均值，而平均值相同的两簇
   可能一个是"大家都差不多"、另一个是"两头分化"。地平线图把这一层差别
   摆进同一个图元，不必再点开第二张图。

   几何与论文实现（tools_for_d3.js 的 cluster_radar）逐行对应：
     轴 i 的顶点方向 = (cx − sin(2πi/N)·R, cy − cos(2πi/N)·R)，轴 0 朝正上
     扇区 i 介于轴 i 与轴 i+1 之间，切线方向角 = (2i+1)π/N
     半径 t 处的扇区宽度 = 多边形边长 × t，条带按这个宽度封顶
     折线半径 = (占比 / 定义域)^(1/4)  —— 论文对技能向量取四次方根后再取半径

   四个通道各管一件事，不互相借用：
     底色多边形 = 这是哪一簇（簇色，10% 透明度）
     地平线层色 = 这一档里有多少条（紫—靛十阶，与簇色无关）
     折线       = 平均构成
     顶点圆     = 各轴取值的落点
   ============================================================ */

import { Fragment } from 'react';
import { HORIZON_COLORS } from '@/data/jobviz';

export interface RadarGeom {
  cx: number;
  cy: number;
  r: number;
  n: number;
}

/* 非有限值一律归零再进几何：一个 NaN 会让整条 path 的 d 失效，浏览器直接
   丢掉这个图元，屏幕上少一块而控制台只报一行属性错误 —— 与其让某个字形
   悄悄消失，不如画成塌到圆心的形状，一眼看得出不对。 */
const num = (v: number) => (Number.isFinite(v) ? v : 0);

/** 轴 i 的顶点（半径系数 t ∈ [0,1]） */
export function axisPoint(g: RadarGeom, i: number, t = 1): [number, number] {
  const a = (i / Math.max(g.n, 1)) * Math.PI * 2;
  const r = num(g.r) * num(t);
  return [num(g.cx) - Math.sin(a) * r, num(g.cy) - Math.cos(a) * r];
}

/** 外框多边形 */
export function polygonPath(g: RadarGeom, t = 1): string {
  let d = '';
  for (let i = 0; i < g.n; i++) {
    const [x, y] = axisPoint(g, i, t);
    d += `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`;
  }
  return `${d}Z`;
}

/** 论文对技能向量取四次方根再定半径：小值全挤在圆心附近就分不出形状 */
export const radiusOf = (v: number, domain: number) =>
  Math.min(Math.pow(Math.max(num(v), 0) / (num(domain) || 1), 0.25), 1);

/** 数据折线 */
export function valuePath(g: RadarGeom, values: number[], domain: number): string {
  let d = '';
  values.forEach((v, i) => {
    const [x, y] = axisPoint(g, i, radiusOf(v, domain));
    d += `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`;
  });
  return `${d}Z`;
}

/** 单个扇区、单个层级的地平线条带路径 */
function bandPath(g: RadarGeom, i: number, bins: number[], level: number, step: number): string {
  const n = Math.max(g.n, 1);
  const side = num(g.r) * Math.sin(Math.PI / n) * 2;
  const tan = ((2 * i + 1) * Math.PI) / n;
  const a = (i / n) * Math.PI * 2;
  const last = Math.max(bins.length - 1, 1);
  const st = num(step) || 1;

  let d = `M${num(g.cx).toFixed(2)},${num(g.cy).toFixed(2)}`;
  for (let j = 0; j < bins.length; j++) {
    const t = j / last;
    const cap = side * t;
    const over = num(bins[j]) - level;
    const y = over <= 0 ? 0 : over < st ? cap * (over / st) : cap;
    const x = num(g.cx) - Math.sin(a) * num(g.r) * t - y * Math.cos(tan);
    const yy = num(g.cy) - Math.cos(a) * num(g.r) * t + y * Math.sin(tan);
    d += `L${x.toFixed(2)},${yy.toFixed(2)}`;
  }
  const [ex, ey] = axisPoint(g, i, 1);
  return `${d}L${ex.toFixed(2)},${ey.toFixed(2)}Z`;
}

interface Props {
  cx: number;
  cy: number;
  r: number;
  /** 各轴的平均构成占比 */
  mean: number[];
  /** 各轴的分箱条数。不给就不画地平线 */
  dist?: number[][];
  /** 地平线图每层代表多少条 */
  levelStep?: number;
  /** 半径方向的定义域上界 */
  domain: number;
  color: string;
  /** 画簇色底多边形 —— 论文只给聚类视图里的簇字形画，其余一律不画 */
  back?: boolean;
  /** 折线闭合成面（论文的 averageCluster / focusCluster / 单个岗位）还是只连线（簇字形） */
  filled?: boolean;
  /** 画轴线。岗位分布图上的小字形一律不画，画了只是噪点 */
  spokes?: boolean;
  /** 画顶点圆 */
  points?: boolean;
  active?: boolean;
  dimmed?: boolean;
  onEnter?: (e: React.MouseEvent, axis: number | null) => void;
  onLeave?: () => void;
  onClick?: () => void;
  onContextMenu?: (e: React.MouseEvent) => void;
  label?: string;
}

export function AugmentedRadar({
  cx,
  cy,
  r,
  mean,
  dist,
  levelStep = 1,
  domain,
  color,
  back = false,
  filled = false,
  spokes = true,
  points = true,
  active,
  dimmed,
  onEnter,
  onLeave,
  onClick,
  onContextMenu,
  label,
}: Props) {
  const g: RadarGeom = { cx, cy, r, n: mean.length };
  const line = 'var(--jv-line)';

  return (
    <g
      className={`arad${active ? ' on' : ''}${dimmed ? ' dim' : ''}${onClick ? ' clickable' : ''}`}
      onClick={onClick}
      onContextMenu={onContextMenu}
      onMouseLeave={onLeave}
      role={onClick ? 'button' : undefined}
      aria-label={label}
    >
      {back && <path className="arad-bg" d={polygonPath(g)} fill={color} fillOpacity={0.1} stroke={color} />}

      {dist &&
        dist.map((bins, i) => {
          const peak = Math.max(...bins);
          const layers: number[] = [];
          for (let lv = 0; lv < peak && layers.length < HORIZON_COLORS.length; lv += levelStep) layers.push(lv);
          return (
            <Fragment key={i}>
              {layers.map((lv, k) => (
                <path
                  key={lv}
                  d={bandPath(g, i, bins, lv, levelStep)}
                  fill={HORIZON_COLORS[Math.min(k, HORIZON_COLORS.length - 1)]}
                  stroke="none"
                  onMouseMove={(e) => onEnter?.(e, i)}
                />
              ))}
            </Fragment>
          );
        })}

      {spokes &&
        mean.map((_, i) => {
          const [x, y] = axisPoint(g, i, 1);
          const [x2, y2] = axisPoint(g, (i + 1) % g.n, 1);
          return (
            <Fragment key={i}>
              <line x1={cx} y1={cy} x2={x} y2={y} stroke={color} strokeOpacity={0.3} strokeWidth={0.5} />
              <line x1={x} y1={y} x2={x2} y2={y2} stroke={color} strokeOpacity={0.55} strokeWidth={0.8} />
            </Fragment>
          );
        })}

      <path
        className="arad-val"
        d={valuePath(g, mean, domain)}
        fill={filled ? color : 'none'}
        fillOpacity={filled ? 0.12 : 0}
        stroke={line}
        strokeWidth={active ? 1.8 : 1}
        strokeLinejoin="round"
      />

      {points &&
        mean.map((v, i) => {
          const [x, y] = axisPoint(g, i, radiusOf(v, domain));
          return (
            <circle
              key={i}
              className="arad-pt"
              cx={x}
              cy={y}
              r={r > 46 ? 2.4 : 1.8}
              fill={line}
              onMouseMove={(e) => onEnter?.(e, i)}
            />
          );
        })}

      {/* 命中区：字形内部大量细路径都可点，统一用一个透明多边形兜住指针 */}
      <path
        className="arad-hit"
        d={polygonPath(g)}
        fill="transparent"
        onMouseMove={(e) => onEnter?.(e, null)}
      />
    </g>
  );
}
