/* ============================================================
   演示数据标

   算法侧尚未产出的那几维由前端补齐，补出来的数不能和实测的数
   长得一模一样。这里给一枚统一的小标：默认只有两个字，
   带 text 时点开才展开说明 —— 说明是给追问的人看的，不是给每个人看的。

   标面上不带问号：全站解释性问号已一并撤去，可点开这件事由标本身的
   悬停与聚焦态交代。
   ============================================================ */

import { useEffect, useRef, useState, type ReactNode } from 'react';

interface Props {
  /** 点开后展开的说明。不给就只是一枚静态标 */
  text?: ReactNode;
  /** 标签文字，默认“演示数据” */
  label?: string;
  /** 气泡贴左还是贴右。靠右栏用 end，免得被容器裁掉 */
  align?: 'start' | 'end';
}

export function DemoTag({ text, label = '演示数据', align = 'end' }: Props) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLSpanElement | null>(null);

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

  if (!text) return <span className="dtag dtag-static">{label}</span>;

  return (
    <span className="dtag-wrap" ref={box}>
      <button
        type="button"
        className={open ? 'dtag dtag-btn open' : 'dtag dtag-btn'}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {label}
      </button>
      {open && (
        <span className={align === 'end' ? 'htip-pop end' : 'htip-pop'} role="note">
          {text}
        </span>
      )}
    </span>
  );
}
