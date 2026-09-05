/* =========================================================
   页首职责条 —— 每一页开头的那一行"这一页给什么"

   四个工具页此前都是直接以数据摘要条开场：第一眼看到的是
   "315,126 条招聘信息 / 去重后 261,239 条计入统计"这样的读数，
   而不是这一页要回答的问题。读数是答案的一部分，问题却没被写出来过。

   只有一行，没有展开项。这里一度挂过一个"读法"下拉，里面是一段
   逐块讲解页面怎么读的长文 —— 但页面本身的分区标题、图上的说明标与
   问号提示已经在讲同一件事，那段长文因此只是把它们又抄了一遍，
   还多出一个要不要点开的决定。改为把这一页给什么写进 duty 那一句里。

   另外承担两件此前无处安放的事：
   ① 这一页在四站路线里的位置 —— 顶栏的五个名字因此有了顺序；
   ② 带参数跳进来时说明"已按来路定位到哪一项"，并给一个返回上一页的出口 ——
      此前从首页点一张榜单跳进来，页面确实选中了那一项，
      但没有任何一处说这件事，看的人不知道自己为什么落在这里。
   ========================================================= */

import { useNavigate } from 'react-router-dom';
import { Icon } from '@/components/Icon';
import { ROUTE, type Station } from '@/data/journey';

interface Props {
  station: Station;
  /** 带参数跳进来时被定位到的条目名。不给就不显示这一行 */
  landed?: string | null;
  /** 定位行右侧的"取消定位"。不给就只有返回上一页 */
  onClearLanded?: () => void;
}

export function PageGuide({ station, landed, onClearLanded }: Props) {
  const nav = useNavigate();

  return (
    <section className="pgd" aria-label={`${station.label}：本页职责`}>
      <div className="pgd-main">
        <span className="pgd-badge">
          <Icon name={station.icon} size={15} />
          <em>第 {station.step} 站</em>
          {station.label}
        </span>

        <p className="pgd-duty">{station.duty}</p>

        <span className="pgd-dots" aria-label={`四站路线中的第 ${station.step} 站`}>
          {ROUTE.map((s) => (
            <i key={s.to} className={s.to === station.to ? 'on' : undefined} aria-hidden="true" />
          ))}
        </span>
      </div>

      {landed && (
        <div className="pgd-landed">
          <Icon name="target" size={14} />
          <p>
            已按来路定位到<b>“{landed}”</b>
          </p>
          <div className="pgd-landed-act">
            {onClearLanded && (
              <button className="pgd-lnk" onClick={onClearLanded}>
                取消定位
              </button>
            )}
            <button className="pgd-lnk" onClick={() => nav(-1)}>
              <Icon name="chevronL" size={12} />
              返回上一页
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
