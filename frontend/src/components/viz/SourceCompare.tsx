/* ============================================================
   三源对照

   同一个条目在三类数据源里的热度走势画在一起：
   以“招聘信息”为基准线，学术论文与行业新闻高出来的那块阴影，
   就是这一项的前瞻热度 —— 也就是全景图谱里斜纹部分的来源。

   支持横向拖动：把前瞻曲线整体右移若干个月，看它能不能和招聘曲线重合。
   能重合，说明这一项确实是“先有论文、后有岗位”，右移的月数就是提前量。

   第二个模式“全部叠加”回答的是另一个问题：单看一项对得上，可能只是巧合；
   把所有已被招聘确认过的条目，各自按自己的确认月对齐后叠在一起，
   若仍浮出一个共同形状，那就不是巧合，而是一条在几十个条目上反复出现的关系。
   ============================================================ */

import { useMemo, useRef, useState } from 'react';
import type { EntitySignal } from '@/types/graph';
import { smoothPath } from '@/utils/viz';
import { useSize } from '@/hooks/useSize';

interface Props {
  signal: EntitySignal;
  /** 全部条目，用于“全部叠加”模式；不传则只有单项对照 */
  all?: EntitySignal[];
}

/** 把序列整体平移 k 个月，k 为正即右移、为负即左移，移出去的一端补零。
    两个方向都要能移：论文未必总是先于招聘，本批数据里不少条目的招聘要求
    反而早于论文与新闻，只准右移时这类条目一格也对不上。 */
function shifted(arr: number[], k: number): number[] {
  const out = new Array<number>(arr.length).fill(0);
  for (let i = 0; i < arr.length; i++) {
    const j = i - k;
    out[i] = j >= 0 && j < arr.length ? arr[j] : 0;
  }
  return out;
}

/** 两条曲线的吻合程度，1 表示完全同步 */
function match(a: number[], b: number[]): number {
  const n = a.length;
  const ma = a.reduce((x, y) => x + y, 0) / n;
  const mb = b.reduce((x, y) => x + y, 0) / n;
  let num = 0;
  let da = 0;
  let db = 0;
  for (let i = 0; i < n; i++) {
    num += (a[i] - ma) * (b[i] - mb);
    da += (a[i] - ma) ** 2;
    db += (b[i] - mb) ** 2;
  }
  return da > 0 && db > 0 ? num / Math.sqrt(da * db) : 0;
}

/** 拖动位移的上下界，两个方向同量程 */
const SHIFT_MAX = 36;

export function SourceCompare({ signal, all }: Props) {
  const { ref, w } = useSize<HTMLDivElement>();
  const [mode, setMode] = useState<'one' | 'stack'>('one');
  const [shift, setShift] = useState(0);
  /** 指针是否停在图上 —— 拖动说明只在此时露出，不常占一行 */
  const [hover, setHover] = useState(false);
  const drag = useRef<{ x0: number; s0: number } | null>(null);

  const W = Math.max(300, w || 560);
  /* 这张图挪到右列后只有半幅页宽，226 高会把三条曲线压成扁平的一片；
     按 2.4 : 1 左右的比例给高度，起伏才看得出来 */
  const H = 292;
  const PAD_L = 42;
  const PAD_R = 12;
  const PAD_T = 16;
  const PAD_B = 28;
  const plotW = W - PAD_L - PAD_R;
  const plotH = H - PAD_T - PAD_B;

  const n = signal.months.length;
  const stepX = plotW / (n - 1);

  /* ---------------- 纵轴 ----------------

     三条曲线各按自身峰值归一，纵轴读作"相对该来源自身最高点的百分比"。

     此前三条共用一把绝对尺子，而三者的量纲本就不同：招聘一路是该条目在本层
     里的相对需求高度，论文与新闻一路是叠层信号强度。共用一把尺子时，凡招聘
     侧本来就占比不高的条目，蓝线整条贴在零线上 —— 图上只剩两条前瞻曲线，
     "招聘跟没跟上"这一问反而看不出来。

     这张图问的是形状：前瞻曲线的上升是不是先于招聘曲线的上升。形状之比与各自
     的绝对高度无关，归一之后三条都占满纵轴，先后关系才读得出来；下方的吻合度
     与自动对齐两项本就是相关系数，对尺度不敏感，读数不因此改变。

     绝对高度另有出处：前瞻热度排行给的是 gap 的绝对值，全景图谱主图的条长
     给的是要求强度本身。 */
  const norm = (arr: number[]) => {
    let mx = 0;
    for (const v of arr) if (v > mx) mx = v;
    return mx > 0 ? arr.map((v) => v / mx) : arr.map(() => 0);
  };
  const jdN = useMemo(() => norm(signal.jd), [signal.jd]);
  const paperN = useMemo(() => norm(signal.paper), [signal.paper]);
  const newsN = useMemo(() => norm(signal.news), [signal.news]);
  const maxY = 1.08;

  const X = (i: number) => PAD_L + i * stepX;
  const Y = (v: number) => PAD_T + plotH - (v / maxY) * plotH;

  const sp = useMemo(() => shifted(paperN, shift), [paperN, shift]);
  const sn = useMemo(() => shifted(newsN, shift), [newsN, shift]);

  /** 平移多少个月最贴合招聘曲线 —— 这就是实测出来的提前量。
      正负两个方向都扫：取到负值即该项的招聘要求早于论文与新闻。 */
  const best = useMemo(() => {
    let lag = 0;
    let score = -2;
    for (let k = -SHIFT_MAX; k <= SHIFT_MAX; k++) {
      const c = match(shifted(paperN, k), jdN);
      if (c > score) {
        score = c;
        lag = k;
      }
    }
    return { lag, score };
  }, [paperN, jdN]);

  /** 招聘侧还没有信号时，“吻合度”“自动对齐”没有对齐的对象，算出来只是噪声，不展示 */
  const hasJd = !!signal.firstJdAt;
  const now = match(sp, jdN);

  const jdPts = jdN.map((v, i) => [X(i), Y(v)] as [number, number]);

  /** 前瞻缺口：前瞻曲线高出招聘曲线的那块面积 */
  const gapArea = useMemo(() => {
    const top: [number, number][] = [];
    const bottom: [number, number][] = [];
    for (let i = 0; i < n; i++) {
      const fore = Math.max(sp[i], sn[i]);
      top.push([X(i), Y(Math.max(fore, jdN[i]))]);
      bottom.push([X(i), Y(jdN[i])]);
    }
    const back = [...bottom].reverse();
    return `${smoothPath(top)} L${back[0][0]},${back[0][1]} ${smoothPath(back).replace(/^M/, 'L')} Z`;
    // X/Y 随宽度变化，故把 W 一并列为依赖
  }, [sp, sn, jdN, n, W]);

  const onDown = (e: React.PointerEvent) => {
    drag.current = { x0: e.clientX, s0: shift };
    (e.target as Element).setPointerCapture(e.pointerId);
  };
  const onMove = (e: React.PointerEvent) => {
    if (!drag.current) return;
    const d = Math.round((e.clientX - drag.current.x0) / stepX);
    setShift(Math.max(-SHIFT_MAX, Math.min(SHIFT_MAX, drag.current.s0 + d)));
  };
  const onUp = () => {
    drag.current = null;
  };

  const jdIdx = signal.firstJdAt ? signal.months.indexOf(signal.firstJdAt) : -1;

  const stackable = (all ?? []).filter((s) => s.firstJdAt && s.firstPaperAt);

  return (
    <div ref={ref} className="dv">
      {stackable.length >= 6 && (
        <div className="dv-mode" role="tablist" aria-label="三源对照视图">
          <button
            role="tab"
            aria-selected={mode === 'one'}
            className={mode === 'one' ? 'dv-mode-b on' : 'dv-mode-b'}
            onClick={() => setMode('one')}
          >
            单项对照
          </button>
          <button
            role="tab"
            aria-selected={mode === 'stack'}
            className={mode === 'stack' ? 'dv-mode-b on' : 'dv-mode-b'}
            onClick={() => setMode('stack')}
          >
            全部叠加（{stackable.length} 项）
          </button>
        </div>
      )}

      {mode === 'stack' && stackable.length >= 6 ? (
        <StackedSignals signals={stackable} w={W} />
      ) : (
        <>
          <div className="dv-bar">
            {hasJd ? (
              <>
                <span className="dv-shift">
                  前瞻曲线{shift === 0 ? '未平移' : `${shift > 0 ? '右移' : '左移'} `}
                  {shift !== 0 && (
                    <>
                      <b>{Math.abs(shift)}</b> 个月
                    </>
                  )}
                </span>
                <span className="dv-fit">
                  与招聘曲线吻合度 <b className={now > 0.9 ? 'hi' : ''}>{(Math.max(0, now) * 100).toFixed(1)}%</b>
                </span>
                <button className="btn sm" onClick={() => setShift(best.lag)}>
                  自动对齐（
                  {best.lag === 0 ? '不平移' : `${best.lag > 0 ? '右移' : '左移'} ${Math.abs(best.lag)} 个月`}
                  拟合最优）
                </button>
                {shift !== 0 && (
                  <button className="btn sm" onClick={() => setShift(0)}>
                    复位
                  </button>
                )}
              </>
            ) : (
              <span className="muted">该项尚未出现在招聘市场，无可对齐的招聘曲线；图中阴影区域全部为前瞻缺口。</span>
            )}
          </div>

          {/* 拖动说明贴在图内右下角，只在指针停在图上时露出：它讲的是这张图上
              的一个动作，动作没在发生时占着一整行，反而把下面的图例推远。
              角落取右下 —— 左上是纵轴刻度与首现标注所在，浮在那里会盖住字。 */}
          <div className="dv-plot">
            {hasJd && hover && <span className="dv-hint">在图上横向拖动可平移前瞻曲线，观察两条曲线的对齐位置</span>}
            <svg
              width={W}
              height={H}
              style={{ display: 'block', cursor: hasJd ? 'ew-resize' : 'default', touchAction: 'none' }}
              onPointerDown={hasJd ? onDown : undefined}
              onPointerMove={hasJd ? onMove : undefined}
              onPointerUp={hasJd ? onUp : undefined}
              onPointerCancel={hasJd ? onUp : undefined}
              onPointerEnter={() => setHover(true)}
              onPointerLeave={() => setHover(false)}
            >
              <g>
                {/* 轴名竖排在刻度左侧：纵轴读的是"占自身峰值的百分比"，
                    不写出来时三条曲线同时贴顶，读者会当成三者高度相同 */}
                <text
                  transform={`translate(11,${PAD_T + plotH / 2}) rotate(-90)`}
                  fontSize={10.5}
                  fill="var(--ink-3)"
                  textAnchor="middle"
                >
                  各来源相对自身峰值
                </text>
                {[0, 0.5, 1].map((f) => (
                  <g key={f}>
                    <line x1={PAD_L} y1={Y(f)} x2={W - PAD_R} y2={Y(f)} stroke="var(--line)" />
                    <text x={PAD_L - 6} y={Y(f) + 4} fontSize={11} fill="var(--ink-2)" textAnchor="end">
                      {(f * 100).toFixed(0)}%
                    </text>
                  </g>
                ))}
                {signal.months.map((m, i) =>
                  m.endsWith('-01') ? (
                    <text key={m} x={X(i)} y={H - 9} fontSize={11} fill="var(--ink-2)" textAnchor="middle">
                      {m.slice(0, 4)}
                    </text>
                  ) : null,
                )}
              </g>

              {/* 前瞻缺口 */}
              <path d={gapArea} fill="var(--src-paper)" opacity={0.2} />

              {/* 招聘信息：作为基准线，画成实心面积 */}
              <path d={`${smoothPath(jdPts)} L${X(n - 1)},${Y(0)} L${X(0)},${Y(0)} Z`} fill="var(--src-jd)" opacity={0.16} />
              <path d={smoothPath(jdPts)} fill="none" stroke="var(--src-jd)" strokeWidth={2} />

              {/* 学术论文 / 行业新闻 */}
              <path
                d={smoothPath(sp.map((v, i) => [X(i), Y(v)] as [number, number]))}
                fill="none"
                stroke="var(--src-paper)"
                strokeWidth={2.2}
                strokeDasharray={shift !== 0 ? '5 3' : undefined}
              />
              <path
                d={smoothPath(sn.map((v, i) => [X(i), Y(v)] as [number, number]))}
                fill="none"
                stroke="var(--src-news)"
                strokeWidth={1.4}
                opacity={0.9}
                strokeDashoffset={0}
                strokeDasharray={shift !== 0 ? '5 3' : undefined}
              />

              {/* 招聘要求第一次出现这一项的时刻 */}
              {jdIdx >= 0 && (
                <g>
                  <line
                    x1={X(jdIdx)}
                    y1={PAD_T}
                    x2={X(jdIdx)}
                    y2={PAD_T + plotH}
                    stroke="var(--src-jd)"
                    strokeDasharray="4 3"
                    opacity={0.65}
                  />
                  <text
                    x={X(jdIdx) + 5}
                    y={PAD_T + 12}
                    fontSize={11}
                    fill="var(--src-jd)"
                    textAnchor={X(jdIdx) > W - 110 ? 'end' : 'start'}
                    dx={X(jdIdx) > W - 110 ? -10 : 0}
                  >
                    招聘要求首次出现
                  </text>
                </g>
              )}
            </svg>
          </div>

          <div className="viz-legend">
            <span>
              <i style={{ background: 'var(--src-jd)' }} />
              招聘信息
            </span>
            <span>
              <i style={{ background: 'var(--src-paper)' }} />
              学术论文
            </span>
            <span>
              <i style={{ background: 'var(--src-news)' }} />
              行业新闻
            </span>
            <span>
              <i style={{ background: 'var(--src-paper)', opacity: 0.32 }} />
              前瞻曲线高出招聘曲线的区间
            </span>
          </div>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------
   全部叠加

   横轴不再是日历时间，而是“距各自的招聘确认月还有几个月”——
   每一项都被平移到自己的 0 点上。纵轴按各条曲线自身的峰值归一：
   比的是形状，不是热度绝对值，否则一两个大热技术会把其余压成一条平线。

   只纳入已被招聘确认过的条目。仍在等待确认的没有 0 点可对齐，
   这与提前量分布是同一个口径，也是同一处必须说明的偏差。
   ------------------------------------------------------------ */

const REL_FROM = -30;
const REL_TO = 6;

function StackedSignals({ signals, w }: { signals: EntitySignal[]; w: number }) {
  const H = 292;
  const PAD_L = 42;
  const PAD_R = 12;
  const PAD_T = 16;
  const PAD_B = 30;
  const W = w;
  const plotW = W - PAD_L - PAD_R;
  const plotH = H - PAD_T - PAD_B;
  const rels = useMemo(
    () => Array.from({ length: REL_TO - REL_FROM + 1 }, (_, i) => REL_FROM + i),
    [],
  );

  const model = useMemo(() => {
    /** 一项在相对月 r 上的归一化取值；越界或该项本身没有热度则为 null */
    const curveOf = (s: EntitySignal, key: 'paper' | 'jd') => {
      const zero = s.months.indexOf(s.firstJdAt!);
      const arr = s[key];
      const peak = Math.max(...arr);
      if (zero < 0 || peak <= 0) return null;
      return rels.map((r) => {
        const i = zero + r;
        return i >= 0 && i < arr.length ? arr[i] / peak : null;
      });
    };

    const papers = signals.map((s) => curveOf(s, 'paper')).filter((c): c is (number | null)[] => !!c);
    const jds = signals.map((s) => curveOf(s, 'jd')).filter((c): c is (number | null)[] => !!c);

    const medianAt = (curves: (number | null)[][], i: number) => {
      const v = curves.map((c) => c[i]).filter((x): x is number => x != null);
      if (v.length === 0) return null;
      v.sort((a, b) => a - b);
      const m = v.length >> 1;
      return { v: v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2, n: v.length };
    };

    const medPaper = rels.map((_, i) => medianAt(papers, i));
    const medJd = rels.map((_, i) => medianAt(jds, i));

    /* 读数：中位曲线什么时候越过自身峰值的一半 —— 那是“学术侧开始起量”的时点，
       比取峰值稳，峰值常落在 0 之后（论文热度在岗位出现后还会继续涨）。 */
    const peakMed = Math.max(...medPaper.map((m) => m?.v ?? 0));
    let riseRel: number | null = null;
    for (let i = 0; i < rels.length; i++) {
      if (rels[i] > 0) break;
      if ((medPaper[i]?.v ?? 0) >= peakMed * 0.5) {
        riseRel = rels[i];
        break;
      }
    }
    return { papers, medPaper, medJd, peakMed, riseRel, n: papers.length };
  }, [signals, rels]);

  const X = (i: number) => PAD_L + (i / (rels.length - 1)) * plotW;
  const Y = (v: number) => PAD_T + plotH - v * plotH;
  const zeroX = X(rels.indexOf(0));

  /** 把一条含空洞的曲线切成若干连续段，空洞处断开而不是连成直线 */
  const segments = (c: (number | null)[]): [number, number][][] => {
    const out: [number, number][][] = [];
    let cur: [number, number][] = [];
    c.forEach((v, i) => {
      if (v == null) {
        if (cur.length > 1) out.push(cur);
        cur = [];
      } else cur.push([X(i), Y(v)]);
    });
    if (cur.length > 1) out.push(cur);
    return out;
  };

  return (
    <>
      <div className="dv-bar">
        <span className="dv-fit">
          共 <b>{model.n}</b> 项，各自按招聘确认月对齐
        </span>
        {model.riseRel != null && (
          <span className="dv-fit">
            中位曲线在确认前 <b className="hi">{-model.riseRel}</b> 个月越过自身峰值的一半
          </span>
        )}
      </div>

      <svg width={W} height={H} style={{ display: 'block' }} role="img"
        aria-label={`${model.n} 项已确认条目按各自招聘确认月对齐后的论文热度叠加`}
      >
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={PAD_L} y1={Y(f)} x2={W - PAD_R} y2={Y(f)} stroke="var(--line)" />
            <text x={PAD_L - 6} y={Y(f) + 4} fontSize={11} fill="var(--ink-2)" textAnchor="end">
              {(f * 100).toFixed(0)}%
            </text>
          </g>
        ))}
        {rels.map((r, i) =>
          r % 6 === 0 ? (
            <text key={r} x={X(i)} y={H - 10} fontSize={11} fill="var(--ink-2)" textAnchor="middle">
              {r === 0 ? '确认月' : r}
            </text>
          ) : null,
        )}

        {/* 每一项自己的论文曲线：淡，只提供“散不散”的观感 */}
        {model.papers.map((c, k) =>
          segments(c).map((seg, j) => (
            <path
              key={`${k}-${j}`}
              d={smoothPath(seg)}
              fill="none"
              stroke="var(--src-paper)"
              strokeWidth={1}
              opacity={0.13}
            />
          )),
        )}

        {/* 中位曲线：招聘在后、论文在前，两条一起画才看得出先后 */}
        {segments(model.medJd.map((m) => m?.v ?? null)).map((seg, j) => (
          <path key={`mj-${j}`} d={smoothPath(seg)} fill="none" stroke="var(--src-jd)" strokeWidth={2.2} />
        ))}
        {segments(model.medPaper.map((m) => m?.v ?? null)).map((seg, j) => (
          <path key={`mp-${j}`} d={smoothPath(seg)} fill="none" stroke="var(--src-paper)" strokeWidth={2.4} />
        ))}

        <line x1={zeroX} y1={PAD_T} x2={zeroX} y2={PAD_T + plotH} stroke="var(--ink-2)" strokeDasharray="4 3" opacity={0.7} />
        <text x={zeroX - 6} y={PAD_T + 12} fontSize={11} fill="var(--ink-2)" textAnchor="end">
          招聘确认月
        </text>
      </svg>

      <div className="viz-legend">
        <span>
          <i style={{ background: 'var(--src-paper)', opacity: 0.28 }} />
          单项论文热度
        </span>
        <span>
          <i style={{ background: 'var(--src-paper)' }} />
          论文热度中位曲线
        </span>
        <span>
          <i style={{ background: 'var(--src-jd)' }} />
          招聘热度中位曲线
        </span>
      </div>
    </>
  );
}
