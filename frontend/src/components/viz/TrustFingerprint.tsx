/* ============================================================
   可信度指纹 —— 紧跟在每一个权重数字旁边的微型图元
     · 中间三段横条 = 招聘 / 论文 / 新闻三类证据的条数占比
     · 外圈弧长     = 该关系的置信度
     · 弧留缺口     = 只有单一来源，没有跨源交叉验证
   证据为空一律渲染“待确认”，从界面层杜绝凭空出现的数字。

   全站不使用原生 title 提示（见 README“界面约定”），
   因此这里只给 aria-label，可见说明由调用方的文案承担。
   ============================================================ */

export interface SourceMix {
  jd: number;
  paper: number;
  news: number;
}

interface Props {
  mix: SourceMix;
  confidence: number;
  size?: number;
}

export function TrustFingerprint({ mix, confidence, size = 16 }: Props) {
  const total = mix.jd + mix.paper + mix.news;
  const sources = (mix.jd > 0 ? 1 : 0) + (mix.paper > 0 ? 1 : 0) + (mix.news > 0 ? 1 : 0);
  const single = sources <= 1;

  if (total === 0) {
    return (
      <span className="fp-empty" aria-label="暂无证据支撑">
        待确认
      </span>
    );
  }

  const s = size;
  const c = s / 2;
  const r = s / 2 - 1.6;
  const circ = 2 * Math.PI * r;
  const arc = circ * Math.min(confidence, 1) * (single ? 0.78 : 1);

  const segW = s - 6;
  const x0 = 3;
  const wJd = (mix.jd / total) * segW;
  const wPaper = (mix.paper / total) * segW;
  const wNews = (mix.news / total) * segW;

  const ringColor = confidence > 0.7 ? 'var(--green)' : confidence > 0.45 ? 'var(--amber)' : 'var(--red)';

  return (
    <svg
      className="fp"
      width={s}
      height={s}
      viewBox={`0 0 ${s} ${s}`}
      role="img"
      aria-label={`证据 ${total} 条：招聘 ${mix.jd} · 论文 ${mix.paper} · 新闻 ${mix.news}，置信度 ${(confidence * 100).toFixed(0)}%${single ? '，单一来源尚未交叉验证' : ''}`}
    >
      <circle cx={c} cy={c} r={r} fill="none" stroke="var(--line-strong)" strokeWidth={1.5} />
      <circle
        cx={c}
        cy={c}
        r={r}
        fill="none"
        stroke={ringColor}
        strokeWidth={1.7}
        strokeDasharray={`${arc} ${circ}`}
        strokeLinecap="round"
        transform={`rotate(-90 ${c} ${c})`}
      />
      {wJd > 0.3 && <rect x={x0} y={c - 1.7} width={wJd} height={3.4} fill="var(--src-jd)" />}
      {wPaper > 0.3 && <rect x={x0 + wJd} y={c - 1.7} width={wPaper} height={3.4} fill="var(--src-paper)" />}
      {wNews > 0.3 && (
        <rect x={x0 + wJd + wPaper} y={c - 1.7} width={wNews} height={3.4} fill="var(--src-news)" />
      )}
    </svg>
  );
}
