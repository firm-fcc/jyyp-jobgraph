/* ============================================================
   技术信号传导时间线

   一项技术从被提出到写进招聘要求，总要走一段路：
   先出现在学术论文里，再被行业新闻报道，最后企业才把它写进岗位要求。
   每行一个条目，横轴是时间，三枚标记就是它在三类数据源里第一次出现的时刻，
   标记之间的水平距离，就是这项技术领先招聘市场的时间 ——
   把“论文比招聘需求早一到三年”这句话，变成图上能直接量出来的长度。

   行末一列是“信号时效”：论文和新闻已经在讲、招聘市场却迟迟没跟进的条目，
   越久没被确认，这条前瞻信号在图谱里的分量就越低。
   ============================================================ */

import { useMemo, useState } from 'react';
import type { EntitySignal } from '@/types/graph';
import { MONTHS } from '@/data/generator';
import { addMonths, monthDiff } from '@/utils/format';
import { Tooltip, type TipState } from '@/components/common/Tooltip';
import { useSize } from '@/hooks/useSize';

interface Props {
  signals: EntitySignal[];
  limit?: number;
  onPick?: (s: EntitySignal) => void;
  selectedId?: string | null;
}

/** 三枚首现标记的画法，图上与图例共用一份定义 —— 两处各画各的，
    对不上的是读图的人。图例里的 <i> 按同一组几何参数由 CSS 描出。 */
const MK_R = 4;
const MK_DIAMOND = 3.4;
const MK_TRI = { up: 5, down: 3.4, half: 4.4 };

const ROW_H = 24;
/* 顶部留给刻度标签与末列的两行列头。列头此前一行写"信号时效"，四个字塞得下；
   现在要写出这一列的两种读数（已确认 / 未确认的时效），一行放不下便折成两行，
   顶部因而多留一档。 */
const TOP = 48;
const NAME_FS = 12;
/** 时间轴要有最小可读宽度；容器比这还窄就让它在自己的框里横向滚，而不是把整页撑破 */
const MIN_W = 520;

/** 中英混排宽度估算：中日韩字符按全角算，其余按半角 */
function textWidth(s: string, fs: number): number {
  let w = 0;
  for (const ch of s) w += /[㐀-鿿豈-﫿＀-｠]/.test(ch) ? fs : fs * 0.56;
  return w;
}

/** 招聘首现的三角标记。已确认与预计确认共用同一个形状，只差填充与描边 */
const triPath = (cx: number, cy: number) =>
  `M${cx},${cy - MK_TRI.up} L${cx + MK_TRI.half},${cy + MK_TRI.down} L${cx - MK_TRI.half},${cy + MK_TRI.down} Z`;

/** 名字放不下时截断收口 —— 悬停仍能看到完整名称 */
function clip(s: string, maxPx: number): string {
  if (textWidth(s, NAME_FS) <= maxPx) return s;
  let cut = s;
  while (cut.length > 1 && textWidth(cut, NAME_FS) + NAME_FS > maxPx) cut = cut.slice(0, -1);
  return `${cut}…`;
}

export function SignalTimeline({ signals, limit = 26, onPick, selectedId }: Props) {
  const { ref, w } = useSize<HTMLDivElement>();
  const [tip, setTip] = useState<TipState | null>(null);

  /** 只画有论文信号的条目，按论文首次出现时间倒序 —— 最新的技术排在最上面。
      total 是截断之前的条数：画出来的不等于全部，这个数要写在图外。 */
  const { rows, total } = useMemo(() => {
    const all = signals
      .filter((s) => s.firstPaperAt)
      .sort((a, b) => {
        const d = monthDiff(b.firstPaperAt!, a.firstPaperAt!);
        return d !== 0 ? -d : a.entityName.localeCompare(b.entityName);
      });
    return { rows: all.slice(0, limit), total: all.length };
  }, [signals, limit]);

  /** 图上这一屏究竟画出了哪几种图元 —— 图例据此逐条决定去留 */
  const has = useMemo(
    () => ({
      paper: rows.some((s) => s.firstPaperAt),
      news: rows.some((s) => s.firstNewsAt),
      jd: rows.some((s) => s.firstJdAt),
      predicted: rows.some((s) => !s.firstJdAt && s.predictedJdAt),
      pending: rows.some((s) => !s.firstJdAt),
      range: rows.some((s) => !s.firstJdAt && s.predictedJdRange),
    }),
    [rows],
  );

  const W = Math.max(MIN_W, w || 760);
  /** 窄屏下名字列与时效列一起收窄，把宽度让给时间轴 —— 那才是这张图要读的东西 */
  const narrow = W < 620;
  const LEFT = narrow ? 108 : 150;
  /* 末列要放下两行列头里较长的那一行（"招聘市场已确认 /"，七字加一个斜杠），
     故比只写"信号时效"时宽一档 */
  const RIGHT = narrow ? 94 : 118;
  const BAR_W = narrow ? 34 : 54;
  const H = rows.length * ROW_H + TOP + 12;
  const plotW = W - LEFT - RIGHT;

  /* ---------------- 横轴的取值区间 ----------------

     轴不铺满整个观测跨度，只铺到画出来的这些条目实际落在的那一段。

     这张图列的是按论文首现时间最新的若干项，而观测跨度已到四十八个自然月：
     铺满全跨度时，这十几项的三枚标记全挤在最右侧几个月里，标记之间的水平
     距离——也就是这张图要量的"领先多久"——短到读不出来，轴的前四分之三是空的。

     代价是这条轴与全站其他几处的时间尺度不再一致，故轴的起止月份写在图注里，
     且两端各留一格余量，使最早与最晚的那枚标记不贴在轴端上。 */
  const domain = useMemo(() => {
    const at = (m?: string) => (m ? monthDiff(MONTHS[0], m) : null);
    let lo = Infinity;
    let hi = -Infinity;
    for (const s of rows) {
      for (const m of [
        s.firstPaperAt,
        s.firstNewsAt,
        s.firstJdAt,
        s.predictedJdAt,
        s.predictedJdRange?.[0],
        s.predictedJdRange?.[1],
      ]) {
        const i = at(m);
        if (i === null) continue;
        if (i < lo) lo = i;
        if (i > hi) hi = i;
      }
    }
    const last = monthDiff(MONTHS[0], MONTHS[MONTHS.length - 1]);
    if (!Number.isFinite(lo) || hi <= lo) return { lo: 0, hi: Math.max(1, last) };
    const pad = Math.max(1, Math.round((hi - lo) * 0.06));
    return { lo: Math.max(0, lo - pad), hi: hi + pad };
  }, [rows]);

  const SPAN = Math.max(1, domain.hi - domain.lo);
  const rawX = (m: string) => LEFT + ((monthDiff(MONTHS[0], m) - domain.lo) / SPAN) * plotW;
  /** 预计确认时间可能落在轴的取值区间之外，夹回画布内，否则会压到两侧的列上 */
  const x = (m: string) => Math.min(LEFT + plotW, Math.max(LEFT, rawX(m)));

  /* 刻度在轴的取值区间内给：跨度满两年时逐年一道，否则按季一道，
     再短则按月。一道标签约占三十像素，道数因而另设上限。 */
  const ticks = useMemo(() => {
    const span = domain.hi - domain.lo;
    const out: { m: string; label: string }[] = [];
    const push = (i: number, label: string) => out.push({ m: addMonths(MONTHS[0], i), label });
    if (span >= 24) {
      for (let i = domain.lo; i <= domain.hi; i++) {
        const m = addMonths(MONTHS[0], i);
        if (m.endsWith('-01')) push(i, m.slice(0, 4));
      }
      if (out.length >= 2) return out;
      out.length = 0;
    }
    const step = Math.max(1, Math.ceil((span + 1) / 8));
    for (let i = domain.lo; i <= domain.hi; i += step) push(i, addMonths(MONTHS[0], i).slice(2));
    return out;
  }, [domain]);
  const trackX = LEFT + plotW + 12;

  if (rows.length === 0) {
    return <p className="viz-empty">当前筛选条件下没有可展示的条目，可放宽技术栈或级别筛选。</p>;
  }

  return (
    <div ref={ref} className="wf">
      <svg width={W} height={H} style={{ display: 'block' }}>
        {/* ---- 时间网格 ---- */}
        <g>
          {ticks.map((t, i) => (
            <g key={t.m}>
              <line x1={rawX(t.m)} y1={TOP - 12} x2={rawX(t.m)} y2={H - 6} stroke="var(--line)" strokeWidth={1} />
              {/* 末档刻度落在绘图区右缘，居中对齐会向右溢出半个标签，压到“信号时效”那一列上 */}
              <text
                x={rawX(t.m)}
                y={TOP - 18}
                fontSize={11}
                fill="var(--ink-2)"
                textAnchor={i === ticks.length - 1 ? 'end' : i === 0 ? 'start' : 'middle'}
              >
                {t.label}
              </text>
            </g>
          ))}
          {/* 这一列两种读数并存：已被招聘市场确认的直接写结论，未确认的画一根
              时效条。列头因而把两者都写出来，只写“信号时效”时，整列都是
              “已确认”的那一档就没有列头可对应。

              折成两行：写在一行需要一百三十像素，那是从时间轴上匀出来的宽度，
              而时间轴的长度本身就是这张图要读的量。 */}
          <text x={trackX} y={TOP - 31} fontSize={10.5} fill="var(--ink-2)">
            招聘市场已确认 /
          </text>
          <text x={trackX} y={TOP - 18} fontSize={10.5} fill="var(--ink-2)">
            信号时效
          </text>
        </g>

        {rows.map((s, i) => {
          const y = TOP + i * ROW_H;
          const px = s.firstPaperAt ? x(s.firstPaperAt) : null;
          const nx = s.firstNewsAt ? x(s.firstNewsAt) : null;
          const jx = s.firstJdAt ? x(s.firstJdAt) : null;
          const confirmed = !!s.firstJdAt;
          const endX = jx ?? (s.predictedJdAt ? x(s.predictedJdAt) : LEFT + plotW);
          const sel = selectedId === s.entityId;
          const fresh = Math.round(s.decayFactor * 100);
          /* 传导链的两端：把这一行上画出来的标记与右端时刻一并纳入，
             再取最左与最右。两端重合（只有一枚标记）时不画线。
             月份与像素一并记：线上要标出两端相隔几个月，那个数只能由月份算，
             按像素反推会把横轴的舍入一并算进读数。 */
          const endM = confirmed ? s.firstJdAt! : (s.predictedJdAt ?? MONTHS[MONTHS.length - 1]);
          const marks = [
            px !== null ? { x: px, m: s.firstPaperAt! } : null,
            nx !== null ? { x: nx, m: s.firstNewsAt! } : null,
            { x: confirmed ? jx! : endX, m: endM },
          ].filter((v): v is { x: number; m: string } => v !== null);
          const ordered = [...marks].sort((a, b) => a.x - b.x);
          const lo = ordered[0];
          const hi = ordered[ordered.length - 1];
          const span: [number, number] | null =
            marks.length > 1 && hi.x - lo.x > 0.5 ? [lo.x, hi.x] : null;
          /* 逐段标月数，不标整条。一行上三枚标记之间有两段，两段问的是两件事
             （论文到新闻隔多久、新闻到招聘又隔多久）；只在整条上标一个总数，
             这两件事都读不出来。段窄到放不下字时该段不标，其余段照标。 */
          const segs = ordered.slice(0, -1).map((a, i) => {
            const b = ordered[i + 1];
            return { a, b, months: Math.abs(monthDiff(a.m, b.m)) };
          });

          return (
            <g
              key={s.entityId}
              className={sel ? 'stl-row on' : 'stl-row'}
              style={{ cursor: 'pointer' }}
              onMouseEnter={(ev) =>
                setTip({
                  x: ev.clientX,
                  y: ev.clientY,
                  content: (
                    <>
                      <div className="tt-title">{s.entityName}</div>
                      <div className="tt-muted">{s.category}</div>
                      <div>
                        学术论文 {s.firstPaperAt ?? '—'} · 行业新闻 {s.firstNewsAt ?? '—'} · 招聘要求{' '}
                        {s.firstJdAt ?? '尚未出现'}
                      </div>
                      {confirmed ? (
                        /* 提前量直接由两个首现月相减，不读 leadMonths：那一项由互相关
                           最优滞后算出，两条序列都太短时算不出值，浮层上就成了一个
                           破折号 —— 而图上这一行的线已经标出了实实在在的月数。
                           招聘早于论文时提前量为负，措辞随之调过来。 */
                        <div>
                          {(() => {
                            const lead = monthDiff(s.firstPaperAt!, s.firstJdAt!);
                            return lead >= 0 ? (
                              <>
                                论文首现较招聘要求提前 <b>{lead}</b> 个月
                              </>
                            ) : (
                              <>
                                招聘要求较论文首现早 <b>{-lead}</b> 个月
                              </>
                            );
                          })()}
                        </div>
                      ) : (
                        <div className="tt-fore">
                          预计 {s.predictedJdAt} 前后进入招聘要求（{s.predictedJdRange?.[0]} ~{' '}
                          {s.predictedJdRange?.[1]}）
                        </div>
                      )}
                      <div className="tt-muted">点击查看该项的数据源对照</div>
                    </>
                  ),
                })
              }
              onMouseLeave={() => setTip(null)}
              onClick={() => onPick?.(s)}
            >
              {/* 整行的命中区。SVG 的 <g> 自身不接收指针事件，只有画出来的子元素才接收，
                  故此前只有点、线、条那几处能悬停与点击，行内的空白处点不动 ——
                  而一行说的是同一个条目，行上任何一处都该等效。这块透明矩形铺满整行，
                  悬停时转为浅底，选中时由下一块矩形盖成主色浅底。 */}
              <rect className="stl-hit" x={2} y={y - ROW_H / 2} width={W - 4} height={ROW_H} rx={4} />
              {sel && <rect x={2} y={y - ROW_H / 2} width={W - 4} height={ROW_H} fill="var(--primary-soft)" rx={4} />}
              <text
                x={LEFT - 10}
                y={y + 4}
                fontSize={NAME_FS}
                textAnchor="end"
                fill={confirmed ? 'var(--ink)' : 'var(--src-paper-ink)'}
              >
                {clip(s.entityName, LEFT - 18)}
              </text>

              {/* 尚未进入招聘要求：画出预计确认的时间区间 */}
              {!confirmed && s.predictedJdRange && (
                <rect
                  x={x(s.predictedJdRange[0])}
                  y={y - 6}
                  width={Math.max(2, x(s.predictedJdRange[1]) - x(s.predictedJdRange[0]))}
                  height={12}
                  fill="var(--src-paper)"
                  opacity={0.16}
                  rx={3}
                />
              )}

              {/* 传导链：连起这一行上出现过的三枚标记，两端取最早与最晚的那一枚。

                  上一版自论文首现画到招聘确认，右端取 max(px, endX)：招聘要求
                  早于论文与新闻时右端即等于左端，线长为零，那批行看上去是三个
                  互不相干的点。本批数据里这类行不在少数（招聘一路的观测起点
                  早于叠层信号的入场），因而一并连起来。

                  已确认与未确认仍分得开：前者实线、取招聘一路的色，
                  后者虚线、取论文一路的色，右端落在预计确认的时刻。 */}
              {span !== null && (
                <line
                  x1={span[0]}
                  y1={y}
                  x2={span[1]}
                  y2={y}
                  stroke={confirmed ? 'var(--src-jd)' : 'var(--src-paper)'}
                  strokeWidth={1.6}
                  strokeOpacity={confirmed ? 0.55 : 0.5}
                  strokeDasharray={confirmed ? undefined : '4 3'}
                />
              )}

              {/* 三枚首现标记。几何参数取自上方的常量，图例按同一组值描出 */}
              {px !== null && <circle cx={px} cy={y} r={MK_R} fill="var(--src-paper)" />}
              {nx !== null && (
                <rect
                  x={nx - MK_DIAMOND}
                  y={y - MK_DIAMOND}
                  width={MK_DIAMOND * 2}
                  height={MK_DIAMOND * 2}
                  fill="var(--src-news)"
                  transform={`rotate(45 ${nx} ${y})`}
                />
              )}
              {jx !== null ? (
                <path d={triPath(jx, y)} fill="var(--src-jd)" />
              ) : s.predictedJdAt ? (
                <path
                  d={triPath(x(s.predictedJdAt), y)}
                  fill="var(--panel)"
                  stroke="var(--src-paper)"
                  strokeWidth={1.2}
                />
              ) : null}

              {/* 相邻两枚标记之间隔几个月：标在该段上方的中点。段长本身就是
                  这张图要读的量，不标出来读者只能拿刻度去量。段窄到放不下字时
                  不标，免得两段的字叠在一起。
                  末段的右端在未确认的一行上是预计确认时刻，措辞随之区分。 */}
              {segs.map((sg, k) =>
                sg.months > 0 && sg.b.x - sg.a.x > 46 ? (
                  <text
                    key={`sg-${k}`}
                    x={(sg.a.x + sg.b.x) / 2}
                    y={y - 7}
                    fontSize={11}
                    fill={
                      !confirmed && k === segs.length - 1 ? 'var(--src-paper-ink)' : 'var(--ink-2)'
                    }
                    textAnchor="middle"
                  >
                    {!confirmed && k === segs.length - 1
                      ? `预计 ${sg.months} 个月`
                      : `${sg.months} 个月`}
                  </text>
                ) : null,
              )}

              {/* 信号时效：已被招聘市场确认的不需要这根条，直接说结论 */}
              {confirmed ? (
                <text x={trackX} y={y + 4} fontSize={11} fill="var(--green)">
                  已确认
                </text>
              ) : (
                <>
                  <rect x={trackX} y={y - 4} width={BAR_W} height={8} fill="var(--surface-2)" rx={4} />
                  <rect
                    x={trackX}
                    y={y - 4}
                    width={Math.max(2, BAR_W * s.decayFactor)}
                    height={8}
                    fill={s.decayFactor > 0.6 ? 'var(--src-paper)' : s.decayFactor > 0.3 ? 'var(--amber)' : 'var(--red)'}
                    rx={4}
                  />
                  <text x={trackX + BAR_W + 6} y={y + 4} fontSize={11} fill="var(--ink-2)">
                    {fresh}%
                  </text>
                </>
              )}
            </g>
          );
        })}
      </svg>
      <Tooltip tip={tip} />
      {/* 图例逐条按图上是否真有这一种图元决定去留。上一版把六条全列出来，
          而本批数据下"预计进入招聘要求的时间区间"与那枚空心三角一个也没画出来
          —— 图例上摆着一格图里找不到的图形，读者只会以为是自己没找到。 */}
      <div className="viz-legend">
        {has.paper && (
          <span>
            <i className="mk-dot" />
            学术论文首次出现
          </span>
        )}
        {has.news && (
          <span>
            <i className="mk-diamond" />
            行业新闻首次出现
          </span>
        )}
        {has.jd && (
          <span>
            <i className="mk-tri" />
            招聘信息首次出现
          </span>
        )}
        {has.predicted && (
          <span>
            <i className="mk-tri-open" />
            预计进入招聘要求
          </span>
        )}
        {has.pending && (
          <span>
            <i className="mk-dash" />
            招聘市场尚未跟进
          </span>
        )}
        {has.range && (
          <span>
            <i className="mk-range" />
            预计进入招聘要求的时间区间
          </span>
        )}
        {has.pending && (
          <span>
            <i className="mk-track" />
            信号时效（招聘市场未确认时长越长，时效越低）
          </span>
        )}
        {/* 截断如实写出：只画前若干行而不说明总数，读者会把画出来的当成全部 */}
        <span className="muted stl-cap">
          共 {total} 项，按学术论文首现时间由近及远列出前 {rows.length} 项
        </span>
      </div>
    </div>
  );
}
