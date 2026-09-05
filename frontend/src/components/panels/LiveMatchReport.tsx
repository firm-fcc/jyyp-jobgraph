/* ============================================================
   匹配结论：达成率、差距明细与学习路径

   报告页只有这一套构件。此前它与一套五维加权的演示版并列存在，
   同一页在两种取数情形下长得不一样，读者无从判断哪一处是口径之别、
   哪一处是实现之别；现改为单一版式，两条链路填同一组视图模型。

   达成率的分母是“可计分的岗位要求”，分子是“已验证满足的要求”，非对称：
   简历多出来的能力不计分也不扣分。解析服务在线时逐项判定由服务端给出，
   载入内置示例简历时由 demoLive 按同一口径算出，两者填的是同一组视图模型。
   ============================================================ */

import { useState } from 'react';
import { Icon } from '@/components/Icon';
import {
  GAP_TEXT,
  levelLabel,
  LEVEL_TEXT,
  PATH_MODE_TEXT,
  requiredLabel,
  type GapCounts,
  type LivePath,
  type LiveSkillItem,
} from '@/data/matchLive';
import type { GapType, PathMode, ProficiencyLevel } from '@/api/matchApi';

/* ==================== 达成率与四态构成 ==================== */

const ORDER: GapType[] = ['SATISFIED', 'LEVEL_GAP', 'EVIDENCE_INSUFFICIENT', 'MISSING'];

const CLS: Record<GapType, string> = {
  SATISFIED: 'ok',
  LEVEL_GAP: 'gap',
  EVIDENCE_INSUFFICIENT: 'unk',
  MISSING: 'miss',
};

export function LiveScoreBar({ summary }: { summary: GapCounts }) {
  const counts: Record<GapType, number> = {
    SATISFIED: summary.satisfied,
    LEVEL_GAP: summary.level_gap,
    EVIDENCE_INSUFFICIENT: summary.evidence_insufficient,
    MISSING: summary.missing,
  };
  const total = Math.max(summary.required_skills, 1);

  return (
    <div className="lv-bar">
      <div className="lv-bar-track">
        {ORDER.map((k) =>
          counts[k] > 0 ? (
            <i
              key={k}
              className={`lv-seg lv-${CLS[k]}`}
              style={{ width: `${(counts[k] / total) * 100}%` }}
              title={`${GAP_TEXT[k]} ${counts[k]} 项`}
            />
          ) : null,
        )}
      </div>
      <ul className="lv-legend">
        {ORDER.map((k) => (
          <li key={k}>
            <i className={`lv-dot lv-${CLS[k]}`} />
            {GAP_TEXT[k]}
            <b>{counts[k]}</b>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ==================== 差距明细 ====================

   一行一项，横排四栏：能力、要求条、两侧档位、判定。

   此前每项占两到三行、右端一枚状态标签，十七项铺开近一屏半，而每一行真正
   传达的只有"差在哪一档"这一件事。改为账本式的一行制之后同样十七项收进半屏，
   且多出一列此前没有的信息 —— 这一项要求有多重。

   要求条画的是两件事，两截同出于一批条目，可直接相加：

     深色截  提到这项能力、且写明了要到什么熟练度的招聘信息占比
     浅色截  提到了、但没写明熟练度的那一部分

   两截合起来即"该岗位有多普遍要求这项能力"（jd_presence_rate）。深色内条是
   简历够到的那一段，按服务端的四态判定折算（见 matchLive 的 attainOf）。
   条长因而可比、可排序：条长而内条短的，就是缺得最要紧的几项。

   展开一项，给出该项熟练度要求的分档条数 —— 报告里说"要求熟练"，凭据即在此。

   截图里那版账本另有"直接点名／经任务传导"一栏。那是图谱侧岗位—能力边的
   两条路径，与此处逐条数出来的招聘信息计数不同源，分母不一致，并列会让人
   以为两者可以相加，故不取。 */

const LEVEL_ORDER: ProficiencyLevel[] = ['P1', 'P2', 'P3', 'P4'];

function GapRow({
  item,
  open,
  onToggle,
  onPickEvidence,
}: {
  item: LiveSkillItem;
  open: boolean;
  onToggle: () => void;
  onPickEvidence?: (item: LiveSkillItem) => void;
}) {
  const ev = item.evidence[0];
  const st = item.stats;
  /* 内条按要求条的长度折算，故两者同一标尺：内条到不了条尾即为缺口 */
  const own = item.demand * item.attain;
  return (
    <div className={`lg2-item lg2-${CLS[item.gap]}${open ? ' open' : ''}`}>
      <button className="lg2-row" aria-expanded={open} onClick={onToggle}>
        <span className="lg2-name">
          <i className="lg2-caret" aria-hidden="true">
            {open ? '▾' : '▸'}
          </i>
          <b title={item.definition}>{item.name}</b>
          <em className="lg2-kind">{item.hard ? '硬技能' : '软技能'}</em>
        </span>

        <span className="lg2-bar" aria-hidden="true">
          <i className="lg2-seg lg2-seg-soft" style={{ width: `${item.demand * 100}%` }} />
          <i className="lg2-seg lg2-seg-hard" style={{ width: `${item.demandGraded * 100}%` }} />
          <i className={`lg2-own lg2-own-${CLS[item.gap]}`} style={{ width: `${own * 100}%` }} />
        </span>

        <span className="lg2-lv">
          {item.requiredLevel ? shortLevel(item.requiredLevel) : <em>未设等级</em>}
        </span>
        <span className="lg2-lv lg2-lv-mine">
          {item.candidateLevel ? (
            shortLevel(item.candidateLevel)
          ) : item.gap === 'SATISFIED' ? (
            <em>有证据</em>
          ) : (
            <em>无</em>
          )}
        </span>
        <span className={`lg2-st lg2-st-${CLS[item.gap]}`}>{GAP_TEXT[item.gap]}</span>
      </button>

      {/* 展开层分两栏，各带一句抬头：左边说这条要求从何而来，右边说简历里凭什么
          判成这样。此前四段平铺 —— 统计一句、分档一行、口径一句、原文一句 ——
          既看不出哪一段说的是岗位、哪一段说的是简历，末尾那句引文更是没有出处，
          读者不知道它为什么出现在这里。 */}
      {open && (
        <div className="lg2-detail">
          {st && (
            <section className="lg2-d-col">
              <h5>岗位一侧 · 这条要求从何而来</h5>
              <p className="lg2-d-h">
                本岗位 {st.jdCount.toLocaleString()} 条招聘信息中，
                <b>{st.presenceCount.toLocaleString()}</b> 条要求这项能力
                <span>（{Math.round(st.presenceRate * 100)}%）</span>；
                其中 <b>{st.gradedCount.toLocaleString()}</b> 条写明了要到什么熟练度
              </p>
              {st.gradedCount > 0 ? (
                <>
                  <ul className="lg2-dist">
                    {LEVEL_ORDER.map((lv) => {
                      const n = st.levelDistribution[lv] ?? 0;
                      return (
                        <li key={lv} className={lv === item.requiredLevel ? 'on' : ''}>
                          <span className="lg2-dist-n">{LEVEL_TEXT[lv]}</span>
                          <span className="lg2-dist-bar">
                            <i style={{ width: `${(n / Math.max(st.gradedCount, 1)) * 100}%` }} />
                          </span>
                          <span className="lg2-dist-v">{n}</span>
                        </li>
                      );
                    })}
                  </ul>
                  <p className="lg2-d-rule">
                    取其中位数，故该岗位对这项能力的要求记作
                    <b>{item.requiredLevel ? shortLevel(item.requiredLevel) : '未设等级'}</b>
                  </p>
                </>
              ) : (
                <p className="lg2-d-rule">
                  写明熟练度的条目不过半，故只记要求存在、<b>不设等级</b>，不代为拟一个档位
                </p>
              )}
            </section>
          )}

          <section className="lg2-d-col">
            <h5>简历一侧 · 判定所依据的原文</h5>
            {ev ? (
              <>
                <blockquote className="lg2-ev">{ev.text}</blockquote>
                <p className="lg2-ev-tail">
                  <span>
                    {item.evidence.length > 1
                      ? `简历中另有 ${item.evidence.length - 1} 段落在这一项上`
                      : '简历中仅此一段落在这一项上'}
                  </span>
                  {onPickEvidence && (
                    <button
                      type="button"
                      className="lg2-loc"
                      onClick={(e) => {
                        e.stopPropagation();
                        onPickEvidence(item);
                      }}
                    >
                      <Icon name="target" size={11} />
                      在左栏逐处标出
                    </button>
                  )}
                </p>
              </>
            ) : (
              <p className="lg2-ev-none">
                简历中未发现支撑这项能力的行为证据。技能清单中列名、或课程与证书中提及，
                均不足以构成支撑。
              </p>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

/* 等级只取中文一档。P1–P4 的编号系后端口径，界面不予呈现，见 LEVEL_TEXT */
const shortLevel = (v: ProficiencyLevel) => LEVEL_TEXT[v];

export function LiveGapLedger({
  items,
  onPickEvidence,
}: {
  items: LiveSkillItem[];
  onPickEvidence?: (item: LiveSkillItem) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  /* items 已按差距严重程度排序，此处只作分组，组内次序不动 */
  const unmet = items.filter((it) => it.gap !== 'SATISFIED');
  const met = items.filter((it) => it.gap === 'SATISFIED');

  const group = (list: LiveSkillItem[]) =>
    list.map((it) => (
      <GapRow
        key={it.teamSkillId}
        item={it}
        open={open === it.teamSkillId}
        onToggle={() => setOpen(open === it.teamSkillId ? null : it.teamSkillId)}
        onPickEvidence={onPickEvidence}
      />
    ));

  return (
    <div className="lg2">
      <div className="lg2-head">
        <span>能力</span>
        <span className="lg2-head-bar">
          岗位要求
          <i className="lg2-key lg2-key-hard" />
          写明了熟练度
          <i className="lg2-key lg2-key-soft" />
          只提到名称
          <i className="lg2-key lg2-key-own" />
          简历够到
        </span>
        <span className="lg2-lv">要求</span>
        <span className="lg2-lv">简历</span>
        <span className="lg2-st">判定</span>
      </div>

      {unmet.length > 0 && (
        <>
          <h4 className="lg2-grp">
            尚未满足
            <b>{unmet.length}</b>
          </h4>
          {group(unmet)}
        </>
      )}

      {met.length > 0 && (
        <>
          <h4 className="lg2-grp">
            已满足
            <b>{met.length}</b>
          </h4>
          {group(met)}
        </>
      )}

      {/* 此处原有一行"未计入达成率的 N 项"，把六项辅助能力（团队协作、职业诚信
          一类）连名带由列出来。它们本就不参与评级计分，列出来既不构成缺口，
          也无从行动，占的却是整块表格的收尾位置。 */}
    </div>
  );
}

/* ==================== 学习路径 ====================

   一份实测报告常带十余项待补的能力，逐项铺开步骤、判据、综合任务与重评说明，
   整节要占五六屏，而每一项真正回答的只有"先做什么、做到什么程度算数"两件事。

   改为按能力折叠：收起时一行给出能力名、行动方式、目标档位与步骤序列，
   序列即路径本身，无须展开也能判断这一项要走几步、走的是哪几步；
   展开一项才给出该步的任务与判据，以及该项的综合验证任务。默认展开首项。

   三处此前逐项重复的文字不再重出：当前状态一句由行动方式的标签表达，
   步骤编号一类的内部标识不上界面，重新评估的口径整节只在末尾说一次。

   尚无发展图谱的那些能力不在本节出现。缺口已在上一节逐项列明，
   在此另开一块列一遍，读者既无从据以行动，也会把它误读为路径的一部分。 */

const MODE_CLS: Record<PathMode, string> = {
  LEARN: 'learn',
  DEEPEN: 'deepen',
  VERIFY_FIRST: 'verify',
  NONE: 'none',
};

function PathCriteria({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="lp-crit">
      {items.map((c) => (
        <li key={c}>{c}</li>
      ))}
    </ul>
  );
}

export function LivePathPlan({ path }: { path: LivePath }) {
  const items = path.ready;
  const [open, setOpen] = useState<string | null>(items[0]?.teamSkillId ?? null);

  if (items.length === 0) {
    return <p className="lp-none">该岗位的能力要求均已满足，无需安排学习路径。</p>;
  }

  return (
    <div className="lp">
      <ol className="lp-list">
        {items.map((p, i) => {
          const on = open === p.teamSkillId;
          return (
            <li
              key={p.teamSkillId}
              className={`lp-item lp-m-${MODE_CLS[p.pathMode]}${on ? ' open' : ''}`}
            >
              <button
                type="button"
                className="lp-row"
                aria-expanded={on}
                onClick={() => setOpen(on ? null : p.teamSkillId)}
              >
                <span className="lp-n">{String(i + 1).padStart(2, '0')}</span>

                <span className="lp-main">
                  <span className="lp-hd">
                    <b>{p.name}</b>
                    <em className="lp-mode">{PATH_MODE_TEXT[p.pathMode]}</em>
                  </span>
                  {/* 收起态的路径本身：一串按序相连的步骤名 */}
                  <span className="lp-track">
                    {p.steps.length > 0 ? (
                      p.steps.map((s) => <i key={s.nodeId}>{s.nodeName}</i>)
                    ) : (
                      <i>{p.verification?.name ?? '验证当前能力证据'}</i>
                    )}
                  </span>
                </span>

                <span className="lp-lv">
                  <b>{requiredLabel(p.requiredLevel)}</b>
                  <small>{p.observedLevel ? `现 ${levelLabel(p.observedLevel)}` : '现无定级'}</small>
                </span>

                <i className="lp-caret" aria-hidden="true" />
              </button>

              {on && (
                <div className="lp-detail">
                  {/* 先行验证：能力已具备而证据不足以定级，给的是一项验证任务而非分步路径 */}
                  {p.verification ? (
                    <section className="lp-task">
                      <h5>{p.verification.name}</h5>
                      <p>{p.verification.description}</p>
                      <PathCriteria items={p.verification.criteria} />
                    </section>
                  ) : (
                    <ol className="lp-steps">
                      {p.steps.map((s, k) => (
                        <li key={s.nodeId}>
                          <span className="lp-step-n">{k + 1}</span>
                          <div>
                            <b>{s.nodeName}</b>
                            <p>{s.evidenceTask}</p>
                            <PathCriteria items={s.criteria} />
                          </div>
                        </li>
                      ))}
                    </ol>
                  )}

                  {p.capstone && (
                    <section className="lp-task lp-task-cap">
                      <h5>综合验证 · {p.capstone.objective}</h5>
                      <p>{p.capstone.description}</p>
                      <PathCriteria items={p.capstone.criteria} />
                    </section>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ol>

      <p className="lp-foot">
        步骤产出的是行为证据；熟练度由后续评估重新判定，不随步骤完成自动升级。
      </p>
    </div>
  );
}
