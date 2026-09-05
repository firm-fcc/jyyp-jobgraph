/* ============================================================
   Post Detail View —— JobViz 论文 Figure 2(C) 的复刻

   一张表。只选了一个落点时是"字段名 | 值"两列；左右各选一个时，字段名挪到
   中间一列，两个值分列两侧 —— 论文用的就是这种把字段名夹在中间的对照排法，
   两列值各自贴着中缝，逐行比过去不用来回找对应关系。

   成段的长文（岗位定义、核心职责、必备技能……）默认折起来显示"（点击展开）"，
   点一下同时展开两列的同一行 —— 与论文的 changeContent 一致：
   对照的前提是两边处在同一状态，只展开一边等于没在对照。

   这一栏只从簇内岗位分布那一层进入：聚类那一屏上的字形是一整簇，不是一个岗位，
   点它是下钻而不是选中。空态因此分两句写 —— 停在聚类屏时先说"先点开一簇"，
   已经进到分布屏才说怎么选。写成同一句"在岗位分布图里单击字形"，
   会让停在聚类屏的人对着一屏找不到的东西反复点。

   两列的填法：论文 Case Study 里是左右键各送一列。这里左键补一条轮转
   （空列优先，两列都占着则顶掉先来的那一个），右键仍直接指定右列 ——
   把"能不能同时看两个"这件事从"知不知道要按右键"里解绑出来。
   ============================================================ */

import { useMemo, useState } from 'react';
import type { DetailRecord } from '@/data/jobviz';
import { HelpTip } from '@/components/common/HelpTip';
import { AugmentedRadar } from './AugmentedRadar';

/* 一张表里并置着两段口径：以"本格"起头的三行说的是所选的那一个落点，
   其余各行说的是该岗位在本窗口内的全部招聘信息。同一个岗位在图上通常
   占着若干落点，故"本格学历门槛"与"主要学历"取值不同是常态。 */
const SCOPE_NOTE = (
  <>
    以“本格”起头的三行是所选落点这一格的读数。一个岗位在图上按其学历分布占据
    若干格，点中哪一格，这三行即为哪一格。其余各行取该岗位全部招聘信息的口径，
    既不随所选的格改变，也不随筛选条件改变。故同一岗位的“本格学历门槛”与
    “主要学历”常不相同：前者是所点的那一列，后者是占比最高的那一档。
  </>
);

interface Props {
  left: DetailRecord | null;
  right: DetailRecord | null;
  domain: number;
  /** 上方那块图当前停在哪一屏 */
  stage: 'cluster' | 'map';
  onClear: (side: 'left' | 'right') => void;
}

export function PostDetailView({ left, right, domain, stage, onClear }: Props) {
  const [openLong, setOpenLong] = useState<Set<string>>(new Set());

  const cols = useMemo(
    () => [
      { side: 'left' as const, rec: left },
      { side: 'right' as const, rec: right },
    ],
    [left, right],
  );
  const shown = cols.filter((c) => c.rec);
  const two = shown.length === 2;
  const labels = (left ?? right)?.rows.map((r) => ({ label: r.label, long: !!r.long })) ?? [];

  if (!left && !right) {
    return (
      <div className="pdv-empty">
        <b>尚未选中岗位</b>
        {stage === 'cluster' ? (
          <p>
            {'上方当前为岗位聚类视图，图上每个字形代表一整簇而非单个岗位。' +
              '先单击任一簇进入簇内岗位分布，再在分布图中选取具体岗位。'}
          </p>
        ) : (
          <p>
            {'在上方分布图中单击一个字形，其详情落入左列；再单击一个落入右列。' +
              '两列同时有内容时，字段名移到中间，逐行左右对照。'}
          </p>
        )}
      </div>
    );
  }

  const toggle = (label: string) =>
    setOpenLong((s) => {
      const next = new Set(s);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });

  const cell = (rec: DetailRecord | null, label: string, long: boolean) => {
    if (!rec) return <div className="pdv-cell muted">—</div>;
    const row = rec.rows.find((r) => r.label === label);
    if (!row) return <div className="pdv-cell muted">—</div>;
    if (long && !openLong.has(label)) return <div className="pdv-cell fold">（点击展开）</div>;
    return <div className={long ? 'pdv-cell long' : 'pdv-cell'}>{row.value}</div>;
  };

  return (
    <div className={two ? 'pdv two' : 'pdv'}>
      <div className="pdv-head">
        {two && (
          <ColHead
            rec={left!}
            domain={domain}
            onClear={() => onClear('left')}
            side="左"
          />
        )}
        {!two && (
          <span className="pdv-mid">
            <HelpTip text={SCOPE_NOTE} />
          </span>
        )}
        {two && (
          <span className="pdv-mid">
            <HelpTip text={SCOPE_NOTE} trigger="字段" />
          </span>
        )}
        <ColHead
          rec={(two ? right : (left ?? right))!}
          domain={domain}
          onClear={() => onClear(two || !left ? 'right' : 'left')}
          side={two ? '右' : left ? '左' : '右'}
        />
      </div>

      <div className="pdv-body">
        {labels.map((r) => (
          <div
            key={r.label}
            className={`pdv-row${r.long ? ' long' : ''}${r.long && openLong.has(r.label) ? ' open' : ''}`}
            onClick={r.long ? () => toggle(r.label) : undefined}
          >
            {two && cell(left, r.label, r.long)}
            <div className="pdv-lb">{r.label}</div>
            {cell(two ? right : (left ?? right), r.label, r.long)}
          </div>
        ))}
      </div>
    </div>
  );
}

function ColHead({
  rec,
  domain,
  onClear,
  side,
}: {
  rec: DetailRecord;
  domain: number;
  onClear: () => void;
  side: string;
}) {
  return (
    <div className="pdv-col">
      <svg width={40} height={40} viewBox="0 0 40 40" aria-hidden="true" className="pdv-glyph">
        <AugmentedRadar
          cx={20}
          cy={20}
          r={16}
          mean={rec.vector}
          domain={domain}
          color={rec.color}
          filled
          spokes
          points={false}
        />
      </svg>
      <div className="pdv-col-text">
        <b>{rec.title}</b>
        <span>{side}列</span>
      </div>
      <button className="pdv-x" onClick={onClear} aria-label={`移出 ${rec.title}`}>
        ×
      </button>
    </div>
  );
}
