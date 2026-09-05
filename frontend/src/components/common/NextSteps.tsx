/* =========================================================
   页尾的下一步

   起因是一次实测统计：全站跨页链接的分布极不均衡 ——
   全景图谱 0 条、职业探索 0 条、岗位洞察 1 条，只有匹配报告页脚给了四个出口。
   于是从首页发散出去之后，每一页都是死胡同：在全景图谱往下滚十屏，
   末尾只剩页脚，没有任何一处说"接下来该看什么"。

   这一块把匹配报告页脚那套做法抽出来，四个工具页统一挂在正文末尾。
   两条约定：

   1. **出口带着当前上下文**，不是通用导航。选中了某个岗位，"匹配该岗位"
      就要把这个岗位带过去（`?target=`），而不是丢到匹配页的默认岗位上。
      通用链接页脚已经有四条了，这里再放一遍等于没放。
   2. **每个出口写清它回答什么**。只写"去职业探索"，等于把顶栏又抄了一遍。
   ========================================================= */

import { Link } from 'react-router-dom';
import { Icon } from '@/components/Icon';
import { nextOf, stationOf } from '@/data/journey';

export interface StepItem {
  to: string;
  label: string;
  /** 这一步回答什么 —— 一行，不写"点击进入"这类没有信息量的话 */
  desc: string;
  icon: string;
  /** 主出口在视觉上重一档。一块里至多一个 */
  primary?: boolean;
  /**
   * 图上浮窗用的短名，两到四字，取目的地页名。留空表示该条不进浮窗。
   *
   * 同一份出口供两处使用：页尾这一块写全每条回答什么，图上那一块（JumpDock）
   * 只排得下短名。两处若各写各的，同一个动作在同一页上就会有两种说法。
   */
  short?: string;
}

interface Props {
  /** 当前页路由，用来算出"下一站"并避免与自定义出口重复 */
  from: string;
  items: StepItem[];
}

export function NextSteps({ from, items }: Props) {
  const next = nextOf(from);
  const cur = stationOf(from);

  /* 至多四张。标题栏那句"下一站是什么"与卡片不做去重：标题说的是路线上的位置，
     卡片给的是带着当前选中岗位的具体动作，两者指向同一页时也不是同一件事。 */
  const list = items.slice(0, 4);

  return (
    <section className="nxt" aria-label="下一步">
      <header className="nxt-hd">
        <h2>下一步</h2>
        {next ? (
          <p>
            下一站为<b>第 {next.step} 站 · {next.label}</b>：{next.duty}
          </p>
        ) : (
          <p>
            四站至此走完{cur?.step ? `（第 ${cur.step} 站为末站）` : ''}。首页为本期结论与入口。
          </p>
        )}
      </header>

      <div className="nxt-grid">
        {list.map((it) => (
          <Link key={it.to + it.label} className={it.primary ? 'nxt-card primary' : 'nxt-card'} to={it.to}>
            <span className="nxt-ic">
              <Icon name={it.icon} size={19} />
            </span>
            <b className="nxt-label">{it.label}</b>
            <span className="nxt-desc">{it.desc}</span>
            <span className="nxt-go">
              <Icon name="arrowR" size={13} />
            </span>
          </Link>
        ))}
      </div>
    </section>
  );
}
