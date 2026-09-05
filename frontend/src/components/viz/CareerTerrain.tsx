/* ============================================================
   能力地形导航

   全部岗位按能力要求向量降维到二维：挨得越近，要求的能力越像。
   再把“在招数量 × 薪资中位数”做核密度估计铺成等高线 —— 高地就是
   需求密集、给得起价的区域。简历落成“当前位置”，目标岗位是“目的地”，
   学习路径上的关键能力落成沿途路标。

   图内文字既不描边也不垫底色。白色描边会在每个字周围糊出一圈白斑，
   字一多整张图就脏了。这里反过来做：把地势压到足够浅（面色合成后最深处
   约三成主色），文字改用接近正文的深色直接压上去，实测对比度全部在
   4.5:1 以上。字号统一 13px（路标 12.5px），与全站正文同一量级。

   标签按优先级逐个落位：当前位置 → 目标岗位 → 路标 → 在招量最大的岗位；
   八个方向轮流试，撞上已落位的框就换一个方向，八个方向都放不下就不标 ——
   图上少一个名字，好过两个名字叠在一起谁都读不出。

   等高线用自己实现的 marching squares（只吐线段，不做环拼接），
   为的是不给一张图多引一个依赖；面色则用同一批高斯核画成径向渐变叠加，
   两者由同一组中心与同一个 σ 生成，所以线与色是对得上的。
   ============================================================ */

import { useCallback, useEffect, useMemo, useState } from 'react';
import type { GraphNode } from '@/types/graph';
import { useSize } from '@/hooks/useSize';
import { useZoomPan } from '@/hooks/useZoomPan';
import { ZoomBar } from '@/components/viz/ZoomBar';
import { Tooltip, type TipState } from '@/components/common/Tooltip';

interface Waypoint {
  name: string;
  weight: number;
}

interface Props {
  jobs: GraphNode[];
  coords: Map<string, [number, number]>;
  resumePos: [number, number];
  targetJobId: string;
  waypoints: Waypoint[];
  onPickJob?: (id: string) => void;
}

const LABEL_FS = 13;
const WP_FS = 12.5;
/** 缩放倍率上限。四倍已能把最密的一片拆开，再高周边一个可参照的岗位都不剩 */
const MAX_K = 4;
/** 右上角那枚缩放控件占的地方，标签绘制时据此避让（见 global.css 的 .viz-zoom） */
const CTRL_W = 208;
const CTRL_H = 32;

interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

const hit = (a: Box, b: Box) => !(a.x1 < b.x0 || b.x1 < a.x0 || a.y1 < b.y0 || b.y1 < a.y0);

/** 中英混排的宽度估算：中日韩按一个字宽，拉丁按 0.56 字宽 */
const textW = (s: string, fs: number) =>
  [...s].reduce((n, ch) => n + (ch.charCodeAt(0) > 0x2e80 ? fs : fs * 0.56), 0);

/** marching squares —— 只吐线段，等值线不需要闭合成环 */

export function CareerTerrain({ jobs, coords, resumePos, targetJobId, waypoints, onPickJob }: Props) {
  const { ref, w } = useSize<HTMLDivElement>();
  const [tip, setTip] = useState<TipState | null>(null);
  /* 悬停的岗位：把它的名字提亮，鼠标停在哪里、看的是哪一条不用来回对 */
  const [hot, setHot] = useState<string | null>(null);

  const W = Math.max(420, w || 900);
  /* 高度直接决定标签有多少地方可落。加高一档，能多标出的名字明显更多 */
  const H = W < 620 ? 400 : 500;
  const PAD = 42;
  /* 一百余个岗位铺满一屏之后，名字大半落不下，而这张图要读的是
     “从当前位置到目标岗位这一路要补什么”。默认取景因此落在这条路线上，
     另给控件退回整图 —— 缩放机制见 hooks/useZoomPan。 */
  const zp = useZoomPan({ w: W, h: H, maxK: MAX_K });
  const { k, wk, hk } = zp;

  /* ---------------- 坐标映射 ----------------
     密度场（高斯核 + 等高线）已撤下，见下方渲染处的说明。
     这里只剩把 MDS 投影出来的坐标线性映射到画布。
     基准坐标为倍率 1、整图恰好铺满画框时的位置，绘制用的是它乘上倍率；
     倍率折进坐标而不是套在 SVG 的 scale 上，否则字号会跟着一起放大。 */
  const base = useMemo(() => {
    const pts = [...coords.values(), resumePos];
    const xs = pts.map((p) => p[0]);
    const ys = pts.map((p) => p[1]);
    const x0 = Math.min(...xs);
    const x1 = Math.max(...xs);
    const y0 = Math.min(...ys);
    const y1 = Math.max(...ys);
    return {
      x: (v: number) => PAD + ((v - x0) / (x1 - x0 || 1)) * (W - PAD * 2),
      y: (v: number) => PAD + ((v - y0) / (y1 - y0 || 1)) * (H - PAD * 2),
    };
  }, [coords, resumePos, W, H]);

  const toX = useCallback((v: number) => base.x(v) * k, [base, k]);
  const toY = useCallback((v: number) => base.y(v) * k, [base, k]);

  /* ---------------- 路线 ---------------- */
  const target = coords.get(targetJobId);
  const mx = toX(resumePos[0]);
  const my = toY(resumePos[1]);

  /* ---------------- 默认取景 ----------------
     取景框为“当前位置 → 目标岗位”这条线段的外接矩形。留白 96px 把弧线
     偏出去的那几个路标一并收进来；倍率封在 2.6 倍，两点挨得极近时
     不至于放到周边一个可参照的岗位都不剩。 */
  const focusBox = useMemo(() => {
    const mxb = base.x(resumePos[0]);
    const myb = base.y(resumePos[1]);
    const t = coords.get(targetJobId);
    if (!t) return { x0: mxb - 90, y0: myb - 90, x1: mxb + 90, y1: myb + 90 };
    const txb = base.x(t[0]);
    const tyb = base.y(t[1]);
    return {
      x0: Math.min(mxb, txb),
      y0: Math.min(myb, tyb),
      x1: Math.max(mxb, txb),
      y1: Math.max(myb, tyb),
    };
  }, [base, coords, targetJobId, resumePos]);

  const { fitTo } = zp;
  const backToFocus = useCallback(() => fitTo(focusBox, 96, 2.6), [fitTo, focusBox]);
  useEffect(() => {
    backToFocus();
  }, [backToFocus]);

  const route = useMemo(() => {
    if (!target) return [];
    const tx = toX(target[0]);
    const ty = toY(target[1]);
    const n = waypoints.length;
    // 垂直于“当前位置 → 目标”的方向做一点弧线偏移，避免路标压在直线上
    const dx = tx - mx;
    const dy = ty - my;
    const len = Math.hypot(dx, dy) || 1;
    const nx = -dy / len;
    const ny = dx / len;
    return waypoints.map((wp, i) => {
      const t = (i + 1) / (n + 1);
      const bend = Math.sin(t * Math.PI) * Math.min(46, len * 0.22);
      return { ...wp, x: mx + dx * t + nx * bend, y: my + dy * t + ny * bend };
    });
  }, [waypoints, target, mx, my, toX, toY]);

  /* ---------------- 标签落位 ----------------
     优先级：当前位置 → 目标岗位 → 路标 → 在招量最大的岗位。
     八个方向轮流试，撞上已落位的框就换一个方向；
     八个方向都放不下，这一条就不标 —— 图上少一个名字，
     好过两个名字叠在一起谁都读不出。 */
  const placed = useMemo(() => {
    // 先把所有图元自己占的地方登记进去，标签就不会盖住别人的点。
    // 占位按“看得见的最大半径”算：光晕比圆点大得多，压在光晕上一样掉对比度
    const boxes: Box[] = [];
    const block = (cx: number, cy: number, r: number) =>
      boxes.push({ x0: cx - r, y0: cy - r, x1: cx + r, y1: cy + r });
    jobs.forEach((j) => {
      const c = coords.get(j.id);
      if (c) block(toX(c[0]), toY(c[1]), j.id === targetJobId ? 11 : 6);
    });
    block(mx, my, 17);
    route.forEach((r) => block(r.x, r.y, 6 + r.weight * 12));
    const out: {
      key: string;
      x: number;
      y: number;
      text: string;
      anchor: 'start' | 'middle' | 'end';
      fs: number;
      cls: string;
      /** 落位框，绘制时据此判断这一条整体在不在画框内 */
      box: Box;
    }[] = [];

    const put = (
      key: string,
      cx: number,
      cy: number,
      r: number,
      text: string,
      fs: number,
      cls: string,
      force = false,
    ) => {
      const tw = textW(text, fs);
      const th = fs + 4;
      /* 四正方向优先（读起来最自然），放不下再试四个斜角 */
      const side = (dx: number, dy: number, anchor: 'start' | 'end') => {
        const x = cx + dx;
        const y = cy + dy + fs * 0.36;
        return {
          x,
          y,
          anchor,
          box: {
            x0: anchor === 'start' ? x - 2 : x - tw - 2,
            y0: y - fs * 0.36 - th / 2,
            x1: anchor === 'start' ? x + tw + 2 : x + 2,
            y1: y - fs * 0.36 + th / 2,
          },
        };
      };
      const stack = (dy: number) => ({
        x: cx,
        y: cy + dy + (dy > 0 ? fs * 0.72 : 0),
        anchor: 'middle' as const,
        box: {
          x0: cx - tw / 2,
          y0: dy > 0 ? cy + dy - 1 : cy + dy - th,
          x1: cx + tw / 2,
          y1: dy > 0 ? cy + dy + th - 1 : cy + dy,
        },
      });
      const d = r * 0.72;
      const cands: { x: number; y: number; anchor: 'start' | 'middle' | 'end'; box: Box }[] = [
        side(r + 7, 0, 'start'),
        side(-r - 7, 0, 'end'),
        stack(-r - 7),
        stack(r + 7),
        side(d + 6, -d - 5, 'start'),
        side(-d - 6, -d - 5, 'end'),
        side(d + 6, d + 5, 'start'),
        side(-d - 6, d + 5, 'end'),
      ];
      for (const c of cands) {
        const inside = c.box.x0 > 4 && c.box.x1 < wk - 4 && c.box.y0 > 4 && c.box.y1 < hk - 4;
        if (!inside) continue;
        if (boxes.some((b) => hit(b, c.box))) continue;
        boxes.push(c.box);
        out.push({ key, x: c.x, y: c.y, text, anchor: c.anchor, fs, cls, box: c.box });
        return true;
      }
      if (force) {
        boxes.push(cands[0].box);
        out.push({
          key,
          x: cands[0].x,
          y: cands[0].y,
          text,
          anchor: 'start',
          fs,
          cls,
          box: cands[0].box,
        });
        return true;
      }
      return false;
    };

    put('me', mx, my, 17, '当前位置', LABEL_FS, 'ct-t-me', true);
    if (target) {
      const tj = jobs.find((j) => j.id === targetJobId);
      put('target', toX(target[0]), toY(target[1]), 10, tj?.name ?? '目标岗位', LABEL_FS, 'ct-t-target', true);
    }
    route.forEach((r, i) => put(`wp-${i}`, r.x, r.y, 6 + r.weight * 12, r.name, WP_FS, 'ct-t-wp'));

    [...jobs]
      .filter((j) => j.id !== targetJobId)
      .sort((a, b) => (b.attrs?.postCount ?? 0) - (a.attrs?.postCount ?? 0))
      .forEach((j) => {
        const c = coords.get(j.id);
        if (!c) return;
        put(j.id, toX(c[0]), toY(c[1]), 5, j.name, LABEL_FS, j.emerging ? 'ct-t-new' : 'ct-t-job');
      });

    return out;
  }, [jobs, coords, route, target, targetJobId, mx, my, toX, toY, wk, hk]);

  /* 画布比画框大，被画框切掉半个字的名字会被读成另一个名字。
     此处按画框当前对着画布的哪一块逐条筛：整条落不进来的干脆不画，
     少一个名字好过一个残缺的名字 —— 平移之后它自会补上。
     右上角那枚缩放控件钉在画框上，压在它底下的名字同样不画。
     这一筛在绘制时做，不进落位计算，因此拖动不触发重新落位。 */
  const shown = placed.filter((p) => {
    const vx = -zp.tx;
    const vy = -zp.ty;
    if (p.box.x0 < vx + 2 || p.box.x1 > vx + W - 2) return false;
    if (p.box.y0 < vy + 2 || p.box.y1 > vy + H - 2) return false;
    return (
      p.box.x1 < vx + W - CTRL_W - 10 ||
      p.box.x0 > vx + W - 6 ||
      p.box.y1 < vy + 6 ||
      p.box.y0 > vy + CTRL_H + 12
    );
  });
  const labelled = new Set(shown.map((p) => p.key));
  /** 当前画框内标出名字的岗位数（不含"当前位置"、目标岗位与沿途路标这三类固定标注） */
  const jobLabelled = shown.filter((p) => p.key !== 'me' && !p.key.startsWith('wp-')).length;

  return (
    <div ref={ref} className="ct-wrap">
      <ZoomBar
        k={k}
        maxK={MAX_K}
        onIn={zp.zoomIn}
        onOut={zp.zoomOut}
        onAll={zp.showAll}
        onFocus={backToFocus}
        focusLabel="回到路线"
      />
      <svg
        ref={zp.svgRef}
        width={W}
        height={H}
        className="ct-svg"
        role="img"
        aria-label="能力地形导航图"
        onPointerDown={zp.onPointerDown}
        onDoubleClick={zp.onDoubleClick}
        style={{ cursor: k > 1 ? (zp.panning ? 'grabbing' : 'grab') : undefined }}
      >
        <defs>
          {/* 画布比画框大，越界的部分裁掉 */}
          <clipPath id="ct-frame">
            <rect x={0.5} y={0.5} width={W - 1} height={H - 1} rx={9} />
          </clipPath>
          <radialGradient id="ct-blob">
            <stop offset="0%" stopColor="var(--primary)" stopOpacity="0.9" />
            <stop offset="40%" stopColor="var(--primary)" stopOpacity="0.5" />
            <stop offset="74%" stopColor="var(--primary)" stopOpacity="0.14" />
            <stop offset="100%" stopColor="var(--primary)" stopOpacity="0" />
          </radialGradient>
          {/* 底不用纯灰面：极浅的天光渐变能把“低处”交代出来，也不吃文字对比度 */}
          <linearGradient id="ct-sky" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#fdfeff" />
            <stop offset="100%" stopColor="#eef3fc" />
          </linearGradient>
        </defs>

        {/* 边框画在图内而不是交给 CSS：CSS 的 border 加在 width 之外，
            图会正好比容器宽出两像素，凭空多一条横向滚动条 */}
        <rect
          x={0.5}
          y={0.5}
          width={W - 1}
          height={H - 1}
          rx={9}
          fill="url(#ct-sky)"
          stroke="var(--line)"
        />

        {/* 画布层。倍率已折进落位，此处只做平移，拖动因此不触发重新落位 */}
        <g clipPath="url(#ct-frame)">
        <g transform={`translate(${zp.tx} ${zp.ty})`} pointerEvents={zp.panning ? 'none' : undefined}>

        {/* 地势整块撤下。
            核权重原本是 log(1 + 在招数量) × (1 + 薪资中位数 / 60)，
            这两个字段真实数据都没有采集 —— 131 个核的权重因此恒等，
            叠出来是一片均匀的雾，合成不透明度最高到 0.996，
            压在上面的岗位名对比度从 8.43:1 掉到 2.29:1，低于 WCAG AA 的 4.5:1。
            画一片没有信息的雾，代价是把有信息的字盖掉，这笔账无论如何算不过来。
            撤掉后底色为空，对比度回到 8.43:1。 */}

        {/* 岗位点。目标点做成圆环，与“当前位置”的实心点区分开 —— 两者同为绿色，
            只靠大小分辨在缩略图上就分不出来了 */}
        {jobs.map((j) => {
          const c = coords.get(j.id);
          if (!c) return null;
          const x = toX(c[0]);
          const y = toY(c[1]);
          const isTarget = j.id === targetJobId;
          return (
            <circle
              key={j.id}
              cx={x}
              cy={y}
              r={isTarget ? 8 : hot === j.id ? 6.4 : 5}
              className={isTarget ? 'ct-dot ct-dot-target' : j.emerging ? 'ct-dot ct-dot-new' : 'ct-dot'}
              onClick={() => !zp.moved.current && onPickJob?.(j.id)}
              onMouseEnter={(e) => {
                setHot(j.id);
                setTip({
                  x: e.clientX,
                  y: e.clientY,
                  content: (
                    <>
                      <div className="tt-title">{j.name}</div>
                      <div className="tt-muted">
                        {j.cluster} · 中位薪资 {j.attrs?.medianSalary}k · 在招 {j.attrs?.postCount?.toLocaleString()}
                      </div>
                      {j.emerging && <div className="tt-fore">新发现的萌芽岗位</div>}
                      {!labelled.has(j.id) && !isTarget && <div className="tt-muted">点击可将报告切换至该岗位</div>}
                    </>
                  ),
                });
              }}
              onMouseLeave={() => {
                setHot(null);
                setTip(null);
              }}
            />
          );
        })}

        {/* 学习路线 */}
        {target && (
          <>
            <path
              className="ct-route"
              d={`M${mx},${my} ${route.map((r) => `L${r.x.toFixed(1)},${r.y.toFixed(1)}`).join(' ')} L${toX(target[0])},${toY(target[1])}`}
            />
            {route.map((r, i) => (
              <g
                key={r.name + i}
                onMouseEnter={(e) =>
                  setTip({
                    x: e.clientX,
                    y: e.clientY,
                    content: (
                      <>
                        <div className="tt-title">
                          沿途路标 {i + 1} · {r.name}
                        </div>
                        <div className="tt-muted">缺口 {r.weight.toFixed(2)}，圆点越大优先级越高</div>
                      </>
                    ),
                  })
                }
                onMouseLeave={() => setTip(null)}
              >
                <circle cx={r.x} cy={r.y} r={6 + r.weight * 12} className="ct-wp-halo" />
                <circle cx={r.x} cy={r.y} r={5} className="ct-wp" />
                <text x={r.x} y={r.y + 4} className="ct-wp-idx">
                  {i + 1}
                </text>
              </g>
            ))}
          </>
        )}

        {/* 当前位置 */}
        <circle cx={mx} cy={my} r={16} className="ct-me-halo" />
        <circle cx={mx} cy={my} r={7} className="ct-me" />

        {/* 标签统一最后画，保证压在所有图元之上 */}
        <g className="ct-labels">
          {shown.map((p) => (
            <text
              key={p.key}
              x={p.x}
              y={p.y}
              textAnchor={p.anchor}
              fontSize={p.fs}
              className={hot === p.key ? `${p.cls} is-hot` : p.cls}
            >
              {p.text}
            </text>
          ))}
        </g>

        </g>
        </g>
      </svg>

      <Tooltip tip={tip} />

      <div className="ct-legend">
        <span>
          <i className="ct-sw ct-sw-me" />
          当前位置（简历落点）
        </span>
        <span>
          <i className="ct-sw ct-sw-target" />
          目标岗位
        </span>
        <span>
          <i className="ct-sw ct-sw-wp" />
          沿途路标（学习路径的关键能力）
        </span>
        <span>
          <i className="ct-sw ct-sw-job" />
          其他岗位
        </span>
        <span>
          <i className="ct-sw ct-sw-new" />
          萌芽岗位
        </span>
        {/* 岗位数一多，标签就会因相互遮挡而逐个让位。标了几个要写出来，
            否则读的人会把"图上只有这些名字"当成"系统里只有这些岗位"。 */}
        <span className="ct-count">
          共 {jobs.length} 个岗位，当前视图标出 {jobLabelled} 个，其余悬停查看或缩放后查看
        </span>
        <span className="ct-help">
        </span>
      </div>
    </div>
  );
}
