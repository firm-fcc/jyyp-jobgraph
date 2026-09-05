/* ============================================================
   说明气泡 —— 收在问号后面的说明文字

   报告里每一块标题下面原本都压着两三行说明。这些话一次读懂之后
   就不必再读，却每次都占着标题正下方最好的位置，把真正要看的数字
   往下推一屏。改成问号：默认只留标题，需要时点开。

   用 role="note" 而不是 tooltip：这是一段可以慢慢读的正文，
   不是划过就消失的提示，所以只认点击，不认悬停。
   ============================================================ */

import { useEffect, useRef, useState, type ReactNode } from 'react';

interface Props {
  /** 说明正文 */
  text: ReactNode;
  /** 问号前面的文字。不给就只有一个问号 */
  trigger?: string;
  /** 气泡贴左还是贴右。靠右栏用 end，免得被容器裁掉 */
  align?: 'start' | 'end';
}

export function HelpTip({ text, trigger, align = 'start' }: Props) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLSpanElement | null>(null);

  /* 点到别处、按 Esc 都收起 —— 看完一段说明不该还要回头再点一次问号 */
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <span className="htip" ref={box}>
      <button
        type="button"
        className={open ? 'htip-btn open' : 'htip-btn'}
        aria-expanded={open}
        aria-label={trigger ? undefined : '查看说明'}
        onClick={() => setOpen((v) => !v)}
      >
        {trigger}
        <i aria-hidden="true">?</i>
      </button>
      {open && (
        <span className={align === 'end' ? 'htip-pop end' : 'htip-pop'} role="note">
          {text}
        </span>
      )}
    </span>
  );
}
