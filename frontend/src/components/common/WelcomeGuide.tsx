/* =========================================================
   四大功能 —— 第一次打开系统时的那一屏

   要解决的是一个具体问题：顶栏五个入口只有名字，第一次进来的人无法
   从"全景图谱 / 岗位洞察 / 职业探索 / 人岗匹配"这四个词里读出它们的分工，
   更读不出它们是一条链而不是四个并列的标签。

   因此这一屏不讲功能列表，只讲**顺序**：四张卡片按站序排开，卡上标着市场侧
   还是个人侧，各写一句这一站给什么。读完这一屏，顶栏就有了含义。

   卡片之外不再另配导语与口径说明 —— 那两段讲的是卡片已经讲过的事，
   放在这里只是把同一件事说第二遍，而这一屏的价值全在四张卡的排列本身。

   每次从封面进入系统时出现一次，在系统内部换页不再打扰。随时可以关掉 ——
   右上角、Esc、点遮罩都算关闭；要重看走顶栏的“路线”按钮。

   页脚不再另放“从第一站开始 / 先看本期结论”两个按钮：四张卡片本身就是入口，
   点哪一张就从哪一站进，再在页脚复述两个入口只是把同一件事说第二遍。
   ========================================================= */

import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Icon } from '@/components/Icon';
import { ROUTE } from '@/data/journey';

interface Props {
  onClose: () => void;
}

export function WelcomeGuide({ onClose }: Props) {
  const nav = useNavigate();

  /* 打开期间锁住背景滚动：这一屏是一个完整的阅读单元，
     背后的长页跟着滚会让人以为弹窗只是浮在某一段内容上的提示。 */
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.body.style.overflow = prev;
      document.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

  const go = (to: string) => {
    onClose();
    nav(to);
  };

  return (
    <div className="wg-mask" onClick={onClose}>
      <div
        className="wg"
        role="dialog"
        aria-modal="true"
        aria-labelledby="wg-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="wg-hd">
          <div className="wg-hd-text">
            <p className="wg-eyebrow">招聘信息 · 学术论文 · 行业新闻</p>
            <h2 id="wg-title">四大功能</h2>
          </div>
          <button className="wg-x" onClick={onClose} aria-label="关闭功能引导">
            <Icon name="close" size={18} />
          </button>
        </header>

        <ol className="wg-route">
          {ROUTE.map((s, i) => (
            <li key={s.to} className={`wg-stop side-${s.side === '市场侧' ? 'mkt' : 'per'}`}>
              {i > 0 && <span className="wg-arrow" aria-hidden="true" />}
              <button className="wg-card" onClick={() => go(s.to)} autoFocus={i === 0}>
                <span className="wg-no">
                  <Icon name={s.icon} size={17} />
                  <em>{s.step}</em>
                </span>
                <b className="wg-name">{s.label}</b>
                <span className="wg-side">{s.side}</span>
                <span className="wg-duty">{s.duty}</span>
                <span className="wg-enter">
                  进入
                  <Icon name="arrowR" size={12} />
                </span>
              </button>
            </li>
          ))}
        </ol>

        <footer className="wg-ft">
          <p className="wg-ft-hint">顶栏“路线”可重新打开本页。</p>
        </footer>
      </div>
    </div>
  );
}
