/* ============================================================
   能力年轮
   —— 把“一个岗位的能力要求这几年怎么变的”画成树的年轮

   半径 = 时间：圆心是最早的一季，一圈一个季度向外生长
   角度 = 该季各项能力的占比（每圈归一化到整圆）
   于是一项能力的“诞生与消亡”直接就是它那条色带的起止半径：
     · 新增 → 色带从中途某一圈开始，圈上打一道径向刻痕
     · 修改 → 角宽突变处打刻痕
     · 删除 → 色带中断，并在断点留一个 ✕
     · 合并 → 两条带汇成一条
   越过“今日线”的最外圈是尚未发生的预测环，用虚线描边。

   ------------------------------------------------------------
   颜色按能力组分

   色相由技能所属的能力组定，九个组九个色相，全站固定不轮转；同一组内的几项
   技能取同一色相的不同明度。于是圆周上同一个色相的一段就是同一组能力，
   而“程序设计与软件工程”与“算法与数据结构”这类同组条目，一眼看得出是一类。

   上一版的分法是“占比最高的八项各给一个色相，其余一律收进灰阶”。
   问题出在后半句：一个岗位的年轮取该岗位技能构成的前十五项，落进灰阶的
   有七项之多，且它们的份额之和常在四成上下 —— 圆周上因此有近半是一片
   由深到浅的灰。灰阶在这里既不表示“弱”也不表示“旧”，它只表示“排在第九位
   以后”，而这一条读者无从得知，看到的只是半张图褪了色。

   排序随之改为先按组、组内再按份额：同色相的条带因此连成一段，
   不与别的色相交替。组的先后仍按份额之和排，故十二点方向仍是这个岗位
   份额最大的那一组能力。
   ============================================================ */

import { useMemo, useState } from 'react';
import type { ChangeEvent, JobAnnuli } from '@/types/graph';
import { annulusPath, polar, TAU } from '@/utils/viz';
import { useSize } from '@/hooks/useSize';
import { Tooltip, type TipState } from '@/components/common/Tooltip';

/**
 * 十个能力组的固定色相，按组编码索引。
 *
 * 取值全部来自全站既有的定性色族，且都是深色档 —— 色带要压得住白底上的字。
 * 与组编码绑定而不与出场次序绑定：同一组能力在任何岗位、任何窗口都是同一色相，
 * 两个岗位的年轮并排看时，“这一段是数据与计算科学”这件事不必重新认一遍。
 *
 * 按能力维度分成两段冷暖：技术技能五组取冷色（蓝、紫罗兰、青绿、天青、靛蓝），
 * 基础通用五组取暖色（黄绿、琥珀、玫红、深红、品红）。配合下面按维度排序，
 * 圆周上因此是“一段冷、一段暖”，而不是十个色相随份额高低随机交替 ——
 * 后者每换一个岗位就重排一次，读者得从头认一遍哪一段是哪一类。
 *
 * T-DG「前瞻新技能」是技术技能下的第五组，收前瞻叠层里已被招聘市场
 * 接住、写进正式体系的那些条目。它在体系文件里排在 T-DA 之后，圆周上的两个
 * 邻居因而是青绿与黄绿，靛蓝与两者都拉得开。
 */
const GROUP_HUES: Record<string, string> = {
  /* 技术技能 */
  'T-SW': '#2563eb',
  'T-AI': '#7c5cfc',
  'T-DA': '#0f8f83',
  'T-SYS': '#0891b2',
  'T-DG': '#4338ca',
  /* 基础通用技能 */
  'F-1': '#65a30d',
  'F-2': '#d97706',
  'F-3': '#db2777',
  'F-4': '#9f1239',
  'F-5': '#a21caf',
};

/** 尚无组归属的技能（叠层的 PS- 一档）。中性色，不占任何一个组的色相 */
const NO_GROUP = '#64748b';

/**
 * 组内第 i 项相对基色的明度偏移：正值向白、负值向黑。
 *
 * 一浅一深交替而不是单向递减，为的是相邻两档拉得开 —— 单向取
 * 0 / 0.3 / 0.6 时，第二、三档在色带上几乎并列。
 * 六档之后回到基色：一个岗位的年轮至多取十五项，任一组占到七项的情形不存在。
 */
const SHADES = [0, 0.34, -0.28, 0.6, -0.48, 0.16];

/** 同色相的明度变体。t > 0 向白靠，t < 0 向黑靠 */
function shade(hex: string, t: number): string {
  const n = parseInt(hex.slice(1), 16);
  const ch = (sh: number) => {
    const c = (n >> sh) & 255;
    const v = t >= 0 ? c + (255 - c) * t : c * (1 + t);
    return Math.max(0, Math.min(255, Math.round(v)))
      .toString(16)
      .padStart(2, '0');
  };
  return `#${ch(16)}${ch(8)}${ch(0)}`;
}

interface Props {
  data: JobAnnuli;
  selectedSkillId: string | null;
  onSelectSkill: (id: string | null) => void;
}

interface Slice {
  skillId: string;
  name: string;
  a0: number;
  a1: number;
  share: number;
  origin: 'base' | 'overlay';
}

interface Ring {
  version: string;
  date: string;
  predicted?: boolean;
  slices: Slice[];
}

export function opLabel(op: ChangeEvent['op']) {
  return op === 'add' ? '新增' : op === 'remove' ? '删除' : op === 'merge' ? '合并' : '修改';
}

/** 弧上标能力名用的字号，与 jobs.css 的 .an-band 一致 */
const BAND_FS = 12.5;

/** 中英混排宽度估算：中日韩字符按全角算，其余按半角 */
function textWidth(s: string, fs: number): number {
  let w = 0;
  for (const ch of s) w += /[㐀-鿿豈-﫿＀-｠]/.test(ch) ? fs : fs * 0.56;
  return w;
}

/** 沿弧线排布的旋转角，超过半圈的一侧翻转 180°，避免出现倒字 */
function arcAngle(a: number): number {
  let deg = ((((a * 180) / Math.PI) % 360) + 360) % 360;
  if (deg > 90 && deg < 270) deg -= 180;
  return deg;
}

/** WCAG 相对亮度 */
function luminance(r: number, g: number, b: number): number {
  const f = (v: number) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

/**
 * 压在色带上的字该用黑还是白。
 *
 * 这几个色相里没有一个统一答案：深蓝、深玫红上白字读得最清，
 * 而琥珀、橄榄绿上白字只有 2.6:1，非得用深色字。
 * 所以按色带混白之后的实际亮度逐条算，取对比度高的那一边。
 *
 * 深色一端取 #0b1320 而不是主文字色：主文字色 #17223b 偏亮，
 * 在中等亮度的蓝、玫红上只有 4.1:1，正好卡在读不清的那一档。
 * 配合色带不透明度 0.8（见 BAND_ALPHA），十一种色相实测最低 5.14:1。
 */
const BAND_DARK = '#0b1320';
const BAND_DARK_RGB: [number, number, number] = [11, 19, 32];
/** 色带填充的不透明度。0.88 → 0.8 是为了给字腾出对比度，观感上几乎看不出区别 */
const BAND_ALPHA = 0.8;
const OVERLAY_ALPHA = 0.6;

function readableInk(hex: string, alpha: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return BAND_DARK;
  const n = parseInt(m[1], 16);
  // 色带是半透明地压在白色面板上，先把混合结果还原出来
  const mix = (c: number) => alpha * c + (1 - alpha) * 255;
  const L = luminance(mix((n >> 16) & 255), mix((n >> 8) & 255), mix(n & 255));
  const onWhite = 1.05 / (L + 0.05);
  const onDark = (L + 0.05) / (luminance(...BAND_DARK_RGB) + 0.05);
  return onDark >= onWhite ? BAND_DARK : '#ffffff';
}

/** 图例的一格 */
export interface AnnuliLegendItem {
  id: string;
  name: string;
  color: string;
  /** 所属能力组名。图例按它分段，同组的几项排在一起 */
  group: string;
}

/**
 * 全局能力顺序 + 配色。
 *
 * 顺序必须全局算一次而不是逐圈算：同一能力在各圈中的角位置连续，
 * 那条色带才连得成一条可追踪的带。
 *
 * 排序分三级 —— 先按能力维度（技术技能在前，基础通用在后），
 * 维度内按组的份额之和降序，组内的技能再按各自份额降序。
 * 份额取“份额 × 圈序”的加权和：末窗的构成比首窗更能代表当下，
 * 而只看末窗又会让中途退出的能力排到最后。
 */
export function useAnnuliColors(data: JobAnnuli) {
  return useMemo(() => {
    const score = new Map<string, number>();
    const nameOf = new Map<string, string>();
    const groupOf = new Map<string, string>();
    const codeOf = new Map<string, string>();
    data.rings.forEach((r, ri) => {
      r.slices.forEach((s) => {
        score.set(s.skillId, (score.get(s.skillId) ?? 0) + s.share * (ri + 1));
        nameOf.set(s.skillId, s.name);
        if (s.group) groupOf.set(s.skillId, s.group);
        if (s.groupCode) codeOf.set(s.skillId, s.groupCode);
      });
    });

    /* 先按组归堆。组内先排好，再按组的份额之和决定组与组的先后 */
    const byGroup = new Map<string, string[]>();
    for (const id of score.keys()) {
      const key = codeOf.get(id) ?? '';
      const list = byGroup.get(key);
      if (list) list.push(id);
      else byGroup.set(key, [id]);
    }
    const groupScore = (ids: string[]) => ids.reduce((a, id) => a + (score.get(id) ?? 0), 0);
    /* 维度由组编码的首字定：T- 技术技能、F- 基础通用技能。
       为此单取一个字段不划算 —— 编码本身即体系的两级结构。 */
    const dimRank = (code: string) => (code.startsWith('T-') ? 0 : code ? 1 : 2);
    const groups = [...byGroup.entries()]
      .map(([code, ids]) => ({
        code,
        ids: ids.sort((a, b) => (score.get(b) ?? 0) - (score.get(a) ?? 0)),
        total: groupScore(ids),
      }))
      /* 先按维度分段（冷色段在前、暖色段在后），段内再按份额之和排。
         无归属的一档一律垫底：它是中性色，夹在两个色相之间会被读成又一个组。 */
      .sort((a, b) => dimRank(a.code) - dimRank(b.code) || b.total - a.total);

    const order: string[] = [];
    const color = new Map<string, string>();
    for (const g of groups) {
      const hue = GROUP_HUES[g.code] ?? NO_GROUP;
      g.ids.forEach((id, i) => {
        order.push(id);
        color.set(id, shade(hue, SHADES[i % SHADES.length]));
      });
    }

    const legend: AnnuliLegendItem[] = order.map((k) => ({
      id: k,
      name: nameOf.get(k) ?? k,
      color: color.get(k)!,
      group: groupOf.get(k) ?? '暂无技能归属',
    }));

    return { order, color, nameOf, legend };
  }, [data]);
}

export function CapabilityAnnuli({ data, selectedSkillId, onSelectSkill }: Props) {
  const { ref, w, h } = useSize<HTMLDivElement>();
  const [tip, setTip] = useState<TipState | null>(null);
  /** 悬停也追一条带：这张图要回答的就是“某项能力从哪一圈起、到哪一圈断”，
      非得点一下才能追踪，等于每看一条都要先点再取消 */
  const [hoverSkill, setHoverSkill] = useState<string | null>(null);
  const { order, color } = useAnnuliColors(data);

  const rings: Ring[] = useMemo(
    () =>
      data.rings.map((r) => {
        const map = new Map(r.slices.map((s) => [s.skillId, s]));
        const slices: Slice[] = [];
        let acc = 0;
        for (const sid of order) {
          const s = map.get(sid);
          if (!s) continue;
          slices.push({
            skillId: sid,
            name: s.name,
            a0: acc * TAU,
            a1: (acc + s.share) * TAU,
            share: s.share,
            origin: s.origin,
          });
          acc += s.share;
        }
        return { version: r.version, date: r.date, predicted: r.predicted, slices };
      }),
    [data, order],
  );

  /** 变更事件按 (版本, 能力) 索引，用来在对应圈上打刻痕 */
  const changeAt = useMemo(() => {
    const m = new Map<string, ChangeEvent>();
    data.changes.forEach((c) => m.set(`${c.version}|${c.target.id}`, c));
    return m;
  }, [data]);

  const size = Math.max(300, Math.min(w || 560, (h || 560) - 2));
  const cx = size / 2;
  const cy = size / 2;
  /* 九圈要挤在一个半径里，环带宽度是这张图能不能读的第一杠杆：
     圆心从 0.135 收到 0.115、外缘从 -22 放到 -14，band 因此涨了两成多。
     外缘不需要留很多：版本刻度改成压在 12 点方向的白底小片上，不再往外伸。 */
  const R0 = size * 0.115;
  const R1 = size / 2 - 14;
  const gap = 2.6;
  const band = (R1 - R0) / rings.length - gap;
  const todayR = R0 + (rings.length - 1) * (band + gap) - gap / 2;
  /** 正在被追踪的那条带：点选优先，其次是悬停 */
  const traced = selectedSkillId ?? hoverSkill;
  /** 最外的一个真实版本环 —— 能力名标在它上面：
      半径最大、弧最长，能放下的名字最多；预测环是虚的，不适合当图注 */
  const predIdx = rings.findIndex((r) => r.predicted);
  const labelRi = predIdx > 0 ? predIdx - 1 : rings.length - 1;

  const showTip = (ev: React.MouseEvent, r: Ring, s: Slice) => {
    const ch = changeAt.get(`${r.version}|${s.skillId}`);
    setTip({
      x: ev.clientX,
      y: ev.clientY,
      content: (
        <>
          <div className="tt-title">{s.name}</div>
          <div>
            {r.version}（{r.date}）占比 <b>{(s.share * 100).toFixed(1)}%</b>
          </div>
          <div className={s.origin === 'overlay' || r.predicted ? 'tt-fore' : 'tt-muted'}>
            {r.predicted
              ? '预测环 · 尚未发生'
              : s.origin === 'overlay'
                ? '主要由论文与新闻的前瞻信号支撑'
                : '由招聘信息证据支撑'}
          </div>
          {ch && <div className="tt-fore">本圈发生变更：{opLabel(ch.op)}</div>}
        </>
      ),
    });
  };

  return (
    <div className="annuli" ref={ref}>
      <svg className="annuli-svg" width={size} height={size} role="img" aria-label={`${data.jobName} 能力年轮`}>
        <defs>
          {/* 斜纹 = 前瞻信号支撑，与全景图谱保持同一套语义 */}
          <pattern id="an-hatch" width="5" height="5" patternTransform="rotate(45)" patternUnits="userSpaceOnUse">
            <line x1="0" y1="0" x2="0" y2="5" stroke="var(--viz-halo)" strokeWidth="2" opacity="0.75" />
          </pattern>
        </defs>

        {rings.map((r, ri) => {
          const r0 = R0 + ri * (band + gap);
          const r1 = r0 + band;
          return (
            <g key={r.version}>
              {r.slices.map((s) => {
                const dim = traced && traced !== s.skillId ? 0.14 : 1;
                const sel = traced === s.skillId;
                const ch = changeAt.get(`${r.version}|${s.skillId}`);
                const d = annulusPath(cx, cy, r0, r1, s.a0, s.a1);
                return (
                  <g key={s.skillId} opacity={dim}>
                    <path
                      d={d}
                      fill={color.get(s.skillId)}
                      fillOpacity={r.predicted ? 0.42 : s.origin === 'overlay' ? OVERLAY_ALPHA : BAND_ALPHA}
                      stroke={r.predicted ? 'var(--src-paper)' : 'var(--viz-halo)'}
                      strokeWidth={r.predicted ? 1.1 : 1.4}
                      strokeDasharray={r.predicted ? '4 3' : undefined}
                      onMouseEnter={(ev) => {
                        setHoverSkill(s.skillId);
                        showTip(ev, r, s);
                      }}
                      onMouseLeave={() => {
                        setHoverSkill(null);
                        setTip(null);
                      }}
                      onClick={() => onSelectSkill(selectedSkillId === s.skillId ? null : s.skillId)}
                    />
                    {s.origin === 'overlay' && !r.predicted && (
                      <path d={d} fill="url(#an-hatch)" pointerEvents="none" />
                    )}
                    {sel && <path d={d} fill="none" stroke="var(--ink)" strokeWidth={1.6} pointerEvents="none" />}
                    {ch && ch.op !== 'remove' && (
                      <line
                        {...radialLine(cx, cy, r0 - 3.5, r1 + 3.5, (s.a0 + s.a1) / 2)}
                        stroke={
                          ch.op === 'add' ? 'var(--green)' : ch.op === 'merge' ? 'var(--amber)' : 'var(--ink)'
                        }
                        strokeWidth={2}
                        strokeLinecap="round"
                        pointerEvents="none"
                      />
                    )}
                  </g>
                );
              })}
            </g>
          );
        })}

        {/* 能力名直接标在“今日环”上 ——
            原来图上没有一个字，认哪条带是什么全靠对照下方的图例色块，
            八个色块来回比对本身就比看图费劲。放得下就标，放不下的仍可悬停。 */}
        <g pointerEvents="none">
          {(() => {
            const r0 = R0 + labelRi * (band + gap);
            const rMid = r0 + band / 2;
            return rings[labelRi]?.slices.map((s) => {
              const arc = (s.a1 - s.a0) * rMid;
              if (arc < textWidth(s.name, BAND_FS) + 10) return null;
              const mid = (s.a0 + s.a1) / 2;
              const [x, y] = polar(cx, cy, rMid, mid);
              return (
                <text
                  key={s.skillId}
                  className="an-band"
                  x={x}
                  y={y}
                  fill={readableInk(color.get(s.skillId) ?? '#64748b', s.origin === 'overlay' ? OVERLAY_ALPHA : BAND_ALPHA)}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  opacity={traced && traced !== s.skillId ? 0.14 : 1}
                  transform={`rotate(${arcAngle(mid)} ${x} ${y})`}
                >
                  {s.name}
                </text>
              );
            });
          })()}
        </g>

        {/* 删除墓碑：色带断掉的那个位置 */}
        {data.changes
          .filter((c) => c.op === 'remove')
          .map((c) => {
            const ri = rings.findIndex((r) => r.version === c.version);
            if (ri <= 0) return null;
            const prev = rings[ri - 1].slices.find((s) => s.skillId === c.target.id);
            if (!prev) return null;
            const r0 = R0 + (ri - 1) * (band + gap);
            const [x, y] = polar(cx, cy, r0 + band / 2, (prev.a0 + prev.a1) / 2);
            return (
              <g key={c.id} pointerEvents="none">
                <circle cx={x} cy={y} r={6.5} fill="var(--viz-halo)" />
                <path
                  d={`M${x - 3.4},${y - 3.4} L${x + 3.4},${y + 3.4} M${x + 3.4},${y - 3.4} L${x - 3.4},${y + 3.4}`}
                  stroke="var(--red)"
                  strokeWidth={2}
                  strokeLinecap="round"
                />
              </g>
            );
          })}

        {/* 今日线。只在确有预测环时画：没有预测环时最外一圈就是最近一次观测，
            再画一道"今日线 · 外圈为预测"等于把观测说成推算。 */}
        {predIdx > 0 && (
        <circle
          cx={cx}
          cy={cy}
          r={todayR}
          fill="none"
          stroke="var(--ink)"
          strokeWidth={1.3}
          strokeDasharray="6 5"
          opacity={0.55}
        />
        )}
        {/* 版本刻度占了 12 点方向的右半边，说明文字往左让开。
            这行字正好落在最外那圈预测环的色带上，同样给它一片底片托着 */}
        {predIdx > 0 && (() => {
          const label = '今日线 · 外圈为预测';
          const tw = textWidth(label, 13) + 14;
          const ty = cy - todayR - 7;
          return (
            <g pointerEvents="none">
              <rect
                x={cx - 10 - tw}
                y={ty - 13}
                width={tw}
                height={19}
                rx={5}
                fill="var(--panel)"
                opacity={0.9}
                stroke="var(--line)"
                strokeWidth={0.8}
              />
              <text className="an-today" x={cx - 17} y={ty} textAnchor="end">
                {label}
              </text>
            </g>
          );
        })()}

        {/* 版本刻度与圆心。
            刻度原来是裸字压在色带上，只靠一圈白描边撑着，九个叠起来糊成一列；
            改成每个版本一片不透明的小底片，等于在 12 点方向立了一把尺。 */}
        <g pointerEvents="none">
          {rings.map((r, ri) => {
            const y = cy - (R0 + ri * (band + gap) + band / 2);
            const cw = r.version.length * 7.2 + 12;
            return (
              <g key={r.version}>
                <rect
                  x={cx + 3}
                  y={y - 9}
                  width={cw}
                  height={18}
                  rx={5}
                  fill="var(--panel)"
                  opacity={0.9}
                  stroke={r.predicted ? 'var(--src-paper)' : 'var(--line)'}
                  strokeWidth={r.predicted ? 1 : 0.8}
                  strokeDasharray={r.predicted ? '3 2.4' : undefined}
                />
                <text
                  className={r.predicted ? 'an-ver fore' : 'an-ver'}
                  x={cx + 3 + cw / 2}
                  y={y + 4}
                  textAnchor="middle"
                >
                  {r.version}
                </text>
              </g>
            );
          })}
          <circle cx={cx} cy={cy} r={R0 - 4} fill="var(--panel)" stroke="var(--line-strong)" />
          <text className="an-center-t" x={cx} y={cy - 4} textAnchor="middle">
            {data.jobName.length > 7 ? data.jobName.slice(0, 6) + '…' : data.jobName}
          </text>
          <text className="an-center-s" x={cx} y={cy + 15} textAnchor="middle">
            {rings.length} 个季度
          </text>
        </g>
      </svg>
      <Tooltip tip={tip} />
    </div>
  );
}

function radialLine(cx: number, cy: number, r0: number, r1: number, a: number) {
  const [x1, y1] = polar(cx, cy, r0, a);
  const [x2, y2] = polar(cx, cy, r1, a);
  return { x1, y1, x2, y2 };
}
