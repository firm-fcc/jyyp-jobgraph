/* ============================================================
   相近岗位对照 —— 空间关系图右侧的那一栏

   左边那张图给出的是方位：新簇落在哪一片、贴着哪个圈、距离读数是多少。
   方位答得了“有多近”，答不了“差在哪儿”。而“差在哪儿”才是这张图
   真正要支撑的那个判断：这个候选究竟应当并入最近的那个已有岗位，
   还是应当单独成条。

   把距离拆回任务这一层，判断就有了可以逐条核对的形态。拆法取并集
   而不是取集合差：最近的那个已有岗位往往与新岗位承担同一组任务，
   差别整个落在份额上，只列“各自独有哪几项”会得到两个零，等于什么也没说。
   逐项并排给出两侧份额，集合差退为标题下的三个计数，两种差别都留得住。

   序号与图中标注同源（同一个 near 数组的次序），因此左右两侧靠序号对上号，
   不必在一百多个点里按名字去找。
   ============================================================ */

import { useMemo } from 'react';
import type { JobSpacePoint, SpaceNeighbor, SpaceTaskItem } from '@/data/jobSpace';
interface Props {
  /** 选中的新岗位在岗位空间中的落点。为空表示当前筛选下无选中项 */
  point: JobSpacePoint | null;
  /** 选中项的显示名 */
  name?: string;
  /** 当前指到的相近岗位，与图上标注同步加重 */
  peerId: string | null;
  onPeerHover: (id: string | null) => void;
}

const pct1 = (v: number) => `${(v * 100).toFixed(v < 0.01 ? 2 : 1)}%`;
const pct0 = (v: number) => `${Math.round(v * 100)}%`;

/** 一项任务的两侧份额。横条按整栏共用的标尺定长，见 peak 的说明 */
function TaskRow({ t, peak }: { t: SpaceTaskItem; peak: number }) {
  /* 只有一侧承担时，另一侧画一道短横而不是一根长度为零的条：
     零长的条与“这一项没算出来”在图上分不开。 */
  const cell = (v: number, ref?: boolean) =>
    v > 0 ? (
      <>
        <span className="jsp-tt-bar">
          <i className={ref ? 'ref' : undefined} style={{ width: `${(v / peak) * 100}%` }} />
        </span>
        <b>{pct0(v)}</b>
      </>
    ) : (
      <>
        <span className="jsp-tt-bar" />
        <b className="nil">—</b>
      </>
    );

  const only = t.a > 0 && t.b === 0 ? ' new' : t.a === 0 ? ' ref' : '';
  return (
    <div className={`jsp-tt-row${only}`}>
      <span className="jsp-tt-n">{t.name}</span>
      <span className="jsp-tt-c">{cell(t.a)}</span>
      <span className="jsp-tt-c">{cell(t.b, true)}</span>
    </div>
  );
}

function PeerCard({
  rank,
  nb,
  peak,
  on,
  onHover,
}: {
  rank: number;
  nb: SpaceNeighbor;
  peak: number;
  on: boolean;
  onHover: (id: string | null) => void;
}) {
  return (
    <li
      className={on ? 'jsp-peer on' : 'jsp-peer'}
      onMouseEnter={() => onHover(nb.id)}
      onMouseLeave={() => onHover(null)}
    >
      <div className="jsp-peer-hd">
        <i className="jsp-peer-rk">{rank}</i>
        <b>{nb.name}</b>
        <em>
          <i>距离</i>
          {nb.dist.toFixed(3)}
        </em>
      </div>
      <div className="jsp-peer-m">
        {nb.cluster}
        <i>·</i>市场占比 {pct1(nb.share)}
      </div>
      <div className="jsp-peer-n">
        共有任务 {nb.nShared} 项<i>·</i>
        <b>仅新岗位 {nb.nOnlyNew} 项</b>
        <i>·</i>仅该岗位 {nb.nOnlyRef} 项
      </div>

      <div className="jsp-tt">
        <div className="jsp-tt-row hd">
          <span className="jsp-tt-n">任务构成</span>
          <span className="jsp-tt-c">新岗位</span>
          <span className="jsp-tt-c">该岗位</span>
        </div>
        {nb.tasks.map((t) => (
          <TaskRow key={t.id} t={t} peak={peak} />
        ))}
        {nb.nTasks > nb.tasks.length && (
          <p className="jsp-tt-more">另有 {nb.nTasks - nb.tasks.length} 项份额更低的任务未列出</p>
        )}
      </div>
    </li>
  );
}

export function JobSpacePeers({ point, name, peerId, onPeerHover }: Props) {
  /* 横条的标尺整栏共用，不按单张卡片定。新岗位那一列在三张卡片里是同一份数据，
     按卡片各自定标会让同一项任务在三处长短不一，读者会以为它变了。 */
  const peak = useMemo(() => {
    let m = 0;
    for (const nb of point?.near ?? [])
      for (const t of nb.tasks) m = Math.max(m, t.a, t.b);
    return m || 1;
  }, [point]);

  /* 用 || 而不是 ??：新岗位不带一级归属，三个字段都是空串而非 undefined，
     ?? 取不到后面的兜底。与左栏卡片、页头写同一句。 */
  const cluster = point
    ? point.job.cluster || point.job.topCategory || point.job.category || '尚未归入体系'
    : '';

  /* 这一栏与左图等高，内容超出的部分在 .jsp-peers-sc 内自行滚动，
     两栏底边因此对齐（见 jobs.css 同名一节）。 */
  return (
    <aside className="jsp-peers">
      <div className="jsp-peers-sc">
        {!point ? (
          <p className="jsp-peers-empty">在左侧列表或图中选取一个新岗位。</p>
        ) : (
          <>
            {/* 栏首先交代选中的是谁：右栏三张卡片全部相对这一个岗位而言，
                不写出来就只剩三个孤立的岗位名。名字下的虚线与图内标签同义：
                临时标签，不是规范岗位名。 */}
            <div className="jsp-self">
              <span className="jsp-self-k">当前选中</span>
              <b>{name ?? point.job.name}</b>
              {/* 逐项拼、分隔符只落在两项之间：叠层新岗位尚未归入岗位体系的任一
                  类别，cluster 为空，照原样铺出来这一行会以一个悬空的间隔点起首。

                  不列市场占比：这批岗位尚未进入招聘市场，加权出现量整批为零，
                  四个岗位一律显示 0.00% —— 一个恒定的零占着一格，读者却会把它
                  当成实测到的占比就是零。该岗位的量在上方相图里由信号强度给出。 */}
              <span className="jsp-self-m">
                {[cluster, `定义置信度 ${pct0(point.job.confidence)}`]
                  .filter(Boolean)
                  .map((t, i) => (
                    <span key={t}>
                      {i > 0 && <i>·</i>}
                      {t}
                    </span>
                  ))}
              </span>
            </div>

            {point.near.length === 0 ? (
              /* 空态分两种，措辞不能合并：
                 一种是这个岗位有任务构成、只是体系内没有可对照的；
                 另一种是它的任务构成连推也推不出来 —— 后者要说清楚
                 “不是没找到近的，是没有可比的东西”，否则读者会把空白读成
                 “它与体系内每个岗位都不像”，而那是一个本批数据给不出的结论。 */
              <div className="jsp-peers-empty">
                <p>体系内尚无可对照的规范岗位。</p>
              </div>
            ) : (
              <>
                <h3 className="jsp-peers-hd">相近岗位</h3>
                <ol className="jsp-peer-list">
                  {point.near.map((nb, i) => (
                    <PeerCard
                      key={nb.id}
                      rank={i + 1}
                      nb={nb}
                      peak={peak}
                      on={peerId === nb.id}
                      onHover={onPeerHover}
                    />
                  ))}
                </ol>
              </>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
