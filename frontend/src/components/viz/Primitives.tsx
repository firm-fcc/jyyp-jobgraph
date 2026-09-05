/* 通用可视化原语 */

import { smoothPath } from '@/utils/viz';

/** 迷你走势线 */
export function Sparkline({
  values,
  color = 'var(--src-jd)',
  w = 96,
  h = 26,
  fill = true,
}: {
  values: number[];
  color?: string;
  w?: number;
  h?: number;
  fill?: boolean;
}) {
  if (values.length === 0) return null;
  const max = Math.max(...values, 1e-6);
  const pts = values.map((v, i) => [(i / (values.length - 1)) * w, h - (v / max) * (h - 3) - 1.5] as [number, number]);
  const d = smoothPath(pts);
  return (
    <svg width={w} height={h} style={{ verticalAlign: 'middle', display: 'block' }} aria-hidden="true">
      {fill && <path d={`${d} L${w},${h} L0,${h} Z`} fill={color} opacity={0.14} />}
      <path d={d} fill="none" stroke={color} strokeWidth={1.6} />
    </svg>
  );
}

/**
 * 双线走势：几条序列画在同一格里，共用纵轴。
 * 各自归一化会让两条线都顶到框顶，谁高谁低就看不出来了 ——
 * 这里要比的正是相对高低（论文 vs 新闻谁在推高前瞻热度），所以必须共用同一个 max。
 * 挤在表格一格里，不铺面积，否则两片半透明色叠在一起反而看不清线。
 */
export function DualSparkline({
  series,
  w = 96,
  h = 26,
}: {
  series: { values: number[]; color: string }[];
  w?: number;
  h?: number;
}) {
  const rows = series.filter((s) => s.values.length > 0);
  if (rows.length === 0) return null;
  /* 每条各按自身峰值归一，不共用一把尺子。

     两条线的量纲不同：一条是该条目在本层里的相对需求高度，另一条是前瞻缺口。
     共用尺子时，凡两者相差一个量级的条目，矮的那条整条压在底边上 ——
     这个小图要看的是"两条的起伏是不是错开的"，那正好是被压掉的那件事。 */
  return (
    <svg width={w} height={h} style={{ verticalAlign: 'middle', display: 'block' }} aria-hidden="true">
      {rows.map((s) => {
        let max = 1e-6;
        for (const v of s.values) if (v > max) max = v;
        return (
          <path
            key={s.color}
            d={smoothPath(
              s.values.map(
                (v, i) => [(i / (s.values.length - 1)) * w, h - (v / max) * (h - 3) - 1.5] as [number, number],
              ),
            )}
            fill="none"
            stroke={s.color}
            strokeWidth={1.5}
          />
        );
      })}
    </svg>
  );
}

/**
 * 结构条：把一项要求拆成“招聘市场已确认”与“前瞻信号追加”两段。
 * 用排序条而不是雷达图 —— 雷达图的面积会随维度顺序变化而失真，读数也不精确。
 */
export function StructureBars({
  items,
  max = 8,
}: {
  items: { name: string; base: number; delta: number }[];
  max?: number;
}) {
  const rows = items.slice(0, max);
  const top = Math.max(...rows.map((r) => r.base + r.delta), 1e-6);
  return (
    <div className="sbars">
      {rows.map((r) => (
        <div className="sbar" key={r.name}>
          {/* 过长的名字由 CSS 省略号收口：同一批能力在下方“能力构成”里有完整名称 */}
          <span className="sbar-n">{r.name}</span>
          <span className="sbar-track">
            <i className="sbar-base" style={{ width: `${(r.base / top) * 100}%` }} />
            <i className="sbar-delta" style={{ width: `${(r.delta / top) * 100}%` }} />
          </span>
          <span className="sbar-v">{(r.base + r.delta).toFixed(2)}</span>
        </div>
      ))}
    </div>
  );
}
