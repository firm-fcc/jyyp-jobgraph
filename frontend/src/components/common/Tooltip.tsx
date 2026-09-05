import { useEffect, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

export interface TipState {
  x: number;
  y: number;
  content: ReactNode;
}

/** 跟随光标的浮层：所有可视化共用一套，避免各写各的 */
export function Tooltip({ tip }: { tip: TipState | null }) {
  const [pos, setPos] = useState({ x: 0, y: 0 });
  useEffect(() => {
    if (!tip) return;
    const w = 300;
    setPos({
      x: Math.min(tip.x + 16, window.innerWidth - w - 12),
      y: Math.min(tip.y + 16, window.innerHeight - 150),
    });
  }, [tip]);
  if (!tip) return null;
  /* 挂到 body 上而不是就地渲染：坐标取自 clientX/clientY，是视口坐标，
     而 position: fixed 一旦有祖先带了 filter / backdrop-filter / transform，
     参照系就变成那个祖先。卡片用上毛玻璃之后这一条必然踩到，
     顺带也不再被 overflow: hidden 的容器裁掉。 */
  return createPortal(
    <div className="tt" style={{ left: pos.x, top: pos.y }} role="tooltip">
      {tip.content}
    </div>,
    document.body,
  );
}
