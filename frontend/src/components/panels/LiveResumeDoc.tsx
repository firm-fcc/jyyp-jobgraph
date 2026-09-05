/* ============================================================
   报告左栏：简历原文

   接口除逐条证据外另返回解析后的简历全文（candidate.resume_text），
   而每条证据都带 [start, end) 两个偏移，锚的正是这份全文。因此本栏渲染的是
   原文本身，被引用的句子就地加底色 —— 右栏说"这一项已具备"，左栏当场指出
   它出自原文哪一句，两边对读不必再在两份文本之间来回找。

   底色分两类，含义不同：
     实心底色  该句是判定某项能力的行为证据
     虚线下划  该处出自技能清单的列名，只是写了个名字，不构成对能力的支撑

   右栏点某一项能力时，只有支撑它的那些句子保留底色，其余压暗，并滚到第一处。

   服务端取不到文字（扫描件、旧版服务）时无全文可标，退回按经历分组的证据清单。
   ============================================================ */

import { useEffect, useMemo, useRef, type ReactNode } from 'react';
import { Icon } from '@/components/Icon';
import type { ExplicitSkillMention } from '@/api/matchApi';
import type { LiveCandidateSummary, LiveEvidence } from '@/data/matchLive';

/** 原文切分后的一段。skills 非空即为证据段，mention 为技能清单里的列名，
    pick 为右栏某一项核验或某段经历指来的落点 */
interface Seg {
  text: string;
  skills: string[];
  mention: boolean;
  pick: boolean;
}

/**
 * 把全文按证据与列名的偏移切成互不重叠的段。
 *
 * 同一句常被多项能力同时引用，区间彼此重叠，故不能逐条套用 —— 先取所有端点作切分点，
 * 再逐段回查覆盖它的区间，重叠处因而合并成一段并带上全部引用方。
 */
function segment(
  text: string,
  spans: { start: number; end: number; skills: string[]; mention: boolean; pick?: boolean }[],
): Seg[] {
  const valid = spans.filter((s) => s.start >= 0 && s.end > s.start && s.start < text.length);
  if (valid.length === 0) return [{ text, skills: [], mention: false, pick: false }];

  const cuts = new Set<number>([0, text.length]);
  for (const s of valid) {
    cuts.add(Math.max(0, s.start));
    cuts.add(Math.min(text.length, s.end));
  }
  const points = [...cuts].sort((a, b) => a - b);

  const out: Seg[] = [];
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i];
    const b = points[i + 1];
    if (b <= a) continue;
    const cover = valid.filter((s) => s.start <= a && s.end >= b);
    const skills: string[] = [];
    let mention = false;
    let pick = false;
    for (const s of cover) {
      if (s.mention) mention = true;
      if (s.pick) pick = true;
      for (const k of s.skills) if (!skills.includes(k)) skills.push(k);
    }
    const seg: Seg = { text: text.slice(a, b), skills, mention, pick };
    /* 相邻两段标注相同时并回一段，免得原文被切成一片碎块 */
    const prev = out[out.length - 1];
    if (
      prev &&
      prev.mention === seg.mention &&
      prev.pick === seg.pick &&
      prev.skills.length === seg.skills.length &&
      prev.skills.every((k, j) => k === seg.skills[j])
    ) {
      prev.text += seg.text;
    } else {
      out.push(seg);
    }
  }
  return out;
}

export function LiveResumeDoc({
  fileName,
  summary,
  resumeText,
  evidence,
  mentions,
  focusSkill,
  focusSpans,
  onClearFocus,
  onBack,
  picker,
}: {
  fileName: string | null;
  summary: LiveCandidateSummary | null;
  /** 解析后的简历全文。为空时退回证据清单 */
  resumeText: string;
  evidence: LiveEvidence[];
  /** 简历技能清单里列到的技术名。列名本身不构成对能力的支撑 */
  mentions: ExplicitSkillMention[];
  /** 右栏点了某项能力时，只高亮支撑它的那些片段 */
  focusSkill: string | null;
  /** 右栏点了某项核验或某段经历时，按字符偏移就地定位。与 focusSkill 互斥 */
  focusSpans: { label: string; spans: [number, number][] } | null;
  onClearFocus: () => void;
  /** 回到上传屏。本栏的主语是"读的是哪一份简历"，换一份的入口因而落在这里 */
  onBack: () => void;
  /** 载入的是内置示例简历时，切换用的选择器；上传件没有可切换的对象 */
  picker?: ReactNode;
}) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const firstHit = useRef<HTMLElement | null>(null);

  const hasText = resumeText.trim().length > 0;

  const segs = useMemo(() => {
    if (!hasText) return [];
    const spans = [
      ...evidence
        .filter((e) => e.start !== null && e.end !== null)
        .map((e) => ({ start: e.start as number, end: e.end as number, skills: e.skills, mention: false })),
      ...mentions.map((m) => ({ start: m.start, end: m.end, skills: [] as string[], mention: true })),
      ...(focusSpans?.spans ?? []).map(([a, b]) => ({
        start: a,
        end: b,
        skills: [] as string[],
        mention: false,
        pick: true,
      })),
    ];
    return segment(resumeText, spans);
  }, [hasText, resumeText, evidence, mentions, focusSpans]);

  /** 落在原文上的证据条数。偏移缺失的那些无处标注，另行计数 */
  const located = useMemo(
    () => evidence.filter((e) => e.start !== null && e.end !== null).length,
    [evidence],
  );
  const hitCount = useMemo(
    () => (focusSkill ? evidence.filter((e) => e.skills.includes(focusSkill)).length : 0),
    [evidence, focusSkill],
  );

  /* 换了聚焦项就滚到第一处。只滚栏内，不动整页 */
  useEffect(() => {
    if (!focusSkill && !focusSpans) return;
    const el = firstHit.current;
    const box = bodyRef.current;
    if (!el || !box) return;
    box.scrollTo({ top: Math.max(0, el.offsetTop - box.clientHeight / 3), behavior: 'smooth' });
  }, [focusSkill, focusSpans, segs]);

  /** 退回形态：按经历分组的证据清单 */
  const groups = useMemo(() => {
    const bag = new Map<string, LiveEvidence[]>();
    for (const e of evidence) bag.set(e.sourceId, [...(bag.get(e.sourceId) ?? []), e]);
    return [...bag.entries()].map(([sourceId, items]) => ({ sourceId, items }));
  }, [evidence]);

  let seen = false;

  return (
    <aside className="rp-doc rp-doc-live">
      <header className="rp-doc-hd">
        <button type="button" className="rp-doc-back" onClick={onBack}>
          <Icon name="chevronL" size={13} />
          返回上传
        </button>
        <div className="rp-doc-t">
          <b>简历原文</b>
          <small>{fileName ?? '已上传简历'}</small>
        </div>
        {picker}
      </header>

      {summary && (
        <div className="lv-doc-sum">
          <span>
            已具备 <b>{summary.supported}</b>
          </span>
          <span>
            证据不足 <b>{summary.partial}</b>
          </span>
          <span>
            标注 <b>{located}</b>
          </span>
          {summary.avgConfidence !== null && (
            <span>
              置信度 <b>{(summary.avgConfidence * 100).toFixed(0)}%</b>
            </span>
          )}
        </div>
      )}

      {/* 正在定位哪一项、命中几处。此前这里叠了三层底：外层一条浅灰、里面一枚
          浅蓝方块只包住文字、右端再一枚白底描边按钮，三段底色各自为界。
          现在整条一色，左侧一句话，右端一枚同色系的退出。 */}
      {(focusSkill || focusSpans) && (
        <div className="lv-locbar">
          <Icon name="target" size={14} />
          <p className="lv-locbar-t">
            正在定位 <b>{focusSkill ?? focusSpans?.label}</b>
            <span>共 {focusSkill ? hitCount : (focusSpans?.spans.length ?? 0)} 处</span>
          </p>
          <button className="lv-locbar-quit" onClick={onClearFocus}>
            <Icon name="close" size={11} />
            退出定位
          </button>
        </div>
      )}

      <div className={focusSkill || focusSpans ? 'rp-doc-bd focusing' : 'rp-doc-bd'} ref={bodyRef}>
        {hasText ? (
          <pre className="lv-doc-text">
            {segs.map((s, i) => {
              /* 按偏移指来的落点压在最上层：此刻要看的是"这一条判据落在哪一句"，
                 该句同时是不是某项能力的证据，暂居其次 */
              if (s.pick) {
                const isFirst = !seen;
                if (isFirst) seen = true;
                return (
                  <mark
                    key={i}
                    ref={isFirst ? (el) => { firstHit.current = el; } : undefined}
                    className="lv-mk pick"
                    title={focusSpans?.label}
                  >
                    {s.text}
                  </mark>
                );
              }
              if (s.skills.length === 0 && !s.mention) return <span key={i}>{s.text}</span>;
              if (s.skills.length === 0) {
                return (
                  <span key={i} className="lv-mk-mention" title="出自技能清单的列名，不构成对能力的支撑">
                    {s.text}
                  </span>
                );
              }
              const on = !focusSkill && !focusSpans ? true : focusSkill ? s.skills.includes(focusSkill) : false;
              const isFirst = on && !!focusSkill && !seen;
              if (isFirst) seen = true;
              return (
                <mark
                  key={i}
                  ref={isFirst ? (el) => { firstHit.current = el; } : undefined}
                  className={on ? 'lv-mk' : 'lv-mk dim'}
                  title={`支撑：${s.skills.join('、')}`}
                >
                  {s.text}
                </mark>
              );
            })}
          </pre>
        ) : groups.length > 0 ? (
          <>
            <p className="lv-doc-fallback">
              解析器未能取回简历全文，下列为被引用的原文片段，按所属经历分组。
            </p>
            {groups.map((g) => (
              <section key={g.sourceId} className="rd-sec">
                <h4>{g.sourceId || '未标注来源'}</h4>
                <ul>
                  {g.items.map((e) => (
                    <li
                      key={e.id}
                      className={`lv-ev-line${
                        focusSkill ? (e.skills.includes(focusSkill) ? ' on' : ' dim') : ''
                      }`}
                    >
                      <q>{e.text}</q>
                      <span className="lv-ev-meta">支撑：{e.skills.join('、')}</span>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </>
        ) : (
          <p className="lv-doc-empty">
            本次抽取未产生可核验的行为证据。简历中若只列出技能名称、课程或证书，
            而未写明承担的任务与做法，不构成对能力的支撑。
          </p>
        )}
      </div>

      {/* 此处原有一栏图例，逐条解释底色与虚线各代表什么。两种标注在原文里各自
          只出现在该出现的地方 —— 有底色的是被引用的那几句，带虚线的是技能清单里
          那几个词 —— 点右栏任一项能力，左栏当场只留支撑它的那几句，标注的含义
          在这一次联动里就说清了。常驻一栏说明，等于把一次就能看懂的事写在每一屏。 */}
    </aside>
  );
}
