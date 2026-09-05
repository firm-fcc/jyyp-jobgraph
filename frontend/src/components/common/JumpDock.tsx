/* =========================================================
   选中项旁的跨页出口：一小块浮窗

   页尾的“下一步”已经给出四个工具页之间带上下文的出口，但它挂在正文末尾。
   全景图谱纵向十屏有余，岗位洞察与职业探索也各在四屏上下，而选中一个岗位之后
   的下一个动作就在选中的那一刻定下来了，视线停在被选中的那一行或那一张卡片上，
   离页尾还隔着一屏以上；实测中这一段距离足以让出口被整块略过。

   这一块把同一份出口搬到选中项旁边。数据仍是页尾那个 StepItem 数组，
   两处共用一份，目的地与顺序不会出现分歧；差别只在密度：
   页尾写全每个出口回答什么，浮窗只留短名与图标，整句退到悬停提示。

   四个出口不全放：一次只给三条带岗位上下文的跨页动作，其中一条为主出口，
   带短名与箭头，另两条只留图标。整块因此控制在一百六十像素以内，
   在三处落点上都排得进它所在的那一列。

   落位与遮挡由各页负责，三处的锚点见 styles/jumpdock.css 开头一段；
   形制、朝向与出场则由那个文件统一给。
   ========================================================= */

import { Link } from 'react-router-dom';
import { Icon } from '@/components/Icon';
import type { StepItem } from '@/components/common/NextSteps';

interface Props {
  /** 与页尾同一份出口。只有写了 short 的条目进入浮窗 */
  items: StepItem[];
  /** 读屏用的分组名，说明这一组按钮把什么带往哪里 */
  label: string;
}

export function JumpDock({ items, label }: Props) {
  const list = items.filter((it) => it.short);
  if (!list.length) return null;

  return (
    <nav className="jdk" aria-label={label}>
      {list.map((it) => (
        <Link
          key={it.to + it.label}
          className={it.primary ? 'jdk-b primary' : 'jdk-b'}
          to={it.to}
          /* 可见的是短名，读屏与悬停提示给整句：图上留白有限，
             但“这一条到底做什么”不能只靠一个图标交代 */
          aria-label={it.label}
        >
          <Icon name={it.icon} size={14} />
          {it.primary && (
            <>
              <b>{it.short}</b>
              <Icon name="chevronR" size={11} className="jdk-go" />
            </>
          )}
          <span className="jdk-tip" aria-hidden="true">
            {it.label}
          </span>
        </Link>
      ))}
    </nav>
  );
}
