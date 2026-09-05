/* =========================================================
   封面页

   系统入口的单屏封面：主标题、一段系统说明、一个进入系统的入口，
   以及四项从图谱现算的规模指标。整页固定为一个视口高度，不产生滚动 ——
   封面的职责是交代"这是什么"并把人送进系统，不承担内容展示。

   与首页（/home）的分工：封面只放定位与入口，核心洞察、榜单、
   四层体系等成段内容全部留在首页。
   ========================================================= */

import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { Icon } from '@/components/Icon';
import { MANIFEST } from '@/data/manifest';
import '@/styles/landing.css';

export function Landing() {
  /* 四项规模指标读数据清单，不建整份数据集。

     清单不足两千字节，图谱产物十余兆；封面上要报的只是四个数与观测区间，
     为它们把整份产物拉下来再建一遍图，等于把进入系统才需要的等待
     提到封面之前。清单里的计数与图谱同源，由构建脚本一次写出，
     故这四个数与进入系统后数得出来的仍是同一批。 */
  const facts = useMemo(() => {
    const c = MANIFEST.counts;
    const e = Object.values(c.edges).reduce((a, b) => a + b, 0);
    return {
      nodes: c.jobs + c.tasks + c.skills + c.skillpoints,
      edges: e,
      jobs: c.jobs,
      /* 新岗位只数岗位层：叠层条目覆盖全部四层，混在一起标“新岗位”
         对不上口径 —— 读者点进系统后数得出来的是岗位那一层。 */
      newJobs: c.overlay.jobs,
      /* 一窗一版本，版本号即窗序（realGraph.REAL_VERSION_DEFS 同一规则） */
      version: `w${MANIFEST.windows.length}`,
      date: MANIFEST.latest,
      /* 观测区间取首末窗，与顶栏的数据窗口标同源 */
      from: MANIFEST.windows[0] ?? '—',
      to: MANIFEST.latest,
    };
  }, []);

  const stats = [
    { num: facts.nodes.toLocaleString(), label: '图谱节点' },
    { num: facts.edges.toLocaleString(), label: '层间关系' },
    { num: String(facts.jobs), label: '覆盖岗位' },
    { num: String(facts.newJobs), label: '新岗位' },
  ];

  return (
    <div className="landing">
      <div className="lp-bg" aria-hidden="true">
        <div className="lp-orb lp-orb-1" />
        <div className="lp-orb lp-orb-2" />
        <div className="lp-orb lp-orb-3" />
      </div>

      <header className="lp-head">
        <span className="lp-brand">
          <svg className="lp-mark" viewBox="0 0 32 32" width={30} height={30} aria-hidden="true">
            <defs>
              <linearGradient id="lp-g" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0" stopColor="#2563eb" />
                <stop offset="1" stopColor="#0ea5b7" />
              </linearGradient>
            </defs>
            <circle cx="16" cy="7" r="3.4" fill="url(#lp-g)" />
            <circle cx="7" cy="22" r="3.4" fill="url(#lp-g)" opacity=".85" />
            <circle cx="25" cy="22" r="3.4" fill="url(#lp-g)" opacity=".85" />
            <path
              d="M16 10.5 8.5 19.5 M16 10.5 23.5 19.5 M10.5 22h11"
              stroke="url(#lp-g)"
              strokeWidth="1.8"
              fill="none"
              strokeLinecap="round"
            />
          </svg>
          {/* 与顶栏的品牌块同一套类名。此前封面另写了一份竖排、衬线体的样式，
              于是同一个品牌在封面与内页是两个形态，从封面点进去像换了个站。 */}
          <span className="brand-name">
            JobGraph
            <span className="brand-sub">就业有谱</span>
          </span>
        </span>
        <span className="lp-head-meta">
          图谱版本 {facts.version} · {facts.date}
        </span>
      </header>

      <main className="lp-main">
        <p className="lp-eyebrow">招聘信息 · 学术论文 · 行业新闻</p>
        <h1 className="lp-title">
          破解青年人才技能错配的
          <br />
          <span className="lp-title-accent">动态技能图谱</span>与可视化就业导航系统
        </h1>
        <div className="lp-actions">
          <Link to="/home" className="lp-btn lp-btn-primary">
            进入系统
            <Icon name="arrowR" size={16} />
          </Link>
          <Link to="/panorama" className="lp-btn lp-btn-ghost">
            全景图谱
          </Link>
        </div>
      </main>

      <footer className="lp-foot">
        <div className="lp-stats">
          {stats.map((s, i) => (
            <div className="lp-stat" key={s.label}>
              {i > 0 && <span className="lp-stat-div" aria-hidden="true" />}
              <span className="lp-stat-num">{s.num}</span>
              <span className="lp-stat-label">{s.label}</span>
            </div>
          ))}
        </div>
        {/* 交叉验证已写进上面的副标题，这里不再说第二遍；换成它没讲过的那一条。

            原先写作"当前展示为演示数据"。图谱产物接入后，上排四项规模指标与全站各图
            均取自算法侧的实测批次，该句已与实际不符；换成读图的前提 —— 这批数据
            覆盖的时间区间。仍由前端补齐的那几维列在顶栏的数据窗口标内。 */}
        <p className="lp-note">
          每条结论可回溯至原文 · 数据窗口 {facts.from} — {facts.to}
        </p>
      </footer>
    </div>
  );
}
