/* ============================================================
   数据窗口标

   顶栏与封面共用的一枚标，回答两个问题：这套界面上的数都取自哪一段时间，
   以及其中还有哪几维不是实测的。

   前者是读全站任何一张图的前提 —— 各页的量（市场占比、招聘条数、薪资档、
   要求强度、逐月序列）同出于一批招聘信息，该批次覆盖的自然月即本标所示区间，
   界面上不存在晚于该区间的"当期"数据。

   后者原先由顶栏的静态"演示数据"三字承担。图谱产物接入后，四层结构、四类边、
   月度序列与前瞻证据均已是实测，全站一律标演示会把口径说反；仍由前端补齐
   的那几维逐条列出。本轮结清企业类别、岗位定义三要素与领先月数三项之后，
   清单只剩证据原文全文与人岗匹配页的示例简历两条，各页图上不再挂演示数据标，
   需推导一层的维度改在该图自己的问号里写明推法与覆盖率。
   ============================================================ */

import { useEffect, useRef, useState } from 'react';
import { REAL_GRAPH_STATS } from '@/data/realGraph';
import { ABSENT_DIMENSIONS } from '@/data/provenance';
import { IS_REAL_GRAPH } from '@/data/dataSource';

/** 2022-05 → 2022 年 5 月 */
const cn = (m: string) => `${m.slice(0, 4)} 年 ${Number(m.slice(5, 7))} 月`;

export function DataWindowBadge() {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  /* 演示词表与对照档下全站皆为生成值，此时仍以原先的静态标示人 */
  if (!IS_REAL_GRAPH) return <span className="demo-badge">演示数据</span>;

  const { from, to, windows, spanMonths, gapMonths, jdSampled } = REAL_GRAPH_STATS;

  return (
    <span className="dwin-wrap" ref={box}>
      <button
        type="button"
        className={open ? 'dwin open' : 'dwin'}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="dwin-k">数据窗口</span>
        <span className="dwin-v">
          {from} — {to}
        </span>
      </button>
      {open && (
        <span className="htip-pop end dwin-pop" role="note">
          <b>全站各图同出于一批数据</b>
          <p>
            {cn(from)}至{cn(to)}共 {spanMonths} 个自然月，其中 {windows} 个月各有一次观测
            {gapMonths > 0 && `，余下 ${gapMonths} 个月无独立观测`}，合计{' '}
            {jdSampled.toLocaleString()} 条招聘信息，及同期的学术论文与行业新闻。
            四层结构、四类关系、逐月序列与前瞻证据均取自这一批次，
            界面上不存在晚于该区间的数据；“当前”一词在全站一律指该批次的末窗（{to}）。
          </p>
          {ABSENT_DIMENSIONS.length > 0 && (
            <>
              <b>仍由前端补齐的维度</b>
              <ul>
                {ABSENT_DIMENSIONS.map((a) => (
                  <li key={a.name}>
                    <em>{a.name}</em>
                    {a.why}
                  </li>
                ))}
              </ul>
              <p className="dwin-foot">
                除上列两项外，界面上的各维均有实测或可回源的推导来源，逐图的口径收在各图的问号里。
              </p>
            </>
          )}
        </span>
      )}
    </span>
  );
}
