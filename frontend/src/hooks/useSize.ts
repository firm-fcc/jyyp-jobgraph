import { useEffect, useRef, useState } from 'react';

/**
 * 观测容器尺寸，供 SVG 可视化自适应。
 *
 * 这里刻意用“实测像素”而不是固定 viewBox 等比缩放：图内文字必须真的是 12px。
 * 若靠 viewBox 缩放，容器一变小字号就跟着缩，又回到看不清的老问题。
 *
 * 量宽不走 clientWidth。clientWidth 按规范四舍五入到整数：容器实宽 860.67px 时
 * 它返回 861，据此定出的 SVG 就比容器宽 0.33px，容器上于是常驻一条横向滚动条；
 * 窗口宽度稍变、小数部分翻过 0.5，滚动条又自行消失 —— “某些宽度下无端出现
 * 横向滚动条、缩窄反而没有”正是这么来的。改为取未取整的实宽再向下取整，
 * 宁可少一像素，也不越界。
 *
 * 扣的是边框而不是 offsetWidth − clientWidth：后者把纵向滚动条也算进去，
 * 而滚动条的有无又取决于内容宽度，会绕成一个自激的循环。
 *
 * ResizeObserver 的回调挂在渲染步骤上，在不合成帧的环境里不会触发，
 * 因此额外挂一层 window resize 兜底。
 */
export function useSize<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const read = () => {
      const rect = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      const bx = (parseFloat(cs.borderLeftWidth) || 0) + (parseFloat(cs.borderRightWidth) || 0);
      const by = (parseFloat(cs.borderTopWidth) || 0) + (parseFloat(cs.borderBottomWidth) || 0);
      const w = Math.max(0, Math.floor(rect.width - bx));
      const h = Math.max(0, Math.floor(rect.height - by));
      setSize((cur) => (cur.w === w && cur.h === h ? cur : { w, h }));
    };

    const ro = new ResizeObserver(read);
    ro.observe(el);
    window.addEventListener('resize', read);
    read();
    // 挂载瞬间容器可能还没完成布局（字体未就位、面板刚插入），补测一次
    const retry = window.setTimeout(read, 0);

    return () => {
      window.clearTimeout(retry);
      ro.disconnect();
      window.removeEventListener('resize', read);
    };
  }, []);

  return { ref, ...size };
}
