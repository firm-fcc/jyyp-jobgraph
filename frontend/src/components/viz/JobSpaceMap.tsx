/* ============================================================
   新岗位空间关系图 —— 新簇挨着哪个已有岗位圈，又差多远

   相图那张按“首现多久 × 确认强度”排点，读的是“它是不是真的在长出来”。
   长出来之后紧接着的一问是“它到底是什么”，而回答这一问最省事的办法，
   是指出它离哪个已有岗位最近：体系内的 131 个规范岗位是现成的参照系，
   说“贴着算法工程师那一圈，但差 0.18”比任何一段定义都快。

   坐标由 data/jobSpace 算出（任务向量的多维标度投影，见该文件说明）。
   本文件只负责落位与画法，两件事分开是因为坐标要接后端预计算的投影，
   而画法不该跟着换。

   ------------------------------------------------------------
   画法取自两处

   ① 圆形字形（RumorLens, CHI'22 图 2）：一个点同时说三件事 ——
      内圆大小是市场占比，外环两段弧是论文与新闻各占多少，
      内圆填色是两者按比例调出的中间色。三件事叠在一个点上而不是三张图上，
      是因为它们要一起读：“占比还小、但论文那一段特别长”才是值得盯的那种新岗位。
   ② 大类光晕与右下角那组尺寸图例（《新闻大学》“就业市场中的专业需求与共现关系”）：
      用一团渐隐的底色表示“圈”，比实线边界诚实 —— 大类的覆盖范围本来就没有硬边。
      原图是深底星空，这里换成浅底：全站是浅色界面，为一张图翻一次底色，
      读者会先花几秒去想“这块为什么是黑的”，那几秒本该用来看点在哪。
      浅底上光晕压到 3.5% ~ 7.5%：放大到密集区之后多圈相叠，再深就糊成一层蓝雾。

   ------------------------------------------------------------
   默认给的是局部而非整图。131 个点铺满一屏之后单点只剩几像素、名字大半落不下，
   而这张图要回答的偏偏是“它贴着哪一圈、差多远”。默认取景落在选中新岗位
   与它三个相近岗位那一块，另给控件退回整图。倍率折进落位计算而不是套在
   SVG 的 scale 上：后者会连字号一起放大 —— 缩放机制见 hooks/useZoomPan。
   ============================================================ */

import { useCallback, useEffect, useMemo, useState } from 'react';
import type { JobSpace, JobSpacePoint } from '@/data/jobSpace';
import { Tooltip, type TipState } from '@/components/common/Tooltip';
import { useSize } from '@/hooks/useSize';
import { useZoomPan } from '@/hooks/useZoomPan';
import { ZoomBar } from '@/components/viz/ZoomBar';
import { measureText } from '@/utils/viz';

interface Props {
  space: JobSpace;
  selectedId: string | null;
  /** 落在当前筛选条件内的新岗位；其余压暗 */
  focusIds?: Set<string>;
  /** 右栏对照卡片当前指到的那个已有岗位，图上同步加重 */
  peerId?: string | null;
  onSelect: (id: string) => void;
  /** 图上指到某个相近岗位时回传，供右栏对照卡片同步加重 */
  onPeerHover?: (id: string | null) => void;
}

/* 图场高度跟着宽度走。
   两轴共用一个标尺，所以点云的长宽比由数据定死，图场比它更扁的话，
   多出来的宽度只会变成左右两条空白 —— 面板越宽，空得越多。
   下限此前取 470，与右栏三张对照卡片的总高差出三百余像素，图下方空出一大块；
   现改为 600，且右栏在同一行内自行滚动（见 jobs.css 的 .jsp-peers-sc），
   两栏底边由此对齐。上限相应放宽，超宽视口下图不至于只占半屏。 */
const ratio = (w: number) => Math.round(Math.max(600, Math.min(760, w * 0.78)));
/** 点不贴图场的边，给标签留出地方 */
const INSET = 56;
/** 缩放倍率上限。四倍已能把最密的一簇拆开，再高只剩几个点，失去参照 */
const MAX_K = 4;

/* 两个来源色直接取全站的 --src-paper / --src-news。
   中间色由它们按比例插值，因此这两个值必须在 JS 里拿得到 —— 写成 CSS 变量
   就只能交给浏览器求值，插不出中间的那些点。图例里的色标同源。 */
const PAPER_HEX = '#7c5cfc';
const NEWS_HEX = '#d97706';

/* 相近岗位的记号色，与右栏卡片上的序号牌同源（--primary-deep）。
   此前这三个标注混在一片同族蓝里认不出来，改法不在色相上，而在三处：
   名字提到与新岗位同一档字号并加粗、环外垫一圈白因而压在别的点上也分得开、
   选中项一旦成立其余已有岗位的点同时压暗一档，只留这三个是饱和的。 */
const PEER_HEX = '#1d4ed8';
/** 被指到时压深一档，与右栏卡片的加重同步 */
const PEER_HEX_ON = '#173bb0';

const LABEL_FONT = 12.5;
/** 相近岗位的名字左右各多占的一点地方，免得加粗之后贴上隔壁 */
const PEER_PAD = 3;
/** 右上角那枚缩放控件占的地方，标签绘制时据此避让（见 global.css 的 .viz-zoom） */
const CTRL_W = 208;
const CTRL_H = 32;
/** 碰撞疏解允许的最大位移。超过这个数，图上的位置就不再是投影出来的位置了 */
const MAX_SHIFT = 9;

type Anchor = 'start' | 'end' | 'middle';
type Box = [number, number, number, number];

/** 序号牌的半径，以及它挂在圆点外的哪一侧。八个方位由右上起顺时针试 */
const BADGE_R = 7.2;
const BADGE_DIRS: [number, number][] = [
  [0.72, -0.72],
  [-0.72, -0.72],
  [0.72, 0.72],
  [-0.72, 0.72],
  [1, 0],
  [0, -1],
  [-1, 0],
  [0, 1],
];

const overlap = (a: Box, b: Box) => !(a[2] < b[0] || b[2] < a[0] || a[3] < b[1] || b[3] < a[1]);

const cross = (ax: number, ay: number, bx: number, by: number, px: number, py: number) =>
  (bx - ax) * (py - ay) - (by - ay) * (px - ax);

/** 线段与矩形是否相交（线段整段落在框内也算） */
function segHits(x0: number, y0: number, x1: number, y1: number, b: Box): boolean {
  if (Math.max(x0, x1) < b[0] || Math.min(x0, x1) > b[2]) return false;
  if (Math.max(y0, y1) < b[1] || Math.min(y0, y1) > b[3]) return false;
  if (x0 >= b[0] && x0 <= b[2] && y0 >= b[1] && y0 <= b[3]) return true;
  if (x1 >= b[0] && x1 <= b[2] && y1 >= b[1] && y1 <= b[3]) return true;
  const edge = (ax: number, ay: number, bx: number, by: number) =>
    cross(x0, y0, x1, y1, ax, ay) > 0 !== cross(x0, y0, x1, y1, bx, by) > 0 &&
    cross(ax, ay, bx, by, x0, y0) > 0 !== cross(ax, ay, bx, by, x1, y1) > 0;
  return (
    edge(b[0], b[1], b[2], b[1]) ||
    edge(b[2], b[1], b[2], b[3]) ||
    edge(b[2], b[3], b[0], b[3]) ||
    edge(b[0], b[3], b[0], b[1])
  );
}

/** 两个端点色按比例调出中间色。t = 新闻占比 */
function blend(t: number): string {
  const hex = (s: string) => [
    parseInt(s.slice(1, 3), 16),
    parseInt(s.slice(3, 5), 16),
    parseInt(s.slice(5, 7), 16),
  ];
  const [r0, g0, b0] = hex(PAPER_HEX);
  const [r1, g1, b1] = hex(NEWS_HEX);
  const c = (a: number, b: number) => Math.round(a + (b - a) * Math.max(0, Math.min(1, t)));
  return `rgb(${c(r0, r1)}, ${c(g0, g1)}, ${c(b0, b1)})`;
}

/** 圆环上的一段弧。角度顺时针，0 在正上方 */
function arc(cx: number, cy: number, r: number, a0: number, a1: number): string {
  const p = (a: number): [number, number] => [cx + r * Math.sin(a), cy - r * Math.cos(a)];
  const [x0, y0] = p(a0);
  const [x1, y1] = p(a1);
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return `M${x0.toFixed(2)},${y0.toFixed(2)} A${r},${r} 0 ${large} 1 ${x1.toFixed(2)},${y1.toFixed(2)}`;
}

const pct = (v: number) => `${(v * 100).toFixed(v < 0.01 ? 2 : 1)}%`;

/** 有信号强度、却一条原文都抽不到的那一侧。两侧都齐时返回空串 */
function missingSide(p: JobSpacePoint): string {
  const has = (t: 'paper' | 'news') => p.cite.some((c) => c.sourceType === t);
  const out: string[] = [];
  if (p.paperShare > 0.001 && !has('paper')) out.push('论文');
  if (p.newsShare > 0.001 && !has('news')) out.push('新闻');
  return out.join('与');
}

interface Label {
  id: string;
  text: string;
  x: number;
  y: number;
  x0: number;
  w: number;
  size: number;
  anchor: Anchor;
  kind: 'new' | 'ref' | 'ring';
  strong: boolean;
  /** 是不是选中项那三个相近岗位之一。这三个的名字提字号、加粗 */
  peer?: boolean;
  /** 大类名后面那个成员数。有它才一眼分得出这是一片区域而不是一个岗位 */
  note?: string;
  /** 引线：贴着点放不下时，标签移远并拉一条线回来 */
  lead?: [number, number, number, number];
}

export function JobSpaceMap({
  space,
  selectedId,
  focusIds,
  peerId = null,
  onSelect,
  onPeerHover,
}: Props) {
  const { ref, w } = useSize<HTMLDivElement>();
  const [tip, setTip] = useState<TipState | null>(null);

  const W = Math.max(460, w || 760);
  const H = ratio(W);
  /* 131 个已有岗位加上新簇，整幅铺进一屏之后单点只剩几像素，
     名字也大半落不下。默认因此不给整图，而是落在选中项与它三个相近岗位
     那一块，随时可退回整图 —— 控件与操作见 hooks/useZoomPan。 */
  const zp = useZoomPan({ w: W, h: H, maxK: MAX_K });
  const { k, wk, hk } = zp;
  /* 筛选只压暗新岗位。已有岗位是这张图的参照系，把参照系一起压暗，
     “离哪个圈近”就没有东西可参照了 —— 筛选条里本来也筛不到它们。 */
  const lit = (id: string) => !focusIds || focusIds.has(id) || id === selectedId;

  /* ---------------- 落位 ----------------
     两轴共用一个标尺，剩下的空白左右上下均分：这张图的读法全在距离上，
     任何一边被单独拉伸，“贴着但有距离”就读不准了。

     基准坐标即倍率为 1、整图恰好铺满画框时的位置；实际绘制用的是它乘上倍率。
     倍率折进坐标而不是套在 SVG 的 scale 上：后者会连字号一起放大。 */
  const geoBase = useMemo(() => {
    const boxW = W - INSET * 2;
    const boxH = H - INSET * 2;
    const s = Math.min(boxW / (space.spanX || 1), boxH / (space.spanY || 1));
    return {
      s,
      ox: INSET + (boxW - space.spanX * s) / 2,
      oy: INSET + (boxH - space.spanY * s) / 2,
    };
  }, [W, H, space.spanX, space.spanY]);

  const geo = useMemo(
    () => ({ s: geoBase.s * k, ox: geoBase.ox * k, oy: geoBase.oy * k }),
    [geoBase, k],
  );

  const rOf = useMemo(() => {
    const max = space.maxShare || 1;
    return (share: number) => 2.2 + 9.4 * Math.sqrt(Math.max(0, share) / max);
  }, [space.maxShare]);

  /* 色标按本页新岗位的实际比值区间定标，不按理论上的 0 ~ 100%。
     两段弧仍按原始占比画、浮层也仍报原始占比 —— 定标只让颜色之间可比，不改数。 */
  const tone = useMemo(() => {
    const [lo, hi] = space.leanRange;
    const span = hi - lo;
    return (p: JobSpacePoint) =>
      blend(p.foresight > 1e-9 && span > 1e-6 ? (p.newsShare - lo) / span : 0.5);
  }, [space.leanRange]);

  /**
   * 像素落位 + 碰撞疏解。
   *
   * 131 个已有岗位投到二维必然堆叠，堆成一坨就看不出“新簇挨着哪一圈”。
   * 这里只把已有岗位互相推开，新岗位钉死不动 —— 新岗位的落位是这张图的结论，
   * 结论不能为了好看而挪。位移另有上限，超过上限即视为疏解失败、宁可继续叠。
   */
  const pts = useMemo(() => {
    const base = space.points.map((p) => ({
      p,
      x: geo.ox + p.x * geo.s,
      y: geo.oy + (space.spanY - p.y) * geo.s,
      /* 新岗位的内圆按其前瞻强度定大小，不按市场占比：这批岗位尚未进入招聘市场，
         占比一律为零，按占比取一律缩到半径下限，图上只剩一个两像素的点。
         两者不同量纲，故新岗位一路单独定标——它们之间可比，与既有岗位的圈
         不可比，后者本就由市场占比说话，图例也分开列。 */
      r: p.job.emerging ? 4.6 + 5.2 * Math.sqrt(Math.min(1, Math.max(0, p.foresight))) : rOf(p.share),
      pin: !!p.job.emerging,
    }));
    const home = base.map((b) => ({ x: b.x, y: b.y }));

    const cell = 30;
    for (let it = 0; it < 9; it++) {
      const grid = new Map<string, number[]>();
      base.forEach((b, i) => {
        const key = `${Math.floor(b.x / cell)},${Math.floor(b.y / cell)}`;
        const list = grid.get(key);
        if (list) list.push(i);
        else grid.set(key, [i]);
      });

      for (let i = 0; i < base.length; i++) {
        const a = base[i];
        const gx = Math.floor(a.x / cell);
        const gy = Math.floor(a.y / cell);
        for (let dx = -1; dx <= 1; dx++)
          for (let dy = -1; dy <= 1; dy++)
            for (const j of grid.get(`${gx + dx},${gy + dy}`) ?? []) {
              if (j <= i) continue;
              const b = base[j];
              const need = a.r + b.r + 2.4;
              let ddx = b.x - a.x;
              let ddy = b.y - a.y;
              let d = Math.hypot(ddx, ddy);
              if (d >= need) continue;
              if (d < 1e-6) {
                // 完全重合时给一个由下标定死的方向，避免随机数
                ddx = Math.cos(i * 2.399);
                ddy = Math.sin(i * 2.399);
                d = 1;
              }
              const push = (need - d) / d / 2;
              if (!a.pin) {
                a.x -= ddx * push;
                a.y -= ddy * push;
              }
              if (!b.pin) {
                b.x += ddx * push;
                b.y += ddy * push;
              }
            }
      }
    }

    return base.map((b, i) => {
      const dx = b.x - home[i].x;
      const dy = b.y - home[i].y;
      const d = Math.hypot(dx, dy);
      const k = d > MAX_SHIFT ? MAX_SHIFT / d : 1;
      return { ...b, x: home[i].x + dx * k, y: home[i].y + dy * k };
    });
  }, [space.points, space.spanY, geo, rOf]);

  const byId = useMemo(() => new Map(pts.map((p) => [p.p.job.id, p])), [pts]);
  const emerging = useMemo(() => pts.filter((p) => p.p.job.emerging), [pts]);

  /* 选中新岗位的三个最近已有岗位，键为岗位 id、值为名次。
     图上给它们套环加序号，右栏逐个展开对照，两侧靠同一个名次对上号 ——
     没有序号的话，右栏第二张卡片对应图上哪一个点，只能靠名字去找。 */
  const peerRank = useMemo(() => {
    const m = new Map<string, number>();
    const sel = emerging.find((p) => p.p.job.id === selectedId);
    sel?.p.near.forEach((nb, i) => m.set(nb.id, i + 1));
    return m;
  }, [emerging, selectedId]);

  /** 选中的那个新岗位。它的连线、读数、三个相近岗位的标注都挂在它身上 */
  const selPoint = useMemo(
    () => emerging.find((p) => p.p.job.id === selectedId) ?? null,
    [emerging, selectedId],
  );

  /* ---------------- 默认取景 ----------------
     默认给的是局部而非整图：这张图要回答的是“它贴着哪一圈、差多远”，
     131 个点铺满一屏之后，那几个点连名字都落不下。取景框取选中新岗位
     与它三个相近岗位的外接矩形；未选中时取全部新岗位的外接矩形 ——
     那是本页关心的那一带。留白 88px，倍率封在 2.8 倍，
     否则四个挨得极近的点会被放到周边一个可参照的岗位都不剩。 */
  const focusBox = useMemo(() => {
    const bx = (p: JobSpacePoint) => geoBase.ox + p.x * geoBase.s;
    const by = (p: JobSpacePoint) => geoBase.oy + (space.spanY - p.y) * geoBase.s;
    const sel = space.points.find((p) => p.job.id === selectedId && p.job.emerging);
    /* 选中项没有相近岗位时（任务向量为空，见 jobSpace 的 grounded），
       取景框会退化成这一个点周围的一小块，放大之后图上只剩它自己 ——
       而它恰恰是最需要参照系的那一类：没有连线、没有距离读数，
       全靠周边的岗位圈给出方位。此时取全部新岗位的外接矩形，与未选中时同框。 */
    const group =
      sel && sel.near.length > 0
        ? [sel, ...sel.near.map((n) => space.points.find((p) => p.job.id === n.id)!).filter(Boolean)]
        : space.points.filter((p) => p.job.emerging);
    if (group.length === 0) return null;
    const xs = group.map(bx);
    const ys = group.map(by);
    return { x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) };
  }, [space, selectedId, geoBase]);

  const { fitTo } = zp;
  const backToFocus = useCallback(() => {
    if (focusBox) fitTo(focusBox, 88, 2.8);
  }, [focusBox, fitTo]);
  useEffect(() => {
    backToFocus();
  }, [backToFocus]);

  /* 序号牌的落位。三个相近岗位本来就相互挨得近（都贴着同一个新簇），
     一律挂在右上角时，牌子会压到隔壁那个的环上。此处按名次先后挑方位，
     躲开已经放好的牌子与另外两个环；八个方位都躲不开就仍用右上角，
     压一点边总好过把牌子甩到认不出是谁的地方。 */
  const badges = useMemo(() => {
    const peers = pts
      .filter((p) => peerRank.has(p.p.job.id))
      .sort((a, b) => peerRank.get(a.p.job.id)! - peerRank.get(b.p.job.id)!)
      .map((p) => ({ p, ring: p.r + 4.6 }));

    const out: { id: string; rank: number; ring: number; bx: number; by: number }[] = [];
    for (const { p, ring } of peers) {
      const gap = ring + BADGE_R + 1.6;
      let hit = { bx: p.x + gap * 0.72, by: p.y - gap * 0.72 };
      for (const [dx, dy] of BADGE_DIRS) {
        const bx = p.x + gap * dx;
        const by = p.y + gap * dy;
        if (bx < BADGE_R + 4 || bx > wk - BADGE_R - 4 || by < BADGE_R + 4 || by > hk - BADGE_R - 4)
          continue;
        const onBadge = out.some((o) => Math.hypot(o.bx - bx, o.by - by) < BADGE_R * 2 + 3);
        const onRing = peers.some(
          (q) => q.p !== p && Math.hypot(q.p.x - bx, q.p.y - by) < q.ring + BADGE_R + 2,
        );
        if (onBadge || onRing) continue;
        hit = { bx, by };
        break;
      }
      out.push({ id: p.p.job.id, rank: peerRank.get(p.p.job.id)!, ring, ...hit });
    }
    return out;
  }, [pts, peerRank, wk, hk]);

  /* 标名的圈：挨着某个新岗位的，加上最大的三个。
     前者是这张图要指的地方，后者给整片点云一个方位感 ——
     一张只有几个孤立地名的地图，读者认不出自己在看哪一带。 */
  const rings = useMemo(() => {
    const big = new Set(
      [...space.rings].sort((a, b) => b.count - a.count).slice(0, 3).map((g) => g.name),
    );
    return space.rings.map((g) => ({
      ...g,
      named: g.adjacent || big.has(g.name),
      px: geo.ox + g.cx * geo.s,
      py: geo.oy + (space.spanY - g.cy) * geo.s,
      pr: g.r * geo.s,
    }));
  }, [space.rings, space.spanY, geo]);

  /* 选中项那三条连线上的距离读数。
     三个相近岗位挨得近时，三个中点会连同序号牌一起堆在同一小块地方。
     此处让读数沿着自己那条线前后挪，躲开序号牌与另外两个读数；
     线太短、前后都挪不开的就不标 —— 该读数在右栏卡片上另有一份，
     图上宁可空着，也不要把序号盖住：序号一旦读不出，左右两侧就对不上号。 */
  const distMarks = useMemo(() => {
    if (!selPoint) return [];
    const sel = selPoint;
    const taken: Box[] = badges.map((b) => [
      b.bx - BADGE_R - 1,
      b.by - BADGE_R - 1,
      b.bx + BADGE_R + 1,
      b.by + BADGE_R + 1,
    ]);
    const out: { id: string; x: number; y: number; text: string; first: boolean }[] = [];
    sel.p.near.forEach((nb, i) => {
      const to = byId.get(nb.id);
      if (!to) return;
      const text = nb.dist.toFixed(3);
      const tw = measureText(text, 12);
      for (const t of [0.5, 0.62, 0.38, 0.72, 0.28]) {
        const x = sel.x + (to.x - sel.x) * t;
        const y = sel.y + (to.y - sel.y) * t - 5;
        // 两端的圆点各自占一块，读数压在上面就分不清它是哪条线的
        if (Math.hypot(x - sel.x, y - sel.y) < sel.r + 10) continue;
        if (Math.hypot(x - to.x, y - to.y) < to.r + 10) continue;
        const box: Box = [x - tw / 2 - 2, y - 12, x + tw / 2 + 2, y + 3];
        if (taken.some((b) => overlap(b, box))) continue;
        taken.push(box);
        out.push({ id: nb.id, x, y, text, first: i === 0 });
        break;
      }
    });
    return out;
  }, [selPoint, badges, byId]);

  /* 图上实际画出的那些连线。标签落位要一并躲开：这张图的连线本身就是结论
     （“贴着哪一个”），被一行名字压断就读不出它从哪连到哪。
     两端各让出 15px 不参与判定 —— 端点旁边正是两头自家名字该待的地方。 */
  const links = useMemo(() => {
    const out: [number, number, number, number][] = [];
    for (const p of emerging) {
      const on = !focusIds || focusIds.has(p.p.job.id) || p.p.job.id === selectedId;
      if (!on) continue;
      const list = p.p.job.id === selectedId ? p.p.near : p.p.near.slice(0, 1);
      for (const nb of list) {
        const to = byId.get(nb.id);
        if (!to) continue;
        const dx = to.x - p.x;
        const dy = to.y - p.y;
        const len = Math.hypot(dx, dy);
        if (len < 44) continue;
        const t = 15 / len;
        out.push([p.x + dx * t, p.y + dy * t, to.x - dx * t, to.y - dy * t]);
      }
    }
    return out;
  }, [emerging, byId, selectedId, focusIds]);

  /* ---------------- 标签 ----------------
     三批依次落位，先来的占住地方：
       ① 新岗位 —— 这张图的主角，一个都不能少，贴不下就拉引线移远
       ② 已有岗位类别 —— 给光晕一个名字
       ③ 被指到的那几个已有岗位 —— 只有它们需要被认出来，
          131 个名字全标出来只会糊成一片
  */
  const labels = useMemo(() => {
    const out: Label[] = [];
    const placed: Box[] = [];
    /* 相近岗位的点外面还套着一个环，占位比点本身大一圈 */
    const dots: Box[] = pts.map((p) => {
      const pad = peerRank.has(p.p.job.id) ? p.r + 8 : p.r + 2;
      return [p.x - pad, p.y - pad, p.x + pad, p.y + pad];
    });

    /* 序号牌先占住地方。它按 placed 避让而不是按 dots：dots 那一路允许
       标签压在自家的点上（名字贴着自己的点本来就是对的），而序号牌恰恰
       最容易被自家的名字压住 —— 图上标着 ③、右栏第三张卡片对不上号，
       这一栏的读法就断了。 */
    for (const b of badges)
      placed.push([b.bx - BADGE_R - 2, b.by - BADGE_R - 2, b.bx + BADGE_R + 2, b.by + BADGE_R + 2]);
    /* 距离读数同样先占地方。它是三条连线各自的读数，被名字盖住就不知道
       这个数属于哪一条；而名字另有别处可落，读数只能落在自己那条线上。 */
    for (const d of distMarks) {
      const half = measureText(d.text, 12, 700) / 2 + 3;
      placed.push([d.x - half, d.y - 13, d.x + half, d.y + 4]);
    }

    const fits = (box: Box, selfId?: string) => {
      if (box[0] < 8 || box[2] > wk - 8 || box[1] < 8 || box[3] > hk - 8) return false;
      if (placed.some((b) => overlap(b, box))) return false;
      if (links.some((l) => segHits(l[0], l[1], l[2], l[3], box))) return false;
      return !dots.some((b, i) => pts[i].p.job.id !== selfId && overlap(b, box));
    };

    /** 贴着点放。放得下返回落位，放不下返回 null。pad 为左右多占的那一点地方 */
    const near = (
      p: { x: number; y: number; r: number },
      text: string,
      size: number,
      selfId: string,
      pad = 0,
      weight = 700,
    ): Omit<Label, 'id' | 'kind' | 'strong'> | null => {
      const tw = measureText(text, size, weight);
      const th = size + 3;
      const opts: [number, number, Anchor][] = [
        [p.r + 7, 4, 'start'],
        [-(p.r + 7), 4, 'end'],
        [0, -(p.r + 8), 'middle'],
        [0, p.r + 16, 'middle'],
        [p.r + 7, -9, 'start'],
        [-(p.r + 7), -9, 'end'],
        [p.r + 7, 17, 'start'],
        [-(p.r + 7), 17, 'end'],
        [p.r + 15, 4, 'start'],
        [-(p.r + 15), 4, 'end'],
      ];
      for (const [dx, dy, anchor] of opts) {
        const x = p.x + dx;
        const y = p.y + dy;
        const x0 = anchor === 'start' ? x : anchor === 'end' ? x - tw : x - tw / 2;
        const box: Box = [x0 - 3 - pad, y - th, x0 + tw + 3 + pad, y + 4];
        if (!fits(box, selfId)) continue;
        placed.push(box);
        return { text, x, y, x0, w: tw, size, anchor };
      }
      return null;
    };

    /** 贴不下就往外挪，挪到哪儿拉一条引线回来 */
    const far = (
      p: { x: number; y: number; r: number },
      text: string,
      size: number,
      selfId: string,
      /** 放宽：只躲开别的标签，允许压在圆点上。仅用于最后一轮兜底 */
      loose = false,
      pad = 0,
      weight = 700,
    ): Omit<Label, 'id' | 'kind' | 'strong'> | null => {
      const tw = measureText(text, size, weight);
      const th = size + 3;
      for (const rad of loose ? [26, 38, 52, 70, 92] : [30, 44, 60, 78]) {
        for (let i = 0; i < 16; i++) {
          // 从右上开始逐个试，方向由下标定死，不用随机数
          const ang = (i * Math.PI * 2) / 16 - Math.PI / 4;
          const x = p.x + Math.cos(ang) * rad;
          const y = p.y + Math.sin(ang) * rad + 4;
          const anchor: Anchor = Math.cos(ang) >= 0 ? 'start' : 'end';
          const x0 = anchor === 'start' ? x : x - tw;
          const box: Box = [x0 - 3 - pad, y - th, x0 + tw + 3 + pad, y + 4];
          if (loose) {
            if (box[0] < 8 || box[2] > wk - 8 || box[1] < 8 || box[3] > hk - 8) continue;
            if (placed.some((b) => overlap(b, box))) continue;
          } else if (!fits(box, selfId)) continue;
          placed.push(box);
          const ex = p.x + Math.cos(ang) * (p.r + 2);
          const ey = p.y + Math.sin(ang) * (p.r + 2);
          return {
            text,
            x,
            y,
            x0,
            w: tw,
            size,
            anchor,
            lead: [ex, ey, anchor === 'start' ? x - 3 : x + 3, y - 3.5],
          };
        }
      }
      return null;
    };

    // ① 新岗位。名字一个都不能少，前两轮都放不下就放宽到“只躲开别的标签”
    const news = emerging
      .filter((p) => lit(p.p.job.id))
      .sort((a, b) =>
        a.p.job.id === selectedId ? -1 : b.p.job.id === selectedId ? 1 : b.p.share - a.p.share,
      );
    for (const p of news) {
      const id = p.p.job.id;
      /* 没有任务关联边的那几个，名字后缀一句短的。图上这类点的区别是
         “没有连线、没有读数”—— 那是一处缺席，缺席本身不会自己说明原因，
         不写出来只能靠点开浮层才知道。落位按含后缀的整串宽度算，
         否则后缀会压到隔壁的点上。 */
      const text = p.p.grounded ? p.p.job.name : `${p.p.job.name}（未计距离）`;
      const hit =
        near(p, text, LABEL_FONT, id) ??
        far(p, text, LABEL_FONT, id) ??
        far(p, text, LABEL_FONT, id, true);
      if (hit) out.push({ ...hit, id, kind: 'new', strong: id === selectedId });
    }

    /* ② 大类。绕着光晕试十二个方位，背向点云中心的先试 ——
       朝里那一侧多半压在自家的点上，字压在点上就是两层深色叠在一起。
       十二个方位都占住了就不标：圈还在，只是没名字，好过糊一行看不清的字。 */
    const cx0 = pts.reduce((a, p) => a + p.x, 0) / Math.max(1, pts.length);
    const cy0 = pts.reduce((a, p) => a + p.y, 0) / Math.max(1, pts.length);
    for (const g of rings) {
      if (!g.named) continue;
      const size = LABEL_FONT - 1.5;
      const note = ` ${g.count}`;
      const tw = measureText(g.name + note, size, 600);
      const th = size + 3;
      const out0 = Math.atan2(g.py - cy0, g.px - cx0);
      const dirs = Array.from({ length: 12 }, (_, k) => {
        // 0, +30°, −30°, +60°, −60° … 由背向中心的方向往两边交替展开
        const step = Math.ceil(k / 2) * (k % 2 === 1 ? 1 : -1);
        return out0 + (step * Math.PI) / 6;
      });
      let done = false;
      // 先贴着圈找，找不到空地再往外挪两档。挪得太远就认不出是谁的名字了，到 +36 为止
      for (const pad of [12, 24, 36]) {
        for (const ang of dirs) {
          const x = g.px + Math.cos(ang) * (g.pr + pad);
          const y = g.py + Math.sin(ang) * (g.pr + pad) + 4;
          const anchor: Anchor =
            Math.abs(Math.cos(ang)) < 0.35 ? 'middle' : Math.cos(ang) > 0 ? 'start' : 'end';
          const x0 = anchor === 'start' ? x : anchor === 'end' ? x - tw : x - tw / 2;
          const box: Box = [x0 - 3, y - th, x0 + tw + 3, y + 4];
          if (!fits(box)) continue;
          placed.push(box);
          out.push({
            id: `ring:${g.name}`,
            text: g.name,
            note,
            x,
            y,
            x0,
            w: tw,
            size,
            anchor,
            kind: 'ring',
            strong: false,
          });
          done = true;
          break;
        }
        if (done) break;
      }
    }

    /* ③ 被指到的已有岗位。选中新岗位的三个相近岗位排在前面先占地方：
       它们是右栏逐条对照的那三个，名字缺一个就对不上号，而其余的指向
       只是给未选中的新簇一个方位，放不下可以不放。 */
    const anchorIds = new Set<string>(peerRank.keys());
    for (const e of emerging) if (e.p.near[0] && lit(e.p.job.id)) anchorIds.add(e.p.near[0].id);
    if (selectedId) anchorIds.add(selectedId);
    const refs = pts
      .filter((p) => !p.p.job.emerging && anchorIds.has(p.p.job.id))
      .sort((a, b) => (peerRank.get(a.p.job.id) ?? 9) - (peerRank.get(b.p.job.id) ?? 9));
    for (const p of refs) {
      const id = p.p.job.id;
      const isPeer = peerRank.has(id);
      /* 这三个名字要与右栏逐条对上号，字号提到与新岗位同一档并加粗；
         其余指向只是给未选中的新簇一个方位，仍走次一级字号。 */
      const size = isPeer ? LABEL_FONT : LABEL_FONT - 1;
      const pad = isPeer ? PEER_PAD : 0;
      const hit = isPeer
        ? (near(p, p.p.job.name, size, id, pad) ?? far(p, p.p.job.name, size, id, true, pad))
        : near(p, p.p.job.name, size, id, 0, 500);
      if (hit)
        out.push({
          ...hit,
          id,
          kind: 'ref',
          strong: isPeer || id === selectedId,
          peer: isPeer,
        });
    }

    return out;
  }, [pts, emerging, rings, selectedId, peerRank, badges, distMarks, links, focusIds, wk, hk]);

  /** 连线与读数取选中新岗位的字形色，与它的内圆同色，一眼看出这几条属于谁 */
  const selTone = selPoint ? tone(selPoint.p) : 'var(--ink-2)';

  /* 画布比画框大，被画框切掉半个字的名字会被读成另一个名字。
     此处按画框当前对着画布的哪一块逐条筛：整条落不进来的干脆不画，
     少一个名字好过一个残缺的名字 —— 平移之后它自会补上。
     缩放控件与尺寸图例这两块钉在画框上，压在它们底下的名字同样不画。
     这一筛在绘制时做，不进落位计算，因此拖动不触发重新落位。 */
  const inView = (x0: number, y0: number, x1: number, y1: number) => {
    const vx = -zp.tx;
    const vy = -zp.ty;
    if (x0 < vx + 2 || x1 > vx + W - 2 || y0 < vy + 2 || y1 > vy + H - 2) return false;
    /** 画框坐标下的一块遮挡区，换算成画布坐标后判相交 */
    const clear = (a: number, b: number, c: number, d: number) =>
      x1 < vx + a || x0 > vx + c || y1 < vy + b || y0 > vy + d;
    return (
      clear(W - CTRL_W - 10, 6, W - 6, CTRL_H + 12) &&
      clear(legendX - 15, legendY - 19, legendX + legendW + 15, legendY + 89)
    );
  };

  /* ---------------- 尺寸图例 ----------------
     四档按最大占比折半取：读者拿图上的点直接比大小，不必先在脑子里开一次平方根 */
  const sizeLegend = useMemo(
    () => [1, 0.5, 0.25, 0.1].map((f) => ({ share: space.maxShare * f, r: rOf(space.maxShare * f) })),
    [space.maxShare, rOf],
  );

  /* 图例的版面：圆在左、读数在右，一列左对齐。此前是整块右对齐、读数在圆的
     左侧，读起来是"11.0% ●"，与"这么大的圆代表这个数"的次序相反。 */
  const LEG_CX = 13;
  const LEG_LB = 30;
  const LEG_ROW = 19;
  const legendW = 96;
  const legendX = W - legendW - 14;
  const legendY = H - 104;

  const tipFor = (p: JobSpacePoint) => {
    const n0 = p.near[0];
    return {
      content: (
        <>
          <div className="tt-title">{p.job.name}</div>
          {n0 && (
            <div>
              最近的已有岗位：{n0.name}
              <br />
              <span className="tt-muted">
                {n0.cluster} · 任务向量余弦距离 {n0.dist.toFixed(3)}
              </span>
            </div>
          )}
          {/* 没有任务边的新岗位不报最近岗位。距离在这里算得出来（恒为 1），
              但它是零向量的代数结果，不是量出来的远近，报出去即是假读数。 */}
          {p.job.emerging && !p.grounded && (
            <div className="tt-fore">
              本批数据尚无任务关联边
              <br />
              <span className="tt-muted">任务构成无从比较，故不给最近岗位与距离；点位仅示意</span>
            </div>
          )}
          <div>
            {/* 叠层新岗位尚未进入招聘市场，加权出现量整批为零，这一项对它们
                恒等于 0.00% —— 一个不变的零读起来像一次读数，故只报有值的那一档 */}
            {!p.job.emerging && <>市场占比 {pct(p.share)}<br /></>}
            {p.job.emerging && p.foresight > 0.001 && (
              <>
                信号构成：<span className="jsp-tt-paper">论文 {Math.round(p.paperShare * 100)}%</span>
                <span className="tt-muted"> · </span>
                <span className="jsp-tt-news">新闻 {Math.round(p.newsShare * 100)}%</span>
                <br />
              </>
            )}
            定义置信度 {Math.round(p.job.confidence * 100)}%
          </div>
          {p.job.emerging && (
            <div className="tt-muted">
              证据首现 {p.firstAt} · 末次 {p.lastAt}
              {(p.firstPaperAt || p.firstNewsAt) && (
                <>
                  <br />
                  {p.firstPaperAt ? `论文首现 ${p.firstPaperAt}` : '论文尚无信号'}
                  {' · '}
                  {p.firstNewsAt ? `新闻首现 ${p.firstNewsAt}` : '新闻尚无信号'}
                </>
              )}
            </div>
          )}
          {p.cite.map((c) => (
            <div key={c.docId} className={c.sourceType === 'paper' ? 'jsp-tt-paper' : 'jsp-tt-news'}>
              {c.sourceType === 'paper' ? '论文' : '新闻'}《{c.title}》{c.publishedAt}
            </div>
          ))}
          {/* 强度序列与原文是两份数据，可能对不上：某一侧有强度、这个岗位名下却
              一条原文都抽不到。此时明说拿不出，不能让人以为原文只是没列出来。 */}
          {p.job.emerging && missingSide(p) && (
            <div className="tt-muted">{missingSide(p)}侧无可回溯原文</div>
          )}
        </>
      ),
    };
  };

  return (
    <div className="jspace" ref={ref}>
      <ZoomBar
        k={k}
        maxK={MAX_K}
        onIn={zp.zoomIn}
        onOut={zp.zoomOut}
        onAll={zp.showAll}
        onFocus={backToFocus}
        focusLabel={selPoint ? '回到选中' : '回到新岗位'}
      />
      <svg
        ref={zp.svgRef}
        className="jspace-svg"
        width={W}
        height={H}
        role="img"
        aria-label="新岗位空间关系图"
        onPointerDown={zp.onPointerDown}
        onDoubleClick={zp.onDoubleClick}
        style={{ cursor: k > 1 ? (zp.panning ? 'grabbing' : 'grab') : undefined }}
      >
        <defs>
          {/* 画布比画框大，越界的部分裁掉，否则会盖到图例与面板边框上 */}
          <clipPath id="jsp-frame">
            <rect x={0} y={0} width={W} height={H} rx={7} />
          </clipPath>
          {/* 光晕压到 9%：整图时看的是稀疏的外围，放大到密集区之后多圈相叠，
              原先 13% 那一档会糊成一层蓝雾，压在上面的岗位名跟着掉对比度 */}
          <radialGradient id="jsp-ring-fill">
            <stop offset="0%" stopColor="var(--lay-job)" stopOpacity="0.075" />
            <stop offset="58%" stopColor="var(--lay-job)" stopOpacity="0.035" />
            <stop offset="100%" stopColor="var(--lay-job)" stopOpacity="0" />
          </radialGradient>
          {emerging.map((p) => (
            <radialGradient id={`jsp-glow-${p.p.job.id}`} key={p.p.job.id}>
              <stop offset="0%" stopColor={tone(p.p)} stopOpacity="0.2" />
              <stop offset="100%" stopColor={tone(p.p)} stopOpacity="0" />
            </radialGradient>
          ))}
        </defs>

        <rect
          x={0.5}
          y={0.5}
          width={W - 1}
          height={H - 1}
          rx={7}
          fill="var(--surface-tint)"
          stroke="var(--line-strong)"
        />

        {/* 画布层。倍率已折进落位，此处只做平移；拖动因此不触发重新落位。
            拖动过程中关掉命中，免得指针扫过的每个点都弹一次浮层。 */}
        <g clipPath="url(#jsp-frame)">
          <g transform={`translate(${zp.tx} ${zp.ty})`} pointerEvents={zp.panning ? 'none' : undefined}>

            {/* 已有岗位圈：光晕给覆盖范围，虚线给分位边界。
            大类的边界本来就没有硬边，实线会把“四分之三成员落在圈内”读成“到此为止”。 */}
            {rings.map((g) => (
              <g key={g.name}>
                <circle cx={g.px} cy={g.py} r={g.pr * 1.5} fill="url(#jsp-ring-fill)" />
                <circle
                  cx={g.px}
                  cy={g.py}
                  r={g.pr}
                  fill="none"
                  stroke="var(--lay-job)"
                  strokeOpacity={g.named ? 0.3 : 0.14}
                  strokeDasharray="3 5"
                />
              </g>
            ))}

            {/* 新簇 → 最近的已有岗位。这条线是这张图的主张：“贴着，但没落进去”。
            未选中的只连最近的那一个，选中的把三个相近岗位一并连出：
            右栏逐条展开的正是这三个，图上少连两条就只剩一个可对照的落点。 */}
            {emerging.map((p) => {
              const sel = selectedId === p.p.job.id;
              const list = sel ? p.p.near : p.p.near.slice(0, 1);
              if (!list.length) return null;
              return (
                <g key={`ln-${p.p.job.id}`} opacity={lit(p.p.job.id) ? 1 : 0.2}>
                  {list.map((nb, i) => {
                    const to = byId.get(nb.id);
                    if (!to) return null;
                    const first = i === 0;
                    const on = sel && peerId === nb.id;
                    return (
                      <g key={nb.id}>
                        <line
                          x1={p.x}
                          y1={p.y}
                          x2={to.x}
                          y2={to.y}
                          stroke={sel ? tone(p.p) : 'var(--ink-3)'}
                          strokeWidth={on ? 2.2 : sel && first ? 1.6 : sel ? 1.2 : 1}
                          strokeOpacity={sel ? (on ? 1 : first ? 0.9 : 0.55) : 0.5}
                          strokeDasharray={sel ? (first ? undefined : '3 4') : '2 4'}
                        />
                      </g>
                    );
                  })}
                </g>
              );
            })}

            {/* 已有岗位。大小同样是市场占比，与新岗位一个标尺；
            描一圈面色的边，点叠点时仍分得开 */}
            {pts
              .filter((p) => !p.p.job.emerging)
              .map((p) => {
                const sel = selectedId === p.p.job.id;
                const peer = peerRank.has(p.p.job.id);
                return (
                  <circle
                    key={p.p.job.id}
                    className="jsp-star"
                    cx={p.x}
                    cy={p.y}
                    r={peer ? p.r + 0.8 : p.r}
                    fill={peer || sel ? PEER_HEX : 'var(--lay-job)'}
                    /* 已经指出三个相近岗位时，其余已有岗位压暗一档退作底图：
                       同族色里要突出三个点，让周围淡下去比给它们换色更省事 */
                    fillOpacity={sel || peer ? 1 : peerRank.size > 0 ? 0.2 : 0.4}
                    stroke="var(--viz-halo)"
                    strokeWidth={sel || peer ? 2 : 0.9}
                    onMouseEnter={(e) => {
                      setTip({ x: e.clientX, y: e.clientY, ...tipFor(p.p) });
                      if (peer) onPeerHover?.(p.p.job.id);
                    }}
                    onMouseLeave={() => {
                      setTip(null);
                      if (peer) onPeerHover?.(null);
                    }}
                  />
                );
              })}

            {/* 新岗位字形：内圆大小 = 市场占比，内圆填色 = 论文与新闻调出的中间色，
            外环两段弧 = 两类信号各自的占比。外环半径与内圆脱钩，
            占比再小的新岗位，那两段弧也仍然读得出来。 */}
            {emerging.map((p) => {
              const sel = selectedId === p.p.job.id;
              const on = lit(p.p.job.id);
              const fill = tone(p.p);
              const ringR = p.r + 5.4;
              const GAP = 0.13;
              const pa = Math.max(0, p.p.paperShare) * (Math.PI * 2 - GAP * 2);
              const na = Math.max(0, p.p.newsShare) * (Math.PI * 2 - GAP * 2);
              return (
                <g
                  key={p.p.job.id}
                  className="jsp-glyph"
                  opacity={on ? 1 : 0.22}
                  onClick={() => !zp.moved.current && onSelect(p.p.job.id)}
                  onMouseEnter={(e) => setTip({ x: e.clientX, y: e.clientY, ...tipFor(p.p) })}
                  onMouseLeave={() => setTip(null)}
                >
                  <circle
                    cx={p.x}
                    cy={p.y}
                    r={ringR + 12}
                    fill={`url(#jsp-glow-${p.p.job.id})`}
                    pointerEvents="none"
                  />
                  {/* 垫圈：外环压在已有岗位的点上时仍分得开 */}
                  <circle cx={p.x} cy={p.y} r={ringR + 2} fill="var(--viz-halo)" />
                  {p.p.foresight > 0.001 && (
                    <>
                      <path
                        d={arc(p.x, p.y, ringR, GAP, GAP + pa)}
                        fill="none"
                        stroke={PAPER_HEX}
                        strokeWidth={3}
                      />
                      <path
                        d={arc(p.x, p.y, ringR, GAP * 3 + pa, GAP * 3 + pa + na)}
                        fill="none"
                        stroke={NEWS_HEX}
                        strokeWidth={3}
                      />
                    </>
                  )}
                  <circle cx={p.x} cy={p.y} r={p.r} fill={fill} />
                  {/* 没有任务关联边的新岗位另套一圈虚线。
                  它的坐标由“与每个岗位距离都是 1”这一组约束解出，
                  不来自任何一次任务构成的比较 —— 与其余点画成一样，
                  等于给了一个它并没有的方位。
                  标记画在外环之外而不是改内圆：这批岗位的市场占比皆为零，
                  内圆一律缩到半径下限，两三个像素上分不出实心与虚线。 */}
                  {!p.p.grounded && (
                    <circle
                      cx={p.x}
                      cy={p.y}
                      r={ringR + 4.5}
                      fill="none"
                      stroke="var(--ink-3)"
                      strokeWidth={1.5}
                      strokeDasharray="3.4 3"
                    />
                  )}
                  {sel && (
                    <circle
                      cx={p.x}
                      cy={p.y}
                      r={ringR + 5.5}
                      fill="none"
                      stroke="var(--ink)"
                      strokeWidth={1.4}
                    />
                  )}
                </g>
              );
            })}

            {/* 相近岗位标注：套一个环，右上角挂一个序号牌。
            序号与右栏对照卡片的序号是同一套，图上看方位、右栏看差在哪儿。
            画在新岗位字形之后，字形的白垫圈才盖不住它。 */}
            {badges.map((b) => {
              const p = byId.get(b.id);
              if (!p) return null;
              const { rank, ring, bx, by } = b;
              const on = peerId === b.id;
              return (
                <g
                  key={`pk-${b.id}`}
                  className="jsp-peer-mk"
                  onMouseEnter={(e) => {
                    setTip({ x: e.clientX, y: e.clientY, ...tipFor(p.p) });
                    onPeerHover?.(b.id);
                  }}
                  onMouseLeave={() => {
                    setTip(null);
                    onPeerHover?.(null);
                  }}
                >
                  {/* 环外先垫一圈白：环压在别的蓝点上时，两条深色边不至于糊成一条 */}
                  <circle
                    cx={p.x}
                    cy={p.y}
                    r={ring}
                    fill="none"
                    stroke="var(--viz-halo)"
                    strokeWidth={on ? 5.4 : 4.4}
                  />
                  <circle
                    cx={p.x}
                    cy={p.y}
                    r={ring}
                    fill="none"
                    stroke={on ? PEER_HEX_ON : PEER_HEX}
                    strokeWidth={on ? 2.8 : 2}
                  />
                  <circle
                    cx={bx}
                    cy={by}
                    r={on ? BADGE_R + 1 : BADGE_R}
                    fill={on ? PEER_HEX_ON : PEER_HEX}
                    stroke="var(--viz-halo)"
                    strokeWidth={1.5}
                  />
                  <text className="jsp-peer-n" x={bx} y={by + 3.6} textAnchor="middle">
                    {rank}
                  </text>
                </g>
              );
            })}

            {/* 距离读数画在序号牌之后：两者万一还是挨上了，读数带白边、盖得住，
            而序号牌是实心圆底，被盖住就读不出名次了 */}
            {distMarks
              .filter((d) => inView(d.x - 22, d.y - 13, d.x + 22, d.y + 4))
              .map((d) => (
                <text
                  key={`d-${d.id}`}
                  className="jsp-dist"
                  x={d.x}
                  y={d.y}
                  textAnchor="middle"
                  fill={selTone}
                  opacity={d.first || peerId === d.id ? 1 : 0.75}
                >
                  {d.text}
                </text>
              ))}

            {labels
              .filter((l) => inView(l.x0 - 8, l.y - l.size - 5, l.x0 + l.w + 8, l.y + 6))
              .map((l) => (
                <g
                  key={l.id}
                  className={l.kind === 'new' ? 'jsp-label-g' : undefined}
                  onClick={l.kind === 'new' ? () => !zp.moved.current && onSelect(l.id) : undefined}
                  onMouseEnter={l.peer ? () => onPeerHover?.(l.id) : undefined}
                  onMouseLeave={l.peer ? () => onPeerHover?.(null) : undefined}
                >
                  {l.lead && (
                    <line
                      className="jsp-lead"
                      x1={l.lead[0]}
                      y1={l.lead[1]}
                      x2={l.lead[2]}
                      y2={l.lead[3]}
                    />
                  )}
                  <text
                    className={`jsp-label ${l.kind}${l.strong ? ' strong' : ''}${l.peer ? ' peer' : ''}${l.peer && peerId === l.id ? ' on' : ''
                      }`}
                    x={l.x}
                    y={l.y}
                    fontSize={l.size}
                    textAnchor={l.anchor}
                  >
                    {l.text}
                    {l.note && <tspan className="jsp-ring-n">{l.note}</tspan>}
                  </text>
                  {/* 虚线下划线：这个名字是临时标签，不是规范岗位名 */}
                  {l.kind === 'new' && (
                    <line
                      className="jsp-label-rule"
                      x1={l.x0}
                      y1={l.y + 3}
                      x2={l.x0 + l.w}
                      y2={l.y + 3}
                      strokeDasharray="2 2"
                    />
                  )}
                </g>
              ))}

          </g>
        </g>

        {/* 尺寸图例钉在画框上，不随平移走：它是这张图的标尺，跟着跑就用不上了。
            底下垫一片不透明底，否则缩放到某一处时点云会从它身下穿过去。 */}
        <g transform={`translate(${legendX} ${legendY})`}>
          <rect
            className="jsp-legend-box"
            x={-11}
            y={-15}
            width={legendW + 22}
            height={100}
            rx={8}
          />
          <text className="jsp-legend-hd" x={0} y={0}>
            市场占比
          </text>
          {sizeLegend.map((s, i) => (
            <g key={i} transform={`translate(0 ${16 + i * LEG_ROW})`}>
              <circle
                cx={LEG_CX}
                cy={0}
                r={s.r}
                fill="var(--lay-job)"
                fillOpacity={0.4}
                stroke="var(--viz-halo)"
                strokeWidth={0.9}
              />
              <text className="jsp-legend-t" x={LEG_LB} y={3.5}>
                {pct(s.share)}
              </text>
            </g>
          ))}
        </g>
      </svg>
      <Tooltip tip={tip} />
    </div>
  );
}
