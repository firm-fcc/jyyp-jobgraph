/* ============================================================
   能力账本

   岗位对一项能力的要求由两条路径合成：招聘信息里直接点名（岗位 → 能力），
   以及经由核心任务传导过来（岗位 → 任务 → 能力）。账本把要求条水平劈成
   这两段，展开还能看到是哪几项任务在要求它 —— 直接回答“为什么要这项能力”。
   我的水平以深色内条叠在同一条上，一眼看出差多少。

   用账本而不是雷达图：雷达图的面积会随维度顺序变形，也没法排序、没法点开，
   而差距分析恰恰要求“展示清晰、易于理解”。
   ============================================================ */

import { useState } from 'react';
import type { MatchItem } from '@/types/graph';

const BAND_LABEL = { have: '已具备', improve: '需提升', missing: '缺失' } as const;

export function CapabilityLedger({ items, onPick }: { items: MatchItem[]; onPick?: (i: MatchItem) => void }) {
  const [open, setOpen] = useState<string | null>(null);

  return (
    <div className="ledger">
      <div className="lg-head">
        <span>能力</span>
        <span className="lg-head-bar">
          岗位要求
          <i className="lg-key lg-key-direct" />
          直接点名
          <i className="lg-key lg-key-task" />
          经任务传导
          <i className="lg-key lg-key-own" />
          简历水平
        </span>
        <span className="lg-num">要求</span>
        <span className="lg-num">简历</span>
        <span className="lg-num">差距</span>
        <span className="lg-st">状态</span>
      </div>

      {items.map((it) => {
        const isOpen = open === it.skillId;
        const total = Math.max(it.required, 1e-6);
        return (
          <div key={it.skillId} className={isOpen ? 'lg-item open' : 'lg-item'}>
            <button
              className="lg-row"
              aria-expanded={isOpen}
              onClick={() => {
                setOpen(isOpen ? null : it.skillId);
                onPick?.(it);
              }}
            >
              <span className="lg-name">
                <i className="lg-caret">{isOpen ? '▾' : '▸'}</i>
                <b>{it.name}</b>
                {it.forwardLooking && <em className="lg-fore">前瞻</em>}
              </span>

              <span className="lg-bar">
                <i className="lg-seg lg-seg-direct" style={{ width: `${it.directPart * 100}%` }} />
                <i className="lg-seg lg-seg-task" style={{ left: `${it.directPart * 100}%`, width: `${it.viaTaskPart * 100}%` }} />
                <i className="lg-own" style={{ width: `${Math.min(it.owned, 1) * 100}%` }} />
              </span>

              {/* 证据列整列撤下：它画的是三源证据构成，而本批证据是生成的。
                  留着一列恒为演示数据的指纹，比不画更容易被当成结论。 */}
              <span className="lg-num">{it.required.toFixed(2)}</span>
              <span className="lg-num">{it.owned.toFixed(2)}</span>
              <span className={it.gap > 0.05 ? 'lg-num lg-gap-down' : 'lg-num lg-gap-ok'}>
                {it.gap > 0 ? '−' : '+'}
                {Math.abs(it.gap).toFixed(2)}
              </span>
              <span className={`lg-st lg-st-${it.band}`}>{BAND_LABEL[it.band]}</span>
            </button>

            {isOpen && (
              <div className="lg-detail">
                <p>
                  该项要求中，<b>{Math.round((it.directPart / total) * 100)}%</b> 来自招聘信息直接点名
                  （{it.directPart.toFixed(3)}），
                  <b>{Math.round((it.viaTaskPart / total) * 100)}%</b> 由以下核心任务传导
                  （{it.viaTaskPart.toFixed(3)}）。
                  {it.forwardLooking && '其中含前瞻信号追加项：招聘市场尚未普遍写入要求，论文与行业新闻强度已连续走高。'}
                </p>
                {it.viaTasks.length === 0 ? (
                  <p className="lg-detail-empty">该能力仅由招聘信息直接列出，无任务传导路径。</p>
                ) : (
                  <ul className="lg-tasks">
                    {it.viaTasks.map((t) => (
                      <li key={t.taskId}>
                        <span className="lg-task-n">{t.taskName}</span>
                        <span className="lg-task-bar">
                          <i style={{ width: `${(t.part / Math.max(it.viaTaskPart, 1e-6)) * 100}%` }} />
                        </span>
                        <span className="lg-num">{t.part.toFixed(3)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
