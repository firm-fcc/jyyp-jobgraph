import { useCallback, useEffect, useRef, useState } from 'react';

/* ============================================================
   图内缩放与平移

   点云类的图有一个共同的两难：一屏之内画下全部点，单点就小到读不出名字；
   只画关心的那一块，又失去“它在整体的哪一带”这个参照。此处的解法是
   默认落在局部、随时可退回整图 —— 与看地图是同一套动作。

   实现上把“倍率”折进落位计算，而不是给整张 SVG 套一个 scale：
   套 scale 会连字号一起放大，图上的字就不再是 12px；把倍率折进坐标之后，
   点之间的距离随倍率展开，字号保持不变，放大反而让更多标签落得下地方。
   平移则是纯粹的 translate，不触发重新落位，拖动时不必反复算避让。

   坐标系有两层，下文一律按此称呼：
     · 基准坐标 —— 倍率为 1、整图恰好铺满画框时的像素位置
     · 画布坐标 —— 基准坐标 × 倍率，即实际绘制用的位置
   画框（frame）指容器给出的那块可见区域，尺寸为 w × h。
   ============================================================ */

export interface ZoomBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

interface Options {
  /** 画框尺寸 */
  w: number;
  h: number;
  /** 倍率上限 */
  maxK?: number;
  /** 按钮每次的倍率步长 */
  step?: number;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

export function useZoomPan({ w, h, maxK = 4, step = 1.5 }: Options) {
  const [view, setView] = useState({ k: 1, tx: 0, ty: 0 });
  const [panning, setPanning] = useState(false);
  const svgRef = useRef<SVGSVGElement | null>(null);
  /** 按下时的指针位置与当时的位移，拖动过程中据此算增量 */
  const from = useRef({ px: 0, py: 0, tx: 0, ty: 0 });
  /** 这一次按下之后指针是否移动过。移动过即为拖图，不应再当作对图元的点击 */
  const moved = useRef(false);

  /** 把任意一组取值收进合法范围：倍率不小于 1，且画布四边不脱离画框 */
  const legal = useCallback(
    (k: number, tx: number, ty: number) => {
      const kk = clamp(k, 1, maxK);
      return {
        k: kk,
        tx: w > 0 ? clamp(tx, w - w * kk, 0) : 0,
        ty: h > 0 ? clamp(ty, h - h * kk, 0) : 0,
      };
    },
    [w, h, maxK],
  );

  /** 画框尺寸变了（换视口、面板改宽）之后按同一组取值重新收一次 */
  useEffect(() => {
    setView((v) => {
      const n = legal(v.k, v.tx, v.ty);
      return n.k === v.k && n.tx === v.tx && n.ty === v.ty ? v : n;
    });
  }, [legal]);

  /** 以画框内某点为不动点缩放；不给点则取画框中心 */
  const zoomBy = useCallback(
    (factor: number, px?: number, py?: number) =>
      setView((v) => {
        const cx = px ?? w / 2;
        const cy = py ?? h / 2;
        const k = clamp(v.k * factor, 1, maxK);
        const bx = (cx - v.tx) / v.k;
        const by = (cy - v.ty) / v.k;
        return legal(k, cx - bx * k, cy - by * k);
      }),
    [w, h, maxK, legal],
  );

  const zoomIn = useCallback(() => zoomBy(step), [zoomBy, step]);
  const zoomOut = useCallback(() => zoomBy(1 / step), [zoomBy, step]);
  /** 退回整图 */
  const showAll = useCallback(() => setView({ k: 1, tx: 0, ty: 0 }), []);

  /**
   * 让一块基准坐标下的矩形落在画框正中，并按它的尺寸定倍率。
   * pad 为四周留出的画框像素，cap 为该次取景允许的倍率上限 ——
   * 目标区域过小时不宜一路放到上限，否则周边一个可参照的点都不剩。
   */
  const fitTo = useCallback(
    (box: ZoomBox, pad = 48, cap = maxK) => {
      if (w <= 0 || h <= 0) return;
      const bw = Math.max(1, box.x1 - box.x0);
      const bh = Math.max(1, box.y1 - box.y0);
      const k = clamp(
        Math.min((w - pad * 2) / bw, (h - pad * 2) / bh),
        1,
        Math.min(cap, maxK),
      );
      const cx = (box.x0 + box.x1) / 2;
      const cy = (box.y0 + box.y1) / 2;
      setView(legal(k, w / 2 - cx * k, h / 2 - cy * k));
    },
    [w, h, maxK, legal],
  );

  /* ---------------- 拖动平移 ----------------
     指针捕获（setPointerCapture）此处不能用：捕获之后 click 一并派发给
     捕获元素，图元上的“点击选中”就失效了。改为拖动期间把 move / up
     挂到 window 上，click 的目标不变，再靠 moved 把拖动尾巴上的那一次
     点击滤掉。 */
  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (e.button !== 0 || view.k <= 1) return;
      moved.current = false;
      from.current = { px: e.clientX, py: e.clientY, tx: view.tx, ty: view.ty };
      setPanning(true);
    },
    [view.k, view.tx, view.ty],
  );

  useEffect(() => {
    if (!panning) return;
    const move = (e: PointerEvent) => {
      const dx = e.clientX - from.current.px;
      const dy = e.clientY - from.current.py;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) moved.current = true;
      setView((v) => legal(v.k, from.current.tx + dx, from.current.ty + dy));
    };
    const stop = () => setPanning(false);
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop);
    window.addEventListener('pointercancel', stop);
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', stop);
      window.removeEventListener('pointercancel', stop);
    };
  }, [panning, legal]);

  /* 滚轮须按住 Ctrl / ⌘ 才缩放：图高六百余像素，直接劫持滚轮会让页面
     滚到这里就卡住。监听须为非被动，否则 preventDefault 不生效，
     Ctrl+滚轮会被浏览器当成整页缩放 —— React 的 onWheel 正是被动挂载。 */
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoomBy(e.deltaY < 0 ? 1.18 : 1 / 1.18, e.clientX - r.left, e.clientY - r.top);
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [zoomBy]);

  /** 双击就地放大一档 */
  const onDoubleClick = useCallback(
    (e: React.MouseEvent) => {
      const r = (e.currentTarget as Element).getBoundingClientRect();
      zoomBy(step, e.clientX - r.left, e.clientY - r.top);
    },
    [zoomBy, step],
  );

  return {
    ...view,
    /** 画布尺寸 = 画框 × 倍率。落位与避让一律按这个范围算 */
    wk: w * view.k,
    hk: h * view.k,
    panning,
    /** 供图元的点击处理判定：这一次是拖图的收尾，不是点击 */
    moved,
    svgRef,
    zoomIn,
    zoomOut,
    showAll,
    fitTo,
    onPointerDown,
    onDoubleClick,
  };
}
