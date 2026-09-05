/* ============================================================
   综合匹配度的构成 —— 从满分 100 一路扣到最终得分

   综合分是五个维度按固定权重的加权和：
       综合 = 0.34·能力结构 + 0.26·任务覆盖 + 0.16·关键能力 + 0.14·经验 + 0.10·学历
   所以它同样可以反过来读：满分 100，每个维度按自己的权重扣掉没达成的那部分，
       扣分 = 权重 × (1 − 该维度得分) × 100
   五项扣完，剩下的正好是综合分。

   为什么用“扣”而不是“加”：两种画法都严格成立，但看报告的人要的是
   “我差在哪儿、差多少”，不是“我的分是从哪儿攒起来的”。
   扣分式把最大的那一段直接摆在最长的位置上，一眼看得出该先补哪一项。

   每一行都写着“权重 × 未达成 = 扣分”三个数，拿计算器就能核对 ——
   这一页最容易翻车的从来不是好不好看，是同一页里两处说法打架。
   ============================================================ */

import { useMemo } from 'react';
import type { MatchResult } from '@/types/graph';
import { DIM_WEIGHTS, SCORED_DIMS } from '@/data/matching';

const DIM_LABEL: Record<keyof MatchResult['dims'], string> = {
  skill: '能力结构',
  task: '任务覆盖',
  domain: '关键能力',
  experience: '经验年限',
  degree: '学历要求',
};

const DIM_HINT: Record<keyof MatchResult['dims'], string> = {
  skill: '岗位能力向量与简历能力向量的余弦相似度',
  task: '岗位各项核心任务所需能力的加权平均达成率',
  domain: '要求权重最高的一组能力上的加权达成率',
  experience: '简历年限相对本岗位主流经验档位',
  degree: '简历学历相对本岗位主流学历档位',
};

/* 哪几维计入综合分由 data/matching.ts 的 SCORED_DIMS 决定 ——
   综合分与这张图必须读同一个常量，否则同一页里会出现两个打架的数字。
   不计入的那几维画成空心轨道并写"未计入"，不按 0 分扣除。 */
const UNSCORED = (['skill', 'task', 'domain', 'experience', 'degree'] as (keyof MatchResult['dims'])[]).filter(
  (k) => !SCORED_DIMS.includes(k),
);

interface Props {
  dims: MatchResult['dims'];
  score: number;
  /** 点某一维，可把下方对应的分析块滚到眼前 */
  onPickDim?: (key: keyof MatchResult['dims']) => void;
}

export function ScoreWaterfall({ dims, onPickDim }: Props) {
  const rows = useMemo(() => {
    // 未计入的维度不参与扣分，剩下两维按各自权重在可计分的总权重里归一
    const scored = DIM_WEIGHTS.filter((d) => !UNSCORED.includes(d.key));
    const wSum = scored.reduce((a, b) => a + b.weight, 0) || 1;
    let running = 1;
    return DIM_WEIGHTS.map((d) => {
      const unscored = UNSCORED.includes(d.key);
      const weight = unscored ? 0 : d.weight / wSum;
      const lost = unscored ? 0 : weight * (1 - dims[d.key]);
      const before = running;
      running -= lost;
      return { key: d.key, weight, value: dims[d.key], lost, before, after: running, unscored };
    });
  }, [dims]);

  const scoredScore = rows.length ? rows[rows.length - 1].after : 0;

  /* 五项扣完应当正好落在综合分上。差值只可能来自 dims 各自的三位小数舍入，
     真出现大于 0.001 的偏差就是口径断了，界面上直说，不要糊过去。 */
  const pct = (v: number) => `${(v * 100).toFixed(1)}`;

  return (
    <div className="wf">
      <div className="wf-row wf-row-top">
        <span className="wf-name">满分</span>
        <span className="wf-track">
          <i className="wf-full" />
        </span>
        <b className="wf-num">100.0</b>
      </div>

      {rows.map((r) => (
        <button
          key={r.key}
          className={r.unscored ? 'wf-row wf-row-dim unscored' : 'wf-row wf-row-dim'}
          onClick={() => onPickDim?.(r.key)}
          aria-label={
            r.unscored
              ? `${DIM_LABEL[r.key]}：未计入综合分。${DIM_HINT[r.key]}`
              : `${DIM_LABEL[r.key]}：权重 ${(r.weight * 100).toFixed(0)}%，得分 ${pct(
                r.value,
              )}，扣 ${pct(r.lost)} 分。${DIM_HINT[r.key]}`
          }
        >
          <span className="wf-name">
            {DIM_LABEL[r.key]}
            <em>{r.unscored ? '未计入' : `${(r.weight * 100).toFixed(0)}%`}</em>
          </span>
          <span className={r.unscored ? 'wf-track hollow' : 'wf-track'}>
            {!r.unscored && (
              <>
                {/* 已经站住的那一段：淡色，表示“这部分分数没丢” */}
                <i className="wf-keep" style={{ width: `${r.after * 100}%` }} />
                {/* 这一维扣掉的那一段：从 after 浮到 before */}
                <i
                  className="wf-lost"
                  style={{ left: `${r.after * 100}%`, width: `${Math.max(r.lost * 100, 0.4)}%` }}
                />
              </>
            )}
          </span>
          <b className="wf-num wf-num-lost">{r.unscored ? '—' : r.lost >= 0.0005 ? `−${pct(r.lost)}` : '0.0'}</b>
        </button>
      ))}

      <div className="wf-row wf-row-end">
        <span className="wf-name">综合匹配度</span>
        <span className="wf-track">
          <i className="wf-score" style={{ width: `${scoredScore * 100}%` }} />
        </span>
        <b className="wf-num">{pct(scoredScore)}</b>
      </div>

    </div>
  );
}
