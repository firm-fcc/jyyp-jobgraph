/* ============================================================
   岗位涌现相图
   —— 回答“这个候选岗位是真的在长出来，还是数据噪声”

   横轴：首现至今的月数，0 在左端，越靠左的岗位越新
   纵轴：多源确认强度 × 任务结构稳定性，越靠上越站得住
   点的大小：该岗位已经成形的任务簇规模
   尾迹：过去 8 个季度它在这张图上走过的位置

   时间只会把点往右推，所以要看的是纵向：一路抬升、留在左上“成长区”的，
   是正在成型的新岗位；一直贴着底边漂到右侧的，是长期拿不到强信号的长尾。
   ============================================================ */

import { useMemo, useState } from 'react';
import type { EntitySignal, GraphEdge, GraphNode } from '@/types/graph';
import { MONTHS, NOW } from '@/data/generator';
import { monthDiff } from '@/utils/format';
import { useSize } from '@/hooks/useSize';
import { Tooltip, type TipState } from '@/components/common/Tooltip';
import { measureText, smoothPath } from '@/utils/viz';

interface Props {
  jobs: GraphNode[];
  edges: GraphEdge[];
  signalMap: Map<string, EntitySignal>;
  selectedId: string | null;
  /** 落在当前筛选条件内的岗位；其余画成浅色参照，不再标名字 */
  focusIds?: Set<string>;
  onSelect: (id: string) => void;
}

/** 横轴上限的封顶值：8 年，再久的岗位一律压在最右端 */
const AGE_CAP = 96;

/**
 * 横轴量程按本批数据的观测区间定，上限封在 AGE_CAP。
 *
 * 写死 96 个月的前提是首现时间铺在数年之内。观测区间短于此时（早先的批次只覆盖
 * 六个自然月），固定量程下上百个点会压在最左端一条像素柱上，纵向的判读无从谈起
 * —— 这张图要看的恰是纵向。
 *
 * 量程取实际最大月龄留半成余量，再向上取整到一个整数刻度，最少留三个月。
 * 两年以上按半年取整而不按整年：按整年取整时，最大月龄二十四个月会撑出一个
 * 三十六个月的量程，右边三分之一是空的，各点因而挤在左半张图上。
 */
function axisSpan(maxAge: number): number {
  const want = Math.max(3, Math.ceil(maxAge * 1.06));
  return Math.min(AGE_CAP, want <= 12 ? Math.ceil(want / 3) * 3 : Math.ceil(want / 6) * 6);
}

/** 量程对应的刻度位置与标注：跨度在两年以上按年标，否则按月 */
function axisTicks(span: number): { m: number; label: string }[] {
  if (span >= 24) {
    const step = Math.ceil(span / 4 / 12) * 12;
    const out: { m: number; label: string }[] = [];
    for (let m = 0; m <= span; m += step) out.push({ m, label: m === 0 ? '首现' : `${m / 12} 年` });
    return out;
  }
  const step = Math.max(1, Math.round(span / 4));
  const out: { m: number; label: string }[] = [];
  for (let m = 0; m <= span; m += step) out.push({ m, label: m === 0 ? '首现' : `${m} 个月` });
  return out;
}

/**
 * 超过这个岗位数就不再给每个点画尾迹，只画筛选命中与选中项的。
 * 尾迹是九段平滑曲线，岗位类别到数百量级时会有上千段路径叠在一张图上 ——
 * 既拖渲染，也把点本身淹没在一层灰雾里。散点本身可以承载数百个点，尾迹不行。
 */
const TRAIL_BUDGET = 60;

const H = 512;
const PAD_L = 64;
const PAD_R = 26;
const PAD_T = 26;
const PAD_B = 54;

const LABEL_FONT = 13;

/** 四个象限。底色统一压到很低的透明度，只留下“这块地方是什么意思”的暗示 ——
    原先四块用了四个成品浅色（绿 / 灰 / 紫 / 红），深浅不一，
    点和尾迹压在上面反而比背景还弱，看着像一张打翻了的调色板。

    color 是底色，ink 是分区名的字色，两者不能共用：
    底色要淡到不抢点，字色要深到白底上过 4.5:1，同一个值两头都不讨好。 */
const ZONES = [
  { key: 'grow', title: '成长区', note: '首现较晚，确认强度较高', color: 'var(--green)', ink: '#15803d', op: 0.075 },
  { key: 'stable', title: '稳定区', note: '首现较早，确认强度较高', color: 'var(--lay-job)', ink: 'var(--primary-deep)', op: 0.05 },
  { key: 'seed', title: '萌芽区', note: '首现较晚，确认强度较低', color: 'var(--src-paper)', ink: 'var(--src-paper-ink)', op: 0.06 },
  { key: 'tail', title: '长尾区', note: '首现较早，确认强度较低', color: 'var(--ink-3)', ink: 'var(--ink-2)', op: 0.07 },
] as const;

type Anchor = 'start' | 'end' | 'middle';
type Box = [number, number, number, number];

const overlap = (a: Box, b: Box) => !(a[2] < b[0] || b[2] < a[0] || a[3] < b[1] || b[3] < a[1]);

export function EmergencePhasePlot({ jobs, edges, signalMap, selectedId, focusIds, onSelect }: Props) {
  const { ref, w } = useSize<HTMLDivElement>();
  const [tip, setTip] = useState<TipState | null>(null);
  const inFocus = (id: string) => !focusIds || focusIds.has(id) || id === selectedId;

  const W = Math.max(420, w || 720);
  const plotW = W - PAD_L - PAD_R;
  const plotH = H - PAD_T - PAD_B;

  /* 量程先于坐标算出：它依赖这批岗位的最大月龄，而坐标依赖它 */
  /* 各岗位在横轴上的取值（月数），以及它的观测起止窗。

     横轴此前按"首现至今"的最大值定量程，而点画在"首现到停观测"上：这批候选
     多数在转正那一窗就停止记录，两者相差二十余个月，右半张图因而是空的。
     现由同一份取值定量程与落位，图上有多长的跨度就画多宽。 */
  const spans = useMemo(() => {
    const last = MONTHS.length - 1;
    const m = new Map<string, { age: number; lastIdx: number; firstIdx: number; stopped: boolean; observedTo: string }>();
    for (const j of jobs) {
      const sig = signalMap.get(j.id);
      const firstIdx = (() => {
        const i = MONTHS.indexOf(j.firstSeen);
        if (i >= 0) return i;
        const k = MONTHS.findIndex((x) => x >= j.firstSeen);
        return k >= 0 ? k : last;
      })();
      let lastIdx = last;
      for (let i = last; i >= firstIdx; i--) {
        if ((sig?.paper[i] ?? 0) > 0 || (sig?.news[i] ?? 0) > 0) {
          lastIdx = i;
          break;
        }
      }
      const stopped = lastIdx < last;
      const observedTo = MONTHS[lastIdx];
      m.set(j.id, {
        firstIdx,
        lastIdx,
        stopped,
        observedTo,
        age: Math.max(0, monthDiff(j.firstSeen, stopped ? observedTo : NOW)),
      });
    }
    return m;
  }, [jobs, signalMap]);

  const span = useMemo(
    () => axisSpan([...spans.values()].reduce((m, v) => Math.max(m, v.age), 0)),
    [spans],
  );

  const pts = useMemo(() => {
    // 0 个月在左端：越新的岗位越靠左，随着时间推移点只会向右移动
    const X = (age: number) => PAD_L + (Math.min(age, span) / span) * plotW;
    const Y = (v: number) => PAD_T + plotH - v * plotH;
    /** 尾迹的采样点数（首尾各占一个，故实际分段数为它减一） */
    const TRAIL_POINTS = 9;

    return jobs.map((j) => {
      const sig = signalMap.get(j.id);
      const cluster = edges.filter((e) => e.kind === 'J-T' && e.source === j.id);
      const clusterSize = cluster.length;
      const stability =
        cluster.length > 0 ? cluster.reduce((a, b) => a + b.confidence, 0) / cluster.length : 0.2;

      const strengthAt = (mi: number) => {
        const jd = sig?.jd[mi] ?? 0;
        const fore = (sig?.paper[mi] ?? 0) * 0.7 + (sig?.news[mi] ?? 0) * 0.3;
        return Math.min(1, (jd * 2.4 + fore * 1.1) * (0.55 + stability * 0.45));
      };

      /* 尾迹：自本岗位的首现窗口起，到末窗为止，等分取若干个采样点。

         采样区间锚在首现窗口上，不再按固定步长自末窗往回数。按固定步长回溯时，
         步长是由观测窗口总数定的（本批四十六窗，一步十五窗），而各岗位的首现
         早晚不一：一个 2024 年首现的岗位，回溯到的那几步多半落在它出现之前，
         逐个被判掉，剩下两三个点。画出来是一段悬在图中间的短线，起点既不在
         "首现"那一格，也读不出它这一路是怎么走过来的。

         锚在首现之后，尾迹的第一个点必然落在横轴 0（"首现"那一格）上，
         最后一个点落在当前位置，中间等分 —— 无论首现早晚，这条线都完整。 */
      /* 首现窗与观测截止窗由上方 spans 一并算出，两处必须同源：
         量程按它定，落位也按它取 */
      const sp = spans.get(j.id)!;
      const firstIdx = sp.firstIdx;
      const lastIdx = sp.lastIdx;
      const trail: [number, number][] = [];
      let prevMi = -1;
      for (let k = 0; k < TRAIL_POINTS; k++) {
        const mi = Math.round(firstIdx + ((lastIdx - firstIdx) * k) / (TRAIL_POINTS - 1));
        if (mi === prevMi) continue;
        prevMi = mi;
        const age = Math.max(0, monthDiff(j.firstSeen, MONTHS[mi]));
        trail.push([X(age), Y(strengthAt(mi))]);
      }

      const { age, stopped, observedTo } = sp;
      const strength = strengthAt(lastIdx);

      return {
        job: j,
        x: X(age),
        y: Y(strength),
        r: 5 + Math.sqrt(clusterSize) * 2.05,
        trail,
        clusterSize,
        age,
        strength,
        stopped,
        observedTo,
        confirmed: !!sig?.firstJdAt,
        predictedJdAt: sig?.predictedJdAt,
      };
    });
  }, [jobs, edges, signalMap, spans, plotW, plotH, span]);

  /**
   * 落点重合的岗位。
   *
   * 两个岗位的三源强度序列若逐窗相同，它们在这张图上的点与尾迹会完全重合，
   * 看上去只有一个。本批数据里确有这样一对：数据科学岗位与机器学习岗位
   * 同出自 arXiv:2209.10114 的同一次抽取，强度读数逐窗一致。
   *
   * 重合本身是数据的实情，不作抖动处理 —— 挪开一点即等于报了一个它没有的差别。
   * 改为在浮层里点名与它同处一点的是谁：图上少一个点，比图上多一个假的位置要好，
   * 而“为什么少了一个”这一问必须答得出。
   */
  const overlaps = useMemo(() => {
    const at = new Map<string, string[]>();
    for (const p of pts) {
      const k = `${Math.round(p.x)}|${Math.round(p.y)}`;
      const list = at.get(k);
      if (list) list.push(p.job.id);
      else at.set(k, [p.job.id]);
    }
    const m = new Map<string, string[]>();
    for (const p of pts) {
      const ids = at.get(`${Math.round(p.x)}|${Math.round(p.y)}`) ?? [];
      if (ids.length > 1)
        m.set(
          p.job.id,
          ids.filter((id) => id !== p.job.id).map((id) => jobs.find((j) => j.id === id)?.name ?? id),
        );
    }
    return m;
  }, [pts, jobs]);

  /** “新 / 老”分界线：量程够长时落在 36 个月，否则落在量程正中 */
  const midX = PAD_L + plotW * (span >= 48 ? 36 / span : 0.5);
  const midY = PAD_T + plotH * 0.5;

  /** 标签落位：候选偏移逐个试，压住已落位的就让位，宁可少标也不糊成一团。
      去掉白描边之后，还要连别人的圆点一起躲开 —— 原先靠描边硬压在点上也读得出来，
      现在压上去就是两层深色叠在一起。自己的点不算，几个候选位本来就绕开了自身半径。 */
  const zoneBoxes: {
    key: string;
    title: string;
    note: string;
    color: string;
    ink: string;
    op: number;
    x: number;
    y: number;
    w: number;
    h: number;
    tx: number;
    ty: number;
    end: boolean;
  }[] = [
    { ...ZONES[0], x: PAD_L, y: PAD_T, w: midX - PAD_L, h: midY - PAD_T, tx: PAD_L + 12, ty: PAD_T + 22, end: false },
    {
      ...ZONES[1],
      x: midX,
      y: PAD_T,
      w: PAD_L + plotW - midX,
      h: midY - PAD_T,
      tx: PAD_L + plotW - 12,
      ty: PAD_T + 22,
      end: true,
    },
    {
      ...ZONES[2],
      x: PAD_L,
      y: midY,
      w: midX - PAD_L,
      h: PAD_T + plotH - midY,
      tx: PAD_L + 12,
      ty: PAD_T + plotH - 12,
      end: false,
    },
    {
      ...ZONES[3],
      x: midX,
      y: midY,
      w: PAD_L + plotW - midX,
      h: PAD_T + plotH - midY,
      tx: PAD_L + plotW - 12,
      ty: PAD_T + plotH - 12,
      end: true,
    },
  ];

  const labels = useMemo(() => {
    const out: { id: string; text: string; x: number; y: number; anchor: Anchor; strong: boolean }[] = [];
    const placed: Box[] = [];
    /* 四个角上的区名另立一档软避让。此前避让只躲圆点与已放好的岗位名，区名不在
       其列，落点集中在某一区时，那一区的岗位名恰好压在该区的名字上。但区名是
       一句背景说明，岗位名是这张图的读数：两者只能压一个时压前者，故区名单列，
       在十二个位置都落不下时才放行。 */
    const zoneLabels: Box[] = zoneBoxes.map((z) => {
      const w = measureText(`${z.title} · ${z.note}`, 12) + 6;
      const x0 = z.end ? z.tx - w : z.tx;
      return [x0 - 3, z.ty - 14, x0 + w + 3, z.ty + 4] as Box;
    });
    const dotBox = new Map<string, Box>(
      pts.map((p) => [p.job.id, [p.x - p.r - 2, p.y - p.r - 2, p.x + p.r + 2, p.y + p.r + 2] as Box]),
    );
    const candidates = pts
      .filter((p) => (!focusIds || focusIds.has(p.job.id)) && (p.job.emerging || p.job.id === selectedId))
      .sort((a, b) => (a.job.id === selectedId ? -1 : b.job.id === selectedId ? 1 : b.strength - a.strength));

    for (const p of candidates) {
      const text = p.job.name;
      const tw = text.length * LABEL_FONT * 0.98;
      const th = LABEL_FONT + 4;
      const others = pts.filter((o) => o.job.id !== p.job.id).map((o) => dotBox.get(o.job.id)!);
      // 多给几个候选位：现在还要躲开别人的圆点，只有六个位置会有标签落不下去
      const opts: [number, number, Anchor][] = [
        [p.r + 7, 4.5, 'start'],
        [-(p.r + 7), 4.5, 'end'],
        [p.r + 7, -10, 'start'],
        [-(p.r + 7), -10, 'end'],
        [0, -(p.r + 8), 'middle'],
        [0, p.r + 18, 'middle'],
        [p.r + 7, 18, 'start'],
        [-(p.r + 7), 18, 'end'],
        [p.r + 18, 4.5, 'start'],
        [-(p.r + 18), 4.5, 'end'],
        [p.r + 18, -13, 'start'],
        [-(p.r + 18), -13, 'end'],
      ];
      /* 两轮：先连区名一并躲开，仍无处可放时再放开这一档 */
      let done = false;
      for (const avoidZone of [true, false]) {
        for (const [dx, dy, anchor] of opts) {
          const x = p.x + dx;
          const y = p.y + dy;
          const x0 = anchor === 'start' ? x : anchor === 'end' ? x - tw : x - tw / 2;
          const box: Box = [x0 - 2, y - th, x0 + tw + 2, y + 3];
          if (box[0] < PAD_L - 6 || box[2] > W - 4 || box[1] < 4 || box[3] > PAD_T + plotH + 4) continue;
          if (placed.some((b) => overlap(b, box))) continue;
          if (others.some((b) => overlap(b, box))) continue;
          if (avoidZone && zoneLabels.some((b) => overlap(b, box))) continue;
          placed.push(box);
          out.push({ id: p.job.id, text, x, y, anchor, strong: p.job.id === selectedId });
          done = true;
          break;
        }
        if (done) break;
      }
    }
    return out;
  }, [pts, selectedId, focusIds, W, plotH]);

  /** 四个象限的矩形与标题落位。标题一律贴着各自象限的外角，不与中间的十字线抢地方 */
  /** 横轴刻度：跨度在两年以上按“年”标（没人会把 72 个月心算成 6 年），否则按月 */
  const xTicks = useMemo(() => axisTicks(span), [span]);

  return (
    <div className="phase" ref={ref}>
      <svg className="phase-svg" width={W} height={H} role="img" aria-label="岗位涌现相图">
        <defs>
          {/* 四区的底色此前是四块等浓度的实心矩形，在正中对撞成一个硬十字：
              分区依的是两条中位线，而中位线本身是一道判读的参考，不是断崖。
              现改为自各区的外角向内渐隐，浓在角上、淡到中线，接缝随之消失，
              "越靠角越典型"这层意思也由浓淡直接给出。 */}
          {zoneBoxes.map((z) => (
            <radialGradient
              key={`zg-${z.key}`}
              id={`ph-zone-${z.key}`}
              cx={z.end ? '100%' : '0%'}
              cy={z.ty < midY ? '0%' : '100%'}
              r="118%"
            >
              <stop offset="0%" stopColor={z.color} stopOpacity={z.op * 1.9} />
              <stop offset="62%" stopColor={z.color} stopOpacity={z.op * 0.72} />
              <stop offset="100%" stopColor={z.color} stopOpacity={0} />
            </radialGradient>
          ))}
          {/* 尾迹沿时间方向由淡转浓：一条等浓度的线读不出哪头是早、哪头是今，
              而这张图看的正是"一路抬升还是原地打转"。 */}
          <linearGradient id="ph-trail-em" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="var(--src-paper)" stopOpacity={0.12} />
            <stop offset="100%" stopColor="var(--src-paper)" stopOpacity={1} />
          </linearGradient>
          <linearGradient id="ph-trail-base" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="var(--lay-job)" stopOpacity={0.12} />
            <stop offset="100%" stopColor="var(--lay-job)" stopOpacity={1} />
          </linearGradient>
        </defs>
        {zoneBoxes.map((z) => (
          <rect
            key={z.key}
            x={z.x}
            y={z.y}
            width={Math.max(0, z.w)}
            height={Math.max(0, z.h)}
            fill={`url(#ph-zone-${z.key})`}
          />
        ))}

        {/* 网格：先铺淡的刻度线，再压上分区的十字线和外框 */}
        {xTicks.map((t) => (
          <line
            key={`gx${t.m}`}
            x1={PAD_L + (t.m / span) * plotW}
            y1={PAD_T}
            x2={PAD_L + (t.m / span) * plotW}
            y2={PAD_T + plotH}
            stroke="var(--line)"
          />
        ))}
        {[0.25, 0.75].map((v) => (
          <line
            key={`gy${v}`}
            x1={PAD_L}
            y1={PAD_T + plotH - v * plotH}
            x2={PAD_L + plotW}
            y2={PAD_T + plotH - v * plotH}
            stroke="var(--line)"
          />
        ))}
        <line x1={midX} y1={PAD_T} x2={midX} y2={PAD_T + plotH} stroke="var(--line-strong)" strokeDasharray="5 4" />
        <line x1={PAD_L} y1={midY} x2={PAD_L + plotW} y2={midY} stroke="var(--line-strong)" strokeDasharray="5 4" />
        <rect
          x={PAD_L}
          y={PAD_T}
          width={plotW}
          height={plotH}
          fill="none"
          stroke="var(--line-strong)"
          strokeWidth={1}
        />

        {/* 分区名：名字用该区自己的色相，后半句维持弱色，
            两段分色比一整行同色更易辨认 */}
        {zoneBoxes.map((z) => (
          <text key={`t${z.key}`} className="ph-zone" x={z.tx} y={z.ty} textAnchor={z.end ? 'end' : 'start'}>
            <tspan fill={z.ink} fontWeight={700}>
              {z.title}
            </tspan>
            <tspan className="ph-zone-note"> · {z.note}</tspan>
          </text>
        ))}

        {/* 轴 */}
        {xTicks.map((t) => (
          <text
            key={t.m}
            className="ph-tick"
            x={PAD_L + (t.m / span) * plotW}
            y={PAD_T + plotH + 21}
            textAnchor={t.m === 0 ? 'start' : t.m === span ? 'end' : 'middle'}
          >
            {t.label}
          </text>
        ))}
        {[0, 0.5, 1].map((v) => (
          <text key={v} className="ph-tick" x={PAD_L - 10} y={PAD_T + plotH - v * plotH + 4} textAnchor="end">
            {v.toFixed(1)}
          </text>
        ))}
        <text className="ph-axis" x={PAD_L + plotW / 2} y={H - 13} textAnchor="middle">
          首次出现至今
        </text>
        <text
          className="ph-axis"
          x={18}
          y={PAD_T + plotH / 2}
          textAnchor="middle"
          transform={`rotate(-90 18 ${PAD_T + plotH / 2})`}
        >
          多源确认强度 × 结构稳定性
        </text>

        {/* 尾迹。岗位数超过预算时只留筛选命中与选中项，避免上千段曲线糊成一片 */}
        {pts
          .filter((p) => pts.length <= TRAIL_BUDGET || inFocus(p.job.id))
          .map((p) => (
            <path
              key={`t-${p.job.id}`}
              d={smoothPath(p.trail)}
              fill="none"
              stroke={p.job.emerging ? 'url(#ph-trail-em)' : 'url(#ph-trail-base)'}
              strokeWidth={selectedId === p.job.id ? 2.8 : 1.8}
              /* 未选中的尾迹此前压到 0.34，几条淡线叠在浅底的分区色上便读不出来了。
                 本页一屏至多五条尾迹，不存在糊成一片的问题，故提到 0.62。 */
              strokeOpacity={selectedId === p.job.id ? 0.9 : inFocus(p.job.id) ? 0.62 : 0.14}
              strokeLinecap="round"
            />
          ))}

        {/* 当前位置 */}
        {pts.map((p) => {
          const sel = selectedId === p.job.id;
          return (
            <g
              key={p.job.id}
              className="ph-dot"
              opacity={inFocus(p.job.id) ? 1 : 0.3}
              onClick={() => onSelect(p.job.id)}
              onMouseEnter={(e) =>
                setTip({
                  x: e.clientX,
                  y: e.clientY,
                  content: (
                    <>
                      <div className="tt-title">{p.job.name}</div>
                      <div className="tt-muted">
                        {p.job.cluster || '尚未归入体系'} · 首现 {p.job.firstSeen}，
                        {p.stopped ? `观测 ${p.age} 个月` : `已 ${p.age} 个月`}
                      </div>
                      {p.stopped && (
                        <div className="tt-muted">
                          观测截至 {p.observedTo}：该岗位其后已写入岗位体系，不再作为新岗位记录
                        </div>
                      )}
                      <div>
                        任务簇 {p.clusterSize} 条 · 确认强度 {p.strength.toFixed(2)}
                      </div>
                      <div className={p.confirmed ? undefined : 'tt-fore'}>
                        {p.confirmed
                          ? '招聘市场已确认'
                          : `招聘市场尚未确认${p.predictedJdAt ? `，预计 ${p.predictedJdAt} 前后出现` : ''}`}
                      </div>
                      {overlaps.has(p.job.id) && (
                        <div className="tt-muted">
                          与{overlaps.get(p.job.id)!.join('、')}落点重合
                          <br />
                          两者的逐窗信号强度相同，图上因而只见一个点
                        </div>
                      )}
                    </>
                  ),
                })
              }
              onMouseLeave={() => setTip(null)}
            >
              {p.job.emerging && (
                <circle cx={p.x} cy={p.y} r={p.r + 6} fill="var(--src-paper)" opacity={0.13} />
              )}
              {/* 白色垫圈：点叠点时仍分得开 */}
              <circle cx={p.x} cy={p.y} r={p.r + 1.6} fill="var(--viz-halo)" />
              {/* 五个候选一律画成同一种实心点。此前按"招聘市场是否已确认"分作
                  实心与虚线空心两式，同处一张图上读起来像两类不同的东西，而它们
                  同为新岗位候选，分的只是各自的进度。这一条信息仍在：浮层里
                  写着"招聘市场已确认"或"尚未确认，预计某月前后出现"。 */}
              <circle
                cx={p.x}
                cy={p.y}
                r={p.r}
                fill={p.job.emerging ? 'var(--src-paper)' : 'var(--lay-job)'}
                fillOpacity={0.82}
                stroke={p.job.emerging ? 'var(--src-paper)' : 'var(--lay-job)'}
                strokeWidth={sel ? 3 : 1.6}
              />
              {sel && <circle cx={p.x} cy={p.y} r={p.r + 5.5} fill="none" stroke="var(--ink)" strokeWidth={1.4} />}
            </g>
          );
        })}

        {labels.map((l) => (
          <text
            key={l.id}
            className={l.strong ? 'ph-label strong' : 'ph-label'}
            x={l.x}
            y={l.y}
            textAnchor={l.anchor}
            onClick={() => onSelect(l.id)}
          >
            {l.text}
          </text>
        ))}
      </svg>
      <Tooltip tip={tip} />
    </div>
  );
}
