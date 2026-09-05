/* ============================================================
   棱镜时间轴 —— 能力棱镜的时间游标

   ------------------------------------------------------------
   为什么时间维长成一根轴，而不是别的样子

   棱镜是一张同心环图，四层结构本身已经占满了两个视觉通道
   （角度 = 谁挨着谁，半径 = 在第几层）。时间要挤进去，只剩三条路：

     ① 把时间铺到角度或半径上 —— 那要拆掉现有的四层结构，
        换来的是一张"时间螺旋"，四层就读不出来了。
     ② 小倍数：一行几十张缩略棱镜。棱镜单张就已经要靠 500px 直径
        才标得出十几个名字，缩到 120px 一个名字也剩不下。
     ③ 一次只画一个月，用一根轴选月份，用动画把两个月之间连起来。

   取 ③。于是这根轴要同时回答三个问题，而不只是"现在停在几月"：

     · 什么时候有变化 —— 轴底衬着当前剖面逐月的已确认项数曲线，
       哪一段陡、哪一段平，扫一眼就知道该把游标拖到哪。
     · 现在这个数是测出来的还是推出来的 —— 今日线之后是斜纹区，
       那六个月是按最近一年的增量外推的，不是观测。
     · 跟什么比 —— 可以在轴上钉一个对比基准月，棱镜随即显示残影。

   ------------------------------------------------------------
   一根轴上同时存在两个游标（当前月、基准月），所以它们的形状必须
   一眼分得开：当前月是实线加圆头，基准月是虚线加方旗。
   只靠颜色区分的话，在把整页打成灰度的评审材料里就分不出来了。
   ============================================================ */

import { useCallback, useMemo, useRef, useState } from 'react';
import { useSize } from '@/hooks/useSize';
import { Tooltip, type TipState } from '@/components/common/Tooltip';
import { Icon } from '@/components/Icon';
import { smoothPath } from '@/utils/viz';

interface Milestone {
  month: string;
  label: string;
}

interface Props {
  months: string[];
  /** 外推段的起始下标；>= months.length 表示整段都是实测 */
  forecastFrom: number;
  /** "回到最新"落在哪个月。默认是轴末，有外推段时应传最后一个实测月 ——
      "最新"指的是最新的观测，不是轴上最右边那一格 */
  latest?: number;
  /** 当前游标（月份下标） */
  value: number;
  onChange: (i: number) => void;
  /** 对比基准月下标，null 表示未设 */
  baseline: number | null;
  onBaseline: (i: number | null) => void;
  playing: boolean;
  onPlaying: (v: boolean) => void;
  /** 轴底衬的合计序列，长度与 months 对齐；null = 当月无观测 */
  total: (number | null)[];
  /** 这条底衬曲线量的是什么 —— 出现在悬停提示里。
      不同的图往轴上衬的东西不一样，写死一句"已进入招聘要求 N 项"会说错 */
  totalLabel?: string;
  /** 轴上的里程碑标记（图谱版本） */
  milestones?: Milestone[];
}

const H = 58;
const TOP = 10;
const BOT = 40;
const PAD = 10;

/** 'YYYY-MM' → '2025 年 3 月' */
export function monthText(m: string): string {
  const [y, mo] = m.split('-');
  return `${y} 年 ${Number(mo)} 月`;
}

export function PrismTimeAxis({
  months,
  forecastFrom,
  latest,
  value,
  onChange,
  baseline,
  onBaseline,
  playing,
  onPlaying,
  total,
  totalLabel = '当月已进入招聘要求',
  milestones = [],
}: Props) {
  const { ref, w } = useSize<HTMLDivElement>();
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [tip, setTip] = useState<TipState | null>(null);
  /** 悬停到的月份 —— 只影响轴自己的浮标，不动棱镜 */
  const [hoverI, setHoverI] = useState<number | null>(null);

  const W = Math.max(260, w || 640);
  const plotW = W - PAD * 2;
  const last = months.length - 1;
  const newest = Math.max(0, Math.min(last, latest ?? last));
  const x = useCallback((i: number) => PAD + (i / Math.max(1, last)) * plotW, [last, plotW]);

  /** 指针位置 → 最近的月份下标 */
  const indexAt = useCallback(
    (clientX: number) => {
      const box = svgRef.current?.getBoundingClientRect();
      if (!box) return value;
      const r = (clientX - box.left - PAD) / Math.max(1, plotW);
      return Math.max(0, Math.min(last, Math.round(r * last)));
    },
    [plotW, last, value],
  );

  /* 轴底衬：当前口径的合计规模。归一化只看这一条序列自己的最大值 ——
     它是"什么时候有变化"的向导，不是要和棱镜的段高比大小。 */
  const areaD = useMemo(() => {
    const max = Math.max(...total.map((v) => v ?? 0), 1);
    const pts: [number, number][] = [];
    total.forEach((v, i) => {
      if (v === null) return;
      pts.push([x(i), BOT - (v / max) * (BOT - TOP - 2)]);
    });
    if (pts.length < 2) return { line: '', fill: '' };
    const line = smoothPath(pts);
    return {
      line,
      fill: `${line} L${pts[pts.length - 1][0]},${BOT} L${pts[0][0]},${BOT} Z`,
    };
  }, [total, x]);

  const yearTicks = useMemo(
    () => months.map((m, i) => ({ m, i })).filter(({ m }) => m.endsWith('-01')),
    [months],
  );

  const drag = useCallback(
    (e: React.PointerEvent<SVGSVGElement>) => {
      e.preventDefault();
      onPlaying(false);
      onChange(indexAt(e.clientX));
      const move = (ev: PointerEvent) => onChange(indexAt(ev.clientX));
      const up = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
        window.removeEventListener('pointercancel', up);
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
      window.addEventListener('pointercancel', up);
    },
    [indexAt, onChange, onPlaying],
  );

  const onKey = useCallback(
    (e: React.KeyboardEvent) => {
      const step =
        e.key === 'ArrowLeft' ? -1 : e.key === 'ArrowRight' ? 1 : e.key === 'PageDown' ? -12 : e.key === 'PageUp' ? 12 : 0;
      if (step) {
        e.preventDefault();
        onPlaying(false);
        onChange(Math.max(0, Math.min(last, value + step)));
        return;
      }
      if (e.key === 'Home' || e.key === 'End') {
        e.preventDefault();
        onPlaying(false);
        onChange(e.key === 'Home' ? 0 : last);
      }
    },
    [value, last, onChange, onPlaying],
  );

  const isForecast = value >= forecastFrom;
  const cursorX = x(value);
  const baseX = baseline === null ? null : x(baseline);

  return (
    <div className="pt-axis" ref={ref}>
      {/* ---- 控制行 ---- */}
      <div className="pt-bar">
        <div className="pt-play">
          <button
            className="pt-btn"
            onClick={() => {
              onPlaying(false);
              onChange(Math.max(0, value - 1));
            }}
            disabled={value <= 0}
            aria-label="上一个月"
          >
            <Icon name="chevronL" size={15} />
          </button>
          <button
            className={playing ? 'pt-btn main on' : 'pt-btn main'}
            onClick={() => onPlaying(!playing)}
            aria-label={playing ? '暂停' : '按月播放'}
          >
            {playing ? (
              <svg width="13" height="13" viewBox="0 0 13 13" aria-hidden="true">
                <rect x="2" y="1.5" width="3.4" height="10" rx="1" fill="currentColor" />
                <rect x="7.6" y="1.5" width="3.4" height="10" rx="1" fill="currentColor" />
              </svg>
            ) : (
              <svg width="13" height="13" viewBox="0 0 13 13" aria-hidden="true">
                <path d="M3 1.6 L11.4 6.5 L3 11.4 Z" fill="currentColor" />
              </svg>
            )}
          </button>
          <button
            className="pt-btn"
            onClick={() => {
              onPlaying(false);
              onChange(Math.min(last, value + 1));
            }}
            disabled={value >= last}
            aria-label="下一个月"
          >
            <Icon name="chevronR" size={15} />
          </button>
        </div>

        <div className="pt-now">
          <b>{monthText(months[value])}</b>
          {isForecast ? <span className="pt-flag fc">外推</span> : <span className="pt-flag">实测</span>}
        </div>

        <div className="pt-acts">
          {baseline === null ? (
            <button className="link-btn" onClick={() => onBaseline(value)}>
              设为对比基准
            </button>
          ) : (
            <>
              <span className="pt-basis">
                对比 {months[baseline]}
                {baseline !== value && (
                  <em>
                    {value > baseline ? '＋' : '－'}
                    {Math.abs(value - baseline)} 个月
                  </em>
                )}
              </span>
              <button className="link-btn" onClick={() => onBaseline(null)}>
                取消对比
              </button>
            </>
          )}
          {value !== newest && (
            <button
              className="link-btn"
              onClick={() => {
                onPlaying(false);
                onChange(newest);
              }}
            >
              回到最新
            </button>
          )}
        </div>
      </div>

      {/* ---- 轴 ---- */}
      <svg
        ref={svgRef}
        width={W}
        height={H}
        className="pt-svg"
        role="slider"
        tabIndex={0}
        aria-label="棱镜时间游标"
        aria-valuemin={0}
        aria-valuemax={last}
        aria-valuenow={value}
        aria-valuetext={`${monthText(months[value])}${isForecast ? '，外推' : ''}`}
        onPointerDown={drag}
        onKeyDown={onKey}
        onMouseMove={(e) => {
          const i = indexAt(e.clientX);
          setHoverI(i);
          const v = total[i];
          setTip({
            x: e.clientX,
            y: e.clientY,
            content: (
              <>
                <div className="tt-title">{monthText(months[i])}</div>
                <div>
                  {totalLabel} {v === null ? '—' : v >= 100 ? Math.round(v) : v.toFixed(1)}
                </div>
                <div className="tt-muted">
                  {i >= forecastFrom ? '外推区间：按最近一年的增量推算，非观测值' : '点击或拖动定位到该月'}
                </div>
              </>
            ),
          });
        }}
        onMouseLeave={() => {
          setHoverI(null);
          setTip(null);
        }}
      >
        <defs>
          {/* 外推区的斜纹：与全站"尚未落地"的表达一致，不另占一个颜色 */}
          <pattern id="pt-fc" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="5" height="5" fill="var(--panel)" />
            <line x1="0" y1="0" x2="0" y2="5" stroke="var(--ink-3)" strokeWidth="1.4" opacity="0.34" />
          </pattern>
        </defs>

        {/* 底轨 */}
        <rect x={PAD} y={TOP} width={plotW} height={BOT - TOP} fill="var(--viz-track)" rx={4} />

        {/* 外推区 */}
        {forecastFrom <= last && (
          <>
            <rect
              x={x(forecastFrom)}
              y={TOP}
              width={Math.max(0, plotW + PAD - x(forecastFrom))}
              height={BOT - TOP}
              fill="url(#pt-fc)"
            />
            <line x1={x(forecastFrom)} y1={TOP - 4} x2={x(forecastFrom)} y2={BOT + 3} stroke="var(--ink-3)" strokeWidth={1} />
            <text className="pt-tick fc" x={x(forecastFrom) + 4} y={TOP + 8}>
              今日线
            </text>
          </>
        )}

        {/* 年份网格 */}
        {yearTicks.map(({ m, i }) => (
          <g key={m}>
            <line x1={x(i)} y1={TOP} x2={x(i)} y2={BOT} stroke="var(--line-strong)" strokeWidth={1} />
            <text className="pt-tick" x={x(i)} y={BOT + 13} textAnchor="middle">
              {m.slice(0, 4)}
            </text>
          </g>
        ))}

        {/* 已确认项数：知道该把游标拖到哪一段去 */}
        {areaD.fill && <path d={areaD.fill} fill="var(--primary)" opacity={0.1} />}
        {areaD.line && <path d={areaD.line} fill="none" stroke="var(--primary)" strokeWidth={1.4} opacity={0.5} />}

        {/* 里程碑：图谱版本。悬停轴面时才显形，常态只留一个小缺口 */}
        {milestones.map((ms) => {
          const i = months.indexOf(ms.month);
          if (i < 0) return null;
          return (
            <g key={ms.month}>
              <path d={`M${x(i) - 3.6},${TOP} L${x(i) + 3.6},${TOP} L${x(i)},${TOP + 4.6} Z`} fill="var(--ink-3)" />
              <title>{`${ms.month} ${ms.label}`}</title>
            </g>
          );
        })}

        {/* 对比基准：虚线 + 方旗，与当前月的实线圆头在形状上分得开 */}
        {baseX !== null && (
          <g>
            <line x1={baseX} y1={TOP - 5} x2={baseX} y2={BOT + 3} stroke="var(--ink-2)" strokeWidth={1.4} strokeDasharray="3 3" />
            <rect x={baseX - 1} y={TOP - 9} width={9} height={7} fill="var(--ink-2)" rx={1} />
          </g>
        )}

        {/* 悬停浮标 */}
        {hoverI !== null && hoverI !== value && (
          <line x1={x(hoverI)} y1={TOP} x2={x(hoverI)} y2={BOT} stroke="var(--ink-3)" strokeWidth={1} opacity={0.6} />
        )}

        {/* 当前月 */}
        <g style={{ pointerEvents: 'none' }}>
          <line x1={cursorX} y1={TOP - 7} x2={cursorX} y2={BOT + 4} stroke="var(--primary)" strokeWidth={2} />
          <circle cx={cursorX} cy={TOP - 7} r={4.4} fill="var(--primary)" stroke="var(--panel)" strokeWidth={1.4} />
        </g>
      </svg>
      <Tooltip tip={tip} />
    </div>
  );
}
