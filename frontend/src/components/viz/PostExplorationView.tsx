/* ============================================================
   Post Exploration View —— JobViz 论文 Figure 2(B) 的复刻

   一块画布，两种状态，外加一条常驻的图例带：

     B1 岗位聚类   每一簇一个增强雷达字形，落位来自簇中心在能力空间的二维投影，
                   再按半径互斥推开（论文 collision_detection）。
                   划过一簇 → 图例带左格换成这一簇的大字形；点一簇 → 进 B2。

     B2 岗位分布   横轴学历门槛、纵轴薪资档，格宽格高按落在其中的岗位数分配；
                   格内字形随机散开，位置本身不编码任何量。
                   划过一格 → 同行同列一起点亮；左键送入详情左列，右键送入右列。

     图例带       论文 ClusterSvg 底部那条 195px 高的带子，四格：
                   ① 大字形：默认全部簇的平均，悬停时换成那一簇，B2 里换成所选岗位
                   ② 样例字形：标出每条轴是哪个能力组
                   ③ 地平线图读法：一条剖面画两遍，看清折叠这一步做了什么
                   ④ 层色标尺与簇色对照

   与论文的一处出入：论文从 28 个技能里挑 8 个代表轴画字形（"仍占八成以上"），
   本系统的能力层一共只有 9 个能力组，全画即是全部，不必再挑。
   ============================================================ */

import { useEffect, useMemo, useState } from 'react';
import type { ClusterInfo, ClusterModel, PostCell, SkillAxis } from '@/data/explore';
import { HORIZON_COLORS, postMapLayout, clusterRadius, layoutClusters } from '@/data/jobviz';
import { AugmentedRadar, axisPoint, polygonPath, valuePath } from './AugmentedRadar';
import { fitText, measureText } from '@/utils/viz';

/** 图例带高度。论文取 195，这里按整块面板的比例压到 176 */
const STRIP = 176;
/* 图例带顶边留白。四格此前各自贴着分界线起排，标题字顶距线不足三像素，
   一条分界线两侧因而是"上面画完、下面紧接着写"，读起来像压在图上。
   现四格一律从这道线以下起排。 */
const STRIP_PAD = 16;
/** 底部那行格名占的高度。①②两格的内容在 [STRIP_PAD, STRIP - STRIP_FOOT] 内居中 */
const STRIP_FOOT = 22;
/** ③④⑤三格的标题基线：与①②的内容顶边同高，四格因而共用一条起排线 */
const STRIP_TITLE = STRIP_PAD + 9;
/* 图例带四格的宽度。合计此前 554px，而右栏在 1440 宽的屏上只有 456px，
   栏内因此恒挂一条横滚条。现收到 508px，并把两栏并排的下限提到 1540
   （见 explore.css 的 .ex-grid），两处一并使这张图不再横向溢出。 */
const W_SQUARE = 148;
const W_SAMPLE = 132;
const W_HORIZON = 96;
const W_SCALE = 50;
const MIN_W = W_SQUARE + W_SAMPLE + W_HORIZON + W_SCALE + 82;

/** 簇名单独占一行，画在字形正下方。落位区要先把这一行让出来 */
const CLABEL_H = 16;

/* ---- ③ 地平线图读法的示意剖面 ----
   纵向单位是"层"：一层就是图例④色标上的一格。折叠前、折叠后两幅小图都由
   这一条剖面算出来，因此两幅图逐点对得上 —— 折叠后的第 k 层，正是折叠前
   落在第 k 格与第 k+1 格之间的那一段。分箱数与簇字形的 RADIAL_BINS 同为 14。 */
const HZ_PROFILE = [0.04, 0.16, 0.42, 0.86, 1.5, 2.24, 2.62, 2.35, 1.68, 1.02, 0.58, 0.26, 0.09, 0.02];
const HZ_LAYERS = 3;
const HZ_LAST = HZ_PROFILE.length - 1;
const clamp01 = (v: number) => Math.min(Math.max(v, 0), 1);

/** 折叠前：纵轴就是条数，第 k+1 格以下的部分连成一块，由深到浅依次盖出三层 */
function hzFlat(x0: number, w: number, base: number, h: number, k: number): string {
  const unit = h / HZ_LAYERS;
  let d = `M${x0.toFixed(1)},${base.toFixed(1)}`;
  HZ_PROFILE.forEach((v, j) => {
    const y = Math.min(v, k + 1) * unit;
    d += `L${(x0 + (w * j) / HZ_LAST).toFixed(1)},${(base - y).toFixed(1)}`;
  });
  return `${d}L${(x0 + w).toFixed(1)},${base.toFixed(1)}Z`;
}

/** 折叠后：超出一层的部分翻折回来重画一遍，可用高度随半径变宽 —— 即扇区那个楔形 */
function hzFold(x0: number, w: number, base: number, h: number, k: number): string {
  let d = `M${x0.toFixed(1)},${base.toFixed(1)}`;
  HZ_PROFILE.forEach((v, j) => {
    const t = j / HZ_LAST;
    d += `L${(x0 + w * t).toFixed(1)},${(base - clamp01(v - k) * h * t).toFixed(1)}`;
  });
  return `${d}L${(x0 + w).toFixed(1)},${base.toFixed(1)}Z`;
}

const trim = (v: number) => (Number.isInteger(v) ? `${v}` : v.toFixed(1));
const fmt = (n: number) =>
  n >= 10000 ? `${trim(n / 10000)}w` : n >= 1000 ? `${trim(n / 1000)}k` : `${Math.round(n)}`;

/* 列名沿列宽折行，一律不截断。

   此前这一行走 fitText：放不下先把字号缩到 9.5，仍放不下便切掉尾字挂一个省略号。
   列名此前是技术方向的全称，多为八九个汉字，而一列不过六十来像素，于是下缘
   刻度带上多数列只剩"基础设施与云…""数据存储与…"，究竟是哪个方向要靠猜。
   现改为折两行写全，切点在字数中点附近就近选取，两行都放不下时再退字号，
   仍以不丢字为准。横轴换成学历门槛之后列名短了，这一段在窄列上仍会走到。 */
function colLabel(text: string, maxW: number): { size: number; lines: string[] } {
  for (const size of [10, 9.5, 9, 8.5]) {
    if (measureText(text, size) <= maxW) return { size, lines: [text] };
    const mid = Math.round(text.length / 2);
    for (let d = 0; d <= mid; d++) {
      for (const cut of d === 0 ? [mid] : [mid - d, mid + d]) {
        if (cut <= 0 || cut >= text.length) continue;
        const a = text.slice(0, cut).trim();
        const b = text.slice(cut).trim();
        if (measureText(a, size) <= maxW && measureText(b, size) <= maxW) {
          return { size, lines: [a, b] };
        }
      }
    }
  }
  /* 极窄的列：两行也放不下时按字数等分，宁可略微探出列宽也不丢字 */
  const mid = Math.round(text.length / 2);
  return { size: 8.5, lines: [text.slice(0, mid), text.slice(mid)] };
}

/** 汉字名按四字一行折行，论文对拉丁名是按空格折 */
const wrap = (s: string, per = 4) => {
  const out: string[] = [];
  for (let i = 0; i < s.length; i += per) out.push(s.slice(i, i + per));
  return out.slice(0, 3);
};

export interface ExplorationProps {
  width: number;
  height: number;
  model: ClusterModel;
  axes: SkillAxis[];
  /** null = 还在 B1 聚类态 */
  open: ClusterInfo | null;
  cells: PostCell[];
  columns: string[];
  bands: string[];
  /** 详情左 / 右列当前锁定的落点 key */
  leftKey: string | null;
  rightKey: string | null;
  onOpenCluster: (id: number) => void;
  onPick: (cell: PostCell, side: 'left' | 'right') => void;
  /** 收起提示时传 (null, null)：那一步没有指针位置，也不需要 */
  onTip: (e: React.MouseEvent | null, content: React.ReactNode | null) => void;
}

export function PostExplorationView({
  width,
  height,
  model,
  axes,
  open,
  cells,
  columns,
  bands,
  leftKey,
  rightKey,
  onOpenCluster,
  onPick,
  onTip,
}: ExplorationProps) {
  const W = Math.max(width, MIN_W);
  const H = Math.max(height, 480);
  const mapH = H - STRIP;

  const [hoverCluster, setHoverCluster] = useState<number | null>(null);
  const [hoverCell, setHoverCell] = useState<{ cc: string; band: string } | null>(null);
  /* 图例带左格里当前显示的那个岗位。它只在簇内分布里有意义 —— 退回聚类视图、
     或详情两列都被清空之后，左格要回到"这一屏在说什么"，而不是停在上一次点过的岗位上。 */
  const [focusCell, setFocusCell] = useState<PostCell | null>(null);
  useEffect(() => {
    setFocusCell(null);
    setHoverCell(null);
  }, [open?.id]);
  useEffect(() => {
    if (!leftKey && !rightKey) setFocusCell(null);
  }, [leftKey, rightKey]);

  const domain = model.domainMax;
  const n = axes.length;

  /* ---- B1 落位 ----
     落位区比图区矮一行：簇名画在字形正下方，不预留这一行，最底下那一簇的名字
     会越过分隔线落进图例带，压在簇色对照那一列上。 */
  const spotH = Math.max(mapH - CLABEL_H, 80);
  const r = useMemo(() => clusterRadius(model.clusters.length, W, spotH), [model.clusters.length, W, spotH]);
  const spots = useMemo(
    () => layoutClusters(model.clusters, W, spotH, r, `${W}x${spotH}|${model.clusters.length}`),
    [model.clusters, W, spotH, r],
  );

  /* ---- B2 落位 ---- */
  const grid = useMemo(
    () => (open ? postMapLayout(cells, columns, bands, W, mapH, `${open.id}|${W}x${mapH}`) : null),
    [open, cells, columns, bands, W, mapH],
  );

  /* ---- 图例带左格的大字形 ---- */
  const avg = useMemo(() => {
    const mean = new Array(n).fill(0);
    const dist = axes.map(() => new Array(model.clusters[0]?.dist[0]?.length ?? 1).fill(0));
    for (const c of model.clusters) {
      for (let i = 0; i < n; i++) {
        mean[i] += c.mean[i] / Math.max(model.clusters.length, 1);
        for (let j = 0; j < dist[i].length; j++) dist[i][j] += c.dist[i]?.[j] ?? 0;
      }
    }
    return { mean, dist };
  }, [model.clusters, axes, n]);

  const hovered = hoverCluster !== null ? model.clusters.find((c) => c.id === hoverCluster) : null;
  const bigR = STRIP * 0.38;
  const bigCx = W_SQUARE / 2;
  /** 内容区（顶边留白之下、底部格名之上）的竖直中心。①②两格各自居中于此 */
  const stripMid = mapH + STRIP_PAD + (STRIP - STRIP_PAD - STRIP_FOOT) / 2;
  const bigCy = stripMid;

  const big = focusCell
    ? { mean: focusCell.vector, dist: undefined, color: open?.color ?? 'var(--primary)', label: focusCell.jobName }
    : hovered
      ? { mean: hovered.mean, dist: hovered.dist, color: hovered.color, label: `以${hovered.label}为代表的一簇` }
      : open
        ? { mean: open.mean, dist: open.dist, color: open.color, label: `以${open.label}为代表的一簇` }
        : { mean: avg.mean, dist: avg.dist, color: 'var(--ink-3)', label: '全部簇平均' };

  /* 样例字形只标轴位，不承载量：半径与外圈标注一并收进本格之内，
     否则十条轴名沿半径向外铺开时会越过左侧那条分格线，压到大字形那一格上。
     纵向另向下让出一档，与图例带顶边留出与其他三格一致的间距。 */
  const sampleCx = W_SQUARE + W_SAMPLE / 2;
  const sampleCy = stripMid;
  const sampleR = 24;
  /** 轴名沿半径向外的落位半径。取值使最外侧的字仍落在本格宽度之内 */
  const sampleLabelR = sampleR + 20;

  const hx = W_SQUARE + W_SAMPLE + 12;
  const hw = W_HORIZON - 24;
  const scaleX = W_SQUARE + W_SAMPLE + W_HORIZON + 6;
  const legendX = W_SQUARE + W_SAMPLE + W_HORIZON + W_SCALE + 8;
  const legendW = W - legendX - 8;

  return (
    <svg
      width={W}
      height={H}
      className="pev-svg"
      role="img"
      aria-label={open ? '簇内岗位分布' : '岗位聚类字形'}
      onContextMenu={(e) => e.preventDefault()}
    >
      <rect className="pev-frame" x={0.5} y={0.5} width={W - 1} height={H - 1} rx={4} />

      {/* ================= B1 聚类 ================= */}
      {!open && model.clusters.length === 0 && (
        <text className="pev-empty" x={W / 2} y={mapH / 2}>
          当前属性筛选下没有岗位可聚类
        </text>
      )}
      {!open && (
        <g className="pev-clusters">
          {model.clusters.map((c, i) => {
            /* 簇名居中在字形正下方，但两端要夹回画布内：贴边那一簇的名字比字形宽，
               居中排会有半截落到框线外面去。 */
            const f = fitText(c.label, r * 2.4, 10);
            const half = measureText(f.text, f.size) / 2;
            const lx = Math.min(Math.max(spots[i][0], half + 5), W - half - 5);
            return (
              <g key={c.id}>
                <AugmentedRadar
                  cx={spots[i][0]}
                  cy={spots[i][1]}
                  r={r}
                  mean={c.mean}
                  dist={c.dist}
                  levelStep={model.levelStep}
                  domain={domain}
                  color={c.color}
                  back
                  spokes
                  points
                  active={hoverCluster === c.id}
                  dimmed={hoverCluster !== null && hoverCluster !== c.id}
                  onClick={() => onOpenCluster(c.id)}
                  onEnter={(e, ax) => {
                    setHoverCluster(c.id);
                    onTip(
                      e,
                      <>
                        <div className="tt-title">以{c.label}为代表的一簇</div>
                        <div>
                          {c.jobIds.length} 个岗位 · {Math.round(c.posts).toLocaleString()} 条招聘信息
                        </div>
                        {ax !== null && (
                          <div className="tt-muted">
                            {axes[ax]?.name}：平均占比 {(c.mean[ax] * 100).toFixed(1)}%
                          </div>
                        )}

                      </>,
                    );
                  }}
                  onLeave={() => {
                    setHoverCluster(null);
                    onTip(null, null);
                  }}
                  label={`以${c.label}为代表的一簇`}
                />
                <text
                  className="pev-clabel"
                  x={lx}
                  y={spots[i][1] + r + 11}
                  style={{ fontSize: f.size }}
                >
                  {f.text}
                  <title>{c.label}</title>
                </text>
              </g>
            );
          })}
          {model.clusters.length === 0 && (
            <text className="pev-empty" x={W / 2} y={mapH / 2}>
              当前筛选下没有可聚类的岗位
            </text>
          )}
        </g>
      )}

      {/* ================= B2 岗位分布 ================= */}
      {open && grid && (
        <g className="pev-map">
          {grid.cols.map((col) =>
            grid.rows.map((row) => {
              const on = hoverCell && (hoverCell.cc === col.key || hoverCell.band === row.key);
              return (
                <rect
                  key={`${col.key}|${row.key}`}
                  className={`pev-cell${on ? ' on' : ''}`}
                  x={col.at + 0.5}
                  y={row.at + 0.5}
                  width={col.size - 1}
                  height={row.size - 1}
                  onMouseEnter={() => setHoverCell({ cc: col.key, band: row.key })}
                  onMouseLeave={() => setHoverCell(null)}
                />
              );
            }),
          )}

          {/* 轴刻度：列名在下缘、行名在左缘竖排，与论文一致 */}
          {grid.cols.map((col) => {
            const lb = colLabel(col.key, col.size - 8);
            /* 末行贴着下缘，多出来的一行往上叠 —— 各列行数不同时下缘仍齐平 */
            const y0 = mapH - 6 - (lb.lines.length - 1) * 11;
            return (
              <text
                key={col.key}
                className="pev-axis"
                x={col.at + 4}
                y={y0}
                style={{ fontSize: lb.size }}
              >
                {lb.lines.map((t, i) => (
                  <tspan key={i} x={col.at + 4} dy={i === 0 ? 0 : 11}>
                    {t}
                  </tspan>
                ))}
              </text>
            );
          })}
          {grid.rows.map((row) => {
            const f = fitText(row.key, row.size - 8, 9.5);
            return (
              <text
                key={row.key}
                className="pev-axis"
                transform={`translate(11,${row.at + 4}) rotate(90)`}
                x={0}
                y={0}
                style={{ fontSize: f.size }}
              >
                {f.text}
                <title>{row.key}</title>
              </text>
            );
          })}

          {grid.glyphs.map((g) => {
            const key = `${g.cell.jobId}|${g.cell.cc}|${g.cell.band}`;
            const picked = key === leftKey || key === rightKey;
            return (
              <g
                key={key}
                className={`pev-glyph${picked ? ' on' : ''}`}
                onClick={() => {
                  setFocusCell(g.cell);
                  onPick(g.cell, 'left');
                }}
                onContextMenu={(e) => {
                  e.preventDefault();
                  setFocusCell(g.cell);
                  onPick(g.cell, 'right');
                }}
                onMouseMove={(e) =>
                  onTip(
                    e,
                    <>
                      <div className="tt-title">{g.cell.jobName}</div>
                      <div>
                        {g.cell.cc} · {g.cell.band}
                      </div>
                      <div className="tt-muted">
                        约 {Math.round(g.cell.posts).toLocaleString()} 条
                      </div>
                    </>,
                  )
                }
                onMouseLeave={(e) => onTip(e, null)}
                role="button"
                aria-label={`${g.cell.jobName} · ${g.cell.cc} · ${g.cell.band}`}
              >
                {/* 论文里单个岗位的字形不画外框多边形，只有一条闭合折线本身：
                    这一格里要比的是形状，多一圈外框只会让密排的格子糊成一片圆点。
                    外框改为透明的命中区，指针仍按整个字形的范围认。 */}
                <path
                  className="pev-glyph-val"
                  d={valuePath({ cx: g.x, cy: g.y, r: g.r, n }, g.cell.vector, domain)}
                  fill={open.color}
                  fillOpacity={picked ? 0.4 : 0.12}
                  stroke={picked ? 'var(--ink)' : 'var(--jv-line)'}
                  strokeWidth={picked ? 1.6 : 0.9}
                  strokeLinejoin="round"
                />
                <circle cx={g.x} cy={g.y} r={g.r} fill="transparent" />
              </g>
            );
          })}

          {grid.glyphs.length === 0 && (
            <text className="pev-empty" x={W / 2} y={mapH / 2}>
              本簇没有可落入格内的岗位
            </text>
          )}
        </g>
      )}

      {/* ================= 图例带 ================= */}
      <g className="pev-strip">
        <line x1={0} y1={mapH} x2={W} y2={mapH} />
        <line x1={W_SQUARE} y1={mapH} x2={W_SQUARE} y2={H} />

        {/* ① 大字形 */}
        <AugmentedRadar
          cx={bigCx}
          cy={bigCy}
          r={bigR}
          mean={big.mean}
          dist={big.dist}
          levelStep={model.levelStep}
          domain={domain}
          color={big.color}
          filled
          spokes
          points
          onEnter={(e, ax) => {
            if (ax === null) return;
            onTip(
              e,
              <>
                <div className="tt-title">{axes[ax]?.name}</div>
                <div>平均占比 {((big.mean[ax] ?? 0) * 100).toFixed(1)}%</div>
              </>,
            );
          }}
          onLeave={() => onTip(null, null)}
        />
        {/* fitText 压出来的字号此前没有用上，仍按样式表的 10.5px 排：
            它算的是"缩到多少能放下"，不落到元素上，长名便原样探出本格，
            压到右邻那一格的名字上。以下四处名字一律连同字号一并落上。 */}
        {(() => {
          const f = fitText(big.label, W_SQUARE - 12, 10.5);
          return (
            <text className="pev-striplb" x={6} y={H - 7} style={{ fontSize: f.size }}>
              {f.text}
              <title>{big.label}</title>
            </text>
          );
        })()}

        {/* ② 轴位对照：标出每条轴是哪个能力组。

            这一格不画量，只画轴 —— 它回答的是"图上那些字形，哪一条轴对应哪个
            能力组"。此前它与左邻的大字形形制相同而内部空着，读者会当成一张
            没画出来的图；现在多一行标题写明它是对照表，且轴上另点一圈小点，
            与"有量可读"的字形在形制上分开。 */}
        <g className="pev-sample">
          <path d={polygonPath({ cx: sampleCx, cy: sampleCy, r: sampleR, n })} />
          {axes.map((_, i) => {
            const [x, y] = axisPoint({ cx: sampleCx, cy: sampleCy, r: sampleR, n }, i, 1);
            return <line key={i} x1={sampleCx} y1={sampleCy} x2={x} y2={y} />;
          })}
          {axes.map((a, i) => {
            const [x, y] = axisPoint({ cx: sampleCx, cy: sampleCy, r: sampleR, n }, i, 1);
            return <circle key={`d-${a.id}`} className="pev-axdot" cx={x} cy={y} r={1.6} />;
          })}
          {axes.map((a, i) => {
            const [x, y] = axisPoint({ cx: sampleCx, cy: sampleCy, r: sampleLabelR, n }, i, 1);
            const lines = wrap(a.name);
            /* 落在格子两侧的轴名改为贴边对齐并向内收，居中对齐时最长的那几个
               会各向外探出半个词宽，正好越过分格线 */
            const side = x < sampleCx - 6 ? 'start' : x > sampleCx + 6 ? 'end' : 'middle';
            const ax = side === 'start' ? W_SQUARE + 7 : side === 'end' ? W_SQUARE + W_SAMPLE - 7 : x;
            return (
              <text
                key={a.id}
                className="pev-axname"
                x={ax}
                y={y - (lines.length - 1) * 3.5}
                style={{ textAnchor: side }}
              >
                {lines.map((t, k) => (
                  <tspan key={k} x={ax} dy={k === 0 ? 0 : 7.5}>
                    {t}
                  </tspan>
                ))}
              </text>
            );
          })}
        </g>
        <text className="pev-striplb" x={W_SQUARE + 6} y={H - 7}>
          轴位对照 · {axes.length} 个能力组
        </text>

        {/* ③ 地平线图读法：同一条剖面画两遍 —— 先按条数原样画，再折进扇区 */}
        <g className="pev-hz">
          <text className="pev-hzlb" x={hx} y={mapH + STRIP_TITLE}>
            地平线图读法
          </text>
          {(() => {
            const ph = 36;
            const topBase = mapH + 80;
            const botBase = mapH + 138;
            const ruleY = (k: number) => topBase - (ph * k) / HZ_LAYERS;
            return (
              <>
                <text className="pev-hztick" x={hx} y={mapH + STRIP_TITLE + 13}>
                  折叠前 · 纵轴为条数
                </text>
                <rect className="pev-hzbox" x={hx} y={topBase - ph} width={hw} height={ph} />
                {[2, 1, 0].map((k) => (
                  <path key={k} d={hzFlat(hx, hw, topBase, ph, k)} fill={HORIZON_COLORS[k]} />
                ))}
                {[1, 2].map((k) => (
                  <line key={k} className="pev-hzrule" x1={hx} x2={hx + hw} y1={ruleY(k)} y2={ruleY(k)} />
                ))}

                <text className="pev-hztick" x={hx} y={mapH + STRIP_TITLE + 71}>
                  折叠后 · 沿半径变宽
                </text>
                <path
                  className="pev-hztri"
                  d={`M${hx},${botBase}L${hx + hw},${botBase - ph}L${hx + hw},${botBase}Z`}
                />
                {[0, 1, 2].map((k) => (
                  <path key={k} d={hzFold(hx, hw, botBase, ph, k)} fill={HORIZON_COLORS[k]} />
                ))}

                <text className="pev-hztick" x={hx} y={botBase + 11}>
                  0
                </text>
                <text className="pev-hztick" x={hx + hw} y={botBase + 11} textAnchor="end">
                  能力占比
                </text>
                <text className="pev-hztick" x={hx} y={botBase + 23}>
                  颜色越深，条数越多
                </text>
              </>
            );
          })()}
        </g>

        {/* ④ 层色标尺 */}
        <g className="pev-scale">
          {HORIZON_COLORS.map((col, i) => (
            <rect key={col} x={scaleX} y={mapH + STRIP_TITLE + 5 + i * 13} width={9} height={13} fill={col} />
          ))}
          {HORIZON_COLORS.map((col, i) =>
            i % 3 === 0 || i === HORIZON_COLORS.length - 1 ? (
              <text key={col} className="pev-scalelb" x={scaleX + 12} y={mapH + STRIP_TITLE + 15 + i * 13}>
                ≤{fmt((i + 1) * model.levelStep)}
              </text>
            ) : null,
          )}
          <text className="pev-scaletitle" x={scaleX} y={mapH + STRIP_TITLE}>
            条数
          </text>
        </g>

        {/* ⑤ 簇色对照 */}
        <g className="pev-legend">
          <text className="pev-scaletitle" x={legendX} y={mapH + STRIP_TITLE}>
            {open ? '当前簇' : `${model.clusters.length} 簇`}
          </text>
          {(open ? [open] : model.clusters).slice(0, 10).map((c, i) => (
            <g key={c.id} className={hoverCluster === c.id ? 'on' : undefined}>
              <circle cx={legendX + 5} cy={mapH + STRIP_TITLE + 13 + i * 15} r={4.5} fill={c.color} fillOpacity={0.2} stroke={c.color} />
              {(() => {
                const f = fitText(c.label, Math.max(legendW - 18, 30), 10);
                return (
                  <text
                    className="pev-legendlb"
                    x={legendX + 14}
                    y={mapH + STRIP_TITLE + 16.5 + i * 15}
                    style={{ fontSize: f.size }}
                  >
                    {f.text}
                    <title>{c.label}</title>
                  </text>
                );
              })()}
            </g>
          ))}
        </g>
      </g>
    </svg>
  );
}
